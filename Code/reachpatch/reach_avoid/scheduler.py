"""Deterministic primary-repair scheduler.

Evidence recovery and impact replay are bounded validation work.  They must
never starve a target frontier that already contains a concrete repair basis.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Any
from .frontier import (
    FrontierStatus, RepairFrontier, RepairFrontierKind,
    repair_frontier_kind_rank,
)

@dataclass(frozen=True, slots=True)
class ScheduledRepair:
    primary_frontier_id: str
    primary_semantic_key: str
    action: Literal["RUN_PRIMARY_CHALLENGE", "RECOVER_PRIMARY_EVIDENCE", "REPAIR_PRIMARY", "REPAIR_EVIDENCE_LIMITED", "SEAL"]
    reason: str
    challenge_id: str | None = None

def _authority_rank(value: Any) -> int:
    return {"A": 4, "B": 3, "C": 2, "PROVISIONAL": 1}.get(str(value), 0)

def select_primary_repair_frontier(state: Any) -> RepairFrontier | None:
    frontiers = getattr(state, "repair_frontiers", {}) or {}
    candidates = [
        item for item in frontiers.values()
        if not item.terminal and item.patch_hash == getattr(state.graph_stack, "patch_hash", item.patch_hash)
        and repair_frontier_kind_rank(item) < 99
    ]
    candidates.sort(key=lambda item: (
        repair_frontier_kind_rank(item), not item.hard, not item.actionable,
        -_authority_rank(item.authority), item.semantic_key,
    ))
    return candidates[0] if candidates else None

def schedule_primary_action(state: Any, primary: RepairFrontier | None = None) -> ScheduledRepair:
    primary = primary or select_primary_repair_frontier(state)
    if primary is None:
        return ScheduledRepair("", "", "SEAL", "no primary repair frontier")
    attempts_state = getattr(state, "frontier_attempts", {})
    noop_count = int(attempts_state.get(f"noop:{primary.semantic_key}", 0))
    if primary.actionable and noop_count >= 2:
        recovery_count = int(attempts_state.get(f"recovery:{primary.semantic_key}", primary.recovery_attempts))
        if recovery_count < 2:
            return ScheduledRepair(primary.frontier_id, primary.semantic_key, "RECOVER_PRIMARY_EVIDENCE", "two no-op attempts require mechanism recovery")
        return ScheduledRepair(primary.frontier_id, primary.semantic_key, "SEAL", "primary mechanism exhausted after two no-op attempts")
    if primary.actionable and primary.kind is not RepairFrontierKind.MECHANICAL_FAILURE:
        cells = getattr(getattr(state.graph_stack, "challenge_graph", None), "active_cells", lambda: ())()
        selected = set(primary.challenge_ids)
        for cell in sorted(cells, key=lambda item: item.challenge_id):
            if cell.challenge_id not in selected or cell.terminal_status.value not in {"PENDING", "UNKNOWN"}:
                continue
            prefix = f"{cell.challenge_id}|{state.graph_stack.patch_hash}"
            attempts = sum(int(value) for key, value in getattr(state, "challenge_attempts", {}).items() if str(key).startswith(prefix))
            if attempts < 2:
                return ScheduledRepair(primary.frontier_id, primary.semantic_key, "RUN_PRIMARY_CHALLENGE", "selected frontier challenge precedes repair", cell.challenge_id)
    if primary.actionable:
        return ScheduledRepair(primary.frontier_id, primary.semantic_key, "REPAIR_PRIMARY", "actionable primary repair frontier")
    recovery_count = int(getattr(state, "frontier_attempts", {}).get(f"recovery:{primary.semantic_key}", primary.recovery_attempts))
    if recovery_count < 2:
        return ScheduledRepair(primary.frontier_id, primary.semantic_key, "RECOVER_PRIMARY_EVIDENCE", "bounded evidence recovery for primary frontier")
    if primary.kind in {RepairFrontierKind.BEHAVIOR_FAILURE, RepairFrontierKind.LOCALIZATION_FAILURE, RepairFrontierKind.REQUIREMENT_COVERAGE_GAP, RepairFrontierKind.ISSUE_DIFF_MISMATCH, RepairFrontierKind.PRESERVATION_REGRESSION, RepairFrontierKind.MECHANICAL_FAILURE} and primary.repair_slice_ids:
        return ScheduledRepair(primary.frontier_id, primary.semantic_key, "REPAIR_EVIDENCE_LIMITED", "evidence budget exhausted; provisional repair is permitted")
    return ScheduledRepair(primary.frontier_id, primary.semantic_key, "SEAL", "primary frontier has no bounded repair basis")

def select_next_scheduled_action(state: Any) -> ScheduledRepair:
    # Controller states always carry a working checkpoint.  Lightweight
    # scheduler unit fixtures may intentionally omit it; those fixtures test
    # frontier ordering only and must not turn a missing optional field into a
    # production seal.  Do not swallow evaluation failures from a real state.
    if hasattr(state, "working_checkpoint"):
        from .gates import evaluate_reach
        if evaluate_reach(state).reached:
            return ScheduledRepair("", "", "SEAL", "strict Reach Set satisfied")
    # Validation backlog is consumed only as part of a transition batch for a
    # selected repair frontier.  Running arbitrary pending checks here revives
    # the old starvation path: a large impact cone can prevent every repair.
    return schedule_primary_action(state)
