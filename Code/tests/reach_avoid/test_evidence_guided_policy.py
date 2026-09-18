from dataclasses import replace
from types import SimpleNamespace

from reachpatch.models.execution import ExecutableCheck, CheckRole, CheckStatus, CheckExecution, GoalContract, EvidenceSpan
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    DynamicReachAvoidGraph, GraphNodeKind, GraphEdgeKind, CheckpointState,
    register_validation_checks, derive_validation_obligations, predicate_input_recipes,
    rank_causal_cuts, materialize_graph_guided_challenges, seed_dynamic_graph,
    build_distinct_repair_hypotheses, record_hypothesis_feedback,
    select_open_checkpoint_from_graph, select_best_evaluated_checkpoint,
)
from reachpatch.execution.oracle_audit import audit_return_oracles, uncovered_oracle_gaps
from reachpatch.execution.checks import execute_check
from reachpatch.reach_avoid.controller import ReachAvoidController


def check(check_id="target", role=CheckRole.TARGET, command=("python", "-c", "from pkg import target; assert target(1) == 3"), **kwargs):
    return ExecutableCheck(check_id, "goal", role, "B", command, ".", (), 5,
                           "EXIT_ZERO", {"exit_code": 0}, ("issue:expected",), ("target",), kwargs.get("input_recipe"))


def graph_with_target():
    graph = DynamicReachAvoidGraph()
    graph.add_node(GraphNodeKind.SYMBOL, node_id="target-symbol", file="pkg.py", symbol="target",
                   line_start=1, line_end=4, source_span="def target(x):\n    if x < 100:\n        return 0\n    return x",
                   metadata={"requirement_ids": ("goal",)})
    graph.add_node(GraphNodeKind.BRANCH, node_id="branch", file="pkg.py", symbol="target",
                   line_start=2, line_end=3, source_span="if x < 100:\n    return 0", metadata={"predicate": "x < 100"})
    graph.register_checkpoint(CheckpointState("parent", None, "", "hash"))
    return graph


def test_queue_requires_executable_binding_and_scopes_challenges():
    graph = graph_with_target()
    checks = (check(), check("preserve", CheckRole.PRESERVATION),
              check("other-child", CheckRole.CHALLENGE, input_recipe={"affected_checkpoint_ids": ("other",)}))
    register_validation_checks(graph, checks, locked_ids=("target",))
    graph.add_node(GraphNodeKind.OBLIGATION, node_id="fake", metadata={"role": "TARGET"})
    batch = derive_validation_obligations(graph, "parent")
    assert len(batch.target_obligations) == len(batch.locked_success_obligations) == 1
    assert len(batch.preservation_obligations) == 1
    assert not batch.challenge_obligations
    assert any(edge.source_id == "parent" and edge.kind is GraphEdgeKind.REQUIRES_VALIDATION for edge in graph.edges.values())


def test_controller_consumes_graph_order_and_membership():
    graph = graph_with_target()
    state = SimpleNamespace(dynamic_failure_graph=graph,
        working_checkpoint=SimpleNamespace(checkpoint_id="parent"),
        target_checks=(check(),), preservation_checks=(), locked_checks=(),
        challenge_checks=(check("unrelated", CheckRole.CHALLENGE, input_recipe={"affected_checkpoint_ids": ("other",)}),))
    assert [item.check_id for item in ReachAvoidController._graph_checks(state)] == ["target"]
    assert graph.update_log[-1]["event"] == "VALIDATION_BATCH"


def test_ast_boundaries_use_actual_constant_and_truthiness():
    assert [item["value"] for item in predicate_input_recipes("x < 100")] == [99, 100, 101]
    assert [item["value"] for item in predicate_input_recipes("-5 <= x")] == [-6, -5, -4]
    assert [item["value"] for item in predicate_input_recipes("if value:")][:3] == [None, [], [1]]


def test_unrelated_symbol_in_same_file_is_not_a_causal_cut():
    graph = graph_with_target()
    graph.add_node(GraphNodeKind.SYMBOL, node_id="unrelated", file="pkg.py", symbol="setup", line_start=10,
                   line_end=11, source_span="def setup(): return 0")
    cuts = rank_causal_cuts(graph, "goal", "failure", "", 3)
    assert cuts and all("setup" not in " ".join(cut.source_spans) for cut in cuts)
    assert rank_causal_cuts(graph, "unknown-goal", "unknown-failure", "", 3) == ()


