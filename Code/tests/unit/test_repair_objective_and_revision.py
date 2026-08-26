from __future__ import annotations

import json
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

from reachpatch.models.base import stable_id
from reachpatch.models.evidence import (
    ConfirmedFailure, CounterexamplePacket, PairedTraceBundle,
    PairClassification, RunObservation, TraceBundle, OutcomeStatus,
)
from reachpatch.models.reach_avoid import (
    GeneratorResult, RepairObjective, ValidationObligation,
)
from reachpatch.models.graphs import (
    CausalRepairCut, ChallengeStatus, ExecutableScenario, ProgramNode, ProgramNodeKind,
)
from reachpatch.reach_avoid.controller import ReachAvoidConfig
from reachpatch.reach_avoid.controller import ReachAvoidController
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.reach_avoid.frontier import RepairFrontier, RepairFrontierKind
from reachpatch.reach_avoid.semantics import input_partition_semantic_key
from reachpatch.reach_avoid.transition import _materialize_registered_probes
from reachpatch.reach_avoid.checkpoint import CheckpointStore, capture_current_graph_checkpoint
from reachpatch.repair.deepseek_agent import DeepSeekConfig
from reachpatch.repair.deepseek_agent import DeepSeekAgent
from reachpatch.repair.objective import compile_repair_objective
from reachpatch.repair.tools import RepairToolExecutor
from reachpatch.execution.mechanical import run_mechanical_checks
from reachpatch.execution.worktree import (
    apply_patch_action, apply_unified_diff, copy_source_tree, diff_between,
)


def packet(identifier: str, patch_hash: str) -> CounterexamplePacket:
    return CounterexamplePacket(
        identifier, "req-target", "binding-target", "challenge-target",
        patch_hash, ("python", "check.py"), {"value": identifier},
        ("public input",), "oracle", "A", "calc must return 2",
        {"value": 1}, {"value": 0}, f"signature-{identifier}",
        {"line": 2}, ("path-calc",), ("hunk-calc",), (), ("caller",),
        ("check-target",), ("check-preservation",), ("EDIT_CAUSAL_CUT",),
    )


def test_repair_objective_contains_all_related_failures(state_factory):
    state = state_factory()
    state.current_repair_objective = replace(
        _objective(state),
        public_context=({
            "source": "PUBLIC_DISCUSSION",
            "authority": "PROVISIONAL",
            "normative": False,
            "content": "public repair context",
        },),
    )
    first = packet("counterexample-1", state.graph_stack.patch_hash)
    second = packet("counterexample-2", state.graph_stack.patch_hash)
    state.counterexamples.extend((first, second))
    state.confirmed_failures.extend((
        ConfirmedFailure("failure-1", "req-target", "binding-target", "challenge-target", first.counterexample_id, state.graph_stack.patch_hash, first.failure_signature, "component", first.first_divergence, True, 0),
        ConfirmedFailure("failure-2", "req-target", "binding-target", "challenge-target", second.counterexample_id, state.graph_stack.patch_hash, second.failure_signature, "component", second.first_divergence, True, 0),
    ))
    objective = compile_repair_objective(state, state.confirmed_failures[0])
    assert {item.counterexample_id for item in objective.counterexamples} == {
        first.counterexample_id, second.counterexample_id,
    }
    assert len(objective.related_failures) == 2
    assert objective.public_context == state.current_repair_objective.public_context


def test_repair_objective_contains_locked_behaviors(state_factory):
    state = state_factory()
    value = packet("counterexample", state.graph_stack.patch_hash)
    failure = ConfirmedFailure(
        "failure", "req-target", "binding-target", "challenge-target",
        value.counterexample_id, state.graph_stack.patch_hash,
        value.failure_signature, "component", value.first_divergence, True, 0,
    )
    state.counterexamples.append(value)
    state.confirmed_failures.append(failure)
    state.locked_checks.target_ids.add("check-target")
    state.locked_checks.preservation_ids.add("check-preservation")
    objective = compile_repair_objective(state, failure)
    assert set(objective.locked_check_ids) == {"check-target", "check-preservation"}
    assert objective.cumulative_diff
    assert objective.observations[0]["actual"] == value.patched_observation


def test_frontier_objective_has_explicit_mechanical_and_selected_validation(
    state_factory,
):
    state = state_factory()
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE,
        patch_hash=state.graph_stack.patch_hash, graph_revision=state.graph_stack.revision,
        requirement_ids=("req-target",), binding_ids=("binding-target",),
        challenge_ids=("challenge-target",), repair_slice_ids=("symbol-calc",),
        expected_contract={"expected": 2}, failure_location={"path": "calc.py", "line": 2},
        requirement_contract_id="target-contract", input_partition_id="target-input",
        source_symbol="calc", failure_signature="calc-must-return-2",
    )

    objective = compile_repair_objective(state, frontier)

    mechanical = [
        item for item in objective.validation_obligations
        if item.role == "MECHANICAL"
    ]
    selected = [
        item for item in objective.validation_obligations
        if item.challenge_id == "challenge-target"
    ]
    assert len(mechanical) == 1
    assert mechanical[0].command[:4] == ("python", "-m", "compileall", "-q")
    assert selected
    assert len({item.key for item in objective.atomic_obligations}) == len(
        objective.atomic_obligations
    )
    assert any(item.role == "MECHANICAL" for item in objective.atomic_obligations)
    assert any(
        item.input_partition_id
        == input_partition_semantic_key(item.input_recipe)
        for item in objective.atomic_obligations
        if item.role == "TARGET"
    )
    assert all(
        item.input_partition_id != "challenge-target"
        for item in objective.atomic_obligations
    )


def test_repair_objective_includes_all_locked_preservation_leaves(state_factory):
    state = state_factory(preservation_status=ChallengeStatus.PENDING)
    value = packet("counterexample", state.graph_stack.patch_hash)
    failure = ConfirmedFailure(
        "failure", "req-target", "binding-target", "challenge-target",
        value.counterexample_id, state.graph_stack.patch_hash,
        value.failure_signature, "component", value.first_divergence, True, 0,
    )
    state.counterexamples.append(value)
    state.confirmed_failures.append(failure)
    state.locked_checks.preservation_ids.add("check-preservation")
    objective = compile_repair_objective(state, failure)
    assert {item["requirement_id"] for item in objective.preservation_requirements} == {
        "req-preservation",
    }


