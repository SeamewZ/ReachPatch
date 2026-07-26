from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import time
from pathlib import Path
from typing import Iterable

from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import stable_id
from reachpatch.models.controller import MechanicalCheck
from reachpatch.models.enums import OutcomeStatus


def _source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for current, directories, names in os.walk(root):
        directories[:] = sorted(
            name for name in directories
            if name not in {".git", ".venv", "venv", "__pycache__", ".reachpatch"}
        )
        for name in sorted(names):
            if not name.endswith((".py", ".pyi", ".toml", ".cfg", ".ini")):
                continue
            path = Path(current) / name
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _syntax_check(root: Path, actual_diff: ActualDiff, source_hash: str) -> MechanicalCheck:
    started = time.monotonic()
    errors: list[str] = []
    checked: list[str] = []
    python_files = {
        relative
        for relative in actual_diff.changed_files
        if relative.endswith((".py", ".pyi")) and relative not in actual_diff.deleted_files
    }
    for relative in sorted(python_files):
        path = root / relative
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=relative, type_comments=True)
            checked.append(relative)
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{relative}: {exc}")
    status = OutcomeStatus.PASS if not errors and actual_diff.applies else OutcomeStatus.FAIL
    return MechanicalCheck(
        check_id=stable_id("mechanical", "syntax", actual_diff.diff_id, source_hash),
        kind="SYNTAX",
        command=("ast.parse", *sorted(python_files)),
        status=status,
        return_code=0 if status == OutcomeStatus.PASS else 1,
        stdout="\n".join(checked),
        stderr="\n".join(errors),
        duration_seconds=time.monotonic() - started,
        source_hash=source_hash,
    )


def _scope_check(actual_diff: ActualDiff, source_hash: str) -> MechanicalCheck:
    forbidden = actual_diff.forbidden_paths + actual_diff.oracle_contamination_paths
    status = OutcomeStatus.PASS if not forbidden else OutcomeStatus.FAIL
    return MechanicalCheck(
        check_id=stable_id("mechanical", "scope", actual_diff.diff_id, forbidden),
        kind="SCOPE_AND_ORACLE_INTEGRITY",
        command=("internal:scope-check",),
        status=status,
        return_code=0 if status == OutcomeStatus.PASS else 1,
        stdout="",
        stderr="\n".join(forbidden),
        duration_seconds=0.0,
        source_hash=source_hash,
    )


def _command_check(root: Path, command: tuple[str, ...], source_hash: str, timeout: float) -> MechanicalCheck:
    started = time.monotonic()
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    try:
        process = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        status = OutcomeStatus.PASS if process.returncode == 0 else OutcomeStatus.FAIL
        return_code = process.returncode
        stdout = process.stdout
        stderr = process.stderr
    except subprocess.TimeoutExpired as exc:
        status = OutcomeStatus.UNKNOWN
        return_code = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
    return MechanicalCheck(
        check_id=stable_id("mechanical", command, source_hash, status),
        kind="COMMAND",
        command=command,
        status=status,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=time.monotonic() - started,
        source_hash=source_hash,
    )


def run_mechanical_checks(
    trial_root: str | Path,
    actual_diff: ActualDiff,
    *,
    commands: Iterable[Iterable[str]] = (),
    timeout_seconds: float = 300.0,
) -> tuple[MechanicalCheck, ...]:
    root = Path(trial_root).resolve()
    source_hash = _source_hash(root)
    checks = [_syntax_check(root, actual_diff, source_hash), _scope_check(actual_diff, source_hash)]
    checks.extend(
        _command_check(root, tuple(command), source_hash, timeout_seconds)
        for command in commands
    )
    return tuple(checks)


def mechanical_pass(checks: Iterable[MechanicalCheck]) -> bool:
    selected = tuple(checks)
    return bool(selected) and all(item.status == OutcomeStatus.PASS for item in selected)
