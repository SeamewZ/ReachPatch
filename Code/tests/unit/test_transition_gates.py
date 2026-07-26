from __future__ import annotations

from types import SimpleNamespace

from reachpatch.models.controller import UnitOutcome
from reachpatch.models.enums import OutcomeStatus
from reachpatch.reach_avoid.gates import raw_avoid_reasons


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
        "DIFF_SAFETY_NOT_CLOSED",
        "ESTABLISHED_SUCCESS_LOST",
        "FORBIDDEN_EDIT",
        "MECHANICAL_FAILURE",
        "ORACLE_CONTAMINATION",
        "PRESERVATION_FAILURE",
    )
