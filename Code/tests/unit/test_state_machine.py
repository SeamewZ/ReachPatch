from __future__ import annotations

import pytest

from reachpatch.models.enums import ControllerPhase
from reachpatch.reach_avoid.machine import StateMachine


def test_state_machine_records_legal_transition_sequence():
    machine = StateMachine()
    machine.transition(ControllerPhase.GRAPH_BUILD, event="semantic_frozen")
    machine.transition(ControllerPhase.INCUMBENT_CLOSE, event="graphs_built")
    machine.transition(ControllerPhase.CORE_SELECT, event="close_failed")
    machine.transition(ControllerPhase.INTENT_SELECT, event="core_selected")
    machine.transition(ControllerPhase.GENERATOR_REVISE, event="intent_selected")
    machine.transition(ControllerPhase.DIFF_RECONCILE, event="trial_started")
    machine.transition(ControllerPhase.DICC_VALIDATE, event="diff_reconciled")
    machine.transition(ControllerPhase.TRANSITION_GATE, event="closure_computed")
    machine.transition(ControllerPhase.COUNTEREXAMPLE_FEEDBACK, event="rollback")
    machine.transition(ControllerPhase.INCUMBENT_CLOSE, event="resume")

    assert machine.phase == ControllerPhase.INCUMBENT_CLOSE
    assert len(machine.history) == 10
    assert len({item.transition_id for item in machine.history}) == 10


def test_state_machine_rejects_illegal_and_stale_transitions():
    machine = StateMachine()
    with pytest.raises(ValueError, match="illegal controller transition"):
        machine.transition(ControllerPhase.DICC_VALIDATE, event="skip")
    with pytest.raises(ValueError, match="stale controller phase"):
        machine.transition(
            ControllerPhase.GRAPH_BUILD,
            event="stale",
            expected_phase=ControllerPhase.CORE_SELECT,
        )
