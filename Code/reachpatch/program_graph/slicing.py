from __future__ import annotations

from collections import deque

from reachpatch.models.base import stable_id
from reachpatch.models.evidence import DiffHunk, ExecutableCheck, TraceBundle
from reachpatch.models.graphs import (
    CausalRepairCut, ImpactCone, ProgramEdgeKind, ProgramGraph, ProgramNodeKind,
)


def match_trace_nodes(
    graph: ProgramGraph,
    trace: TraceBundle,
) -> tuple[tuple[str, ...], frozenset[str]]:
    """Map a trace to graph nodes without scanning the full graph per line."""

    nodes_by_path: dict[str, list] = {}
    for node in graph.nodes.values():
        nodes_by_path.setdefault(node.path, []).append(node)
    line_cache: dict[tuple[str, int], tuple[str, ...]] = {}
    ordered: list[str] = []
    hit: set[str] = set()
    for line_id in trace.executed_line_ids:
        path, separator, raw_line = line_id.rpartition(":")
        if not separator or not raw_line.isdigit():
            continue
        line = int(raw_line)
        key = (path, line)
        matches = line_cache.get(key)
        if matches is None:
            containing = sorted(
                (
                    node for node in nodes_by_path.get(path, ())
                    if node.start_line <= line <= node.end_line
                ),
                key=lambda node: (
                    node.end_line - node.start_line,
                    node.start_line,
                    node.node_id,
                ),
            )
            matches = tuple(node.node_id for node in containing)
            line_cache[key] = matches
        if matches:
            hit.update(matches)
            if not ordered or ordered[-1] != matches[0]:
                ordered.append(matches[0])
    names = set(trace.executed_symbol_ids)
    for node in graph.nodes.values():
        if (
            node.node_id in names
            or node.symbol in names
            or node.symbol.split(".")[-1] in names
        ):
            hit.add(node.node_id)
            if node.node_id not in ordered:
                ordered.append(node.node_id)
    return tuple(ordered), frozenset(hit)


def _changed_nodes(graph: ProgramGraph, hunks: tuple[DiffHunk, ...]) -> set[str]:
    return {
        node.node_id
        for node in graph.nodes.values()
        for hunk in hunks
        if node.path == hunk.path
        for changed_line in (hunk.changed_new_lines or (hunk.new_start,))
        if node.start_line <= changed_line <= node.end_line
    }


def compute_impact_cone(
    graph: ProgramGraph,
    changed_hunks: tuple[DiffHunk, ...],
    public_checks: tuple[ExecutableCheck, ...] = (),
) -> ImpactCone:
    changed = _changed_nodes(graph, changed_hunks)
    direct: set[str] = set()
    returns: set[str] = set()
    exceptions: set[str] = set()
    readers: set[str] = set()
    reverse: set[str] = set()
    rendering: set[str] = set()
    changed_state_write_ids = {
        edge.target_id for edge in graph.edges.values()
        if edge.kind is ProgramEdgeKind.STATE_WRITE
        and edge.source_id in changed
        and edge.target_id in graph.nodes
    }

    def state_key(node_id: str) -> tuple[str, str, str] | None:
        node = graph.nodes.get(node_id)
        if node is None:
            return None
        attribute = node.metadata.get("attribute_name")
        receiver = node.metadata.get("receiver")
        owner = node.metadata.get("owner_scope")
        if not all(isinstance(value, str) and value for value in (
            attribute, receiver, owner,
        )):
            return None
        return owner, receiver, attribute

    changed_state_keys = {
        key for node_id in changed_state_write_ids
        for key in (state_key(node_id),)
        if key is not None
    }
    for edge in graph.edges.values():
        if edge.target_id in changed and edge.kind in {
            ProgramEdgeKind.CALLS, ProgramEdgeKind.EXECUTED_CALL,
        }:
            direct.add(edge.source_id)
        if (
            edge.source_id in changed
            and edge.target_id not in changed
            and edge.kind in {
            ProgramEdgeKind.RETURN_FLOW, ProgramEdgeKind.CONSUMER,
            }
        ):
            returns.add(edge.target_id)
        if (
            edge.source_id in changed
            and edge.target_id not in changed
            and edge.kind is ProgramEdgeKind.EXCEPTION_FLOW
        ):
            exceptions.add(edge.target_id)
        if edge.kind is ProgramEdgeKind.STATE_READ:
            key = state_key(edge.target_id)
            if (
                key is not None
                and key in changed_state_keys
                and edge.source_id not in changed
                and edge.target_id not in changed
            ):
                readers.add(edge.target_id)
        if (
            edge.source_id in changed
            and edge.target_id not in changed
            and edge.kind is ProgramEdgeKind.REFLECTED_DISPATCH
        ):
            reverse.add(edge.target_id)
    for node in graph.nodes.values():
        lowered = node.symbol.lower()
        if node.node_id in changed and any(word in lowered for word in ("render", "serialize", "format", "repr")):
            rendering.add(node.node_id)
        if node.kind is ProgramNodeKind.METHOD and node.symbol.split(".")[-1].startswith("__r"):
            if any(
                graph.nodes[item].symbol.split(".")[-1].lstrip("__r") in node.symbol
                for item in changed if item in graph.nodes
            ):
                reverse.add(node.node_id)
    changed_symbols = {
        graph.nodes[item].symbol.split(".")[-1] for item in changed if item in graph.nodes
    }
    checks = tuple(dict.fromkeys(
        check.check_id for check in public_checks
        if changed_symbols.intersection(check.symbol_references)
    ))
    graph_check_ids = tuple(dict.fromkeys(
        check_id
        for node in graph.nodes.values()
        if node.node_id in direct | returns | exceptions | readers | reverse
        for check_id in node.metadata.get("public_check_ids", ())
    ))
    return ImpactCone(
        cone_id=stable_id("impact-cone", tuple(sorted(changed)), tuple(h.hunk_id for h in changed_hunks)),
        changed_hunk_ids=tuple(hunk.hunk_id for hunk in changed_hunks),
        direct_caller_ids=tuple(sorted(direct)),
        return_consumer_ids=tuple(sorted(returns)),
        exception_handler_ids=tuple(sorted(exceptions)),
        state_reader_ids=tuple(sorted(readers)),
        reverse_dispatch_ids=tuple(sorted(reverse)),
        rendering_consumer_ids=tuple(sorted(rendering)),
        public_check_ids=tuple(dict.fromkeys(checks + graph_check_ids)),
    )


