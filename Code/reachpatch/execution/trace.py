from __future__ import annotations

import os
import json
import re
import subprocess
import tempfile
import time
import shutil
from pathlib import Path

from reachpatch.models.base import stable_id
from reachpatch.models.evidence import OutcomeStatus, RunObservation, TraceBundle

from .worktree import tree_hash


_FRAME = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([A-Za-z_]\w*))?')
_TRACE_MARKER = re.compile(r"^__REACHPATCH_TRACE__=(.+)$", re.MULTILINE)
_SITECUSTOMIZE = r'''import atexit, builtins, collections, json, os, sys, threading
_root = os.path.realpath(os.environ.get("REACHPATCH_TRACE_ROOT", ""))
_output = os.environ.get("REACHPATCH_TRACE_OUTPUT", "")
try:
    _focus_paths = tuple(json.loads(os.environ.get("REACHPATCH_TRACE_FOCUS", "[]")))
except (TypeError, ValueError):
    _focus_paths = ()
_events = collections.deque(maxlen=4096)
_focus_events = collections.deque(maxlen=2048)
_first_event = None
_sequence = 0
def _reset():
    global _first_event, _sequence
    _events.clear()
    _focus_events.clear()
    _first_event = None
    _sequence = 0
builtins.__reachpatch_trace_reset__ = _reset
def _profile(
    frame, event, arg,
    _trace_root=_root,
    _path_separator=os.sep,
    _relative_path=os.path.relpath,
    _configured_focus=_focus_paths,
):
    global _first_event, _sequence
    path = frame.f_code.co_filename
    if not isinstance(path, str):
        return
    inside = _trace_root and (
        path == _trace_root
        or path.startswith(_trace_root + _path_separator)
    )
    if inside and event in {"call", "return", "exception"}:
        _sequence += 1
        value = (_sequence, path, frame.f_lineno, frame.f_code.co_name, event)
        if _first_event is None:
            _first_event = value
        relative = _relative_path(path, _trace_root).replace(_path_separator, "/")
        focused = any(
            relative == item or relative.startswith(item.rstrip("/") + "/")
            for item in _configured_focus
        )
        (_focus_events if focused else _events).append(value)
def _write():
    if _output:
        values = list(_events) + list(_focus_events)
        if _first_event is not None and _first_event not in values:
            values.append(_first_event)
        values.sort(key=lambda item: item[0])
        with open(_output, "w", encoding="utf-8") as handle:
            json.dump([item[1:] for item in values], handle)
atexit.register(_write)
sys.setprofile(_profile)
threading.setprofile(_profile)
'''


