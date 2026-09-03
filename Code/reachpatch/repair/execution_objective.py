from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from reachpatch.execution.worktree import diff_between
from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.evidence import DiffHunk
from reachpatch.models.execution import (
    ActiveFailure, ActiveFailureKind, ExecutableCheck, GoalContract,
    LockedCheck, ReachAvoidState,
)


class RepairMode(StrEnum):
    FIX_MECHANICAL = "FIX_MECHANICAL"
    FIX_TARGET = "FIX_TARGET"
    FIX_PRESERVATION = "FIX_PRESERVATION"
    FIX_CHALLENGE = "FIX_CHALLENGE"
    RECOVER_ROOT_CAUSE = "RECOVER_ROOT_CAUSE"


@dataclass(frozen=True, slots=True)
class SourceSlice(SerializableRecord):
    path: str
    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True, slots=True)
class MechanicalBlocker(SerializableRecord):
    file: str
    line: int
    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class RepairAttempt(SerializableRecord):
    attempt_id: str
    mechanism: str
    incremental_patch_hash: str
    result_kind: str
    failure_signature: str = ""


@dataclass(frozen=True, slots=True)
class RepairObjective(SerializableRecord):
    objective_id: str
    mode: RepairMode
    active_failure: ActiveFailure
    exact_failure_command: tuple[str, ...]
    comparator: str
    expected_observation: Any
    actual_observation: Any
    stdout: str
    stderr: str
    traceback_frames: tuple[str, ...]
    current_full_diff: str
    parent_patch_hash: str
    current_patch_hash: str
    relevant_source_slices: tuple[SourceSlice, ...]
    changed_hunks: tuple[DiffHunk, ...]
    dynamic_failure_graph: Any
    locked_checks: tuple[ExecutableCheck, ...]
    preservation_checks: tuple[ExecutableCheck, ...]
    mechanical_blockers: tuple[MechanicalBlocker, ...]
    previous_attempts: tuple[RepairAttempt, ...]
    forbidden_repeated_mechanisms: tuple[str, ...]

    @property
    def objective_kind(self) -> str:
        return self.mode.value


@dataclass(frozen=True, slots=True)
class InitialPatchObjective(SerializableRecord):
    objective_id: str
    goal_contracts: tuple[GoalContract, ...]
    public_context: tuple[dict[str, Any], ...]
    current_full_diff: str
    current_patch_hash: str
    mode: str = "INITIAL_PATCH"

    @property
    def objective_kind(self) -> str:
        return "INITIAL_PATCH"


def _source_slices(
    tree: Path,
    active_failure: ActiveFailure,
    changed_hunks: tuple[DiffHunk, ...],
    *,
    limit: int = 12,
) -> tuple[SourceSlice, ...]:
    candidates: list[tuple[int, str, int, int]] = []
    for hunk in changed_hunks:
        candidates.append((0, hunk.path, max(1, hunk.new_start), max(1, hunk.new_start + max(1, hunk.new_count) - 1)))
    for index, frame in enumerate(active_failure.traceback_frames):
        match = re.search(r'File ["\']([^"\']+)["\'], line (\d+)', frame)
        if not match:
            continue
        raw_path, line = match.group(1), int(match.group(2))
        try:
            path = Path(raw_path).resolve().relative_to(tree.resolve()).as_posix()
        except ValueError:
            path = raw_path.replace("\\", "/").lstrip("./")
        candidates.append((index + 1, path, line, line))
    slices: list[SourceSlice] = []
    seen: set[tuple[str, int, int]] = set()
    for _, path, start, end in sorted(candidates):
        source = tree / path
        if not source.is_file():
            continue
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        lower, upper = max(1, start - 12), min(len(lines), end + 12)
        key = (path, lower, upper)
        if key in seen:
            continue
        seen.add(key)
        slices.append(SourceSlice(
            path, lower, upper,
            "\n".join(f"{number}: {lines[number - 1]}" for number in range(lower, upper + 1)),
        ))
        if len(slices) >= limit:
            break
    return tuple(slices)


def _mechanical_blockers(state: ReachAvoidState) -> tuple[MechanicalBlocker, ...]:
    result = state.last_mechanical_result
    if result is None:
        return ()
    blockers: list[MechanicalBlocker] = []
    for finding in result.undefined_name_findings:
        if getattr(finding, "severity", "BLOCKER") != "BLOCKER":
            continue
        blockers.append(MechanicalBlocker(
            str(getattr(finding, "file", "<unknown>")),
            int(getattr(finding, "line", 0)),
            str(getattr(finding, "name", "<unknown>")),
            str(getattr(finding, "reason", "introduced undefined name")),
        ))
    for reason in result.failure_reasons:
        match = re.search(r"(?:in\s+)?([^\s:]+\.py):(\d+)", str(reason))
        file, line = (match.group(1), int(match.group(2))) if match else ("<repository>", 0)
        blocker = MechanicalBlocker(file, line, "<mechanical>", str(reason))
        if blocker not in blockers:
            blockers.append(blocker)
    return tuple(blockers)


