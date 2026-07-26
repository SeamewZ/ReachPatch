from __future__ import annotations

from types import SimpleNamespace

from reachpatch.models.controller import UnitOutcome
from reachpatch.models.enums import OutcomeStatus
from reachpatch.reach_avoid.metrics import component_shadow_pass


def _outcome(path: str, status: OutcomeStatus) -> UnitOutcome:
    return UnitOutcome(
        outcome_id=f"outcome-{path}",
        unit_id=f"unit-{path}",
        path_obligation_id=path,
        scenario_id="scenario",
        challenge_id="challenge",
        kind="TARGET",
        status=status,
        weight=1.0,
        execution_bundle_id="bundle",
        failure_origin="NONE",
        stable=True,
        comparable=True,
        observation={},
        graph_hashes={},
    )


def test_component_shadow_requires_all_selected_paths_to_pass():
    intent = SimpleNamespace(complete_component_path_ids=("path-a", "path-b"))
    assert component_shadow_pass(intent, {
        "a": _outcome("path-a", OutcomeStatus.PASS),
        "b": _outcome("path-b", OutcomeStatus.PASS),
    })
    assert not component_shadow_pass(intent, {
        "a": _outcome("path-a", OutcomeStatus.PASS),
        "b": _outcome("path-b", OutcomeStatus.UNKNOWN),
    })
    assert not component_shadow_pass(
        SimpleNamespace(complete_component_path_ids=()), {}
    )
