from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from reachpatch.models.base import SerializableRecord, content_hash
from reachpatch.models.evidence import ActualDiff
from reachpatch.models.graphs import GraphBudget, ProgramGraph, RequirementGraph
from .local_builder import build_initial_program_graph


@dataclass(frozen=True, slots=True)
class ProgramSliceBudget(SerializableRecord):
    max_files: int = 30
    max_symbols: int = 800
    max_static_edges: int = 4000
    initial_caller_depth: int = 1
    initial_callee_depth: int = 1
    max_expansion_depth: int = 3
    wall_seconds: float = 45.0


@dataclass(frozen=True, slots=True)
class SliceFrontier(SerializableRecord):
    reason: str
    symbols: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    depth: int = 0


@dataclass(frozen=True, slots=True)
class ActiveProgramSlice(SerializableRecord):
    graph: ProgramGraph
    frontier: tuple[SliceFrontier, ...] = ()
    expanded_depth: int = 0

    @property
    def nodes(self):
        return self.graph.nodes

    @property
    def edges(self):
        return self.graph.edges

    @property
    def path_classes(self):
        return self.graph.path_classes

    def graph_hash(self) -> str:
        return self.graph.graph_hash()


def build_active_program_slice(
    repo_root: Path, diff: str | ActualDiff, requirement_graph: RequirementGraph,
    traces: Sequence[Any], public_checks: Sequence[Any],
    previous_slice: ActiveProgramSlice | None, budget: ProgramSliceBudget,
) -> ActiveProgramSlice:
    """Build a bounded graph around the current diff and observed routes."""
    actual = diff if isinstance(diff, ActualDiff) else ActualDiff(diff, content_hash(diff), (), (), ())
    relevant = tuple(dict.fromkeys(
        str(leaf.operation) for leaf in requirement_graph.leaves.values() if leaf.operation
    ))
    graph_budget = GraphBudget(
        max_files=budget.max_files, max_nodes=budget.max_symbols,
        max_edges=budget.max_static_edges, direct_caller_depth=budget.initial_caller_depth,
    )
    if previous_slice is not None:
        from reachpatch.program_graph.incremental import update_program_graph_after_diff
        graph = update_program_graph_after_diff(
            previous_slice.graph, repo_root, actual, tuple(
                item.patched if hasattr(item, "patched") else item for item in traces
            ), (), graph_budget, tuple(public_checks),
        ).graph
    else:
        graph = build_initial_program_graph(
            repo_root, "", actual, tuple(public_checks), graph_budget,
            relevant_symbols=relevant,
        )
    nodes = dict(list(sorted(graph.nodes.items()))[:budget.max_symbols])
    edges = {key: edge for key, edge in sorted(graph.edges.items()) if edge.source_id in nodes and edge.target_id in nodes}
    edges = dict(list(edges.items())[:budget.max_static_edges])
    paths = {key: path for key, path in graph.path_classes.items() if all(node_id in nodes for node_id in path.node_ids)}
    files = tuple(sorted({node.path for node in nodes.values()}))
    frontier: list[SliceFrontier] = []
    if len(files) >= budget.max_files or len(nodes) >= budget.max_symbols or len(edges) >= budget.max_static_edges:
        frontier.append(SliceFrontier("ACTIVE_SLICE_BUDGET", tuple(relevant), files, budget.initial_caller_depth))
    trimmed = ProgramGraph(
        patch_hash=graph.patch_hash, base_commit=graph.base_commit, nodes=nodes, edges=edges,
        path_classes=paths, file_hashes={key: value for key, value in graph.file_hashes.items() if key in files},
        symbol_index={key: tuple(value for value in vals if value in nodes) for key, vals in graph.symbol_index.items()},
        causal_cuts={key: value for key, value in graph.causal_cuts.items() if value.observation_node_id in nodes},
        impact_cone=graph.impact_cone, frontier_requests=graph.frontier_requests,
        files_reparsed=graph.files_reparsed, symbols_expanded=graph.symbols_expanded, cache_hits=graph.cache_hits,
    )
    return ActiveProgramSlice(trimmed, tuple(frontier), budget.initial_caller_depth)
