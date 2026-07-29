from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from reachpatch.execution.models import CheckExecution, ExecutableCheck
from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import stable_id
from reachpatch.program_graph.budget import GraphBudget
from reachpatch.program_graph.index import RepositoryIndex, SymbolLocation
from reachpatch.program_graph.models import CausalSlice, ImpactSlice, ProgramGraph, TargetSlice
from reachpatch.program_graph.slice import ContextRequest, RepairSliceSeed


_CAUSAL_RELATIONS = {
    "def_use", "data_flow", "parameter_flow", "return_flow", "field_flow",
    "control_dependency", "control_flow", "dispatch", "exception_flow",
    "raises", "catches", "state_read", "state_write", "calls", "may_call",
}
_IMPACT_RELATIONS = {
    "calls", "may_call", "return_flow", "field_flow", "state_read",
    "state_write", "dispatch", "protocol_candidate", "protocol_fallback",
    "exception_flow", "catches", "serializes", "observes", "test_coverage",
}


def _location_nodes(graph: ProgramGraph, relative_path: str, line: int) -> list[str]:
    matches = []
    for node_id in graph.file_index.get(relative_path, ()):
        node = graph.nodes[node_id]
        start = int(node.attributes.get("line", -1))
        end = int(node.attributes.get("end_line", start))
        if start <= line <= end:
            matches.append(node_id)
    return sorted(matches, key=lambda node_id: (
        int(graph.nodes[node_id].attributes.get("end_line", line))
        - int(graph.nodes[node_id].attributes.get("line", line)),
        graph.nodes[node_id].kind,
        node_id,
    ))


def _enclosing_location(
    repository_index: RepositoryIndex, relative_path: str, line: int,
) -> SymbolLocation | None:
    locations = {
        (item.qualified_name, item.relative_path, item.line, item.end_line): item
        for values in repository_index.symbols.values()
        for item in values
        if item.relative_path == relative_path
        and item.kind in {"function", "method"}
        and item.line <= line <= item.end_line
    }
    return min(
        locations.values(), key=lambda item: (item.end_line - item.line, item.line),
        default=None,
    )


def _fallback_enclosing_callable(
    repository_index: RepositoryIndex,
    start_frame: dict[str, object],
) -> SymbolLocation | None:
    """Choose a concrete method when a fallback frame names a class."""

    relative_path = str(start_frame.get("relative_path", ""))
    line = int(start_frame.get("line", 0))
    enclosing = _enclosing_location(repository_index, relative_path, line)
    if enclosing is not None:
        return enclosing
    symbol = str(start_frame.get("symbol", ""))
    class_locations = tuple(
        item for item in repository_index.symbols.get(symbol, ())
        if item.relative_path == relative_path and item.kind == "class"
    )
    if not class_locations:
        return None
    owner = min(class_locations, key=lambda item: (item.end_line - item.line, item.line))
    methods = [
        item
        for values in repository_index.symbols.values()
        for item in values
        if item.relative_path == relative_path
        and item.kind in {"function", "method"}
        and owner.line <= item.line <= owner.end_line
        and item.qualified_name.startswith(owner.qualified_name + ".")
    ]
    def method_priority(item: SymbolLocation) -> tuple[int, int, int, str]:
        name = item.qualified_name.rsplit(".", 1)[-1].lower()
        # A frame-less public reproduction commonly reports only its final
        # assertion. When the imported class exposes a check/validation path,
        # that path is the causal entrypoint; choosing __init__ merely because
        # it appears first sends repair toward unrelated construction code.
        if name == "check":
            semantic_rank = 0
        elif name.startswith("_check") or name in {
            "validate", "clean", "serialize", "deconstruct",
        }:
            semantic_rank = 1
        elif name in {"__call__", "run", "process", "convert"}:
            semantic_rank = 2
        elif name == "__init__":
            semantic_rank = 4
        else:
            semantic_rank = 3
        return semantic_rank, item.end_line - item.line, item.line, name

    return min(methods, key=method_priority, default=None)


_BOOTSTRAP_SYMBOL_MARKERS = {
    "configure", "setup", "initialize", "initialise", "bootstrap",
}


