from __future__ import annotations

from typing import Iterable

from reachpatch.models.base import stable_id
from reachpatch.program_graph.models import ImpactCone, ProgramGraph

IMPACT_RELATIONS = {
    "calls",
    "may_call",
    "return_flow",
    "exception_flow",
    "raises",
    "catches",
    "data_flow",
    "def_use",
    "alias",
    "field_flow",
    "state_read",
    "state_write",
    "dispatch",
    "protocol_selected",
    "protocol_candidate",
    "protocol_fallback",
    "serializes",
    "external_effect",
    "observes",
    "test_coverage",
}


def impact_cone(
    graph: ProgramGraph,
    source_node_ids: Iterable[str],
    *,
    max_nodes: int = 20000,
) -> ImpactCone:
    sources = tuple(sorted(set(source_node_ids)))
    missing = [node_id for node_id in sources if node_id not in graph.nodes]
    if missing:
        raise KeyError(f"impact sources missing: {missing}")
    downstream = graph.reachable(
        sources,
        edge_predicate=lambda edge: edge.kind in IMPACT_RELATIONS,
        max_nodes=max_nodes,
    )
    affected_edges = {
        edge.edge_id
        for node_id in downstream
        for edge in graph.outgoing(node_id)
        if edge.kind in IMPACT_RELATIONS and set(edge.target_ids) & downstream
    }
    callers = {
        source
        for node_id in downstream
        for edge in graph.incoming(node_id, {"calls", "may_call"})
        for source in edge.source_ids
    }
    consumers = {
        node_id
        for node_id in downstream
        if graph.nodes[node_id].kind in {
            "call_site", "branch", "protocol_operation", "external_interface", "observation_point"
        }
    }
    handlers = {
        node_id
        for node_id in downstream
        if graph.nodes[node_id].kind == "exception"
        or graph.incoming(node_id, {"catches", "exception_flow"})
    }
    serializers = {
        node_id
        for node_id in downstream
        if graph.incoming(node_id, {"serializes"}) or graph.outgoing(node_id, {"serializes"})
    }
    preservation_tests = downstream & graph.test_node_ids
    open_frontiers = {
        frontier.frontier_id
        for frontier in graph.frontiers.values()
        if frontier.status == "OPEN" and frontier.owner_id in downstream
    }
    return ImpactCone(
        cone_id=stable_id("impact-cone", graph.source_hash, sources, sorted(downstream)),
        source_node_ids=sources,
        downstream_node_ids=tuple(sorted(downstream)),
        caller_ids=tuple(sorted(callers)),
        consumer_ids=tuple(sorted(consumers)),
        exception_handler_ids=tuple(sorted(handlers)),
        serialization_ids=tuple(sorted(serializers)),
        preservation_test_ids=tuple(sorted(preservation_tests)),
        affected_edge_ids=tuple(sorted(affected_edges)),
        frontier_ids=tuple(sorted(open_frontiers)),
    )


def guarded_diff_influence_cone(
    base_graph: ProgramGraph,
    trial_graph: ProgramGraph,
    changed_node_ids: Iterable[str],
) -> ImpactCone:
    changed = set(changed_node_ids)
    base_sources = changed & base_graph.nodes.keys()
    trial_sources = changed & trial_graph.nodes.keys()
    base = impact_cone(base_graph, base_sources) if base_sources else None
    trial = impact_cone(trial_graph, trial_sources) if trial_sources else None
    if base is None and trial is None:
        raise ValueError("diff influence cone has no nodes in either graph version")
    cones = [cone for cone in (base, trial) if cone is not None]
    return ImpactCone(
        cone_id=stable_id("diff-cone", [cone.cone_id for cone in cones]),
        source_node_ids=tuple(sorted({item for cone in cones for item in cone.source_node_ids})),
        downstream_node_ids=tuple(sorted({item for cone in cones for item in cone.downstream_node_ids})),
        caller_ids=tuple(sorted({item for cone in cones for item in cone.caller_ids})),
        consumer_ids=tuple(sorted({item for cone in cones for item in cone.consumer_ids})),
        exception_handler_ids=tuple(sorted({item for cone in cones for item in cone.exception_handler_ids})),
        serialization_ids=tuple(sorted({item for cone in cones for item in cone.serialization_ids})),
        preservation_test_ids=tuple(sorted({item for cone in cones for item in cone.preservation_test_ids})),
        affected_edge_ids=tuple(sorted({item for cone in cones for item in cone.affected_edge_ids})),
        frontier_ids=tuple(sorted({item for cone in cones for item in cone.frontier_ids})),
    )
