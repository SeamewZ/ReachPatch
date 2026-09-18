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
from reachpatch.models.evidence import OutcomeStatus, RunObservation, TraceBundle, TraceEvent

from .worktree import copy_source_tree, tree_hash
from .case_budget import charged_execution


_FRAME = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([A-Za-z_]\w*))?')
_TRACE_MARKER = re.compile(r"^__REACHPATCH_TRACE__=(.+)$", re.MULTILINE)
_SITECUSTOMIZE = r'''import atexit, ast, builtins, collections, json, os, sys, threading, linecache, re
_root = os.path.realpath(os.environ.get("REACHPATCH_TRACE_ROOT", ""))
_output = os.environ.get("REACHPATCH_TRACE_OUTPUT", "")
try:
    _focus_paths = tuple(json.loads(os.environ.get("REACHPATCH_TRACE_FOCUS", "[]")))
    _focus_symbols = tuple(json.loads(os.environ.get("REACHPATCH_TRACE_SYMBOLS", "[]")))
except (TypeError, ValueError):
    _focus_paths = ()
    _focus_symbols = ()
_events = collections.deque(maxlen=4096)
_focus_events = collections.deque(maxlen=2048)
_first_event = None
_sequence = 0
_previous = {}
_branch_cache = {}
_anchor_cache = {}
_ast_cache = {}
_frame_depth = {}
def _parsed_source(path):
    if path not in _ast_cache:
        _ast_cache[path] = ast.parse(''.join(linecache.getlines(path)), filename=path)
    return _ast_cache[path]
def _source_anchor(path, line):
    if path not in _anchor_cache:
        anchors = {}
        try:
            tree = _parsed_source(path)
            def visit(node, owner, route, shape):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    owner = owner + '.' + node.name if owner else node.name
                    route = ()
                    shape = ','.join(type(part).__name__ for part in ast.walk(node) if isinstance(part, ast.stmt))
                if isinstance(node, ast.stmt) and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    anchors[node.lineno] = (owner, '/'.join(route), type(node).__name__, shape)
                for field, children in ast.iter_fields(node):
                    if isinstance(children, list):
                        for index, child in enumerate(children):
                            if isinstance(child, ast.AST): visit(child, owner, route + (field + ':' + str(index),), shape)
                    elif isinstance(children, ast.AST): visit(children, owner, route + (field,), shape)
            visit(tree, '', (), '')
        except (SyntaxError, OSError):
            anchors = {}
        _anchor_cache[path] = anchors
    return _anchor_cache[path].get(line)
def _reset():
    global _first_event, _sequence, _previous
    _events.clear()
    _focus_events.clear()
    _first_event = None
    _sequence = 0
    _previous.clear()
builtins.__reachpatch_trace_reset__ = _reset
def _safe_locals(frame):
    values = {}
    for key, value in list(frame.f_locals.items())[-24:]:
        if str(key).startswith('__'):
            continue
        try:
            values[str(key)] = {'type': type(value).__name__,
                'len': len(value) if type(value) in (str, bytes, list, tuple, dict, set, frozenset) else None,
                'none': value is None, 'truthy': (bool(value) if type(value) in (bool, int, float, str, bytes, list, tuple, dict, set, frozenset, type(None)) else None)}
        except Exception:
            values[str(key)] = {'type': type(value).__name__}
    return values
def _branch_info(path, line):
    key = (path, line)
    if key in _branch_cache:
        return _branch_cache[key]
    result = None
    try:
        parsed = _parsed_source(path)
        for node in ast.walk(parsed):
            if isinstance(node, (ast.If, ast.While)) and node.lineno == line:
                true_line = node.body[0].lineno if getattr(node, 'body', None) else line
                false_line = node.orelse[0].lineno if getattr(node, 'orelse', None) else getattr(node, 'end_lineno', line) + 1
                result = (true_line, false_line)
                break
            if isinstance(node, ast.Match):
                for index, case in enumerate(node.cases):
                    guard = getattr(case, 'guard', None)
                    if guard is None or getattr(guard, 'lineno', -1) != line:
                        continue
                    true_line = case.body[0].lineno if case.body else line
                    false_line = (
                        getattr(node.cases[index + 1].pattern, 'lineno', getattr(node, 'end_lineno', line) + 1)
                        if index + 1 < len(node.cases) else getattr(node, 'end_lineno', line) + 1
                    )
                    result = (true_line, false_line)
                    break
    except Exception:
        result = None
    _branch_cache[key] = result
    return result
def _trace(
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
    qualified = getattr(frame.f_code, 'co_qualname', frame.f_code.co_name).replace('.<locals>.', '.')
    if inside and _focus_symbols:
        if event == 'call':
            target = any(qualified == name or name.endswith('.' + qualified)
                         or ('.' not in name and qualified.rsplit('.', 1)[-1] == name)
                         for name in _focus_symbols)
            parent_depth = _frame_depth.get(id(frame.f_back), 99)
            if not target and (parent_depth >= 2 or qualified == '<module>'):
                return None
            _frame_depth[id(frame)] = 0 if target else parent_depth + 1
        elif id(frame) not in _frame_depth:
            return None
    if inside and event in {"call", "line", "return", "exception"}:
        _sequence += 1
        source_line = linecache.getline(path, frame.f_lineno).strip()
        previous = _previous.get(id(frame))
        branch_id = None
        branch_outcome = None
        if event == "line" and previous and previous[2]:
            branch_id = previous[3]
            info = _branch_info(path, previous[1])
            branch_outcome = ("taken" if info and info[0] != previous[1] and frame.f_lineno == info[0]
                              else "not_taken" if info and info[1] != previous[1] and frame.f_lineno == info[1]
                              else "UNKNOWN")
        current_branch = (_relative_path(path, _trace_root).replace(_path_separator, "/") + ":" + str(frame.f_lineno)
                          if event == "line" and re.search(r"\b(if|elif|while|for)\b|\bmatch\b|\bcase\b", source_line) else None)
        if branch_id is None:
            branch_id = current_branch
        value = {
            'sequence': _sequence, 'path': path, 'file': path,
            'line': frame.f_lineno, 'function': getattr(frame.f_code, 'co_qualname', frame.f_code.co_name).replace('.<locals>.', '.'),
            'symbol': getattr(frame.f_code, 'co_qualname', frame.f_code.co_name).replace('.<locals>.', '.'), 'event': event,
            'caller': getattr(frame.f_back.f_code, 'co_qualname', frame.f_back.f_code.co_name).replace('.<locals>.', '.') if frame.f_back else None,
            'caller_file': frame.f_back.f_code.co_filename if frame.f_back else None,
            'branch_id': branch_id, 'branch_outcome': branch_outcome,
            'source_anchor': _source_anchor(path, previous[1] if branch_outcome and previous else frame.f_lineno),
            'predicate': (
                previous[4] if branch_outcome and previous and len(previous) > 4
                else source_line if branch_id else None
            ),
            'safe_local_summary': _safe_locals(frame),
            'previous_line': (previous[0] if previous else None),
            'line_arc': ([previous[0], frame.f_lineno] if previous else None),
            'return_summary': ({'type': type(arg).__name__, 'none': arg is None,
                                'truthy': (bool(arg) if type(arg) in (bool, int, float, str, bytes, list, tuple, dict, set, frozenset, type(None)) else None)} if event == 'return' else None),
        }
        if _first_event is None:
            _first_event = value
        relative = _relative_path(path, _trace_root).replace(_path_separator, "/")
        focused = any(
            relative == item or relative.startswith(item.rstrip("/") + "/")
            for item in _configured_focus
        )
        (_focus_events if focused else _events).append(value)
        _previous[id(frame)] = (frame.f_lineno, frame.f_lineno,
                                bool(current_branch), current_branch, source_line)
    if event == "return":
        _previous.pop(id(frame), None)
        _frame_depth.pop(id(frame), None)
    return _trace if inside else None
_profile = _trace
def _write():
    if _output:
        values = list(_events) + list(_focus_events)
        if _first_event is not None and _first_event not in values:
            values.append(_first_event)
        values.sort(key=lambda item: item.get('sequence', 0) if isinstance(item, dict) else item[0])
        with open(_output, "w", encoding="utf-8") as handle:
            json.dump(values, handle)
atexit.register(_write)
sys.settrace(_trace)
threading.settrace(_trace)
'''


