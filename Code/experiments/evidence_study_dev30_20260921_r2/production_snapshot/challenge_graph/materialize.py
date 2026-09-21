from __future__ import annotations

import time
from dataclasses import replace

from reachpatch.models.base import stable_id
from reachpatch.models.evidence import ObservationContract, OutcomeStatus, PublicEvidence
from reachpatch.models.graphs import (
    BindingGap, BindingGraph, BindingRecoveryAction, BindingStatus,
    BindingUnit, ChallengeCell, ChallengeGraph, ChallengeStatus,
    ExecutableScenario, ProgramEdgeKind, ProgramGraph, RequirementGraph,
    RequirementLeaf,
)
from reachpatch.oracle.resolve import resolve_oracle

from .input_recipes import compile_input_recipe


_IMPACT_EVIDENCE_PREFIX = "impact-preservation:"
_TARGET_IMPACT_EVIDENCE_PREFIX = "impact-target:"


def _enclosing_function_id(program_graph: ProgramGraph, node_id: str) -> str | None:
    current = node_id
    seen = {current}
    while current in program_graph.nodes:
        node = program_graph.nodes[current]
        if node.kind.value in {"FUNCTION", "METHOD"}:
            return current
        parent = next((
            edge.source_id for edge in program_graph.edges.values()
            if edge.target_id == current
            and edge.kind is ProgramEdgeKind.CONTAINS
            and edge.source_id in program_graph.nodes
            and edge.source_id not in seen
        ), None)
        if parent is None:
            return None
        seen.add(parent)
        current = parent
    return None