def test_preservation_repair_objective_keeps_unresolved_target_intent(state_factory):
    state = state_factory(preservation_status=ChallengeStatus.PENDING)
    value = replace(
        packet("counterexample-preservation", state.graph_stack.patch_hash),
        requirement_id="req-preservation",
        binding_id="binding-preservation",
        challenge_id="challenge-preservation",
    )
    failure = ConfirmedFailure(
        "failure-preservation", "req-preservation", "binding-preservation",
        "challenge-preservation", value.counterexample_id,
        state.graph_stack.patch_hash, value.failure_signature, "component",
        value.first_divergence, True, 0,
    )
    state.counterexamples.append(value)
    state.confirmed_failures.append(failure)

    objective = compile_repair_objective(state, failure)

    assert {item["requirement_id"] for item in objective.related_requirements} >= {
        "req-target", "req-preservation",
    }
    assert any(
        "req-target" in effect for effect in objective.expected_next_effects
    )


def test_preservation_objective_includes_real_passing_target_execution(state_factory):
    state = state_factory(
        target_status=ChallengeStatus.PASS,
        preservation_status=ChallengeStatus.PENDING,
        stability_runs=2,
    )
    baseline = TraceBundle(
        "trace-base", "base", ("python", "check.py"),
        RunObservation(OutcomeStatus.FAIL, 1, "", "old failure", 0.1),
        ("symbol-calc",), ("path-calc",), stable_runs=2,
    )
    patched = TraceBundle(
        "trace-patched", state.graph_stack.patch_hash,
        ("python", "check.py"),
        RunObservation(OutcomeStatus.PASS, 0, "2\n", "", 0.1),
        ("symbol-calc",), ("path-calc",), stable_runs=2,
        executed_line_ids=("calc.py:2", "calc.py:4"),
    )
    execution = PairedTraceBundle(
        "paired-target", "check-target", "challenge-target",
        state.graph_stack.patch_hash, baseline, patched,
        PairClassification.TARGET_FIXED, "oracle-target", "A",
        "calc must return 2", 2,
    )
    state.observations.record(execution, "req-target")
    shared_node = state.graph_stack.program_graph.nodes["symbol-calc"]
    target_only_node = ProgramNode(
        "symbol-target-consumer", ProgramNodeKind.METHOD, "calc.py",
        "Calculator.target_consumer", 4, 6, True,
    )
    preservation_only_node = ProgramNode(
        "symbol-preservation-consumer", ProgramNodeKind.STATE_READ, "calc.py",
        "Calculator.preservation_consumer.value", 8, 8, True,
    )
    program = replace(
        state.graph_stack.program_graph,
        nodes={
            **state.graph_stack.program_graph.nodes,
            target_only_node.node_id: target_only_node,
            preservation_only_node.node_id: preservation_only_node,
        },
        causal_cuts={
            "causal-cut-shared-upstream": CausalRepairCut(
                "causal-cut-shared-upstream", preservation_only_node.node_id,
                (preservation_only_node.node_id, shared_node.node_id),
                shared_node.node_id, ("hunk-calc",),
                (target_only_node.node_id,),
            ),
        },
    )
    units = dict(state.graph_stack.binding_graph.units)
    units["binding-target"] = replace(
        units["binding-target"],
        program_symbol_ids=(target_only_node.node_id,),
    )
    units["binding-preservation"] = replace(
        units["binding-preservation"],
        program_symbol_ids=(preservation_only_node.node_id,),
    )
    binding = replace(
        state.graph_stack.binding_graph,
        program_hash=program.graph_hash(),
        units=units,
    )
    challenge = replace(
        state.graph_stack.challenge_graph,
        binding_hash=binding.graph_hash(),
    )
    state.graph_stack = replace(
        state.graph_stack,
        program_graph=program,
        binding_graph=binding,
        challenge_graph=challenge,
    )
    state.graph_stack.validate()
    value = replace(
        packet("counterexample-preservation", state.graph_stack.patch_hash),
        requirement_id="req-preservation",
        binding_id="binding-preservation",
        challenge_id="challenge-preservation",
        causal_cut_ids=("causal-cut-shared-upstream",),
    )
    failure = ConfirmedFailure(
        "failure-preservation", "req-preservation", "binding-preservation",
        "challenge-preservation", value.counterexample_id,
        state.graph_stack.patch_hash, value.failure_signature, "component",
        value.first_divergence, True, 0,
    )
    state.counterexamples.append(value)
    state.confirmed_failures.append(failure)

    objective = compile_repair_objective(state, failure)

    target = next(
        item for item in objective.observations
        if item.get("evidence_kind") == "PROTECTED_TARGET_EXECUTION"
    )
    assert target["classification"] == PairClassification.TARGET_FIXED
    assert target["actual"]["status"] == OutcomeStatus.PASS
    assert target["oracle_id"] == "oracle-target"
    assert target["oracle_authority"] == "A"
    assert any(
        obligation.command == ("python", "check.py")
        for obligation in objective.validation_obligations
    )
    assert "challenge-target" in objective.protected_target_ids
    assert {item["binding_id"] for item in objective.bindings} >= {
        "binding-target", "binding-preservation",
    }
    assert tuple(
        item["node_id"]
        for item in objective.causal_guidance["target_only_path_symbols"]
    ) == (target_only_node.node_id,)
    assert tuple(
        item["node_id"]
        for item in objective.causal_guidance["failure_only_path_symbols"]
    ) == (preservation_only_node.node_id,)
    assert tuple(
        item["node_id"]
        for item in objective.causal_guidance["shared_changed_symbols"]
    ) == (shared_node.node_id,)
    assert target["target_only_path_symbols"][0]["symbol"] == (
        target_only_node.symbol
    )
    assert objective.editable_source_slices[0]["path"] == "calc.py"
    assert len(objective.editable_source_slices) <= 24


def test_generator_receives_current_cumulative_diff(state_factory):
    state = state_factory()
    value = packet("counterexample", state.graph_stack.patch_hash)
    failure = ConfirmedFailure(
        "failure", "req-target", "binding-target", "challenge-target",
        value.counterexample_id, state.graph_stack.patch_hash,
        value.failure_signature, "component", value.first_divergence, True, 0,
    )
    state.counterexamples.append(value)
    state.confirmed_failures.append(failure)
    objective = compile_repair_objective(state, failure)
    assert objective.cumulative_diff.startswith("diff --git")


