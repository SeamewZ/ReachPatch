from reachpatch.models.evidence import (
    ObservationContract, OutcomeStatus, RunObservation, TraceBundle,
)
from reachpatch.models.execution import (
    CheckExecution, CheckStatus, ExecutableCheck, FailureStage,
)
from reachpatch.reach_avoid.execution_transition import (
    classify_failure_stage, compute_atomic_progress,
)


def _check(comparator="EQUALS", expected=3):
    return ExecutableCheck(
        check_id="check", goal_id="goal", role="TARGET", authority="A",
        command=("python", "-c", "probe"), cwd=".", environment=(),
        timeout_seconds=5, comparator=comparator, expected=expected,
        evidence_ids=("evidence",), target_symbols=("calc",), input_recipe=None,
    )


def _execution(
    status, stage=None, *, value=None, entered=True, stderr="", stable=True,
    symbols=("calc",),
):
    outcome = OutcomeStatus.PASS if status is CheckStatus.PASS else OutcomeStatus.FAIL
    observation = RunObservation(
        outcome, 0 if status is CheckStatus.PASS else 1,
        str(value) if value is not None else "", stderr, 0.1, value=value,
        exception=stderr or None,
    )
    trace = TraceBundle(
        "trace", "tree", ("python",), observation,
        tuple(symbols) if entered else (), ("api.py:1",) if entered else (),
        ("api.py:1",) if entered else (), first_project_frame="api.py:1" if entered else None,
    )
    return CheckExecution(
        "check", status, observation, trace, 2 if stable else 1, stable,
        "signature", entered, stage, goal_id="goal",
    )


def test_fail_to_pass_is_strict_progress():
    progress = compute_atomic_progress(
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=1),
        _execution(CheckStatus.PASS, FailureStage.TARGET_PASS, value=3), _check(),
    )
    assert progress.strict_progress
    assert progress.trial_stage is FailureStage.TARGET_PASS


def test_name_error_to_target_failure_advances_stage():
    progress = compute_atomic_progress(
        _execution(CheckStatus.FAIL, stderr="NameError: numbers", entered=False),
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=1), _check(),
    )
    assert progress.partial_progress
    assert progress.parent_stage is FailureStage.IMPORT_OR_NAME_BLOCKER
    assert progress.trial_stage is FailureStage.TARGET_CONTRACT_FAILURE


def test_undefined_name_blocker_removed_is_progress():
    progress = compute_atomic_progress(
        _execution(CheckStatus.FAIL, stderr="NameError: numbers", entered=False),
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=1), _check(),
    )
    assert progress.partial_progress
    assert progress.reason == "failure stage advanced"


def test_target_failure_to_syntax_failure_is_regression():
    progress = compute_atomic_progress(
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=1),
        _execution(CheckStatus.FAIL, FailureStage.PATCH_OR_SYNTAX_BLOCKER, entered=False, stderr="SyntaxError"),
        _check(),
    )
    assert progress.regression
    assert not progress.partial_progress


def test_timeout_is_not_progress():
    parent = _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=1)
    trial = _execution(CheckStatus.UNKNOWN, None, entered=False, stable=False)
    assert not compute_atomic_progress(parent, trial, _check()).partial_progress


def test_expected_raises_name_error_is_target_pass():
    check = _check("RAISES", {"exception_type": "NameError"})
    observation = RunObservation(
        OutcomeStatus.FAIL, 1, "", "NameError: x", 0.1,
        exception="NameError: x",
    )
    trace = TraceBundle("trace", "tree", check.command, observation, ("calc",), ("api.py:1",), first_project_frame="api.py:1")
    assert classify_failure_stage(observation, check, trace) is FailureStage.TARGET_PASS


def test_length_equals_distance_decreases():
    check = _check("LENGTH_EQUALS", 3)
    progress = compute_atomic_progress(
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=[]),
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=[1, 2]), check,
    )
    assert progress.parent_distance == 3
    assert progress.trial_distance == 1
    assert progress.partial_progress


def test_unsupported_comparator_has_no_distance():
    progress = compute_atomic_progress(
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=1),
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=2),
        _check("RELATION_HOLDS", 3),
    )
    assert progress.parent_distance is None
    assert progress.trial_distance is None
    assert not progress.partial_progress


def test_unstable_distance_change_is_not_progress():
    progress = compute_atomic_progress(
        _execution(CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE, value=[]),
        _execution(
            CheckStatus.UNKNOWN, FailureStage.TARGET_CONTRACT_FAILURE,
            value=[1, 2], stable=False,
        ),
        _check("LENGTH_EQUALS", 3),
    )
    assert progress.trial_distance == 1
    assert not progress.partial_progress


def test_timeout_never_matches_ordered_failure_stage():
    check = _check("RAISES", {"exception_type": "TIMEOUT"})
    observation = RunObservation(
        OutcomeStatus.BLOCKED, None, "", "", 5.0, exception="TIMEOUT",
    )
    assert classify_failure_stage(observation, check, None) is None


def test_pass_on_a_different_function_is_not_stage_progress():
    progress = compute_atomic_progress(
        _execution(
            CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE,
            value=1, symbols=("calc",),
        ),
        _execution(
            CheckStatus.PASS, FailureStage.TARGET_PASS,
            value=3, symbols=("shortcut",),
        ),
        _check(),
    )

    assert not progress.strict_progress
    assert not progress.partial_progress


def test_target_assertion_mismatch_count_decreases():
    check = _check("EXIT_ZERO", {"exit_code": 0})
    progress = compute_atomic_progress(
        _execution(
            CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE,
            stderr="2 failed",
        ),
        _execution(
            CheckStatus.FAIL, FailureStage.TARGET_CONTRACT_FAILURE,
            stderr="1 failed",
        ),
        check,
    )

    assert progress.parent_distance == 2
    assert progress.trial_distance == 1
    assert progress.partial_progress
    assert progress.reason == "target assertion mismatch count improved"