def _target_fallback_locations(
    target_check: ExecutableCheck,
    repository_index: RepositoryIndex,
) -> tuple[SymbolLocation, ...]:
    """Resolve all public symbols when a reproduction has no project frame.

    A temporary reproduction normally reports its own assertion frame, not the
    project code that produced the observation.  The imported symbols on the
    check are therefore the only executable localization evidence.  Resolving
    every candidate matters: setup/configuration calls often sort before the
    actual domain class (for example ``settings.configure`` before
    ``CharField``), and choosing only the first candidate sends repair toward
    infrastructure code.
    """

    candidates: list[tuple[tuple[object, ...], SymbolLocation]] = []
    seen: set[tuple[str, str, int, int]] = set()
    evidence_symbols = tuple(
        evidence.split(":", 1)[1]
        for evidence in target_check.source_evidence_ids
        if evidence.startswith("issue-behavior:")
    )
    for evidence_index, symbol in enumerate(evidence_symbols):
        exact = tuple(repository_index.symbols.get(symbol, ()))
        leaf = symbol.rsplit(".", 1)[-1]
        locations = exact or tuple(repository_index.symbols.get(leaf, ()))
        module_tokens = set(symbol.rsplit(".", 1)[0].split("."))
        for location in locations:
            relative = str(location.relative_path).replace("\\", "/")
            if not relative or "tests" in Path(relative).parts:
                continue
            key = (
                str(location.qualified_name), relative,
                int(location.line), int(location.end_line),
            )
            if key in seen:
                continue
            seen.add(key)
            path_tokens = set(Path(relative).with_suffix("").parts)
            module_overlap = len(module_tokens & path_tokens)
            symbol_leaf = str(location.qualified_name).rsplit(".", 1)[-1].lower()
            evidence_leaf = leaf.lower()
            bootstrap_penalty = int(
                evidence_leaf in _BOOTSTRAP_SYMBOL_MARKERS
                or symbol_leaf in _BOOTSTRAP_SYMBOL_MARKERS
                or ".settings." in symbol.lower()
            )
            kind_penalty = int(location.kind not in {"class", "function", "method"})
            candidates.append((
                (
                    bootstrap_penalty,
                    -module_overlap,
                    int(location.qualified_name != symbol),
                    kind_penalty,
                    evidence_index,
                    relative,
                    int(location.line),
                ),
                location,
            ))
    candidates.sort(key=lambda item: item[0])
    return tuple(location for _, location in candidates[:16])


def prioritize_target_repair_seeds(
    seeds: RepairSliceSeed,
    target_recovery_result,
    repository_index: RepositoryIndex,
) -> RepairSliceSeed:
    """Put stable target frames and directly invoked symbols into the L0 budget."""

    files: list[str] = []
    symbols: list[str] = []

    def add_file(relative_path: str) -> None:
        if (
            relative_path in repository_index.source_hashes
            and relative_path not in files
            and len(files) < 8
        ):
            files.append(relative_path)

    def add_symbol(symbol: str) -> None:
        if symbol and symbol not in symbols and len(symbols) < 40:
            symbols.append(symbol)

    executions = {
        item.check_id: item
        for item in target_recovery_result.baseline_executions
    }
    for check in target_recovery_result.targets:
        execution = executions.get(check.check_id)
        frame = execution.first_project_frame if execution is not None else None
        if frame:
            relative_path = str(frame.get("relative_path", ""))
            line = int(frame.get("line", 0))
            add_file(relative_path)
            add_symbol(str(frame.get("symbol", "")))
            enclosing = _enclosing_location(
                repository_index, relative_path, line,
            )
            if enclosing is not None:
                add_symbol(enclosing.qualified_name)
        for evidence in check.source_evidence_ids:
            if not evidence.startswith("issue-behavior:"):
                continue
            requested = evidence.split(":", 1)[1]
            locations = (
                repository_index.symbols.get(requested, ())
                or repository_index.symbols.get(requested.rsplit(".", 1)[-1], ())
            )
            for location in locations:
                add_file(location.relative_path)
                add_symbol(location.qualified_name)
                if location.kind != "class":
                    continue
                prefix = location.qualified_name + "."
                for candidates in repository_index.symbols.values():
                    for candidate in candidates:
                        if (
                            candidate.relative_path == location.relative_path
                            and candidate.kind in {"function", "method"}
                            and candidate.qualified_name.startswith(prefix)
                        ):
                            add_symbol(candidate.qualified_name)
    if not files and not symbols:
        return seeds
    request = ContextRequest(
        symbols=tuple(symbols), file_paths=tuple(files),
        reason="stable_baseline_target_l0",
    )
    return replace(
        seeds,
        file_paths=tuple(dict.fromkeys((*files, *seeds.file_paths))),
        symbol_names=tuple(dict.fromkeys((*symbols, *seeds.symbol_names))),
        requested_context=(request, *seeds.requested_context),
    )