def _add_impact_bindings(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    binding_graph: BindingGraph,
    public_evidence: PublicEvidence,
) -> BindingGraph:
    impact = program_graph.impact_cone
    if impact is None:
        return binding_graph
    checks = {check.check_id: check for check in public_evidence.checks}
    units = dict(binding_graph.units)
    source_units = tuple(units.values())
    impact_groups = (
        ("DIRECT_CALLER", impact.direct_caller_ids),
        ("RETURN_CONSUMER", impact.return_consumer_ids),
        ("EXCEPTION_HANDLER", impact.exception_handler_ids),
        ("STATE_READER", impact.state_reader_ids),
        ("REVERSE_DISPATCH", impact.reverse_dispatch_ids),
        ("RENDERING_CONSUMER", impact.rendering_consumer_ids),
    )
    for source in source_units:
        source_requirement = requirement_graph.leaves.get(source.requirement_id)
        if source_requirement is None:
            continue
        if not source.changed_hunk_ids:
            continue
        executable_inputs = tuple(
            check_id for check_id in source.preservation_check_ids
            if check_id in checks and checks[check_id].concrete_input is not None
        )
        if source_requirement.preservation and not executable_inputs:
            continue
        if not source_requirement.preservation and not source.branch_partition_ids:
            continue
        for impact_kind, context_ids in impact_groups:
            contexts_by_function: dict[str, str] = {}
            for context_id in sorted(context_ids):
                function_id = _enclosing_function_id(program_graph, context_id)
                if function_id is None:
                    continue
                contexts_by_function.setdefault(function_id, context_id)
            for function_id, context_id in sorted(contexts_by_function.items()):
                path = next((
                    candidate for candidate in program_graph.path_classes.values()
                    if function_id in candidate.node_ids
                ), None)
                if path is None:
                    continue
                function = program_graph.nodes[function_id]
                marker = f"{_IMPACT_EVIDENCE_PREFIX}{impact_kind}:{context_id}"
                requirement_id = stable_id(
                    "impact-preservation-requirement",
                    source.requirement_id, impact_kind, context_id,
                )
                requirement = requirement_graph.leaves.get(requirement_id)
                if requirement is None:
                    requirement = RequirementLeaf(
                        requirement_id=requirement_id,
                        kind="PRESERVATION",
                        quantifier="FOR_EVIDENCE_INPUTS",
                        variables=(),
                        domain_constraints=(),
                        preconditions=(),
                        operation=function.symbol,
                        expected_observation=ObservationContract(
                            relation=(
                                f"stable baseline behavior of {function.symbol} "
                                f"is preserved through {impact_kind}"
                            ),
                            expected=None,
                            observable="baseline_relation",
                            comparator="preserves",
                        ),
                        exception_contract=None,
                        preservation=True,
                        authority="PROVISIONAL",
                        evidence_ids=(marker,),
                        witness_ids=(),
                        status=OutcomeStatus.PROVISIONAL,
                        hard=True,
                    )
                    requirement_graph.leaves[requirement_id] = requirement
                symbols = tuple(dict.fromkeys(
                    (context_id, function_id) + path.node_ids
                ))
                binding_id = stable_id(
                    "binding", requirement_id, path.path_class_id,
                    symbols, source.changed_hunk_ids, executable_inputs,
                )
                units[binding_id] = BindingUnit(
                    binding_id=binding_id,
                    requirement_id=requirement_id,
                    path_class_id=path.path_class_id,
                    program_symbol_ids=symbols,
                    branch_partition_ids=source.branch_partition_ids,
                    changed_hunk_ids=source.changed_hunk_ids,
                    causal_cut_ids=source.causal_cut_ids,
                    impact_cone_ids=(impact.cone_id,),
                    target_check_ids=(),
                    preservation_check_ids=executable_inputs,
                    challenge_ids=(),
                    trace_bundle_ids=(),
                    counterexample_ids=(),
                    authority=requirement.authority,
                    status=BindingStatus.STATIC_ACTIONABLE,
                    evidence_ids=requirement.evidence_ids,
                )
                if not source_requirement.preservation:
                    target_marker = (
                        f"{_TARGET_IMPACT_EVIDENCE_PREFIX}"
                        f"{impact_kind}:{context_id}"
                    )
                    target_binding_id = stable_id(
                        "binding", source_requirement.requirement_id,
                        path.path_class_id, symbols, source.changed_hunk_ids,
                        target_marker,
                    )
                    units[target_binding_id] = BindingUnit(
                        binding_id=target_binding_id,
                        requirement_id=source_requirement.requirement_id,
                        path_class_id=path.path_class_id,
                        program_symbol_ids=symbols,
                        branch_partition_ids=source.branch_partition_ids,
                        changed_hunk_ids=source.changed_hunk_ids,
                        causal_cut_ids=source.causal_cut_ids,
                        impact_cone_ids=(impact.cone_id,),
                        target_check_ids=source.target_check_ids,
                        preservation_check_ids=(),
                        challenge_ids=(),
                        trace_bundle_ids=(),
                        counterexample_ids=(),
                        authority=source_requirement.authority,
                        status=BindingStatus.STATIC_ACTIONABLE,
                        evidence_ids=tuple(dict.fromkeys(
                            source.evidence_ids + (target_marker,)
                        )),
                    )
    return BindingGraph(
        patch_hash=binding_graph.patch_hash,
        requirement_hash=requirement_graph.graph_hash(),
        program_hash=program_graph.graph_hash(),
        units=units,
        gaps=binding_graph.gaps,
    )