def test_hypotheses_have_falsifiers_and_input_partitions():
    graph = graph_with_target()
    cuts = rank_causal_cuts(graph, "goal", "failure", "", 3)
    hypotheses = build_distinct_repair_hypotheses(CheckpointState("parent", None, "", "hash"), "goal", "failure", cuts)
    assert len(hypotheses) >= 2
    assert all(item.falsification_conditions for item in hypotheses)
    assert any(item.distinguishing_inputs for item in hypotheses)
    assert len({item.expected_path_change for item in hypotheses}) == len(hypotheses)


def test_exact_input_oracle_required_even_when_original_is_trusted():
    graph = graph_with_target()
    original = check(input_recipe={"variants": [
        {"command": ["python", "-c", "print(100)"], "input": 100},
        {"command": ["python", "-c", "print(101)"], "input": 101,
         "oracle": {"comparator": "EQUALS", "expected": 101}, "authority": "B", "evidence_ids": ["explicit-clause"]},
    ]})
    register_validation_checks(graph, (original,))
    checkpoint = CheckpointState("parent", None, "", "hash", causal_cut_ids=("branch",))
    cells = materialize_graph_guided_challenges(None, graph, checkpoint, ())
    assert len([cell for cell in cells if cell.status == "PENDING"]) == 1
    assert next(cell for cell in cells if cell.status == "PENDING").oracle["expected"] == 101


def test_descendant_credit_changes_expansion_not_final_quality():
    graph = DynamicReachAvoidGraph()
    for checkpoint in (CheckpointState("root", None, "", "r", search_score=(1, 1)),
                       CheckpointState("parent", "root", "p", "p", search_score=(1, 2)),
                       CheckpointState("child", "parent", "c", "c", search_score=(1, 5), observation_ids=("observed",))):
        graph.register_checkpoint(checkpoint)
    graph.record_transition("parent", "child", "KEEP_REPAIRING")
    assert graph.nodes["root"].metadata["best_descendant_score"] == [1, 5]
    assert graph.nodes["root"].metadata["search_score"] == [1, 1]
    assert not graph.nodes["root"].metadata["certified"]
    assert select_best_evaluated_checkpoint(graph).checkpoint_id == "child"


def test_feedback_does_not_promote_untrusted_or_bypassed_pass():
    graph = graph_with_target()
    hypothesis = build_distinct_repair_hypotheses(CheckpointState("parent", None, "", "hash"), "goal", "failure",
                                                  rank_causal_cuts(graph, "goal", "failure", "", 2))[0]
    graph.register_hypothesis(hypothesis)
    before = CheckExecution("target", CheckStatus.FAIL, None, stable=True, semantic_signature="fail", authority="B", entered_target_code=True)
    after = replace(before, status=CheckStatus.PASS, semantic_signature="pass", entered_target_code=False)
    feedback = record_hypothesis_feedback(graph, hypothesis, "child", (before,), (after,))
    assert feedback["status"] == "REFUTED"


def test_return_oracle_audit_detects_survivor_and_keeps_source_unchanged(tmp_path):
    source = "def target(x):\n    return []\n"
    (tmp_path / "pkg.py").write_text(source)
    goal = GoalContract("goal", "target", ("target",), "EXIT_ZERO", {"exit_code": 0},
                        (EvidenceSpan(0, 38, "target should return empty arrays"),), "B", True)
    graph = seed_dynamic_graph(tmp_path, "target should return empty arrays", (goal,), (), (), None)
    weak = check(command=("python", "-c", "from pkg import target; target([])"))
    results = (execute_check(tmp_path, weak),)
    report = audit_return_oracles(tmp_path, graph, (goal,), (weak,), results, patch_hash="p")
    assert report[0]["status"] == "SURVIVOR"
    assert uncovered_oracle_gaps(report)
    assert (tmp_path / "pkg.py").read_text() == source
    strong = check(command=("python", "-c", "from pkg import target; value=target([]); assert isinstance(value,list) and len(value)==0"))
    report = audit_return_oracles(tmp_path, graph, (goal,), (strong,), (execute_check(tmp_path, strong),), patch_hash="p")
    assert report[0]["status"] == "DISCRIMINATES_SAMPLED_MUTANTS"
    assert not uncovered_oracle_gaps(report)