def build_target_slice(
    target_recovery_result,
    repository_index: RepositoryIndex,
    current_graph: ProgramGraph,
    *,
    changed_files: Iterable[str] = (),
) -> TargetSlice:
    baseline = {item.check_id: item for item in target_recovery_result.baseline_executions}
    files = set(map(str, changed_files))
    symbols: set[str] = set()
    locations = []
    check_ids = []
    for check in target_recovery_result.targets:
        check_ids.append(check.check_id)
        execution = baseline.get(check.check_id)
        frame = execution.first_project_frame if execution is not None else None
        if frame:
            relative = str(frame.get("relative_path", ""))
            if relative:
                files.add(relative)
                locations.append(dict(frame))
            symbol = str(frame.get("symbol", ""))
            if symbol and symbol != "<failure>":
                symbols.add(symbol)
        for evidence in check.source_evidence_ids:
            if evidence.startswith("issue-behavior:"):
                symbols.add(evidence.split(":", 1)[1])
    for symbol in tuple(symbols):
        for location in (
            repository_index.symbols.get(symbol, ())
            or repository_index.symbols.get(symbol.rsplit(".", 1)[-1], ())
        ):
            files.add(location.relative_path)
            locations.append({
                "relative_path": location.relative_path,
                "line": location.line,
                "symbol": location.qualified_name,
            })
    nodes = tuple(sorted({
        node_id for relative in files
        for node_id in current_graph.file_index.get(relative, ())
    }))
    return TargetSlice(
        slice_id=stable_id("target-slice", sorted(check_ids), sorted(files), sorted(symbols), nodes),
        check_ids=tuple(sorted(check_ids)),
        file_paths=tuple(sorted(files)),
        symbol_names=tuple(sorted(symbols)),
        node_ids=nodes,
        source_locations=tuple(sorted(
            locations,
            key=lambda item: (
                str(item.get("relative_path", "")), int(item.get("line", 0)),
                str(item.get("symbol", "")),
            ),
        )),
    )


