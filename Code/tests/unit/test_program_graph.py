from __future__ import annotations

from pathlib import Path

from reachpatch.models.base import content_hash
from reachpatch.models.graph import GraphEdge, GraphNode, TypedMultiGraph
from reachpatch.program_graph import PythonProgramGraphBuilder
from reachpatch.program_graph.analysis import CFGBuilder, DefUseAnalyzer
from reachpatch.program_graph.builder import _iter_python_files, _repository_source_hash
from reachpatch.program_graph.entrypoints import recover_entrypoints
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.program_graph.paths import summarize_path_classes, summarize_path_topology
from reachpatch.program_graph.protocols import ProtocolAnalyzer
from reachpatch.program_graph.slicing import causal_repair_cut


FIXTURE = Path(__file__).parents[1] / "fixtures" / "simple_repo"


def _uncached_program_hash(graph):
    return content_hash({
        "graph_kind": graph.graph_kind,
        "version": graph.version,
        "repository_root": graph.repository_root,
        "source_hash": graph.source_hash,
        "node_hashes": [
            content_hash(graph.nodes[node_id].to_dict()) for node_id in sorted(graph.nodes)
        ],
        "edge_hashes": [
            content_hash(graph.edges[edge_id].to_dict()) for edge_id in sorted(graph.edges)
        ],
        "cfg_hashes": [content_hash(graph.cfgs[key].to_dict()) for key in sorted(graph.cfgs)],
        "protocol_hashes": [
            content_hash(graph.protocol_operations[key].to_dict())
            for key in sorted(graph.protocol_operations)
        ],
        "path_class_hashes": [
            content_hash(graph.path_classes[key].to_dict())
            for key in sorted(graph.path_classes)
        ],
        "frontier_hashes": [
            content_hash(graph.frontiers[key].to_dict()) for key in sorted(graph.frontiers)
        ],
        "external_surface_ids": sorted(graph.external_surface_ids),
        "test_node_ids": sorted(graph.test_node_ids),
        "observation_node_ids": sorted(graph.observation_node_ids),
    })


def test_python_frontend_builds_behavioral_relations_and_protocol_ir():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    kinds = {node.kind for node in graph.nodes.values()}
    relations = {edge.kind for edge in graph.edges.values()}
    protocol_kinds = {operation.kind for operation in graph.protocol_operations.values()}

    assert {
        "module", "class", "function", "method", "branch", "parameter",
        "local", "field", "container_shape", "exception", "protocol_operation",
        "test", "assertion", "return",
    } <= kinds
    assert {
        "containment", "calls", "control_flow", "def_use", "data_flow",
        "return_flow", "exception_flow", "state_read", "state_write",
        "registers", "protocol_candidate", "test_coverage",
    } <= relations
    assert {"binary", "comparison", "truthiness", "indexing"} <= protocol_kinds


def test_entrypoint_requires_forward_observation_and_cut_excludes_tests():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    seed = graph.resolve_symbol("pkg.api.normalize")[0]
    returns = {
        node_id
        for node_id in graph.reachable([seed], max_nodes=1000)
        if graph.nodes[node_id].kind in {"return", "exception"}
    }
    result = recover_entrypoints([seed], graph, observation_ids=returns)
    assert result.paths
    assert result.path_classes

    cut = causal_repair_cut(
        graph,
        {path.entrypoint_id for path in result.paths},
        returns,
        unit_slices={"normalize-unit": {node for path in result.paths for node in path.node_ids}},
    )
    assert cut.node_ids
    assert all(graph.nodes[node_id].kind not in {"test", "assertion"} for node_id in cut.node_ids)
    assert "normalize-unit" in cut.covered_unit_ids


def test_source_text_is_recovered_from_span_instead_of_retained_on_every_node():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    return_node = next(
        node for node in graph.nodes.values()
        if node.kind == "return" and graph.source_segment(node.node_id).startswith("return ")
    )

    assert "source" not in return_node.attributes
    assert graph.source_segment(return_node.node_id) == f"return {return_node.label}"