def materialize_challenge_graph(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    binding_graph: BindingGraph,
    public_evidence: PublicEvidence,
) -> tuple[BindingGraph, ChallengeGraph, float]:
    started = time.monotonic()
    binding_graph = _add_impact_bindings(
        requirement_graph, program_graph, binding_graph, public_evidence,
    )
    cells: dict[str, ChallengeCell] = {}
    units = dict(binding_graph.units)
    gaps = list(binding_graph.gaps)
    frontiers: dict[str, tuple[str, ...]] = {}
    checks = {check.check_id: check for check in public_evidence.checks}
    for binding_id, binding in sorted(units.items()):
        requirement = requirement_graph.leaves[binding.requirement_id]
        path = program_graph.path_classes.get(binding.path_class_id)
        if path is None:
            continue
        challenge_ids: list[str] = []
        impact_markers = tuple(
            item for item in dict.fromkeys(
                requirement.evidence_ids + binding.evidence_ids
            )
            if item.startswith((
                _IMPACT_EVIDENCE_PREFIX,
                _TARGET_IMPACT_EVIDENCE_PREFIX,
            ))
        )
        if impact_markers:
            specifications = []
            partition_predicate = next((
                requirement_graph.challenge_partitions[partition_id].predicate
                for partition_id in binding.branch_partition_ids
                if partition_id in requirement_graph.challenge_partitions
                and requirement_graph.challenge_partitions[partition_id].kind
                == "BRANCH_TRUE"
            ), None)
            for marker in impact_markers:
                prefix = (
                    _IMPACT_EVIDENCE_PREFIX
                    if marker.startswith(_IMPACT_EVIDENCE_PREFIX)
                    else _TARGET_IMPACT_EVIDENCE_PREFIX
                )
                impact_kind, context_node_id = marker.removeprefix(prefix).split(":", 1)
                specifications.append(
                    (
                        impact_kind, partition_predicate,
                        context_node_id, "IMPACT_CONE",
                    )
                )
        else:
            specifications = [
                ("PUBLIC_REPLAY", None, None, "PUBLIC_CHECK"),
            ]
            if requirement.witness_ids and any(
                record.source == "issue"
                and record.evidence_id in requirement.evidence_ids
                for record in public_evidence.records
            ):
                specifications.append(
                    ("ISSUE_WITNESS", None, None, "ISSUE_WITNESS")
                )
            specifications += [
                (
                    item.kind, item.predicate, item.source_branch_id,
                    "DIFF_PARTITION",
                )
                for item in (
                    requirement_graph.challenge_partitions[partition_id]
                    for partition_id in binding.branch_partition_ids
                    if partition_id in requirement_graph.challenge_partitions
                )
            ]
        for kind, predicate, context_node_id, origin in specifications:
            frontier_id = stable_id(
                "challenge-frontier", binding_id, kind, context_node_id,
            )
            recipe_ids: list[str] = []
            for recipe_index in range(3):
                result = compile_input_recipe(
                    requirement, path, binding, program_graph, public_evidence,
                    partition_kind=kind,
                    partition_predicate=predicate,
                    recipe_index=recipe_index,
                    context_node_id=context_node_id,
                )
                if result.recipe is None:
                    if result.frontier and not recipe_ids:
                        recovery = {
                            "DIRECT_CALLER": BindingRecoveryAction.EXPAND_DIRECT_CALLER,
                            "RETURN_CONSUMER": BindingRecoveryAction.EXPAND_RETURN_CONSUMER,
                            "EXCEPTION_HANDLER": BindingRecoveryAction.EXPAND_EXCEPTION_HANDLER,
                            "REVERSE_DISPATCH": BindingRecoveryAction.EXPAND_PROTOCOL_DISPATCH,
                        }.get(kind, BindingRecoveryAction.TRACE_PUBLIC_CHECK)
                        gap = BindingGap(
                            requirement.requirement_id,
                            f"INPUT_RECIPE_FRONTIER:{kind}:{result.frontier}",
                            requirement.hard,
                            tuple(dict.fromkeys(
                                ((context_node_id,) if context_node_id else ())
                                + binding.program_symbol_ids
                            )),
                            (recovery,),
                        )
                        if gap not in gaps:
                            gaps.append(gap)
                    break
                partition = next((
                    item for item in requirement_graph.challenge_partitions.values()
                    if item.requirement_id == requirement.requirement_id
                    and item.kind == kind
                    and item.path_class_id == binding.path_class_id
                ), None)
                recipe_ids.append(result.recipe.recipe_id)
                if result.unreachable and partition is not None:
                    requirement_graph.challenge_partitions[
                        partition.partition_id
                    ] = replace(partition, status=OutcomeStatus.UNREACHABLE)
                check_id = result.recipe.source_check_id
                check = checks.get(check_id) if check_id else None
                use_public_assertion = kind == "PUBLIC_REPLAY"
                oracle_evidence = (
                    PublicEvidence(
                        records=public_evidence.records,
                        checks=(check,),
                        api_contracts=public_evidence.api_contracts,
                        baseline_contracts=public_evidence.baseline_contracts,
                    )
                    if check is not None and use_public_assertion else PublicEvidence(
                        records=public_evidence.records,
                        api_contracts=public_evidence.api_contracts,
                        baseline_contracts=public_evidence.baseline_contracts,
                    )
                )
                structured_witness = (
                    result.recipe.concrete_input.get("__reachpatch_issue_witness__")
                    if isinstance(result.recipe.concrete_input, dict) else None
                )
                oracle_resolution = resolve_oracle(
                    requirement, oracle_evidence, None,
                    witness_id=(
                        str(structured_witness["witness_id"])
                        if isinstance(structured_witness, dict) else None
                    ),
                )
                if oracle_resolution.oracle is None:
                    continue
                scenario = ExecutableScenario(
                    scenario_id=stable_id("scenario", result.recipe.recipe_id),
                    command=result.recipe.command,
                    cwd=check.cwd if check and use_public_assertion else ".",
                    environment=result.recipe.environment,
                    timeout_seconds=check.timeout_seconds if check else 60.0,
                )
                challenge_id = stable_id(
                    "challenge", binding_graph.patch_hash, binding_id,
                    path.path_class_id, kind, context_node_id,
                    result.recipe.recipe_id, oracle_resolution.oracle.oracle_id,
                )
                cell = ChallengeCell(
                    challenge_id=challenge_id,
                    patch_hash=binding_graph.patch_hash,
                    requirement_id=requirement.requirement_id,
                    binding_id=binding_id,
                    path_class_id=path.path_class_id,
                    changed_hunk_ids=binding.changed_hunk_ids,
                    kind=("PRESERVATION" if requirement.preservation else kind),
                    input_recipe=result.recipe,
                    execution_scenario=scenario,
                    observation_contract=requirement.expected_observation,
                    oracle=oracle_resolution.oracle,
                    authority=oracle_resolution.oracle.authority,
                    baseline_outcome=None,
                    patched_outcome=None,
                    trace_bundle_id=None,
                    stability_runs=0,
                    terminal_status=(
                        ChallengeStatus.UNREACHABLE if result.unreachable else
                        ChallengeStatus.EXPLORATION_ONLY
                        if oracle_resolution.exploration_only else ChallengeStatus.PENDING
                    ),
                    # Only reporter/public executable scenarios can certify a
                    # hard target.  AST-derived branch/impact partitions are
                    # useful challenge probes, but without an independent
                    # Authority A/B/C oracle they must remain soft validation
                    # work and cannot block Reach.
                    hard=(
                        requirement.hard
                        and origin in {"PUBLIC_CHECK", "ISSUE_WITNESS"}
                    ),
                    origin=origin,
                )
                cells[challenge_id] = cell
                challenge_ids.append(challenge_id)
            frontiers[frontier_id] = tuple(recipe_ids)
        units[binding_id] = replace(binding, challenge_ids=tuple(challenge_ids))
    actionable_requirements = {
        cell.requirement_id for cell in cells.values()
        if cell.oracle.trusted and cell.oracle.executable
        and cell.execution_scenario.command
    }
    gaps = [
        gap for gap in gaps
        if not (
            (
                gap.gap_type == "NO_EXECUTABLE_CHECK"
                or gap.gap_type.startswith(
                    "INPUT_RECIPE_FRONTIER:PUBLIC_REPLAY:"
                )
            )
            and gap.requirement_id in actionable_requirements
        )
    ]
    updated_binding = BindingGraph(
        patch_hash=binding_graph.patch_hash,
        requirement_hash=requirement_graph.graph_hash(),
        program_hash=program_graph.graph_hash(),
        units=units,
        gaps=tuple(gaps),
    )
    challenge = ChallengeGraph(
        patch_hash=binding_graph.patch_hash,
        binding_hash=updated_binding.graph_hash(),
        cells=cells,
        frontier_attempts=frontiers,
    )
    return updated_binding, challenge, time.monotonic() - started


