from __future__ import annotations

import json

import pytest

from experiments.reachavoid_51 import runner
from reachpatch.models.base import SCHEMA_VERSION
from reachpatch.models.reach_avoid import RepairObjective
from reachpatch.repair.deepseek_agent import DeepSeekAgent


def test_runner_reads_current_checkpoint_and_transition_schema(tmp_path):
    assert runner.SCHEMA == "reachpatch-51-reach-avoid-v2"
    checkpoint = tmp_path / "checkpoint_store" / "checkpoint-current"
    checkpoint.mkdir(parents=True)
    (checkpoint / "checkpoint.json").write_text(json.dumps({
        "schema": SCHEMA_VERSION,
        "checkpoint": {
            "checkpoint_id": "checkpoint-current",
            "status": "INITIAL_WORKING",
            "revision": 0,
        },
    }), encoding="utf-8")
    transitions = tmp_path / "transitions"
    transitions.mkdir()
    (transitions / "transition-current.json").write_text(json.dumps({
        "schema": SCHEMA_VERSION,
        "certificate": {"transition_id": "transition-current"},
    }), encoding="utf-8")

    initial = runner._initial_checkpoint(tmp_path)
    parsed_transitions = runner._transition_payloads(tmp_path)

    assert initial["checkpoint_id"] == "checkpoint-current"
    assert initial["_directory"] == str(checkpoint)
    assert parsed_transitions[0]["certificate"]["transition_id"] == "transition-current"


def test_runner_rejects_requirement_graph_without_challenge_cells():
    evidence = {
        "requirement_graph": {"leaf_count": 3},
        "challenge_graph": {"cell_count": 0},
    }

    with pytest.raises(RuntimeError, match="no Challenge cells"):
        runner._validate_component_evidence("case-id", evidence)


def test_runner_rejects_final_checkpoint_without_executed_challenges():
    evidence = {
        "requirement_graph": {"leaf_count": 3},
        "challenge_graph": {
            "cell_count": 3,
            "executed_challenge_ids": [],
        },
    }

    with pytest.raises(RuntimeError, match="every Challenge cell unexecuted"):
        runner._validate_component_evidence("case-id", evidence)


def test_deepseek_retry_prompt_requires_a_different_patch(monkeypatch):
    objective = RepairObjective(
        objective_id="objective", objective_kind="INITIAL_PATCH",
        primary_requirement={}, related_requirements=(), public_context=(),
        related_failures=(), counterexamples=(), preservation_requirements=(),
        reproduction_commands=(), concrete_inputs=(), input_derivations=(),
        oracle_relations=(), observations=(), failure_signatures=(),
        first_divergences=(), executed_path_ids=(), guarded_branch_ids=(),
        causal_guidance={}, bindings=(), actual_hunks=(), causal_cuts=(),
        impact_cone=None, impact_risks=(), protected_target_ids=(),
        protected_preservation_ids=(), suggested_action_families=(),
        locked_check_ids=(), cumulative_diff="", failed_mechanisms=(),
        forbidden_mechanisms=(), editable_source_slices=(),
        expected_next_effects=(),
    )
    monkeypatch.setenv("REACHPATCH_RA51_ATTEMPT", "2")

    prompt = DeepSeekAgent._prompt(objective)

    assert "independent generation retry 2" in prompt
    assert "materially different" in prompt
