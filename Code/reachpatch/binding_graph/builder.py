from __future__ import annotations

from collections import deque
from dataclasses import replace
import time
from typing import Callable, Iterable

from reachpatch.binding_graph.compatibility import bind_compatibility
from reachpatch.binding_graph.models import (
    BindingGraph,
    BindingStatus,
    BindingUnit,
    OracleFrontier,
    RepairComponent,
)
from reachpatch.models.base import stable_id
from reachpatch.models.core import Frontier
from reachpatch.oracle.authority import observation_contract_from_leaf, resolve_oracle
from reachpatch.oracle.models import ExecutableScenario
from reachpatch.program_graph.impact import impact_cone
from reachpatch.program_graph.models import CausalRepairCut, ImpactCone, PathClass, ProgramGraph
from reachpatch.program_graph.paths import PATH_RELATIONS, guard_feasibility
from reachpatch.program_graph.slicing import causal_repair_cut, component_repair_frontier
from reachpatch.requirement_graph.models import RequirementGraph, RequirementPathObligation


def _binding_frontier(
    unit_id: str,
    leaf_id: str,
    kind: str,
    reason: str,
    action: str,
    *,
    evidence_ids: Iterable[str],
    hard: bool,
) -> Frontier:
    return Frontier(
        frontier_id=stable_id("binding-frontier", unit_id, kind, reason),
        kind=kind,
        owner_id=leaf_id,
        reason=reason,
        resolution_action=action,
        hard=hard,
        evidence_ids=tuple(evidence_ids),
    )


def _scenario_for_unit(
    unit_id: str,
    obligation: RequirementPathObligation,
    leaf,
    graph: ProgramGraph,
    oracle,
) -> ExecutableScenario | None:
    if not oracle.active_and_trusted or obligation.entrypoint_id is None:
        return None
    entrypoint = graph.nodes.get(obligation.entrypoint_id)
    if entrypoint is None:
        return None
    qualified = str(entrypoint.attributes.get("qualified_name", ""))
    if "." not in qualified or entrypoint.kind not in {"function", "test", "class", "method", "property"}:
        return None
    bindings = (
        obligation.partition.candidate_bindings[0]
        if obligation.partition.candidate_bindings else {}
    )
    contract = observation_contract_from_leaf(leaf)
    parts = qualified.split(".")
    module = ".".join(parts[:-1])
    attribute = parts[-1]
    setup = [{"op": "import", "module": module, "as": "target_module"}]
    stimulus = [{
        "op": "call",
        "target": f"target_module.{attribute}",
        "args": list(bindings.values()),
        "kwargs": {},
        "save_as": "result",
    }]
    if entrypoint.kind in {"method", "property"} and len(parts) >= 3:
        class_name = parts[-2]
        class_module = ".".join(parts[:-2])
        setup = [{"op": "import", "module": class_module, "as": "target_module"}]
        setup.append({
            "op": "construct",
            "target": f"target_module.{class_name}",
            "args": [],
            "kwargs": {},
            "save_as": "instance",
        })
        if entrypoint.kind == "property":
            stimulus = [{
                "op": "observe",
                "channel": "return",
                "source": f"instance.{attribute}",
            }]
        else:
            stimulus = [{
                "op": "call",
                "target": f"instance.{attribute}",
                "args": list(bindings.values()),
                "kwargs": {},
                "save_as": "result",
            }]
    scenario_id = stable_id("scenario", unit_id, qualified, bindings, oracle.oracle_id)
    return ExecutableScenario(
        scenario_id=scenario_id,
        binding_unit_id=unit_id,
        assignment_scope="ALL" if leaf.hypothesis_id is None else leaf.hypothesis_id,
        setup=tuple(setup),
        stimulus=tuple(stimulus),
        observe=contract,
        oracle=oracle,
        evidence_ids=leaf.supporting_evidence,
        isolation={"reset_modules": [module], "filesystem": "transactional", "network": "blocked"},
        timeout_seconds=120.0,
        kind="PRESERVATION" if leaf.authority_class.value == "PRESERVATION" else "TARGET",
        source_hashes={
            "requirement": obligation.requirement_graph_hash,
            "program": obligation.program_graph_hash,
        },
        evidence_cluster_id=oracle.evidence_cluster_id,
    )


