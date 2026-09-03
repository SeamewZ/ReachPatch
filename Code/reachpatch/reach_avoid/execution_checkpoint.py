from __future__ import annotations

"""Immutable persistence for graph-free execution checkpoints."""

import dataclasses
import enum
import json
import os
import shutil
import tempfile
import types
import typing
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

from reachpatch.execution.worktree import (
    apply_unified_diff, copy_source_tree, diff_between, tree_hash,
)
from reachpatch.models.base import canonical_json
from reachpatch.models.execution import ReachAvoidState, StateCheckpoint


EXECUTION_SCHEMA_NAME = "reachpatch-execution-checkpoint-v2"
STATE_SCHEMA_NAME = "reachpatch-execution-state-v2"
T = TypeVar("T")


class IncompatibleExecutionArtifact(RuntimeError):
    """Raised when a persisted artifact is not an execution-v2 record."""


def _decode(value: Any, annotation: Any) -> Any:
    if annotation is Any or annotation is typing.Any:
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {typing.Union, types.UnionType}:
        if value is None and type(None) in args:
            return None
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _decode(value, candidate)
            except (TypeError, ValueError, KeyError):
                continue
        raise TypeError(f"cannot decode union {annotation}")
    if origin is tuple:
        item_type = args[0] if args else Any
        if len(args) > 1 and args[-1] is not Ellipsis:
            return tuple(_decode(item, kind) for item, kind in zip(value, args))
        return tuple(_decode(item, item_type) for item in value)
    if origin is list:
        item_type = args[0] if args else Any
        return [_decode(item, item_type) for item in value]
    if origin is set:
        item_type = args[0] if args else Any
        return {_decode(item, item_type) for item in value}
    if origin is dict:
        key_type, value_type = args or (Any, Any)
        return {
            _decode(key, key_type): _decode(item, value_type)
            for key, item in value.items()
        }
    if annotation is Path:
        return Path(value)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return annotation(value)
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        hints = get_type_hints(annotation)
        return annotation(**{
            field.name: _decode(value[field.name], hints.get(field.name, Any))
            for field in dataclasses.fields(annotation)
            if field.name in value
        })
    if annotation in {str, int, float, bool}:
        return annotation(value)
    return value


