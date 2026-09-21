from __future__ import annotations

"""Bounded execution trace context for repairing one repeated failure.

The graph is intentionally not part of the specification or certification
state. It only selects source context for the repair player after a failure
has been observed repeatedly.
"""

import ast
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from reachpatch.models.base import SerializableRecord, content_hash, stable_id


class FailureGraphNodeKind(StrEnum):
    FUNCTION = "FUNCTION"
    BRANCH = "BRANCH"
    HUNK = "HUNK"
    CONSUMER = "CONSUMER"


class FailureGraphEdgeKind(StrEnum):
    DYNAMIC_CALL = "DYNAMIC_CALL"
    DYNAMIC_BRANCH = "DYNAMIC_BRANCH"
    HUNK_MODIFIES = "HUNK_MODIFIES"
    VALUE_FLOWS_TO_CONSUMER = "VALUE_FLOWS_TO_CONSUMER"


@dataclass(frozen=True, slots=True)
class FailureGraphNode(SerializableRecord):
    node_id: str
    kind: FailureGraphNodeKind
    path: str
    symbol: str
    start_line: int = 0
    end_line: int = 0
    distance: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FailureGraphEdge(SerializableRecord):
    edge_id: str
    source_id: str
    target_id: str
    kind: FailureGraphEdgeKind
    distance: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FailureGraphFrontier(SerializableRecord):
    frontier_id: str
    reason: str
    path: str | None = None
    symbol: str | None = None
    depth: int = 0


@dataclass(frozen=True, slots=True)
class DynamicFailureGraphBudget(SerializableRecord):
    max_files: int = 20
    max_nodes: int = 400
    max_edges: int = 1600
    initial_depth: int = 1
    max_expansion_depth: int = 3
    wall_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class DynamicFailureGraph(SerializableRecord):
    graph_id: str
    patch_hash: str
    active_failure_id: str
    nodes: dict[str, FailureGraphNode]
    edges: dict[str, FailureGraphEdge]
    frontier: tuple[FailureGraphFrontier, ...] = ()
    expanded_depth: int = 0


def _trace_events(trace: Any) -> tuple[tuple[str, str, int, str], ...]:
    events: list[tuple[str, str, int, str]] = []
    for item in getattr(trace, "events", ()) or ():
        if isinstance(item, dict):
            path, symbol, line, event = item.get("path"), item.get("symbol"), item.get("line"), item.get("event")
        elif isinstance(item, (tuple, list)) and len(item) >= 4:
            raw_path, raw_line, raw_symbol, raw_event = item[-4:]
            # The production tracer emits path,line,symbol,event.  Older
            # fixtures used path,symbol,line,event; accepting both keeps
            # historical trace artifacts readable without changing control.
            if not str(raw_line).isdigit() and str(raw_symbol).isdigit():
                raw_line, raw_symbol = raw_symbol, raw_line
            path, symbol, line, event = raw_path, raw_symbol, raw_line, raw_event
        else:
            continue
        if path and str(line).isdigit():
            events.append((str(path).replace("\\", "/"), str(symbol or "<unknown>"), int(line), str(event or "")))
    if events:
        return tuple(events)
    for raw in getattr(trace, "executed_line_ids", ()) or ():
        path, separator, line = str(raw).rpartition(":")
        if separator and line.isdigit():
            events.append((path.replace("\\", "/"), "<executed>", int(line), "line"))
    return tuple(events)


def _diff_hunks(diff: str) -> tuple[tuple[str, int, int, str], ...]:
    result: list[tuple[str, int, int, str]] = []
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif path and line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                start = int(match.group(1)); count = int(match.group(2) or 1)
                result.append((path, start, max(start, start + count - 1), line))
    return tuple(result)