def test_audit_budget_does_not_become_evidence(tmp_path):
    goal = GoalContract("goal", "target", ("target",), "EQUALS", 3, (), "B", True)
    result = CheckExecution("target", CheckStatus.PASS, None, stable=True)
    reports = audit_return_oracles(tmp_path, graph_with_target(), (goal,), (check(),), (result,), patch_hash="p", wall_seconds=0)
    assert reports[0]["status"] == "INCONCLUSIVE"


def test_class_method_nodes_do_not_collide(tmp_path):
    (tmp_path / "pkg.py").write_text("class A:\n    def target(self): return 1\nclass B:\n    def target(self): return 2\n")
    goal = GoalContract("goal", "target", ("A.target",), "EQUALS", 3, (), "B", True)
    graph = seed_dynamic_graph(tmp_path, "A.target() should work", (goal,), (), (), None)
    names = {node.symbol for node in graph.nodes.values() if node.kind is GraphNodeKind.SYMBOL}
    assert {"A.target", "B.target"} <= names
    assert not any(node.symbol == "B.target" and "goal" in node.metadata.get("requirement_ids", ()) for node in graph.nodes.values())


def test_sibling_probe_runs_real_inputs_without_copying_original_assertion(tmp_path):
    from reachpatch.execution.discriminating_probes import instantiate_probe_command, execute_sibling_probes
    from reachpatch.reach_avoid.dynamic_reach_avoid_graph import ChallengeCell
    graph = graph_with_target()
    snapshots = {}
    for name, result in (("left", "None"), ("right", "[]")):
        tree = tmp_path / name
        tree.mkdir()
        (tree / "pkg.py").write_text(f"def target(x):\n    return {result}\n")
        snapshots[name] = tree
        graph.register_checkpoint(CheckpointState(name, "parent", name, name))
    command = instantiate_probe_command(
        ("python", "-c", "from pkg import target; assert target(1) == 3"),
        "def target(x):\n    return x", "target", {"parameter": "x", "value": []})
    assert command and "assert target" not in command[2]
    cell = ChallengeCell("probe-cell", "branch", (), {"value": []}, None, "PROVISIONAL", command)
    graph.add_node(GraphNodeKind.CHALLENGE, node_id=cell.challenge_id, metadata=cell.to_dict())
    reports = execute_sibling_probes(graph, (cell,), snapshots, clean=snapshots["left"])
    assert reports[0]["disagreement"] and not reports[0]["certifying"]
    assert all(value["stable"] for value in reports[0]["outcomes"].values())
    assert graph.nodes["branch"].metadata["sibling_disagreement_probe_ids"]
    assert not graph.nodes["left"].metadata["certified"]


def test_source_refresh_tracks_parent_backtrack_without_second_graph(tmp_path):
    from reachpatch.reach_avoid.dynamic_reach_avoid_graph import refresh_checkpoint_source
    graph = graph_with_target()
    (tmp_path / "pkg.py").write_text("def target(x):\n    if x < 100:\n        return 0\n    return x\n")
    diff = "+++ b/pkg.py\n@@ -1,4 +1,4 @@\n"
    refresh_checkpoint_source(graph, tmp_path, diff)
    identity = id(graph)
    (tmp_path / "pkg.py").write_text("def target(x):\n    if x < 200:\n        return 0\n    return x\n")
    refresh_checkpoint_source(graph, tmp_path, diff)
    assert any(node.metadata.get("predicate") == "x < 200" and node.status == "CURRENT_SOURCE" for node in graph.nodes.values())
    (tmp_path / "pkg.py").write_text("def target(x):\n    if x < 100:\n        return 0\n    return x\n")
    refresh_checkpoint_source(graph, tmp_path, diff)
    assert id(graph) == identity
    assert not any(node.metadata.get("predicate") == "x < 200" and node.status == "CURRENT_SOURCE" for node in graph.nodes.values())


