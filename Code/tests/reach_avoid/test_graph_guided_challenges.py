from types import SimpleNamespace

from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    CheckpointState,
    DynamicReachAvoidGraph,
    GraphNodeKind,
    materialize_graph_guided_challenges,
)


def test_changed_branch_without_adapter_records_gap_not_challenge(tmp_path):
    graph = DynamicReachAvoidGraph()
    branch = graph.add_node(GraphNodeKind.BRANCH, node_id="b", file="pkg.py", symbol="target", line_start=2, line_end=2, source_span="if value == 0", metadata={"predicate": "value == 0", "oracle": {"comparator": "EQUALS", "expected": 1}}, authority="B")
    checkpoint = CheckpointState("cp", None, "diff", "hash", causal_cut_ids=(branch.node_id,))
    graph.register_checkpoint(checkpoint)
    observations = (SimpleNamespace(command=("python", "-c", "print(1)")),)
    cells = materialize_graph_guided_challenges(graph=graph, repo_root=tmp_path, checkpoint=checkpoint, observations=observations)
    assert not cells
    assert any(n.status == "MISSING_ADAPTER" for n in graph.nodes.values())
    assert graph.metrics["graph_generated_challenge_count"] == 0


def test_challenge_without_oracle_is_exploratory(tmp_path):
    graph = DynamicReachAvoidGraph()
    branch = graph.add_node(
        GraphNodeKind.BRANCH, node_id="b", file="pkg.py", symbol="target",
        line_start=2, line_end=2, metadata={"predicate": "if value"}, authority="B",
    )
    checkpoint = CheckpointState("cp", None, "diff", "hash", causal_cut_ids=(branch.node_id,))
    graph.register_checkpoint(checkpoint)
    graph.add_node(GraphNodeKind.OBLIGATION, node_id="check", metadata={
        "role": "TARGET", "target_symbols": ("target",), "command": ("python", "-c", "print(0)"),
        "input_recipe": {"variants": [{"command": ("python", "-c", "print(1)"), "input": 1}]}})
    cells = materialize_graph_guided_challenges(
        graph=graph, repo_root=tmp_path, checkpoint=checkpoint,
        observations=(SimpleNamespace(command=("python", "-c", "print(1)")),),
    )
    assert cells and all(cell.status == "EXPLORATION_ONLY" for cell in cells)