def build_dynamic_failure_graph(
    repo_root: Path,
    working_snapshot: Path,
    current_diff: str,
    active_failure: Any,
    trace: Any,
    previous_graph: DynamicFailureGraph | None,
    budget: DynamicFailureGraphBudget,
) -> DynamicFailureGraph:
    """Build only the executed failure slice and current diff hunk context."""
    started = time.monotonic()
    repo_root = Path(repo_root).resolve()
    working_snapshot = Path(working_snapshot).resolve()
    failure_id = str(getattr(active_failure, "failure_id", "failure"))
    failure_kind = str(getattr(
        getattr(active_failure, "kind", None), "value",
        getattr(active_failure, "kind", ""),
    )).upper()
    # The graph records the exact cumulative diff identity supplied by the
    # execution loop. It is diagnostic metadata only, but must still be
    # comparable with checkpoint patch hashes.
    patch_hash = content_hash(current_diff)
    if (
        int(getattr(active_failure, "same_signature_count", 0)) < 2
        and failure_kind != "PRESERVATION"
    ):
        return DynamicFailureGraph(
            graph_id=stable_id("dynamic-failure-graph-empty", patch_hash, failure_id),
            patch_hash=patch_hash,
            active_failure_id=failure_id,
            nodes={}, edges={}, frontier=(), expanded_depth=0,
        )
    nodes: dict[str, FailureGraphNode] = {}
    edges: dict[str, FailureGraphEdge] = {}
    frontiers: list[FailureGraphFrontier] = []
    admitted_files: set[str] = set()
    depth_seen = 0

    trace_events = _trace_events(trace)
    hunks = _diff_hunks(current_diff)
    hunk_paths = {path for path, *_ in hunks}
    # Resolve trace paths to project-relative names. Only files actually
    # observed or modified by the current diff enter the candidate set.
    def relative_path(raw: str) -> str:
        value = Path(str(raw))
        for root in (working_snapshot, repo_root):
            try:
                return value.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
        return value.as_posix().lstrip("./")

    trace_events = tuple((relative_path(path), symbol, line, event)
                         for path, symbol, line, event in trace_events)
    repeats = int(getattr(active_failure, "same_signature_count", 0))
    previous_depth = (
        previous_graph.expanded_depth if previous_graph is not None else 0
    )
    expanded_depth = min(
        budget.max_expansion_depth,
        max(budget.initial_depth, previous_depth),
    )
    previous_nodes = (
        tuple(previous_graph.nodes.values()) if previous_graph is not None else ()
    )
    previous_paths = {item.path for item in previous_nodes}
    previous_symbols = {(item.path, item.symbol) for item in previous_nodes}
    trace_outside = bool(previous_graph) and any(
        path not in previous_paths or (path, symbol) not in previous_symbols
        for path, symbol, _line, _event in trace_events
    )
    same_failure = bool(
        previous_graph
        and previous_graph.active_failure_id == failure_id
    )
    expansion_reason = None
    if same_failure and repeats >= 3:
        expansion_reason = "REPEATED_FAILURE_EXPANSION"
    elif trace_outside:
        expansion_reason = "TRACE_LEFT_ACTIVITY_RANGE"
    elif failure_kind == "PRESERVATION" and previous_graph is not None:
        expansion_reason = "PRESERVATION_REGRESSION_EXPANSION"
    if expansion_reason is not None and expanded_depth < budget.max_expansion_depth:
        expanded_depth += 1

    # Compute dynamic nesting from real call/return events. Initial graphs keep
    # only the active frame and its direct callees; widening admits one
    # additional executed layer without scanning unrelated source.
    event_depths: list[int] = []
    call_depth = 0
    for _path, _symbol, _line, event in trace_events:
        if event == "call":
            event_depths.append(call_depth)
            call_depth += 1
        else:
            event_depths.append(max(0, call_depth - 1))
            if event == "return":
                call_depth = max(0, call_depth - 1)
    admitted_events = tuple(
        event for event, depth in zip(trace_events, event_depths)
        if depth <= expanded_depth
    )
    omitted_events = tuple(
        (event, depth) for event, depth in zip(trace_events, event_depths)
        if depth > expanded_depth
    )
    candidates = set(hunk_paths) | {path for path, *_ in admitted_events}
    # A traceback can point at an absolute first project frame even when the
    # profiler was unable to emit an event (for example during import failure).
    first_frame = str(getattr(active_failure, "first_project_frame", "") or "")
    if first_frame:
        candidates.add(relative_path(first_frame.rsplit(":", 1)[0]))
    candidates = {path for path in candidates if path and not path.startswith(("<", "site-packages/"))}

    distance_by_path: dict[str, int] = {path: 0 for path in hunk_paths}
    for event, distance in zip(trace_events, event_depths):
        path = event[0]
        if distance <= expanded_depth:
            distance_by_path[path] = min(
                distance_by_path.get(path, distance), distance,
            )
    ordered_files = tuple(sorted(candidates, key=lambda path: (distance_by_path.get(path, 99), path)))
    if len(ordered_files) > budget.max_files:
        for path in ordered_files[budget.max_files:]:
            frontiers.append(FailureGraphFrontier(
                stable_id("failure-graph-file", path), "FILE_BUDGET", path,
                None, distance_by_path.get(path, budget.max_expansion_depth),
            ))
        ordered_files = ordered_files[:budget.max_files]
    admitted_files.update(ordered_files)

    for (path, symbol, _line, _event), depth in omitted_events:
        frontiers.append(FailureGraphFrontier(
            stable_id("failure-graph-depth", path, symbol, depth),
            "DEPTH_BUDGET", path, symbol, depth,
        ))

    parsed: dict[str, ast.AST] = {}
    function_nodes: dict[tuple[str, int], str] = {}
    branch_nodes: dict[tuple[str, int], str] = {}
    function_defs: dict[str, tuple[ast.AST, ...]] = {}
    branch_defs: dict[str, tuple[ast.AST, ...]] = {}
    for path in ordered_files:
        if time.monotonic() - started >= budget.wall_seconds:
            frontiers.append(FailureGraphFrontier(
                stable_id("failure-graph-timeout", failure_id, path),
                "TIME_BUDGET", path, None, 0,
            ))
            break
        source = working_snapshot / path
        if not source.is_file() or source.suffix != ".py":
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"), filename=path)
        except (OSError, SyntaxError):
            continue
        parsed[path] = tree
        function_defs[path] = tuple(item for item in ast.walk(tree)
                                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        branch_defs[path] = tuple(item for item in ast.walk(tree) if isinstance(item, (
            ast.If, ast.IfExp, ast.While, ast.For, ast.AsyncFor, ast.Match,
        )))

    def admit(kind: FailureGraphNodeKind, path: str, symbol: str,
              start: int, end: int, distance: int,
              metadata: dict[str, Any] | None = None) -> str | None:
        nonlocal depth_seen
        if path not in admitted_files:
            frontiers.append(FailureGraphFrontier(
                stable_id("failure-graph-file", path), "FILE_BUDGET", path,
                symbol, distance,
            ))
            return None
        node_id = stable_id("failure-graph-node", kind, path, symbol, start, end)
        if node_id in nodes:
            return node_id
        if len(nodes) >= budget.max_nodes:
            frontiers.append(FailureGraphFrontier(
                stable_id("failure-graph-node", kind, path, symbol, start),
                "NODE_BUDGET", path, symbol, distance,
            ))
            return None
        depth_seen = max(depth_seen, distance)
        nodes[node_id] = FailureGraphNode(
            node_id, kind, path, symbol, start, end, distance, metadata or {},
        )
        return node_id

    def add_edge(source: str | None, target: str | None,
                 kind: FailureGraphEdgeKind, distance: int,
                 metadata: dict[str, Any] | None = None) -> None:
        if source is None or target is None:
            return
        edge_id = stable_id("failure-graph-edge", source, target, kind)
        if edge_id in edges:
            return
        if len(edges) >= budget.max_edges:
            frontiers.append(FailureGraphFrontier(
                stable_id("failure-graph-edge", source, target, kind),
                "EDGE_BUDGET", depth=distance,
            ))
            return
        edges[edge_id] = FailureGraphEdge(
            edge_id, source, target, kind, distance, metadata or {},
        )

    def containing(definitions: tuple[ast.AST, ...], line: int) -> ast.AST | None:
        matches = [item for item in definitions
                   if int(getattr(item, "lineno", 0)) <= line <= int(getattr(item, "end_lineno", getattr(item, "lineno", 0)))]
        return min(matches, key=lambda item: int(getattr(item, "end_lineno", 0)) - int(getattr(item, "lineno", 0))) if matches else None

    # First materialize only functions and branches backed by real trace lines.
    event_function_ids: list[str | None] = []
    event_branch_ids: list[str | None] = []
    admitted_depths = tuple(
        depth for depth in event_depths if depth <= expanded_depth
    )
    for index, ((path, symbol, line, event), event_depth) in enumerate(
        zip(admitted_events, admitted_depths)
    ):
        if time.monotonic() - started >= budget.wall_seconds:
            frontiers.append(FailureGraphFrontier(
                stable_id("failure-graph-timeout", failure_id, index),
                "TIME_BUDGET", path, symbol, index,
            ))
            break
        definition = containing(function_defs.get(path, ()), line)
        function_symbol = str(getattr(definition, "name", symbol or "<module>"))
        function_id = admit(
            FailureGraphNodeKind.FUNCTION, path, function_symbol,
            int(getattr(definition, "lineno", line)),
            int(getattr(definition, "end_lineno", line)),
            event_depth, {"event": event},
        )
        branch = containing(branch_defs.get(path, ()), line)
        branch_id = None
        if branch is not None:
            branch_id = admit(
                FailureGraphNodeKind.BRANCH, path,
                type(branch).__name__, int(getattr(branch, "lineno", line)),
                int(getattr(branch, "end_lineno", line)),
                event_depth, {"event": event},
            )
            add_edge(function_id, branch_id, FailureGraphEdgeKind.DYNAMIC_BRANCH,
                     event_depth)
        event_function_ids.append(function_id)
        event_branch_ids.append(branch_id)

    # Diff hunks are always admitted when their file is in the bounded slice.
    hunk_ids: list[tuple[str | None, str, int, int]] = []
    for path, start, end, header in hunks:
        hunk_id = admit(FailureGraphNodeKind.HUNK, path, header, start, end, 0)
        hunk_ids.append((hunk_id, path, start, end))
        function = containing(function_defs.get(path, ()), start)
        function_id = admit(
            FailureGraphNodeKind.FUNCTION, path,
            str(getattr(function, "name", "<module>")),
            int(getattr(function, "lineno", start)),
            int(getattr(function, "end_lineno", end)), 0,
            {"source": "current_diff"},
        ) if function is not None else None
        add_edge(hunk_id, function_id, FailureGraphEdgeKind.HUNK_MODIFIES, 0)
        branch = containing(branch_defs.get(path, ()), start)
        branch_id = admit(
            FailureGraphNodeKind.BRANCH, path, type(branch).__name__,
            int(getattr(branch, "lineno", start)),
            int(getattr(branch, "end_lineno", end)), 0,
            {"source": "current_diff"},
        ) if branch is not None else None
        add_edge(hunk_id, branch_id, FailureGraphEdgeKind.HUNK_MODIFIES, 0)

    # Consecutive traced call events are the only source of dynamic call edges.
    call_stack: list[str] = []
    for (event, function_id), event_depth in zip(
        zip(admitted_events, event_function_ids), admitted_depths,
    ):
        if function_id is None:
            continue
        if event[3] == "call":
            caller = call_stack[-1] if call_stack else None
            if caller != function_id:
                add_edge(
                    caller, function_id, FailureGraphEdgeKind.DYNAMIC_CALL,
                    event_depth,
                )
            call_stack.append(function_id)
        elif event[3] == "return" and call_stack:
            call_stack.pop()

    # Consumer nodes are restricted to traced call sites in the same files.
    traced_lines = {(path, line) for path, _symbol, line, _event in admitted_events}
    for path, tree in parsed.items():
        for call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
            if (path, int(getattr(call, "lineno", 0))) not in traced_lines:
                continue
            owner = containing(function_defs.get(path, ()), int(getattr(call, "lineno", 0)))
            owner_id = admit(
                FailureGraphNodeKind.FUNCTION, path,
                str(getattr(owner, "name", "<module>")),
                int(getattr(owner, "lineno", getattr(call, "lineno", 0))),
                int(getattr(owner, "end_lineno", getattr(call, "lineno", 0))),
                1,
            )
            callee = getattr(call.func, "attr", None) or getattr(call.func, "id", "call")
            consumer_id = admit(
                FailureGraphNodeKind.CONSUMER, path, str(callee),
                int(getattr(call, "lineno", 0)), int(getattr(call, "end_lineno", getattr(call, "lineno", 0))), 1,
                {"expression": ast.unparse(call) if hasattr(ast, "unparse") else str(callee)},
            )
            add_edge(owner_id, consumer_id, FailureGraphEdgeKind.VALUE_FLOWS_TO_CONSUMER, 1)

    # Expansion is deliberately event driven.  The first repeated failure
    # creates the depth-one slice; only a second consecutive observation with
    # the same semantic signature, a trace that leaves the prior slice, or a
    # newly selected preservation regression justifies widening it.  The
    # implementation still admits only traced/diff files, so expansion never
    # degenerates into a repository-wide static scan.
    if expansion_reason is not None:
        for item in sorted(nodes.values(), key=lambda value: (value.distance, value.path, value.start_line))[:8]:
            frontiers.append(FailureGraphFrontier(
                stable_id("failure-graph-expand", item.node_id, repeats, expansion_reason),
                expansion_reason, item.path, item.symbol, expanded_depth,
            ))
    # A changed diff invalidates old hunk-adjacent frontier entries. Preserve
    # only bounded frontiers that still point at an admitted/current path.
    if previous_graph is not None:
        for item in previous_graph.frontier:
            if item.path is not None and item.path not in candidates:
                continue
            if item not in frontiers:
                frontiers.append(item)
    graph_id = stable_id(
        "dynamic-failure-graph", patch_hash, failure_id,
        tuple(sorted(nodes)), tuple(sorted(edges)), expanded_depth,
    )
    return DynamicFailureGraph(
        graph_id, patch_hash, failure_id, nodes, edges,
        tuple(dict((item.frontier_id, item) for item in frontiers).values()),
        expanded_depth,
    )
