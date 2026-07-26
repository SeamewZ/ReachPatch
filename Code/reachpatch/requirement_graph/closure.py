from __future__ import annotations

from reachpatch.models.enums import LedgerStatus
from reachpatch.requirement_graph.models import RequirementClosure, RequirementGraph


_SUCCESSFUL_LEDGER = {
    LedgerStatus.ENUMERATED,
    LedgerStatus.PROVED_INFEASIBLE,
    LedgerStatus.PRESERVATION_ONLY,
}


def requirement_path_closure(graph: RequirementGraph) -> RequirementClosure:
    feasible_by_leaf: dict[str, int] = {leaf_id: 0 for leaf_id in graph.leaves}
    for obligation in graph.feasible_path_obligations():
        feasible_by_leaf[obligation.leaf_id] = feasible_by_leaf.get(obligation.leaf_id, 0) + 1
    missing = tuple(sorted(
        leaf.leaf_id
        for leaf in graph.hard_and_preservation_leaves()
        if feasible_by_leaf.get(leaf.leaf_id, 0) == 0
    ))
    nonterminal = tuple(sorted(
        record.ledger_id
        for record in graph.edge_ledger.values()
        if record.status not in _SUCCESSFUL_LEDGER
    ))
    hard_frontiers = tuple(sorted(
        frontier.frontier_id
        for frontier in graph.frontiers.values()
        if frontier.hard and frontier.status == "OPEN"
    ))
    numerator = sum(
        graph.leaves[item.leaf_id].weight
        for item in graph.feasible_path_obligations()
        if item.leaf_id in graph.leaves
    )
    denominator = numerator + sum(
        graph.leaves[frontier.owner_id].weight
        if frontier.owner_id in graph.leaves else 1.0
        for frontier in graph.frontiers.values()
        if frontier.status == "OPEN"
    )
    coverage = numerator / denominator if denominator else (1.0 if not graph.leaves else 0.0)
    return RequirementClosure(
        closed=not missing and not nonterminal and not hard_frontiers,
        path_coverage=coverage,
        missing_leaf_ids=missing,
        nonterminal_ledger_ids=nonterminal,
        frontier_ids=hard_frontiers,
        graph_hash=graph.to_dict()["graph_hash"],
    )
