from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import SerializableRecord, content_hash
from reachpatch.program_graph.budget import GraphBudget
from reachpatch.program_graph.index import RepositoryIndex
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.program_graph.slice import (
    ContextRequest,
    ProgramGraphBuildResult,
    build_active_program_slice,
    recover_repair_slice_seeds,
)


@dataclass(frozen=True, slots=True)
class ProgramGraphDeltaResult(SerializableRecord):
    graph: ProgramGraph
    added_node_ids: tuple[str, ...]
    removed_node_ids: tuple[str, ...]
    modified_node_ids: tuple[str, ...]
    added_edge_ids: tuple[str, ...]
    removed_edge_ids: tuple[str, ...]
    rebuilt_files: tuple[str, ...]
    retained_file_hashes: dict[str, str]
    build: ProgramGraphBuildResult


def update_active_program_slice(
    previous: ProgramGraph,
    repository_index: RepositoryIndex,
    repository_root: Path,
    actual_diff: ActualDiff,
    trace_delta: dict | None,
    context_requests: tuple[ContextRequest, ...],
    budget: GraphBudget,
) -> ProgramGraphDeltaResult:
    touched = tuple(sorted(actual_diff.changed_files))
    seeds = recover_repair_slice_seeds(
        "", (), repository_index, actual_diff=actual_diff,
        trace_delta=trace_delta, context_requests=context_requests,
    )
    partial = build_active_program_slice(
        repository_root, repository_index, seeds,
        previous=previous, changed_files=touched, budget=budget,
    )
    rebuilt = partial.graph
    old_touched_nodes = {
        node_id for path in touched for node_id in previous.file_index.get(path, ())
    }
    retained_nodes = {
        node_id: node for node_id, node in previous.nodes.items()
        if node_id not in old_touched_nodes
    }
    source_hash = content_hash({
        **{path: digest for path, digest in repository_index.source_hashes.items() if path in previous.file_index and path not in touched},
        **{path: repository_index.source_hashes.get(path, "CHANGED") for path in touched},
    })
    merged = ProgramGraph(
        repository_root=str(repository_root.resolve()), source_hash=source_hash,
        version=previous.version + 1,
    )
    for node in retained_nodes.values():
        merged.index_node(node)
    for edge in previous.edges.values():
        if all(node_id in retained_nodes for node_id in edge.source_ids + edge.target_ids):
            merged.add_edge(edge)
    for node in rebuilt.nodes.values():
        if node.node_id not in merged.nodes:
            merged.index_node(node)
    for edge in rebuilt.edges.values():
        if edge.edge_id not in merged.edges and all(node_id in merged.nodes for node_id in edge.source_ids + edge.target_ids):
            merged.add_edge(edge)
    for mapping_name in ("cfgs", "protocol_operations", "path_classes", "frontiers"):
        target = getattr(merged, mapping_name)
        for key, value in getattr(previous, mapping_name).items():
            owner_file = None
            owner_id = getattr(value, "callable_id", None) or getattr(value, "source_node_id", None) or getattr(value, "entrypoint_id", None) or getattr(value, "owner_id", None)
            if owner_id in previous.nodes:
                owner_file = previous.nodes[owner_id].attributes.get("file")
            if owner_file not in touched:
                target[key] = value
        target.update(getattr(rebuilt, mapping_name))
    old_nodes = previous.nodes
    new_nodes = merged.nodes
    old_edges = previous.edges
    new_edges = merged.edges
    modified = tuple(sorted(
        node_id for node_id in old_nodes.keys() & new_nodes.keys()
        if old_nodes[node_id] != new_nodes[node_id]
    ))
    merged.build_timings = dict(rebuilt.build_timings)
    merged.build_stats = dict(rebuilt.build_stats)
    return ProgramGraphDeltaResult(
        graph=merged,
        added_node_ids=tuple(sorted(new_nodes.keys() - old_nodes.keys())),
        removed_node_ids=tuple(sorted(old_nodes.keys() - new_nodes.keys())),
        modified_node_ids=modified,
        added_edge_ids=tuple(sorted(new_edges.keys() - old_edges.keys())),
        removed_edge_ids=tuple(sorted(old_edges.keys() - new_edges.keys())),
        rebuilt_files=touched,
        retained_file_hashes={
            path: digest for path, digest in repository_index.source_hashes.items()
            if path in previous.file_index and path not in touched
        },
        build=partial,
    )
