from __future__ import annotations

import argparse
import base64
import contextlib
import dis
import importlib
import io
import json
import operator
import os
import resource
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_OPERATORS = {
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "matmul": operator.matmul,
    "truediv": operator.truediv,
    "floordiv": operator.floordiv,
    "mod": operator.mod,
    "pow": operator.pow,
    "lshift": operator.lshift,
    "rshift": operator.rshift,
    "or": operator.or_,
    "xor": operator.xor,
    "and": operator.and_,
    "lt": operator.lt,
    "le": operator.le,
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "ge": operator.ge,
    "contains": operator.contains,
    "getitem": operator.getitem,
    "truth": operator.truth,
    "length": len,
}
_PROTOCOL_METHODS = {
    "__add__", "__radd__", "__sub__", "__rsub__", "__mul__", "__rmul__",
    "__matmul__", "__rmatmul__", "__truediv__", "__rtruediv__", "__floordiv__",
    "__rfloordiv__", "__mod__", "__rmod__", "__pow__", "__rpow__", "__lt__",
    "__le__", "__eq__", "__ne__", "__gt__", "__ge__", "__bool__", "__len__",
    "__iter__", "__next__", "__contains__", "__getitem__", "__enter__", "__exit__",
    "__aenter__", "__aexit__",
}


def _normalize(value: Any, *, depth: int = 0, seen: set[int] | None = None) -> Any:
    if depth > 6:
        return {"type": type(value).__qualname__, "truncated": "max_depth"}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "base64": base64.b64encode(value).decode("ascii"), "length": len(value)}
    seen = seen or set()
    identity = id(value)
    if identity in seen:
        return {"type": type(value).__qualname__, "cycle": True, "identity": identity}
    seen.add(identity)
    if isinstance(value, (list, tuple)):
        return {
            "type": type(value).__qualname__,
            "length": len(value),
            "items": [_normalize(item, depth=depth + 1, seen=seen) for item in value],
        }
    if isinstance(value, (set, frozenset)):
        items = [_normalize(item, depth=depth + 1, seen=seen) for item in value]
        return {"type": type(value).__qualname__, "length": len(value), "items": sorted(items, key=repr)}
    if isinstance(value, dict):
        return {
            "type": "dict",
            "length": len(value),
            "items": [
                [_normalize(key, depth=depth + 1, seen=seen), _normalize(item, depth=depth + 1, seen=seen)]
                for key, item in value.items()
            ],
        }
    attributes = {}
    if hasattr(value, "__dict__"):
        attributes = {
            key: _normalize(item, depth=depth + 1, seen=seen)
            for key, item in vars(value).items()
            if not key.startswith("__")
        }
    shape = getattr(value, "shape", None)
    dtype = getattr(value, "dtype", None)
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "identity": identity,
        "fields": attributes,
        "shape": list(shape) if shape is not None else None,
        "dtype": str(dtype) if dtype is not None else None,
        "repr": repr(value)[:2000],
    }


def _resolve(reference: str, namespace: dict[str, Any]) -> Any:
    parts = reference.split(".")
    if parts[0] not in namespace:
        raise KeyError(f"unknown recipe reference {parts[0]!r}")
    value = namespace[parts[0]]
    for part in parts[1:]:
        value = getattr(value, part)
    return value


def _value(value: Any, namespace: dict[str, Any]) -> Any:
    if isinstance(value, dict) and set(value) == {"ref"}:
        return _resolve(str(value["ref"]), namespace)
    if isinstance(value, list):
        return [_value(item, namespace) for item in value]
    if isinstance(value, dict):
        return {key: _value(item, namespace) for key, item in value.items()}
    return value


def _shape(name: str, value: Any) -> dict[str, Any]:
    length = None
    try:
        length = len(value)
    except (TypeError, AttributeError):
        length = None
    return {
        "name": name,
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "identity": id(value),
        "shape": {
            "length": length,
            "empty": length == 0 if length is not None else None,
            "truthy": bool(value),
            "fields": sorted(vars(value)) if hasattr(value, "__dict__") else [],
            "array_shape": list(getattr(value, "shape", ())) if hasattr(value, "shape") else None,
            "dtype": str(getattr(value, "dtype", "")) if hasattr(value, "dtype") else None,
        },
    }


