from reachpatch.models.evidence import ObservationContract, OutcomeStatus, RunObservation
from reachpatch.models.reach_avoid import AtomicEvidence, AtomicObligation, FailureStage
from reachpatch.reach_avoid.transition import classify_failure_stage, compute_atomic_progress


def _evidence(key, status, *, payload=None, entered=True, runs=2, stage=None):
    return AtomicEvidence(
        obligation_key=key,
        status=status,
        requirement_id="req",
        role="TARGET",
        input_partition_id="partition",
        observed_payload=payload or {},
        stability_runs=runs,
        entered_project_code=entered,
        failure_stage=stage or FailureStage.NOT_EXECUTED,
        authority="A",
    )


def _obligation(comparator="EQUALS", expected=3):
    return AtomicObligation(
        key="obligation", requirement_id="req", requirement_contract_id="contract",
        role="TARGET", input_recipe={}, input_partition_id="partition",
        oracle_contract=ObservationContract("return", expected, comparator=comparator),
        authority="A", hard=True, source="challenge:cell",
    )


def test_fail_to_pass_is_strict_progress():
    progress = compute_atomic_progress(
        _evidence("obligation", "FAIL", payload={"status": "FAIL", "value": 1}, stage=FailureStage.TARGET_CONTRACT_FAILURE),
        _evidence("obligation", "PASS", payload={"status": "PASS", "value": 3}, stage=FailureStage.TARGET_PASS),
        _obligation(),
    )
    assert progress.strict_fail_to_pass
    assert progress.after_stage is FailureStage.TARGET_PASS


def test_name_error_to_target_failure_advances_stage():
    progress = compute_atomic_progress(
        _evidence("obligation", "FAIL", payload={"status": "FAIL", "stderr": "NameError: x"}, stage=FailureStage.IMPORT_OR_NAME_BLOCKER, entered=False),
        _evidence("obligation", "FAIL", payload={"status": "FAIL", "stderr": "assertion failed"}, stage=FailureStage.TARGET_CONTRACT_FAILURE),
        _obligation(),
    )
    assert progress.stage_advanced
    assert not progress.regression


def test_undefined_name_blocker_removed_is_progress():
    progress = compute_atomic_progress(
        _evidence("obligation", "FAIL", payload={"status": "FAIL", "stderr": "NameError: numbers"}, stage=FailureStage.IMPORT_OR_NAME_BLOCKER, entered=False),
        _evidence("obligation", "FAIL", payload={"status": "FAIL", "stderr": "assertion failed"}, stage=FailureStage.TARGET_CONTRACT_FAILURE),
        _obligation(),
    )
    assert progress.blocker_removed


def test_expected_raises_is_pass():
    obligation = _obligation("RAISES", {"exception_type": "NameError"})
    observation = RunObservation(OutcomeStatus.FAIL, 1, "", "NameError: x", 0.1, exception="NameError: x")
    assert classify_failure_stage(observation, obligation) is FailureStage.TARGET_PASS


def test_unsupported_contract_has_no_distance():
    progress = compute_atomic_progress(
        _evidence("obligation", "FAIL", payload={"status": "FAIL", "value": 1}, stage=FailureStage.TARGET_CONTRACT_FAILURE),
        _evidence("obligation", "FAIL", payload={"status": "FAIL", "value": 2}, stage=FailureStage.TARGET_CONTRACT_FAILURE),
        _obligation("RELATION_HOLDS", 3),
    )
    assert progress.contract_distance_before is None
    assert progress.contract_distance_after is None
    assert not progress.contract_distance_improved
