from __future__ import annotations

from reachpatch.challenge_graph.models import (
    challenge_obligation_key, closed_challenge_obligation_keys,
    open_high_challenge_ids,
)
from reachpatch.models.evidence import OutcomeStatus, PairClassification
from reachpatch.models.graphs import BindingStatus, ChallengeStatus
from reachpatch.models.reach_avoid import (
    AvoidEvaluation, AvoidKind, ProgressEvaluation, ReachAvoidState,
    ReachEvaluation, TransitionEvidence, TrialTransition,
)


_CONFIRMED_BINDING = {
    BindingStatus.EXECUTION_CONFIRMED,
    BindingStatus.TARGET_FAILING,
    BindingStatus.TARGET_PASSING,
    BindingStatus.PRESERVATION_RISK,
    BindingStatus.COUNTEREXAMPLE_OPEN,
}


def _active_cells(state: ReachAvoidState):
    return state.graph_stack.challenge_graph.active_cells()


def evaluate_reach(state: ReachAvoidState) -> ReachEvaluation:
    reasons: list[str] = []
    checkpoint = state.working_checkpoint
    if not checkpoint.canonical_diff.strip():
        reasons.append("working patch is empty")
    if not checkpoint.evidence.mechanical_pass:
        reasons.append("mechanical checks are not passing")
    try:
        state.graph_stack.validate()
    except RuntimeError as exc:
        reasons.append(str(exc))

    requirements = state.graph_stack.requirement_graph.leaves
    cells = _active_cells(state)
    bindings = state.graph_stack.binding_graph.units
    hard_targets = [
        leaf for leaf in requirements.values()
        if leaf.hard and not leaf.preservation and leaf.authority in {"A", "B", "C"}
    ]
    executable = {
        leaf.requirement_id: [
            cell for cell in cells
            if cell.requirement_id == leaf.requirement_id
            and cell.oracle.trusted and cell.oracle.executable
        ]
        for leaf in hard_targets
    }
    trusted_target_count = sum(bool(value) for value in executable.values())
    if not hard_targets or trusted_target_count != len(hard_targets):
        reasons.append("one or more HARD targets lack a trusted executable oracle")
    stable_pass = 0
    confirmed = 0
    for leaf in hard_targets:
        target_cells = executable[leaf.requirement_id]
        if not target_cells:
            continue
        obligations: dict[str, list] = {}
        for cell in target_cells:
            obligations.setdefault(challenge_obligation_key(cell), []).append(cell)
        if obligations and all(
            any(
                cell.terminal_status is ChallengeStatus.UNREACHABLE
                or (
                    cell.terminal_status is ChallengeStatus.PASS
                    and cell.stability_runs >= 2
                    and cell.patched_outcome is OutcomeStatus.PASS
                )
                for cell in equivalent_cells
            )
            for equivalent_cells in obligations.values()
        ):
            stable_pass += 1
        else:
            reasons.append(f"HARD target is not stably passing: {leaf.requirement_id}")
        if obligations and all(
            any(
                cell.terminal_status is ChallengeStatus.UNREACHABLE
                or (
                    cell.binding_id in bindings
                    and bindings[cell.binding_id].status in _CONFIRMED_BINDING
                    and cell.trace_bundle_id is not None
                )
                for cell in equivalent_cells
            )
            for equivalent_cells in obligations.values()
        ):
            confirmed += 1
        else:
            reasons.append(f"HARD target lacks execution-confirmed binding: {leaf.requirement_id}")

    by_check = {}
    for cell in cells:
        binding = bindings.get(cell.binding_id)
        if binding is None:
            continue
        check_id = cell.input_recipe.source_check_id
        if check_id is not None:
            by_check.setdefault(check_id, []).append(cell)
    for check_id in sorted(state.locked_checks.target_ids):
        if not by_check.get(check_id) or not any(
            cell.terminal_status is ChallengeStatus.PASS
            and cell.stability_runs >= 2
            and cell.patched_outcome is OutcomeStatus.PASS
            and (
                (cell.origin == "PUBLIC_CHECK" and cell.input_recipe.kind == "PUBLIC_REPLAY")
                or cell.origin == "REGRESSION_REPLAY"
            )
            for cell in by_check[check_id]
        ):
            reasons.append(f"locked target is not passing: {check_id}")
    for check_id in sorted(state.locked_checks.preservation_ids):
        if not by_check.get(check_id) or not any(
            cell.terminal_status is ChallengeStatus.PASS
            and cell.stability_runs >= 2
            and cell.patched_outcome is OutcomeStatus.PASS
            and (
                (cell.origin == "PUBLIC_CHECK" and cell.input_recipe.kind == "PUBLIC_REPLAY")
                or cell.origin == "REGRESSION_REPLAY"
            )
            for cell in by_check[check_id]
        ):
            reasons.append(f"locked preservation is not passing: {check_id}")

    for gap in state.graph_stack.binding_graph.gaps:
        if gap.hard:
            related_cells = [
                cell for cell in cells if cell.requirement_id == gap.requirement_id
            ]
            if related_cells and any(
                cell.terminal_status in {ChallengeStatus.PASS, ChallengeStatus.UNREACHABLE}
                and cell.stability_runs >= 2
                for cell in related_cells
            ):
                continue
            reasons.append(
                f"HARD Requirement has an unresolved BindingGap: "
                f"{gap.requirement_id}:{gap.gap_type}"
            )

    open_counterexamples = [
        item for item in state.counterexamples
        if item.patch_hash == state.graph_stack.patch_hash
        and cells_by_id(cells).get(item.challenge_id, None) is not None
        and cells_by_id(cells)[item.challenge_id].terminal_status is ChallengeStatus.FAIL
    ]
    if open_counterexamples:
        reasons.append("stable counterexample remains open")
    hard_obligations: dict[str, list] = {}
    for cell in cells:
        if cell.hard and cell.oracle.trusted and cell.oracle.executable:
            hard_obligations.setdefault(
                challenge_obligation_key(cell), []
            ).append(cell)
    if open_high_challenge_ids(cells):
        reasons.append("high-priority Challenge remains open")

    active_partition_ids = {
        partition_id for binding in bindings.values()
        for partition_id in binding.branch_partition_ids
    }
    partitions = {
        partition_id: partition
        for partition_id, partition in state.graph_stack.requirement_graph.challenge_partitions.items()
        if partition_id in active_partition_ids
    }
    partition_cells = {
        partition_id: [
            cell for cell in cells
            if partition_id in bindings.get(cell.binding_id, empty_binding()).branch_partition_ids
        ]
        for partition_id in partitions
    }
    closed_obligations = closed_challenge_obligation_keys(cells)
    for partition_id, partition in partitions.items():
        related = partition_cells[partition_id]
        obligation_ids = {
            challenge_obligation_key(cell) for cell in related
        }
        if not obligation_ids or not obligation_ids.issubset(closed_obligations):
            reasons.append(f"diff partition is not closed: {partition_id}")

    impact = state.graph_stack.program_graph.impact_cone
    if impact is not None:
        current_executions = tuple(
            execution for execution in state.observations.by_challenge.values()
            if execution.patch_hash == state.graph_stack.patch_hash
        )
        executed_paths = {
            item for execution in state.observations.by_challenge.values()
            if execution.patch_hash == state.graph_stack.patch_hash
            for item in execution.patched.executed_path_ids
        }
        executed_names = {
            item for execution in current_executions
            for item in execution.patched.executed_symbol_ids
        }
        executed_nodes = {
            edge.source_id for edge in state.graph_stack.program_graph.edges.values()
            if edge.dynamic_confirmed
        } | {
            edge.target_id for edge in state.graph_stack.program_graph.edges.values()
            if edge.dynamic_confirmed
        }
        for node in state.graph_stack.program_graph.nodes.values():
            if node.symbol in executed_names or node.symbol.split(".")[-1] in executed_names:
                executed_nodes.add(node.node_id)
            for line_id in executed_paths:
                path, separator, raw_line = line_id.rpartition(":")
                if separator and raw_line.isdigit() and node.path == path:
                    line = int(raw_line)
                    if node.start_line <= line <= node.end_line:
                        executed_nodes.add(node.node_id)
        for risk_id in (
            impact.direct_caller_ids + impact.return_consumer_ids + impact.exception_handler_ids
            + impact.state_reader_ids + impact.reverse_dispatch_ids
            + impact.rendering_consumer_ids
        ):
            if risk_id not in executed_nodes:
                reasons.append(f"changed behavior consumer was not replayed: {risk_id}")
        executed_checks = {
            check_id
            for cell in cells
            if cell.patch_hash == state.graph_stack.patch_hash
            and cell.trace_bundle_id is not None
            and cell.stability_runs >= 2
            and cell.terminal_status in {ChallengeStatus.PASS, ChallengeStatus.FAIL}
            for check_id in (
                cell.input_recipe.source_check_id,
                *bindings.get(cell.binding_id, empty_binding()).target_check_ids,
                *bindings.get(cell.binding_id, empty_binding()).preservation_check_ids,
            )
            if check_id
        }
        for check_id in impact.public_check_ids:
            if check_id not in executed_checks:
                reasons.append(f"changed public check was not replayed: {check_id}")

    impact_hunks = set(impact.changed_hunk_ids) if impact is not None else set()
    changed_protocol = bool(impact and impact.reverse_dispatch_ids) or any(
        node.metadata.get("protocol")
        for node in state.graph_stack.program_graph.nodes.values()
        if node.node_id in {
            item for binding in bindings.values()
            if set(binding.changed_hunk_ids).intersection(impact_hunks)
            for item in binding.program_symbol_ids
        }
    )
    if changed_protocol:
        kinds = {
            cell.input_recipe.kind for cell in cells
            if cell.terminal_status is ChallengeStatus.PASS
        }
        if not {"FORWARD_DISPATCH", "REVERSE_DISPATCH"}.issubset(kinds):
            reasons.append("protocol change lacks forward and reverse dispatch coverage")
    partition_kinds = {partition.kind for partition in partitions.values()}
    wrapper_required = bool(partition_kinds.intersection({
        "EMPTY", "NONEMPTY", "WRAPPER_TRUTHY", "WRAPPER_FALSY",
    }))
    if wrapper_required:
        passed_kinds = {
            cell.input_recipe.kind for cell in cells
            if cell.terminal_status is ChallengeStatus.PASS
        }
        required = partition_kinds.intersection({
            "EMPTY", "NONEMPTY", "WRAPPER_TRUTHY", "WRAPPER_FALSY",
        })
        if not required.issubset(passed_kinds):
            reasons.append("wrapper change lacks empty/nonempty/truthiness coverage")
    if any(
        not any(
            cell.terminal_status is ChallengeStatus.UNREACHABLE
            or (
                cell.trace_bundle_id is not None
                and cell.stability_runs >= 2
                and cell.terminal_status in {ChallengeStatus.PASS, ChallengeStatus.FAIL}
            )
            for cell in equivalent_cells
        )
        for equivalent_cells in hard_obligations.values()
    ):
        reasons.append("diff closure is not backed by current Challenge executions")
    return ReachEvaluation(
        reached=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        trusted_target_count=trusted_target_count,
        stable_target_pass_count=stable_pass,
        execution_confirmed_target_count=confirmed,
    )