class Worker:
    def __init__(self, recipe: dict[str, Any], repository_root: Path) -> None:
        self.recipe = recipe
        self.root = repository_root.resolve()
        self.namespace: dict[str, Any] = {}
        self.trace: list[dict[str, Any]] = []
        self.side_effects: list[dict[str, Any]] = []
        self.shapes: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.observations: dict[str, Any] = {}
        self.active = False
        self._pending_branches: dict[int, tuple[int, int, int, str]] = {}

    def _trace(self, frame, event: str, arg):
        raw_file_name = frame.f_code.co_filename
        if raw_file_name.startswith("<"):
            return self._trace
        file_path = Path(raw_file_name).resolve()
        if not file_path.is_relative_to(self.root):
            return self._trace
        relative = str(file_path.relative_to(self.root)).replace(os.sep, "/")
        frame_id = id(frame)
        if event == "call":
            frame.f_trace_opcodes = True
        elif event == "opcode":
            pending = self._pending_branches.pop(frame_id, None)
            if pending is not None:
                target_offset, fallthrough_offset, branch_line, opname = pending
                outcome = (
                    "taken" if frame.f_lasti == target_offset
                    else "fallthrough" if frame.f_lasti == fallthrough_offset
                    else "unknown"
                )
                self.trace.append({
                    "kind": "branch",
                    "file": relative,
                    "line": branch_line,
                    "function": frame.f_code.co_qualname,
                    "payload": {
                        "opcode": opname,
                        "target_offset": target_offset,
                        "fallthrough_offset": fallthrough_offset,
                        "outcome": outcome,
                    },
                    "timestamp_ns": time.monotonic_ns(),
                })
            instructions = list(dis.get_instructions(frame.f_code))
            instruction = next(
                (item for item in instructions if item.offset == frame.f_lasti), None
            )
            if instruction is not None and (
                "JUMP" in instruction.opname and "IF" in instruction.opname
            ):
                next_offsets = [
                    item.offset for item in instructions if item.offset > instruction.offset
                ]
                fallthrough = next_offsets[0] if next_offsets else instruction.offset + 2
                target = int(instruction.argval) if isinstance(instruction.argval, int) else fallthrough
                self._pending_branches[frame_id] = (
                    target, fallthrough, frame.f_lineno, instruction.opname
                )
            return self._trace
        payload: dict[str, Any] = {}
        kind = event
        if event == "return":
            payload["return"] = _normalize(arg)
        elif event == "exception":
            exception_type, exception, _ = arg
            payload["exception"] = {
                "type": exception_type.__name__,
                "message": str(exception),
            }
        elif event == "call" and frame.f_code.co_name in _PROTOCOL_METHODS:
            kind = "protocol_selected"
            payload["target"] = f"{frame.f_globals.get('__name__', '')}.{frame.f_code.co_qualname}"
            payload["method"] = frame.f_code.co_name
            payload["caller_line"] = frame.f_back.f_lineno if frame.f_back else None
        self.trace.append({
            "kind": kind,
            "file": relative,
            "line": frame.f_lineno,
            "function": frame.f_code.co_qualname,
            "payload": payload,
            "timestamp_ns": time.monotonic_ns(),
        })
        return self._trace

    def _audit(self, event: str, args: tuple[Any, ...]) -> None:
        if not self.active:
            return
        interesting = (
            event == "open"
            or event.startswith("socket.")
            or event.startswith("subprocess.")
            or event.startswith("os.system")
            or event.startswith("sqlite3.")
        )
        if interesting:
            self.side_effects.append({
                "event": event,
                "args": _normalize(list(args)),
                "timestamp_ns": time.monotonic_ns(),
            })

    def _network_guard(self) -> None:
        if self.recipe.get("allow_network", False):
            return

        def blocked(*args: Any, **kwargs: Any):
            raise PermissionError("ReachPatch recipe network access is blocked")

        socket.create_connection = blocked
        original_connect = socket.socket.connect

        def blocked_connect(instance, address):
            raise PermissionError(f"ReachPatch recipe network access is blocked: {address!r}")

        socket.socket.connect = blocked_connect
        self.namespace["__original_socket_connect"] = original_connect

    def _subprocess_guard(self) -> None:
        if self.recipe.get("allow_subprocess", False):
            return

        def blocked(*args: Any, **kwargs: Any):
            raise PermissionError("ReachPatch recipe subprocess access is blocked")

        subprocess.Popen = blocked
        subprocess.run = blocked
        subprocess.call = blocked

    def _execute_step(self, step: dict[str, Any]) -> None:
        operation = step["op"]
        if operation == "import":
            module = importlib.import_module(step["module"])
            self.namespace[step.get("as", step["module"].rsplit(".", 1)[-1])] = module
            return
        if operation == "container":
            kind = step.get("kind", "list")
            values = _value(step.get("items", []), self.namespace)
            constructors = {"list": list, "tuple": tuple, "set": set, "dict": dict}
            if kind not in constructors:
                raise ValueError(f"unsupported container kind {kind!r}")
            result = constructors[kind](values)
        elif operation == "construct":
            target = _resolve(step["target"], self.namespace)
            result = target(
                *[_value(item, self.namespace) for item in step.get("args", [])],
                **{key: _value(value, self.namespace) for key, value in step.get("kwargs", {}).items()},
            )
        elif operation == "set_field":
            target = _resolve(step["target"], self.namespace)
            setattr(target, step["field"], _value(step.get("value"), self.namespace))
            result = target
        elif operation == "call":
            target = _resolve(step["target"], self.namespace)
            result = target(
                *[_value(item, self.namespace) for item in step.get("args", [])],
                **{key: _value(value, self.namespace) for key, value in step.get("kwargs", {}).items()},
            )
        elif operation == "operator":
            name = step["operator"]
            if name == "iterate":
                value = _value(step["left"], self.namespace)
                limit = int(self.recipe.get("max_iteration_items", 1000))
                result = []
                for index, item in enumerate(value):
                    if index >= limit:
                        raise RuntimeError("iteration resource boundary exceeded")
                    result.append(item)
            else:
                function = _OPERATORS[name]
                arguments = [_value(step["left"], self.namespace)]
                if "right" in step:
                    arguments.append(_value(step["right"], self.namespace))
                result = function(*arguments)
        elif operation == "state_snapshot":
            target = _resolve(step["target"], self.namespace)
            snapshot = {
                "name": step.get("name", step["target"]),
                "value": _normalize(target),
                "timestamp_ns": time.monotonic_ns(),
            }
            self.snapshots.append(snapshot)
            result = target
        elif operation == "delete":
            target_name = step["target"]
            if "." in target_name:
                owner_name, attribute = target_name.rsplit(".", 1)
                delattr(_resolve(owner_name, self.namespace), attribute)
            else:
                self.namespace.pop(target_name)
            result = None
        elif operation == "sequence":
            result = None
            for nested in step.get("steps", []):
                self._execute_step(nested)
                result = self.namespace.get(nested.get("save_as", ""), result)
        elif operation == "observe":
            channel = step["channel"]
            source_name = step.get("source", "result")
            source = (
                _resolve(source_name, self.namespace)
                if isinstance(source_name, str) and "." in source_name
                else self.namespace.get(source_name)
            )
            if channel == "return":
                self.observations["return"] = _normalize(source)
                self.observations["return_identity"] = id(source)
            elif channel == "state":
                self.observations["state"] = _normalize(source)
            elif channel in {"calls", "effects", "output", "exception"}:
                self.observations[channel] = None
            else:
                self.observations[channel] = _normalize(source)
            return
        else:
            raise ValueError(f"unsupported worker operation {operation!r}")
        save_as = step.get("save_as")
        if save_as:
            self.namespace[save_as] = result
            self.shapes.append(_shape(save_as, result))

    def run(self) -> dict[str, Any]:
        sys.path.insert(0, str(self.root))
        self._network_guard()
        self._subprocess_guard()
        sys.addaudithook(self._audit)
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        status = "PASS"
        stage = "setup"
        exception_record = None
        started = time.monotonic()
        observation_reached = False
        try:
            with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
                sys.settrace(self._trace)
                self.active = True
                for step in self.recipe.get("imports", ()):
                    self._execute_step(step)
                for step in self.recipe.get("setup", ()):
                    self._execute_step(step)
                stage = "stimulus"
                for step in self.recipe.get("stimulus", ()):
                    self._execute_step(step)
                stage = "observe"
                for step in self.recipe.get("observations", ()):
                    self._execute_step(step)
                observation_reached = True
                stage = "teardown"
                for step in self.recipe.get("teardown", ()):
                    self._execute_step(step)
                related = []
                for trace_spec in self.recipe.get("traces", ()):
                    trace_observation = {}
                    for step in trace_spec.get("steps", ()):
                        self._execute_step(step)
                    trace_observation.update(self.observations)
                    related.append({"trace_id": trace_spec["trace_id"], "observation": trace_observation})
                self.observations["related_traces"] = related
        except BaseException as exc:
            status = "FAIL"
            exception_record = {
                "type": type(exc).__name__,
                "module": type(exc).__module__,
                "message": str(exc),
                "stage": stage,
            }
            self.observations["exception"] = exception_record
            observation_reached = stage in {"stimulus", "observe", "teardown"}
        finally:
            self.active = False
            sys.settrace(None)
        self.observations["stdout"] = captured_stdout.getvalue()
        self.observations["stderr"] = captured_stderr.getvalue()
        self.observations["calls"] = [
            {
                "function": event.get("function"),
                "file": event.get("file"),
                "line": event.get("line"),
                "protocol": event.get("kind") == "protocol_selected",
            }
            for event in self.trace
            if event.get("kind") in {"call", "protocol_selected"}
        ]
        self.observations["effects"] = list(self.side_effects)
        self.observations["output"] = {
            "stdout": captured_stdout.getvalue(),
            "stderr": captured_stderr.getvalue(),
        }
        self.observations["state_snapshots"] = self.snapshots
        self.observations["side_effects"] = self.side_effects
        self.observations["object_shapes"] = self.shapes
        return {
            "worker_status": status,
            "stage": stage,
            "observation_reached": observation_reached,
            "exception": exception_record,
            "observations": self.observations,
            "trace": self.trace,
            "side_effects": self.side_effects,
            "object_shapes": self.shapes,
            "state_snapshots": self.snapshots,
            "stdout": captured_stdout.getvalue(),
            "stderr": captured_stderr.getvalue(),
            "duration_seconds": time.monotonic() - started,
        }


def _set_limits(recipe: dict[str, Any]) -> None:
    limits = recipe["resource_limits"]
    resource.setrlimit(resource.RLIMIT_CPU, (int(limits["cpu_seconds"]), int(limits["cpu_seconds"])))
    resource.setrlimit(resource.RLIMIT_AS, (int(limits["memory_bytes"]), int(limits["memory_bytes"])))
    resource.setrlimit(resource.RLIMIT_FSIZE, (int(limits["output_bytes"]), int(limits["output_bytes"])))
    resource.setrlimit(resource.RLIMIT_NOFILE, (int(limits["open_files"]), int(limits["open_files"])))
    if hasattr(resource, "RLIMIT_NPROC"):
        resource.setrlimit(resource.RLIMIT_NPROC, (int(limits["process_count"]), int(limits["process_count"])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args(argv)
    recipe = json.loads(Path(args.recipe).read_text(encoding="utf-8"))
    _set_limits(recipe)
    result = Worker(recipe, Path(args.repository)).run()
    Path(args.result).write_text(json.dumps(result, ensure_ascii=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
