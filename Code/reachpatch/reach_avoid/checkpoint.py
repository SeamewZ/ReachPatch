"""Transactional checkpoint primitives used by the reach-avoid controller."""

from reachpatch.execution.worktree import (
    TransactionalTrial,
    WorktreeManager,
    WorktreeReceipt,
)


def atomic_commit_checkpoint(
    manager: WorktreeManager,
    trial: TransactionalTrial,
    checkpoint_id: str,
) -> WorktreeReceipt:
    """Publish exactly one immutable checkpoint for an active trial."""

    return manager.commit(trial, checkpoint_id)


def rollback(
    manager: WorktreeManager,
    trial: TransactionalTrial,
) -> WorktreeReceipt:
    """Discard an active trial and verify exact restoration."""

    return manager.rollback(trial)


__all__ = [
    "TransactionalTrial",
    "WorktreeManager",
    "WorktreeReceipt",
    "atomic_commit_checkpoint",
    "rollback",
]