def test_controller_does_not_certify_a_weak_return_oracle(tmp_path, monkeypatch):
    from reachpatch.models.core import Instance
    from reachpatch.reach_avoid.controller import ReachAvoidConfig
    from reachpatch.reach_avoid.repair_player import RepairPlayer
    import reachpatch.reach_avoid.controller as controller_module
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg.py").write_text("def target(x):\n    raise ValueError('empty input')\n")
    goal = GoalContract("goal", "target", ("target",), "EXIT_ZERO", {"exit_code": 0},
                        (EvidenceSpan(0, 38, "target should return empty arrays"),), "B", True)
    monkeypatch.setattr(controller_module, "compile_goal_contracts", lambda *args, **kwargs: (goal,))
    class WrongGenerator:
        def revise(self, objective, tools, initial=False):
            assert initial, "An oracle gap must recover evidence, not generate a speculative repair."
            tools.apply_patch("diff --git a/pkg.py b/pkg.py\n--- a/pkg.py\n+++ b/pkg.py\n@@ -1,2 +1,2 @@\n def target(x):\n-    raise ValueError('empty input')\n+    return None\n")
            pending = tools.validation_status()
            while pending["pending_commands"]:
                tools.run_allowed_public_check(pending["pending_commands"][0])
                pending = tools.validation_status()
            tools.finish_revision("wrong return", "early-return")
            return {"summary": "wrong return"}
    instance = Instance("weak-oracle-toy", str(repo), "base", "target should return empty arrays", public_metadata={"public_checks": (
        {"check_id": "weak", "command": ("python", "-c", "from pkg import target; target([])"),
         "role": "TARGET", "authority": "A", "symbol_references": ("target",)},)})
    result = ReachAvoidController(RepairPlayer(WrongGenerator()), ReachAvoidConfig(target_recovery_attempts=0)).run_case(instance, run_root=tmp_path / "run")
    assert result.status == "EVIDENCE_LIMITED"
    import json
    final = json.loads((tmp_path / "run" / "final_selection.json").read_text())
    assert not final["certified"]
    assert (tmp_path / "run" / "validation_observations.jsonl").read_text()


def test_target_entry_does_not_accept_substring_match(tmp_path):
    (tmp_path / "pkg.py").write_text("def not_target(x):\n    return x\n")
    probe = check(command=("python", "-c", "from pkg import not_target; not_target(1)"))
    result = execute_check(tmp_path, probe)
    assert result.status is CheckStatus.PASS
    assert not result.entered_target_code
    assert len(result.run_observations) == 2


def test_trace_does_not_invoke_custom_truthiness(tmp_path):
    (tmp_path / "pkg.py").write_text(
        "class Result:\n    def __bool__(self):\n        raise RuntimeError('instrumentation side effect')\n"
        "def target(x):\n    value = Result()\n    return value\n")
    result = execute_check(tmp_path, check(command=("python", "-c", "from pkg import target; target(1)")))
    assert result.stable and result.status is CheckStatus.PASS


def test_partial_numeric_progress_uses_baseline_distance_not_diff_size(tmp_path):
    graph = graph_with_target()
    (tmp_path / "pkg.py").write_text("def target(x):\n    return 2\n")
    numeric = replace(check(command=("python", "-c", "from pkg import target; print(target(1))")), comparator="EQUALS", expected=3)
    execution = execute_check(tmp_path, numeric)
    assert execution.status is CheckStatus.FAIL and execution.distance == 1
    graph.add_node(GraphNodeKind.OBSERVATION, node_id="clean-observed", metadata={
        "phase": "CLEAN", "execution": {"check_id": "target", "stable": True, "entered_target_code": True, "distance": 3}})
    state = SimpleNamespace(dynamic_failure_graph=graph)
    assert ReachAvoidController._distance_reduction_count(state, (execution, execution)) == 1
    p0 = replace(execution, distance=2)
    assert ReachAvoidController._distance_reduction_count(state, (execution,), amount=True) > ReachAvoidController._distance_reduction_count(state, (p0,), amount=True)