def _discover_bypasses(
    obligation: RequirementPathObligation,
    path_class: PathClass,
    cut_node_ids: Iterable[str],
    graph: ProgramGraph,
    *,
    path_cache: dict[tuple[str, str, str], tuple[str, ...] | None] | None = None,
) -> tuple[str, ...]:
    if obligation.public_trigger_id is None:
        return ()
    bypasses: set[str] = set()
    for cut_node in cut_node_ids:
        if cut_node in {obligation.public_trigger_id, obligation.observation_id}:
            continue
        cache_key = (
            obligation.public_trigger_id,
            obligation.observation_id,
            cut_node,
        )
        cached_edges = path_cache.get(cache_key) if path_cache is not None else None
        cache_hit = path_cache is not None and cache_key in path_cache
        if not cache_hit:
            path = graph.shortest_path(
                obligation.public_trigger_id,
                obligation.observation_id,
                edge_predicate=lambda edge: edge.kind in PATH_RELATIONS,
                forbidden_nodes={cut_node},
            )
            cached_edges = tuple(path[1]) if path is not None else None
            if path_cache is not None:
                path_cache[cache_key] = cached_edges
        if cached_edges is not None and cached_edges != path_class.edge_ids:
            bypasses.add(stable_id(
                "binding-bypass", obligation.path_obligation_id, cut_node, cached_edges
            ))
    return tuple(sorted(bypasses))