def record_from_dict(cls: type[T], value: dict[str, Any]) -> T:
    return _decode(value, cls)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(canonical_json(value))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class ExecutionCheckpointStore:
    def __init__(self, run_root: Path) -> None:
        self.run_root = Path(run_root).resolve()
        self.root = self.run_root / "execution_checkpoints"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, checkpoint_id: str) -> Path:
        return self.root / checkpoint_id

    def load(self, checkpoint_id: str) -> StateCheckpoint:
        path = self.path(checkpoint_id) / "checkpoint.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != EXECUTION_SCHEMA_NAME:
            raise IncompatibleExecutionArtifact("not an execution checkpoint v2")
        return record_from_dict(StateCheckpoint, raw["checkpoint"])

    def save(
        self,
        checkpoint: StateCheckpoint,
        source_tree: Path,
        *,
        mechanical: Any,
        target_results: tuple[Any, ...] = (),
        preservation_results: tuple[Any, ...] = (),
        challenge_results: tuple[Any, ...] = (),
    ) -> StateCheckpoint:
        final = self.path(checkpoint.checkpoint_id)
        expected_snapshot = final / "working_tree"
        if Path(checkpoint.snapshot_tree) != expected_snapshot:
            raise ValueError("checkpoint snapshot path is not content-addressed")
        if final.exists():
            loaded = self.load(checkpoint.checkpoint_id)
            self.validate(loaded, None)
            return loaded
        source_tree = Path(source_tree).resolve()
        source_hash = tree_hash(source_tree)
        if checkpoint.working_tree_hash and checkpoint.working_tree_hash != source_hash:
            raise RuntimeError("checkpoint source tree hash mismatch")
        temporary = Path(tempfile.mkdtemp(prefix=f".{checkpoint.checkpoint_id}.", dir=self.root))
        try:
            copy_source_tree(source_tree, temporary / "working_tree", hardlink_files=True)
            _atomic_json(temporary / "checkpoint.json", {
                "schema": EXECUTION_SCHEMA_NAME,
                "checkpoint": checkpoint.to_dict(),
                "tree_hash": source_hash,
                "mechanical": mechanical.to_dict() if hasattr(mechanical, "to_dict") else mechanical,
                "target_results": [item.to_dict() for item in target_results],
                "preservation_results": [item.to_dict() for item in preservation_results],
                "challenge_results": [item.to_dict() for item in challenge_results],
            })
            os.replace(temporary, final)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return self.load(checkpoint.checkpoint_id)

    def replace_metadata(self, checkpoint: StateCheckpoint) -> StateCheckpoint:
        path = self.path(checkpoint.checkpoint_id) / "checkpoint.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        previous = record_from_dict(StateCheckpoint, raw["checkpoint"])
        immutable = ("patch_hash", "cumulative_diff", "snapshot_tree", "working_tree_hash")
        if any(getattr(previous, field) != getattr(checkpoint, field) for field in immutable):
            raise RuntimeError("execution checkpoint content cannot be mutated")
        raw["checkpoint"] = checkpoint.to_dict()
        _atomic_json(path, raw)
        return self.load(checkpoint.checkpoint_id)

    def recover_snapshot(self, checkpoint: StateCheckpoint, clean_snapshot: Path) -> Path:
        destination = Path(checkpoint.snapshot_tree)
        if destination.is_dir():
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".rebuild-", dir=destination.parent))
        rebuilt = temporary / "working_tree"
        try:
            copy_source_tree(Path(clean_snapshot), rebuilt)
            apply_unified_diff(rebuilt, checkpoint.cumulative_diff)
            actual = diff_between(clean_snapshot, rebuilt)
            if actual.patch_hash != checkpoint.patch_hash:
                raise RuntimeError("rebuilt checkpoint patch hash mismatch")
            rebuilt_hash = tree_hash(rebuilt)
            if checkpoint.working_tree_hash and rebuilt_hash != checkpoint.working_tree_hash:
                raise RuntimeError("rebuilt checkpoint tree hash mismatch")
            os.replace(rebuilt, destination)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return destination

    def validate(
        self,
        checkpoint: StateCheckpoint,
        base_repository: Path | None,
        *,
        clean_snapshot: Path | None = None,
    ) -> None:
        loaded = self.load(checkpoint.checkpoint_id)
        if loaded != checkpoint:
            raise RuntimeError("execution checkpoint metadata mismatch")
        snapshot = Path(checkpoint.snapshot_tree)
        if not snapshot.is_dir():
            if clean_snapshot is None:
                raise FileNotFoundError(snapshot)
            snapshot = self.recover_snapshot(checkpoint, clean_snapshot)
        # The immutable clean snapshot is the authority for the cumulative
        # diff. The source repository may be modified by an embedding caller.
        base_value = clean_snapshot or base_repository
        if base_value is not None:
            base = Path(base_value)
            actual = diff_between(base, snapshot)
            if actual.patch_hash != checkpoint.patch_hash or actual.canonical_diff != checkpoint.cumulative_diff:
                raise RuntimeError("execution checkpoint cumulative diff mismatch")
        if checkpoint.working_tree_hash and tree_hash(snapshot) != checkpoint.working_tree_hash:
            raise RuntimeError("execution checkpoint working tree hash mismatch")

    def write_state(self, state: ReachAvoidState) -> Path:
        path = self.run_root / "execution_state.json"
        _atomic_json(path, {"schema": STATE_SCHEMA_NAME, "state": state.to_dict()})
        return path

    def read_state(self) -> ReachAvoidState:
        raw = json.loads((self.run_root / "execution_state.json").read_text(encoding="utf-8"))
        if raw.get("schema") != STATE_SCHEMA_NAME:
            raise IncompatibleExecutionArtifact("not an execution state v2")
        return record_from_dict(ReachAvoidState, raw["state"])


def update_working_checkpoint(state: ReachAvoidState, checkpoint: StateCheckpoint) -> StateCheckpoint:
    state.working_checkpoint = checkpoint
    return checkpoint


def update_safe_checkpoint(state: ReachAvoidState, checkpoint: StateCheckpoint) -> StateCheckpoint:
    state.safe_checkpoint = checkpoint
    return checkpoint


def update_best_checkpoint(state: ReachAvoidState, checkpoint: StateCheckpoint) -> StateCheckpoint:
    state.best_checkpoint = checkpoint
    return checkpoint


def restore_parent_working_checkpoint(
    state: ReachAvoidState,
    parent: StateCheckpoint,
    store: ExecutionCheckpointStore,
) -> StateCheckpoint:
    store.validate(parent, state.base_repository, clean_snapshot=state.clean_snapshot)
    state.working_checkpoint = parent
    return parent


def select_final_checkpoint(state: ReachAvoidState) -> StateCheckpoint:
    if state.certified_checkpoint is not None:
        return state.certified_checkpoint
    candidates = tuple(
        item for item in (state.best_checkpoint, state.safe_checkpoint)
        if item is not None and item.final_eligible
    )
    if candidates:
        return max(candidates, key=lambda item: (item.revision, item.checkpoint_id))
    return state.working_checkpoint
