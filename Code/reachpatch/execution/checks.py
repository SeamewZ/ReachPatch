"""Execution-backed check evaluation used by recovery and transitions.

This module deliberately has no graph imports.  A check is certified only by
its command, contract and repeated observations.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Hashable

from reachpatch.models.base import canonical_json, content_hash
from reachpatch.models.evidence import ObservationContract, OutcomeStatus, RunObservation, TraceBundle
from reachpatch.models.execution import CheckExecution, CheckStatus, ExecutableCheck
from .trace import run_trace
from .worktree import diff_between


ExecutionStatus = CheckStatus


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TEMP_PATH = re.compile(r"(?:/tmp|/var/tmp|/private/tmp)/[^\s:'\"]+")
_PID = re.compile(r"\b(?:pid|process)\s*[=:]?\s*\d+\b", re.IGNORECASE)
_TRACE_ID = re.compile(r"\b(?:trace(?:[_ -]?id)?|run[_ -]?id)\s*[=:]\s*[A-Za-z0-9_.-]+", re.IGNORECASE)
_DURATION = re.compile(
    r"\b(?:duration|elapsed|wall[_ -]?time|took|time)\s*[=:]?\s*"
    r"\d+(?:\.\d+)?\s*(?:ms|s|sec(?:onds?)?)?\b", re.IGNORECASE,
)


def _clean_text(value: str) -> str:
    value = _ANSI.sub("", value or "")
    value = _TEMP_PATH.sub("<TMP>", value)
    value = _PID.sub("<PID>", value)
    value = _TRACE_ID.sub("<TRACE>", value)
    value = _DURATION.sub("<DURATION>", value)
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


def _observed_value(observation: RunObservation) -> Any:
    if observation.value is not None:
        return observation.value
    output = _clean_text(observation.stdout)
    if not output:
        return None
    last = output.splitlines()[-1]
    try:
        return json.loads(last)
    except (TypeError, json.JSONDecodeError):
        try:
            return ast.literal_eval(last)
        except (ValueError, SyntaxError):
            return last


def observed_value(observation: RunObservation) -> Any:
    """Return the normalized contract value exposed to repair objectives."""
    return _observed_value(observation)


def _normalize_signature_value(value: Any) -> Any:
    """Normalize only observable value noise relevant to a contract."""
    if isinstance(value, dict):
        return {
            str(key): _normalize_signature_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_signature_value(item) for item in value)
    if isinstance(value, str):
        return _clean_text(value)
    return value


def _contract(check: ExecutableCheck) -> ObservationContract:
    if isinstance(check.expected, ObservationContract):
        return check.expected
    return ObservationContract(
        relation=check.comparator, expected=check.expected,
        observable="process" if check.comparator == "EXIT_ZERO" else "return",
        comparator=check.comparator,
    )


def _expected_exception(expected: Any) -> tuple[str, str | None]:
    if isinstance(expected, dict):
        raw = str(expected.get("exception_type") or expected.get("type") or expected.get("exception") or "")
        message = expected.get("message") or expected.get("message_pattern")
        return raw.rsplit(".", 1)[-1], str(message) if message is not None else None
    return str(expected).rsplit(".", 1)[-1], None


def observation_matches_check(observation: RunObservation, check: ExecutableCheck) -> bool:
    """Evaluate only typed contract semantics, never model prose or graphs."""
    contract = _contract(check)
    comparator = contract.normalized_comparator
    value = _observed_value(observation)
    if comparator == "EXIT_ZERO":
        if observation.return_code != 0 or observation.status is not OutcomeStatus.PASS:
            return False
        expected = contract.expected
        if isinstance(expected, dict):
            # EXIT_ZERO may carry an issue-witness stdout/stderr contract.
            # Compare only fields explicitly supplied by the evidence.
            observed = {
                "stdout": observation.stdout,
                "stderr": observation.stderr,
                "value": value,
                "exception": observation.exception,
            }
            return all(observed.get(key) == expected_value for key, expected_value in expected.items() if key != "exit_code")
        return True
    if comparator in {"RAISES", "NOT_RAISES"}:
        raised_text = "\n".join(filter(None, (observation.exception, observation.stderr)))
        raised = bool(raised_text) and observation.return_code not in {0, None}
        if comparator == "NOT_RAISES":
            return not raised
        expected_type, message = _expected_exception(contract.expected)
        type_matches = not expected_type or expected_type in raised_text
        return raised and type_matches and (message is None or message in raised_text)
    if observation.status in {OutcomeStatus.BLOCKED, OutcomeStatus.UNSUPPORTED}:
        return False
    if comparator in {"EQUALS", "ORDER_EQUALS", "STATE_DELTA_EQUALS"}:
        return _normalize_signature_value(value) == _normalize_signature_value(contract.expected)
    if comparator == "NOT_EQUALS":
        return value != contract.expected
    if comparator == "TYPE_IS":
        return type(value).__name__ == str(contract.expected).rsplit(".", 1)[-1]
    if comparator == "CONTAINS":
        try:
            return contract.expected in value
        except TypeError:
            return False
    if comparator == "LENGTH_EQUALS":
        try:
            return len(value) == int(contract.expected)
        except (TypeError, ValueError):
            return False
    if comparator == "HAS_ATTR":
        return hasattr(value, str(contract.expected))
    return value is True


def semantic_observation_signature(observation: RunObservation, check: ExecutableCheck) -> Hashable:
    """Return the comparator-relevant, noise-free stability signature."""
    contract = _contract(check)
    comparator = contract.normalized_comparator
    value = _observed_value(observation)
    if comparator == "EXIT_ZERO":
        payload: Any = (observation.status.value, observation.return_code, observation_matches_check(observation, check))
    elif comparator in {"EQUALS", "NOT_EQUALS", "STATE_DELTA_EQUALS"}:
        payload = _normalize_signature_value(value)
    elif comparator == "LENGTH_EQUALS":
        try:
            payload = len(value)
        except TypeError:
            payload = None
    elif comparator == "ORDER_EQUALS":
        try:
            payload = _normalize_signature_value(tuple(value))
        except TypeError:
            payload = _normalize_signature_value(value)
    elif comparator == "CONTAINS":
        try:
            payload = contract.expected in value
        except TypeError:
            payload = False
    elif comparator == "TYPE_IS":
        payload = type(value).__name__
    elif comparator == "RAISES":
        expected_type, expected_message = _expected_exception(contract.expected)
        raised = "\n".join(filter(None, (observation.exception, _clean_text(observation.stderr))))
        actual_type = next((match.group(1) for match in re.finditer(r"\b([A-Za-z_]\w*(?:Error|Exception))\b", raised)), "")
        normalized_message = _clean_text(raised) if expected_message is not None else None
        payload = (
            bool(raised), actual_type, expected_type,
            expected_type in raised if expected_type else bool(raised),
            normalized_message,
        )
    elif comparator == "NOT_RAISES":
        payload = bool(observation.exception or observation.return_code not in {0, None})
    elif comparator == "HAS_ATTR":
        payload = hasattr(value, str(contract.expected))
    else:
        payload = _normalize_signature_value(value)
    return (comparator, canonical_json(payload), observation_matches_check(observation, check))


def _execution_status(observation: RunObservation, check: ExecutableCheck) -> str:
    if observation.exception == "TIMEOUT":
        return CheckStatus.UNKNOWN
    if observation.status is OutcomeStatus.BLOCKED:
        return CheckStatus.BLOCKED
    if observation.status is OutcomeStatus.UNSUPPORTED:
        return CheckStatus.UNSUPPORTED
    return CheckStatus.PASS if observation_matches_check(observation, check) else CheckStatus.FAIL


def _environment_blocked(trace) -> bool:
    """Recognize setup/import failures before project code executes."""
    if getattr(trace, "first_project_frame", None):
        return False
    observation = trace.observation
    text = f"{observation.exception or ''} {observation.stderr or ''}".casefold()
    return any(token in text for token in (
        "modulenotfounderror", "no module named", "cannot import name",
        "importerror", "environmenterror",
    ))


def execute_check(
    tree: Path,
    check: ExecutableCheck,
    *,
    stability_runs: int = 2,
    base_tree: Path | None = None,
) -> CheckExecution:
    """Run a grounded check repeatedly, preserving first-run trace metadata."""
    count = max(1, int(stability_runs))
    overlay_paths = (
        diff_between(Path(base_tree), Path(tree)).changed_files
        if base_tree is not None else ()
    )
    traces = [
        run_trace(
            tree, tuple(check.command), cwd=check.cwd, environment=check.environment,
            timeout_seconds=check.timeout_seconds, trace_enabled=index == 0,
            overlay_paths=overlay_paths,
        )
        for index in range(count)
    ]
    signatures = [semantic_observation_signature(item.observation, check) for item in traces]
    statuses = [_execution_status(item.observation, check) for item in traces]
    statuses = [
        CheckStatus.BLOCKED if _environment_blocked(item) else status
        for item, status in zip(traces, statuses)
    ]
    stable = len(set(signatures)) == 1 and len(set(statuses)) == 1
    observation = traces[-1].observation
    first = traces[0]
    trace = replace(
        first, observation=observation, stable_runs=count if stable else 0,
        comparable=stable,
    )
    status = statuses[-1] if stable else CheckStatus.UNKNOWN
    # Failure stages are meaningful only for a stable executable result.
    # In particular, an unstable pair or timeout is an evidence gap and must
    # not be fed into the ordered stage comparison used by AtomicProgress.
    failure_stage = None
    if stable and status in {CheckStatus.PASS, CheckStatus.FAIL}:
        from reachpatch.reach_avoid.execution_transition import classify_failure_stage
        failure_stage = classify_failure_stage(observation, check, trace)
    return CheckExecution(
        check_id=check.check_id, status=status, observation=observation, trace=trace,
        runs=count, stable=stable, semantic_signature=content_hash(signatures),
        entered_project_code=bool(first.first_project_frame or first.executed_symbol_ids or first.executed_path_ids),
        failure_stage=failure_stage,
        goal_id=check.goal_id,
        role=getattr(check, "role", None),
        authority=getattr(check, "authority", None),
    )
