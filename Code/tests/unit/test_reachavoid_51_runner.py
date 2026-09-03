from __future__ import annotations

import json

import pytest

from experiments.reachavoid_51 import runner
from reachpatch.models.base import SCHEMA_VERSION
from reachpatch.models.reach_avoid import RepairObjective
from reachpatch.reach_avoid.execution_checkpoint import EXECUTION_SCHEMA_NAME
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


def test_runner_reads_execution_v2_cumulative_diff_for_p0(tmp_path):
    checkpoint = tmp_path / "execution_checkpoints" / "p0"
    checkpoint.mkdir(parents=True)
    (checkpoint / "checkpoint.json").write_text(json.dumps({
        "schema": EXECUTION_SCHEMA_NAME,
        "checkpoint": {
            "checkpoint_id": "p0",
            "status": "P0",
            "revision": 0,
            "cumulative_diff": "diff --git a/example.py b/example.py\n",
            "patch_hash": "patch",
        },
    }), encoding="utf-8")

    initial = runner._initial_checkpoint(tmp_path)

    assert runner._initial_checkpoint_diff(initial).startswith("diff --git")


def test_runner_keeps_legacy_canonical_diff_for_p0():
    assert runner._initial_checkpoint_diff({"canonical_diff": "legacy"}) == "legacy"


def test_runner_rejects_p0_checkpoint_without_diff():
    with pytest.raises(RuntimeError, match="no cumulative diff"):
        runner._initial_checkpoint_diff({"status": "P0"})


def test_runner_unwraps_execution_state_for_component_evidence(tmp_path):
    (tmp_path / "execution_summary.json").write_text(
        json.dumps({"status": "REACHED", "transition_count": 0}),
        encoding="utf-8",
    )
    (tmp_path / "execution_state.json").write_text(json.dumps({
        "schema": EXECUTION_SCHEMA_NAME,
        "state": {
            "goal_contracts": [{"goal_id": "goal", "hard": True}],
        },
    }), encoding="utf-8")
    checkpoint = tmp_path / "execution_checkpoints" / "final"
    checkpoint.mkdir(parents=True)
    (checkpoint / "checkpoint.json").write_text(json.dumps({
        "schema": EXECUTION_SCHEMA_NAME,
        "checkpoint": {"checkpoint_id": "final", "patch_hash": "patch"},
        "target_results": [{
            "check_id": "target", "status": "PASS", "stable": True,
        }],
        "preservation_results": [],
        "challenge_results": [],
    }), encoding="utf-8")

    evidence = runner._execution_component_evidence(
        tmp_path, {"checkpoint_id": "final"},
    )

    assert evidence["requirement_graph"]["leaf_count"] == 1
    assert evidence["requirement_graph"]["objective_requirement_ids"] == ["goal"]
    assert evidence["challenge_graph"]["executed_challenge_ids"] == ["target"]


def test_diagnostic_official_rows_require_matching_ten_case_seal(
    tmp_path, monkeypatch,
):
    instance_ids = [f"case-{index}" for index in range(10)]
    sealed = tmp_path / "sealed.json"
    official = tmp_path / "diagnostic-official.jsonl"
    sealed.write_text(json.dumps({
        "case_count": 10,
        "instance_ids": instance_ids,
    }), encoding="utf-8")
    official.write_text(
        "".join(json.dumps({"instance_id": item}) + "\n" for item in instance_ids),
        encoding="utf-8",
    )
    monkeypatch.setenv("REACHPATCH_DIAGNOSTIC10", "1")
    monkeypatch.setattr(runner, "SEALED_MANIFEST", sealed)
    monkeypatch.setattr(runner, "DIAGNOSTIC_OFFICIAL_PATH", official)

    rows = runner._official_rows_after_seal()

    assert [item["instance_id"] for item in rows] == instance_ids


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
        observations=(), failure_signatures=(),
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
