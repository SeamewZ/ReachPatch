from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from reachpatch.execution.worktree import (
    apply_generator_result, copy_source_tree, diff_between, register_runtime_root,
)
from reachpatch.models.evidence import ConfirmedFailure, FailureHistory, LockedCheckSet, ObservationBundle
from reachpatch.models.reach_avoid import Decision, GeneratorResult
from reachpatch.models.graphs import ChallengeStatus, empty_graph_stack
from reachpatch.models.reach_avoid import CheckpointEvidence
from reachpatch.models.reach_avoid import ReachAvoidPhase
from reachpatch.reach_avoid.checkpoint import (
    CheckpointStore, IncompatibleArtifactError, capture_current_graph_checkpoint,
    capture_initial_checkpoint, restore_checkpoint,
)
from reachpatch.reach_avoid.certificates import verify_transition_certificate
from reachpatch.reach_avoid.persistence import apply_transition_decision
from reachpatch.reach_avoid.transition import (
    decide_reach_avoid_transition, evaluate_trial_transition,
)


def _consistent_state(state):
    actual = diff_between(state.base_repository, state.working_checkpoint.snapshot_tree)
    stack = empty_graph_stack(state.base_commit, actual.patch_hash)
    checkpoint = replace(
        state.working_checkpoint,
        patch_hash=actual.patch_hash,
        canonical_diff=actual.canonical_diff,
        graph_hashes=stack.graph_hashes(),
    )
    state.graph_stack = stack
    state.working_checkpoint = checkpoint
    return state


def test_commit_updates_patch_and_four_graphs_atomically(tmp_path):
    base = tmp_path / "base"
    tree = tmp_path / "tree"
    base.mkdir()
    tree.mkdir()
    (base / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tree / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
    actual = diff_between(base, tree)
    stack = empty_graph_stack("base", actual.patch_hash)
    store = CheckpointStore(tmp_path / "run")
    checkpoint = capture_initial_checkpoint(
        store=store,
        base_repository=base,
        source_tree=tree,
        graph_stack=stack,
        evidence=CheckpointEvidence(True, True, 0, 0, 0, 0, 0, 0),
        locked_checks=LockedCheckSet(),
        observations=ObservationBundle(),
        status="WORKING",
    )
    store.validate(checkpoint, base)
    assert checkpoint.patch_hash == stack.patch_hash
    assert set(checkpoint.graph_hashes) == {"requirement", "program", "binding", "challenge"}
    assert (store.path(checkpoint.checkpoint_id) / "working_tree" / "value.py").is_file()


def test_provisional_becomes_next_revision_parent(state_factory, tmp_path):
    state = _consistent_state(state_factory())
    run_root = tmp_path / "run-store"
    state.run_root = run_root
    store = CheckpointStore(run_root)
    first = capture_initial_checkpoint(
        store=store,
        base_repository=state.base_repository,
        source_tree=state.working_checkpoint.snapshot_tree,
        graph_stack=state.graph_stack,
        evidence=state.working_checkpoint.evidence,
        locked_checks=state.locked_checks,
        observations=state.observations,
        status="WORKING",
    )
    state.working_checkpoint = first
    second = capture_current_graph_checkpoint(state, store, "PROVISIONAL")
    assert second.parent_checkpoint_id == first.checkpoint_id
    state.working_checkpoint = second
    third = capture_current_graph_checkpoint(state, store, "WORKING")
    assert third.parent_checkpoint_id == second.checkpoint_id


def test_rollback_restores_patch_and_four_graphs(state_factory, tmp_path):
    state = _consistent_state(state_factory())
    state.run_root = tmp_path / "store"
    store = CheckpointStore(state.run_root)
    checkpoint = capture_initial_checkpoint(
        store=store,
        base_repository=state.base_repository,
        source_tree=state.working_checkpoint.snapshot_tree,
        graph_stack=state.graph_stack,
        evidence=state.working_checkpoint.evidence,
        locked_checks=state.locked_checks,
        observations=state.observations,
        status="WORKING",
    )
    state.graph_stack.patch_hash = "stale"
    restore_checkpoint(state, checkpoint, store)
    assert state.working_checkpoint.patch_hash == state.graph_stack.patch_hash
    assert state.graph_stack.graph_hashes() == checkpoint.graph_hashes


def test_checkpoint_hash_mismatch_fails(state_factory, tmp_path):
    state = _consistent_state(state_factory())
    state.run_root = tmp_path / "store"
    store = CheckpointStore(state.run_root)
    checkpoint = capture_initial_checkpoint(
        store=store,
        base_repository=state.base_repository,
        source_tree=state.working_checkpoint.snapshot_tree,
        graph_stack=state.graph_stack,
        evidence=state.working_checkpoint.evidence,
        locked_checks=state.locked_checks,
        observations=state.observations,
        status="WORKING",
    )
    graph_path = store.path(checkpoint.checkpoint_id) / "program_graph.json"
    raw = json.loads(graph_path.read_text(encoding="utf-8"))
    raw["patch_hash"] = "tampered"
    graph_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeError, match="hash|stale"):
        store.validate(checkpoint, state.base_repository)


