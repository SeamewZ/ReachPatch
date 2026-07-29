from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reachpatch.models.base import SerializableRecord, canonical_json, stable_id, utc_now


_EXCLUDES = {
    ".git", ".hg", ".svn", ".reachpatch", ".pytest_cache",
    "__pycache__", ".venv", "venv", "node_modules", "build", "dist",
}


def tree_hash(root: str | Path) -> str:
    base = Path(root).resolve()
    digest = hashlib.sha256()
    for current, directories, names in os.walk(base):
        directories[:] = sorted(name for name in directories if name not in _EXCLUDES)
        directory = Path(current)
        for name in sorted(names):
            path = directory / name
            relative = str(path.relative_to(base)).replace(os.sep, "/")
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _copy_tree(source: Path, destination: Path) -> None:
    def ignore(_: str, names: list[str]) -> set[str]:
        return {name for name in names if name in _EXCLUDES}

    shutil.copytree(source, destination, symlinks=True, ignore=ignore)


@dataclass(frozen=True, slots=True)
class WorktreeReceipt(SerializableRecord):
    receipt_id: str
    operation: str
    source_checkpoint_id: str
    result_checkpoint_id: str | None
    trial_id: str | None
    before_tree_hash: str
    after_tree_hash: str
    snapshot_tree: str
    created_at: str


@dataclass(frozen=True, slots=True)
class TransactionalTrial(SerializableRecord):
    trial_id: str
    source_checkpoint_id: str
    tree: str
    source_tree_hash: str
    lease_path: str


