from pathlib import Path
from types import SimpleNamespace

from reachpatch.models.execution import CheckStatus
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    DynamicGraphBudget,
    DynamicReachAvoidGraph,
    GraphEdgeKind,
    GraphNodeKind,
    CheckpointState,
    build_distinct_repair_hypotheses,
    rank_causal_cuts,
    seed_dynamic_graph,
    update_graph_from_execution,
)
from reachpatch.execution.trace import run_trace


def test_seed_graph_exists_before_p0_and_round_trips(tmp_path: Path):
    source = tmp_path / "pkg.py"
    source.write_text("def target(value):\n    if value:\n        return 1\n    return 0\n", encoding="utf-8")
    goal = SimpleNamespace(goal_id="goal", operation="target", target_symbols=("target",), authority="B", hard=True, evidence_spans=(), to_dict=lambda: {})
    graph = seed_dynamic_graph(tmp_path, "target should return one", (goal,), (), (), DynamicGraphBudget())
    assert graph.nodes
    assert any(node.kind is GraphNodeKind.SYMBOL for node in graph.nodes.values())
    restored = DynamicReachAvoidGraph.from_dict(graph.to_dict())
    assert restored.digest() == graph.digest()


def test_first_execution_updates_branch_and_failure_locus():
    graph = DynamicReachAvoidGraph()
    obligation = SimpleNamespace(obligation_id="ob", target_symbols=("target",), to_dict=lambda: {})
    observation = SimpleNamespace(status=CheckStatus.FAIL, semantic_signature="x", exception="AssertionError", to_dict=lambda: {})
    trace = SimpleNamespace(first_project_frame="pkg.py:2", events=({"file": "pkg.py", "line": 2, "function": "target", "event": "line", "branch_id": "pkg.py:2", "branch_outcome": "taken", "safe_local_summary": {"value": {"type": "int", "truthy": True}}},))
    update_graph_from_execution(graph, obligation, observation, trace)
    assert graph.metrics["graph_update_count"] == 1
    assert any(edge.kind is GraphEdgeKind.BRANCH_TAKEN for edge in graph.edges.values())
    first = graph.digest()
    observation2 = SimpleNamespace(status=CheckStatus.FAIL, semantic_signature="y", exception="AssertionError", to_dict=lambda: {})
    update_graph_from_execution(graph, obligation, observation2, trace)
    assert graph.digest() != first


def test_rank_cuts_and_distinct_hypotheses():
    graph = DynamicReachAvoidGraph()
    graph.add_node(GraphNodeKind.SYMBOL, node_id="symbol-a", file="pkg.py", symbol="target", line_start=2, line_end=4, source_span="return value", metadata={"requirement_ids": ("goal",)})
    graph.add_node(GraphNodeKind.BRANCH, node_id="branch-a", file="pkg.py", symbol="target", line_start=2, line_end=2, source_span="if value")
    cuts = rank_causal_cuts(graph, "goal", "failure", "diff --git a/pkg.py b/pkg.py\n+++ b/pkg.py\n@@ -2 +2 @@\n", 2)
    assert len(cuts) == 2
    parent = CheckpointState("p", None, "", "p", depth=0)
    hypotheses = build_distinct_repair_hypotheses(parent, "goal", "failure", cuts, limit=2)
    assert len(hypotheses) == 2
    assert len({item.proposed_mechanism for item in hypotheses}) == 2


def test_real_trace_records_taken_and_not_taken(tmp_path: Path):
    (tmp_path / "pkg.py").write_text(
        "def target(value):\n    if value:\n        return 1\n    return 0\n",
        encoding="utf-8",
    )
    taken = run_trace(tmp_path, ("python", "-c", "from pkg import target; target(1)"))
    not_taken = run_trace(tmp_path, ("python", "-c", "from pkg import target; target(0)"))
    assert any(event.get("branch_outcome") == "taken" for event in taken.events)
    assert any(event.get("branch_outcome") == "not_taken" for event in not_taken.events)


def test_seed_static_def_use_connects_parameter_branch_and_return(tmp_path: Path):
    (tmp_path / "pkg.py").write_text(
        "def target(value):\n    if value > 0:\n        return value\n    raise ValueError()\n",
        encoding="utf-8",
    )
    goal = SimpleNamespace(
        goal_id="goal", operation="target", target_symbols=("target",),
        authority="B", hard=True, evidence_spans=(), to_dict=lambda: {},
    )
    graph = seed_dynamic_graph(tmp_path, "target(value) should work", (goal,), (), (), DynamicGraphBudget())
    def_use = [edge for edge in graph.edges.values() if edge.kind is GraphEdgeKind.DEF_USE]
    assert def_use
    assert not any(edge.static_or_dynamic == "dynamic" for edge in def_use)


def test_static_and_dynamic_calls_are_distinct(tmp_path: Path):
    (tmp_path / "pkg.py").write_text(
        "def helper(value):\n    return value\n\ndef target(value):\n    return helper(value)\n",
        encoding="utf-8",
    )
    goal = SimpleNamespace(
        goal_id="goal", operation="target", target_symbols=("target",),
        authority="B", hard=True, evidence_spans=(), to_dict=lambda: {},
    )
    graph = seed_dynamic_graph(tmp_path, "target(value) should work", (goal,), (), (), DynamicGraphBudget())
    check = SimpleNamespace(obligation_id="ob", target_symbols=("target",), to_dict=lambda: {})
    observation = SimpleNamespace(status=CheckStatus.FAIL, semantic_signature="failure", exception="AssertionError", to_dict=lambda: {})
    trace = SimpleNamespace(
        first_project_frame="pkg.py:4",
        events=(
            {"file": "pkg.py", "line": 4, "function": "target", "event": "call", "caller": "<module>"},
            {"file": "pkg.py", "line": 1, "function": "helper", "event": "call", "caller": "target"},
        ),
    )
    update_graph_from_execution(graph, check, observation, trace)
    assert any(edge.kind is GraphEdgeKind.STATIC_CALLS for edge in graph.edges.values())
    assert any(edge.kind is GraphEdgeKind.DYNAMIC_CALLS for edge in graph.edges.values())


def test_graph_budget_creates_explicit_frontier(tmp_path: Path):
    (tmp_path / "a.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    hints = (
        SimpleNamespace(symbol="first", file="a.py"),
        SimpleNamespace(symbol="second", file="b.py"),
    )
    graph = seed_dynamic_graph(
        tmp_path, "first and second should work", (), hints, (),
        DynamicGraphBudget(max_files=1),
    )
    assert graph.frontiers and graph.frontiers[-1].reason == "FILE_BUDGET"


def test_same_failure_locus_accumulates_when_actual_changes():
    graph = DynamicReachAvoidGraph()
    obligation = SimpleNamespace(obligation_id="ob", target_symbols=("target",), to_dict=lambda: {})
    trace = SimpleNamespace(first_project_frame="pkg.py:2", events=())
    for signature in ("actual-one", "actual-two"):
        observation = SimpleNamespace(
            status=CheckStatus.FAIL, semantic_signature=signature,
            failure_stage="TARGET_CONTRACT_FAILURE", exception="AssertionError",
            to_dict=lambda: {},
        )
        update_graph_from_execution(graph, obligation, observation, trace)
    assert len(graph.failure_loci) == 1
    failure_id = next(iter(graph.failure_loci))
    assert graph.nodes[failure_id].metadata["occurrence_count"] == 2
