from __future__ import annotations

from collections import deque
from pathlib import Path

from reachpatch.execution.models import CheckExecution, ExecutableCheck
from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import stable_id
from reachpatch.program_graph.models import ProgramGraph, RepairCut


_BACKWARD_RELATIONS = {
    "def_use", "data_flow", "parameter_flow", "return_flow", "field_flow",
    "control_dependency", "control_flow", "dispatch", "protocol_selected",
    "protocol_fallback", "exception_flow", "raises", "catches",
    "state_read", "state_write", "calls", "may_call",
}
_OBSERVATION_KINDS = {
    "return", "raise", "assignment", "state_write", "expression",
    "call", "branch", "condition", "statement",
}


def _excluded(relative_path: str) -> bool:
    path = Path(relative_path)
    lowered = {part.lower() for part in path.parts}
    return (
        "tests" in lowered
        or "test" in lowered
        or "vendor" in lowered
        or "vendored" in lowered
        or "generated" in lowered
        or "migrations" in lowered
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or ".generated." in path.name.lower()
    )


def _frame_nodes(execution: CheckExecution, graph: ProgramGraph) -> list[str]:
    frame = execution.first_project_frame or {}
    relative = str(frame.get("relative_path", ""))
    line = int(frame.get("line", 0))
    matches = []
    for node_id in graph.file_index.get(relative, ()):
        node = graph.nodes[node_id]
        start = int(node.attributes.get("line", -1))
        end = int(node.attributes.get("end_line", start))
        if start <= line <= end:
            matches.append(node_id)
    return sorted(matches, key=lambda item: (
        graph.nodes[item].kind not in _OBSERVATION_KINDS,
        int(graph.nodes[item].attributes.get("end_line", line))
        - int(graph.nodes[item].attributes.get("line", line)),
        item,
    ))


def _target_symbol_nodes(check: ExecutableCheck, graph: ProgramGraph) -> list[str]:
    symbols = {
        evidence.split(":", 1)[1]
        for evidence in check.source_evidence_ids
        if evidence.startswith("issue-behavior:")
    }
    matches: set[str] = set()
    for symbol in symbols:
        symbol_nodes = graph.resolve_symbol(symbol)
        matches.update(symbol_nodes)
        for symbol_node_id in symbol_nodes:
            node = graph.nodes[symbol_node_id]
            relative = str(node.attributes.get("file", ""))
            lower = int(node.attributes.get("line", 0))
            upper = int(node.attributes.get("end_line", lower))
            matches.update(
                node_id for node_id in graph.file_index.get(relative, ())
                if lower <= int(graph.nodes[node_id].attributes.get("line", -1)) <= upper
                and graph.nodes[node_id].kind in _OBSERVATION_KINDS
            )
    return sorted(matches, key=lambda node_id: (
        graph.nodes[node_id].kind not in _OBSERVATION_KINDS,
        int(graph.nodes[node_id].attributes.get("line", 0)), node_id,
    ))


def causal_repair_cut(
    failure_execution: CheckExecution,
    target_check: ExecutableCheck,
    program_graph: ProgramGraph,
    actual_diff: ActualDiff | None,
) -> tuple[RepairCut, ...]:
    """Rank concrete source ranges on the backward failure-producing slice."""

    starts = _frame_nodes(failure_execution, program_graph)
    if not starts:
        starts = _target_symbol_nodes(target_check, program_graph)
    if not starts:
        return ()
    queue = deque((node_id, 0) for node_id in starts)
    distance: dict[str, int] = {}
    controlling: set[str] = set()
    while queue and len(distance) < 500:
        node_id, depth = queue.popleft()
        if node_id in distance or node_id not in program_graph.nodes:
            continue
        distance[node_id] = depth
        for edge in program_graph.incoming(node_id, _BACKWARD_RELATIONS):
            if edge.kind in {
                "control_dependency", "dispatch", "protocol_selected",
                "protocol_fallback", "exception_flow", "raises", "catches",
            }:
                controlling.update(edge.source_ids)
            for source in edge.source_ids:
                if source not in distance:
                    queue.append((source, depth + 1))
    issue_symbols = {
        token.rsplit(".", 1)[-1].lower()
        for value in (
            target_check.selector, *target_check.command,
            *target_check.source_evidence_ids,
        )
        for token in str(value).replace(":", " ").replace("/", " ").split()
        if token
    }
    diff_files = set(actual_diff.changed_files) if actual_diff is not None else set()
    sibling_by_node: dict[str, set[str]] = {}
    for path_id, path_class in program_graph.path_classes.items():
        for node_id in path_class.node_ids:
            sibling_by_node.setdefault(node_id, set()).add(path_id)
    candidates = []
    for node_id, failure_distance in distance.items():
        node = program_graph.nodes[node_id]
        relative = str(node.attributes.get("file", ""))
        if not relative or _excluded(relative):
            continue
        start = int(node.attributes.get("line", 0))
        end = int(node.attributes.get("end_line", start))
        if start < 1 or end < start:
            continue
        symbol = str(node.attributes.get("qualified_name", node.label))
        kind_priority = 0 if node.kind in _OBSERVATION_KINDS else 1
        control_priority = 0 if node_id in controlling else 1
        symbol_priority = 0 if symbol.rsplit(".", 1)[-1].lower() in issue_symbols else 1
        diff_priority = 0 if relative in diff_files else 1
        candidates.append((
            failure_distance, kind_priority, control_priority, symbol_priority,
            diff_priority, end - start, relative, start, node_id,
        ))
    cuts = []
    seen_ranges = set()
    for ranking in sorted(candidates):
        failure_distance, _, control_priority, symbol_priority, diff_priority, _, relative, start, node_id = ranking
        node = program_graph.nodes[node_id]
        end = int(node.attributes.get("end_line", start))
        key = (relative, start, end)
        if key in seen_ranges:
            continue
        seen_ranges.add(key)
        reasons = ["backward def-use path from first project failure frame"]
        if control_priority == 0:
            reasons.append("controls the failing observation")
        if symbol_priority == 0:
            reasons.append("overlaps issue/check symbol")
        if diff_priority == 0:
            reasons.append("overlaps current working diff")
        cuts.append(RepairCut(
            cut_id=stable_id(
                "repair-cut", failure_execution.execution_id, relative,
                start, end, node_id,
            ),
            relative_path=relative,
            start_line=start,
            end_line=end,
            symbol=str(node.attributes.get("qualified_name", node.label)),
            reason="; ".join(reasons),
            failure_distance=failure_distance,
            protected_sibling_paths=tuple(sorted(
                sibling_by_node.get(node_id, set())
            )),
        ))
        if len(cuts) >= 5:
            break
    return tuple(cuts)
