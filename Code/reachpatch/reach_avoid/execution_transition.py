"""Graph-free transition primitives for the production execution loop.

This module is intentionally independent of the retired graph control state.
The controller imports these functions so static context cannot affect
execution evidence, atomic progress, or checkpoint decisions.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from reachpatch.execution.checks import (
    _observed_value, observation_matches_check,
)
from reachpatch.models.evidence import RunObservation
from reachpatch.models.execution import (
    AtomicProgress, CheckExecution, CheckStatus, ExecutableCheck, FailureStage,
    MechanicalResult, StateCheckpoint, TransitionDecision,
)


def classify_failure_stage(
    observation: RunObservation | None, check: Any | None = None, trace: Any = None,
    mechanical: MechanicalResult | None = None,
) -> FailureStage | None:
    """Classify a stable execution result on the ordered failure ladder."""
    if observation is None:
        return None
    exception = str(getattr(observation, "exception", "") or "")
    stderr = str(getattr(observation, "stderr", "") or "")
    status = str(getattr(getattr(observation, "status", None), "value", getattr(observation, "status", None)))
    entered = bool(
        getattr(trace, "first_project_frame", None)
        or getattr(trace, "executed_symbol_ids", ())
        or getattr(trace, "executed_path_ids", ())
    )
    text = f"{exception} {stderr}".casefold()
    if mechanical is not None and not mechanical.passed:
        mechanical_text = " ".join(map(str, mechanical.failure_reasons)).casefold()
        text = f"{text} {mechanical_text}"
        if any(token in text for token in (
            "syntaxerror", "syntax error", "indentationerror",
            "patch apply", "malformed diff",
        )):
            return FailureStage.PATCH_OR_SYNTAX_BLOCKER
    # Environmental and unstable outcomes never participate in the ordered
    # failure ladder, even when their text happens to resemble an expected
    # exception name.
    if exception == "TIMEOUT" or status in {
        "UNKNOWN", "BLOCKED", "UNSUPPORTED", "TIMEOUT",
        "NONDETERMINISTIC", "None",
    }:
        return None
    # An expected exception is a successful oracle match even though the
    # process exits non-zero. Check the typed contract before interpreting the
    # exception name as an import/name blocker.
    if check is not None and observation_matches_check(observation, check):
        return FailureStage.TARGET_PASS
    if any(token in text for token in ("syntaxerror", "indentationerror", "patch apply", "malformed diff")):
        return FailureStage.PATCH_OR_SYNTAX_BLOCKER
    if any(token in text for token in ("nameerror", "importerror", "modulenotfounderror", "undefined name")):
        return FailureStage.IMPORT_OR_NAME_BLOCKER
    if check is None and status == "PASS":
        return FailureStage.TARGET_PASS
    if not entered:
        return FailureStage.PRE_TARGET_RUNTIME_BLOCKER
    return FailureStage.TARGET_CONTRACT_FAILURE


def _execution_distance(execution: CheckExecution, check: ExecutableCheck) -> float | int | None:
    comparator = str(getattr(check, "comparator", "")).upper()
    expected = check.expected
    value = _observed_value(execution.observation)
    try:
        if comparator == "LENGTH_EQUALS":
            return abs(len(value) - int(expected))
        if comparator == "CONTAINS":
            return 0 if expected in value else 1
        if comparator == "ORDER_EQUALS":
            return sum(left != right for left, right in zip(value, expected)) + abs(len(value) - len(expected))
        if comparator == "EQUALS" and isinstance(value, (int, float)) and isinstance(expected, (int, float)):
            return abs(value - expected)
    except (TypeError, ValueError):
        return None
    return None


def _same_or_deeper_target_path(
    parent: CheckExecution,
    trial: CheckExecution,
    check: ExecutableCheck,
) -> bool:
    if not parent.entered_project_code:
        return trial.entered_project_code
    if not trial.entered_project_code:
        return False
    parent_trace, trial_trace = parent.trace, trial.trace
    parent_symbols = {
        str(item).rsplit(".", 1)[-1]
        for item in getattr(parent_trace, "executed_symbol_ids", ()) or ()
        if str(item) and str(item) != "<module>"
    }
    trial_symbols = {
        str(item).rsplit(".", 1)[-1]
        for item in getattr(trial_trace, "executed_symbol_ids", ()) or ()
        if str(item) and str(item) != "<module>"
    }
    target_symbols = {
        str(item).rsplit(".", 1)[-1]
        for item in getattr(check, "target_symbols", ()) or ()
        if str(item)
    }
    parent_targets = parent_symbols.intersection(target_symbols)
    if parent_targets:
        return bool(parent_targets.intersection(trial_symbols))
    if parent_symbols and trial_symbols and parent_symbols.intersection(trial_symbols):
        return True
    parent_paths = tuple(getattr(parent_trace, "executed_path_ids", ()) or ())
    trial_paths = tuple(getattr(trial_trace, "executed_path_ids", ()) or ())
    if parent_paths and trial_paths:
        # Trace event counts are not path depth.  A repair commonly removes a
        # redundant branch or a return event, so the trial can have fewer
        # events while still executing the same target function.  Require
        # shared path evidence instead of treating that harmless shortening as
        # an early-runtime shortcut.
        if set(parent_paths).intersection(trial_paths):
            return True
        # A causal edit may move line IDs while preserving the same project
        # function. Compare the first traced file/function as a stable path
        # identity before rejecting stage progress.
        parent_frame = getattr(parent_trace, "first_project_frame", None)
        trial_frame = getattr(trial_trace, "first_project_frame", None)
        if parent_frame and trial_frame:
            return str(parent_frame).split(":", 1)[0] == str(trial_frame).split(":", 1)[0]
        return False
    parent_frame = getattr(parent_trace, "first_project_frame", None)
    trial_frame = getattr(trial_trace, "first_project_frame", None)
    return not parent_frame or parent_frame == trial_frame


def _assertion_mismatch_count(execution: CheckExecution) -> int | None:
    if execution.status is not CheckStatus.FAIL:
        return None
    observation = execution.observation
    text = (
        f"{getattr(observation, 'stdout', '')}\n"
        f"{getattr(observation, 'stderr', '')}"
    )
    summaries = tuple(
        int(match.group(1))
        for match in re.finditer(r"\b(\d+)\s+failed\b", text, re.IGNORECASE)
    )
    if summaries:
        return summaries[-1]
    count = len(re.findall(r"\bAssertionError\b", text))
    return count or None


def compute_execution_atomic_progress(parent: CheckExecution, trial: CheckExecution, check: ExecutableCheck) -> AtomicProgress:
    """Compare the same grounded check on parent and trial snapshots."""
    before = classify_failure_stage(parent.observation, check, parent.trace)
    after = classify_failure_stage(trial.observation, check, trial.trace)
    stable = bool(parent.stable and trial.stable)
    comparable = bool(
        stable and parent.check_id == trial.check_id
        and parent.status in {CheckStatus.PASS, CheckStatus.FAIL}
        and trial.status in {CheckStatus.PASS, CheckStatus.FAIL}
    )
    # A PASS is only progress when the trial reached the same causal target
    # path (or a deeper one). This prevents an early import/setup shortcut
    # from being certified as a fix for a check that previously exercised the
    # target implementation.
    path_progress = _same_or_deeper_target_path(parent, trial, check)
    strict = (
        comparable and parent.status is CheckStatus.FAIL
        and trial.status is CheckStatus.PASS and path_progress
    )
    stage_advanced = bool(
        comparable and before is not None and after is not None and after > before
        and _same_or_deeper_target_path(parent, trial, check)
        and after not in {FailureStage.PATCH_OR_SYNTAX_BLOCKER, FailureStage.IMPORT_OR_NAME_BLOCKER}
    )
    before_distance = _execution_distance(parent, check)
    after_distance = _execution_distance(trial, check)
    mismatch_distance = False
    if before_distance is None and after_distance is None:
        before_mismatches = _assertion_mismatch_count(parent)
        after_mismatches = _assertion_mismatch_count(trial)
        if before_mismatches is not None and after_mismatches is not None:
            before_distance = before_mismatches
            after_distance = after_mismatches
            mismatch_distance = after_mismatches < before_mismatches
    distance_improved = bool(
        comparable and path_progress
        and before_distance is not None and after_distance is not None
        and after_distance < before_distance
    )
    blocker_removed = bool(
        comparable and path_progress
        and
        before in {FailureStage.PATCH_OR_SYNTAX_BLOCKER, FailureStage.IMPORT_OR_NAME_BLOCKER}
        and after is not None and after > before
    )
    regression = bool(
        comparable and ((parent.status is CheckStatus.PASS and trial.status is CheckStatus.FAIL)
        or (before is not None and after is not None and after < before))
    )
    reason = (
        "stable FAIL -> PASS" if strict else
        "failure stage advanced" if stage_advanced else
        "mechanical blocker removed" if blocker_removed else
        "target assertion mismatch count improved" if distance_improved and mismatch_distance else
        "contract distance improved" if distance_improved else
        "stable regression" if regression else "no atomic progress"
    )
    return AtomicProgress(
        check_id=check.check_id, parent_status=parent.status, trial_status=trial.status,
        parent_stage=before, trial_stage=after, parent_distance=before_distance,
        trial_distance=after_distance, strict_progress=strict,
        partial_progress=bool(stage_advanced or distance_improved or blocker_removed),
        regression=regression, reason=reason,
    )


def compute_atomic_progress(
    parent: CheckExecution,
    trial: CheckExecution,
    check: ExecutableCheck,
) -> AtomicProgress:
    """Graph-free public alias used by execution-driven callers."""
    return compute_execution_atomic_progress(parent, trial, check)


def compute_mechanical_atomic_progress(parent: MechanicalResult, trial: MechanicalResult) -> AtomicProgress:
    def blockers(result: MechanicalResult) -> tuple[str, ...]:
        findings = tuple(getattr(result, "undefined_name_findings", ()) or ())
        definite = tuple(
            f"{getattr(item, 'file', '')}:{getattr(item, 'line', 0)}:{getattr(item, 'name', '')}"
            for item in findings if getattr(item, "severity", "BLOCKER") == "BLOCKER"
        )
        return tuple(dict.fromkeys((*definite, *(str(item) for item in getattr(result, "failure_reasons", ()) or ()))))
    before = blockers(parent); after = blockers(trial)
    def stage(values: tuple[str, ...]) -> FailureStage:
        text = " ".join(values).casefold()
        if any(token in text for token in ("syntax error", "syntaxerror", "indentationerror", "patch apply", "malformed diff")):
            return FailureStage.PATCH_OR_SYNTAX_BLOCKER
        return FailureStage.IMPORT_OR_NAME_BLOCKER if values else FailureStage.TARGET_PASS
    removed = bool(set(before) - set(after)); before_stage = stage(before); after_stage = stage(after)
    regression = bool(
        (not before and after)
        or (before and after and after_stage < before_stage)
    )
    return AtomicProgress(
        check_id="__mechanical__", parent_status="FAIL" if before else "PASS",
        trial_status="FAIL" if after else "PASS", parent_stage=before_stage,
        trial_stage=after_stage, strict_progress=False, partial_progress=removed,
        regression=regression,
        reason=(
            "mechanical blocker removed" if removed else
            "mechanical failure stage regressed" if regression else
            "mechanical blockers unchanged"
        ),
        parent_distance=len(before), trial_distance=len(after),
    )


def _definite_undefined_names(result: MechanicalResult) -> tuple[Any, ...]:
    return tuple(
        item for item in (getattr(result, "undefined_name_findings", ()) or ())
        if getattr(item, "severity", "BLOCKER") == "BLOCKER"
    )


def all_reach_conditions_pass(
    mechanical: MechanicalResult,
    target_results: Sequence[CheckExecution],
    preservation_results: Sequence[CheckExecution] = (),
    challenge_results: Sequence[CheckExecution] = (),
    required_goal_ids: Sequence[str] = (),
) -> bool:
    """Certify Reach using only executable check observations."""
    if not (
        mechanical.passed
        and not _definite_undefined_names(mechanical)
        and not mechanical.static_blocker_ids
    ):
        return False
    targets = tuple(target_results or ())
    preservations = tuple(preservation_results or ())
    challenges = tuple(challenge_results or ())
    # At least one trusted executable target must pass.  Hard goals are
    # checked explicitly below; a soft/auxiliary target is allowed to remain
    # unresolved without preventing certification.  Preservation and
    # challenge checks, when executable, must all be stable PASS.
    def _role_is_target(item: CheckExecution) -> bool:
        role = getattr(item, "role", None)
        return role is None or str(getattr(role, "value", role)).upper().replace("CHECKROLE.", "") == "TARGET"

    def _trusted_authority(item: CheckExecution) -> bool:
        authority = getattr(item, "authority", None)
        return authority is None or str(authority).upper() in {"A", "B", "C"}

    trusted_target_pass = any(
        item.stable and item.status is CheckStatus.PASS
        and _role_is_target(item) and _trusted_authority(item)
        for item in targets
    )
    checks = (*preservations, *challenges)
    if not trusted_target_pass or not all(item.stable and item.status is CheckStatus.PASS for item in checks):
        return False

    # Execution results normally carry the role/authority copied from their
    # grounded ExecutableCheck.  Keep ``None`` permissive for old serialized
    # fixtures, but reject any explicitly untrusted target.  In particular a
    # model-provisional check must never become a Reach oracle merely because
    # its command happened to pass.
    for item in targets:
        role = getattr(item, "role", None)
        if role is not None and str(getattr(role, "value", role)).upper() != "TARGET":
            return False
        authority = getattr(item, "authority", None)
        if authority is not None and str(authority).upper() not in {"A", "B", "C"}:
            return False
    for item in (*preservation_results, *challenge_results):
        authority = getattr(item, "authority", None)
        if authority is not None and str(authority).upper() not in {"A", "B", "C"}:
            return False
    required = {str(item) for item in required_goal_ids if str(item)}
    if required:
        hard_target_results = tuple(
            item for item in targets
            if str(getattr(item, "goal_id", "")) in required
        )
        if not hard_target_results or not all(
            item.stable and item.status is CheckStatus.PASS
            for item in hard_target_results
        ):
            return False
        passed_goal_ids = {
            str(getattr(item, "goal_id", ""))
            for item in targets if item.stable and item.status is CheckStatus.PASS
        }
        if not required.issubset(passed_goal_ids):
            return False
    return True


def decide_transition(parent: StateCheckpoint, trial: StateCheckpoint, mechanical_before: MechanicalResult, mechanical_after: MechanicalResult, progress: Sequence[AtomicProgress], target_results: Sequence[CheckExecution], preservation_results: Sequence[CheckExecution], challenge_results: Sequence[CheckExecution], required_goal_ids: Sequence[str] = ()) -> TransitionDecision:
    """Deterministic execution-only Reach-Avoid transition table."""
    if not trial.patch_is_applicable or trial.repository_corrupted or trial.forbidden_path_changed:
        return TransitionDecision.REJECT_TRIAL
    if trial.patch_hash == parent.patch_hash:
        return TransitionDecision.REJECT_TRIAL
    if all_reach_conditions_pass(
        mechanical_after, target_results, preservation_results,
        challenge_results, required_goal_ids,
    ):
        return TransitionDecision.REACHED
    progress = tuple(progress or ())
    locked = set(parent.locked_check_ids)
    lost_locks = tuple(
        item for item in progress
        if item.regression and item.check_id in locked
    )
    locked_lost = bool(lost_locks)
    executions = {
        item.check_id: item
        for item in (*target_results, *preservation_results, *challenge_results)
    }
    locked_records = {item.check_id: item for item in parent.locked_checks}

    def locked_role(check_id: str) -> str:
        record = locked_records.get(check_id)
        if record is None:
            return ""
        role = record.check.role
        return str(getattr(role, "value", role)).upper()

    lost_target_locks = tuple(
        item for item in lost_locks if locked_role(item.check_id) == "TARGET"
    )

    def authority_rank(value: str | None) -> int:
        return {"A": 3, "B": 2, "C": 1}.get(str(value or "").upper(), 0)

    lost_rank = max((
        authority_rank(locked_records[item.check_id].check.authority)
        for item in lost_target_locks if item.check_id in locked_records
    ), default=3)
    higher_authority_progress = any(
        item.strict_progress
        and item.check_id not in locked
        and authority_rank(getattr(executions.get(item.check_id), "authority", None)) > lost_rank
        for item in progress
    )
    has_progress = any(item.strict_progress or item.partial_progress for item in progress)
    preservation_regression = any(item.stable and item.status is CheckStatus.FAIL for item in preservation_results)
    target_regression = any(item.regression and item.check_id in {check.check_id for check in target_results} for item in progress)
    mechanical_unresolved = bool(
        not mechanical_after.passed
        or _definite_undefined_names(mechanical_after)
        or mechanical_after.static_blocker_ids
    )
    if lost_target_locks and not higher_authority_progress:
        return TransitionDecision.REJECT_TRIAL
    if has_progress:
        return TransitionDecision.KEEP_REPAIRING if preservation_regression or mechanical_unresolved or locked_lost or target_regression else TransitionDecision.ADVANCE_SAFE
    before_blockers = len(_definite_undefined_names(mechanical_before)) + len(getattr(mechanical_before, "failure_reasons", ()))
    after_blockers = len(_definite_undefined_names(mechanical_after)) + len(getattr(mechanical_after, "failure_reasons", ()))
    if (locked_lost or target_regression or after_blockers > before_blockers) and not has_progress:
        return TransitionDecision.REJECT_TRIAL
    if after_blockers < before_blockers:
        return TransitionDecision.KEEP_REPAIRING if preservation_regression else TransitionDecision.ADVANCE_SAFE
    if preservation_regression or any(item.status in {CheckStatus.UNKNOWN, CheckStatus.BLOCKED} for item in target_results):
        return TransitionDecision.KEEP_REPAIRING
    return TransitionDecision.KEEP_REPAIRING