@charged_execution
def run_trace(
    tree: Path,
    command: tuple[str, ...],
    *,
    cwd: str = ".",
    environment: tuple[tuple[str, str], ...] = (),
    timeout_seconds: float = 60.0,
    trace_enabled: bool = True,
    target_symbols: tuple[str, ...] = (),
    overlay_paths: tuple[str, ...] = (),
) -> TraceBundle:
    started = time.monotonic()
    tree = Path(tree).resolve()
    input_tree_hash = tree_hash(tree)
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
        image = env.pop("REACHPATCH_EXECUTION_IMAGE", "").strip()
        base_commit = env.pop("REACHPATCH_EXECUTION_BASE_COMMIT", "").strip()
        execution_tree = tree
        if not image:
            # Public checks may build extensions, create caches, or rewrite
            # compatibility files. Execute them on an isolated copy so those
            # side effects can never become part of a checkpoint diff.
            execution_tree = instrumentation / "working"
            copy_source_tree(tree, execution_tree)
        # Working snapshots can be revised within the filesystem timestamp
        # resolution while preserving a module's byte size.  Never create a
        # timestamp-based .pyc that a later trial could mistake for its source.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        search_paths = [str(execution_tree), env.get("PYTHONPATH", "")]
        if trace_enabled:
            (instrumentation / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
            env["REACHPATCH_TRACE_ROOT"] = str(execution_tree)
            env["REACHPATCH_TRACE_OUTPUT"] = str(trace_output)
            env["REACHPATCH_TRACE_FOCUS"] = json.dumps(sorted(set(overlay_paths)))
            env["REACHPATCH_TRACE_SYMBOLS"] = json.dumps(sorted(set(target_symbols)))
            search_paths.insert(0, str(instrumentation))
        env["PYTHONPATH"] = os.pathsep.join(filter(None, search_paths))
        try:
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
                        "--env", "REACHPATCH_TRACE_SYMBOLS=" + json.dumps(sorted(set(target_symbols))),
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
                    cwd=(execution_tree / cwd).resolve(),
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
            stderr_lines = result.stderr.splitlines()
            exception = None if result.returncode == 0 else (
                stderr_lines[-1] if stderr_lines else None
            )
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
    last_frame = None
    for raw in dynamic_events:
        if isinstance(raw, dict):
            raw_path = raw.get("path", raw.get("file"))
            raw_line = raw.get("line", 0)
            raw_symbol = raw.get("symbol", raw.get("function", ""))
        elif isinstance(raw, (tuple, list)) and len(raw) >= 4:
            raw_path, raw_line, raw_symbol = raw[0], raw[1], raw[2]
        else:
            continue
        path = Path(raw_path)
        try:
            relative = (
                path.relative_to("/testbed").as_posix()
                if path.is_absolute() and path.is_relative_to("/testbed")
                else path.resolve().relative_to(execution_tree).as_posix()
            )
        except ValueError:
            continue
        if isinstance(raw, dict):
            raw["file"] = relative
            raw["path"] = relative
            caller_path = Path(raw.get("caller_file") or "")
            try:
                raw["caller_file"] = caller_path.relative_to("/testbed" if str(caller_path).startswith("/testbed/") else execution_tree).as_posix()
            except ValueError:
                raw["caller_file"] = None
            raw["source_version_id"] = input_tree_hash
            raw["instrumentation_status"] = "UNRESOLVED_BRANCH" if raw.get("branch_outcome") == "UNKNOWN" else "OBSERVED"
        line_id = f"{relative}:{raw_line}"
        lines.append(line_id)
        symbols.append(str(raw_symbol))
        if first_frame is None:
            first_frame = line_id
        last_frame = line_id
    for match in _FRAME.finditer(combined):
        path = Path(match.group(1))
        try:
            relative = (
                path.relative_to("/testbed").as_posix()
                if path.is_absolute() and path.is_relative_to("/testbed")
                else path.resolve().relative_to(execution_tree).as_posix()
            )
        except ValueError:
            continue
        line_id = f"{relative}:{match.group(2)}"
        lines.append(line_id)
        if match.group(3):
            symbols.append(match.group(3))
        if first_frame is None:
            first_frame = line_id
        last_frame = line_id
    for marker in _TRACE_MARKER.findall(combined):
        symbols.extend(item.strip() for item in marker.split(",") if item.strip())
    observation = RunObservation(
        status=status,
        return_code=return_code,
        # Keep a substantial tail for repair diagnostics while bounding
        # artifacts. The first project traceback is retained separately below.
        stdout=stdout[-12000:],
        stderr=stderr[-12000:],
        duration_seconds=time.monotonic() - started,
        exception=str(exception) if exception else None,
    )
    digest = input_tree_hash
    trace_id = stable_id("trace", digest, command, observation, lines, symbols)
    typed_events = tuple(
        TraceEvent(
            file=str(item.get("file", item.get("path", ""))),
            function=str(item.get("function", item.get("symbol", ""))),
            line=int(item.get("line", 0) or 0),
            event=str(item.get("event", "line")),
            caller=(str(item["caller"]) if item.get("caller") is not None else None),
            branch_id=(str(item["branch_id"]) if item.get("branch_id") is not None else None),
            branch_outcome=(str(item["branch_outcome"]) if item.get("branch_outcome") is not None else None),
            safe_local_summary=dict(item.get("safe_local_summary") or {}),
            predicate=(str(item["predicate"]) if item.get("predicate") is not None else None),
            return_summary=(dict(item["return_summary"]) if isinstance(item.get("return_summary"), dict) else None),
            sequence=int(item.get("sequence", 0) or 0),
            caller_file=item.get("caller_file"),
            source_anchor=tuple(item.get("source_anchor") or ()),
            source_version_id=item.get("source_version_id"),
            instrumentation_status=item.get("instrumentation_status", "OBSERVED"),
            previous_line=(int(item["previous_line"]) if item.get("previous_line") is not None else None),
            line_arc=(tuple(int(value) for value in item["line_arc"]) if isinstance(item.get("line_arc"), (list, tuple)) and len(item["line_arc"]) == 2 else None),
        )
        if isinstance(item, dict)
        else item
        for item in dynamic_events
        if isinstance(item, dict) or (isinstance(item, (tuple, list)) and len(item) >= 4)
    )
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
        last_project_frame=last_frame,
        cwd=cwd,
        environment=tuple(sorted((str(key), str(value)) for key, value in environment)),
        backend=backend,
        events=typed_events,
    )