def recover_causal_slice(
    execution: CheckExecution,
    repository_index: RepositoryIndex,
    current_graph: ProgramGraph,
    budget: GraphBudget,
    target_check: ExecutableCheck | None = None,
) -> CausalSlice:
    frame = execution.first_project_frame
    fallback_locations: tuple[SymbolLocation, ...] = ()
    if not frame and target_check is not None:
        fallback_locations = _target_fallback_locations(
            target_check, repository_index,
        )
        if fallback_locations:
            location = fallback_locations[0]
            frame = {
                "relative_path": location.relative_path,
                "line": location.line,
                "symbol": location.qualified_name,
                "origin": "target_check_public_symbol_set",
            }
    if not frame:
        return CausalSlice(
            slice_id=stable_id("causal-slice", execution.execution_id, "no-frame"),
            execution_id=execution.execution_id, failure_location=None,
            enclosing_callable=None, node_ids=(), edge_ids=(),
            branch_predicate_ids=(), dispatch_edge_ids=(), exception_edge_ids=(),
            candidate_cut_node_ids=(), truncated_reason="NO_PROJECT_FRAME",
        )
    fallback_frames = tuple({
        "relative_path": location.relative_path,
        "line": location.line,
        "symbol": location.qualified_name,
        "origin": "target_check_public_symbol_set",
    } for location in fallback_locations)
    start_frames = (frame, *fallback_frames) if fallback_frames else (frame,)
    starts: dict[str, int] = {}
    primary_enclosing: SymbolLocation | None = None
    for frame_index, start_frame in enumerate(start_frames):
        relative_path = str(start_frame.get("relative_path", ""))
        line = int(start_frame.get("line", 0))
        enclosing = _fallback_enclosing_callable(repository_index, start_frame)
        if frame_index == 0:
            primary_enclosing = enclosing
        initial_distance = frame_index
        for node_id in _location_nodes(current_graph, relative_path, line):
            starts[node_id] = min(starts.get(node_id, initial_distance), initial_distance)
        if enclosing is None:
            continue
        callable_nodes = [
            node_id
            for node_id in current_graph.file_index.get(relative_path, ())
            if enclosing.line
            <= int(current_graph.nodes[node_id].attributes.get("line", -1))
            <= enclosing.end_line
        ]
        observation_nodes = [
            node_id for node_id in callable_nodes
            if current_graph.nodes[node_id].kind in {
                "return", "raise", "assignment", "state_write", "expression",
                "call", "branch", "condition", "statement",
            }
        ]
        for node_id in (*observation_nodes, *(callable_nodes if not observation_nodes else ())):
            starts[node_id] = min(starts.get(node_id, initial_distance), initial_distance)
        for node_id in current_graph.resolve_symbol(enclosing.qualified_name):
            starts[node_id] = min(starts.get(node_id, initial_distance), initial_distance)
    queue = deque(starts.items())
    visited: set[str] = set()
    edges: set[str] = set()
    distance: dict[str, int] = {}
    while queue and budget.check(nodes=len(visited), edges=len(edges)):
        node_id, depth = queue.popleft()
        if node_id in visited or node_id not in current_graph.nodes:
            continue
        visited.add(node_id)
        distance[node_id] = depth
        for edge in current_graph.incoming(node_id, _CAUSAL_RELATIONS):
            edges.add(edge.edge_id)
            for source in edge.source_ids:
                if source not in visited:
                    queue.append((source, depth + 1))
    branch_nodes = tuple(sorted(
        node_id for node_id in visited
        if current_graph.nodes[node_id].kind in {"branch", "condition", "predicate"}
        or current_graph.nodes[node_id].attributes.get("predicate")
    ))
    dispatch_edges = tuple(sorted(
        edge_id for edge_id in edges
        if current_graph.edges[edge_id].kind in {
            "dispatch", "protocol_candidate", "protocol_selected", "protocol_fallback",
        }
    ))
    exception_edges = tuple(sorted(
        edge_id for edge_id in edges
        if current_graph.edges[edge_id].kind in {"exception_flow", "raises", "catches"}
    ))
    excluded_kinds = {"module", "parameter", "test", "external_interface"}
    cut_candidates = tuple(
        node_id for node_id in sorted(
            visited,
            key=lambda item: (
                distance.get(item, 10**9),
                int(current_graph.nodes[item].attributes.get("line", 10**9)), item,
            ),
        )
        if current_graph.nodes[node_id].kind not in excluded_kinds
        and "tests" not in Path(str(current_graph.nodes[node_id].attributes.get("file", ""))).parts
    )[:20]
    return CausalSlice(
        slice_id=stable_id("causal-slice", execution.execution_id, sorted(visited), sorted(edges)),
        execution_id=execution.execution_id, failure_location=dict(frame),
        enclosing_callable=(
            primary_enclosing.qualified_name if primary_enclosing else None
        ),
        node_ids=tuple(sorted(visited)), edge_ids=tuple(sorted(edges)),
        branch_predicate_ids=branch_nodes, dispatch_edge_ids=dispatch_edges,
        exception_edge_ids=exception_edges, candidate_cut_node_ids=cut_candidates,
        truncated_reason=budget.truncated_reason,
    )


