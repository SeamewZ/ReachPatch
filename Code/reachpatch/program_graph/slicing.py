from __future__ import annotations

from collections import defaultdict, deque
from fnmatch import fnmatch
from typing import Iterable

from reachpatch.models.base import stable_id
from reachpatch.program_graph.impact import impact_cone
from reachpatch.program_graph.models import CausalRepairCut, ImpactCone, ProgramGraph

FORWARD_SLICE_RELATIONS = {
    "control_flow", "control_dependency", "calls", "may_call", "parameter_flow",
    "return_flow", "exception_flow", "data_flow", "def_use", "field_flow",
    "state_read", "state_write", "dispatch", "protocol_selected", "protocol_candidate",
}
BACKWARD_SLICE_RELATIONS = FORWARD_SLICE_RELATIONS | {
    "alias", "exports", "registers", "inheritance", "override", "triggers",
}
MODIFIABLE_KINDS = {
    "function", "method", "property", "statement", "expression", "branch", "loop",
    "call_site", "return", "exception", "field", "local", "protocol_operation",
}


def _modifiable(
    graph: ProgramGraph,
    node_id: str,
    forbidden_patterns: Iterable[str],
) -> bool:
    node = graph.nodes[node_id]
    file_name = str(node.attributes.get("file", ""))
    if node.kind not in MODIFIABLE_KINDS:
        return False
    if node_id in graph.test_node_ids or node.kind in {"test", "assertion", "fixture"}:
        return False
    if file_name.startswith("<") or any(part in {"tests", "test", "generated", "vendor"} for part in file_name.split("/")):
        return False
    return not any(fnmatch(file_name, pattern) for pattern in forbidden_patterns)


