from dataclasses import replace
from types import SimpleNamespace

import pytest

from reachpatch.models.evidence import TraceEvent
from reachpatch.models.execution import ExecutableCheck, CheckRole, CheckStatus, GoalContract, EvidenceSpan
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    DynamicReachAvoidGraph, CheckpointState, RepairHypothesis, GraphNodeKind as N,
    GraphEdgeKind as E, register_validation_checks, update_graph_from_execution,
)
from reachpatch.reach_avoid.graph_policy import (
    compile_contract_obligations, evaluate_validation_closure, derive_frontier_actions,
    exhaust_action, select_next_action, select_comparable_checkpoints,
)
from reachpatch.reach_avoid.mechanism_predictions import align_execution_paths
from reachpatch.execution.oracle_audit import uncovered_oracle_gaps
from reachpatch.execution.case_budget import CaseBudget, BudgetedTransport, CaseBudgetExhausted, active_case_budget, charged_execution
from reachpatch.execution.validation_cache import validation_cache_key


def probe(name="check", value=1):
    return ExecutableCheck(name, "goal", CheckRole.TARGET, "B",
        ("python", "-c", f"from pkg import target; print(target({value}))"),
        ".", (), 5, "EQUALS", 3, ("issue:expected",), ("target",), None)


def goal():
    return GoalContract("goal", "target", ("target",), "EQUALS", 3,
        (EvidenceSpan(0, 20, "target must return 3"),), "B", True)


def graph():
    value = DynamicReachAvoidGraph()
    value.register_checkpoint(CheckpointState("root", None, "patch", "hash"))
    return value


def test_two_input_partition_oracle_gaps_do_not_cancel():
    g = graph()
    ids = compile_contract_obligations(g, (goal(),), (probe(), probe("second", 2)))
    assert len(set(ids)) == 2
    reports = ({"obligation_id": ids[0], "status": "DISCRIMINATES_SAMPLED_MUTANTS", "blocks_certification": False},
               {"obligation_id": ids[1], "status": "SURVIVOR", "blocks_certification": True})
    assert uncovered_oracle_gaps(reports) == (reports[1],)


def test_new_child_challenge_must_execute_before_closure():
    g = graph()
    ids = compile_contract_obligations(g, (goal(),), (probe(),))
    g.add_node(N.OBLIGATION, node_id="new-input", metadata={"role": "CHALLENGE", "executable_handle": True, "check_id": "boundary"})
    g.add_edge(E.REQUIRES_VALIDATION, "root", "new-input")
    result = SimpleNamespace(check_id="check", stable=True, status=CheckStatus.PASS, authority="B", entered_target_code=True)
    report = evaluate_validation_closure(g, "root", ids, (result,), ())
    assert not report["closed"]
    assert report["gaps"] == [{"obligation_id": "new-input", "reason": "OPEN_EXECUTABLE_CHALLENGE"}]
    boundary = SimpleNamespace(**{**vars(result), "check_id": "boundary"})
    assert evaluate_validation_closure(g, "root", ids, (result, boundary), ())["closed"]


def test_action_exhaustion_survives_checkpoint_registration_and_timing_changes():
    g = graph()
    cp = CheckpointState("root", None, "patch", "hash", observation_ids=("run",),
        target_results={"check": {"status": "PASS", "stable": True, "duration_seconds": 1}})
    g.register_checkpoint(cp)
    action = derive_frontier_actions(g, max_depth=3)[0]
    exhaust_action(g, action, "NO_NEW_EVIDENCE")
    g.register_checkpoint(replace(cp, target_results={"check": {"status": "PASS", "stable": True, "duration_seconds": 9}}))
    assert not derive_frontier_actions(g, max_depth=3)


def test_local_exhaustion_leaves_another_graph_checkpoint_open():
    g = graph()
    g.register_checkpoint(CheckpointState("other", None, "another", "other-hash"))
    selected = select_next_action(g, derive_frontier_actions(g, max_depth=3))
    exhaust_action(g, selected, "LOCAL_EXHAUSTION")
    assert select_next_action(g, derive_frontier_actions(g, max_depth=3)).checkpoint_id != selected.checkpoint_id


def test_untried_hypotheses_are_separate_global_actions():
    g = graph()
    g.update_checkpoint("root", observation_ids=("failure",), target_results={"check": {"status": "FAIL", "stable": True}})
    for name in ("guard", "return"):
        g.register_hypothesis(RepairHypothesis(name, "root", "goal", "failure", (), name, "path", "value", ()))
    actions = derive_frontier_actions(g, max_depth=3)
    assert {item.hypothesis_id for item in actions} == {"guard", "return"}
    assert len({item.action_id for item in actions}) == 2
    g.update_checkpoint("root", expanded_hypothesis_ids=("guard",))
    assert [item.hypothesis_id for item in derive_frontier_actions(g, max_depth=3)] == ["return"]


