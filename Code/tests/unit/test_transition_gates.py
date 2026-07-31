from __future__ import annotations

from types import SimpleNamespace

from reachpatch.execution.models import (
    CheckClassification, CheckStatus, CheckComparison, CheckExecution,
)
from reachpatch.models.controller import UnitOutcome
from reachpatch.models.enums import OutcomeStatus
from reachpatch.reach_avoid.metrics import progress_vector_from_comparisons
from reachpatch.reach_avoid.gates import in_target_set, raw_avoid_reasons


def _outcome(identifier: str, status: OutcomeStatus, *, kind: str = "TARGET") -> UnitOutcome:
    return UnitOutcome(
        outcome_id=identifier,
        unit_id=f"unit-{identifier}",
        path_obligation_id=f"path-{identifier}",
        scenario_id=f"scenario-{identifier}",
        challenge_id=f"challenge-{identifier}",
        kind=kind,
        status=status,
        weight=1.0,
        execution_bundle_id=f"bundle-{identifier}",
        failure_origin="NONE",
        stable=True,
        comparable=True,
        observation={},
        graph_hashes={},
    )


def test_raw_avoid_gate_keeps_failures_independent_and_never_treats_unknown_as_pass():
    established = _outcome("established", OutcomeStatus.PASS)
    state = SimpleNamespace(outcomes={established.outcome_id: established})
    lost = _outcome("established", OutcomeStatus.UNKNOWN_EXECUTION)
    preservation = _outcome(
        "preservation", OutcomeStatus.FAIL, kind="PRESERVATION"
    )
    mechanical = SimpleNamespace(status=OutcomeStatus.FAIL)

    reasons = raw_avoid_reasons(
        state,
        {lost.outcome_id: lost, preservation.outcome_id: preservation},
        (mechanical,),
        forbidden_edit=True,
        oracle_contamination=True,
        diff_safety_pass=False,
    )

    assert reasons == (
        "ESTABLISHED_SUCCESS_LOST",
        "FORBIDDEN_EDIT",
        "MECHANICAL_FAILURE",
        "ORACLE_CONTAMINATION",
        "PRESERVATION_FAILURE",
    )


def test_reach_requires_at_least_one_executable_active_target():
    requirement = SimpleNamespace(
        leaves={}, semantic_layer_hash=lambda: "requirements"
    )
    program = SimpleNamespace(program_hash=lambda: "program")
    binding = SimpleNamespace(
        units={}, oracle_frontiers={},
        requirement_graph_hash="requirements", program_graph_hash="program",
    )
    state = SimpleNamespace(
        checkpoint=SimpleNamespace(
            safe=True, patch=SimpleNamespace(canonical_diff="diff --git a/x b/x")
        ),
        requirement_graph=requirement, program_graph=program,
        binding_graph=binding, outcomes={},
        runtime_metrics={
            "diff_adequacy_closed": True,
            "high_value_pending_challenge_ids": (),
        },
    )

    assert not in_target_set(state)


def test_target_infrastructure_failure_is_unknown_not_preservation_regression():
    baseline = CheckExecution(
        execution_id="baseline", check_id="target", tree_hash="base",
        status=CheckStatus.FAIL, return_code=1, stdout="", stderr="failure",
        duration_seconds=0.1, stable=True, failure_signature="baseline-failure",
        first_project_frame=None,
    )
    patched = CheckExecution(
        execution_id="patched", check_id="target", tree_hash="patch",
        status=CheckStatus.INVALID_ENVIRONMENT, return_code=1, stdout="",
        stderr="environment failure", duration_seconds=0.1, stable=True,
        failure_signature="environment-failure", first_project_frame=None,
    )
    comparison = CheckComparison(
        comparison_id="comparison", check_id="target",
        baseline=baseline, patched=patched,
        classification=CheckClassification.NEW_INFRA_FAILURE,
    )

    progress = progress_vector_from_comparisons((), (comparison,))

    assert progress.preservation_regression_delta == 0
    assert progress.unresolved_frontier_delta == 0
    assert not progress.meaningful
