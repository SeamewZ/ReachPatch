from __future__ import annotations

from reachpatch.models.enums import OutcomeStatus
from reachpatch.oracle.authority import evaluate_oracle
from reachpatch.oracle.models import (
    ExecutableScenario,
    OracleEvaluation,
    PairClassification,
    RunObservation,
)


def _unknown(reason: str, origin: str) -> PairClassification:
    return PairClassification(
        status=OutcomeStatus.UNKNOWN,
        reason=reason,
        base_evaluation=None,
        patch_evaluation=None,
        comparable=False,
        failure_origin=origin,
    )


def classify_pair(
    base_run: RunObservation,
    patch_run: RunObservation,
    scenario: ExecutableScenario,
    *,
    inputs: dict[str, object] | None = None,
) -> PairClassification:
    if base_run.environment_signature != patch_run.environment_signature:
        return _unknown("environment_mismatch", "ENVIRONMENT")
    shared_nonbehavioral = (
        (base_run.setup_failure and patch_run.setup_failure)
        or (base_run.dependency_failure and patch_run.dependency_failure)
        or (base_run.global_timeout and patch_run.global_timeout)
    )
    if shared_nonbehavioral:
        return _unknown("shared_nonbehavioral_failure", "SHARED_SETUP_OR_DEPENDENCY")
    if not base_run.observation_reached:
        return _unknown("invalid_base_scenario", "SCENARIO")
    if patch_run.mechanical_failure and not base_run.mechanical_failure:
        return PairClassification(
            status=OutcomeStatus.FAIL,
            reason="patch_specific_mechanical",
            base_evaluation=None,
            patch_evaluation=None,
            comparable=True,
            failure_origin="PATCH_MECHANICAL",
        )
    required_channels = set(scenario.observe.channels)
    if scenario.kind == "TARGET":
        if not scenario.oracle.active_and_trusted:
            return _unknown("oracle_not_active", "ORACLE")
        if not required_channels <= set(patch_run.channels):
            return _unknown("patch_observation_channel_missing", "OBSERVATION_SCHEMA")
        if required_channels <= set(base_run.channels):
            base_evaluation = evaluate_oracle(
                scenario.oracle, base_run.channels, inputs=inputs
            )
        else:
            base_evaluation = OracleEvaluation(
                status=OutcomeStatus.FAIL,
                reason="required_channel_absent_on_base",
                expected=sorted(required_channels),
                actual=sorted(base_run.channels),
                channel=",".join(sorted(required_channels)),
            )
        if base_evaluation.status != OutcomeStatus.FAIL:
            return PairClassification(
                status=OutcomeStatus.UNKNOWN,
                reason="base_precondition_false",
                base_evaluation=base_evaluation,
                patch_evaluation=None,
                comparable=True,
                failure_origin="SCENARIO_PRECONDITION",
            )
        patch_evaluation = evaluate_oracle(
            scenario.oracle, patch_run.channels, inputs=inputs
        )
        return PairClassification(
            status=patch_evaluation.status,
            reason="target_predicate",
            base_evaluation=base_evaluation,
            patch_evaluation=patch_evaluation,
            comparable=True,
            failure_origin="TARGET_BEHAVIOR" if patch_evaluation.status == OutcomeStatus.FAIL else "NONE",
        )
    if scenario.kind != "PRESERVATION":
        return _unknown("unsupported_scenario_kind", "SCENARIO")
    if any(
        (channel in base_run.channels) != (channel in patch_run.channels)
        for channel in required_channels
    ):
        return _unknown("observation_not_comparable", "OBSERVATION_SCHEMA")
    base_evaluation = evaluate_oracle(
        scenario.oracle,
        base_run.channels,
        baseline=base_run.channels,
        inputs=inputs,
    )
    if base_evaluation.status != OutcomeStatus.PASS:
        return PairClassification(
            status=OutcomeStatus.UNKNOWN,
            reason="preservation_not_established",
            base_evaluation=base_evaluation,
            patch_evaluation=None,
            comparable=True,
            failure_origin="BASELINE",
        )
    patch_evaluation = evaluate_oracle(
        scenario.oracle,
        patch_run.channels,
        baseline=base_run.channels,
        inputs=inputs,
    )
    return PairClassification(
        status=patch_evaluation.status,
        reason="preservation_predicate",
        base_evaluation=base_evaluation,
        patch_evaluation=patch_evaluation,
        comparable=True,
        failure_origin="PRESERVATION" if patch_evaluation.status == OutcomeStatus.FAIL else "NONE",
    )