def _attempts(state: ReachAvoidState) -> tuple[RepairAttempt, ...]:
    result: list[RepairAttempt] = []
    for raw in state.generator_session.attempt_history[-8:]:
        result.append(RepairAttempt(
            str(raw.get("attempt_id", "")),
            str(raw.get("model_mechanism", raw.get("mechanism", ""))),
            str(raw.get("incremental_patch_hash", "")),
            str(raw.get("result_kind", "")),
            str(raw.get("remaining_failure_signature", "")),
        ))
    return tuple(result)


def compile_execution_repair_objective(
    state: ReachAvoidState,
    active_failure: ActiveFailure,
    *,
    target_checks: tuple[ExecutableCheck, ...] = (),
    preservation_checks: tuple[ExecutableCheck, ...] = (),
    challenge_checks: tuple[ExecutableCheck, ...] = (),
    dynamic_failure_graph: Any = None,
) -> RepairObjective:
    mode = {
        ActiveFailureKind.MECHANICAL: RepairMode.FIX_MECHANICAL,
        ActiveFailureKind.TARGET: RepairMode.FIX_TARGET,
        ActiveFailureKind.PRESERVATION: RepairMode.FIX_PRESERVATION,
        ActiveFailureKind.CHALLENGE: RepairMode.FIX_CHALLENGE,
    }[active_failure.kind]
    if mode is not RepairMode.FIX_MECHANICAL and active_failure.same_signature_count >= 2:
        mode = RepairMode.RECOVER_ROOT_CAUSE
    if mode is not RepairMode.FIX_MECHANICAL:
        missing = []
        if not active_failure.command:
            missing.append("exact failure command")
        if not active_failure.comparator.strip():
            missing.append("comparator")
        # ``None`` is a legitimate Oracle value (for example EQUALS(None)).
        # Presence is guaranteed by the frozen ActiveFailure record and by
        # the grounded ExecutableCheck lookup below; do not treat a valid
        # ``None`` payload as a missing field.
        if missing:
            raise ValueError("execution repair objective is incomplete: " + ", ".join(missing))
    mechanical = _mechanical_blockers(state)
    if mode is RepairMode.FIX_MECHANICAL and not mechanical:
        raise ValueError("mechanical repair requires a concrete blocker with file, line, name, and reason")
    tree = Path(state.working_checkpoint.snapshot_tree)
    current = diff_between(state.clean_snapshot, tree)
    locked = tuple(item.check for item in state.locked_checks)
    attempts = _attempts(state)
    checks = (*target_checks, *preservation_checks, *challenge_checks)
    active_check = next((item for item in checks if item.check_id == active_failure.check_id), None)
    if mode is not RepairMode.FIX_MECHANICAL and active_check is None:
        raise ValueError("active failure check is absent from the executable queue")
    if mode is not RepairMode.FIX_MECHANICAL and active_check is not None:
        if not active_check.command or not str(active_check.comparator).strip():
            raise ValueError("execution repair objective requires a grounded command and comparator")
    return RepairObjective(
        objective_id=stable_id(
            "execution-repair-objective", state.instance_id,
            state.working_checkpoint.patch_hash, active_failure.failure_id,
            state.revision_count,
        ),
        mode=mode, active_failure=active_failure,
        exact_failure_command=active_failure.command,
        comparator=active_failure.comparator,
        expected_observation=active_failure.expected,
        actual_observation=active_failure.actual,
        stdout=active_failure.stdout, stderr=active_failure.stderr,
        traceback_frames=active_failure.traceback_frames,
        current_full_diff=current.canonical_diff,
        parent_patch_hash=state.working_checkpoint.patch_hash,
        current_patch_hash=current.patch_hash,
        relevant_source_slices=_source_slices(tree, active_failure, current.hunks),
        changed_hunks=current.hunks,
        dynamic_failure_graph=dynamic_failure_graph,
        locked_checks=locked,
        preservation_checks=tuple(preservation_checks),
        mechanical_blockers=mechanical,
        previous_attempts=attempts,
        forbidden_repeated_mechanisms=tuple(
            item.incremental_patch_hash for item in attempts
            if item.incremental_patch_hash and item.result_kind in {"REJECT_TRIAL", "GENERATOR_ERROR"}
        ),
    )
