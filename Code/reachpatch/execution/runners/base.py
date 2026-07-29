from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable, Protocol

from reachpatch.execution.models import (
    CheckExecution,
    CheckRole,
    CheckStatus,
    EnvironmentHealth,
    EnvironmentHealthStatus,
    EnvironmentPreparation,
    ExecutableCheck,
    NormalizedSelector,
)
from reachpatch.execution.public_docker import run_via_public_execution_broker
from reachpatch.models.base import content_hash, stable_id


class ProjectRunner(Protocol):
    def prepare_environment(self, check_id: str) -> EnvironmentPreparation: ...
    def normalize_selector(self, selector: str) -> NormalizedSelector: ...
    def health_check(self, check: ExecutableCheck) -> EnvironmentHealth: ...
    def compile_visible_checks(
        self,
        selectors: Iterable[str],
        *,
        role: CheckRole = CheckRole.EXPLORATION,
        authority: str = "PUBLIC",
    ) -> tuple[ExecutableCheck, ...]: ...
    def run_check(
        self,
        check: ExecutableCheck,
        *,
        repository: str | Path | None = None,
        tree_hash: str | None = None,
        repeats: int = 2,
    ) -> CheckExecution: ...
    def classify_infrastructure_failure(
        self, stdout: str, stderr: str, return_code: int | None,
    ) -> str | None: ...


_TRACEBACK_FRAME = re.compile(
    r'File ["\'](?P<path>[^"\']+)["\'], line (?P<line>\d+), in (?P<symbol>[^\n]+)'
)
_PYTEST_LOCATION = re.compile(
    r"(?m)^(?P<path>(?:[A-Za-z]:)?[^\s:]+\.py):(?P<line>\d+):"
)