class WorktreeManager:
    """Own exactly one accepted checkpoint lineage and one optional trial."""

    def __init__(self, run_root: str | Path) -> None:
        self.root = Path(run_root).resolve()
        self.checkpoints = self.root / "checkpoints"
        self.transaction = self.root / "transaction"
        self.uncertified = self.root / "uncertified"
        self.receipts = self.root / "receipts"
        self.lease = self.transaction / "active.json"
        for path in (
            self.checkpoints, self.transaction, self.uncertified, self.receipts,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(canonical_json(value))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def checkpoint_tree(self, checkpoint_id: str) -> Path:
        return self.checkpoints / checkpoint_id / "tree"

    def initialize(self, repository: str | Path, checkpoint_id: str) -> WorktreeReceipt:
        source = Path(repository).resolve()
        destination = self.checkpoint_tree(checkpoint_id)
        if not source.is_dir():
            raise FileNotFoundError(source)
        if destination.exists():
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=False)
        _copy_tree(source, destination)
        digest = tree_hash(destination)
        receipt = WorktreeReceipt(
            receipt_id=stable_id("worktree-receipt", "initialize", checkpoint_id, digest),
            operation="INITIALIZE",
            source_checkpoint_id=checkpoint_id,
            result_checkpoint_id=checkpoint_id,
            trial_id=None,
            before_tree_hash=tree_hash(source),
            after_tree_hash=digest,
            snapshot_tree=str(destination),
            created_at=utc_now(),
        )
        self._atomic_json(self.receipts / f"{receipt.receipt_id}.json", receipt.to_dict())
        return receipt

    def begin_trial(self, checkpoint_id: str) -> TransactionalTrial:
        if self.lease.exists():
            active = json.loads(self.lease.read_text(encoding="utf-8"))
            raise RuntimeError(f"transaction already active: {active.get('trial_id')}")
        source = self.checkpoint_tree(checkpoint_id)
        if not source.is_dir():
            raise FileNotFoundError(source)
        source_hash = tree_hash(source)
        trial_id = stable_id("trial", checkpoint_id, source_hash, utc_now())
        trial_root = self.transaction / trial_id
        trial_tree = trial_root / "tree"
        trial_root.mkdir(parents=True, exist_ok=False)
        _copy_tree(source, trial_tree)
        trial = TransactionalTrial(
            trial_id=trial_id,
            source_checkpoint_id=checkpoint_id,
            tree=str(trial_tree),
            source_tree_hash=source_hash,
            lease_path=str(self.lease),
        )
        self._atomic_json(self.lease, trial.to_dict())
        return trial

    def commit(self, trial: TransactionalTrial, checkpoint_id: str) -> WorktreeReceipt:
        self._verify_active(trial)
        trial_tree = Path(trial.tree)
        destination = self.checkpoint_tree(checkpoint_id)
        if destination.exists():
            raise FileExistsError(destination)
        temporary_parent = self.checkpoints / f".{checkpoint_id}.pending"
        temporary_tree = temporary_parent / "tree"
        temporary_parent.mkdir(parents=True, exist_ok=False)
        try:
            _copy_tree(trial_tree, temporary_tree)
            before = tree_hash(self.checkpoint_tree(trial.source_checkpoint_id))
            after = tree_hash(temporary_tree)
            if before != trial.source_tree_hash:
                raise RuntimeError("accepted checkpoint changed while trial was active")
            os.replace(temporary_parent, destination.parent)
            receipt = WorktreeReceipt(
                receipt_id=stable_id("worktree-receipt", "commit", trial.trial_id, checkpoint_id, after),
                operation="COMMIT",
                source_checkpoint_id=trial.source_checkpoint_id,
                result_checkpoint_id=checkpoint_id,
                trial_id=trial.trial_id,
                before_tree_hash=before,
                after_tree_hash=after,
                snapshot_tree=str(destination),
                created_at=utc_now(),
            )
            self._finish_trial(trial)
            self._atomic_json(self.receipts / f"{receipt.receipt_id}.json", receipt.to_dict())
            return receipt
        except BaseException:
            if temporary_parent.exists():
                shutil.rmtree(temporary_parent)
            raise

    def rollback(self, trial: TransactionalTrial) -> WorktreeReceipt:
        self._verify_active(trial)
        checkpoint_tree = self.checkpoint_tree(trial.source_checkpoint_id)
        before = tree_hash(Path(trial.tree))
        restored = tree_hash(checkpoint_tree)
        if restored != trial.source_tree_hash:
            raise RuntimeError("rollback target does not match its checkpoint hash")
        receipt = WorktreeReceipt(
            receipt_id=stable_id("worktree-receipt", "rollback", trial.trial_id, restored),
            operation="ROLLBACK",
            source_checkpoint_id=trial.source_checkpoint_id,
            result_checkpoint_id=trial.source_checkpoint_id,
            trial_id=trial.trial_id,
            before_tree_hash=before,
            after_tree_hash=restored,
            snapshot_tree=str(checkpoint_tree),
            created_at=utc_now(),
        )
        self._finish_trial(trial)
        self._atomic_json(self.receipts / f"{receipt.receipt_id}.json", receipt.to_dict())
        return receipt

    def keep_uncertified(self, trial: TransactionalTrial) -> WorktreeReceipt:
        """Archive a trial without replacing the last verified checkpoint."""

        self._verify_active(trial)
        trial_tree = Path(trial.tree)
        destination = self.uncertified / trial.trial_id
        if destination.exists():
            raise FileExistsError(destination)
        before = tree_hash(self.checkpoint_tree(trial.source_checkpoint_id))
        after = tree_hash(trial_tree)
        if before != trial.source_tree_hash:
            raise RuntimeError("accepted checkpoint changed while trial was active")
        os.replace(trial_tree.parent, destination)
        self.lease.unlink(missing_ok=True)
        snapshot = destination / "tree"
        receipt = WorktreeReceipt(
            receipt_id=stable_id(
                "worktree-receipt", "keep-uncertified", trial.trial_id, after,
            ),
            operation="KEEP_UNCERTIFIED",
            source_checkpoint_id=trial.source_checkpoint_id,
            result_checkpoint_id=None,
            trial_id=trial.trial_id,
            before_tree_hash=before,
            after_tree_hash=after,
            snapshot_tree=str(snapshot),
            created_at=utc_now(),
        )
        self._atomic_json(self.receipts / f"{receipt.receipt_id}.json", receipt.to_dict())
        return receipt

    def recover(self) -> dict[str, Any]:
        if not self.lease.exists():
            return {"discarded_trial": None, "checkpoint_count": self._checkpoint_count()}
        raw = json.loads(self.lease.read_text(encoding="utf-8"))
        trial_tree = Path(str(raw["tree"])).resolve()
        transaction_root = self.transaction.resolve()
        if not trial_tree.is_relative_to(transaction_root):
            raise RuntimeError("active transaction lease points outside the run transaction root")
        trial_root = trial_tree.parent
        trial_id = str(raw["trial_id"])
        if trial_root.exists():
            shutil.rmtree(trial_root)
        self.lease.unlink(missing_ok=True)
        return {"discarded_trial": trial_id, "checkpoint_count": self._checkpoint_count()}

    def _verify_active(self, trial: TransactionalTrial) -> None:
        if not self.lease.is_file():
            raise RuntimeError("transaction lease is missing")
        active = json.loads(self.lease.read_text(encoding="utf-8"))
        if active.get("trial_id") != trial.trial_id:
            raise RuntimeError("transaction lease belongs to another trial")
        if not Path(trial.tree).is_dir():
            raise FileNotFoundError(trial.tree)

    def _finish_trial(self, trial: TransactionalTrial) -> None:
        trial_root = Path(trial.tree).parent
        if trial_root.exists():
            shutil.rmtree(trial_root)
        self.lease.unlink(missing_ok=True)

    def _checkpoint_count(self) -> int:
        return sum(1 for path in self.checkpoints.iterdir() if (path / "tree").is_dir())