def test_merkle_program_hash_is_cached_and_invalidated_by_graph_updates():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    initial = graph.program_hash()
    assert initial == _uncached_program_hash(graph)

    owner_id = next(iter(graph.nodes))
    graph.create_frontier(
        "TEST_FRONTIER",
        owner_id,
        "exercise hash invalidation",
        "resolve in test",
        hard=False,
    )

    updated = graph.program_hash()
    assert updated != initial
    assert updated == _uncached_program_hash(graph)
    assert graph.program_hash() == updated
    assert graph.to_dict()["graph_hash"] == updated

    node_id = next(iter(graph.nodes))
    attributes = dict(graph.nodes[node_id].attributes)
    attributes["observed"] = True
    graph.update_node_attributes(node_id, attributes)
    node_updated = graph.program_hash()
    assert node_updated != updated
    assert node_updated == _uncached_program_hash(graph)


def test_suffix_resolution_and_hyperedges_preserve_all_conservative_targets():
    graph = PythonProgramGraphBuilder(FIXTURE).build()

    short_matches = set(graph.resolve_symbol("normalize"))
    exact_matches = set(graph.resolve_symbol("pkg.api.normalize"))
    assert exact_matches <= short_matches
    assert all(
        str(graph.nodes[node_id].attributes.get("qualified_name", ""))
        .endswith(".normalize")
        for node_id in short_matches
    )
    for operation in graph.protocol_operations.values():
        represented = {
            target_id
            for edge in graph.outgoing(operation.operation_id, {"protocol_candidate"})
            for target_id in edge.target_ids
        }
        assert represented == set(operation.candidate_target_ids)

    reflected = next(
        operation
        for operation in graph.protocol_operations.values()
        if operation.kind == "binary" and "__radd__" in operation.candidate_method_names
    )
    candidate_edges = graph.outgoing(
        reflected.operation_id, {"protocol_candidate"}
    )
    assert len(candidate_edges) <= 2


def test_symbol_resolution_cache_is_exact_and_invalidated_by_matching_tail():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    before = graph.resolve_symbol("normalize")
    assert before == graph.resolve_symbol("normalize")
    assert graph.resolve_symbol("new_protocol_method") == []

    added = GraphNode.create(
        "method",
        "new_protocol_method",
        identity="cache-invalidation-target",
        attributes={
            "qualified_name": "pkg.Dynamic.new_protocol_method",
            "file": "pkg/dynamic.py",
        },
    )
    graph.index_node(added)

    assert graph.resolve_symbol("normalize") == before
    assert graph.resolve_symbol("new_protocol_method") == [added.node_id]


def test_protocol_candidate_tuples_are_interned_without_changing_edges():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    by_candidates = {}
    shared = False
    for operation in graph.protocol_operations.values():
        previous = by_candidates.get(operation.candidate_target_ids)
        if previous is not None and previous.candidate_target_ids:
            assert operation.candidate_target_ids is previous.candidate_target_ids
            shared = True
            break
        by_candidates[operation.candidate_target_ids] = operation
    assert shared


def test_streaming_builder_releases_ast_and_deferred_fact_state():
    builder = PythonProgramGraphBuilder(FIXTURE)
    graph = builder.build()

    assert graph.nodes and graph.edges
    assert builder.analyses == []
    assert builder.call_facts == []
    assert builder.inheritance_facts == []
    assert builder.registration_facts == []
    assert builder.protocol_facts == []


def test_streaming_graph_is_hash_identical_to_retained_ast_multipass_graph():
    root = FIXTURE.resolve()
    retained_builder = PythonProgramGraphBuilder(root)
    paths = _iter_python_files(root, retained_builder.excludes)
    retained = ProgramGraph(
        repository_root=str(root),
        source_hash=_repository_source_hash(root, paths),
    )
    for path in paths:
        analysis = retained_builder._analyze_path(
            retained, path, declarations_only=False
        )
        if analysis is not None:
            retained_builder.analyses.append(analysis)
    retained_builder._import_export_pass(retained)
    retained_builder._points_to_pass(retained)
    for analysis in retained_builder.analyses:
        CFGBuilder(retained, analysis).build()
        DefUseAnalyzer(retained, analysis).run()
        retained_builder._collect_call_facts(retained, analysis)
    retained_builder._materialize_call_flows(retained)
    retained_builder._inheritance_dispatch_pass(retained)
    retained_builder._registration_external_pass(retained)
    for analysis in retained_builder.analyses:
        ProtocolAnalyzer(retained, analysis).run()
    retained_builder._property_descriptor_pass(retained)
    retained_builder._mark_test_observations(retained)
    retained_builder._load_package_entrypoints(retained)

    streamed = PythonProgramGraphBuilder(root).build()

    assert streamed.program_hash() == retained.program_hash()
    assert streamed.to_dict() == retained.to_dict()


