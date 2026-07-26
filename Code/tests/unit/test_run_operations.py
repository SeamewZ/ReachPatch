from __future__ import annotations

import json
from pathlib import Path

from reachpatch.artifacts import (
    ArtifactStore,
    recover_run_storage,
    verify_artifacts,
    verify_run,
)
from reachpatch.execution.reconcile import reconcile_actual_diff
from reachpatch.execution.worktree import WorktreeManager
from reachpatch.models.controller import IncumbentCheckpoint, WorkingPatch
from reachpatch.models.budget import BudgetVector
from reachpatch.reporting import build_run_report, export_patch


def _active_run(tmp_path: Path) -> tuple[Path, WorktreeManager, str]:
    root = tmp_path / "run"
    root.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    instance_id = "run-operations"
    (root / "run_manifest.json").write_text(json.dumps({
        "instance": {
            "instance_id": instance_id,
            "base_commit": "base",
            "repository": str(repository),
        }
    }), encoding="utf-8")
    manager = WorktreeManager(root / "worktrees")
    manager.initialize(repository, "checkpoint-0")
    snapshot = manager.checkpoint_tree("checkpoint-0")
    actual = reconcile_actual_diff(snapshot, snapshot)
    patch = WorkingPatch(
        version=0,
        base_commit="base",
        canonical_diff=actual.canonical_diff,
        canonical_diff_hash=actual.canonical_diff_hash,
        base_tree_hash=actual.base_tree_hash,
        working_tree_hash=actual.trial_tree_hash,
        parent_patch_hash=None,
        checkpoint_id="checkpoint-0",
    )
    checkpoint = IncumbentCheckpoint(
        checkpoint_id="checkpoint-0",
        parent_checkpoint_id=None,
        episode_id="episode",
        assignment_id="assignment",
        base_commit="base",
        snapshot_tree=str(snapshot),
        patch=patch,
        actual_fingerprint=actual.fingerprint,
        graph_hashes={},
        environment_hash="environment",
        pass_pairs=(),
        fail_pairs=(),
        unknown_pairs=(),
        blocked_path_obligation_ids=(),
        executed_target_deficit=0.0,
        accepted_transition_id=None,
        generator_session_cursor="0",
        remaining_budget=BudgetVector(execution_seconds=1, wall_seconds=1),
        safe=True,
        graph_reached=False,
    )
    store = ArtifactStore(root / "artifacts")
    store.put(
        "working_patch", patch, instance_id=instance_id, producer="unit-test"
    )
    store.put(
        "incumbent_checkpoint",
        checkpoint,
        instance_id=instance_id,
        producer="unit-test",
    )
    store.put(
        "reach_avoid_state",
        {
            "state_id": "state",
            "base_commit": "base",
            "working_patch_hash": patch.canonical_diff_hash,
            "checkpoint": checkpoint.to_dict(),
            "outcomes": [],
        },
        instance_id=instance_id,
        producer="unit-test",
    )
    return root, manager, instance_id


def test_verify_report_export_and_transaction_recovery(tmp_path):
    root, manager, _ = _active_run(tmp_path)

    verification = verify_run(root)
    compatibility_verification = verify_artifacts(root)
    report = build_run_report(root)
    exported = export_patch(root)

    assert verification.valid
    assert compatibility_verification.verification_hash == verification.verification_hash
    assert report["status"] == "ACTIVE"
    assert report["artifact_verification"]["valid"] is True
    assert exported.is_relative_to(root)
    assert exported.read_text(encoding="utf-8") == ""
    assert (root / "reports" / "run_report.json").is_file()
    assert (root / "reports" / "run_report.md").is_file()

    trial = manager.begin_trial("checkpoint-0")
    Path(trial.tree, "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    recovered = recover_run_storage(root)

    assert recovered["transaction_recovery"]["discarded_trial"] == trial.trial_id
    assert recovered["verification"]["valid"] is True
    assert not manager.lease.exists()