def test_exhausted_cut_produces_bounded_expansion_action():
    g = graph()
    g.update_checkpoint("root", status="EXPANDED", observation_ids=("failure",),
                        target_results={"check": {"status": "FAIL", "stable": True}})
    assert derive_frontier_actions(g, max_depth=3)[0].kind == "EXPAND_GRAPH"
    node = g.nodes["root"]
    g.nodes["root"] = replace(node, metadata={**node.metadata, "expansion_rounds": g.budget.max_expansion_depth})
    assert not derive_frontier_actions(g, max_depth=3)


def test_comparable_checkpoints_need_not_share_parent():
    g = graph()
    for name, parent in (("a", "root"), ("b", "a")):
        g.register_checkpoint(CheckpointState(name, parent, name, name, target_results={"check": {"status": "PASS"}}))
    assert set(select_comparable_checkpoints(g, "b")) == {"a", "b"}


def execution(event):
    return SimpleNamespace(trace=SimpleNamespace(events=(event,)))


def test_typed_trace_anchor_survives_blank_line_movement():
    before = TraceEvent("pkg.py", "target", 2, "line", branch_outcome="taken",
                        source_anchor=("target", "body:0", "If"), source_version_id="parent")
    after = replace(before, line=4, source_version_id="child")
    result = align_execution_paths(execution(before), execution(after), file="pkg.py", symbol="target", kind="BRANCH_OUTCOME_CHANGE")
    assert result["status"] == "CONTRADICTED"
    assert align_execution_paths(execution(before), execution(replace(after, branch_outcome="not_taken")),
        file="pkg.py", symbol="target", kind="BRANCH_OUTCOME_CHANGE")["status"] == "SUPPORTED"


def test_unknown_branch_cannot_support_path_change():
    before = TraceEvent("pkg.py", "target", 2, "line", branch_outcome="taken", source_anchor=("target", "body:0", "If"))
    assert align_execution_paths(execution(before), execution(replace(before, branch_outcome="UNKNOWN")),
        file="pkg.py", symbol="target", kind="BRANCH_OUTCOME_CHANGE")["status"] == "NOT_OBSERVABLE"


def test_absolute_snapshot_path_does_not_count_as_aligned_evidence():
    before = TraceEvent("/tmp/parent/pkg.py", "target", 2, "line", branch_outcome="taken", source_anchor=("target", "body:0", "If"))
    after = replace(before, file="/tmp/trial/pkg.py", branch_outcome="not_taken")
    assert align_execution_paths(execution(before), execution(after), file="pkg.py", symbol="target", kind="BRANCH_OUTCOME_CHANGE")["status"] == "NOT_OBSERVABLE"


def test_one_budget_counts_all_transport_callers():
    class Transport:
        def complete(self, messages, **kwargs):
            return {"_usage": {"total_tokens": 12}}
    budget = CaseBudget(100, max_model_calls=2)
    transport = BudgetedTransport(Transport(), budget)
    transport.complete([{"content": "compile"}])
    transport.complete([{"content": "recover"}])
    with pytest.raises(CaseBudgetExhausted):
        transport.complete([{"content": "repair"}])
    assert budget.model_calls == 2 and budget.tokens == 24


def test_validation_reserve_blocks_model_but_allows_execution():
    budget = CaseBudget(10, final_validation_reserve=20)
    with pytest.raises(CaseBudgetExhausted):
        BudgetedTransport(object(), budget).complete([])
    token = active_case_budget.set(budget)
    try:
        @charged_execution
        def execute(*, timeout_seconds):
            return timeout_seconds
        assert 0 < execute(timeout_seconds=30) <= 10
        assert budget.events[-1]["kind"] == "EXECUTION"
    finally:
        active_case_budget.reset(token)


def test_validation_cache_requires_attested_environment_and_invalidates_inputs(tmp_path, monkeypatch):
    (tmp_path / "pkg.py").write_text("def target(x): return x\n")
    check = probe()
    assert validation_cache_key(tmp_path, tmp_path, check) is None
    cached = replace(check, input_recipe={"cache_policy": {"isolated_deterministic": True,
        "external_state": False, "environment_fingerprint": "toy-env"}})
    original = validation_cache_key(tmp_path, tmp_path, cached)
    assert original
    monkeypatch.setenv("REACHPATCH_TEST_ENVIRONMENT", "changed")
    assert original != validation_cache_key(tmp_path, tmp_path, cached)
    assert validation_cache_key(tmp_path, tmp_path, cached) != validation_cache_key(tmp_path, tmp_path, replace(cached, expected=4))


def test_failure_locus_ignores_ephemeral_execution_root():
    g = graph()
    g.add_node(N.SYMBOL, node_id="symbol", file="pkg.py", symbol="target", line_start=1, line_end=3)
    register_validation_checks(g, (probe(),))
    trace = SimpleNamespace(events=(TraceEvent("pkg.py", "target", 2, "line"),), first_project_frame="pkg.py:2")
    for root in ("/tmp/execution-one", "/tmp/execution-two"):
        observation = SimpleNamespace(status="FAIL", semantic_signature=root,
            observation=SimpleNamespace(exception="AssertionError", stderr=f'File "{root}/pkg.py", line 2'))
        update_graph_from_execution(g, probe(), observation, trace, SimpleNamespace(hunks=()))
    assert len(g.failure_loci) == 1
    failure = next(node for node in g.nodes.values() if node.kind is N.FAILURE)
    assert failure.metadata["occurrence_count"] == 2