def run_trace(
    tree: Path,
    command: tuple[str, ...],
    *,
    cwd: str = ".",
    environment: tuple[tuple[str, str], ...] = (),
    timeout_seconds: float = 60.0,
    trace_enabled: bool = True,
    overlay_paths: tuple[str, ...] = (),
) -> TraceBundle:
    started = time.monotonic()
    dynamic_events = []
    configured_temp_root = os.environ.get("REACHPATCH_TRACE_TEMP_ROOT", "").strip()
    temp_root = Path(configured_temp_root).resolve() if configured_temp_root else None
    if temp_root is not None:
        temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="reachpatch-trace-", dir=temp_root,
    ) as trace_directory:
        instrumentation = Path(trace_directory)
        trace_output = instrumentation / "events.json"
        env = os.environ.copy()
        env.update(dict(environment))
        search_paths = [str(tree), env.get("PYTHONPATH", "")]
        if trace_enabled:
            (instrumentation / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
            env["REACHPATCH_TRACE_ROOT"] = str(tree.resolve())
            env["REACHPATCH_TRACE_OUTPUT"] = str(trace_output)
            env["REACHPATCH_TRACE_FOCUS"] = json.dumps(sorted(set(overlay_paths)))
            search_paths.insert(0, str(instrumentation))
        env["PYTHONPATH"] = os.pathsep.join(filter(None, search_paths))
        try:
            image = env.pop("REACHPATCH_EXECUTION_IMAGE", "").strip()
            base_commit = env.pop("REACHPATCH_EXECUTION_BASE_COMMIT", "").strip()
            backend = image or "HOST"
            if image and shutil.which("docker"):
                if not re.fullmatch(r"[0-9a-f]{40}", base_commit):
                    raise OSError(
                        "dependency-image execution requires a verified public base commit"
                    )
                instrumentation.chmod(0o777)
                overlay_manifest = instrumentation / "overlay-paths.json"
                overlay_manifest.write_text(
                    json.dumps(sorted(set(overlay_paths))), encoding="utf-8",
                )
                bootstrap = (
                    "set -e; "
                    "git -C /testbed checkout -q --detach \"$1\"; "
                    "git -C /testbed reset --hard -q \"$1\"; "
                    "python - \"$2\" <<'PY'\n"
                    "import json, pathlib, shutil, sys\n"
                    "source = pathlib.Path('/reachpatch-working')\n"
                    "target = pathlib.Path('/testbed')\n"
                    "for raw in json.loads(pathlib.Path(sys.argv[1]).read_text()):\n"
                    "    relative = pathlib.PurePosixPath(raw)\n"
                    "    if relative.is_absolute() or '..' in relative.parts:\n"
                    "        raise SystemExit(f'unsafe overlay path: {raw}')\n"
                    "    incoming = source.joinpath(*relative.parts)\n"
                    "    destination = target.joinpath(*relative.parts)\n"
                    "    if incoming.is_file():\n"
                    "        destination.parent.mkdir(parents=True, exist_ok=True)\n"
                    "        shutil.copy2(incoming, destination)\n"
                    "    elif destination.exists():\n"
                    "        destination.unlink()\n"
                    "PY\n"
                    "shift 2; cd /testbed/\"$1\"; shift; exec \"$@\""
                )
                docker_command = [
                    "docker", "run", "--rm", "--network", "none",
                    "--tmpfs", "/tmp:rw,exec,nosuid,size=256m",
                    "--workdir", "/testbed",
                    "--volume", f"{instrumentation}:/tmp/reachpatch-trace:rw",
                    "--volume", f"{tree.resolve()}:/reachpatch-working:ro",
                    "--env", "PYTHONDONTWRITEBYTECODE=1",
                ]
                if trace_enabled:
                    docker_command.extend((
                        "--env", "PYTHONPATH=/tmp/reachpatch-trace:/testbed",
                        "--env", "REACHPATCH_TRACE_ROOT=/testbed",
                        "--env", "REACHPATCH_TRACE_OUTPUT=/tmp/reachpatch-trace/events.json",
                        "--env", (
                            "REACHPATCH_TRACE_FOCUS="
                            + json.dumps(sorted(set(overlay_paths)))
                        ),
                    ))
                else:
                    docker_command.extend(("--env", "PYTHONPATH=/testbed",))
                for key, value in environment:
                    if key not in {
                        "REACHPATCH_EXECUTION_IMAGE",
                        "REACHPATCH_EXECUTION_BASE_COMMIT",
                    }:
                        docker_command.extend(("--env", f"{key}={value}"))
                docker_command.extend((
                    "--entrypoint", "/bin/bash", image,
                    "-lc", bootstrap, "reachpatch-challenge", base_commit,
                    "/tmp/reachpatch-trace/overlay-paths.json", cwd.strip("./"),
                    *command,
                ))
                result = subprocess.run(
                    docker_command,
                    cwd=tree.resolve(),
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout_seconds,
                    check=False,
                )
            else:
                result = subprocess.run(
                    command,
                    cwd=(tree / cwd).resolve(),
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout_seconds,
                    check=False,
                )
            status = OutcomeStatus.PASS if result.returncode == 0 else OutcomeStatus.FAIL
            return_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
            exception = None if result.returncode == 0 else result.stderr.splitlines()[-1:] or None
        except subprocess.TimeoutExpired as exc:
            status = OutcomeStatus.BLOCKED
            return_code = None
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
            exception = "TIMEOUT"
        except OSError as exc:
            status = OutcomeStatus.UNSUPPORTED
            return_code = None
            stdout = ""
            stderr = str(exc)
            exception = type(exc).__name__
        if trace_output.is_file():
            try:
                dynamic_events = json.loads(trace_output.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                dynamic_events = []
    combined = f"{stdout}\n{stderr}"
    lines: list[str] = []
    symbols: list[str] = []
    first_frame = None
    for raw_path, raw_line, raw_symbol, _ in dynamic_events:
        path = Path(raw_path)
        try:
            relative = (
                path.relative_to("/testbed").as_posix()
                if path.is_absolute() and path.is_relative_to("/testbed")
                else path.resolve().relative_to(tree.resolve()).as_posix()
            )
        except ValueError:
            continue
        line_id = f"{relative}:{raw_line}"
        lines.append(line_id)
        symbols.append(str(raw_symbol))
        if first_frame is None:
            first_frame = line_id
    for match in _FRAME.finditer(combined):
        path = Path(match.group(1))
        try:
            relative = (
                path.relative_to("/testbed").as_posix()
                if path.is_absolute() and path.is_relative_to("/testbed")
                else path.resolve().relative_to(tree.resolve()).as_posix()
            )
        except ValueError:
            continue
        line_id = f"{relative}:{match.group(2)}"
        lines.append(line_id)
        if match.group(3):
            symbols.append(match.group(3))
        if first_frame is None:
            first_frame = line_id
    for marker in _TRACE_MARKER.findall(combined):
        symbols.extend(item.strip() for item in marker.split(",") if item.strip())
    observation = RunObservation(
        status=status,
        return_code=return_code,
        stdout=stdout[-10000:],
        stderr=stderr[-10000:],
        duration_seconds=time.monotonic() - started,
        exception=str(exception) if exception else None,
    )
    digest = tree_hash(tree)
    trace_id = stable_id("trace", digest, command, observation, lines, symbols)
    return TraceBundle(
        trace_bundle_id=trace_id,
        tree_hash=digest,
        command=command,
        observation=observation,
        # Repeated events are meaningful for the 0/1/MANY loop and recursion
        # path classes, so retain their real execution order.
        executed_symbol_ids=tuple(symbols),
        executed_path_ids=tuple(lines),
        executed_line_ids=tuple(lines),
        first_project_frame=first_frame,
        cwd=cwd,
        environment=tuple(sorted((str(key), str(value)) for key, value in environment)),
        backend=backend,
    )
