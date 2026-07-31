from __future__ import annotations

from types import SimpleNamespace

from reachpatch.models.controller import MechanismAttempt, UnitOutcome
from reachpatch.models.enums import OutcomeStatus
from reachpatch.repair.policy import next_untried_repair_intent, select_losing_core


def _state():
    unit = SimpleNamespace(
        unit_id="unit-1",
        leaf_id="leaf-1",
        path_obligation_id="path-1",
        repair_cut_node_ids=("cut-1",),
        bypass_path_ids=(),
        frontier_ids=(),
        scenario_ids=("scenario-1",),
    )
    component = SimpleNamespace(
        component_id="component-1",
        unit_ids=("unit-1",),
        legal_repair_cut_ids=("cut-1",),
        preservation_node_ids=("preserve-1",),
    )
    failed = UnitOutcome(
        outcome_id="outcome-1",
        unit_id="unit-1",
        path_obligation_id="path-1",
        scenario_id="scenario-1",
        challenge_id="challenge-1",
        kind="TARGET",
        status=OutcomeStatus.FAIL,
        weight=4.0,
        execution_bundle_id="bundle-1",
        failure_origin="TARGET_BEHAVIOR",
        stable=True,
        comparable=True,
        observation={"return": 1},
        graph_hashes={},
    )
    return SimpleNamespace(
        outcomes={failed.outcome_id: failed},
        active_binding_graph=SimpleNamespace(
            units={unit.unit_id: unit},
            components={component.component_id: component},
        ),
        requirement_graph=SimpleNamespace(
            leaves={"leaf-1": SimpleNamespace(weight=4.0)}
        ),
        checkpoint=SimpleNamespace(checkpoint_id="checkpoint-1"),
        program_graph=SimpleNamespace(
            nodes={"cut-1": SimpleNamespace(kind="branch")}
        ),
        mechanism_memory={},
    )


def test_policy_selects_one_component_complete_intent_and_rotates_forbidden_mechanism():
    state = _state()
    core = select_losing_core(state)
    assert core is not None
    assert core.unit_ids == ("unit-1",)
    assert core.common_causal_cut_ids == ("cut-1",)

    first = next_untried_repair_intent(state, core)
    assert first is not None
    assert first.root_mechanism_class == "guard_boundary"
    assert first.complete_component_path_ids == ("path-1",)

    state.mechanism_memory[core.core_id] = [MechanismAttempt(
        component_id=core.component_id,
        losing_core_id=core.core_id,
        mechanism_class="guard_boundary",
        fingerprint_hash="fingerprint-1",
        result="ROLLBACK",
        causal_cut_ids=("cut-1",),
        failure_observation_hash="failure-1",
        transition_id="transition-1",
        equivalent_attempt_count=2,
        forbidden_next=True,
    )]
    second = next_untried_repair_intent(state, core)
    assert second is not None
    assert second.root_mechanism_class == "return_relation"
    assert second.forbidden_fingerprints == ("fingerprint-1",)
