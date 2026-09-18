from types import SimpleNamespace

from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    CheckpointState,
    DynamicReachAvoidGraph,
    GraphNodeKind,
    materialize_graph_guided_challenges,
)


def test_changed_branch_generates_graph_challenges():
    graph = DynamicReachAvoidGraph()
    branch = graph.add_node(GraphNodeKind.BRANCH, node_id="b", file="pkg.py", symbol="target", line_start=2, line_end=2, source_span="if value == 0", metadata={"predicate": "value == 0", "oracle": {"comparator": "EQUALS", "expected": 1}}, authority="B")
    checkpoint = CheckpointState("cp", None, "diff", "hash", causal_cut_ids=(branch.node_id,))
    observations = (SimpleNamespace(command=("python", "-c", "print(1)")),)
    cells = materialize_graph_guided_challenges(graph=graph, repo_root=None, checkpoint=checkpoint, observations=observations)
    assert cells
    assert all(cell.source_branch_id == "b" for cell in cells)
    # Authority on a branch/original input does not authorize adjacent inputs.
    assert all(cell.status == "EXPLORATION_ONLY" for cell in cells)


def test_challenge_without_oracle_is_exploratory():
    graph = DynamicReachAvoidGraph()
    branch = graph.add_node(
        GraphNodeKind.BRANCH, node_id="b", file="pkg.py", symbol="target",
        line_start=2, line_end=2, metadata={"predicate": "if value"}, authority="B",
    )
    checkpoint = CheckpointState("cp", None, "diff", "hash", causal_cut_ids=(branch.node_id,))
    cells = materialize_graph_guided_challenges(
        graph=graph, repo_root=None, checkpoint=checkpoint,
        observations=(SimpleNamespace(command=("python", "-c", "print(1)")),),
    )
    assert cells and all(cell.status == "EXPLORATION_ONLY" for cell in cells)
