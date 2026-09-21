from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.evidence import ActualDiff, TraceBundle
from reachpatch.models.graphs import (
    ContextRequest, GraphBudget, PathClass, ProgramEdge, ProgramEdgeKind,
    ProgramGraph, ProgramGraphDelta, ProgramNodeKind,
)

from .local_builder import (
    RepositoryIndex, _diff_focus, _limit_edges, _parse_file,
    _resolve_call_edges,
)
from .slicing import compute_impact_cone, match_trace_nodes


_CALLABLE_KINDS = {
    ProgramNodeKind.FUNCTION,
    ProgramNodeKind.METHOD,
}


def materialize_execution_path_class(
    graph: ProgramGraph,
    trace: TraceBundle,
    anchor_node_ids: tuple[str, ...],
) -> tuple[ProgramGraph, PathClass | None]:
    """Compress a real trace around a statically bound operation.

    A trace may contain thousands of import and framework events.  Only the
    executed nodes in the narrowest bound source scope are retained.  Requiring
    a non-module anchor hit prevents an unrelated startup failure from being
    rebound to a Requirement.
    """

    ordered, matched = match_trace_nodes(graph, trace)
    anchor_hits = tuple(
        node_id for node_id in anchor_node_ids
        if node_id in matched
        and node_id in graph.nodes
        and graph.nodes[node_id].kind is not ProgramNodeKind.MODULE
    )
    if not anchor_hits:
        return graph, None

    def anchor_priority(node_id: str) -> tuple[int, int, int, str]:
        node = graph.nodes[node_id]
        return (
            0 if node.kind in _CALLABLE_KINDS else
            1 if node.kind is ProgramNodeKind.CLASS else 2,
            -(node.symbol.count(".")),
            node.end_line - node.start_line,
            node.node_id,
        )

    anchor_id = min(anchor_hits, key=anchor_priority)
    anchor = graph.nodes[anchor_id]
    if anchor.kind in _CALLABLE_KINDS | {ProgramNodeKind.CLASS}:
        scope = anchor.symbol
    else:
        callable_containers = tuple(
            node for node in graph.nodes.values()
            if node.path == anchor.path
            and node.kind in _CALLABLE_KINDS | {ProgramNodeKind.CLASS}
            and node.start_line <= anchor.start_line <= node.end_line
        )
        if not callable_containers:
            return graph, None
        container = min(
            callable_containers,
            key=lambda node: (
                node.end_line - node.start_line,
                -node.symbol.count("."),
                node.node_id,
            ),
        )
        scope = container.symbol

    relevant_ids = {
        node_id for node_id in matched
        if node_id in graph.nodes
        and graph.nodes[node_id].path == anchor.path
        and (
            graph.nodes[node_id].symbol == scope
            or graph.nodes[node_id].symbol.startswith(f"{scope}.")
        )
        and graph.nodes[node_id].kind is not ProgramNodeKind.MODULE
    }
    if not relevant_ids:
        return graph, None

    executed_names = tuple(trace.executed_symbol_ids)
    callables = tuple(
        graph.nodes[node_id] for node_id in relevant_ids
        if graph.nodes[node_id].kind in _CALLABLE_KINDS
    )

    def callable_priority(node) -> tuple[int, int, int, str]:
        terminal = node.symbol.rsplit(".", 1)[-1]
        try:
            execution_index = executed_names.index(terminal)
        except ValueError:
            execution_index = len(executed_names) + 1
        return (
            execution_index,
            node.end_line - node.start_line,
            -node.symbol.count("."),
            node.node_id,
        )

    entry = min(callables, key=callable_priority) if callables else anchor
    compressed_order = tuple(dict.fromkeys(
        (entry.node_id,)
        + tuple(node_id for node_id in ordered if node_id in relevant_ids)
        + anchor_hits
    ))[:64]
    if not compressed_order:
        return graph, None

    repeated = max(
        (sum(node_id == candidate for node_id in ordered) for candidate in relevant_ids),
        default=1,
    )
    loop_class = "0" if repeated <= 1 else "1" if repeated == 2 else "MANY"
    terminal_name = entry.symbol.rsplit(".", 1)[-1]
    recursive_calls = (
        executed_names.count(terminal_name)
        if not terminal_name.startswith("__") else 1
    )
    recursion_class = (
        "NONE" if recursive_calls <= 1 else
        "ONE" if recursive_calls == 2 else "DEPTH_LIMIT"
    )
    dispatch_route = next(iter(trace.dispatch_routes), None) or str(
        entry.metadata.get("protocol", "DIRECT")
    )
    raised = bool(trace.observation.exception) or any(
        graph.nodes[node_id].kind is ProgramNodeKind.RAISE
        for node_id in relevant_ids
    )
    exit_kind = "RAISE" if raised else "RETURN"
    observed_effect = (
        "EXCEPTION" if raised else
        "STATE_WRITE" if trace.state_writes else
        "EXTERNAL_EFFECT" if any(
            graph.nodes[node_id].kind is ProgramNodeKind.EXTERNAL_EFFECT
            for node_id in relevant_ids
        ) else "RETURN_VALUE"
    )
    path_id = stable_id(
        "execution-path-class", graph.patch_hash, entry.symbol,
        dispatch_route, exit_kind, observed_effect, loop_class,
        recursion_class, compressed_order,
    )
    path = PathClass(
        path_class_id=path_id,
        entrypoint=entry.symbol,
        ordered_guard_outcomes=(),
        dispatch_route=dispatch_route,
        exit_kind=exit_kind,
        observed_effect_kind=observed_effect,
        loop_class=loop_class,
        recursion_class=recursion_class,
        node_ids=compressed_order,
    )
    paths = dict(graph.path_classes)
    paths[path_id] = path
    return replace(graph, path_classes=paths), path


