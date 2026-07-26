from __future__ import annotations

from reachpatch.binding_graph.models import BindingClosure, BindingGraph
from reachpatch.requirement_graph.models import RequirementGraph


def compute_binding_path_closure(
    requirement_graph: RequirementGraph,
    binding_graph: BindingGraph,
) -> BindingClosure:
    feasible_ids = {item.path_obligation_id for item in requirement_graph.feasible_path_obligations()}
    missing = tuple(sorted(
        path_id for path_id in feasible_ids if not binding_graph.by_path_obligation.get(path_id)
    ))
    duplicates = tuple(sorted(
        path_id
        for path_id, unit_ids in binding_graph.by_path_obligation.items()
        if path_id in feasible_ids and len(unit_ids) != 1
    ))
    stale = tuple(sorted(
        unit.unit_id
        for unit in binding_graph.units.values()
        if unit.requirement_graph_hash != binding_graph.requirement_graph_hash
        or unit.program_graph_hash != binding_graph.program_graph_hash
    ))
    blocked = tuple(sorted(
        unit.unit_id for unit in binding_graph.units.values() if unit.status != "READY"
    ))
    hard_frontiers = tuple(sorted(
        frontier.frontier_id
        for frontier in binding_graph.frontiers.values()
        if frontier.hard and frontier.status == "OPEN"
    ))
    ready_weight = sum(
        requirement_graph.leaves[unit.leaf_id].weight
        for unit in binding_graph.units.values()
        if unit.status == "READY"
    )
    denominator = sum(
        requirement_graph.leaves[item.leaf_id].weight
        for item in requirement_graph.feasible_path_obligations()
    ) + len(hard_frontiers)
    ratio = ready_weight / denominator if denominator else (1.0 if not feasible_ids else 0.0)
    return BindingClosure(
        closed=not missing and not duplicates and not stale and not blocked and not hard_frontiers,
        ready_ratio=ratio,
        missing_path_obligation_ids=missing,
        duplicate_path_obligation_ids=duplicates,
        stale_unit_ids=stale,
        blocked_unit_ids=blocked,
        frontier_ids=hard_frontiers,
        graph_hash=binding_graph.graph_hash(),
    )
