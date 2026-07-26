from __future__ import annotations

from reachpatch.models.graph import GraphEdge, GraphNode, TypedMultiGraph


def make_graph() -> TypedMultiGraph:
    graph = TypedMultiGraph(graph_kind="test")
    for label in "abcd":
        graph.add_node(GraphNode.create("symbol", label, identity=label))
    ids = {node.label: node.node_id for node in graph}
    for source, target, kind in [
        ("a", "b", "calls"),
        ("b", "c", "data_flow"),
        ("c", "b", "control_flow"),
        ("c", "d", "returns"),
    ]:
        graph.add_edge(GraphEdge.create(kind, [ids[source]], [ids[target]]))
    return graph


def test_graph_reachability_path_and_scc_round_trip():
    graph = make_graph()
    ids = {node.label: node.node_id for node in graph}
    assert graph.reachable([ids["a"]]) == set(ids.values())
    nodes, edges = graph.shortest_path(ids["a"], ids["d"])
    assert nodes == [ids[label] for label in "abcd"]
    assert len(edges) == 3
    assert (tuple(sorted((ids["b"], ids["c"])))) in graph.strongly_connected_components()

    raw = graph.to_dict()
    restored = TypedMultiGraph.from_dict(raw)
    assert restored.to_dict() == raw


def test_iterative_scc_preserves_filtered_hyperedge_semantics():
    graph = TypedMultiGraph(graph_kind="filtered-hypergraph")
    ids = {}
    for label in "abcde":
        node = GraphNode.create("symbol", label, identity=label)
        graph.add_node(node)
        ids[label] = node.node_id
    graph.add_edge(GraphEdge.create(
        "path", [ids["a"], ids["b"]], [ids["c"], ids["d"]]
    ))
    graph.add_edge(GraphEdge.create("path", [ids["c"]], [ids["a"]]))
    graph.add_edge(GraphEdge.create("ignored", [ids["d"]], [ids["b"]]))
    graph.add_edge(GraphEdge.create("path", [ids["e"]], [ids["e"]]))

    components = graph.strongly_connected_components(
        edge_predicate=lambda edge: edge.kind == "path"
    )

    assert set(components) == {
        tuple(sorted((ids["a"], ids["c"]))),
        (ids["b"],),
        (ids["d"],),
        (ids["e"],),
    }


def test_forbidden_node_exposes_bypass_absence():
    graph = make_graph()
    ids = {node.label: node.node_id for node in graph}
    assert graph.shortest_path(
        ids["a"], ids["d"], forbidden_nodes={ids["c"]}
    ) is None


def test_sorted_adjacency_cache_is_invalidated_by_edge_updates():
    graph = TypedMultiGraph(graph_kind="cache-test")
    for label in ("a", "b", "c"):
        graph.add_node(GraphNode.create("symbol", label))
    ids = {node.label: node.node_id for node in graph}

    first = GraphEdge.create("flow", [ids["a"]], [ids["b"]])
    graph.add_edge(first)
    assert [edge.edge_id for edge in graph.outgoing(ids["a"])] == [first.edge_id]

    second = GraphEdge.create("flow", [ids["a"]], [ids["c"]])
    graph.add_edge(second)
    assert [edge.edge_id for edge in graph.outgoing(ids["a"])] == sorted(
        [first.edge_id, second.edge_id]
    )
    graph.remove_edge(first.edge_id)
    assert [edge.edge_id for edge in graph.outgoing(ids["a"])] == [second.edge_id]