def test_repair_objective_is_persisted_with_current_four_graph_hashes(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    value = packet("counterexample", state.graph_stack.patch_hash)
    failure = ConfirmedFailure(
        "failure", "req-target", "binding-target", "challenge-target",
        value.counterexample_id, state.graph_stack.patch_hash,
        value.failure_signature, "component", value.first_divergence, True, 0,
    )
    state.counterexamples.append(value)
    state.confirmed_failures.append(failure)
    objective = compile_repair_objective(state, failure)

    ReachAvoidController(RepairPlayer(object()))._record_repair_objective(
        state, objective,
    )

    artifact = json.loads(
        (
            state.run_root / "repair_objectives"
            / f"attempt-01-{objective.objective_id}.json"
        )
        .read_text(encoding="utf-8")
    )
    assert artifact["schema"] == "reachpatch-repair-objective-v1"
    assert artifact["patch_hash"] == state.graph_stack.patch_hash
    assert artifact["graph_hashes"] == state.graph_stack.graph_hashes()
    assert artifact["objective"]["counterexamples"][0]["counterexample_id"] == (
        value.counterexample_id
    )


def test_repair_objective_consumes_pending_transition_direction(state_factory):
    state = state_factory()
    value = packet("counterexample", state.graph_stack.patch_hash)
    failure = ConfirmedFailure(
        "failure", "req-target", "binding-target", "challenge-target",
        value.counterexample_id, state.graph_stack.patch_hash,
        value.failure_signature, "component", value.first_divergence, True, 0,
    )
    state.counterexamples.append(value)
    state.generator_session.conversation.append({
        "role": "system",
        "patch_hash": state.graph_stack.patch_hash,
        "pending_objective_kind": "PRESERVATION_REGRESSION",
    })
    assert compile_repair_objective(state, failure).objective_kind == "PRESERVATION_REGRESSION"


def test_historical_closed_failure_is_not_reopened_for_a_new_patch(state_factory):
    state = state_factory()
    value = packet("counterexample", "old-patch")
    state.confirmed_failures.append(ConfirmedFailure(
        "failure", "req-target", "binding-target", "challenge-target",
        value.counterexample_id, "old-patch", value.failure_signature,
        "component", value.first_divergence, True, 0, open=False,
    ))
    ReachAvoidController(RepairPlayer(object()))._refresh_confirmed_failures(state)
    assert not state.confirmed_failures[0].open


def test_no_project_or_case_hardcoding():
    root = Path(__file__).parents[2] / "reachpatch"
    production = "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.py")
    ).lower()
    for forbidden in ("swebench", "astropy", "django", "sympy"):
        assert forbidden not in production


def test_public_check_path_is_oracle_contamination(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    base.mkdir()
    trial.mkdir()
    (base / "public_check.py").write_text("assert True\n", encoding="utf-8")
    (trial / "public_check.py").write_text("assert False\n", encoding="utf-8")
    actual = diff_between(base, trial)
    mechanical = run_mechanical_checks(
        trial, actual, oracle_paths=("public_check.py",),
    )
    assert not mechanical.passed
    assert mechanical.oracle_contamination


def test_mechanical_command_uses_shared_scenario_backend(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    base.mkdir()
    trial.mkdir()
    (trial / "scenario-cwd").mkdir()
    scenario = ExecutableScenario(
        scenario_id="mechanical-scenario",
        command=(
            "python", "-c",
            (
                "import os, pathlib, sys; sys.exit(0 if "
                "(os.environ['REACHPATCH_MECHANICAL_ENV'] == 'present' "
                "and pathlib.Path.cwd().name == 'scenario-cwd') else 7)"
            ),
        ),
        cwd="scenario-cwd",
        environment=(("REACHPATCH_MECHANICAL_ENV", "present"),),
        timeout_seconds=7.0,
    )

    result = run_mechanical_checks(
        trial, diff_between(base, trial), command_scenarios=(scenario,),
    )

    assert result.passed
    command = result.command_results[0]
    assert command["cwd"] == "scenario-cwd"
    assert command["environment"] == scenario.environment
    assert command["timeout_seconds"] == 7.0
    assert command["backend"] == "HOST"


def _objective(state) -> RepairObjective:
    return RepairObjective(
        objective_id="objective",
        objective_kind="CONFIRMED_FAILURE",
        primary_requirement={},
        related_requirements=(),
        public_context=(),
        related_failures=(),
        counterexamples=(),
        preservation_requirements=(),
        observations=(),
        failure_signatures=(),
        first_divergences=(),
        executed_path_ids=(),
        guarded_branch_ids=(),
        causal_guidance={},
        bindings=(),
        actual_hunks=(),
        causal_cuts=(),
        impact_cone=None,
        impact_risks=(),
        protected_target_ids=(),
        protected_preservation_ids=(),
        suggested_action_families=(),
        locked_check_ids=(),
        cumulative_diff=state.working_checkpoint.canonical_diff,
        failed_mechanisms=(),
        forbidden_mechanisms=(),
        editable_source_slices=(),
        expected_next_effects=(),
    )


def _validation(command, expected, requirement_id="req-preservation"):
    return ValidationObligation(
        validation_id=stable_id("test-validation", command, requirement_id, expected),
        role="PRESERVATION", authority="A", command=command, cwd=".",
        environment={}, timeout_seconds=60, backend="shared-executor",
        concrete_input=None, input_derivation="test preservation replay",
        oracle_id="oracle-preservation", expected_relation="preserve return value",
        expected_observation=expected, requirement_id=requirement_id,
        binding_id="binding-preservation", challenge_id="challenge-preservation",
    )


def test_run_validation_accepts_stable_pending_command_key(state_factory, monkeypatch):
    state = state_factory()
    state.run_root.mkdir()
    command = ("python", "-c", "print(2)")
    objective = replace(
        _objective(state),
        validation_obligations=(_validation(command, {"value": 2}),),
    )
    executor = RepairToolExecutor(
        Path(state.working_checkpoint.snapshot_tree), state, objective,
    )
    seen = []
    monkeypatch.setattr(
        executor, "run_allowed_public_check",
        lambda value: seen.append(tuple(value)) or {"ok": True},
    )
    key = stable_id("repair-validation-command", command)
    assert executor.run_validation(key) == {"ok": True}
    assert seen == [command]


def test_registered_probe_enters_triplet_state_and_trial_graph(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE,
        patch_hash=state.graph_stack.patch_hash, graph_revision=0,
        requirement_ids=("req-target",), binding_ids=("binding-target",),
        expected_contract={"probe": "return-value"},
        failure_location="calc",
    )
    state.repair_frontiers[frontier.frontier_id] = frontier
    objective = replace(_objective(state), selected_frontier=frontier)
    executor = RepairToolExecutor(
        Path(state.working_checkpoint.snapshot_tree), state, objective,
    )

    written = executor.write_probe("observe_calc", "print(2)\n")
    registered = executor.register_observation_contract(
        {
            "relation": "probe prints the observed return value",
            "expected": 2, "comparator": "EQUALS",
            "input_recipe": {
                "command": ["python", written["path"]],
                "requirement_id": "req-target",
                "binding_id": "binding-target",
            },
        },
        probe_id=written["probe_id"],
    )
    execution = executor.run_probe(probe_id=registered["probe_id"])

    obligation_key = registered["obligation_key"]
    assert execution["trial"]["status"] == "PASS"
    assert obligation_key in state.atomic_obligations
    assert state.atomic_evidence[obligation_key].stability_runs == 2
    assert state.probe_registrations[registered["probe_id"]].authority == "PROVISIONAL"

    trial_stack = _materialize_registered_probes(
        state, state.graph_stack, frontier,
    )
    assert any(
        cell.origin == "PROBE_REGISTRATION"
        for cell in trial_stack.challenge_graph.cells.values()
    )
    store = CheckpointStore(state.run_root)
    checkpoint = capture_current_graph_checkpoint(state, store, "PROBE_EVIDENCE")
    restored = store.runtime_state(checkpoint.checkpoint_id)
    assert restored.probe_registrations[registered["probe_id"]].execution_results["trial"].status == "PASS"


def test_revision_modifies_current_working_tree(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    class Fake:
        def revise(self, objective, tools, initial=False):
            tools.apply_patch(
                "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def calc():\n-    return 2\n+    return 3\n"
            )
            tools.finish_revision("move from current value")
            return {"summary": "current", "mechanism": "causal"}
    result = RepairPlayer(Fake()).revise_working_patch(state, _objective(state))
    assert "-    return 2" in result.incremental_diff
    assert "+    return 3" in result.incremental_diff


def test_revision_cannot_replace_cumulative_patch_through_empty_intermediate(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "empty-revert"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))
    revert = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 1\n"
    )

    with pytest.raises(RuntimeError, match="removes the complete cumulative diff"):
        tools.apply_patch(revert)
    intermediate = diff_between(state.base_repository, staging)
    assert "+    return 2" in intermediate.canonical_diff

    tools.apply_patch(
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )
    result = tools.finish_revision("replace the failed mechanism")
    replacement = diff_between(state.base_repository, staging)
    assert result["finished"]
    assert "+    return 3" in replacement.canonical_diff
    assert replacement.patch_hash != state.working_checkpoint.patch_hash


def test_finish_revision_rejects_rolled_back_hash_but_allows_new_hash(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "rejected-hash"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))
    tools.apply_patch(
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )
    rejected_hash = diff_between(state.base_repository, staging).patch_hash
    state.generator_session.conversation.append({
        "role": "system",
        "patch_hash": state.working_checkpoint.patch_hash,
        "rejected_trial_patch_hash": rejected_hash,
    })

    with pytest.raises(RuntimeError, match="previously rolled back"):
        tools.finish_revision("repeat rejected trial")

    tools.apply_patch(
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 3\n+    return 4\n"
    )
    assert tools.finish_revision("different causal edit")["finished"]
    assert diff_between(state.base_repository, staging).patch_hash != rejected_hash


