from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
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


def _import_check(
    root: Path,
    actual_diff: ActualDiff,
    source_hash: str,
    timeout: float,
    baseline_root: Path | None,
) -> MechanicalCheck:
    modules = []
    for relative in actual_diff.changed_files:
        path = Path(relative)
        if (
            path.suffix != ".py" or relative in actual_diff.deleted_files
            or "tests" in path.parts or path.name.startswith("test_")
        ):
            continue
        parts = list(path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if parts and all(part.isidentifier() for part in parts):
            modules.append(".".join(parts))
    started = time.monotonic()
    if not modules:
        return MechanicalCheck(
            check_id=stable_id("mechanical", "import", actual_diff.diff_id, ()),
            kind="IMPORT",
            command=("internal:no-importable-changed-module",),
            status=OutcomeStatus.PASS,
            return_code=0,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started,
            source_hash=source_hash,
        )
    code = (
        "import importlib\n"
        + "\n".join(f"importlib.import_module({module!r})" for module in sorted(set(modules)))
    )
    def execute_import(check_root: Path):
        return subprocess.run(
            (sys.executable, "-c", code), cwd=check_root,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
            capture_output=True, text=True, timeout=min(timeout, 60.0),
            check=False,
        )

    kind = "IMPORT"
    try:
        process = execute_import(root)
        status = OutcomeStatus.PASS if process.returncode == 0 else OutcomeStatus.FAIL
        return_code = process.returncode
        stdout, stderr = process.stdout, process.stderr
        if status == OutcomeStatus.FAIL and baseline_root is not None:
            baseline = execute_import(baseline_root)
            if baseline.returncode != 0:
                kind = "IMPORT_BASELINE_BLOCKED"
                status = OutcomeStatus.PASS
                return_code = 0
                stderr = (
                    "baseline import was already blocked; no confirmed import regression\n"
                    f"BASELINE:\n{baseline.stderr}\nTRIAL:\n{process.stderr}"
                )
    except subprocess.TimeoutExpired as exc:
        status = OutcomeStatus.UNKNOWN_EXECUTION
        return_code = None
        stdout, stderr = str(exc.stdout or ""), str(exc.stderr or "")
    return MechanicalCheck(
        check_id=stable_id("mechanical", "import", modules, source_hash, status),
        kind=kind,
        command=(sys.executable, "-c", code), status=status,
        return_code=return_code, stdout=stdout, stderr=stderr,
        duration_seconds=time.monotonic() - started, source_hash=source_hash,
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
    baseline_root: str | Path | None = None,
) -> tuple[MechanicalCheck, ...]:
    root = Path(trial_root).resolve()
    source_hash = _source_hash(root)
    checks = [
        _syntax_check(root, actual_diff, source_hash),
        _scope_check(actual_diff, source_hash),
        _import_check(
            root, actual_diff, source_hash, timeout_seconds,
            Path(baseline_root).resolve() if baseline_root is not None else None,
        ),
    ]
    checks.extend(
        _command_check(root, tuple(command), source_hash, timeout_seconds)
        for command in commands
    )
    return tuple(checks)


def mechanical_pass(checks: Iterable[MechanicalCheck]) -> bool:
    selected = tuple(checks)
    return bool(selected) and all(item.status == OutcomeStatus.PASS for item in selected)