def bind_path_obligation(
    obligation: RequirementPathObligation,
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    *,
    cut_results: dict[str, CausalRepairCut] | None = None,
    impact_cache: dict[tuple[str, ...], ImpactCone] | None = None,
    cut_basis_cache: dict[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], dict[str, object]] | None = None,
    cut_node_metric_cache: dict[str, tuple[int, int]] | None = None,
    trigger_path_cache: dict[tuple[str, str], bool] | None = None,
    bypass_path_cache: dict[tuple[str, str, str], tuple[str, ...] | None] | None = None,
) -> tuple[BindingUnit, object, ExecutableScenario | None, tuple[Frontier, ...]]:
    leaf = requirement_graph.leaves[obligation.leaf_id]
    path_class = program_graph.path_classes.get(obligation.path_class_id)
    if path_class is None:
        raise KeyError(f"path class missing from Program Graph: {obligation.path_class_id}")
    oracle = resolve_oracle(leaf)
    observation_contract = observation_contract_from_leaf(leaf)
    unit_id = stable_id(
        "binding-unit",
        obligation.path_obligation_id,
        requirement_graph.assignment_id,
        obligation.entrypoint_id,
        obligation.path_class_id,
        obligation.scenario_partition_id,
        observation_contract.contract_id,
        oracle.oracle_id,
    )
    projection = bind_compatibility(
        leaf,
        obligation,
        path_class,
        program_graph,
        observation_contract,
        oracle,
        trigger_path_cache=trigger_path_cache,
    )
    cut = causal_repair_cut(
        program_graph,
        [obligation.entrypoint_id] if obligation.entrypoint_id else [],
        [obligation.observation_id],
        unit_slices={unit_id: set(obligation.dependence_slice_ids)},
        impact_cache=impact_cache,
        basis_cache=cut_basis_cache,
        node_metric_cache=cut_node_metric_cache,
    ) if obligation.entrypoint_id else None
    if cut is not None and cut_results is not None:
        cut_results[unit_id] = cut
    cut_nodes = cut.node_ids if cut else ()
    cone_key = tuple(sorted(cut_nodes))
    cone = impact_cache.get(cone_key) if impact_cache is not None else None
    if cone is None and cut_nodes:
        cone = impact_cone(program_graph, cut_nodes)
        if impact_cache is not None:
            impact_cache[cone_key] = cone
    preservation = set(obligation.preservation_caller_ids)
    if cone:
        preservation.update(cone.preservation_test_ids)
        preservation.update(cone.caller_ids)
    bypasses = _discover_bypasses(
        obligation,
        path_class,
        cut_nodes,
        program_graph,
        path_cache=bypass_path_cache,
    )
    scenario = _scenario_for_unit(
        unit_id, obligation, leaf, program_graph, oracle
    )
    frontiers: list[Frontier] = []
    if not projection.domain_guard_projection["compatible"]:
        frontiers.append(_binding_frontier(
            unit_id, leaf.leaf_id, "INCOMPATIBLE_DOMAIN_PATH",
            projection.reason, "refine the symbolic domain/path guard projection",
            evidence_ids=leaf.supporting_evidence, hard=leaf.mandatory,
        ))
    if not projection.trigger_projection["compatible"]:
        frontiers.append(_binding_frontier(
            unit_id, leaf.leaf_id, "UNCONTROLLABLE_TRIGGER",
            projection.reason, "recover a controllable trigger and confirm forward influence",
            evidence_ids=leaf.supporting_evidence, hard=leaf.mandatory,
        ))
    if not projection.observation_projection["compatible"]:
        frontiers.append(_binding_frontier(
            unit_id, leaf.leaf_id, "OBSERVATION_PROJECTION",
            projection.reason, "project the locked observation channel to a reachable node",
            evidence_ids=leaf.supporting_evidence, hard=leaf.mandatory,
        ))
    if not projection.oracle_projection["compatible"]:
        frontiers.append(_binding_frontier(
            unit_id, leaf.leaf_id, "ORACLE_FRONTIER",
            oracle.unknown_condition, "obtain an executable A/B/C relation or retain as exploration-only",
            evidence_ids=leaf.supporting_evidence, hard=leaf.mandatory,
        ))
    if cut is None or not cut.node_ids:
        frontiers.append(_binding_frontier(
            unit_id, leaf.leaf_id, "NO_CAUSAL_REPAIR_CUT",
            "forward/backward dependence intersection has no legal source node",
            "expand dependency/dispatch/state analysis",
            evidence_ids=leaf.supporting_evidence, hard=leaf.mandatory,
        ))
    if scenario is None:
        frontiers.append(_binding_frontier(
            unit_id, leaf.leaf_id, "SCENARIO_MATERIALIZATION",
            "entrypoint construction or trusted oracle is incomplete",
            "materialize an isolated public InputRecipe without changing the oracle",
            evidence_ids=leaf.supporting_evidence, hard=leaf.mandatory,
        ))
    ready = projection.compatible and cut is not None and bool(cut.node_ids) and scenario is not None
    unit = BindingUnit(
        unit_id=unit_id,
        path_obligation_id=obligation.path_obligation_id,
        assignment_scope="ALL" if leaf.hypothesis_id is None else leaf.hypothesis_id,
        leaf_id=leaf.leaf_id,
        authority=leaf.authority.value,
        trigger_id=obligation.public_trigger_id,
        entrypoint_id=obligation.entrypoint_id,
        path_class_id=path_class.path_class_id,
        interaction_path_ids=tuple(path_class.node_ids),
        guard=obligation.accumulated_guard,
        exit_kind=obligation.exit_kind,
        repair_cut_node_ids=tuple(cut_nodes),
        observation_node_ids=(obligation.observation_id,),
        bypass_path_ids=bypasses,
        preservation_node_ids=tuple(sorted(preservation)),
        repair_component_id=None,
        oracle_id=oracle.oracle_id,
        scenario_ids=(scenario.scenario_id,) if scenario else (),
        frontier_ids=tuple(sorted(frontier.frontier_id for frontier in frontiers)),
        status="READY" if ready else "BLOCKED",
        requirement_graph_hash=obligation.requirement_graph_hash,
        program_graph_hash=obligation.program_graph_hash,
        projection_witness=projection,
        impact_cone_node_ids=cone.downstream_node_ids if cone else (),
    )
    return unit, oracle, scenario, tuple(frontiers)