def test_preservation_revision_requires_graph_grounded_execution(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    command = (
        "python", "-c", "from calc import calc; print(calc())",
    )
    value = replace(
        packet("preservation", state.graph_stack.patch_hash),
        requirement_id="req-preservation",
        reproduction_command=command,
        baseline_observation={
            "status": "PASS", "return_code": 0,
            "stdout": "3\n", "stderr": "", "exception": None,
        },
        patched_observation={
            "status": "PASS", "return_code": 0,
            "stdout": "2\n", "stderr": "", "exception": None,
        },
    )
    objective = replace(
        _objective(state),
        objective_kind="PRESERVATION_REGRESSION",
        primary_requirement={"preservation": True},
        counterexamples=(value,),
        validation_obligations=(_validation(command, value.baseline_observation),),
    )
    staging = state.run_root / "generator_staging" / "grounded-validation"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, objective)
    tools.apply_patch(
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 4\n"
    )

    with pytest.raises(RuntimeError, match="reproduction commands"):
        tools.finish_revision("unvalidated repair")
    failed = tools.run_allowed_public_check(command)
    assert failed["grounded_validations"][0]["outcome"] == "FAILED"
    with pytest.raises(RuntimeError, match="still fails graph-grounded"):
        tools.finish_revision("observably wrong repair")

    tools.apply_patch(
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 4\n+    return 3\n"
    )
    passed = tools.run_allowed_public_check(command)
    assert passed["grounded_validations"][0]["outcome"] == "SATISFIED", passed
    assert tools.finish_revision("observably closes preservation")["finished"]


def test_agent_executes_pending_graph_validations_without_model_guessing(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    command = ("python", "-c", "from calc import calc; print(calc())")
    value = replace(
        packet("preservation", state.graph_stack.patch_hash),
        requirement_id="req-preservation",
        reproduction_command=command,
        baseline_observation={
            "status": "PASS", "return_code": 0, "stdout": "3\n",
            "stderr": "", "exception": None,
        },
        patched_observation={
            "status": "PASS", "return_code": 0, "stdout": "2\n",
            "stderr": "", "exception": None,
        },
    )
    objective = replace(
        _objective(state),
        objective_kind="PRESERVATION_REGRESSION",
        primary_requirement={"preservation": True},
        counterexamples=(value,),
        validation_obligations=(_validation(command, value.baseline_observation),),
    )
    model_calls = []

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            available = {item["function"]["name"] for item in tools}
            model_calls.append((available, tuple(messages)))
            if "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"validated graph repair"}'
            else:
                name = "apply_patch"
                arguments = json.dumps({
                    "patch": (
                        "diff --git a/calc.py b/calc.py\n"
                        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                        " def calc():\n-    return 2\n+    return 3\n"
                    ),
                })
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(model_calls)}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "automatic-validation"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=2,
        revision_generator_max_turns=2,
        root_recovery_max_turns=2,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] is None
    assert len(model_calls) == 2
    assert all(
        available != {"run_allowed_public_check"}
        for available, _ in model_calls
    )
    assert any(
        "grounded_validations" in str(message.get("content", ""))
        for _, messages in model_calls for message in messages
    )


def test_failed_generator_apply_restores_exact_pre_call_patch(state_factory, monkeypatch):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "atomic-apply"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))
    before = diff_between(state.base_repository, staging)

    def partial_write_then_fail(tree, patch):
        (tree / "calc.py").write_text(
            "def calc():\n    return 99\n", encoding="utf-8",
        )
        raise RuntimeError("synthetic partial failure")

    monkeypatch.setattr(
        "reachpatch.repair.tools.apply_patch_action", partial_write_then_fail,
    )
    with pytest.raises(RuntimeError, match="synthetic partial failure"):
        tools.apply_patch("synthetic patch")

    restored = diff_between(state.base_repository, staging)
    assert restored.patch_hash == before.patch_hash
    assert (staging / "calc.py").read_text(encoding="utf-8") == (
        "def calc():\n    return 2\n"
    )


def test_generator_apply_rejects_successful_noop(state_factory, monkeypatch):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "noop-apply"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))
    monkeypatch.setattr(
        "reachpatch.repair.tools.apply_patch_action", lambda tree, patch: None,
    )

    with pytest.raises(RuntimeError, match="no-op against the current working tree"):
        tools.apply_patch("synthetic no-op")


