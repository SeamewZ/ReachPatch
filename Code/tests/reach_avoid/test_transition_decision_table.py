from reachpatch.models.evidence import OutcomeStatus, RunObservation, TraceBundle
from reachpatch.models.execution import (
    AtomicProgress, CheckExecution, CheckStatus, ExecutableCheck,
    FailureStage, LockedCheck, MechanicalResult, StateCheckpoint,
    TransitionDecision,
)
from reachpatch.reach_avoid.execution_transition import (
    compute_atomic_progress, decide_transition,
)


def _mechanical(*, passed=True, blockers=()):
    return MechanicalResult(
        passed, tuple(blockers), False, False, False, False,
        undefined_name_findings=tuple(blockers),
    )


def _check(check_id="target", goal_id="goal", role="TARGET", authority="A"):
    return ExecutableCheck(
        check_id, goal_id, role, authority, ("python", "-c", "probe"), ".", (), 5,
        "EQUALS", 2, (), ("calc",), None,
    )


def _execution(
    check_id, status, *, value=1, stable=True, entered=True, stage=None,
    goal_id="goal", role="TARGET", authority="A",
):
    observation = RunObservation(
        OutcomeStatus.PASS if status is CheckStatus.PASS else OutcomeStatus.FAIL,
        0 if status is CheckStatus.PASS else 1, str(value), "", 0.1, value=value,
    )
    trace = TraceBundle(
        "trace", "tree", ("python",), observation,
        ("calc",) if entered else (), ("api.py:1",) if entered else (),
        first_project_frame="api.py:1" if entered else None,
    )
    return CheckExecution(
        check_id, status, observation, trace, 2 if stable else 1, stable,
        "signature", entered, stage, goal_id=goal_id,
        role=role, authority=authority,
    )


def _checkpoint(checkpoint_id, patch_hash, *, locked=()):
    return StateCheckpoint(
        checkpoint_id, None, f"/tmp/{checkpoint_id}", patch_hash,
        f"diff-{checkpoint_id}", "TRIAL", 1, locked_checks=locked,
    )


def test_unknown_target_keeps_repairing():
    parent = _checkpoint("parent", "parent")
    trial = _checkpoint("trial", "trial")
    result = decide_transition(
        parent, trial, _mechanical(), _mechanical(), (),
        (_execution("target", CheckStatus.UNKNOWN, stable=False, entered=False),), (), (),
    )
    assert result is TransitionDecision.KEEP_REPAIRING


def test_target_progress_with_preservation_regression_keeps_working_patch():
    check = _check()
    target = _execution("target", CheckStatus.PASS, value=2, stage=FailureStage.TARGET_PASS)
    preservation = _execution(
        "preserve", CheckStatus.FAIL, value=0,
        stage=FailureStage.TARGET_CONTRACT_FAILURE, goal_id="preserve",
    )
    progress = (AtomicProgress(
        "target", "FAIL", "PASS", FailureStage.TARGET_CONTRACT_FAILURE,
        FailureStage.TARGET_PASS, 1, 0, True, True, False, "stable FAIL -> PASS",
    ),)
    result = decide_transition(
        _checkpoint("parent", "parent"), _checkpoint("trial", "trial"),
        _mechanical(), _mechanical(), progress, (target,), (preservation,), (),
    )
    assert result is TransitionDecision.KEEP_REPAIRING


def test_stably_worse_trial_is_rejected():
    check = _check()
    parent_result = _execution("target", CheckStatus.PASS, value=2, stage=FailureStage.TARGET_PASS)
    trial_result = _execution("target", CheckStatus.FAIL, value=1, stage=FailureStage.TARGET_CONTRACT_FAILURE)
    progress = (compute_atomic_progress(parent_result, trial_result, check),)
    result = decide_transition(
        _checkpoint("parent", "parent"), _checkpoint("trial", "trial"),
        _mechanical(), _mechanical(), progress, (trial_result,), (), (),
    )
    assert result is TransitionDecision.REJECT_TRIAL


def test_removed_mechanical_blocker_is_safe_progress():
    progress = (AtomicProgress(
        "__mechanical__", "FAIL", "PASS", FailureStage.IMPORT_OR_NAME_BLOCKER,
        FailureStage.TARGET_PASS, 1, 0, False, True, False,
        "mechanical blocker removed",
    ),)
    target = _execution(
        "target", CheckStatus.FAIL, stage=FailureStage.TARGET_CONTRACT_FAILURE,
    )
    result = decide_transition(
        _checkpoint("parent", "parent"), _checkpoint("trial", "trial"),
        _mechanical(passed=False, blockers=("undefined name numbers",)), _mechanical(),
        progress, (target,), (), (),
    )
    assert result is TransitionDecision.ADVANCE_SAFE


