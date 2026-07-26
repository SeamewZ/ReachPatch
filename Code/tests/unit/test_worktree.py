from __future__ import annotations

from pathlib import Path

from reachpatch.execution.worktree import WorktreeManager, tree_hash
from reachpatch.reach_avoid.checkpoint import atomic_commit_checkpoint, rollback


def _repository(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    return repository


def test_rollback_discards_trial_and_exactly_restores_checkpoint(tmp_path):
    repository = _repository(tmp_path)
    manager = WorktreeManager(tmp_path / "worktrees")
    initialized = manager.initialize(repository, "checkpoint-0")
    trial = manager.begin_trial("checkpoint-0")
    Path(trial.tree, "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    receipt = manager.rollback(trial)

    checkpoint = manager.checkpoint_tree("checkpoint-0")
    assert receipt.operation == "ROLLBACK"
    assert receipt.after_tree_hash == initialized.after_tree_hash == tree_hash(checkpoint)
    assert Path(checkpoint, "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not Path(trial.tree).exists()
    assert not manager.lease.exists()


def test_commit_publishes_one_immutable_checkpoint_and_closes_trial(tmp_path):
    repository = _repository(tmp_path)
    manager = WorktreeManager(tmp_path / "worktrees")
    manager.initialize(repository, "checkpoint-0")
    trial = manager.begin_trial("checkpoint-0")
    Path(trial.tree, "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    receipt = manager.commit(trial, "checkpoint-1")

    old_tree = manager.checkpoint_tree("checkpoint-0")
    new_tree = manager.checkpoint_tree("checkpoint-1")
    assert receipt.operation == "COMMIT"
    assert receipt.result_checkpoint_id == "checkpoint-1"
    assert receipt.after_tree_hash == tree_hash(new_tree)
    assert Path(old_tree, "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert Path(new_tree, "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert not Path(trial.tree).exists()
    assert not manager.lease.exists()


def test_checkpoint_compatibility_primitives_delegate_transactionally(tmp_path):
    repository = _repository(tmp_path)
    manager = WorktreeManager(tmp_path / "worktrees")
    manager.initialize(repository, "checkpoint-0")
    trial = manager.begin_trial("checkpoint-0")
    Path(trial.tree, "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    committed = atomic_commit_checkpoint(manager, trial, "checkpoint-1")

    assert committed.result_checkpoint_id == "checkpoint-1"
    trial = manager.begin_trial("checkpoint-1")
    Path(trial.tree, "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    restored = rollback(manager, trial)
    assert restored.operation == "ROLLBACK"
