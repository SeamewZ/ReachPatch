from pathlib import Path
from types import SimpleNamespace

from reachpatch.models.evidence import OutcomeStatus, RunObservation, TraceBundle
from reachpatch.models.execution import (
    CheckExecution, CheckStatus, ExecutableCheck, FailureStage,
    MechanicalResult, StateCheckpoint, TransitionDecision,
)
from reachpatch.reach_avoid.dynamic_failure_graph import (
    DynamicFailureGraphBudget, FailureGraphEdgeKind, FailureGraphNodeKind,
    build_dynamic_failure_graph,
)
from reachpatch.reach_avoid.execution_transition import (
    compute_atomic_progress, decide_transition,
)


def _failure(count=2):
    return SimpleNamespace(failure_id="failure", same_signature_count=count)


def _trace(events=()):
    return SimpleNamespace(
        executed_line_ids=tuple(f"pkg/calc.py:{item[1]}" for item in events),
        events=events,
        first_project_frame="pkg/calc.py:1" if events else None,
    )


def _repo(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "calc.py").write_text(
        "def consumer():\n    return helper()\n\ndef helper():\n    if True:\n        return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.py").write_text("def unrelated():\n    return 9\n", encoding="utf-8")


def test_first_target_failure_does_not_expand_graph(tmp_path):
    _repo(tmp_path)
    graph = build_dynamic_failure_graph(
        tmp_path, tmp_path, "", _failure(1), _trace((("pkg/calc.py", 1, "consumer", "call"),)),
        None, DynamicFailureGraphBudget(),
    )
    assert graph.nodes == {}
    assert graph.edges == {}


def test_repeated_failure_contains_only_dynamic_node_kinds_and_edges(tmp_path):
    _repo(tmp_path)
    diff = (
        "diff --git a/pkg/calc.py b/pkg/calc.py\n"
        "--- a/pkg/calc.py\n+++ b/pkg/calc.py\n"
        "@@ -4,2 +4,2 @@\n-    if True:\n+    if False:\n"
    )
    events = (
        ("pkg/calc.py", 1, "consumer", "call"),
        ("pkg/calc.py", 4, "helper", "call"),
        ("pkg/calc.py", 5, "helper", "return"),
    )
    graph = build_dynamic_failure_graph(
        tmp_path, tmp_path, diff, _failure(2), _trace(events), None,
        DynamicFailureGraphBudget(),
    )
    assert {node.kind for node in graph.nodes.values()} <= set(FailureGraphNodeKind)
    assert {edge.kind for edge in graph.edges.values()} <= set(FailureGraphEdgeKind)
    assert any(edge.kind is FailureGraphEdgeKind.HUNK_MODIFIES for edge in graph.edges.values())
    assert any(edge.kind is FailureGraphEdgeKind.DYNAMIC_CALL for edge in graph.edges.values())
    assert all(node.path != "unrelated.py" for node in graph.nodes.values())


def test_old_hunk_is_removed_when_current_diff_disappears(tmp_path):
    _repo(tmp_path)
    old = build_dynamic_failure_graph(
        tmp_path, tmp_path,
        "diff --git a/pkg/calc.py b/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -4,1 +4,1 @@\n+return 1\n",
        _failure(2), _trace((("pkg/calc.py", 4, "helper", "call"),)), None,
        DynamicFailureGraphBudget(),
    )
    new = build_dynamic_failure_graph(
        tmp_path, tmp_path, "", _failure(2), _trace((("pkg/calc.py", 4, "helper", "call"),)), old,
        DynamicFailureGraphBudget(),
    )
    assert all(node.kind is not FailureGraphNodeKind.HUNK for node in new.nodes.values())


def test_budget_is_explicitly_recorded_as_frontier(tmp_path):
    _repo(tmp_path)
    events = tuple(("pkg/calc.py", line, "helper", "call") for line in range(1, 6))
    graph = build_dynamic_failure_graph(
        tmp_path, tmp_path, "diff --git a/pkg/calc.py b/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -1,1 +1,1 @@\n+return 2\n",
        _failure(2), _trace(events), None, DynamicFailureGraphBudget(max_nodes=1),
    )
    assert graph.frontier
    assert any(item.reason == "NODE_BUDGET" for item in graph.frontier)