def causal_repair_cut(
    graph: ProgramGraph,
    entrypoint_ids: Iterable[str],
    observation_ids: Iterable[str],
    *,
    unit_slices: dict[str, set[str]] | None = None,
    forbidden_patterns: Iterable[str] = (),
    include_insertion_boundaries: bool = True,
    impact_cache: dict[tuple[str, ...], ImpactCone] | None = None,
    basis_cache: dict[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], dict[str, object]] | None = None,
    node_metric_cache: dict[str, tuple[int, int]] | None = None,
) -> CausalRepairCut:
    entries = tuple(sorted(set(entrypoint_ids)))
    observations = tuple(sorted(set(observation_ids)))
    if not entries or not observations:
        raise ValueError("causal repair cut requires entrypoints and observations")
    forbidden = tuple(sorted(set(forbidden_patterns)))
    basis_key = (entries, observations, forbidden)
    basis = basis_cache.get(basis_key) if basis_cache is not None else None
    if basis is None:
        forward = graph.reachable(
            entries,
            edge_predicate=lambda edge: edge.kind in FORWARD_SLICE_RELATIONS,
        )
        backward = graph.reachable(
            observations,
            direction="backward",
            edge_predicate=lambda edge: edge.kind in BACKWARD_SLICE_RELATIONS,
        )
        intersection = forward & backward
        legal = {
            node_id for node_id in intersection
            if _modifiable(graph, node_id, forbidden)
        }
        excluded = intersection - legal

        # One reverse BFS is exactly equivalent to taking the minimum of a
        # separate forward shortest-path query from every legal node.
        distance_by_node: dict[str, int] = {}
        pending = set(legal)
        queue = deque((node_id, 0) for node_id in observations)
        visited: set[str] = set()
        while queue and pending:
            node_id, distance = queue.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            if node_id in pending:
                distance_by_node[node_id] = distance
                pending.remove(node_id)
            for edge in graph.incoming(node_id):
                if edge.kind not in FORWARD_SLICE_RELATIONS:
                    continue
                for source_id in edge.source_ids:
                    if source_id not in visited:
                        queue.append((source_id, distance + 1))
        basis = {
            "forward": frozenset(forward),
            "backward": frozenset(backward),
            "intersection": frozenset(intersection),
            "legal": frozenset(legal),
            "excluded": frozenset(excluded),
            "distance_by_node": distance_by_node,
        }
        if basis_cache is not None:
            basis_cache[basis_key] = basis
    forward = set(basis["forward"])
    backward = set(basis["backward"])
    intersection = set(basis["intersection"])
    legal = set(basis["legal"])
    excluded = set(basis["excluded"])
    distance_by_node = dict(basis["distance_by_node"])
    units = unit_slices or {"unit": intersection}
    coverage = {
        node_id: sorted(unit_id for unit_id, unit_slice in units.items() if node_id in unit_slice)
        for node_id in legal
    }
    ranked = []
    for node_id in legal:
        node = graph.nodes[node_id]
        metrics = node_metric_cache.get(node_id) if node_metric_cache is not None else None
        if metrics is None:
            blast = len(graph.reachable(
                [node_id],
                edge_predicate=lambda edge: edge.kind in FORWARD_SLICE_RELATIONS,
                max_nodes=1000,
            ))
            preservation_conflict = len(
                graph.reachable(
                    [node_id],
                    edge_predicate=lambda edge: edge.kind == "test_coverage",
                    max_nodes=1000,
                )
                & graph.test_node_ids
            )
            metrics = (blast, preservation_conflict)
            if node_metric_cache is not None:
                node_metric_cache[node_id] = metrics
        blast, preservation_conflict = metrics
        distance = distance_by_node.get(node_id, 1000)
        score = len(coverage[node_id]) * 10.0 - distance - 0.05 * blast - 3.0 * preservation_conflict
        ranked.append({
            "node_id": node_id,
            "score": score,
            "covered_unit_ids": coverage[node_id],
            "distance_to_observation": distance,
            "blast": blast,
            "preservation_conflict": preservation_conflict,
            "kind": node.kind,
        })
    ranked.sort(key=lambda item: (-float(item["score"]), str(item["node_id"])))
    score_by_node = {
        str(item["node_id"]): float(item["score"])
        for item in ranked
    }

    uncovered = set(units)
    selected: list[str] = []
    while uncovered:
        best = max(
            (node_id for node_id in legal if set(coverage[node_id]) & uncovered),
            key=lambda node_id: (
                len(set(coverage[node_id]) & uncovered),
                score_by_node[node_id],
                node_id,
            ),
            default=None,
        )
        if best is None:
            break
        selected.append(best)
        uncovered -= set(coverage[best])
    if not selected and ranked:
        selected.append(str(ranked[0]["node_id"]))
    boundaries = set()
    if include_insertion_boundaries:
        for node_id in selected:
            boundaries.update(
                source
                for edge in graph.incoming(node_id, {"control_flow", "calls", "data_flow"})
                for source in edge.source_ids
                if _modifiable(graph, source, forbidden_patterns)
            )
    cone_sources = tuple(sorted(selected or legal))
    cone = impact_cache.get(cone_sources) if impact_cache is not None else None
    if cone is None:
        cone = impact_cone(graph, cone_sources)
        if impact_cache is not None:
            impact_cache[cone_sources] = cone
    return CausalRepairCut(
        cut_id=stable_id("causal-cut", entries, observations, selected, graph.source_hash),
        node_ids=tuple(selected),
        ranked_nodes=tuple(ranked),
        insertion_boundary_ids=tuple(sorted(boundaries)),
        covered_unit_ids=tuple(sorted(set(units) - uncovered)),
        excluded_node_ids=tuple(sorted(excluded)),
        impact_cone_node_ids=cone.downstream_node_ids,
        proof={
            "forward_slice_size": len(forward),
            "backward_slice_size": len(backward),
            "intersection_size": len(intersection),
            "uncovered_unit_ids": sorted(uncovered),
        },
    )


def component_repair_frontier(
    cuts_by_unit: dict[str, CausalRepairCut],
    *,
    node_costs: dict[str, float] | None = None,
) -> tuple[str, ...]:
    costs = node_costs or {}
    candidates: dict[str, set[str]] = defaultdict(set)
    for unit_id, cut in cuts_by_unit.items():
        for node_id in cut.node_ids:
            candidates[node_id].add(unit_id)
    uncovered = set(cuts_by_unit)
    selected: list[str] = []
    while uncovered:
        best = max(
            (node_id for node_id, units in candidates.items() if units & uncovered),
            key=lambda node_id: (
                len(candidates[node_id] & uncovered) / max(costs.get(node_id, 1.0), 0.001),
                len(candidates[node_id] & uncovered),
                node_id,
            ),
            default=None,
        )
        if best is None:
            raise ValueError(f"no repair locus covers units {sorted(uncovered)}")
        selected.append(best)
        uncovered -= candidates[best]
    return tuple(selected)
