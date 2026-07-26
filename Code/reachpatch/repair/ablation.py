from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from reachpatch.execution.reconcile import ActualDiff, reconcile_actual_diff
from reachpatch.execution.worktree import WorktreeManager
from reachpatch.models.base import SerializableRecord, stable_id


@dataclass(frozen=True, slots=True)
class AblationValidation(SerializableRecord):
    graph_reached: bool
    safe: bool
    closure_closed: bool
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AblationAttempt(SerializableRecord):
    group_id: str
    relative_path: str
    base_span: tuple[int, int]
    incumbent_span: tuple[int, int]
    decision: str
    receipt_id: str
    candidate_diff_hash: str
    validation: AblationValidation


@dataclass(frozen=True, slots=True)
class EditRetentionAblation(SerializableRecord):
    ablation_id: str
    source_checkpoint_id: str
    final_checkpoint_id: str
    final_snapshot_tree: str
    removed_group_ids: tuple[str, ...]
    retained_group_ids: tuple[str, ...]
    attempts: tuple[AblationAttempt, ...]
    final_diff: ActualDiff


ValidationCallback = Callable[[Path, Path, ActualDiff], AblationValidation]


def _files(root: Path) -> dict[str, Path]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in {".git", ".venv", "venv", "__pycache__"} for part in path.parts)
    }


def _groups(base: Path, incumbent: Path) -> list[tuple[str, int, int, int, int]]:
    base_files = _files(base)
    incumbent_files = _files(incumbent)
    groups = []
    for relative in sorted(base_files.keys() | incumbent_files.keys()):
        before = (
            base_files[relative].read_text(encoding="utf-8").splitlines(keepends=True)
            if relative in base_files else []
        )
        after = (
            incumbent_files[relative].read_text(encoding="utf-8").splitlines(keepends=True)
            if relative in incumbent_files else []
        )
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, before, after, autojunk=False
        ).get_opcodes():
            if tag != "equal":
                groups.append((relative, i1, i2, j1, j2))
    return groups


def _restore_group(
    base: Path,
    trial: Path,
    group: tuple[str, int, int, int, int],
) -> None:
    relative, i1, i2, j1, j2 = group
    base_path = base / relative
    trial_path = trial / relative
    before = (
        base_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if base_path.is_file() else []
    )
    current = (
        trial_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if trial_path.is_file() else []
    )
    restored = current[:j1] + before[i1:i2] + current[j2:]
    if restored:
        trial_path.parent.mkdir(parents=True, exist_ok=True)
        trial_path.write_text("".join(restored), encoding="utf-8")
    elif trial_path.exists():
        trial_path.unlink()
        parent = trial_path.parent
        while parent != trial and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def edit_retention_ablation(
    manager: WorktreeManager,
    *,
    base_tree: str | Path,
    checkpoint_id: str,
    validate: ValidationCallback,
    max_groups: int = 32,
) -> EditRetentionAblation:
    if max_groups < 1:
        raise ValueError("max_groups must be positive")
    base = Path(base_tree).resolve()
    current_checkpoint = checkpoint_id
    attempts = []
    removed = []
    retained = []
    tried_fingerprints: set[str] = set()
    for _ in range(max_groups):
        current_tree = manager.checkpoint_tree(current_checkpoint)
        candidates = _groups(base, current_tree)
        selected = None
        selected_id = None
        for group in candidates:
            group_id = stable_id("ablation-group", current_checkpoint, group)
            fingerprint = stable_id("ablation-fingerprint", group)
            if fingerprint not in tried_fingerprints:
                selected = group
                selected_id = group_id
                tried_fingerprints.add(fingerprint)
                break
        if selected is None or selected_id is None:
            break
        trial = manager.begin_trial(current_checkpoint)
        _restore_group(base, Path(trial.tree), selected)
        candidate_diff = reconcile_actual_diff(base, trial.tree)
        try:
            validation = validate(current_tree, Path(trial.tree), candidate_diff)
        except BaseException:
            manager.rollback(trial)
            raise
        if validation.graph_reached and validation.safe and validation.closure_closed:
            next_checkpoint = stable_id(
                "checkpoint", current_checkpoint, "ablation", candidate_diff.canonical_diff_hash
            )
            receipt = manager.commit(trial, next_checkpoint)
            current_checkpoint = next_checkpoint
            decision = "REMOVE"
            removed.append(selected_id)
            tried_fingerprints.clear()
        else:
            receipt = manager.rollback(trial)
            decision = "RETAIN"
            retained.append(selected_id)
        attempts.append(AblationAttempt(
            group_id=selected_id,
            relative_path=selected[0],
            base_span=(selected[1], selected[2]),
            incumbent_span=(selected[3], selected[4]),
            decision=decision,
            receipt_id=receipt.receipt_id,
            candidate_diff_hash=candidate_diff.canonical_diff_hash,
            validation=validation,
        ))
    final_tree = manager.checkpoint_tree(current_checkpoint)
    final_diff = reconcile_actual_diff(base, final_tree)
    ablation_id = stable_id(
        "edit-retention-ablation", checkpoint_id, current_checkpoint,
        removed, retained, final_diff.canonical_diff_hash,
    )
    return EditRetentionAblation(
        ablation_id=ablation_id,
        source_checkpoint_id=checkpoint_id,
        final_checkpoint_id=current_checkpoint,
        final_snapshot_tree=str(final_tree),
        removed_group_ids=tuple(removed),
        retained_group_ids=tuple(retained),
        attempts=tuple(attempts),
        final_diff=final_diff,
    )