def test_deepseek_continues_after_reverting_failed_working_mechanism(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    calls = []
    revert = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 1\n"
    )
    replacement = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            available = {item["function"]["name"] for item in tools}
            if "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"replacement causal edit"}'
            else:
                name = "apply_patch"
                arguments = json.dumps({
                    "patch": revert if not calls else replacement,
                })
            calls.append((name, available))
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(calls)}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "replace-through-base"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=3,
        revision_generator_max_turns=3,
        root_recovery_max_turns=3,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert [name for name, _ in calls] == [
        "apply_patch", "apply_patch", "finish_revision",
    ]
    assert "finish_revision" not in calls[1][1]
    assert "apply_patch" in calls[2][1]
    assert "finish_revision" in calls[2][1]
    assert result["error_kind"] is None
    assert "+    return 3" in tools.inspect_diff()["canonical_diff"]


def test_deepseek_does_not_retain_base_only_revert_as_revision(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    revert = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 1\n"
    )

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": "revert",
                    "function": {
                        "name": "apply_patch",
                        "arguments": json.dumps({"patch": revert}),
                    },
                }],
            }

    staging = state.run_root / "generator_staging" / "base-only-revert"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=1,
        revision_generator_max_turns=1,
        root_recovery_max_turns=1,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] == "TURN_LIMIT"
    assert "+    return 2" in tools.inspect_diff()["canonical_diff"]


def test_deepseek_executes_only_one_mutating_tool_call_per_turn(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    calls = 0
    revert = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 1\n"
    )
    stale_reapply = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 1\n+    return 2\n"
    )
    replacement = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            nonlocal calls
            calls += 1
            available = {item["function"]["name"] for item in tools}
            if "finish_revision" in available:
                tool_calls = [{
                    "id": "finish",
                    "function": {
                        "name": "finish_revision",
                        "arguments": '{"summary":"replacement"}',
                    },
                }]
            elif calls == 1:
                tool_calls = [
                    {
                        "id": "revert",
                        "function": {
                            "name": "apply_patch",
                            "arguments": json.dumps({"patch": revert}),
                        },
                    },
                    {
                        "id": "stale-reapply",
                        "function": {
                            "name": "apply_patch",
                            "arguments": json.dumps({"patch": stale_reapply}),
                        },
                    },
                ]
            else:
                tool_calls = [{
                    "id": "replacement",
                    "function": {
                        "name": "apply_patch",
                        "arguments": json.dumps({"patch": replacement}),
                    },
                }]
            return {"role": "assistant", "tool_calls": tool_calls}

    staging = state.run_root / "generator_staging" / "single-mutating-call"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=3,
        revision_generator_max_turns=3,
        root_recovery_max_turns=3,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert calls == 3
    assert result["error_kind"] is None
    assert "+    return 3" in tools.inspect_diff()["canonical_diff"]
    assert "+    return 2" not in tools.inspect_diff()["canonical_diff"]


def test_deepseek_revises_rejected_cumulative_hash_before_finishing(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "revise-rejected-hash"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    rejected_patch = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )
    apply_unified_diff(staging, rejected_patch)
    rejected_hash = diff_between(state.base_repository, staging).patch_hash
    apply_unified_diff(
        staging,
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 3\n+    return 2\n",
    )
    state.generator_session.conversation.append({
        "role": "system",
        "patch_hash": state.working_checkpoint.patch_hash,
        "rejected_trial_patch_hash": rejected_hash,
    })
    calls = []

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            available = {item["function"]["name"] for item in tools}
            if "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"finish current diff"}'
            else:
                name = "apply_patch"
                patch = (
                    rejected_patch if not calls else
                    "diff --git a/calc.py b/calc.py\n"
                    "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                    " def calc():\n-    return 3\n+    return 4\n"
                )
                arguments = json.dumps({"patch": patch})
            calls.append(name)
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(calls)}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=4,
        revision_generator_max_turns=4,
        root_recovery_max_turns=4,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert calls == ["apply_patch", "apply_patch", "finish_revision"]
    assert result["error_kind"] is None
    assert "+    return 4" in tools.inspect_diff()["canonical_diff"]
    assert not tools.cumulative_patch_rejected()


def test_length_recovery_preserves_working_patch(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    before = Path(state.working_checkpoint.snapshot_tree, "calc.py").read_text(encoding="utf-8")
    class Empty:
        def revise(self, objective, tools, initial=False):
            return {"error_kind": "API_LENGTH", "recovery_used": True}
    result = RepairPlayer(Empty()).revise_working_patch(state, _objective(state))
    assert not result.has_new_nonempty_diff
    assert Path(state.working_checkpoint.snapshot_tree, "calc.py").read_text(encoding="utf-8") == before


def test_generator_result_records_structure_recovery_in_session(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    class Recovered:
        def revise(self, objective, tools, initial=False):
            return GeneratorResult(
                "result", "", "causal", "recovered response",
                error_kind="API_LENGTH", structure_recovery_attempted=True,
            )
    RepairPlayer(Recovered()).revise_working_patch(state, _objective(state))
    assert state.generator_session.structure_recovery_used


def test_revision_budgets_and_counter_limits():
    deepseek = DeepSeekConfig()
    controller = ReachAvoidConfig()
    assert (deepseek.initial_generator_max_turns, deepseek.revision_generator_max_turns) == (20, 12)
    assert deepseek.initial_generator_token_budget == deepseek.revision_generator_token_budget == 32768
    assert controller.max_real_patch_revisions == 8
    assert controller.max_challenge_rounds == 24


def test_initial_empty_graph_symbol_search_uses_local_source_index(state_factory):
    state = state_factory()
    state.graph_stack.program_graph.nodes = {}
    state.graph_stack.program_graph.edges = {}
    state.run_root.mkdir()
    objective = _objective(state)
    tools = RepairToolExecutor(
        Path(state.working_checkpoint.snapshot_tree), state, objective,
    )

    result = tools.search_symbol("calc")

    assert result["matches"]
    assert result["matches"][0]["path"] == "calc.py"
    assert "source_context" in result["matches"][0]


def test_initial_symbol_search_accepts_dotted_python_names(state_factory):
    state = state_factory()
    state.graph_stack.program_graph.nodes = {}
    state.graph_stack.program_graph.edges = {}
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "revision"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))

    result = tools.search_symbol("module.calc")

    assert result["matches"]
    assert result["matches"][0]["path"] == "calc.py"


def test_dotted_class_method_search_excludes_unrelated_methods(state_factory):
    state = state_factory()
    state.graph_stack.program_graph.nodes = {}
    state.graph_stack.program_graph.edges = {}
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "revision"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    (staging / "writers.py").write_text(
        "class RST:\n"
        "    def write(self, lines):\n"
        "        return lines\n\n"
        "class Other:\n"
        "    def write(self, lines):\n"
        "        return []\n",
        encoding="utf-8",
    )
    tools = RepairToolExecutor(staging, state, _objective(state))

    result = tools.search_symbol("RST.write")

    assert [match["symbol"] for match in result["matches"]] == ["RST.write"]