def test_path_topology_cache_is_reused_and_invalidated_by_graph_mutation():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    initial = summarize_path_topology(graph)
    assert summarize_path_topology(graph) is initial

    owner_id = next(iter(graph.nodes))
    graph.add_relation("observes", [owner_id], [owner_id])
    updated = summarize_path_topology(graph)
    assert updated is not initial


def test_hyperedges_are_pairwise_relation_and_traversal_equivalent():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    frontier_snapshot = dict(graph.frontiers)
    pairwise = TypedMultiGraph(graph_kind="pairwise-projection")
    for node in graph.nodes.values():
        pairwise.add_node(node)
    for edge in graph.edges.values():
        for source_index, source_id in enumerate(edge.source_ids):
            for target_index, target_id in enumerate(edge.target_ids):
                pairwise.add_edge(GraphEdge(
                    edge_id=f"{edge.edge_id}:{source_index}:{target_index}",
                    kind=edge.kind,
                    source_ids=(source_id,),
                    target_ids=(target_id,),
                    condition=edge.condition,
                    confidence=edge.confidence,
                    attributes=edge.attributes,
                    provenance_ids=edge.provenance_ids,
                ))

    def pair_relations(candidate):
        return {
            (edge.kind, source_id, target_id, edge.condition)
            for edge in candidate.edges.values()
            for source_id in edge.source_ids
            for target_id in edge.target_ids
        }

    assert pair_relations(graph) == pair_relations(pairwise)
    for node_id in graph.nodes:
        assert graph.successors(node_id) == pairwise.successors(node_id)
        assert graph.predecessors(node_id) == pairwise.predecessors(node_id)

    traversal_seeds = sorted({
        source_id
        for edge in graph.edges.values()
        for source_id in edge.source_ids
    })[::17]
    for node_id in traversal_seeds:
        assert graph.reachable([node_id]) == pairwise.reachable([node_id])

    for operation in graph.protocol_operations.values():
        projected_targets = {
            target_id
            for edge in pairwise.outgoing(operation.operation_id, {"protocol_candidate"})
            for target_id in edge.target_ids
        }
        assert projected_targets == set(operation.candidate_target_ids)
    assert graph.frontiers == frontier_snapshot


def test_scc_analysis_handles_paths_beyond_python_recursion_limit():
    graph = TypedMultiGraph(graph_kind="deep-scc-test")
    node_ids = []
    for index in range(2500):
        node = GraphNode.create("statement", str(index), identity=index)
        graph.add_node(node)
        node_ids.append(node.node_id)
    for source_id, target_id in zip(node_ids[:-1], node_ids[1:], strict=True):
        graph.add_edge(GraphEdge.create("flow", [source_id], [target_id]))

    components = graph.strongly_connected_components()

    assert len(components) == len(node_ids)
    assert {component[0] for component in components} == set(node_ids)


def test_path_class_default_budget_covers_long_acyclic_corridor():
    graph = ProgramGraph(repository_root=".", source_hash="long-corridor")
    node_ids = []
    for index in range(20001):
        node = GraphNode(f"corridor-{index:05d}", "statement", str(index))
        graph.add_node(node)
        node_ids.append(node.node_id)
    for index, (source_id, target_id) in enumerate(
        zip(node_ids[:-1], node_ids[1:], strict=True)
    ):
        graph.add_edge(GraphEdge(
            f"corridor-edge-{index:05d}",
            "control_flow",
            (source_id,),
            (target_id,),
        ))

    enumeration = summarize_path_classes(graph, node_ids[0], {node_ids[-1]})

    assert not enumeration.capped
    assert len(enumeration.path_classes) == 1
    assert enumeration.path_classes[0].node_ids == tuple(node_ids)
