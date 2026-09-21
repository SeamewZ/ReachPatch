from __future__ import annotations

from reachpatch.models.reach_avoid import ChallengeSelection, ReachAvoidState
from reachpatch.models.graphs import BindingStatus, ChallengeStatus
from reachpatch.challenge_graph.models import (
    challenge_obligation_key, closed_challenge_obligation_keys,
)


def select_challenge_batch(
    state: ReachAvoidState,
    max_batch: int = 6,
) -> ChallengeSelection:
    cells = list(state.graph_stack.challenge_graph.active_cells())
    closed_obligations = closed_challenge_obligation_keys(cells)
    open_failures = {
        failure.challenge_id for failure in state.confirmed_failures
        if failure.open and failure.patch_hash == state.graph_stack.patch_hash
    }
    locked = state.locked_checks
    bindings = state.graph_stack.binding_graph.units

    def frontier_for(cell):
        return tuple(
            frontier_id for frontier_id, recipe_ids
            in state.graph_stack.challenge_graph.frontier_attempts.items()
            if cell.input_recipe.recipe_id in recipe_ids
        )

    def key(cell):
        binding = bindings.get(cell.binding_id)
        trusted = cell.oracle.trusted and cell.oracle.executable
        known_failure = cell.challenge_id in open_failures
        locked_replay = bool(
            set(binding.target_check_ids + binding.preservation_check_ids)
            & (locked.target_ids | locked.preservation_ids)
        ) and cell.origin == "PUBLIC_CHECK" and cell.input_recipe.kind == "PUBLIC_REPLAY" if binding else False
        touched = bool(cell.changed_hunk_ids)
        unconfirmed = binding is not None and binding.status is BindingStatus.STATIC_ACTIONABLE
        return (
            not cell.hard,
            not trusted,
            not known_failure,
            not locked_replay,
            not touched,
            not unconfirmed,
            cell.execution_scenario.timeout_seconds,
            cell.challenge_id,
        )

    ordered = sorted(cells, key=key)
    # Keep a batch adversarially diverse. A single changed binding can expose
    # many recipes; spending all six executions on that family delays the
    # independent callers and preservation consumers that may regress.
    chosen = []
    covered_obligations: set[str] = set()
    for cell in ordered:
        if cell.terminal_status in {ChallengeStatus.PASS, ChallengeStatus.UNREACHABLE}:
            continue
        if state.frontier_attempts.get(cell.challenge_id, 0) >= 1:
            continue
        if any(
            state.frontier_attempts.get(frontier_id, 0) >= 3
            for frontier_id in frontier_for(cell)
        ):
            continue
        obligation = challenge_obligation_key(cell)
        if obligation in closed_obligations:
            continue
        if obligation in covered_obligations:
            continue
        chosen.append(cell.challenge_id)
        covered_obligations.add(obligation)
        if len(chosen) >= max_batch:
            break
    if len(chosen) < max_batch:
        for cell in ordered:
            if cell.challenge_id in chosen:
                continue
            if cell.terminal_status in {ChallengeStatus.PASS, ChallengeStatus.UNREACHABLE}:
                continue
            if state.frontier_attempts.get(cell.challenge_id, 0) >= 1:
                continue
            if any(
                state.frontier_attempts.get(frontier_id, 0) >= 3
                for frontier_id in frontier_for(cell)
            ):
                continue
            if challenge_obligation_key(cell) in closed_obligations:
                continue
            chosen.append(cell.challenge_id)
            if len(chosen) >= max_batch:
                break
    recovery_values = []
    for gap in state.graph_stack.binding_graph.gaps:
        attempt = state.frontier_attempts.get(gap.requirement_id, 0)
        if attempt >= min(3, len(gap.next_recovery_actions)):
            continue
        recovery_values.append((
            gap.requirement_id,
            gap.next_recovery_actions[attempt].value,
        ))
    recovery = tuple(recovery_values)
    exhausted = not chosen and not recovery
    return ChallengeSelection(tuple(chosen), recovery[:max_batch], exhausted)
