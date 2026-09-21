from __future__ import annotations

from reachpatch.challenge_graph.models import challenge_obligation_key
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
    """Evaluate only evidence that can certify the current working patch.

    Reach--Avoid uses graph data to choose bounded validation work, not as a
    second, repository-wide specification.  In particular, a static binding
    gap, an unexecuted MAY_EXECUTE consumer, or a pending exploration cell must
    never keep an otherwise proven working patch from being sealed.
    """
    reasons: list[str] = []
    checkpoint = state.working_checkpoint
    if not checkpoint.canonical_diff.strip():
        reasons.append("working patch is empty")
    if not checkpoint.evidence.mechanical_pass:
        reasons.append("mechanical checks are not passing")
    if checkpoint.confirmed_regressions:
        reasons.append("confirmed preservation or locked behavior regression remains")
    requirements = state.graph_stack.requirement_graph.leaves
    cells = _active_cells(state)
    bindings = state.graph_stack.binding_graph.units
    hard_targets = [
        leaf for leaf in requirements.values()
        if leaf.hard and not leaf.preservation and leaf.authority in {"A", "B", "C"}
    ]
    # Atomic obligations are the transition contract.  They retain semantic
    # identity across baseline/incumbent/trial, while graph cells can be
    # regenerated as local graph state is refreshed.  Once trusted atomic
    # evidence exists for a requirement, static or pending cells must be kept
    # in the validation backlog rather than become a second global gate.
    #
    # The cell fallback is only for the initial observation path before any
    # transition has registered atomic evidence.  It deliberately considers
    # executed cells only, so a newly materialized PENDING scenario cannot
    # invalidate a stable result for the current working patch.
    trusted_target_count = 0
    stable_pass = 0
    confirmed = 0
    if not hard_targets:
        reasons.append("one or more HARD targets lack a trusted executable oracle")
    for leaf in hard_targets:
        atomic_items = [
            (obligation, state.atomic_evidence.get(obligation_key))
            for obligation_key, obligation in state.atomic_obligations.items()
            if (
                obligation.requirement_id == leaf.requirement_id
                and obligation.role == "TARGET"
                and obligation.hard
                and obligation.authority in {"A", "B", "C"}
            )
        ]
        if atomic_items:
            trusted_target_count += 1
            all_stable_pass = all(
                evidence is not None
                and evidence.authority in {"A", "B", "C"}
                and evidence.status == "PASS"
                and evidence.stability_runs >= 2
                for _, evidence in atomic_items
            )
            if all_stable_pass:
                stable_pass += 1
                confirmed += 1
            else:
                reasons.append(
                    f"HARD target is not stably passing: {leaf.requirement_id}"
                )
            continue

        target_cells = [
            cell for cell in cells
            if (
                cell.requirement_id == leaf.requirement_id
                and cell.oracle.trusted
                and cell.oracle.executable
                and (
                    cell.trace_bundle_id is not None
                    or cell.terminal_status is not ChallengeStatus.PENDING
                )
            )
        ]
        if not target_cells:
            reasons.append("one or more HARD targets lack a trusted executable oracle")
            continue
        trusted_target_count += 1
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
            reasons.append(
                f"HARD target lacks execution-confirmed binding: {leaf.requirement_id}"
            )

    by_check = {}
    for cell in cells:
        binding = bindings.get(cell.binding_id)
        if binding is None:
            continue
        check_id = cell.input_recipe.source_check_id
        if check_id is not None:
            by_check.setdefault(check_id, []).append(cell)
    atomic_by_check = {}
    for obligation_key, obligation in state.atomic_obligations.items():
        recipe = obligation.input_recipe
        source_check_id = (
            recipe.get("source_check_id")
            if isinstance(recipe, dict)
            else getattr(recipe, "source_check_id", None)
        )
        if source_check_id:
            atomic_by_check.setdefault(source_check_id, []).append((
                obligation,
                state.atomic_evidence.get(obligation_key),
            ))

    def locked_atomic_pass(check_id: str, roles: set[str]) -> bool:
        return any(
            obligation.role in roles
            and obligation.authority in {"A", "B", "C"}
            and evidence is not None
            and evidence.authority in {"A", "B", "C"}
            and evidence.status == "PASS"
            and evidence.stability_runs >= 2
            for obligation, evidence in atomic_by_check.get(check_id, ())
        )

    for check_id in sorted(state.locked_checks.target_ids):
        cell_pass = bool(by_check.get(check_id)) and any(
            cell.terminal_status is ChallengeStatus.PASS
            and cell.stability_runs >= 2
            and cell.patched_outcome is OutcomeStatus.PASS
            and (
                (cell.origin == "PUBLIC_CHECK" and cell.input_recipe.kind == "PUBLIC_REPLAY")
                or cell.origin == "REGRESSION_REPLAY"
            )
            for cell in by_check[check_id]
        )
        if not cell_pass and not locked_atomic_pass(check_id, {"TARGET"}):
            reasons.append(f"locked target is not passing: {check_id}")
    for check_id in sorted(state.locked_checks.preservation_ids):
        cell_pass = bool(by_check.get(check_id)) and any(
            cell.terminal_status is ChallengeStatus.PASS
            and cell.stability_runs >= 2
            and cell.patched_outcome is OutcomeStatus.PASS
            and (
                (cell.origin == "PUBLIC_CHECK" and cell.input_recipe.kind == "PUBLIC_REPLAY")
                or cell.origin == "REGRESSION_REPLAY"
            )
            for cell in by_check[check_id]
        )
        if not cell_pass and not locked_atomic_pass(
            check_id, {"PRESERVATION", "IMPACT"},
        ):
            reasons.append(f"locked preservation is not passing: {check_id}")

    current_cells = cells_by_id(cells)
    open_counterexamples = [
        item for item in state.counterexamples
        if item.patch_hash == state.graph_stack.patch_hash
        and getattr(item, "oracle_authority", getattr(item, "authority", "PROVISIONAL")) in {"A", "B", "C"}
        and current_cells.get(item.challenge_id, None) is not None
        and current_cells[item.challenge_id].terminal_status is ChallengeStatus.FAIL
        and current_cells[item.challenge_id].stability_runs >= 2
    ]
    if open_counterexamples:
        reasons.append("stable counterexample remains open")

    # A selected trusted failure remains a real Reach blocker.  Static
    # uncertainty and provisional review frontiers are instead validation
    # backlog, so they cannot turn into implicit global gates.
    selected = getattr(state, "selected_frontier", None)
    if selected is not None and selected.hard and selected.authority in {"A", "B", "C"}:
        if selected.status.value not in {"CLOSED", "SUPERSEDED"}:
            reasons.append(f"selected trusted repair frontier remains open: {selected.semantic_key}")

    # Graph-backed ChallengeCells remain the complete coverage model, while
    # probes and transition triplets register additional atomic obligations.
    # A trusted hard atomic result can never be ignored merely because it was
    # produced through the probe path rather than an existing public cell.
    for obligation_key, obligation in state.atomic_obligations.items():
        if not obligation.hard or obligation.authority not in {"A", "B", "C"}:
            continue
        evidence = state.atomic_evidence.get(obligation_key)
        if evidence is None:
            reasons.append(f"trusted atomic obligation was not executed: {obligation_key}")
            continue
        if evidence.authority not in {"A", "B", "C"}:
            reasons.append(f"trusted atomic obligation lost authority: {obligation_key}")
            continue
        if evidence.stability_runs < 2 or evidence.status != "PASS":
            reasons.append(f"trusted atomic obligation is not stably passing: {obligation_key}")
    return ReachEvaluation(
        reached=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        trusted_target_count=trusted_target_count,
        stable_target_pass_count=stable_pass,
        execution_confirmed_target_count=confirmed,
    )


def cells_by_id(cells):
    return {cell.challenge_id: cell for cell in cells}


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
