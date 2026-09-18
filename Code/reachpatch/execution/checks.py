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
    if comparator == "INSTANCE_OF":
        if isinstance(check.input_recipe, dict) and check.input_recipe.get("observation_adapter") == "SAFE_RETURN_SUMMARY_V1":
            return isinstance(value, dict) and str(contract.expected) in value.get("__reachpatch_return__", {}).get("container_kinds", ())
        return contract.matches(value)
    if comparator == "TYPE_IS":
        if isinstance(check.input_recipe, dict) and check.input_recipe.get("observation_adapter") == "SAFE_RETURN_SUMMARY_V1":
            return isinstance(value, dict) and value.get("__reachpatch_return__", {}).get("type") == str(contract.expected)
        return type(value).__name__ == str(contract.expected).rsplit(".", 1)[-1]
    if comparator == "CONTAINS":
        try:
            return contract.expected in value
        except TypeError:
            return False
    if comparator == "LENGTH_EQUALS":
        if isinstance(check.input_recipe, dict) and check.input_recipe.get("observation_adapter") == "SAFE_RETURN_SUMMARY_V1":
            return isinstance(value, dict) and value.get("__reachpatch_return__", {}).get("length") == contract.expected
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
        if isinstance(check.input_recipe, dict) and check.input_recipe.get("observation_adapter") == "SAFE_RETURN_SUMMARY_V1":
            payload = value.get("__reachpatch_return__", {}).get("length") if isinstance(value, dict) else None
        else:
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
        if isinstance(check.input_recipe, dict) and check.input_recipe.get("observation_adapter") == "SAFE_RETURN_SUMMARY_V1":
            payload = value.get("__reachpatch_return__", {}).get("type") if isinstance(value, dict) else None
        else:
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
    observation = trace.observation
    diagnostic = f"{observation.stdout or ''}\n{observation.stderr or ''}"
    # Pytest can fail in a dependency after it already called the target.
    # A dependency warning promoted to an error is not target-contract
    # evidence merely because a target frame was observed earlier.
    if (re.search(r"(?m)^E\s+\w*Warning:", diagnostic)
        and re.search(r"(?m)[^\n]*(?:site-packages|dist-packages)/[^\n]+\.py:\d+:\s*\w*Warning\s*$", diagnostic)):
        return True
    if getattr(trace, "first_project_frame", None):
        return False
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
    traces = []
    for index in range(count):
        # Line tracing is localization instrumentation, not part of the
        # executable contract. Large dependency images may need substantially
        # longer for a traced cold start than for the same command itself.
        # Give only the first (traced) member a bounded overhead allowance;
        # the second semantic run still enforces the configured check timeout,
        # so a genuinely hanging command cannot be certified as stable PASS.
        instrumented_timeout = (
            max(float(check.timeout_seconds), 120.0)
            if index == 0 else float(check.timeout_seconds)
        )
        traces.append(run_trace(
            tree, tuple(check.command), cwd=check.cwd,
            environment=check.environment,
            timeout_seconds=instrumented_timeout,
            trace_enabled=index == 0,
            target_symbols=tuple(check.target_symbols),
            overlay_paths=overlay_paths,
        ))
    signatures = [semantic_observation_signature(item.observation, check) for item in traces]
    statuses = [_execution_status(item.observation, check) for item in traces]
    statuses = [
        CheckStatus.BLOCKED if status is not CheckStatus.PASS and _environment_blocked(item) else status
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
    target_names = {
        str(item).rsplit(".", 1)[-1].casefold()
        for item in getattr(check, "target_symbols", ()) if str(item).strip()
    }
    trace_names = {
        str(item).rsplit(".", 1)[-1].casefold()
        for item in getattr(trace, "executed_symbol_ids", ()) if str(item).strip()
    }
    trace_events = getattr(trace, "events", ()) or ()
    def event_value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    event_files = {
        str(event_value(item, "path", event_value(item, "file", "")))
        for item in trace_events
    }
    # A project frame by itself is insufficient: pytest/setup code is also
    # project code.  Executable target evidence must either name the bound
    # symbol or be explicitly identified by a public test caller.
    target_entered = False
    if target_names and trace_events:
        for event in trace_events:
            function = str(event_value(event, "function", event_value(event, "symbol", ""))).casefold()
            file = str(event_value(event, "path", event_value(event, "file", ""))).replace("\\", "/")
            if any(part in {"tests", "test", "setup"} for part in file.split("/")) or Path(file).name.startswith("test_"):
                continue
            for declared in check.target_symbols:
                declared = str(declared).casefold()
                if "." not in declared:
                    matched = function.rsplit(".", 1)[-1] == declared
                elif function == declared:
                    matched = True
                elif declared.endswith("." + function):
                    module = declared[:-(len(function) + 1)].replace(".", "/")
                    matched = file.casefold().endswith((module + ".py", module + "/__init__.py"))
                else:
                    matched = False
                target_entered = target_entered or matched
    elif target_names:
        # Older trace adapters without events can establish only an exact
        # declared symbol, not a substring or an unrelated same-file frame.
        target_entered = any(str(symbol).casefold() in {str(item).casefold() for item in check.target_symbols}
                             for symbol in getattr(trace, "executed_symbol_ids", ()))
    # A test/setup frame alone is not target execution evidence.
    if event_files and all(any(part in path.split("/") for part in ("tests", "test", "setup")) for path in event_files):
        target_entered = False
    result = CheckExecution(
        check_id=check.check_id, status=status, observation=observation, trace=trace,
        runs=count, stable=stable, semantic_signature=content_hash(signatures),
        # Keep the broad frame signal for diagnostics and historical reports;
        # all target decisions use ``entered_target_code`` below.
        entered_project_code=bool(getattr(trace, "first_project_frame", None)),
        entered_target_code=target_entered,
        failure_stage=failure_stage,
        goal_id=check.goal_id,
        role=getattr(check, "role", None),
        authority=getattr(check, "authority", None),
        run_observations=tuple(item.observation for item in traces),
    )
    from reachpatch.reach_avoid.execution_transition import _execution_distance
    return replace(result, distance=_execution_distance(result, check) if stable else None)