def test_noneditable_graph_match_does_not_hide_editable_source(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "source-fallback"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    source = staging / "project" / "references.py"
    source.parent.mkdir()
    source.write_text(
        "class Expressions:\n"
        "    def rename_table_references(self, old, new):\n"
        "        return new\n",
        encoding="utf-8",
    )
    test_node = next(iter(state.graph_stack.program_graph.nodes.values()))
    state.graph_stack.program_graph.nodes = {
        "test-method": replace(
            test_node,
            node_id="test-method",
            path="tests/test_references.py",
            symbol="MockReference.rename_table_references",
            editable=False,
        ),
    }
    tools = RepairToolExecutor(staging, state, _objective(state))

    result = tools.search_symbol("rename_table_references")

    assert any(
        match["path"] == "project/references.py" and match["editable"]
        for match in result["matches"]
    )


def test_rejected_generator_patch_returns_current_hunk_context(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "revision"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))
    bad_patch = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def missing():\n"
        "+def changed():\n"
        "     return 2\n"
    )

    with pytest.raises(RuntimeError) as captured:
        tools.apply_patch(bad_patch)

    assert "Patch was rejected without changing the tree" in str(captured.value)
    assert "Current source for calc.py" in str(captured.value)
    assert "1: def calc():" in str(captured.value)
    assert "def missing" not in staging.joinpath("calc.py").read_text(encoding="utf-8")
    rejected = state.run_root / "rejected_generator_patches.jsonl"
    assert "def missing" in rejected.read_text(encoding="utf-8")


def test_zero_context_generator_diff_is_checked_then_applied(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    source = tree / "calc.py"
    source.write_text("def calc():\n    return 2\n", encoding="utf-8")
    patch = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -2 +2 @@\n"
        "-    return 2\n"
        "+    return 3\n"
    )

    apply_unified_diff(tree, patch)

    assert source.read_text(encoding="utf-8") == "def calc():\n    return 3\n"


def test_deepseek_turn_budget_forces_revision_convergence(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    prompts = []

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            prompts.append(tuple(
                item.get("content", "") for item in messages
                if item.get("role") == "user"
            ))
            available = {item["function"]["name"] for item in tools}
            name = (
                "read_file" if len(prompts) < 7
                else "finish_revision" if "finish_revision" in available
                else "apply_patch"
            )
            arguments = (
                '{"summary":"finished"}' if name == "finish_revision" else
                '{"path":"calc.py"}' if name == "read_file" else
                '{"patch":"diff --git a/calc.py b/calc.py\\n'
                '--- a/calc.py\\n+++ b/calc.py\\n@@ -1,2 +1,2 @@\\n'
                ' def calc():\\n-    return 2\\n+    return 3\\n"}'
            )
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(prompts)}",
                    "function": {
                        "name": name,
                        "arguments": arguments,
                    },
                }],
            }

    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=9,
        revision_generator_max_turns=9,
        root_recovery_max_turns=9,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))
    staging = state.run_root / "generator_staging" / "convergence"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))

    result = agent.revise(_objective(state), tools)

    assert result["error_kind"] is None
    assert tools.inspect_diff()["canonical_diff"]
    assert any("Eight tool turns remain" in content for turn in prompts for content in turn)


def test_redundant_source_read_forces_apply_patch_choice(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    choices = []
    offered_tools = []
    calls = 0

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            nonlocal calls
            calls += 1
            choices.append(tool_choice)
            available = {item["function"]["name"] for item in tools}
            offered_tools.append(available)
            if calls <= 2:
                name = "read_file"
                arguments = '{"path":"calc.py"}'
            elif "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"finished"}'
            else:
                name = "apply_patch"
                arguments = json.dumps({
                    "patch": (
                        "diff --git a/calc.py b/calc.py\n"
                        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                        " def calc():\n-    return 2\n+    return 3\n"
                    ),
                })
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{calls}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "redundant-read"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=6,
        revision_generator_max_turns=6,
        root_recovery_max_turns=6,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] is None
    assert choices[2] == {
        "type": "function",
        "function": {"name": "apply_patch"},
    }
    assert offered_tools[2] == {"apply_patch"}


def test_http_400_recovery_rebuilds_compact_convergence_session(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    message_lengths = []
    calls = 0

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            nonlocal calls
            calls += 1
            message_lengths.append(len(messages))
            available = {item["function"]["name"] for item in tools}
            if calls == 1:
                name = "read_file"
                arguments = '{"path":"calc.py"}'
            elif calls == 2:
                raise urllib.error.HTTPError(
                    "https://api.deepseek.com/chat/completions", 400,
                    "context length exceeded", None, None,
                )
            elif "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"finished"}'
            else:
                name = "apply_patch"
                arguments = json.dumps({
                    "patch": (
                        "diff --git a/calc.py b/calc.py\n"
                        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                        " def calc():\n-    return 2\n+    return 3\n"
                    ),
                })
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{calls}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "http-400"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=6,
        revision_generator_max_turns=6,
        root_recovery_max_turns=6,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] is None
    assert message_lengths[1] > 2
    assert message_lengths[2] == 2


def test_read_file_marks_eof_and_rejects_redundant_interval(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "read-eof"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))

    first = tools.read_file("calc.py", 1, 200)
    repeated = tools.read_file("calc.py", first["end_line"], 400)

    assert first["eof"]
    assert first["next_start_line"] is None
    assert first["line_count"] == 2
    assert not first["redundant"]
    assert repeated["redundant"]
    assert repeated["content"] == ""