def update_program_graph_after_diff(
    previous: ProgramGraph,
    repository: Path,
    actual_diff: ActualDiff,
    trace_bundles: tuple[TraceBundle, ...],
    context_requests: tuple[ContextRequest, ...],
    budget: GraphBudget,
    public_checks: tuple = (),
) -> ProgramGraphDelta:
    started = time.monotonic()
    diff_focus = _diff_focus(actual_diff)
    local_candidates = set(previous.file_hashes) | set(actual_diff.changed_files)
    changed_files = {
        relative for relative in local_candidates
        if (
            content_hash((repository / relative).read_bytes().hex())
            if (repository / relative).is_file() else None
        ) != previous.file_hashes.get(relative)
    }
    nodes = {
        key: value for key, value in previous.nodes.items()
        if value.path not in changed_files
    }
    removed = tuple(sorted(set(previous.nodes) - set(nodes)))
    edges = {
        key: value for key, value in previous.edges.items()
        if value.source_id in nodes and value.target_id in nodes
    }
    paths = {
        key: value for key, value in previous.path_classes.items()
        if not any(node_id in removed for node_id in value.node_ids)
    }
    requested_nodes: set[str] = set()
    requested_symbols: list[str] = []
    adjacency = {
        "EXPAND_DIRECT_CALLER": {
            ProgramEdgeKind.CALLS, ProgramEdgeKind.MAY_CALL,
            ProgramEdgeKind.EXECUTED_CALL,
        },
        "EXPAND_RETURN_CONSUMER": {
            ProgramEdgeKind.RETURN_FLOW, ProgramEdgeKind.CONSUMER,
        },
        "EXPAND_EXCEPTION_HANDLER": {ProgramEdgeKind.EXCEPTION_FLOW},
        "EXPAND_PROTOCOL_DISPATCH": {
            ProgramEdgeKind.DISPATCH, ProgramEdgeKind.REFLECTED_DISPATCH,
        },
    }
    previous_frontiers = {
        (request.action, request.symbol_id, request.depth)
        for request in previous.frontier_requests
    }
    effective_requests = tuple(
        request for request in context_requests
        if (request.action, request.symbol_id, request.depth) not in previous_frontiers
    )
    for request in effective_requests:
        node = previous.nodes.get(request.symbol_id)
        if node is not None:
            requested_nodes.add(node.node_id)
            requested_symbols.append(node.symbol.split(".")[-1])
        else:
            requested_symbols.append(request.symbol_id.split(".")[-1])
    for request in effective_requests:
        start = request.symbol_id if request.symbol_id in previous.nodes else None
        if start is None or request.action not in adjacency:
            continue
        frontier = {start}
        seen = {start}
        for _ in range(min(2, max(1, request.depth))):
            next_frontier: set[str] = set()
            for edge in previous.edges.values():
                if edge.kind not in adjacency[request.action]:
                    continue
                if request.action == "EXPAND_DIRECT_CALLER" and edge.target_id in frontier:
                    next_frontier.add(edge.source_id)
                elif edge.source_id in frontier:
                    next_frontier.add(edge.target_id)
                elif edge.target_id in frontier:
                    next_frontier.add(edge.source_id)
            next_frontier -= seen
            seen.update(next_frontier)
            frontier = next_frontier
        requested_nodes.update(seen)
    if any(request.action == "TRACE_PUBLIC_CHECK" for request in effective_requests):
        requested_nodes.update(
            node.node_id for node in previous.nodes.values()
            if node.metadata.get("public_check_ids")
        )
    requested_symbols.extend(
        previous.nodes[node_id].symbol.split(".")[-1]
        for node_id in requested_nodes if node_id in previous.nodes
    )
    requested_symbol_tuple = tuple(dict.fromkeys(requested_symbols))
    index = (
        RepositoryIndex.build(
            repository, previous.base_commit, requested_symbol_tuple,
            max(budget.max_files, len(changed_files) + len(effective_requests)),
        )
        if effective_requests else
        RepositoryIndex(repository, previous.base_commit, {}, {})
    )
    parse_files = sorted(changed_files)
    parse_files.extend(
        previous.nodes[node_id].path
        for node_id in requested_nodes if node_id in previous.nodes
    )
    parse_files.extend(tuple(dict.fromkeys(
        path
        for trace in trace_bundles
        for line_id in trace.executed_line_ids
        for path, separator, raw_line in (line_id.rpartition(":"),)
        if separator and raw_line.isdigit()
        and (repository / path).is_file()
        and path.endswith(".py")
    )))
    changed_definition_names = {
        node.symbol.split(".")[-1]
        for node in previous.nodes.values()
        if node.path in changed_files
        and node.kind.value in {"FUNCTION", "METHOD", "CLASS"}
    } | set(actual_diff.changed_symbols)
    for name in changed_definition_names:
        parse_files.extend(previous.symbol_index.get(name, ()))
    for request in effective_requests:
        if request.depth > 2:
            continue
        symbol = (
            previous.nodes[request.symbol_id].symbol.split(".")[-1]
            if request.symbol_id in previous.nodes
            else request.symbol_id.split(".")[-1]
        )
        parse_files.extend(index.symbol_files.get(symbol, ()))
    cache_hits = index.cache_hits
    files_reparsed = 0
    parsed: set[str] = set()
    for relative in dict.fromkeys(parse_files):
        if len(parsed) >= budget.max_files or not (repository / relative).is_file():
            continue
        focus_lines, focus_symbols = diff_focus.get(relative, ((), ()))
        stats = _parse_file(
            index, relative, nodes, edges, paths, budget,
            focus_lines=focus_lines, focus_symbols=focus_symbols,
        )
        cache_hits += int(stats.cache_hit)
        files_reparsed += int(stats.file_reparsed)
        parsed.add(relative)
    _resolve_call_edges(nodes, edges)
    _limit_edges(edges, budget.max_edges)
    trace_graph = ProgramGraph(
        patch_hash=actual_diff.patch_hash,
        base_commit=previous.base_commit,
        nodes=nodes,
        edges=edges,
        path_classes=paths,
        file_hashes=previous.file_hashes,
        symbol_index=previous.symbol_index,
    )
    for trace in trace_bundles:
        ordered, _ = match_trace_nodes(trace_graph, trace)
        transitions = (
            ((ordered[0], ordered[0]),)
            if len(ordered) == 1 else
            tuple(dict.fromkeys(zip(ordered, ordered[1:])))
        )
        for ordinal, (before, after) in enumerate(transitions):
            edge_id = stable_id(
                "executed-edge", actual_diff.patch_hash,
                trace.trace_bundle_id, ordinal, before, after,
            )
            edges[edge_id] = ProgramEdge(
                edge_id, before, after, ProgramEdgeKind.EXECUTED_CALL,
                True, (trace.trace_bundle_id,),
            )
    file_hashes = dict(previous.file_hashes)
    for relative in changed_files:
        path = repository / relative
        if path.is_file():
            file_hashes[relative] = content_hash(path.read_bytes().hex())
        else:
            file_hashes.pop(relative, None)
    graph = ProgramGraph(
        patch_hash=actual_diff.patch_hash,
        base_commit=previous.base_commit,
        nodes=nodes,
        edges=edges,
        path_classes=paths,
        file_hashes=file_hashes,
        symbol_index={
            **previous.symbol_index,
            **index.symbol_files,
        },
        causal_cuts={
            key: value for key, value in previous.causal_cuts.items()
            if value.earliest_editable_node_id in nodes
        },
        frontier_requests=tuple(
            dict.fromkeys(
                previous.frontier_requests + effective_requests,
            )
        )[-64:],
        files_reparsed=files_reparsed,
        symbols_expanded=len(set(nodes) - set(previous.nodes)),
        cache_hits=cache_hits,
    )
    impact = compute_impact_cone(graph, actual_diff.hunks, public_checks)
    if (
        not public_checks
        and previous.impact_cone is not None
        and previous.impact_cone.changed_hunk_ids == impact.changed_hunk_ids
    ):
        impact = replace(
            impact,
            public_check_ids=previous.impact_cone.public_check_ids,
        )
    graph.impact_cone = impact
    return ProgramGraphDelta(
        graph=graph,
        added_node_ids=tuple(sorted(set(nodes) - set(previous.nodes))),
        removed_node_ids=removed,
        files_reparsed=graph.files_reparsed,
        symbols_expanded=graph.symbols_expanded,
        cache_hits=cache_hits,
        update_seconds=time.monotonic() - started,
    )