def update_challenge_graph_after_diff(
    previous: ChallengeGraph,
    previous_binding: BindingGraph,
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    binding_graph: BindingGraph,
    public_evidence: PublicEvidence,
    changed_binding_ids: tuple[str, ...],
) -> tuple[BindingGraph, ChallengeGraph, float]:
    """Recompile changed bindings and retarget retained recipes to one new patch."""

    started = time.monotonic()
    changed = set(changed_binding_ids)
    new_or_changed = changed | (set(binding_graph.units) - set(previous_binding.units))
    subset = BindingGraph(
        patch_hash=binding_graph.patch_hash,
        requirement_hash=binding_graph.requirement_hash,
        program_hash=binding_graph.program_hash,
        units={
            binding_id: unit for binding_id, unit in binding_graph.units.items()
            if binding_id in new_or_changed
        },
        gaps=tuple(
            gap for gap in binding_graph.gaps
            if any(
                unit.requirement_id == gap.requirement_id
                for unit in binding_graph.units.values()
                if unit.binding_id in new_or_changed
            )
        ),
    )
    rebuilt_binding, rebuilt_challenge, _ = materialize_challenge_graph(
        requirement_graph, program_graph, subset, public_evidence,
    )
    cells = dict(rebuilt_challenge.cells)
    if binding_graph.patch_hash == previous.patch_hash:
        # Context expansion may rebuild a BindingUnit because its local
        # Program slice grew, even though the working patch and executable
        # obligation did not change. Preserve evidence only for the exact
        # challenge identity. A new patch produces different challenge IDs
        # and therefore cannot inherit a PASS from the previous revision.
        for challenge_id, rebuilt in tuple(cells.items()):
            old = previous.cells.get(challenge_id)
            if old is None:
                continue
            cells[challenge_id] = replace(
                rebuilt,
                oracle=old.oracle,
                authority=old.authority,
                baseline_outcome=old.baseline_outcome,
                patched_outcome=old.patched_outcome,
                trace_bundle_id=old.trace_bundle_id,
                stability_runs=old.stability_runs,
                terminal_status=old.terminal_status,
            )
    frontiers = (
        dict(previous.frontier_attempts)
        if binding_graph.patch_hash == previous.patch_hash else {}
    )
    frontiers.update(rebuilt_challenge.frontier_attempts)
    units = dict(binding_graph.units)
    units.update(rebuilt_binding.units)

    previous_cells_by_binding: dict[str, list[ChallengeCell]] = {}
    for cell in previous.active_cells():
        previous_cells_by_binding.setdefault(cell.binding_id, []).append(cell)
    for binding_id, unit in sorted(binding_graph.units.items()):
        if binding_id in new_or_changed:
            continue
        retained_ids: list[str] = []
        for old in sorted(
            previous_cells_by_binding.get(binding_id, ()),
            key=lambda cell: cell.challenge_id,
        ):
            if binding_graph.patch_hash == previous.patch_hash:
                challenge_id = old.challenge_id
                cell = old
            else:
                challenge_id = stable_id(
                    "challenge", binding_graph.patch_hash, binding_id,
                    old.path_class_id, old.input_recipe.recipe_id,
                    old.oracle.oracle_id, old.origin,
                )
                cell = replace(
                    old,
                    challenge_id=challenge_id,
                    patch_hash=binding_graph.patch_hash,
                    changed_hunk_ids=unit.changed_hunk_ids,
                    baseline_outcome=None,
                    patched_outcome=None,
                    trace_bundle_id=None,
                    stability_runs=0,
                    terminal_status=(
                        ChallengeStatus.UNREACHABLE
                        if old.terminal_status is ChallengeStatus.UNREACHABLE
                        else ChallengeStatus.EXPLORATION_ONLY
                        if not old.oracle.trusted or not old.oracle.executable
                        else ChallengeStatus.PENDING
                    ),
                )
            cells[challenge_id] = cell
            retained_ids.append(challenge_id)
            frontier_id = stable_id(
                "challenge-frontier", binding_id,
                old.input_recipe.kind,
                old.input_recipe.trace_symbols,
            )
            frontiers.setdefault(frontier_id, ())
            frontiers[frontier_id] = tuple(dict.fromkeys(
                frontiers[frontier_id] + (old.input_recipe.recipe_id,)
            ))
        units[binding_id] = replace(unit, challenge_ids=tuple(retained_ids))

    updated_binding = BindingGraph(
        patch_hash=binding_graph.patch_hash,
        requirement_hash=requirement_graph.graph_hash(),
        program_hash=program_graph.graph_hash(),
        units=units,
        gaps=tuple(dict.fromkeys(binding_graph.gaps + rebuilt_binding.gaps)),
    )
    challenge = ChallengeGraph(
        patch_hash=binding_graph.patch_hash,
        binding_hash=updated_binding.graph_hash(),
        cells=cells,
        frontier_attempts=frontiers,
    )
    return updated_binding, challenge, time.monotonic() - started