def cells_by_id(cells):
    return {cell.challenge_id: cell for cell in cells}


class _EmptyBinding:
    branch_partition_ids = ()
    program_symbol_ids = ()


def empty_binding():
    return _EmptyBinding()


def evaluate_avoid(
    before: ReachAvoidState,
    trial: TrialTransition,
) -> AvoidEvaluation:
    evidence = trial.evidence
    hard: list[str] = []
    if not evidence.mechanical.passed:
        hard.extend(evidence.mechanical.failure_reasons)
    if evidence.mechanical.forbidden_edit:
        hard.append("forbidden edit")
    if evidence.mechanical.oracle_contamination:
        hard.append("oracle contamination")
    if evidence.mechanical.unsafe_api_break:
        hard.append("unsafe public API break")
    if evidence.mechanical.high_risk_side_effect:
        hard.append("high-risk side effect")
    hard.extend(f"locked target lost: {item}" for item in evidence.locked_targets_lost)
    if hard:
        kind = AvoidKind.HARD_AVOID
        reasons = tuple(dict.fromkeys(hard))
    elif evidence.preservation_regressions:
        kind = AvoidKind.REPAIRABLE_AVOID
        reasons = tuple(
            f"executable preservation regression: {item}"
            for item in evidence.preservation_regressions
        )
    else:
        kind = AvoidKind.NOT_AVOID
        reasons = ()
    return AvoidEvaluation(
        kind=kind,
        reasons=reasons,
        locked_targets_lost=evidence.locked_targets_lost,
        preservation_regressions=evidence.preservation_regressions,
    )