def test_same_failure_expands_across_a_new_patch_hash(tmp_path):
    _repo(tmp_path)
    events = (
        ("pkg/calc.py", 1, "consumer", "call"),
        ("pkg/calc.py", 4, "helper", "call"),
        ("pkg/calc.py", 5, "nested", "call"),
        ("pkg/calc.py", 5, "nested", "return"),
        ("pkg/calc.py", 5, "helper", "return"),
        ("pkg/calc.py", 1, "consumer", "return"),
    )
    first = build_dynamic_failure_graph(
        tmp_path, tmp_path,
        "diff --git a/pkg/calc.py b/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -5,1 +5,1 @@\n+return 1\n",
        _failure(2), _trace(events), None, DynamicFailureGraphBudget(),
    )
    second_failure = SimpleNamespace(
        failure_id="failure", same_signature_count=3,
    )
    second = build_dynamic_failure_graph(
        tmp_path, tmp_path,
        "diff --git a/pkg/calc.py b/pkg/calc.py\n+++ b/pkg/calc.py\n@@ -5,1 +5,1 @@\n+return 2\n",
        second_failure, _trace(events), first, DynamicFailureGraphBudget(),
    )
    assert first.patch_hash != second.patch_hash
    assert first.expanded_depth == 1
    assert second.expanded_depth == 2


def test_new_preservation_regression_can_expand_prior_trace_context(tmp_path):
    _repo(tmp_path)
    events = (
        ("pkg/calc.py", 1, "consumer", "call"),
        ("pkg/calc.py", 4, "helper", "call"),
        ("pkg/calc.py", 5, "helper", "return"),
        ("pkg/calc.py", 1, "consumer", "return"),
    )
    previous = build_dynamic_failure_graph(
        tmp_path, tmp_path, "", _failure(2), _trace(events), None,
        DynamicFailureGraphBudget(),
    )
    regression = SimpleNamespace(
        failure_id="preservation-failure",
        same_signature_count=1,
        kind="PRESERVATION",
    )

    graph = build_dynamic_failure_graph(
        tmp_path, tmp_path, "", regression, _trace(events), previous,
        DynamicFailureGraphBudget(),
    )

    assert graph.active_failure_id == "preservation-failure"
    assert graph.nodes
    assert graph.expanded_depth == 2
    assert any(
        item.reason == "PRESERVATION_REGRESSION_EXPANSION"
        for item in graph.frontier
    )


def test_empty_graph_does_not_block_real_fail_to_pass_transition():
    check = ExecutableCheck(
        "target", "goal", "TARGET", "A", ("python",), ".", (), 5,
        "EQUALS", 2, (), ("calc",), None,
    )

    def execution(status, value):
        observation = RunObservation(
            OutcomeStatus.PASS if status is CheckStatus.PASS else OutcomeStatus.FAIL,
            0 if status is CheckStatus.PASS else 1, str(value), "", 0.1, value=value,
        )
        trace = TraceBundle(
            "trace", "tree", check.command, observation, ("calc",), ("api.py:1",),
            first_project_frame="api.py:1",
        )
        return CheckExecution(
            "target", status, observation, trace, 2, True, str(value), True,
            FailureStage.TARGET_PASS if status is CheckStatus.PASS else FailureStage.TARGET_CONTRACT_FAILURE,
            goal_id="goal",
        )

    parent_result = execution(CheckStatus.FAIL, 1)
    trial_result = execution(CheckStatus.PASS, 2)
    progress = (compute_atomic_progress(parent_result, trial_result, check),)
    mechanical = MechanicalResult(True, (), False, False, False, False)
    parent = StateCheckpoint("parent", None, "/tmp/parent", "p", "diff", "PARENT", 0)
    trial = StateCheckpoint("trial", "parent", "/tmp/trial", "t", "diff2", "TRIAL", 1)
    assert decide_transition(
        parent, trial, mechanical, mechanical, progress,
        (trial_result,), (), (),
    ) is TransitionDecision.REACHED