def build_diff_impact_slice(
    actual_diff: ActualDiff,
    repository_index: RepositoryIndex,
    active_program_graph: ProgramGraph,
    budget: GraphBudget,
) -> ImpactSlice:
    changed_nodes: set[str] = set()
    changed_symbols: set[str] = set()
    for hunk in actual_diff.hunks:
        lower = hunk.new_start
        upper = lower + max(hunk.new_count, 1) - 1
        for node_id in active_program_graph.file_index.get(hunk.file, ()):
            node = active_program_graph.nodes[node_id]
            start = int(node.attributes.get("line", -1))
            end = int(node.attributes.get("end_line", start))
            if start <= upper and end >= lower:
                changed_nodes.add(node_id)
                qualified = str(node.attributes.get("qualified_name", ""))
                if qualified:
                    changed_symbols.add(qualified)
        for values in repository_index.symbols.values():
            for location in values:
                if location.relative_path == hunk.file and location.line <= upper and location.end_line >= lower:
                    changed_symbols.add(location.qualified_name)
    queue = deque(changed_nodes)
    impacted = set(changed_nodes)
    relevant_edges = set()
    while queue and budget.check(nodes=len(impacted), edges=len(relevant_edges)):
        node_id = queue.popleft()
        for edge in (
            *active_program_graph.incoming(node_id, _IMPACT_RELATIONS),
            *active_program_graph.outgoing(node_id, _IMPACT_RELATIONS),
        ):
            relevant_edges.add(edge.edge_id)
            for neighbor in edge.source_ids + edge.target_ids:
                if neighbor not in impacted:
                    impacted.add(neighbor)
                    queue.append(neighbor)
    callers = tuple(sorted({
        source for node_id in changed_nodes
        for edge in active_program_graph.incoming(node_id, {"calls", "may_call"})
        for source in edge.source_ids
    }))
    state_consumers = tuple(sorted(
        node_id for node_id in impacted
        if active_program_graph.nodes[node_id].kind in {"state_read", "field", "local"}
        or any(edge.kind == "state_read" for edge in active_program_graph.incoming(node_id))
    ))
    dispatch_alternatives = tuple(sorted({
        node_id for node_id in impacted
        if any(
            edge.kind in {"dispatch", "protocol_candidate", "protocol_fallback"}
            for edge in (*active_program_graph.incoming(node_id), *active_program_graph.outgoing(node_id))
        )
    }))
    exception_consumers = tuple(sorted({
        node_id for node_id in impacted
        if any(edge.kind in {"exception_flow", "catches"} for edge in active_program_graph.incoming(node_id))
    }))
    sibling_paths = tuple(sorted(
        path_id for path_id, path_class in active_program_graph.path_classes.items()
        if changed_nodes & set(path_class.node_ids)
        and (
            path_class.critical_predicates
            or any(
                active_program_graph.nodes[node_id].kind
                in {"branch", "condition", "predicate"}
                for node_id in path_class.node_ids
                if node_id in active_program_graph.nodes
            )
        )
    ))
    uncovered = tuple(sorted(
        path_id for path_id in sibling_paths
        if not set(active_program_graph.path_classes[path_id].node_ids) & (impacted - changed_nodes)
    ))
    return ImpactSlice(
        slice_id=stable_id("impact-slice", actual_diff.diff_id, sorted(impacted), sorted(changed_symbols)),
        changed_files=tuple(sorted(actual_diff.changed_files)),
        changed_symbol_names=tuple(sorted(changed_symbols)), node_ids=tuple(sorted(impacted)),
        direct_caller_ids=callers, sibling_path_ids=sibling_paths,
        state_consumer_ids=state_consumers, dispatch_alternative_ids=dispatch_alternatives,
        exception_consumer_ids=exception_consumers,
        uncovered_branch_partition_ids=uncovered, truncated_reason=budget.truncated_reason,
    )


def expansion_event_allowed(event: str, *, repeated_failure_count: int = 0) -> bool:
    if event in {
        "FAILURE_FRAME_OUTSIDE_SLICE", "GENERATOR_SYMBOL_REQUEST",
        "PATCH_DISPATCH_STATE_REPRESENTATION", "CHALLENGE_BYPASS",
    }:
        return True
    return event == "REPEATED_FAILURE_SIGNATURE" and repeated_failure_count >= 2