def compare_progress(
    before: ReachAvoidState,
    trial: TrialTransition,
) -> ProgressEvaluation:
    evidence = trial.evidence
    target_delta = len(set(evidence.target_pass_ids_after) - set(evidence.target_pass_ids_before))
    hard_delta = len(set(evidence.hard_pass_ids_after) - set(evidence.hard_pass_ids_before))
    target_hard_delta = len({
        requirement_id
        for requirement_id in set(evidence.hard_pass_ids_after)
        - set(evidence.hard_pass_ids_before)
        if (
            (leaf := trial.graph_stack.requirement_graph.leaves.get(requirement_id))
            is not None
            and not leaf.preservation
        )
    })
    causal = bool(evidence.causal_progress_reasons)
    reasons = []
    if target_delta > 0:
        reasons.append(f"{target_delta} target requirements newly succeed")
    if target_hard_delta > 0:
        reasons.append(f"{target_hard_delta} HARD target requirements newly succeed")
    if evidence.confirmed_failures_closed:
        reasons.append("confirmed failure closed")
    if evidence.counterexamples_closed:
        reasons.append("stable counterexample closed")
    reasons.extend(evidence.causal_progress_reasons)
    return ProgressEvaluation(
        strict_progress=bool(
            target_delta > 0 or target_hard_delta > 0
            or evidence.confirmed_failures_closed or evidence.counterexamples_closed
        ),
        causal_progress=causal,
        target_pass_delta=target_delta,
        hard_requirement_pass_delta=hard_delta,
        closed_failure_ids=evidence.confirmed_failures_closed,
        closed_counterexample_ids=evidence.counterexamples_closed,
        reasons=tuple(reasons),
    )
