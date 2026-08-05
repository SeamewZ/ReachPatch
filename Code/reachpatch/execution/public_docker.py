from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import socket
import socketserver
import subprocess
import threading
import time
from collections import OrderedDict
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any


BROKER_SOCKET_ENV = "REACHPATCH_PUBLIC_EXECUTION_BROKER"
BROKER_TOKEN_ENV = "REACHPATCH_PUBLIC_EXECUTION_TOKEN"
PUBLIC_IMAGE_ENV = "REACHPATCH_PUBLIC_EXECUTION_IMAGE"

_SAFE_ENVIRONMENT = {
    "HOME", "XDG_CACHE_HOME", "MPLCONFIGDIR", "TMPDIR",
    "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH",
    "DJANGO_SETTINGS_MODULE", "LANG", "LC_ALL",
}
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def public_swebench_image(instance_id: str) -> str:
    owner, separator, repository_issue = str(instance_id).partition("__")
    if not separator or not owner or not repository_issue:
        raise ValueError(f"invalid SWE-bench instance id: {instance_id}")
    image_case = f"{owner}_1776_{repository_issue}".replace("/", "_")
    return f"swebench/sweb.eval.x86_64.{image_case}:latest"


def _translate_argument(value: str, repository: Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            relative = path.resolve().relative_to(repository)
        except (OSError, ValueError):
            return value
        return str(Path("/testbed") / relative)
    return value


class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class PublicDockerExecutionBroker(AbstractContextManager["PublicDockerExecutionBroker"]):
    """Expose only fixed public-check execution, never the Docker daemon itself."""

    def __init__(
        self,
        *,
        socket_path: str | Path,
        image: str,
        case_tree: str | Path,
        case_run_root: str | Path,
        memory_limit: str = "8g",
        max_live_containers: int = 2,
    ) -> None:
        self.socket_path = Path(socket_path).resolve()
        self.image = str(image)
        self.case_tree = Path(case_tree).resolve()
        self.case_run_root = Path(case_run_root).resolve()
        self.memory_limit = memory_limit
        self.max_live_containers = max_live_containers
        self.token = secrets.token_hex(32)
        self._server: _ThreadingUnixServer | None = None
        self._thread: threading.Thread | None = None
        self._containers: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def available(self) -> bool:
        completed = subprocess.run(
            ("docker", "image", "inspect", self.image),
            capture_output=True, text=True, check=False, timeout=30,
        )
        return completed.returncode == 0

    def __enter__(self) -> "PublicDockerExecutionBroker":
        if not self.available():
            raise RuntimeError(f"public SWE-bench environment image is missing: {self.image}")
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        broker = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    raw = self.rfile.readline(2_000_000)
                    if not raw or len(raw) >= 2_000_000:
                        raise ValueError("invalid broker request size")
                    payload = json.loads(raw.decode("utf-8"))
                    response = broker.execute(payload)
                except Exception as exc:
                    response = {
                        "error": f"{type(exc).__name__}: {exc}",
                        "return_code": None,
                        "stdout": "",
                        "stderr": str(exc),
                        "timed_out": False,
                    }
                try:
                    self.wfile.write(
                        json.dumps(response, sort_keys=True).encode("utf-8") + b"\n"
                    )
                except (BrokenPipeError, ConnectionResetError):
                    # A bounded recovery client may time out and close the Unix
                    # socket while the broker is still cleaning up execution.
                    # This is an expected probe terminal, not repair evidence.
                    return

        self._server = _ThreadingUnixServer(str(self.socket_path), Handler)
        os.chmod(self.socket_path, 0o600)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"public-docker-{self.case_tree.name}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        with self._lock:
            containers = list(self._containers.values())
            self._containers.clear()
        for container_id in containers:
            self._stop_container(container_id)
        self.socket_path.unlink(missing_ok=True)

    def worker_environment(self) -> dict[str, str]:
        return {
            BROKER_SOCKET_ENV: str(self.socket_path),
            BROKER_TOKEN_ENV: self.token,
            PUBLIC_IMAGE_ENV: self.image,
        }

    def _validated_repository(self, raw: Any) -> Path:
        repository = Path(str(raw)).resolve()
        allowed = (
            repository == self.case_tree
            or repository.is_relative_to(self.case_run_root)
        )
        if not allowed or not repository.is_dir():
            raise ValueError("public check repository is outside this case")
        return repository

    @staticmethod
    def _safe_relative_path(raw: str) -> str | None:
        """Return a normalized repository path or reject an unsafe Git result."""

        value = str(raw).replace("\\", "/")
        path = Path(value)
        if (
            not value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.parts[0] == ".git"
        ):
            return None
        return "/".join(path.parts)

    def _repository_delta(
        self,
        repository: Path,
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
        """Compute a checkpoint delta using the public case tree's Git index.

        Checkpoint trees intentionally omit ``.git``.  The case tree still owns a
        worktree index for the exact SWE-bench base commit, so it can compare any
        checkpoint beneath the run root without scanning or copying the complete
        repository into Docker.  ``None`` requests the conservative full-copy path.
        """

        environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
        try:
            git_dir_result = subprocess.run(
                ("git", "-C", str(self.case_tree), "rev-parse", "--absolute-git-dir"),
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
                env=environment,
            )
            commit_result = subprocess.run(
                ("git", "-C", str(self.case_tree), "rev-parse", "HEAD"),
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
                env=environment,
            )
            if git_dir_result.returncode != 0 or commit_result.returncode != 0:
                return None
            git_dir = git_dir_result.stdout.strip()
            base_commit = commit_result.stdout.strip()
            if not git_dir or not base_commit:
                return None

            common = (
                "git", f"--git-dir={git_dir}", f"--work-tree={repository}"
            )
            diff_result = subprocess.run(
                (*common, "diff", "--name-status", "-z", "--no-renames", "HEAD", "--"),
                capture_output=True,
                check=False,
                timeout=30,
                env=environment,
            )
            untracked_result = subprocess.run(
                (*common, "ls-files", "--others", "--exclude-standard", "-z"),
                capture_output=True,
                check=False,
                timeout=30,
                env=environment,
            )
            if diff_result.returncode != 0 or untracked_result.returncode != 0:
                return None

            fields = diff_result.stdout.decode("utf-8", "surrogateescape").split("\0")
            fields = fields[:-1] if fields and fields[-1] == "" else fields
            if len(fields) % 2:
                return None
            copied: set[str] = set()
            deleted: set[str] = set()
            for index in range(0, len(fields), 2):
                status = fields[index]
                relative = self._safe_relative_path(fields[index + 1])
                if relative is None:
                    return None
                if status.startswith("D"):
                    deleted.add(relative)
                else:
                    copied.add(relative)

            untracked = untracked_result.stdout.decode(
                "utf-8", "surrogateescape"
            ).split("\0")
            for raw in untracked:
                if not raw:
                    continue
                relative = self._safe_relative_path(raw)
                if relative is None:
                    return None
                copied.add(relative)
            copied.difference_update(deleted)
            if any(not (repository / path).exists() for path in copied):
                return None
            return base_commit, tuple(sorted(copied)), tuple(sorted(deleted))
        except (OSError, subprocess.SubprocessError, UnicodeError):
            return None

    def _sync_repository_source(self, container_id: str, repository: Path) -> None:
        delta = self._repository_delta(repository)
        if delta is not None:
            base_commit, copied, deleted = delta
            container_commit = subprocess.run(
                ("docker", "exec", container_id, "git", "-C", "/testbed", "rev-parse", "HEAD"),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if (
                container_commit.returncode == 0
                and container_commit.stdout.strip() == base_commit
            ):
                commands: list[str] = []
                if copied:
                    quoted = " ".join(shlex.quote(path) for path in copied)
                    commands.append(
                        "tar -C /reachpatch-source -cf - -- "
                        f"{quoted} | tar -C /testbed -xf -"
                    )
                if deleted:
                    quoted = " ".join(
                        shlex.quote(f"/testbed/{path}") for path in deleted
                    )
                    commands.append(f"rm -f -- {quoted}")
                if not commands:
                    return
                synchronized = subprocess.run(
                    ("docker", "exec", container_id, "bash", "-lc", " && ".join(commands)),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                )
                if synchronized.returncode == 0:
                    return

        synchronized = subprocess.run(
            (
                "docker", "exec", container_id, "bash", "-lc",
                "tar -C /reachpatch-source --exclude=.git --exclude=./.git "
                "-cf - . | tar -C /testbed -xf -",
            ),
            capture_output=True, text=True, check=False, timeout=180,
        )
        if synchronized.returncode != 0:
            raise RuntimeError(
                synchronized.stderr.strip() or "failed to synchronize public source tree"
            )

    def _start_container(self, repository: Path) -> str:
        command = [
            "docker", "run", "--detach", "--rm", "--network", "none",
            "--memory", self.memory_limit, "--pids-limit", "1024",
            "--tmpfs", "/testbed/.git:rw,nosuid,nodev,noexec,size=64k",
            "--volume", f"{repository}:/reachpatch-source:ro",
            "--volume", f"{self.case_run_root}:{self.case_run_root}:rw",
            self.image,
            "bash", "-lc", "while :; do sleep 3600; done",
        ]
        started = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=60,
        )
        if started.returncode != 0:
            raise RuntimeError(started.stderr.strip() or "failed to start public check container")
        container_id = started.stdout.strip()
        try:
            self._sync_repository_source(container_id, repository)
        except Exception:
            self._stop_container(container_id)
            raise
        return container_id

    @staticmethod
    def _stop_container(container_id: str) -> None:
        try:
            subprocess.run(
                ("docker", "stop", "--time", "1", container_id),
                capture_output=True, text=True, check=False, timeout=20,
            )
        except subprocess.TimeoutExpired:
            # A public check may leave a stuck process behind.  Cleanup must be
            # best-effort so one dead container cannot abort generation
            # preflight or hide the actual case result.
            try:
                subprocess.run(
                    ("docker", "kill", container_id),
                    capture_output=True, text=True, check=False, timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    def _container(self, repository: Path) -> str:
        key = str(repository)
        with self._lock:
            existing = self._containers.pop(key, None)
            if existing is not None:
                self._containers[key] = existing
                return existing
            container_id = self._start_container(repository)
            self._containers[key] = container_id
            stale = None
            if len(self._containers) > self.max_live_containers:
                _, stale = self._containers.popitem(last=False)
        if stale is not None:
            self._stop_container(stale)
        return container_id

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not secrets.compare_digest(str(payload.get("token", "")), self.token):
            raise PermissionError("invalid public execution broker token")
        repository = self._validated_repository(payload.get("repository"))
        raw_command = payload.get("command")
        if not isinstance(raw_command, list) or not raw_command or not all(
            isinstance(item, str) and "\0" not in item for item in raw_command
        ):
            raise ValueError("invalid public check command")
        executable = Path(raw_command[0]).name
        if not (
            executable.startswith("python")
            or executable in {"pytest", "sphinx-build"}
        ):
            raise ValueError(f"unsupported public check executable: {executable}")
        command = [_translate_argument(item, repository) for item in raw_command]
        if executable.startswith("python"):
            command[0] = "python"

        environment: dict[str, str] = {}
        for key, value in dict(payload.get("environment") or {}).items():
            if key not in _SAFE_ENVIRONMENT or not _ENVIRONMENT_NAME.fullmatch(key):
                continue
            text = str(value)
            if key == "PYTHONPATH":
                entries = [
                    _translate_argument(item, repository)
                    for item in text.split(os.pathsep)
                    if item and (
                        Path(item).resolve() == repository
                        or Path(item).resolve().is_relative_to(repository)
                        or Path(item).resolve().is_relative_to(self.case_run_root)
                    )
                ]
                text = os.pathsep.join(entries)
            environment[key] = text
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONHASHSEED"] = "0"
        timeout = max(1.0, min(float(payload.get("timeout", 120.0)), 900.0))
        shell_command = "cd /testbed && exec env " + " ".join(
            [
                *(f"{key}={shlex.quote(value)}" for key, value in sorted(environment.items())),
                *(shlex.quote(item) for item in command),
            ]
        )
        container_id = self._container(repository)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                ("docker", "exec", container_id, "bash", "-lc", shell_command),
                capture_output=True, text=True, check=False, timeout=timeout,
            )
            return {
                "return_code": completed.returncode,
                "stdout": completed.stdout[-30000:],
                "stderr": completed.stderr[-30000:],
                "duration_seconds": time.monotonic() - started,
                "timed_out": False,
                "image": self.image,
            }
        except subprocess.TimeoutExpired as exc:
            with self._lock:
                self._containers.pop(str(repository), None)
            self._stop_container(container_id)
            return {
                "return_code": None,
                "stdout": str(exc.stdout or "")[-30000:],
                "stderr": str(exc.stderr or "")[-30000:],
                "duration_seconds": time.monotonic() - started,
                "timed_out": True,
                "image": self.image,
            }


def run_via_public_execution_broker(
    *,
    repository: Path,
    command: tuple[str, ...],
    environment: dict[str, str],
    timeout: float,
) -> dict[str, Any] | None:
    socket_path = os.environ.get(BROKER_SOCKET_ENV)
    token = os.environ.get(BROKER_TOKEN_ENV)
    if not socket_path or not token:
        return None
    payload = {
        "token": token,
        "repository": str(repository),
        "command": list(command),
        "environment": {
            key: value for key, value in environment.items()
            if key in _SAFE_ENVIRONMENT
        },
        "timeout": timeout,
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout + 60.0)
        client.connect(socket_path)
        client.sendall(json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n")
        response = b""
        while not response.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            response += chunk
            if len(response) > 2_000_000:
                raise RuntimeError("public execution broker response is too large")
    decoded = json.loads(response.decode("utf-8"))
    if decoded.get("error"):
        raise OSError(str(decoded["error"]))
    return decoded
