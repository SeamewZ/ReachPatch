from __future__ import annotations

from dataclasses import replace

from reachpatch.models.evidence import ObservationContract, OutcomeStatus, RunObservation
from reachpatch.reach_avoid.frontier import (
    FrontierStatus, NextActionKind, RepairFrontier, RepairFrontierKind,
    select_next_action,
)


def test_frontier_id_is_stable_and_content_addressed():
    first = RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE, patch_hash="p", graph_revision=1,
        requirement_ids=("r2", "r1"), binding_ids=("b",),
        expected_contract={"relation": " return 2 ", "expected": 2},
        failure_location={"line": 4},
    )
    second = RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE, patch_hash="p", graph_revision=1,
        requirement_ids=("r1", "r2"), binding_ids=("b",),
        expected_contract={"expected": 2, "relation": "return 2"},
        failure_location={"line": 4},
    )
    assert first.frontier_id == second.frontier_id


def test_observation_contract_normalization_is_not_free_text_equality():
    contract = ObservationContract("must equal 2", 2, comparator="equals")
    observation = RunObservation(OutcomeStatus.PASS, 0, "", "", 0.0, value=2)
    assert contract.contract_id
    assert contract.matches(observation)
    assert not ObservationContract("ignored prose", 3, comparator="equals").matches(observation)


def test_action_selector_repairs_frontier_after_challenges():
    class Dummy: pass
    state = Dummy()
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.MECHANICAL_FAILURE, patch_hash="p", graph_revision=0,
        priority=-1,
    )
    state.repair_frontiers = {frontier.frontier_id: frontier}
    state.graph_stack = Dummy()
    state.graph_stack.patch_hash = "p"
    state.graph_stack.revision = 0
    state.graph_stack.challenge_graph = Dummy()
    state.graph_stack.challenge_graph.active_cells = lambda: ()
    action = select_next_action(state)
    assert action.kind is NextActionKind.REPAIR
    assert action.frontier_id == frontier.frontier_id
