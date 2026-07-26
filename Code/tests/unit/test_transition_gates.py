from __future__ import annotations

from types import SimpleNamespace

from reachpatch.models.controller import UnitOutcome
from reachpatch.models.enums import OutcomeStatus
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