def test_repeated_duplicate_patch_exhausts_graph_actions_without_looping(tmp_path):
    from reachpatch.models.core import Instance
    from reachpatch.reach_avoid.controller import ReachAvoidController, ReachAvoidConfig
    from reachpatch.reach_avoid.repair_player import RepairPlayer
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg.py").write_text("def target(x):\n    return 0\n")
    class DuplicateGenerator:
        calls = 0
        def revise(self, objective, tools, initial=False):
            self.calls += 1
            assert self.calls <= 12, "Repeated identical mechanism was not exhausted."
            if initial:
                tools.apply_patch("diff --git a/pkg.py b/pkg.py\n--- a/pkg.py\n+++ b/pkg.py\n@@ -1,2 +1,2 @@\n def target(x):\n-    return 0\n+    return 1\n")
            tools.finish_revision("same parent", "no-change")
            return {"summary": "same parent"}
    generator = DuplicateGenerator()
    instance = Instance("duplicate-actions", str(repo), "base", "target must return 3.", public_metadata={"public_checks": ({
        "check_id": "check", "command": ("python", "-c", "from pkg import target; assert target(1) == 3"),
        "role": "TARGET", "authority": "A", "symbol_references": ("target",)},)})
    result = ReachAvoidController(RepairPlayer(generator), ReachAvoidConfig(execution_budget_seconds=120)).run_case(instance, run_root=tmp_path / "run")
    assert result.status != "REACHED"
    assert result.status != "BEST_EFFORT_BUDGET_EXHAUSTED"
    assert 1 < generator.calls <= 12


def test_path_change_outside_selected_cut_does_not_support_hypothesis():
    def trace(first, second):
        return SimpleNamespace(trace=SimpleNamespace(events=(
            TraceEvent("pkg.py", "target", 3, "line", branch_id="pkg.py:2", branch_outcome=first, source_anchor=("target", "body:0", "If")),
            TraceEvent("pkg.py", "target", 7, "line", branch_id="pkg.py:6", branch_outcome=second, source_anchor=("target", "body:1", "If")),
        )))
    report = align_execution_paths(trace("taken", "taken"), trace("taken", "not_taken"),
        file="pkg.py", symbol="target", kind="BRANCH_OUTCOME_CHANGE", parent_line_start=2, parent_line_end=3)
    assert report["status"] == "CONTRADICTED"


def test_trace_retains_source_version_and_unknown_ternary(tmp_path):
    from reachpatch.execution.trace import run_trace
    (tmp_path / "pkg.py").write_text("def target(x):\n    return 1 if x else 2\n")
    result = run_trace(tmp_path, ("python", "-c", "from pkg import target; target(True)"))
    assert any(event.source_anchor for event in result.events)
    assert all(event.source_version_id == result.tree_hash for event in result.events)
    assert not any(event.branch_outcome in {"taken", "not_taken"} for event in result.events)


def test_scoped_trace_skips_import_loop_but_records_target_and_callee(tmp_path):
    from reachpatch.execution.trace import run_trace
    (tmp_path / "pkg.py").write_text(
        "for i in range(10000):\n    ignored = i\n"
        "def helper(x):\n    return x + 1\n"
        "def target(x):\n    if x:\n        return helper(x)\n    return 0\n")
    trace = run_trace(tmp_path, ("python", "-c", "from pkg import target; target(1)"), target_symbols=("target",))
    assert {event.function for event in trace.events} == {"target", "helper"}
    assert any(event.branch_outcome == "taken" for event in trace.events)
    assert any(event.function == "helper" and event.caller == "target" for event in trace.events)


def test_dependency_warning_failure_is_blocked_despite_target_entry(tmp_path, monkeypatch):
    import reachpatch.execution.checks as checks
    from reachpatch.models.evidence import RunObservation, OutcomeStatus, TraceBundle
    observation = RunObservation(status=OutcomeStatus.FAIL, return_code=1,
        stdout="E   DeprecationWarning: deprecated dependency API\n/env/lib/site-packages/dep/api.py:14: DeprecationWarning\n",
        stderr="", duration_seconds=0.1)
    trace = TraceBundle("trace", "hash", probe().command, observation,
        ("target",), ("pkg.py:1",), first_project_frame="pkg.py:1",
        events=(TraceEvent("pkg.py", "target", 1, "return"),))
    monkeypatch.setattr(checks, "run_trace", lambda *args, **kwargs: trace)
    result = checks.execute_check(tmp_path, replace(probe(), comparator="EXIT_ZERO", expected={"exit_code": 0}))
    assert result.entered_target_code
    assert result.stable and result.status is CheckStatus.BLOCKED
    assert result.failure_stage is None