def test_runtime_snapshot_is_compared_as_a_complete_source_tree(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "source.py").write_text("value = 1\n", encoding="utf-8")
    (repository / ".git").write_text(
        "gitdir: /outside/repository.git\n", encoding="utf-8",
    )
    run_root = tmp_path / "runs" / "case"
    snapshot = run_root / "checkpoint_store" / "checkpoint" / "working_tree"
    snapshot.parent.mkdir(parents=True)
    copy_source_tree(repository, snapshot)
    register_runtime_root(run_root)

    assert diff_between(repository, snapshot).empty


def test_repository_runtime_directory_is_excluded_from_source_diff(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "source.py").write_text("value = 1\n", encoding="utf-8")
    run_root = repository / "runs" / "case"
    run_root.mkdir(parents=True)
    (run_root / "artifact.json").write_text("{}\n", encoding="utf-8")
    register_runtime_root(run_root)
    snapshot = tmp_path / "snapshot"
    copy_source_tree(repository, snapshot, exclude_paths=(run_root,))

    assert diff_between(repository, snapshot).empty


def test_generator_incremental_diff_is_the_single_applied_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    run_root = tmp_path / "run"
    staging = run_root / "generator_staging" / "attempt"
    target = run_root / "initial_working"
    copy_source_tree(source, staging)
    copy_source_tree(source, target)
    (staging / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
    register_runtime_root(run_root)
    incremental = diff_between(source, staging)
    result = GeneratorResult(
        "result", incremental.canonical_diff, "causal", "change value",
        modified_tree=str(staging),
    )

    apply_generator_result(target, result)

    assert (target / "value.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert diff_between(source, target).patch_hash == incremental.patch_hash


def test_apply_diff_does_not_discover_a_parent_git_repository(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    subprocess.run(("git", "init", "-q"), cwd=parent, check=True)
    tree = parent / "nested" / "working"
    tree.mkdir(parents=True)
    (tree / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = GeneratorResult(
        "result",
        """diff --git a/value.py b/value.py
--- a/value.py
+++ b/value.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
""",
        "causal",
        "change value",
    )

    apply_generator_result(tree, result)

    assert (tree / "value.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_apply_diff_recounts_model_hunk_lengths(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    result = GeneratorResult(
        "result",
        """diff --git a/value.py b/value.py
--- a/value.py
+++ b/value.py
@@ -1,9 +1,9 @@
-VALUE = 1
+VALUE = 2
""",
        "causal",
        "change value",
    )

    apply_generator_result(tree, result)

    assert (tree / "value.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_old_artifact_schema_is_rejected(tmp_path):
    store = CheckpointStore(tmp_path)
    path = store.path("old")
    path.mkdir()
    (path / "checkpoint.json").write_text(
        json.dumps({"schema": "0.9", "checkpoint": {}}), encoding="utf-8",
    )
    with pytest.raises(IncompatibleArtifactError, match="retired pre-integrated"):
        store.load("old")


def test_checkpoint_restores_runtime_failure_and_frontier_state(state_factory, tmp_path):
    state = _consistent_state(state_factory())
    state.run_root = tmp_path / "runtime-store"
    state.confirmed_failures = []
    state.frontier_attempts["frontier"] = 2
    state.challenge_round_count = 4
    state.revision_count = 3
    state.phase = ReachAvoidPhase.REPAIR
    store = CheckpointStore(state.run_root)
    checkpoint = capture_initial_checkpoint(
        store=store,
        base_repository=state.base_repository,
        source_tree=state.working_checkpoint.snapshot_tree,
        graph_stack=state.graph_stack,
        evidence=state.working_checkpoint.evidence,
        locked_checks=state.locked_checks,
        observations=state.observations,
        status="WORKING",
        state=state,
    )
    state.frontier_attempts = {}
    state.challenge_round_count = 0
    state.revision_count = 0
    state.phase = ReachAvoidPhase.CHALLENGE
    restore_checkpoint(state, checkpoint, store)
    assert state.frontier_attempts == {"frontier": 2}
    assert state.challenge_round_count == 4
    assert state.revision_count == 3
    assert state.phase is ReachAvoidPhase.REPAIR


def test_checkpoint_open_high_uses_distinct_obligations(state_factory, tmp_path):
    state = state_factory(
        target_status=ChallengeStatus.PASS,
        stability_runs=2,
    )
    state.run_root = tmp_path / "obligation-store"
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    duplicate = replace(
        cell,
        challenge_id="challenge-pending-equivalent",
        input_recipe=replace(
            cell.input_recipe,
            recipe_id="recipe-pending-equivalent",
        ),
        trace_bundle_id=None,
        stability_runs=0,
        terminal_status=ChallengeStatus.PENDING,
    )
    state.graph_stack.challenge_graph.cells[duplicate.challenge_id] = duplicate
    store = CheckpointStore(state.run_root)

    checkpoint = capture_current_graph_checkpoint(
        state, store, "CHALLENGE_EVIDENCE",
    )

    assert checkpoint.open_high_challenge_ids == ()
    assert checkpoint.evidence.open_high_challenge_count == 0
    store.validate(checkpoint, state.base_repository)


def test_repeated_checkpoint_snapshot_reuses_immutable_file_content(state_factory, tmp_path):
    state = state_factory()
    state.run_root = tmp_path / "run"
    store = CheckpointStore(state.run_root)
    first = capture_current_graph_checkpoint(state, store, "FIRST")
    state.working_checkpoint = first
    state.checkpoint_history[first.checkpoint_id] = first
    second = capture_current_graph_checkpoint(state, store, "SECOND")
    first_file = Path(first.snapshot_tree) / "calc.py"
    second_file = Path(second.snapshot_tree) / "calc.py"
    assert first_file.stat().st_ino == second_file.stat().st_ino
    assert first_file.read_text(encoding="utf-8") == second_file.read_text(encoding="utf-8")


def test_rollback_evidence_checkpoint_restores_failure_mechanism_history(state_factory, tmp_path):
    state = _consistent_state(state_factory())
    state.run_root = tmp_path / "rollback-evidence"
    state.failure_history["signature"] = FailureHistory(
        "signature", mechanism_failures=[{
            "mechanism_id": "wrong-branch",
            "source_patch_hash": "fixture-patch",
            "failure_signature": "signature",
            "changed_hunk_ids": (),
        }],
    )
    store = CheckpointStore(state.run_root)
    checkpoint = capture_initial_checkpoint(
        store=store,
        base_repository=state.base_repository,
        source_tree=state.working_checkpoint.snapshot_tree,
        graph_stack=state.graph_stack,
        evidence=state.working_checkpoint.evidence,
        locked_checks=state.locked_checks,
        observations=state.observations,
        status="ROLLBACK_EVIDENCE",
        state=state,
    )
    state.failure_history = {}
    restore_checkpoint(state, checkpoint, store)
    assert state.failure_history["signature"].mechanism_failures[0]["mechanism_id"] == "wrong-branch"


def test_rollback_transition_restores_source_and_verifies_certificate(state_factory, tmp_path):
    state = _consistent_state(state_factory())
    state.run_root = tmp_path / "rollback-transition"
    store = CheckpointStore(state.run_root)
    source = capture_initial_checkpoint(
        store=store,
        base_repository=state.base_repository,
        source_tree=state.working_checkpoint.snapshot_tree,
        graph_stack=state.graph_stack,
        evidence=state.working_checkpoint.evidence,
        locked_checks=state.locked_checks,
        observations=state.observations,
        status="WORKING",
        state=state,
    )
    state.working_checkpoint = source
    state.checkpoint_history = {source.checkpoint_id: source}
    result = GeneratorResult(
        "generator-result",
        """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def calc():
-    return 2
+    return 3
""",
        "unrelated-edit",
        "non-progressing trial",
    )
    trial = evaluate_trial_transition(state, result)
    assert trial.trial_patch_changed and trial.entered_evaluation
    assert trial.decision.value == "ROLLBACK"
    state.revision_count += 1
    certificate = apply_transition_decision(state, trial, store)
    assert state.working_checkpoint.patch_hash == source.patch_hash
    assert state.graph_stack.graph_hashes() == source.graph_hashes
    assert state.working_checkpoint.parent_checkpoint_id == source.checkpoint_id
    assert any(
        event.get("patch_hash") == source.patch_hash
        and event.get("rejected_trial_patch_hash") == trial.cumulative_diff.patch_hash
        for event in state.generator_session.conversation
    )
    verify_transition_certificate(certificate, store)


def test_rollback_does_not_close_incumbent_failure(state_factory, tmp_path):
    state = _consistent_state(state_factory())
    state.run_root = tmp_path / "rollback-open-failure"
    failure = ConfirmedFailure(
        "failure-current", "requirement-current", "binding-current",
        "challenge-current", "counterexample-current",
        state.graph_stack.patch_hash, "signature-current", "component-current",
        None, True, 0, True,
    )
    state.confirmed_failures = [failure]
    state.failure_history[failure.failure_signature] = FailureHistory(
        failure.failure_signature, counterexample_ids=[failure.counterexample_id],
    )
    store = CheckpointStore(state.run_root)
    source = capture_initial_checkpoint(
        store=store,
        base_repository=state.base_repository,
        source_tree=state.working_checkpoint.snapshot_tree,
        graph_stack=state.graph_stack,
        evidence=state.working_checkpoint.evidence,
        locked_checks=state.locked_checks,
        observations=state.observations,
        status="WORKING",
        state=state,
    )
    state.working_checkpoint = source
    state.checkpoint_history = {source.checkpoint_id: source}
    result = GeneratorResult(
        "generator-result",
        """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def calc():
-    return 2
+    return 3
""",
        "regressing-edit",
        "trial must roll back",
    )
    trial = evaluate_trial_transition(state, result)
    trial.evidence = replace(
        trial.evidence,
        mechanical=replace(
            trial.evidence.mechanical,
            passed=False,
            failure_reasons=("forced mechanical regression",),
        ),
        confirmed_failures_closed=(failure.failure_id,),
        target_failures_closed=(failure.failure_id,),
    )
    trial.transition_decision = decide_reach_avoid_transition(
        state, trial.graph_stack, trial.evidence,
    )
    assert trial.decision is Decision.ROLLBACK
    state.revision_count += 1

    apply_transition_decision(state, trial, store)

    current = next(item for item in state.confirmed_failures if item.failure_id == failure.failure_id)
    assert current.open
    assert not state.failure_history[failure.failure_signature].closed
