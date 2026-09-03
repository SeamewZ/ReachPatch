from pathlib import Path

from reachpatch.execution.checks import CheckExecution, ExecutionStatus
from reachpatch.reach_avoid.active_failure import ActiveFailureKind, select_active_failure
from reachpatch.models.evidence import ExecutableCheck, ObservationContract, OutcomeStatus, RunObservation, TraceBundle
from reachpatch.models.execution import FailureStage, MechanicalResult


def _check(check_id, role):
    return ExecutableCheck(
        check_id=check_id, command=("python", "-c", "pass"), role=role,
        authority="A", expected=ObservationContract("check", True, comparator="EQUALS"),
    )


def _failure(check_id, status=ExecutionStatus.FAIL):
    observation = RunObservation(OutcomeStatus.FAIL, 1, "", "AssertionError", 0.0)
    trace = TraceBundle(
        trace_bundle_id=check_id, tree_hash="tree", command=("python",),
        observation=observation, executed_symbol_ids=("calc",),
        executed_path_ids=("calc.py:1",), first_project_frame="calc.py:1",
    )
    return CheckExecution(check_id, status, observation, trace, 2, True, check_id, True, FailureStage.TARGET_CONTRACT_FAILURE)


def test_preservation_precedes_target_and_only_one_is_selected():
    mechanical = MechanicalResult(True, (), False, False, False, False)
    preservation = _failure("preserve")
    target = _failure("target")
    selected = select_active_failure(
        mechanical, (target,), (preservation,), (), {},
        target_checks=(_check("target", "TARGET"),),
        preservation_checks=(_check("preserve", "PRESERVATION"),),
    )
    assert selected is not None
    assert selected.kind == ActiveFailureKind.PRESERVATION
    assert selected.check_id == "preserve"


def test_syntax_mechanical_failure_precedes_undefined_name():
    finding = type("Finding", (), {"file": "calc.py", "line": 2, "name": "numbers", "reason": "undefined", "severity": "BLOCKER"})()
    mechanical = MechanicalResult(
        False, ("syntax error in calc.py:1", "introduced undefined name numbers"),
        False, False, False, False, undefined_name_findings=(finding,),
    )
    selected = select_active_failure(mechanical, (), (), (), {})
    assert selected is not None
    assert selected.failure_stage is FailureStage.PATCH_OR_SYNTAX_BLOCKER


def test_target_precedes_challenge_when_no_preservation_failure():
    mechanical = MechanicalResult(True, (), False, False, False, False)
    selected = select_active_failure(
        mechanical, (_failure("target"),), (), (_failure("challenge"),), {},
        target_checks=(_check("target", "TARGET"),),
        challenge_checks=(_check("challenge", "CHALLENGE"),),
    )
    assert selected is not None
    assert selected.kind is ActiveFailureKind.TARGET


def test_mechanical_flag_without_text_still_has_active_failure():
    mechanical = MechanicalResult(True, (), True, False, False, False)
    selected = select_active_failure(mechanical, (), (), (), {})
    assert selected is None
    mechanical = MechanicalResult(False, (), True, False, False, False)
    selected = select_active_failure(mechanical, (), (), (), {})
    assert selected is not None
    assert selected.kind is ActiveFailureKind.MECHANICAL


def test_active_failure_carries_parsed_actual_value():
    mechanical = MechanicalResult(True, (), False, False, False, False)
    selected = select_active_failure(
        mechanical, (_failure("target"),), (), (), {},
        target_checks=(_check("target", "TARGET"),),
    )
    assert selected is not None
    # Empty stdout has no scalar value, so the process observation is retained.
    assert selected.actual["return_code"] == 1

    execution = _failure("target")
    execution = CheckExecution(
        execution.check_id, execution.status,
        RunObservation(OutcomeStatus.PASS, 0, "3\n", "", 0.0),
        execution.trace, execution.runs, execution.stable,
        execution.semantic_signature, execution.entered_project_code,
        execution.failure_stage,
    )
    selected = select_active_failure(
        mechanical, (execution,), (), (), {},
        target_checks=(_check("target", "TARGET"),),
    )
    assert selected is not None
    assert selected.actual == 3