def _build_components(
    binding_graph: BindingGraph,
    program_graph: ProgramGraph,
    cuts_by_unit: dict[str, CausalRepairCut],
) -> None:
    units = sorted(
        (item for item in binding_graph.units.values() if item.status in {"ACTIVE", "READY"}),
        key=lambda item: item.unit_id,
    )
    if not units:
        return
    parent = {unit.unit_id: unit.unit_id for unit in units}

    def find(unit_id: str) -> str:
        cursor = unit_id
        while parent[cursor] != cursor:
            parent[cursor] = parent[parent[cursor]]
            cursor = parent[cursor]
        return cursor

    def union(left_id: str, right_id: str) -> None:
        left_root = find(left_id)
        right_root = find(right_id)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    shared_by_pair: dict[tuple[str, str], set[str]] = {}
    relations_by_pair: dict[tuple[str, str], set[str]] = {}
    relation_nodes: dict[str, dict[str, set[str]]] = {
        "repair_cut": {},
        "preservation": {},
        "observation": {},
        "state": {},
        "dispatch": {},
    }
    for unit in units:
        path_ids = set(unit.interaction_path_ids)
        relation_nodes["repair_cut"][unit.unit_id] = set(unit.repair_cut_node_ids)
        relation_nodes["preservation"][unit.unit_id] = set(unit.preservation_node_ids)
        relation_nodes["observation"][unit.unit_id] = set(unit.observation_node_ids)
        relation_nodes["state"][unit.unit_id] = {
            node_id for node_id in path_ids if program_graph.nodes[node_id].kind == "field"
        }
        relation_nodes["dispatch"][unit.unit_id] = {
            node_id
            for node_id in path_ids
            if program_graph.nodes[node_id].kind == "protocol_operation"
        }

    # The relation order is the precedence used by _interaction_witness when
    # one node participates in more than one interaction category.
    claimed_relation: dict[tuple[tuple[str, str], str], str] = {}
    for relation in ("repair_cut", "preservation", "observation", "state", "dispatch"):
        units_by_node: dict[str, list[str]] = {}
        for unit_id, node_ids in relation_nodes[relation].items():
            for node_id in node_ids:
                units_by_node.setdefault(node_id, []).append(unit_id)
        for node_id, unit_ids in units_by_node.items():
            node = program_graph.nodes[node_id]
            if (
                len(program_graph.incoming(node_id)) + len(program_graph.outgoing(node_id)) > 100
                and node.label.lower() in {"log", "logger", "helper", "fixture"}
            ):
                continue
            ordered_units = sorted(set(unit_ids))
            if len(ordered_units) < 2:
                continue
            # A star is the exact connectivity-preserving reduction of the
            # clique induced by a shared node. It retains a concrete witness
            # for every union while avoiding O(k^2) pair materialization.
            left_id = ordered_units[0]
            for right_id in ordered_units[1:]:
                if find(left_id) == find(right_id):
                    continue
                pair = (left_id, right_id)
                union(left_id, right_id)
                shared_by_pair.setdefault(pair, set()).add(node_id)
                key = (pair, node_id)
                if key not in claimed_relation:
                    claimed_relation[key] = relation
                    relations_by_pair.setdefault(pair, set()).add(relation)

    witness_by_pair: dict[tuple[str, str], dict] = {}
    for pair, node_ids in shared_by_pair.items():
        left_id, right_id = pair
        witness_by_pair[pair] = {
            "left": left_id,
            "right": right_id,
            "shared_node_ids": sorted(node_ids),
            "relations": sorted(relations_by_pair[pair]),
        }
    members_by_root: dict[str, set[str]] = {}
    for unit in units:
        members_by_root.setdefault(find(unit.unit_id), set()).add(unit.unit_id)
    for members in sorted(members_by_root.values(), key=lambda item: sorted(item)[0]):
        member_units = [binding_graph.units[unit_id] for unit_id in sorted(members)]
        cuts = {
            member.unit_id: cuts_by_unit[member.unit_id]
            for member in member_units
            if member.unit_id in cuts_by_unit
        }
        try:
            frontier = component_repair_frontier(cuts) if cuts else ()
        except ValueError:
            frontier = tuple(sorted({node for member in member_units for node in member.repair_cut_node_ids}))
        component_id = stable_id("repair-component", sorted(members), frontier)
        component_witnesses = tuple(
            witness
            for pair, witness in sorted(witness_by_pair.items())
            if set(pair) <= members
        )
        common_cut = set(member_units[0].repair_cut_node_ids)
        for member in member_units[1:]:
            common_cut &= set(member.repair_cut_node_ids)
        state_owners = {
            node_id
            for member in member_units
            for node_id in member.interaction_path_ids
            if program_graph.nodes[node_id].kind == "field"
        }
        dispatch = {
            node_id
            for member in member_units
            for node_id in member.interaction_path_ids
            if program_graph.nodes[node_id].kind == "protocol_operation"
        }
        component = RepairComponent(
            component_id=component_id,
            unit_ids=tuple(sorted(members)),
            common_dominator_ids=tuple(sorted(common_cut)),
            state_owner_ids=tuple(sorted(state_owners)),
            dispatch_boundary_ids=tuple(sorted(dispatch)),
            legal_repair_cut_ids=tuple(frontier),
            preservation_node_ids=tuple(sorted({
                node_id for member in member_units for node_id in member.preservation_node_ids
            })),
            interaction_witnesses=component_witnesses,
        )
        binding_graph.components[component_id] = component
        for member in member_units:
            binding_graph.replace_unit(member.unit_id, repair_component_id=component_id)


