from __future__ import annotations

from collections import defaultdict

from reachpatch.models.base import stable_id
from reachpatch.models.controller import LosingCore, ReachAvoidState, RepairIntent, UnitOutcome
from reachpatch.models.enums import OutcomeStatus


_STATUS_PRESSURE = {
    OutcomeStatus.FAIL: 4.0,
    OutcomeStatus.BLOCKED: 3.0,
    OutcomeStatus.UNKNOWN: 2.5,
    OutcomeStatus.FLAKY: 2.5,
    OutcomeStatus.UNKNOWN_EXECUTION: 2.5,
    OutcomeStatus.UNKNOWN_ORACLE: 2.0,
    OutcomeStatus.BLOCKED_EXTERNAL: 2.0,
    OutcomeStatus.UNSUPPORTED: 2.0,
}


def _unit_statuses(state: ReachAvoidState) -> dict[str, list[UnitOutcome]]:
    grouped: dict[str, list[UnitOutcome]] = defaultdict(list)
    for outcome in state.outcomes.values():
        grouped[outcome.unit_id].append(outcome)
    for unit_id in state.binding_graph.units:
        grouped.setdefault(unit_id, [])
    return grouped


def select_losing_core(state: ReachAvoidState) -> LosingCore | None:
    grouped = _unit_statuses(state)
    protected = tuple(sorted(
        (item.path_obligation_id, item.scenario_id or "")
        for item in state.outcomes.values()
        if item.status == OutcomeStatus.PASS
    ))
    ranked: list[tuple[float, str, tuple[str, ...]]] = []
    for component in state.binding_graph.components.values():
        losing = tuple(sorted(
            unit_id
            for unit_id in component.unit_ids
            if not grouped[unit_id]
            or any(item.status != OutcomeStatus.PASS for item in grouped[unit_id])
        ))
        if not losing:
            continue
        pressure = 0.0
        for unit_id in losing:
            leaf = state.requirement_graph.leaves[state.binding_graph.units[unit_id].leaf_id]
            outcomes = grouped[unit_id]
            pressure += leaf.weight
            pressure += max(
                (_STATUS_PRESSURE.get(item.status, 1.0) for item in outcomes),
                default=3.0,
            )
            pressure += len(state.binding_graph.units[unit_id].bypass_path_ids) * 0.5
            pressure += len(state.binding_graph.units[unit_id].frontier_ids) * 0.75
        ranked.append((pressure, component.component_id, losing))
    if not ranked:
        return None
    pressure, component_id, losing = max(ranked, key=lambda item: (item[0], item[1]))
    component = state.binding_graph.components[component_id]
    cuts = [set(state.binding_graph.units[unit_id].repair_cut_node_ids) for unit_id in losing]
    common = set.intersection(*cuts) if cuts else set()
    if not common:
        common = set(component.legal_repair_cut_ids)
    observations = {
        item.outcome_id: item.observation
        for unit_id in losing
        for item in grouped[unit_id]
        if item.status != OutcomeStatus.PASS
    }
    paths = tuple(sorted(
        state.binding_graph.units[unit_id].path_obligation_id for unit_id in losing
    ))
    scenarios = tuple(sorted({
        item.scenario_id
        for unit_id in losing for item in grouped[unit_id]
        if item.scenario_id is not None
    }))
    return LosingCore(
        core_id=stable_id("losing-core", component_id, paths, observations, state.checkpoint.checkpoint_id),
        component_id=component_id,
        unit_ids=losing,
        path_obligation_ids=paths,
        scenario_ids=scenarios,
        common_causal_cut_ids=tuple(sorted(common)),
        protected_pass_pairs=protected,
        preservation_ids=component.preservation_node_ids,
        actual_failure_observations=observations,
        pressure=pressure,
    )


def _mechanism_candidates(state: ReachAvoidState, core: LosingCore) -> list[str]:
    nodes = [
        state.program_graph.nodes[node_id]
        for node_id in core.common_causal_cut_ids
        if node_id in state.program_graph.nodes
    ]
    candidates: list[str] = []
    if any(node.kind in {"branch", "loop"} for node in nodes):
        candidates.append("guard_boundary")
    if any(node.kind in {"protocol_operation", "dispatch_slot"} for node in nodes):
        candidates.append("dispatch_protocol")
    if any(node.kind in {"exception", "handler"} for node in nodes):
        candidates.append("exception_contract")
    if any(node.kind in {"field", "assignment", "state_write"} for node in nodes):
        candidates.append("state_order")
    candidates.extend(("return_relation", "mechanism_rewrite"))
    return list(dict.fromkeys(candidates))


def next_untried_repair_intent(
    state: ReachAvoidState,
    core: LosingCore,
) -> RepairIntent | None:
    attempts = state.mechanism_memory.get(core.core_id, [])
    forbidden_classes = {
        item.mechanism_class for item in attempts if item.forbidden_next
    }
    mechanism = next(
        (item for item in _mechanism_candidates(state, core) if item not in forbidden_classes),
        None,
    )
    if mechanism is None:
        return None
    component = state.binding_graph.components[core.component_id]
    complete_paths = tuple(sorted(
        state.binding_graph.units[unit_id].path_obligation_id
        for unit_id in component.unit_ids
    ))
    frontier_ids = tuple(sorted({
        frontier_id
        for unit_id in core.unit_ids
        for frontier_id in state.binding_graph.units[unit_id].frontier_ids
    }))
    execution_ids = tuple(sorted(
        item.execution_bundle_id
        for item in state.outcomes.values()
        if item.unit_id in core.unit_ids and item.execution_bundle_id
    ))
    forbidden_fingerprints = tuple(sorted({
        item.fingerprint_hash for item in attempts if item.forbidden_next
    }))
    witness = {
        "component_pressure": core.pressure,
        "covered_unit_ids": list(core.unit_ids),
        "complete_component_unit_ids": list(component.unit_ids),
        "cut_coverage": {
            cut_id: sorted(
                unit_id for unit_id in component.unit_ids
                if cut_id in state.binding_graph.units[unit_id].repair_cut_node_ids
            )
            for cut_id in core.common_causal_cut_ids
        },
        "mechanism_candidates": _mechanism_candidates(state, core),
    }
    return RepairIntent(
        intent_id=stable_id(
            "repair-intent", state.checkpoint.checkpoint_id, core.core_id,
            mechanism, forbidden_fingerprints,
        ),
        source_checkpoint_id=state.checkpoint.checkpoint_id,
        losing_core_id=core.core_id,
        component_id=core.component_id,
        losing_path_obligation_ids=core.path_obligation_ids,
        complete_component_path_ids=complete_paths,
        repair_cut_ids=core.common_causal_cut_ids,
        root_mechanism_class=mechanism,
        actual_failure_execution_ids=execution_ids,
        protected_pass_pairs=core.protected_pass_pairs,
        preservation_ids=core.preservation_ids,
        forbidden_fingerprints=forbidden_fingerprints,
        frontier_resolution_ids=frontier_ids,
        selection_witness=witness,
    )