def test_structured_patch_action_becomes_real_unified_diff(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    source = tree / "calc.py"
    source.write_text("def calc():\n    return 1\n", encoding="utf-8")
    before = tmp_path / "before"
    copy_source_tree(tree, before)

    apply_patch_action(tree, """*** Begin Patch
*** Update File: calc.py
@@
 def calc():
-    return 1
+    return 2
*** End Patch
""")

    actual = diff_between(before, tree)
    assert actual.canonical_diff.startswith("diff --git a/calc.py b/calc.py")
    assert "+    return 2" in actual.canonical_diff


def test_standard_unified_diff_without_git_header_is_accepted(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    source = tree / "calc.py"
    source.write_text("def calc():\n    return 1\n", encoding="utf-8")

    apply_patch_action(tree, """--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def calc():
-    return 1
+    return 2
""")

    assert source.read_text(encoding="utf-8") == "def calc():\n    return 2\n"


def test_symbol_search_includes_previously_read_source(state_factory, monkeypatch):
    state = state_factory()
    state.run_root.mkdir()
    staging = state.run_root / "generator_staging" / "read-symbol"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    (staging / "extra.py").write_text(
        "class RequestedSymbol:\n"
        "    def method(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    tools = RepairToolExecutor(staging, state, _objective(state))
    tools.read_file("extra.py", 1, 20)

    class EmptyIndex:
        symbol_files = {"RequestedSymbol": ()}

        def expand_symbol(self, identifier):
            return self.symbol_files.get(identifier, ())

    monkeypatch.setattr(
        "reachpatch.repair.tools.RepositoryIndex.build",
        lambda *args, **kwargs: EmptyIndex(),
    )

    result = tools.search_symbol("RequestedSymbol")
    assert result["matches"][0]["path"] == "extra.py"
    assert result["matches"][0]["symbol"] == "RequestedSymbol"


def test_structured_patch_action_rejects_unchanged_excerpt(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    source = tree / "calc.py"
    source.write_text("def calc():\n    return 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="no-op"):
        apply_patch_action(tree, """*** Begin Patch
*** Update File: calc.py
@@
 def calc():
     return 1
*** End Patch
""")
    assert source.read_text(encoding="utf-8") == "def calc():\n    return 1\n"


def test_apply_patch_rejects_removing_complete_cumulative_diff(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    (state.base_repository / "calc.py").write_text(
        "def calc():\n    return 2\n", encoding="utf-8",
    )
    staging = state.run_root / "generator_staging" / "prevent-empty-diff"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(
        staging, state, replace(_objective(state), objective_kind="INITIAL_PATCH")
    )
    initial = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )
    undo = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 3\n+    return 2\n"
    )

    tools.apply_patch(initial)
    with pytest.raises(RuntimeError, match="removes the complete cumulative diff"):
        tools.apply_patch(undo)

    diff = tools.inspect_diff()["canonical_diff"]
    assert "+    return 3" in diff
    assert "-    return 2" in diff


def test_revision_apply_patch_cannot_reset_incumbent_to_baseline(state_factory):
    """A revision must preserve a non-empty incumbent cumulative patch."""
    state = state_factory()
    state.run_root.mkdir()
    (state.base_repository / "calc.py").write_text(
        "def calc():\n    return 2\n", encoding="utf-8",
    )
    staging = state.run_root / "generator_staging" / "prevent-revision-reset"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    tools = RepairToolExecutor(staging, state, _objective(state))
    initial = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )
    undo = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 3\n+    return 2\n"
    )

    tools.apply_patch(initial)
    with pytest.raises(RuntimeError, match="removes the complete cumulative diff"):
        tools.apply_patch(undo)
    assert "+    return 3" in tools.inspect_diff()["canonical_diff"]


def test_apply_failure_forces_source_refresh_before_new_hunk(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    calls = []

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            available = {item["function"]["name"] for item in tools}
            if calls and calls[-1] == "apply_patch" and "read_file" not in calls:
                name = "read_file"
                arguments = '{"path":"calc.py"}'
            elif "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"corrected hunk"}'
            else:
                name = "apply_patch"
                if "read_file" not in calls:
                    patch = (
                        "diff --git a/calc.py b/calc.py\\n"
                        "--- a/calc.py\\n+++ b/calc.py\\n@@ -1,2 +1,2 @@\\n"
                        "-def missing():\\n+def changed():\\n     return 2\\n"
                    )
                else:
                    patch = (
                        "diff --git a/calc.py b/calc.py\\n"
                        "--- a/calc.py\\n+++ b/calc.py\\n@@ -1,2 +1,2 @@\\n"
                        " def calc():\\n-    return 2\\n+    return 3\\n"
                    )
                arguments = '{"patch":"' + patch + '"}'
            calls.append(name)
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(calls)}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "apply-recovery"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=4,
        revision_generator_max_turns=4,
        root_recovery_max_turns=4,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert calls == ["apply_patch", "read_file", "apply_patch", "finish_revision"]
    assert result["error_kind"] is None
    assert "+    return 3" in tools.inspect_incremental_diff()["canonical_diff"]


def test_structural_response_recovery_still_forces_real_edit(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    calls = 0

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            nonlocal calls
            calls += 1
            available = {item["function"]["name"] for item in tools}
            if calls <= 2:
                return {"role": "assistant", "content": "analysis without a tool call"}
            if "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"recovered"}'
            else:
                name = "apply_patch"
                arguments = json.dumps({
                    "patch": (
                        "diff --git a/calc.py b/calc.py\n"
                        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                        " def calc():\n-    return 2\n+    return 3\n"
                    ),
                })
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{calls}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "structural-recovery"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=5,
        revision_generator_max_turns=5,
        root_recovery_max_turns=5,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] is None
    assert result["recovery_used"]
    assert "+    return 3" in tools.inspect_incremental_diff()["canonical_diff"]


def test_noop_patch_feedback_is_requirement_grounded(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    calls = []
    prompts = []

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            available = {item["function"]["name"] for item in tools}
            prompts.append(tuple(
                item.get("content", "") for item in messages
                if item.get("role") == "user"
            ))
            if calls and calls[-1] == "apply_patch" and "read_file" not in calls:
                name = "read_file"
                arguments = '{"path":"calc.py"}'
            elif "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"behavior changed"}'
            else:
                name = "apply_patch"
                patch = (
                    "*** Begin Patch\n*** Update File: calc.py\n@@\n"
                    " def calc():\n     return 2\n*** End Patch\n"
                    if "read_file" not in calls else
                    "diff --git a/calc.py b/calc.py\n"
                    "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
                    " def calc():\n-    return 2\n+    return 3\n"
                )
                arguments = json.dumps({"patch": patch})
            calls.append(name)
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(calls)}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "noop-recovery"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = replace(
        _objective(state),
        primary_requirement={
            "operation": "calc",
            "expected_observation": {"relation": "return value equals 3"},
        },
        expected_next_effects=("calc returns 3",),
    )
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=4,
        revision_generator_max_turns=4,
        root_recovery_max_turns=4,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] is None
    assert calls == ["apply_patch", "read_file", "apply_patch", "finish_revision"]
    assert any(
        "calc returns 3" in content and "no-ops" in content
        for turn in prompts for content in turn
    ), prompts
    assert "+    return 3" in tools.inspect_incremental_diff()["canonical_diff"]


def test_exact_rejected_patch_does_not_repeat_source_refresh(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    calls = []
    apply_attempts = 0
    noop = (
        "*** Begin Patch\n*** Update File: calc.py\n@@\n"
        " def calc():\n     return 2\n*** End Patch\n"
    )
    good = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            nonlocal apply_attempts
            available = {item["function"]["name"] for item in tools}
            if calls and calls[-1] == "apply_patch" and "read_file" not in calls:
                name = "read_file"
                arguments = '{"path":"calc.py"}'
            elif "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"different algorithm"}'
            else:
                name = "apply_patch"
                apply_attempts += 1
                arguments = json.dumps({
                    "patch": noop if apply_attempts <= 2 else good,
                })
            calls.append(name)
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(calls)}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "exact-rejection"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=5,
        revision_generator_max_turns=5,
        root_recovery_max_turns=5,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] is None
    assert calls == [
        "apply_patch", "read_file", "apply_patch", "apply_patch",
        "finish_revision",
    ]
    assert "+    return 3" in tools.inspect_incremental_diff()["canonical_diff"]


