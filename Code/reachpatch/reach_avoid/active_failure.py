"""The single executable failure selected for one repair revision."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from reachpatch.execution.checks import observed_value
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.execution import (
    ActiveFailure, ActiveFailureKind, CheckExecution, CheckStatus,
    ExecutableCheck, FailureHistory, FailureStage, MechanicalResult,
)


def _traceback_frames(stderr: str) -> tuple[str, ...]:
    return tuple(line for line in (stderr or "").splitlines() if "File " in line or "Error" in line)[-20:]


def _history_count(signature: str, history: Mapping[str, Any]) -> int:
    item = history.get(signature)
    if item is None:
        return 1
    if isinstance(item, int):
        # Controller history stores the number of prior observations directly.
        # The selected failure is the next observation, so its repetition count
        # is one greater than the persisted count.
        return max(1, item + 1)
    if isinstance(item, FailureHistory):
        return max(1, item.count + 1)
    failures = getattr(item, "mechanism_failures", None)
    if isinstance(failures, (tuple, list)):
        return max(1, len(failures) + 1)
    if isinstance(item, Mapping):
        return max(1, int(item.get("count", 0)) + 1)
    return 2


def _failure_from_execution(kind: ActiveFailureKind, check: ExecutableCheck | None, execution: CheckExecution, history: Mapping[str, Any]) -> ActiveFailure:
    observation = execution.observation
    trace = execution.trace
    signature = content_hash({"kind": kind.value, "check": check.check_id if check is not None else execution.check_id, "semantic": execution.semantic_signature, "status": execution.status})
    actual = observed_value(observation)
    if actual is None:
        actual = {
            "status": getattr(getattr(observation, "status", None), "value", getattr(observation, "status", None)),
            "return_code": getattr(observation, "return_code", None),
            "exception": getattr(observation, "exception", None),
        }
    check_id = check.check_id if check is not None else execution.check_id
    command = tuple(check.command) if check is not None else tuple(getattr(trace, "command", ()))
    comparator = check.comparator if check is not None else "RELATION_HOLDS"
    expected = check.expected if check is not None else None
    goal_id = check.goal_id if check is not None else None
    authority = check.authority if check is not None else "PROVISIONAL"
    return ActiveFailure(
        failure_id=stable_id("active-failure", kind.value, check_id, signature), kind=kind, check_id=check_id, goal_id=goal_id, command=command, comparator=comparator,
        expected=expected, actual=actual, stdout=observation.stdout, stderr=observation.stderr, exit_code=observation.return_code, exception=observation.exception,
        traceback_frames=_traceback_frames(observation.stderr), entered_project_code=execution.entered_project_code, first_project_frame=getattr(trace, "first_project_frame", None), failure_stage=execution.failure_stage, signature=signature, same_signature_count=_history_count(signature, history), authority=authority,
    )


def _mechanical_failure(mechanical: MechanicalResult, history: Mapping[str, Any]) -> ActiveFailure | None:
    findings = tuple(
        item for item in (getattr(mechanical, "undefined_name_findings", ()) or ())
        if getattr(item, "severity", "BLOCKER") == "BLOCKER"
    )
    reasons = tuple(str(item) for item in getattr(mechanical, "failure_reasons", ()) or ())
    # Syntax/apply/corruption blockers prevent every executable check from
    # running and therefore always outrank a later lexical-name finding.
    syntax_reason = next((item for item in reasons if any(token in item.casefold() for token in (
        "syntax error", "syntaxerror", "indentationerror", "patch apply",
        "malformed diff", "corrupt", "forbidden",
    ))), None)
    import_reason = next((item for item in reasons if any(token in item.casefold() for token in (
        "importerror", "modulenotfounderror", "nameerror", "undefined name",
        "import blocker",
    ))), None)
    if syntax_reason is not None:
        reason, location = syntax_reason, "<mechanical>"
        findings = ()
    elif findings:
        finding = findings[0]
        reason = getattr(finding, "reason", "introduced undefined name")
        location = f"{getattr(finding, 'file', '<unknown>')}:{getattr(finding, 'line', 0)}"
    elif import_reason is not None:
        reason, location = import_reason, "<mechanical>"
    elif reasons:
        reason, location = reasons[0], "<mechanical>"
    elif getattr(mechanical, "forbidden_edit", False):
        reason, location = "forbidden path edit", "<mechanical>"
    elif getattr(mechanical, "oracle_contamination", False):
        reason, location = "oracle contamination", "<mechanical>"
    elif getattr(mechanical, "unsafe_api_break", False):
        reason, location = "unsafe API break", "<mechanical>"
    elif getattr(mechanical, "high_risk_side_effect", False):
        reason, location = "high risk side effect", "<mechanical>"
    else:
        return None
    signature = content_hash({"kind": "MECHANICAL", "reason": reason, "location": location})
    return ActiveFailure(
        failure_id=stable_id("active-mechanical", signature), kind=ActiveFailureKind.MECHANICAL, check_id=stable_id("mechanical-check", location, reason), goal_id=None, command=(), comparator="MECHANICAL", expected=True, actual=False, stdout="", stderr=str(reason), exit_code=None, exception=None, traceback_frames=(), entered_project_code=False, first_project_frame=location, failure_stage=(FailureStage.PATCH_OR_SYNTAX_BLOCKER if syntax_reason is not None or any(flag in reason.casefold() for flag in ("forbidden", "corrupt", "oracle contamination", "unsafe api", "side effect")) else FailureStage.IMPORT_OR_NAME_BLOCKER), signature=signature, same_signature_count=_history_count(signature, history), authority="MECHANICAL",
    )


def select_active_failure(mechanical: MechanicalResult, target_results: Sequence[CheckExecution], preservation_results: Sequence[CheckExecution], challenge_results: Sequence[CheckExecution], failure_history: Mapping[str, Any], *, target_checks: Sequence[ExecutableCheck] = (), preservation_checks: Sequence[ExecutableCheck] = (), challenge_checks: Sequence[ExecutableCheck] = ()) -> ActiveFailure | None:
    """Select exactly one stable failure; graph state is intentionally absent."""
    if not mechanical.passed:
        return _mechanical_failure(mechanical, failure_history)
    for kind, results, checks in ((ActiveFailureKind.PRESERVATION, preservation_results, preservation_checks), (ActiveFailureKind.TARGET, target_results, target_checks), (ActiveFailureKind.CHALLENGE, challenge_results, challenge_checks)):
        by_id = {item.check_id: item for item in checks}
        for execution in results:
            if execution.status is CheckStatus.FAIL and execution.stable:
                # The production controller always supplies the grounded
                # ExecutableCheck. For lightweight callers, retain a
                # provisional failure derived from trace metadata so priority
                # selection remains deterministic; the repair objective will
                # refuse to edit until an Oracle is recovered.
                return _failure_from_execution(kind, by_id.get(execution.check_id), execution, failure_history)
    return None