def build_binding_graph(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    *,
    progress_callback: Callable[[str, str, float | None], None] | None = None,
) -> BindingGraph:
    total_started = time.perf_counter()
    requirement_hash = requirement_graph.semantic_layer_hash()
    program_hash = program_graph.program_hash()
    binding_graph = BindingGraph(
        requirement_graph_hash=requirement_hash,
        program_graph_hash=program_hash,
        assignment_id=requirement_graph.assignment_id,
    )
    queue = deque(requirement_graph.feasible_path_obligations())
    seen: set[str] = set()
    cuts_by_unit: dict[str, CausalRepairCut] = {}
    impact_cache: dict[tuple[str, ...], ImpactCone] = {}
    cut_basis_cache: dict[
        tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], dict[str, object]
    ] = {}
    cut_node_metric_cache: dict[str, tuple[int, int]] = {}
    trigger_path_cache: dict[tuple[str, str], bool] = {}
    bypass_path_cache: dict[tuple[str, str, str], tuple[str, ...] | None] = {}
    units_started = time.perf_counter()
    if progress_callback is not None:
        progress_callback("unit_materialization", "in_progress", None)
    while queue:
        obligation = queue.popleft()
        if obligation.path_obligation_id in seen:
            continue
        seen.add(obligation.path_obligation_id)
        if obligation.requirement_graph_hash != requirement_hash:
            raise ValueError(f"stale requirement path obligation {obligation.path_obligation_id}")
        if obligation.program_graph_hash != program_hash:
            raise ValueError(f"stale program path obligation {obligation.path_obligation_id}")
        unit, oracle, scenario, frontiers = bind_path_obligation(
            obligation,
            requirement_graph,
            program_graph,
            cut_results=cuts_by_unit,
            impact_cache=impact_cache,
            cut_basis_cache=cut_basis_cache,
            cut_node_metric_cache=cut_node_metric_cache,
            trigger_path_cache=trigger_path_cache,
            bypass_path_cache=bypass_path_cache,
        )
        binding_graph.add_unit(unit)
        binding_graph.oracles[oracle.oracle_id] = oracle
        if scenario:
            binding_graph.scenarios[scenario.scenario_id] = scenario
        for frontier in frontiers:
            binding_graph.add_frontier(frontier)
        if progress_callback is not None and len(seen) % 128 == 0:
            progress_callback(
                "unit_materialization", "progress", time.perf_counter() - units_started
            )
    units_seconds = time.perf_counter() - units_started
    if progress_callback is not None:
        progress_callback("unit_materialization", "complete", units_seconds)
    validation_started = time.perf_counter()
    if progress_callback is not None:
        progress_callback("closure_validation", "in_progress", None)
    expected = {item.path_obligation_id for item in requirement_graph.feasible_path_obligations()}
    actual = set(binding_graph.by_path_obligation)
    missing = expected - actual
    if missing:
        raise RuntimeError(f"binding construction omitted path obligations: {sorted(missing)}")
    duplicates = {
        path_id: unit_ids
        for path_id, unit_ids in binding_graph.by_path_obligation.items()
        if len(unit_ids) != 1
    }
    if duplicates:
        raise RuntimeError(f"binding construction duplicated exact paths: {duplicates}")
    validation_seconds = time.perf_counter() - validation_started
    if progress_callback is not None:
        progress_callback("closure_validation", "complete", validation_seconds)
    components_started = time.perf_counter()
    if progress_callback is not None:
        progress_callback("repair_components", "in_progress", None)
    _build_components(binding_graph, program_graph, cuts_by_unit)
    components_seconds = time.perf_counter() - components_started
    if progress_callback is not None:
        progress_callback("repair_components", "complete", components_seconds)
    binding_graph.build_timings = {
        "unit_materialization_seconds": units_seconds,
        "closure_validation_seconds": validation_seconds,
        "repair_components_seconds": components_seconds,
        "total_seconds": time.perf_counter() - total_started,
    }
    binding_graph.build_stats = {
        "cut_basis_count": len(cut_basis_cache),
        "cut_node_metric_count": len(cut_node_metric_cache),
        "trigger_path_cache_count": len(trigger_path_cache),
        "bypass_path_cache_count": len(bypass_path_cache),
    }
    return binding_graph


