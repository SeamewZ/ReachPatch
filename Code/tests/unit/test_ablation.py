from __future__ import annotations

from pathlib import Path

import pytest

from reachpatch.execution.worktree import WorktreeManager, tree_hash
from reachpatch.repair.ablation import AblationValidation, edit_retention_ablation


def _changed_checkpoint(tmp_path: Path) -> tuple[WorktreeManager, Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    manager = WorktreeManager(tmp_path / "worktrees")
    manager.initialize(repository, "checkpoint-0")
    trial = manager.begin_trial("checkpoint-0")
    Path(trial.tree, "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    manager.commit(trial, "checkpoint-1")
    return manager, manager.checkpoint_tree("checkpoint-0"), "checkpoint-1"


def test_rejected_removal_rolls_back_and_preserves_checkpoint(tmp_path):
    manager, base_tree, checkpoint_id = _changed_checkpoint(tmp_path)
    before_hash = tree_hash(manager.checkpoint_tree(checkpoint_id))

    result = edit_retention_ablation(
        manager,
        base_tree=base_tree,
        checkpoint_id=checkpoint_id,
        validate=lambda *_: AblationValidation(False, True, True, {}),
    )

    assert result.final_checkpoint_id == checkpoint_id
    assert result.removed_group_ids == ()
    assert len(result.retained_group_ids) == 1
    assert result.attempts[0].decision == "RETAIN"
    assert tree_hash(manager.checkpoint_tree(checkpoint_id)) == before_hash
    assert not manager.lease.exists()


def test_accepted_redundant_removal_commits_new_checkpoint(tmp_path):
    manager, base_tree, checkpoint_id = _changed_checkpoint(tmp_path)

    result = edit_retention_ablation(
        manager,
        base_tree=base_tree,
        checkpoint_id=checkpoint_id,
        validate=lambda *_: AblationValidation(True, True, True, {"reason": "redundant"}),
    )

    assert result.final_checkpoint_id != checkpoint_id
    assert len(result.removed_group_ids) == 1
    assert result.retained_group_ids == ()
    assert result.attempts[0].decision == "REMOVE"
    assert result.final_diff.canonical_diff == ""
    assert tree_hash(manager.checkpoint_tree(result.final_checkpoint_id)) == tree_hash(base_tree)
    assert not manager.lease.exists()


def test_validation_error_rolls_back_active_trial(tmp_path):
    manager, base_tree, checkpoint_id = _changed_checkpoint(tmp_path)

    def fail_validation(*_):
        raise RuntimeError("validation failed")

    with pytest.raises(RuntimeError, match="validation failed"):
        edit_retention_ablation(
            manager,
            base_tree=base_tree,
            checkpoint_id=checkpoint_id,
            validate=fail_validation,
        )

    assert not manager.lease.exists()
    assert Path(manager.checkpoint_tree(checkpoint_id), "module.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 2\n"