def compute_causal_repair_cuts(
    graph: ProgramGraph,
    failure_trace: TraceBundle,
    changed_hunks: tuple[DiffHunk, ...],
) -> tuple[CausalRepairCut, ...]:
    """Backward-slice an observed failure to the earliest editable project node."""

    hunk_paths = {hunk.path for hunk in changed_hunks}
    observed = next((
        node_id for node_id in reversed(failure_trace.executed_path_ids)
        if node_id in graph.nodes
        and graph.nodes[node_id].path in hunk_paths
        and graph.nodes[node_id].kind in {
            ProgramNodeKind.RETURN, ProgramNodeKind.RAISE,
            ProgramNodeKind.EXTERNAL_EFFECT, ProgramNodeKind.STATE_WRITE,
            ProgramNodeKind.STATE_READ, ProgramNodeKind.BRANCH,
            ProgramNodeKind.CALL_SITE, ProgramNodeKind.METHOD,
            ProgramNodeKind.FUNCTION,
        }
    ), None)
    if observed is None:
        observed = next((
            node_id for node_id in reversed(failure_trace.executed_path_ids)
            if node_id in graph.nodes
        ), None)
    if observed is None:
        return ()
    trace_nodes = {
        node_id for node_id in failure_trace.executed_path_ids
        if node_id in graph.nodes
    }
    changed = _changed_nodes(graph, changed_hunks)
    causal_scope = trace_nodes | changed
    reverse: dict[str, list[str]] = {}
    allowed = {
        ProgramEdgeKind.RETURN_FLOW, ProgramEdgeKind.EXCEPTION_FLOW,
        ProgramEdgeKind.STATE_WRITE, ProgramEdgeKind.DATA_FLOW,
        ProgramEdgeKind.CONTROL_TRUE, ProgramEdgeKind.CONTROL_FALSE,
        ProgramEdgeKind.EXECUTED_CALL, ProgramEdgeKind.CONSUMER,
        ProgramEdgeKind.ALIAS, ProgramEdgeKind.STATE_READ,
    }
    for edge in graph.edges.values():
        dynamic_call = (
            edge.kind is ProgramEdgeKind.CALLS and edge.dynamic_confirmed
        )
        if edge.kind not in allowed and not dynamic_call:
            continue
        if edge.source_id not in causal_scope or edge.target_id not in causal_scope:
            continue
        reverse.setdefault(edge.target_id, []).append(edge.source_id)
    for parents in reverse.values():
        parents.sort()
    queue = deque([observed])
    seen = {observed}
    ordered: list[str] = []
    while queue and len(seen) < 64:
        current = queue.popleft()
        ordered.append(current)
        for parent in reverse.get(current, ()):
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)
    editable = [
        node_id for node_id in reversed(ordered)
        if graph.nodes[node_id].editable
        and not graph.nodes[node_id].path.startswith((
            "tests/", "test/", "artifacts/", "generated/",
        ))
        and "/generated/" not in graph.nodes[node_id].path
    ]
    if not editable:
        return ()
    earliest = next((item for item in editable if item in changed), editable[0])
    edge_consumers = {
        edge.target_id for edge in graph.edges.values()
        if edge.source_id in seen and edge.kind is ProgramEdgeKind.CONSUMER
    }
    impact_consumers = set(
        graph.impact_cone.all_risk_ids()
        if graph.impact_cone is not None else ()
    )
    consumers = tuple(sorted(
        node_id for node_id in edge_consumers | impact_consumers
        if node_id in graph.nodes
        and graph.nodes[node_id].editable
        and not graph.nodes[node_id].path.startswith((
            "tests/", "test/", "artifacts/", "generated/",
        ))
        and "/generated/" not in graph.nodes[node_id].path
    ))[:24]
    cut = CausalRepairCut(
        cut_id=stable_id("causal-cut", failure_trace.trace_bundle_id, earliest, changed_hunks),
        observation_node_id=observed,
        responsible_node_ids=tuple(ordered),
        earliest_editable_node_id=earliest,
        changed_hunk_ids=tuple(hunk.hunk_id for hunk in changed_hunks),
        preservation_consumer_ids=consumers,
    )
    return (cut,)
