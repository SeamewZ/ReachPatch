from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.graph import GraphNode
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.program_graph.protocols import merge_observed_protocol_selection


@dataclass(frozen=True, slots=True)
class DynamicTraceEvent(SerializableRecord):
    event_id: str
    kind: str
    file: str
    line: int
    function: str
    payload: dict[str, Any]
    timestamp_ns: int


def _nodes_at(graph: ProgramGraph, file_name: str, line: int) -> list[str]:
    return sorted(
        node_id
        for node_id in graph.file_index.get(file_name, [])
        if int(graph.nodes[node_id].attributes.get("line", -1)) == line
    )


def merge_trace(
    graph: ProgramGraph,
    events: Iterable[DynamicTraceEvent],
    *,
    trace_bundle_id: str,
) -> dict[str, list[str]]:
    added_nodes: set[str] = set()
    added_edges: set[str] = set()
    selected_protocols: set[str] = set()
    previous_nodes: list[str] = []
    for event in sorted(events, key=lambda item: (item.timestamp_ns, item.event_id)):
        current_nodes = _nodes_at(graph, event.file, event.line)
        if event.kind in {"call", "line", "return", "exception", "branch"}:
            for previous in previous_nodes:
                for current in current_nodes:
                    edge = graph.add_relation(
                        "observed_execution",
                        [previous],
                        [current],
                        attributes={"event_kind": event.kind, "trace_bundle_id": trace_bundle_id},
                        provenance_ids=[trace_bundle_id],
                    )
                    added_edges.add(edge.edge_id)
            previous_nodes = current_nodes or previous_nodes
        if event.kind == "branch":
            predicate = str(event.payload.get("opcode", "branch"))
            for current in current_nodes:
                attributes = dict(graph.nodes[current].attributes)
                observations = list(attributes.get("dynamic_branch_observations", ()))
                observations.append({
                    "trace_bundle_id": trace_bundle_id,
                    "outcome": event.payload.get("outcome", "unknown"),
                    "predicate": predicate,
                })
                attributes["dynamic_branch_observations"] = observations
                graph.update_node_attributes(current, attributes)
        if event.kind == "protocol_selected":
            operation_id = str(event.payload.get("operation_id", ""))
            target = str(event.payload.get("target", ""))
            if operation_id in graph.protocol_operations and target:
                merge_observed_protocol_selection(
                    graph, operation_id, target, evidence_id=trace_bundle_id
                )
                selected_protocols.add(operation_id)
        elif event.kind == "object_shape":
            shape = event.payload.get("shape", {})
            node = GraphNode.create(
                "abstract_object",
                str(event.payload.get("type", "object")),
                identity=(trace_bundle_id, event.event_id),
                attributes={
                    "qualified_name": f"trace.{trace_bundle_id}.{event.event_id}",
                    "file": event.file,
                    "line": event.line,
                    "observed_shape": shape,
                    "observed": True,
                },
                provenance_ids=[trace_bundle_id],
            )
            graph.index_node(node)
            added_nodes.add(node.node_id)
            for current in current_nodes:
                edge = graph.add_relation(
                    "observed_execution", [current], [node.node_id],
                    provenance_ids=[trace_bundle_id],
                )
                added_edges.add(edge.edge_id)
        elif event.kind == "side_effect":
            node = GraphNode.create(
                "external_interface",
                str(event.payload.get("effect", "unknown")),
                identity=(trace_bundle_id, event.event_id),
                attributes={
                    "qualified_name": f"trace.effect.{event.event_id}",
                    "file": event.file,
                    "line": event.line,
                    "observed": True,
                    **event.payload,
                },
                provenance_ids=[trace_bundle_id],
            )
            graph.index_node(node)
            added_nodes.add(node.node_id)
            for current in current_nodes:
                edge = graph.add_relation(
                    "external_effect", [current], [node.node_id],
                    provenance_ids=[trace_bundle_id],
                )
                added_edges.add(edge.edge_id)
    return {
        "added_node_ids": sorted(added_nodes),
        "added_edge_ids": sorted(added_edges),
        "selected_protocol_operation_ids": sorted(selected_protocols),
    }


def trace_event(
    kind: str,
    file: str,
    line: int,
    function: str,
    payload: dict[str, Any],
    timestamp_ns: int,
) -> DynamicTraceEvent:
    return DynamicTraceEvent(
        event_id=stable_id("trace-event", kind, file, line, function, payload, timestamp_ns),
        kind=kind,
        file=file,
        line=line,
        function=function,
        payload=payload,
        timestamp_ns=timestamp_ns,
    )


class DynamicTracer:
    """Execute a targeted recipe and merge only observed repository behavior."""

    def __init__(self, executor) -> None:
        self.executor = executor

    def trace(
        self,
        graph: ProgramGraph,
        recipe,
        repository,
        *,
        repository_role: str = "TRACE",
    ) -> dict[str, Any]:
        bundle = self.executor.execute_recipe(
            recipe,
            repository,
            repository_role=repository_role,
            repeats=2,
        )
        if bundle.stability_status != "STABLE":
            return {
                "bundle_id": bundle.bundle_id,
                "merged": False,
                "reason": "unstable_trace",
                "added_node_ids": [],
                "added_edge_ids": [],
                "selected_protocol_operation_ids": [],
            }
        events = bundle.runs[0].trace_events if bundle.runs else ()
        delta = merge_trace(graph, events, trace_bundle_id=bundle.bundle_id)
        return {
            "bundle_id": bundle.bundle_id,
            "merged": True,
            "reason": "stable_observation",
            **delta,
        }


def merge_trace_bundles(graph: ProgramGraph, bundles, *, role: str) -> dict[str, list[str]]:
    """Merge stable traces from paired executions into the supplied graph."""

    added_nodes: set[str] = set()
    added_edges: set[str] = set()
    selected: set[str] = set()
    for paired in bundles:
        trace_bundle = paired.base_bundle if role == "BASELINE" else paired.patch_bundle
        if trace_bundle.stability_status != "STABLE" or not trace_bundle.runs:
            continue
        delta = merge_trace(
            graph,
            trace_bundle.runs[0].trace_events,
            trace_bundle_id=trace_bundle.bundle_id,
        )
        added_nodes.update(delta["added_node_ids"])
        added_edges.update(delta["added_edge_ids"])
        selected.update(delta["selected_protocol_operation_ids"])
    return {
        "added_node_ids": sorted(added_nodes),
        "added_edge_ids": sorted(added_edges),
        "selected_protocol_operation_ids": sorted(selected),
    }
