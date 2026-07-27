from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import SerializableRecord, content_hash, stable_id
from reachpatch.models.core import Frontier
from reachpatch.program_graph.budget import GraphBudget
from reachpatch.program_graph.index import RepositoryIndex
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.program_graph.slice import (
    ContextRequest,
    ProgramGraphBuildResult,
    build_active_program_slice,
    recover_repair_slice_seeds,
)


def _derived_references(kind: str, value: object) -> tuple[set[str], set[str]]:
    """Return node/edge references that make a derived record valid."""

    if kind == "cfgs":
        return ({
            value.callable_id, value.entry_node_id, *value.exit_node_ids,
            *value.statement_node_ids,
        }, set(value.edge_ids))
    if kind == "protocol_operations":
        nodes = {
            value.source_node_id,
            *value.candidate_target_ids,
        }
        if value.selected_target_id:
            nodes.add(value.selected_target_id)
        return nodes, set()
    if kind == "path_classes":
        return ({
            value.entrypoint_id, *value.node_ids, *value.observation_ids,
            *value.state_effect_ids,
        }, set(value.edge_ids))
    if kind == "frontiers":
        return ({value.owner_id}, set())
    raise ValueError(f"unsupported derived Program Graph mapping: {kind}")


def _references_touched(
    value: object,
    kind: str,
    previous: ProgramGraph,
    touched: set[str],
) -> bool:
    node_ids, _ = _derived_references(kind, value)
    if kind == "frontiers":
        owner_id = value.owner_id
        for mapping_name in ("cfgs", "protocol_operations", "path_classes"):
            owned = getattr(previous, mapping_name).get(owner_id)
            if owned is not None and _references_touched(
                owned, mapping_name, previous, touched
            ):
                return True
    return any(
        str(previous.nodes[node_id].attributes.get("file", "")) in touched
        for node_id in node_ids
        if node_id in previous.nodes
    )


def _derived_record_valid(
    value: object,
    kind: str,
    graph: ProgramGraph,
) -> bool:
    node_ids, edge_ids = _derived_references(kind, value)
    # Frontier owners can be graph-level or protocol identifiers rather than
    # nodes.  A missing node owner is therefore allowed; records with a known
    # removed/touched owner have already been invalidated above.
    if kind == "frontiers":
        return True
    valid = node_ids <= set(graph.nodes) and edge_ids <= set(graph.edges)
    if kind == "path_classes":
        valid = valid and set(value.protocol_selections) <= set(
            graph.protocol_operations
        )
    return valid


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


def _file_cost(graph: ProgramGraph, path: str) -> tuple[int, int, int]:
    node_ids = set(graph.file_index.get(path, ()))
    functions = sum(
        graph.nodes.get(cfg.callable_id) is not None
        and str(graph.nodes[cfg.callable_id].attributes.get("file", "")) == path
        for cfg in graph.cfgs.values()
    )
    edges = sum(
        bool(node_ids.intersection(edge.source_ids + edge.target_ids))
        for edge in graph.edges.values()
    )
    return len(node_ids), edges, functions


def _precise_files(graph: ProgramGraph) -> set[str]:
    return {
        str(graph.nodes[cfg.callable_id].attributes.get("file", ""))
        for cfg in graph.cfgs.values()
        if cfg.callable_id in graph.nodes
        and graph.nodes[cfg.callable_id].attributes.get("file")
    }