_DETERMINISTIC_ENVIRONMENT = {
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class BaseProjectRunner:
    """Run public project checks with stable, isolated process semantics."""

    name = "python"
    package_names: tuple[str, ...] = ()

    def __init__(
        self,
        repository: str | Path,
        *,
        artifact_root: str | Path,
        base_commit: str = "",
        python_executable: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.repository = Path(repository).resolve()
        self.artifact_root = Path(artifact_root).resolve()
        self.base_commit = base_commit
        self.python_executable = python_executable or sys.executable
        self.environment_overrides = dict(environment or {})
        self.run_root = self.artifact_root / "project-check-runs"
        self.cache_root = self.artifact_root / "baseline-check-cache"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)

    @property
    def environment_hash(self) -> str:
        return content_hash({
            "runner": self.name,
            "python": self.python_executable,
            "version": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "deterministic_environment": _DETERMINISTIC_ENVIRONMENT,
            "overrides": self.environment_overrides,
        })

    @staticmethod
    def _directory_writable(path: Path) -> bool:
        try:
            mode = path.stat().st_mode
        except OSError:
            return False
        return bool(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))

    def prepare_environment(self, check_id: str) -> EnvironmentPreparation:
        try:
            run_directory = Path(tempfile.mkdtemp(
                prefix=f"{self.name}-{check_id[:10]}-", dir=self.run_root,
            ))
            paths = {
                "HOME": run_directory / "home",
                "XDG_CACHE_HOME": run_directory / "xdg-cache",
                "MPLCONFIGDIR": run_directory / "matplotlib",
                "TMPDIR": run_directory / "tmp",
            }
            for path in paths.values():
                path.mkdir(parents=True, exist_ok=False)
                if not self._directory_writable(path):
                    raise PermissionError(f"isolated directory is not writable: {path}")
            environment = {
                key: str(path) for key, path in paths.items()
            }
            environment.update(_DETERMINISTIC_ENVIRONMENT)
            environment.update(self.environment_overrides)
            for key in ("HOME", "XDG_CACHE_HOME", "MPLCONFIGDIR", "TMPDIR"):
                configured = Path(environment[key])
                if not configured.is_dir() or not self._directory_writable(configured):
                    raise PermissionError(f"{key} is not a writable directory: {configured}")
            return EnvironmentPreparation(
                preparation_id=stable_id(
                    "environment-preparation", check_id, str(run_directory),
                    self.environment_hash,
                ),
                status=EnvironmentHealthStatus.HEALTHY,
                run_directory=str(run_directory),
                environment=environment,
                environment_hash=self.environment_hash,
            )
        except OSError as exc:
            return EnvironmentPreparation(
                preparation_id=stable_id(
                    "environment-preparation", check_id, type(exc).__name__, str(exc),
                ),
                status=EnvironmentHealthStatus.UNSUPPORTED_RUNTIME,
                run_directory="",
                environment={},
                environment_hash=self.environment_hash,
                detail=f"{type(exc).__name__}: {exc}",
            )

    def normalize_selector(self, selector: str) -> NormalizedSelector:
        original = str(selector).strip()
        if not original:
            return NormalizedSelector(original, "", False, "empty selector")
        path_text, separator, suffix = original.partition("::")
        path = Path(path_text)
        if path.is_absolute():
            try:
                path_text = str(path.resolve().relative_to(self.repository)).replace(
                    os.sep, "/"
                )
            except ValueError:
                return NormalizedSelector(
                    original, original, False, "selector is outside repository",
                )
        normalized = path_text.replace("\\", "/")
        candidate = self.repository / normalized
        if not candidate.is_file():
            return NormalizedSelector(
                original, normalized, False, "selector path does not exist",
            )
        if separator:
            normalized += f"::{suffix}"
        return NormalizedSelector(original, normalized, True)

    def command_for_selector(self, selector: str) -> tuple[str, ...]:
        return (self.python_executable, "-m", "pytest", "-q", selector)

    def compile_visible_checks(
        self,
        selectors: Iterable[str],
        *,
        role: CheckRole = CheckRole.EXPLORATION,
        authority: str = "PUBLIC",
    ) -> tuple[ExecutableCheck, ...]:
        checks: list[ExecutableCheck] = []
        seen: set[str] = set()
        for selector in selectors:
            normalized = self.normalize_selector(str(selector))
            identity_selector = normalized.normalized or normalized.original
            if identity_selector in seen:
                continue
            seen.add(identity_selector)
            command = self.command_for_selector(identity_selector) if normalized.valid else ()
            check_id = stable_id(
                "executable-check", self.name, identity_selector, role.value, authority,
            )
            checks.append(ExecutableCheck(
                check_id=check_id,
                role=role,
                authority=authority,
                command=command,
                cwd=str(self.repository),
                environment={},
                timeout_seconds=120.0,
                source_evidence_ids=(f"selector:{normalized.original}",),
                target_requirement_ids=(),
                temporary_artifact_paths=(),
                selector=identity_selector if normalized.valid else normalized.original,
            ))
        return tuple(checks)

    def compile_command_checks(
        self,
        commands: Iterable[Iterable[str]],
        *,
        role: CheckRole = CheckRole.EXPLORATION,
        authority: str = "PUBLIC_EXPLICIT_COMMAND",
    ) -> tuple[ExecutableCheck, ...]:
        checks = []
        for raw in commands:
            command = tuple(map(str, raw))
            if not command:
                continue
            check_id = stable_id(
                "executable-command-check", self.name, command, role.value, authority,
            )
            checks.append(ExecutableCheck(
                check_id=check_id,
                role=role,
                authority=authority,
                command=command,
                cwd=str(self.repository),
                environment={},
                timeout_seconds=120.0,
                source_evidence_ids=("explicit-public-command",),
                target_requirement_ids=(),
                temporary_artifact_paths=(),
                selector=" ".join(command),
            ))
        return tuple(checks)

    def classify_infrastructure_failure(
        self, stdout: str, stderr: str, return_code: int | None,
    ) -> str | None:
        diagnostic = f"{stdout}\n{stderr}".lower()
        if any(marker in diagnostic for marker in (
            "modulenotfounderror", "no module named", "command not found",
            "importerror while loading conftest", "error loading plugin",
            "sklearn.__check_build._check_build",
            "cannot import name '_c_internal_utils'",
            "cannot import name 'testdir' from '_pytest.pytester'",
        )):
            return EnvironmentHealthStatus.DEPENDENCY_MISSING.value
        if any(marker in diagnostic for marker in (
            "collected 0 items", "no tests ran", "not found:",
            "ran 0 tests", "does not match any test", "unknown test",
            "invalid test label",
        )):
            return EnvironmentHealthStatus.INVALID_SELECTOR.value
        if any(marker in diagnostic for marker in (
            "could not connect", "connection refused", "temporary failure in name resolution",
            "external service", "database is unavailable", "network is unreachable",
            "database backend is required",
        )):
            return EnvironmentHealthStatus.EXTERNAL_SERVICE_REQUIRED.value
        if any(marker in diagnostic for marker in (
            "permission denied", "read-only file system", "no space left on device",
            "could not create", "failed to create process",
            "settings are not configured",
            "requested setting ",
            "appregistrynotready",
            "apps aren't loaded yet",
            "cannot import name 'mapping' from 'collections'",
            "remotetestresult' object has no attribute 'addduration'",
            "was removed in the numpy 2.0 release",
        )):
            return EnvironmentHealthStatus.UNSUPPORTED_RUNTIME.value
        if return_code in {4, 5}:
            return EnvironmentHealthStatus.COLLECTION_BROKEN.value
        return None

    def _first_project_frame(
        self, stdout: str, stderr: str, repository: Path,
    ) -> dict[str, object] | None:
        diagnostic = f"{stdout}\n{stderr}"
        frames: list[dict[str, object]] = []

        def project_relative(raw: str) -> Path | None:
            path = Path(raw)
            if path.is_absolute():
                try:
                    container_relative = path.relative_to("/testbed")
                except ValueError:
                    pass
                else:
                    path = repository / container_relative
            else:
                path = repository / path
            try:
                return path.resolve().relative_to(repository.resolve())
            except (OSError, ValueError):
                return None

        for match in _TRACEBACK_FRAME.finditer(diagnostic):
            relative = project_relative(match.group("path"))
            if relative is None:
                continue
            frames.append({
                "relative_path": str(relative).replace(os.sep, "/"),
                "line": int(match.group("line")),
                "symbol": match.group("symbol").strip(),
            })
        if not frames:
            for match in _PYTEST_LOCATION.finditer(diagnostic):
                relative = project_relative(match.group("path"))
                if relative is None:
                    continue
                frames.append({
                    "relative_path": str(relative).replace(os.sep, "/"),
                    "line": int(match.group("line")),
                    "symbol": "<failure>",
                })
        project = [
            frame for frame in frames
            if "tests" not in Path(str(frame["relative_path"])).parts
            and not Path(str(frame["relative_path"])).name.startswith("test_")
        ]
        return (project or frames)[-1] if (project or frames) else None

    @staticmethod
    def _failure_signature(
        status: CheckStatus, return_code: int | None, stdout: str, stderr: str,
    ) -> str | None:
        if status == CheckStatus.PASS:
            return None
        diagnostic = f"{stdout}\n{stderr}"[-12000:]
        normalized = re.sub(r"(?<=, line )\d+", "<line>", diagnostic)
        normalized = re.sub(r"(?m)(\.py):\d+(?=[:\s])", r"\1:<line>", normalized)
        normalized = re.sub(r"(?:[A-Za-z]:)?[/\\][^\s:\n]+", "<path>", normalized)
        normalized = re.sub(r"\b\d+(?:\.\d+)?s\b", "<duration>", normalized)
        return content_hash({
            "status": status.value,
            "return_code": return_code,
            "diagnostic": normalized,
        })

    def _execute_once(
        self,
        check: ExecutableCheck,
        repository: Path,
    ) -> tuple[CheckStatus, int | None, str, str, float, str | None, dict | None]:
        if not check.command:
            return (
                CheckStatus.INVALID_SELECTOR, None, "", "invalid or unresolved selector",
                0.0, content_hash("invalid-selector"), None,
            )
        preparation = self.prepare_environment(check.check_id)
        if preparation.status != EnvironmentHealthStatus.HEALTHY:
            return (
                CheckStatus.INVALID_ENVIRONMENT, None, "", preparation.detail,
                0.0, content_hash(preparation.detail), None,
            )
        environment = {
            **os.environ,
            **preparation.environment,
            **check.environment,
        }
        configured_pythonpath = environment.get("PYTHONPATH", "")
        pythonpath_entries = [
            entry for entry in configured_pythonpath.split(os.pathsep)
            if entry and Path(entry).resolve() != self.repository
        ]
        environment["PYTHONPATH"] = os.pathsep.join((
            str(repository), *pythonpath_entries,
        ))
        started = time.monotonic()
        try:
            broker_execution = run_via_public_execution_broker(
                repository=repository,
                command=check.command,
                environment=environment,
                timeout=check.timeout_seconds,
            )
            if broker_execution is not None and broker_execution.get("timed_out"):
                stdout = str(broker_execution.get("stdout") or "")[-30000:]
                stderr = str(broker_execution.get("stderr") or "")[-30000:]
                duration = float(
                    broker_execution.get("duration_seconds")
                    or (time.monotonic() - started)
                )
                return (
                    CheckStatus.TIMEOUT, None, stdout, stderr, duration,
                    self._failure_signature(
                        CheckStatus.TIMEOUT, None, stdout, stderr,
                    ),
                    self._first_project_frame(stdout, stderr, repository),
                )
            if broker_execution is None:
                process = subprocess.run(
                    check.command,
                    cwd=repository,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=check.timeout_seconds,
                    check=False,
                    shell=False,
                )
                return_code = process.returncode
                process_stdout = process.stdout
                process_stderr = process.stderr
            else:
                return_code = broker_execution.get("return_code")
                process_stdout = str(broker_execution.get("stdout") or "")
                process_stderr = str(broker_execution.get("stderr") or "")
            duration = time.monotonic() - started
            stdout = process_stdout[-30000:]
            stderr = process_stderr[-30000:]
            infrastructure = self.classify_infrastructure_failure(
                stdout, stderr, return_code,
            )
            if infrastructure == EnvironmentHealthStatus.INVALID_SELECTOR.value:
                status = CheckStatus.INVALID_SELECTOR
            elif infrastructure is not None:
                status = CheckStatus.INVALID_ENVIRONMENT
            else:
                status = CheckStatus.PASS if return_code == 0 else CheckStatus.FAIL
            signature = self._failure_signature(
                status, return_code, stdout, stderr,
            )
            return (
                status, return_code, stdout, stderr, duration, signature,
                self._first_project_frame(stdout, stderr, repository),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "")[-30000:]
            stderr = str(exc.stderr or "")[-30000:]
            duration = time.monotonic() - started
            return (
                CheckStatus.TIMEOUT, None, stdout, stderr, duration,
                self._failure_signature(CheckStatus.TIMEOUT, None, stdout, stderr),
                self._first_project_frame(stdout, stderr, repository),
            )
        except OSError as exc:
            duration = time.monotonic() - started
            diagnostic = f"{type(exc).__name__}: {exc}"
            return (
                CheckStatus.INVALID_ENVIRONMENT, None, "", diagnostic, duration,
                self._failure_signature(
                    CheckStatus.INVALID_ENVIRONMENT, None, "", diagnostic,
                ),
                None,
            )

    @staticmethod
    def _tree_hash(repository: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(repository.rglob("*")):
            if not path.is_file() or any(
                part in {".git", ".reachpatch", "__pycache__"}
                for part in path.parts
            ):
                continue
            digest.update(str(path.relative_to(repository)).encode("utf-8"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                continue
        return digest.hexdigest()

    def run_check(
        self,
        check: ExecutableCheck,
        *,
        repository: str | Path | None = None,
        tree_hash: str | None = None,
        repeats: int = 2,
    ) -> CheckExecution:
        root = Path(repository or check.cwd).resolve()
        if repeats < 1:
            raise ValueError("repeats must be positive")
        observations = [self._execute_once(check, root) for _ in range(repeats)]
        signatures = {
            (item[0], item[1], item[5]) for item in observations
        }
        stable = len(signatures) == 1
        selected = observations[0]
        status = selected[0] if stable else CheckStatus.FLAKY
        source_hash = tree_hash or self._tree_hash(root)
        execution_id = stable_id(
            "check-execution", check.check_id, source_hash,
            [(item[0].value, item[1], item[5]) for item in observations],
        )
        return CheckExecution(
            execution_id=execution_id,
            check_id=check.check_id,
            tree_hash=source_hash,
            status=status,
            return_code=selected[1],
            stdout=selected[2],
            stderr=selected[3],
            duration_seconds=sum(item[4] for item in observations),
            stable=stable,
            failure_signature=selected[5],
            first_project_frame=selected[6],
        )

    def _baseline_cache_path(self, check: ExecutableCheck) -> Path:
        key = content_hash({
            "base_commit": self.base_commit,
            "environment_hash": self.environment_hash,
            "check_id": check.check_id,
        })
        return self.cache_root / f"{key}.json"

    def run_baseline_check(self, check: ExecutableCheck) -> CheckExecution:
        path = self._baseline_cache_path(check)
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return CheckExecution(
                    execution_id=str(raw["execution_id"]),
                    check_id=str(raw["check_id"]),
                    tree_hash=str(raw["tree_hash"]),
                    status=CheckStatus(str(raw["status"])),
                    return_code=raw.get("return_code"),
                    stdout=str(raw.get("stdout", "")),
                    stderr=str(raw.get("stderr", "")),
                    duration_seconds=float(raw.get("duration_seconds", 0.0)),
                    stable=bool(raw.get("stable")),
                    failure_signature=raw.get("failure_signature"),
                    first_project_frame=raw.get("first_project_frame"),
                )
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                pass
        execution = self.run_check(check, repository=self.repository)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(execution.to_dict(), sort_keys=True), encoding="utf-8",
        )
        os.replace(temporary, path)
        return execution

    def health_check(self, check: ExecutableCheck) -> EnvironmentHealth:
        execution = self.run_baseline_check(check)
        classified = self.classify_infrastructure_failure(
            execution.stdout, execution.stderr, execution.return_code,
        )
        if classified is not None:
            try:
                status = EnvironmentHealthStatus(classified)
            except ValueError:
                status = EnvironmentHealthStatus.UNSUPPORTED_RUNTIME
            detail = "baseline environment cannot execute the required public check"
        elif execution.status in {CheckStatus.PASS, CheckStatus.FAIL} and execution.stable:
            status = EnvironmentHealthStatus.HEALTHY
            detail = "baseline check executed stably"
        elif execution.status == CheckStatus.INVALID_SELECTOR:
            status = EnvironmentHealthStatus.INVALID_SELECTOR
            detail = "public selector is invalid for the base checkout"
        elif execution.status == CheckStatus.UNSUPPORTED:
            status = EnvironmentHealthStatus.UNSUPPORTED_RUNTIME
            detail = "project runner does not support this check"
        else:
            try:
                status = EnvironmentHealthStatus(
                    classified or EnvironmentHealthStatus.UNSUPPORTED_RUNTIME.value
                )
            except ValueError:
                status = EnvironmentHealthStatus.UNSUPPORTED_RUNTIME
            detail = "baseline environment cannot execute the required public check"
        return EnvironmentHealth(
            health_id=stable_id(
                "environment-health", check.check_id, execution.execution_id,
                status.value,
            ),
            status=status,
            detail=detail,
            execution=execution,
        )
