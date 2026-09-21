from __future__ import annotations

import ast
import json
from typing import Any

from reachpatch.models.evidence import ExecutableOracle, OutcomeStatus, RunObservation


def _observed_value(observation: RunObservation) -> Any:
    if observation.value is not None:
        return observation.value
    output = observation.stdout.strip()
    if not output:
        return None
    last_line = output.splitlines()[-1]
    try:
        return json.loads(last_line)
    except (json.JSONDecodeError, TypeError):
        try:
            return ast.literal_eval(last_line)
        except (ValueError, SyntaxError):
            return last_line


def observe_oracle(oracle: ExecutableOracle, observation: RunObservation) -> OutcomeStatus:
    if not oracle.executable:
        return OutcomeStatus.UNKNOWN
    if observation.status in {OutcomeStatus.BLOCKED, OutcomeStatus.UNSUPPORTED}:
        return OutcomeStatus.UNKNOWN
    if isinstance(oracle.expected, dict) and any(
        key in oracle.expected
        for key in ("exit_code", "stdout", "stderr", "value", "exception")
    ):
        observed = {
            "exit_code": observation.return_code,
            "stdout": observation.stdout,
            "stderr": observation.stderr,
            "value": observation.value,
            "exception": observation.exception,
        }
        return (
            OutcomeStatus.PASS
            if all(observed[key] == value for key, value in oracle.expected.items())
            else OutcomeStatus.FAIL
        )
    if oracle.relation.lower().startswith("patched observation preserves"):
        expected = oracle.expected
        if not isinstance(expected, RunObservation):
            return OutcomeStatus.UNKNOWN
        comparable = (
            observation.return_code,
            observation.stdout,
            observation.stderr,
            observation.value,
            observation.exception,
        )
        baseline = (
            expected.return_code,
            expected.stdout,
            expected.stderr,
            expected.value,
            expected.exception,
        )
        return OutcomeStatus.PASS if comparable == baseline else OutcomeStatus.FAIL
    relation = oracle.relation.lower()
    if "raise" in relation or "exception" in relation:
        expected = str(oracle.expected)
        return (
            OutcomeStatus.PASS
            if observation.exception and expected in observation.exception
            else OutcomeStatus.FAIL
        )
    if "forbid" in relation or "must not" in relation or "should not" in relation:
        observed = (_observed_value(observation), observation.stdout, observation.stderr)
        return OutcomeStatus.PASS if oracle.expected not in observed else OutcomeStatus.FAIL
    return (
        OutcomeStatus.PASS
        if observation.status is OutcomeStatus.PASS
        and _observed_value(observation) == oracle.expected
        else OutcomeStatus.FAIL
    )