def test_no_progress_without_proof_of_worsening_keeps_repairing():
    target = _execution("target", CheckStatus.FAIL, stage=FailureStage.TARGET_CONTRACT_FAILURE)
    result = decide_transition(
        _checkpoint("parent", "parent"), _checkpoint("trial", "trial"),
        _mechanical(), _mechanical(), (), (target,), (), (),
    )
    assert result is TransitionDecision.KEEP_REPAIRING


def test_all_checks_pass_reaches():
    target = _execution("target", CheckStatus.PASS, value=2, stage=FailureStage.TARGET_PASS)
    result = decide_transition(
        _checkpoint("parent", "parent"), _checkpoint("trial", "trial"),
        _mechanical(), _mechanical(), (), (target,), (), (),
    )
    assert result is TransitionDecision.REACHED


def test_unknown_severity_name_finding_does_not_block_reach():
    class Finding:
        severity = "UNKNOWN"

    target = _execution(
        "target", CheckStatus.PASS, value=2,
        stage=FailureStage.TARGET_PASS,
    )
    mechanical = MechanicalResult(
        True, (), False, False, False, False,
        undefined_name_findings=(Finding(),),
    )
    result = decide_transition(
        _checkpoint("parent", "parent"), _checkpoint("trial", "trial"),
        mechanical, mechanical, (), (target,), (), (),
    )
    assert result is TransitionDecision.REACHED


def test_one_passing_check_cannot_hide_failing_check_for_same_hard_goal():
    passing = _execution(
        "target-a", CheckStatus.PASS, value=2,
        stage=FailureStage.TARGET_PASS,
    )
    failing = _execution(
        "target-b", CheckStatus.FAIL, value=1,
        stage=FailureStage.TARGET_CONTRACT_FAILURE,
    )
    result = decide_transition(
        _checkpoint("parent", "parent"), _checkpoint("trial", "trial"),
        _mechanical(), _mechanical(), (), (passing, failing), (), (),
        required_goal_ids=("goal",),
    )
    assert result is TransitionDecision.KEEP_REPAIRING


def test_locked_regression_requires_higher_authority_compensation():
    locked_check = _check(
        "locked", "locked-goal", role="TARGET", authority="B",
    )
    locked = LockedCheck(locked_check, "observation", "parent")
    lost = AtomicProgress(
        "locked", "PASS", "FAIL", FailureStage.TARGET_PASS,
        FailureStage.TARGET_CONTRACT_FAILURE, None, None,
        False, False, True, "stable regression",
    )
    same_authority_progress = AtomicProgress(
        "target", "FAIL", "PASS", FailureStage.TARGET_CONTRACT_FAILURE,
        FailureStage.TARGET_PASS, None, None,
        True, False, False, "stable FAIL -> PASS",
    )
    result = decide_transition(
        _checkpoint("parent", "parent", locked=(locked,)),
        _checkpoint("trial", "trial"), _mechanical(), _mechanical(),
        (lost, same_authority_progress),
        (_execution(
            "locked", CheckStatus.FAIL,
            stage=FailureStage.TARGET_CONTRACT_FAILURE,
            goal_id="locked-goal", authority="B",
        ), _execution(
            "target", CheckStatus.PASS, stage=FailureStage.TARGET_PASS,
            authority="B",
        )),
        (), (), required_goal_ids=("locked-goal",),
    )

    assert result is TransitionDecision.REJECT_TRIAL


def test_higher_authority_progress_keeps_locked_regression_for_repair():
    locked_check = _check(
        "locked", "locked-goal", role="TARGET", authority="C",
    )
    locked = LockedCheck(locked_check, "observation", "parent")
    lost = AtomicProgress(
        "locked", "PASS", "FAIL", FailureStage.TARGET_PASS,
        FailureStage.TARGET_CONTRACT_FAILURE, None, None,
        False, False, True, "stable regression",
    )
    higher_progress = AtomicProgress(
        "target", "FAIL", "PASS", FailureStage.TARGET_CONTRACT_FAILURE,
        FailureStage.TARGET_PASS, None, None,
        True, False, False, "stable FAIL -> PASS",
    )
    result = decide_transition(
        _checkpoint("parent", "parent", locked=(locked,)),
        _checkpoint("trial", "trial"), _mechanical(), _mechanical(),
        (lost, higher_progress),
        (_execution(
            "locked", CheckStatus.FAIL,
            stage=FailureStage.TARGET_CONTRACT_FAILURE,
            goal_id="locked-goal", authority="C",
        ), _execution(
            "target", CheckStatus.PASS, stage=FailureStage.TARGET_PASS,
            authority="A",
        )),
        (), (), required_goal_ids=("locked-goal",),
    )

    assert result is TransitionDecision.KEEP_REPAIRING