def test_current_cumulative_diff_is_not_resubmitted_as_incremental(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    cumulative = state.working_checkpoint.canonical_diff
    replacement = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )
    calls = []

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            available = {item["function"]["name"] for item in tools}
            if "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"new incremental repair"}'
            else:
                name = "apply_patch"
                arguments = json.dumps({
                    "patch": cumulative if not calls else replacement,
                })
            calls.append(name)
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(calls)}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "cumulative-rejection"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=3,
        revision_generator_max_turns=3,
        root_recovery_max_turns=3,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] is None
    assert calls == ["apply_patch", "apply_patch", "finish_revision"]
    assert "+    return 3" in tools.inspect_incremental_diff()["canonical_diff"]


def test_incremental_causal_brief_is_revision_only(state_factory):
    state = state_factory()
    objective = _objective(state)
    revision_prompt = DeepSeekAgent._prompt(objective)
    initial_prompt = DeepSeekAgent._prompt(replace(
        objective, objective_kind="INITIAL_PATCH",
    ))

    assert "already applied to the working tree" in revision_prompt
    assert "graph-derived causal brief follows first" in revision_prompt
    assert "already applied to the working tree" not in initial_prompt
    assert "graph-derived causal brief follows first" in initial_prompt


def test_out_of_phase_stale_apply_does_not_trap_refresh(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    phases = []
    bad_patch = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        "-def missing():\n+def changed():\n     return 2\n"
    )
    good_patch = (
        "diff --git a/calc.py b/calc.py\n"
        "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n"
        " def calc():\n-    return 2\n+    return 3\n"
    )

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            available = {item["function"]["name"] for item in tools}
            phases.append(available)
            if len(phases) == 1:
                return {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "stale-apply",
                            "function": {
                                "name": "apply_patch",
                                "arguments": json.dumps({"patch": bad_patch}),
                            },
                        },
                        {
                            "id": "deferred-read",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"calc.py"}',
                            },
                        },
                    ],
                }
            if "finish_revision" in available:
                name = "finish_revision"
                arguments = '{"summary":"done"}'
            else:
                name = "apply_patch"
                patch = bad_patch if len(phases) == 1 else good_patch
                arguments = json.dumps({"patch": patch})
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": f"call-{len(phases)}",
                    "function": {"name": name, "arguments": arguments},
                }],
            }

    staging = state.run_root / "generator_staging" / "phase-validation"
    copy_source_tree(Path(state.working_checkpoint.snapshot_tree), staging)
    objective = _objective(state)
    tools = RepairToolExecutor(staging, state, objective)
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=4,
        revision_generator_max_turns=4,
        root_recovery_max_turns=4,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))

    result = agent.revise(objective, tools)

    assert result["error_kind"] is None
    assert all("apply_patch" in phase for phase in phases)
    assert all("read_file" in phase for phase in phases)
    assert "+    return 3" in tools.inspect_incremental_diff()["canonical_diff"]


def test_revision_convergence_uses_incremental_not_cumulative_diff(state_factory):
    state = state_factory()
    state.run_root.mkdir()
    working = Path(state.working_checkpoint.snapshot_tree)
    (working / "calc.py").write_text(
        "def calc():\n    return 2\n", encoding="utf-8",
    )
    seen_tools = []

    class Transport:
        def complete(
            self, messages, *, tools, max_tokens, timeout_seconds,
            tool_choice="auto",
        ):
            names = {item["function"]["name"] for item in tools}
            seen_tools.append(names)
            return {
                "role": "assistant",
                "tool_calls": [{
                    "id": "apply",
                    "function": {
                        "name": "apply_patch",
                        "arguments": (
                            '{"patch":"diff --git a/calc.py b/calc.py\\n'
                            '--- a/calc.py\\n+++ b/calc.py\\n@@ -1,2 +1,2 @@\\n'
                            ' def calc():\\n-    return 2\\n+    return 3\\n"}'
                        ),
                    },
                }],
            }

    staging = state.run_root / "generator_staging" / "revision"
    copy_source_tree(working, staging)
    objective = replace(
        _objective(state),
        validation_obligations=(ValidationObligation(
            validation_id="target-validation", role="TARGET", authority="A",
            command=("python", "-c", "from calc import calc; assert calc() == 3"),
            cwd=".", environment={}, timeout_seconds=60, backend="shared-executor",
            concrete_input=None, input_derivation="test target replay",
            oracle_id="target-oracle", expected_relation="calc returns 3",
            expected_observation={"return_code": 0, "stdout": "", "stderr": "", "exception": None},
            requirement_id="req-target", binding_id="binding-target",
            challenge_id="challenge-target",
        ),),
    )
    tools = RepairToolExecutor(staging, state, objective)
    assert tools.tree != working.resolve()
    agent = DeepSeekAgent(Transport(), DeepSeekConfig(
        initial_generator_max_turns=1,
        revision_generator_max_turns=1,
        root_recovery_max_turns=1,
        initial_generator_wall_time_s=60,
        revision_generator_wall_time_s=60,
        initial_generator_token_budget=1024,
        revision_generator_token_budget=1024,
    ))
    result = agent.revise(objective, tools)
    assert "apply_patch" in seen_tools[0]
    assert result["error_kind"] is None
    assert tools.inspect_incremental_diff()["canonical_diff"]


def test_deepseek_prompt_includes_bounded_public_issue_context(state_factory):
    state = state_factory()
    objective = replace(
        _objective(state),
        public_context=(
            {
                "source": "ISSUE_REPORT",
                "authority": "B",
                "normative": True,
                "content": "STATIC_URL must respect a dynamic SCRIPT_NAME.",
            },
            {
                "source": "PUBLIC_DISCUSSION",
                "authority": "PROVISIONAL",
                "normative": False,
                "content": "Use get_script_prefix only for relative URLs.",
            },
        ),
    )

    context = DeepSeekAgent._repair_context(objective)
    prompt = DeepSeekAgent._prompt(objective)

    assert len(context["public_issue_context"]) == 2
    assert "dynamic SCRIPT_NAME" in prompt
    assert "get_script_prefix" in prompt