def _admit_active_files(
    previous: ProgramGraph,
    rebuilt: ProgramGraph,
    partial: ProgramGraphBuildResult,
    touched: tuple[str, ...],
    context_requests: tuple[ContextRequest, ...],
    budget: GraphBudget,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Bound the cumulative active slice, not merely this update."""

    context_files = tuple(
        path
        for request in context_requests
        for path in request.file_paths
    )
    ordered = tuple(dict.fromkeys((
        *touched,
        *context_files,
        *partial.analyzed_files,
        *previous.file_index,
    )))
    rebuilt_precise_files = _precise_files(rebuilt)
    admitted: list[str] = []
    deferred: list[str] = []
    nodes = edges = functions = 0
    for path in ordered:
        # A freshly precise file replaces its former precise representation.
        # Summary-only files in the partial graph do not erase an existing
        # precise slice and must be costed from the previous graph.
        source = (
            rebuilt
            if path in rebuilt_precise_files or path in touched
            else previous
        )
        if path not in source.file_index:
            continue
        file_nodes, file_edges, file_functions = _file_cost(source, path)
        fits = (
            len(admitted) < budget.max_files
            and nodes + file_nodes <= budget.max_nodes
            and edges + file_edges <= budget.max_edges
            and functions + file_functions <= budget.max_functions
        )
        if not fits:
            deferred.append(path)
            continue
        admitted.append(path)
        nodes += file_nodes
        edges += file_edges
        functions += file_functions
    if deferred:
        budget.truncated_reason = budget.truncated_reason or "ACTIVE_SLICE_LIMIT"
    return tuple(admitted), tuple(deferred)


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
    admitted_files, deferred_files = _admit_active_files(
        previous, rebuilt, partial, touched, context_requests, budget
    )
    admitted_set = set(admitted_files)
    rebuilt_precise_files = _precise_files(rebuilt)
    invalidated_previous_files = (
        set(previous.file_index) - admitted_set
    ) | set(touched) | rebuilt_precise_files
    old_invalidated_nodes = {
        node_id
        for path in invalidated_previous_files
        for node_id in previous.file_index.get(path, ())
    }
    retained_nodes = {
        node_id: node for node_id, node in previous.nodes.items()
        if node_id not in old_invalidated_nodes
        and (
            not node.attributes.get("file")
            or str(node.attributes.get("file")) in admitted_set
        )
    }
    active_paths = admitted_set
    source_hash = content_hash({
        path: repository_index.source_hashes.get(path, "CHANGED")
        for path in sorted(active_paths)
    })
    merged = ProgramGraph(
        repository_root=str(repository_root.resolve()), source_hash=source_hash,
        version=previous.version + 1,
    )
    for node in retained_nodes.values():
        if len(merged.nodes) >= budget.max_nodes:
            budget.truncated_reason = budget.truncated_reason or "NODE_LIMIT"
            break
        merged.index_node(node)
    for edge in previous.edges.values():
        if len(merged.edges) >= budget.max_edges:
            budget.truncated_reason = budget.truncated_reason or "EDGE_LIMIT"
            break
        if all(node_id in merged.nodes for node_id in edge.source_ids + edge.target_ids):
            merged.add_edge(edge)
    for node in rebuilt.nodes.values():
        file_name = str(node.attributes.get("file", ""))
        if file_name and file_name not in admitted_set:
            continue
        if len(merged.nodes) >= budget.max_nodes:
            budget.truncated_reason = budget.truncated_reason or "NODE_LIMIT"
            break
        if node.node_id not in merged.nodes:
            merged.index_node(node)
    for edge in rebuilt.edges.values():
        if len(merged.edges) >= budget.max_edges:
            budget.truncated_reason = budget.truncated_reason or "EDGE_LIMIT"
            break
        if edge.edge_id not in merged.edges and all(node_id in merged.nodes for node_id in edge.source_ids + edge.target_ids):
            merged.add_edge(edge)
    invalidated_set = set(invalidated_previous_files)
    for mapping_name in ("cfgs", "protocol_operations", "path_classes", "frontiers"):
        target = getattr(merged, mapping_name)
        for key, value in getattr(previous, mapping_name).items():
            if _references_touched(
                value, mapping_name, previous, invalidated_set
            ):
                continue
            if _derived_record_valid(value, mapping_name, merged):
                target[key] = value
        # Rebuilt derived records are part of the incremental slice just as
        # much as rebuilt nodes and edges.  Merge them under their own mapping
        # after the retained records so touched callables replace stale CFG,
        # protocol and path-class summaries.
        for key, value in getattr(rebuilt, mapping_name).items():
            if _derived_record_valid(value, mapping_name, merged):
                target[key] = value
    if deferred_files or budget.truncated_reason:
        merged.add_frontier(Frontier(
            frontier_id=stable_id(
                "program-frontier", "ANALYSIS_TRUNCATED",
                admitted_files, deferred_files, budget.truncated_reason,
            ),
            kind="ANALYSIS_TRUNCATED",
            owner_id=merged.graph_kind,
            reason=(
                "cumulative active Program Graph reached its file/function/node/edge budget; "
                f"deferred {len(deferred_files)} files"
            ),
            resolution_action="replace lower-priority active context or request a targeted slice",
            hard=False,
            evidence_ids=(),
        ))
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
    merged.build_stats.update({
        "precise_file_count": len(merged.file_index),
        "precise_function_count": len(merged.cfgs),
        "deferred_active_file_count": len(deferred_files),
    })
    rebuilt_file_ids = tuple(
        path for path in admitted_files if path in rebuilt.file_index
    )
    build_record = replace(
        partial,
        analyzed_files=rebuilt_file_ids,
        analyzed_callable_names=tuple(sorted(
            str(merged.nodes[cfg.callable_id].attributes.get(
                "qualified_name", merged.nodes[cfg.callable_id].label
            ))
            for cfg in merged.cfgs.values()
            if cfg.callable_id in merged.nodes
        )),
        deferred_files=tuple(sorted(set(
            partial.deferred_files + deferred_files
        ))),
        truncated_reason=budget.truncated_reason,
    )
    return ProgramGraphDeltaResult(
        graph=merged,
        added_node_ids=tuple(sorted(new_nodes.keys() - old_nodes.keys())),
        removed_node_ids=tuple(sorted(old_nodes.keys() - new_nodes.keys())),
        modified_node_ids=modified,
        added_edge_ids=tuple(sorted(new_edges.keys() - old_edges.keys())),
        removed_edge_ids=tuple(sorted(old_edges.keys() - new_edges.keys())),
        rebuilt_files=rebuilt_file_ids,
        retained_file_hashes={
            path: digest for path, digest in repository_index.source_hashes.items()
            if path in previous.file_index
            and path in admitted_set
            and path not in touched
        },
        build=build_record,
    )