def build_active_binding_graph(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    *,
    previous: BindingGraph | None,
    affected_leaf_ids: set[str],
    affected_path_ids: set[str],
    max_target_units: int,
    max_preservation_units: int,
    deadline: float | None = None,
) -> BindingGraph:
    """Build the bounded executable part of the Requirement x Program product."""

    if max_target_units < 1 or max_preservation_units < 1:
        raise ValueError("active binding limits must be positive")
    requirement_hash = requirement_graph.semantic_layer_hash()
    program_hash = program_graph.program_hash()
    result = BindingGraph(
        requirement_graph_hash=requirement_hash,
        program_graph_hash=program_hash,
        assignment_id=requirement_graph.assignment_id,
        version=(previous.version + 1) if previous else 1,
    )
    current_paths = requirement_graph.path_obligations
    cuts_by_unit: dict[str, CausalRepairCut] = {}
    if previous is not None:
        for unit in previous.units.values():
            if (
                unit.leaf_id in affected_leaf_ids
                or unit.path_obligation_id in affected_path_ids
                or unit.path_obligation_id not in current_paths
                or unit.path_class_id not in program_graph.path_classes
            ):
                continue
            reused = replace(
                unit,
                requirement_graph_hash=requirement_hash,
                program_graph_hash=program_hash,
            )
            result.add_unit(reused)
            if unit.oracle_id in previous.oracles:
                result.oracles[unit.oracle_id] = previous.oracles[unit.oracle_id]
            for scenario_id in unit.scenario_ids:
                if scenario_id in previous.scenarios:
                    result.scenarios[scenario_id] = previous.scenarios[scenario_id]
    bound_paths = set(result.by_path_obligation)
    obligations = [
        item for item in requirement_graph.feasible_path_obligations()
        if item.path_obligation_id not in bound_paths
        and (
            previous is None
            or not affected_leaf_ids and not affected_path_ids
            or item.leaf_id in affected_leaf_ids
            or item.path_obligation_id in affected_path_ids
        )
    ]
    obligations.sort(key=lambda item: (
        requirement_graph.leaves[item.leaf_id].authority_class.value == "PRESERVATION",
        -requirement_graph.leaves[item.leaf_id].weight,
        item.path_obligation_id,
    ))
    target_count = sum(
        requirement_graph.leaves[item.leaf_id].authority_class.value != "PRESERVATION"
        for item in result.units.values()
    )
    preservation_count = len(result.units) - target_count
    deferred_groups: dict[tuple[tuple[str, ...], str, bool], list[BindingUnit]] = {}
    deadline_truncated = False
    for obligation in obligations:
        if deadline is not None and time.monotonic() >= deadline:
            deadline_truncated = True
            break
        leaf = requirement_graph.leaves[obligation.leaf_id]
        preservation = leaf.authority_class.value == "PRESERVATION"
        if preservation and preservation_count >= max_preservation_units:
            continue
        if not preservation and target_count >= max_target_units:
            continue
        unit, oracle, scenario, frontiers = bind_path_obligation(
            obligation, requirement_graph, program_graph,
            cut_results=cuts_by_unit,
        )
        if not obligation.base_feasible:
            status = BindingStatus.INFEASIBLE
        elif unit.projection_witness.compatible and oracle.active_and_trusted and scenario:
            status = BindingStatus.ACTIVE
        elif scenario and oracle.active_and_trusted:
            status = BindingStatus.CANDIDATE
        else:
            status = BindingStatus.DEFERRED
        unit = replace(unit, status=status.value)
        result.add_unit(unit)
        result.oracles[oracle.oracle_id] = oracle
        if scenario:
            result.scenarios[scenario.scenario_id] = scenario
        if status == BindingStatus.DEFERRED:
            channels = tuple(sorted(map(str, leaf.observation_contract.get("channels", ()))))
            key = (channels, unit.projection_witness.reason, leaf.mandatory)
            deferred_groups.setdefault(key, []).append(unit)
            for frontier in frontiers:
                if frontier.kind not in {"ORACLE_FRONTIER", "SCENARIO_MATERIALIZATION"}:
                    result.add_frontier(replace(frontier, hard=False))
        else:
            for frontier in frontiers:
                result.add_frontier(frontier)
        if preservation:
            preservation_count += 1
        else:
            target_count += 1
    for (channels, reason, mandatory), units in deferred_groups.items():
        leaf_ids = tuple(sorted({item.leaf_id for item in units}))
        unit_ids = tuple(sorted(item.unit_id for item in units))
        frontier_id = stable_id("oracle-frontier", channels, reason, leaf_ids)
        result.oracle_frontiers[frontier_id] = OracleFrontier(
            frontier_id=frontier_id, leaf_ids=leaf_ids, unit_ids=unit_ids,
            observation_channels=channels, reason=reason,
            hard=bool(mandatory and any(
                requirement_graph.leaves[leaf_id].authority.value in {"A", "B"}
                for leaf_id in leaf_ids
            )),
        )
    if deadline_truncated:
        result.add_frontier(Frontier(
            frontier_id=stable_id(
                "binding-frontier", "ANALYSIS_TRUNCATED", requirement_hash,
                program_hash,
            ),
            kind="ANALYSIS_TRUNCATED",
            owner_id=requirement_graph.assignment_id,
            reason="active binding deadline reached",
            resolution_action="resume affected binding join on demand",
            hard=False,
            evidence_ids=(),
        ))
    _build_components(result, program_graph, cuts_by_unit)
    result.build_stats = {
        "candidate_count": sum(item.status == BindingStatus.CANDIDATE for item in result.units.values()),
        "active_count": sum(item.status == BindingStatus.ACTIVE for item in result.units.values()),
        "deferred_count": sum(item.status == BindingStatus.DEFERRED for item in result.units.values()),
        "infeasible_count": sum(item.status == BindingStatus.INFEASIBLE for item in result.units.values()),
        "oracle_frontier_count": len(result.oracle_frontiers),
        "deadline_truncated": int(deadline_truncated),
    }
    return result


def incremental_rebind(
    previous: BindingGraph,
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    *,
    impacted_path_obligation_ids: Iterable[str],
) -> tuple[BindingGraph, dict[str, list[str]]]:
    impacted = set(impacted_path_obligation_ids)
    current = build_binding_graph(requirement_graph, program_graph)
    old_by_path = {
        unit.path_obligation_id: unit for unit in previous.units.values()
    }
    new_by_path = {
        unit.path_obligation_id: unit for unit in current.units.values()
    }
    delta = {
        "invalidated_unit_ids": sorted(
            old_by_path[path_id].unit_id
            for path_id in impacted
            if path_id in old_by_path
        ),
        "rebound_unit_ids": sorted(
            new_by_path[path_id].unit_id
            for path_id in impacted
            if path_id in new_by_path
        ),
        "added_unit_ids": sorted(
            unit.unit_id for path_id, unit in new_by_path.items() if path_id not in old_by_path
        ),
        "removed_unit_ids": sorted(
            unit.unit_id for path_id, unit in old_by_path.items() if path_id not in new_by_path
        ),
    }
    current.version = previous.version + 1
    return current, delta
