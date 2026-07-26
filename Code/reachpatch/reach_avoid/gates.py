from __future__ import annotations

from typing import Iterable

from reachpatch.binding_graph.closure import compute_binding_path_closure
from reachpatch.models.controller import MechanicalCheck, ReachAvoidState, UnitOutcome
from reachpatch.models.enums import OutcomeStatus
from reachpatch.requirement_graph.closure import requirement_path_closure


def _hard_frontiers(state: ReachAvoidState) -> tuple[str, ...]:
    sources = (
        state.requirement_graph.frontiers.values(),
        state.program_graph.frontiers.values(),
        state.binding_graph.frontiers.values(),
        state.challenge_graph.frontiers.values(),
    )
    return tuple(sorted({
        item.frontier_id
        for source in sources for item in source
        if item.hard and item.status == "OPEN"
    }))


def raw_avoid_reasons(
    state: ReachAvoidState,
    new_outcomes: dict[str, UnitOutcome],
    mechanical_checks: Iterable[MechanicalCheck],
    *,
    forbidden_edit: bool,
    oracle_contamination: bool,
    diff_safety_pass: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if any(item.status != OutcomeStatus.PASS for item in mechanical_checks):
        reasons.append("MECHANICAL_FAILURE")
    if forbidden_edit:
        reasons.append("FORBIDDEN_EDIT")
    if oracle_contamination:
        reasons.append("ORACLE_CONTAMINATION")
    if not diff_safety_pass:
        reasons.append("DIFF_SAFETY_NOT_CLOSED")
    old_pass_challenges = {
        item.challenge_id for item in state.outcomes.values()
        if item.status == OutcomeStatus.PASS and item.challenge_id
    }
    new_by_challenge = {
        item.challenge_id: item for item in new_outcomes.values() if item.challenge_id
    }
    lost = [
        challenge_id for challenge_id in old_pass_challenges
        if challenge_id not in new_by_challenge
        or new_by_challenge[challenge_id].status != OutcomeStatus.PASS
    ]
    if lost:
        reasons.append("ESTABLISHED_SUCCESS_LOST")
    if any(
        item.kind == "PRESERVATION" and item.status != OutcomeStatus.PASS
        for item in new_outcomes.values()
    ):
        reasons.append("PRESERVATION_FAILURE")
    return tuple(sorted(set(reasons)))


def in_safe_set(state: ReachAvoidState) -> bool:
    return state.checkpoint.safe and not _hard_frontiers(state)


def in_target_set(state: ReachAvoidState) -> bool:
    requirement_closed = requirement_path_closure(state.requirement_graph).closed
    binding_closed = compute_binding_path_closure(
        state.requirement_graph, state.binding_graph
    ).closed
    all_hard_pass = all(
        not cell.hard or cell.terminal_status.value == "PASS"
        for cell in state.challenge_graph.cells.values()
    )
    outcomes_complete = bool(state.outcomes) and all(
        item.status == OutcomeStatus.PASS for item in state.outcomes.values()
    )
    diff_closed = (
        state.checkpoint.patch.canonical_diff == ""
        or bool(state.diff_closure_certificates)
        and state.diff_closure_certificates[-1].diff_challenge_closed
    )
    hashes_current = (
        state.binding_graph.requirement_graph_hash == state.requirement_graph.semantic_layer_hash()
        and state.binding_graph.program_graph_hash == state.program_graph.program_hash()
    )
    return all((
        state.checkpoint.safe,
        requirement_closed,
        binding_closed,
        all_hard_pass,
        outcomes_complete,
        diff_closed,
        hashes_current,
        not _hard_frontiers(state),
    ))


def terminal_avoid_reason(state: ReachAvoidState) -> str | None:
    if not state.remaining_budget.available() and not in_target_set(state):
        return "BUDGET_EXHAUSTED"
    if state.termination_status in {
        "NO_LEGAL_ACTION", "ENVIRONMENT_BLOCKED", "SEMANTIC_BLOCKED",
        "ORACLE_BLOCKED", "LOCALIZATION_BLOCKED",
    }:
        return state.termination_status
    return None
