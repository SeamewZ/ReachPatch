from dataclasses import replace
from types import SimpleNamespace
import json

import pytest

from reachpatch.models.execution import GoalContract, EvidenceSpan, ExecutableCheck, CheckRole
from reachpatch.models.evidence import ObservationContract
from reachpatch.requirement_graph.facets import decompose_required_facets, match_grounded_probe_goal
from reachpatch.execution.facet_probes import materialize_return_facet_checks
from reachpatch.execution.checks import execute_check
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import DynamicReachAvoidGraph, CheckpointState, GraphNodeKind as N, GraphEdgeKind as E, RepairHypothesis
from reachpatch.reach_avoid.graph_policy import compile_contract_obligations, evaluate_validation_closure, derive_frontier_actions, record_patch_transposition, estimate_action_cost, challenge_lifecycle_metrics
from reachpatch.reach_avoid.mechanism_predictions import align_execution_paths


def goal(quote="target should not fail and should return an empty list"):
    return GoalContract("goal", "target", ("target",), "EXIT_ZERO", {"exit_code": 0},
                        (EvidenceSpan(0, len(quote), quote),), "B", True,
                        evidence_span_ids=("issue-span",))


def test_return_facets_do_not_disappear_after_exit_zero_pass():
    goals = decompose_required_facets((goal(),))
    assert {item.facet_kind for item in goals} == {"CONTRACT", "RETURN_TYPE", "RETURN_LENGTH"}
    assert decompose_required_facets(goals) == goals
    graph = DynamicReachAvoidGraph()
    graph.register_checkpoint(CheckpointState("cp", None, "diff", "hash"))
    ids = compile_contract_obligations(graph, goals, ())
    closure = evaluate_validation_closure(graph, "cp", ids, (), ())
    assert len(closure["gaps"]) == 3
    graph.update_checkpoint("cp", observation_ids=("run",), target_results={"check": {"status": "PASS", "stable": True}})
    actions = derive_frontier_actions(graph, max_depth=3)
    assert len(actions) == 3 and all(item.requirement_id for item in actions)


def test_ambiguous_plural_return_never_guesses_outer_layout():
    goals = decompose_required_facets((goal("target should not fail but instead should return empty lists/arrays"),))
    facet = goals[-1]
    assert facet.hard and facet.unresolved_reason == "RETURN_LAYOUT_REQUIRES_PUBLIC_EVIDENCE"
    assert facet.expected["layout"] == "UNRESOLVED"


def test_grounded_stronger_probe_binds_to_parent_family_not_unrelated_goal():
    goals = decompose_required_facets((goal(),))
    probe = SimpleNamespace(requirement_id="goal", contract=ObservationContract("length", 0, comparator="LENGTH_EQUALS"))
    assert match_grounded_probe_goal(goals, probe).facet_kind == "RETURN_LENGTH"
    assert match_grounded_probe_goal(goals, replace_probe(probe, "unrelated")) is None


def replace_probe(probe, requirement_id):
    return SimpleNamespace(requirement_id=requirement_id, contract=probe.contract)


@pytest.mark.parametrize("returned,expected", [("[]", ("PASS", "PASS")), ("None", ("FAIL", "FAIL")), ("0", ("FAIL", "FAIL")), ("[1]", ("PASS", "FAIL")), ("type('L', (list,), {})()", ("PASS", "PASS"))])
def test_real_same_input_facet_checks_reject_incorrect_returns(tmp_path, returned, expected):
    (tmp_path / "pkg.py").write_text(f"def target(x):\n    return {returned}\n")
    check = ExecutableCheck("witness", "goal", CheckRole.TARGET, "B",
        ("python", "-c", "from pkg import target\ntarget([])"), ".", (), 5,
        "EXIT_ZERO", {"exit_code": 0}, ("issue-span",), ("target",), {})
    checks = materialize_return_facet_checks(check, decompose_required_facets((goal(),)))
    assert len(checks) == 2
    results = [execute_check(tmp_path, item, stability_runs=2) for item in checks]
    assert tuple(str(item.status) for item in results) == expected
    assert all(item.stable and item.entered_target_code for item in results)


def test_transposition_retains_original_parent_and_links_second_hypothesis():
    graph = DynamicReachAvoidGraph()
    graph.register_checkpoint(CheckpointState("root", None, "root", "root-hash"))
    graph.register_checkpoint(CheckpointState("existing", "root", "patch", "hash"))
    graph.register_checkpoint(CheckpointState("other", "root", "other", "other-hash"))
    graph.register_hypothesis(RepairHypothesis("hyp", "other", "goal", "failure", (), "guard", "path", "return", ()))
    assert record_patch_transposition(graph, "other", "hyp", "hash", "patch") == "existing"
    assert graph.nodes["existing"].metadata["parent_checkpoint_id"] == "root"
    assert any(edge.kind is E.REACHES_CHECKPOINT and edge.source_id == "hyp" and edge.target_id == "existing" for edge in graph.edges.values())
    assert "hyp" in graph.nodes["other"].metadata["expanded_hypothesis_ids"]


def test_unknown_cost_is_explicit_and_measured_cost_is_used():
    graph = DynamicReachAvoidGraph()
    assert estimate_action_cost(graph, "cp", "RECOVER_EVIDENCE") is None
    graph.record_update("ACTION_COMPLETED", kind="RECOVER_EVIDENCE", duration_seconds=12)
    graph.record_update("ACTION_COMPLETED", kind="RECOVER_EVIDENCE", duration_seconds=8)
    assert estimate_action_cost(graph, "cp", "RECOVER_EVIDENCE") == 10


def test_exploratory_challenge_is_an_action_not_a_certifying_check():
    graph = DynamicReachAvoidGraph()
    graph.register_checkpoint(CheckpointState("root", None, "patch", "hash", observation_ids=("run",)))
    graph.add_node(N.CHALLENGE, node_id="cell", status="EXPLORATION_ONLY", authority="PROVISIONAL",
        metadata={"command": ("python", "-c", "print(1)"), "affected_checkpoint_ids": ("root",)})
    assert any(action.kind == "RUN_PROBE" for action in derive_frontier_actions(graph, max_depth=3))
    assert challenge_lifecycle_metrics(graph)["challenge_trusted_oracle"] == 0


def test_specific_path_prediction_requires_direction_not_any_change():
    def run(outcome):
        return SimpleNamespace(trace=SimpleNamespace(events=({"file": "p.py", "function": "target", "line": 2,
            "source_anchor": ("target", "If"), "branch_outcome": outcome},)))
    result = align_execution_paths(run("taken"), run("not_taken"), file="p.py", symbol="target",
        kind="BRANCH_OUTCOME_CHANGE", expected_change={"from": "not_taken", "to": "taken"})
    assert result["status"] == "NOT_OBSERVABLE"
    result = align_execution_paths(run("taken"), run("not_taken"), file="p.py", symbol="target",
        kind="BRANCH_OUTCOME_CHANGE", expected_change={"from": "taken", "to": "not_taken"},
        parent_source_version_id="parent", trial_source_version_id="trial")
    assert result["status"] == "NOT_OBSERVABLE"


def test_issue_only_weak_p0_repaired_and_evidence_sealed(tmp_path):
    from reachpatch.models.core import Instance
    from reachpatch.reach_avoid.controller import ReachAvoidController
    from reachpatch.reach_avoid.repair_player import RepairPlayer
    from experiments.reachavoid_51.runner import _execution_component_evidence

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg.py").write_text("def target(values):\n    return [values[0]]\n")

    class Repair:
        def revise(self, objective, tools, initial=False):
            before = "[values[0]]" if initial else "None"
            after = "None" if initial else "[]"
            tools.apply_patch("diff --git a/pkg.py b/pkg.py\n--- a/pkg.py\n+++ b/pkg.py\n"
                f"@@ -1,2 +1,2 @@\n def target(values):\n-    return {before}\n+    return {after}\n")
            status = tools.validation_status()
            while status["pending_commands"]:
                tools.run_allowed_public_check(status["pending_commands"][0])
                status = tools.validation_status()
            tools.finish_revision("Return the required empty list", "repair_return_value_data_flow")
            return {"summary": "return facet repair"}

    issue = "target fails on empty input\n\ntarget should not fail and should return an empty list:\n\n```python\nfrom pkg import target\ntarget([])\n```"
    root = tmp_path / "run"
    result = ReachAvoidController(RepairPlayer(Repair())).run(Instance("facet-toy", str(repo), "base", issue), run_root=root)
    assert result.status == "REACHED"
    selection = json.loads((root / "final_selection.json").read_text())
    assert selection["patch_hash"] != selection["p0_patch_hash"]
    evidence = _execution_component_evidence(root, {"checkpoint_id": result.checkpoint_id})
    assert evidence["validation"]["participated"]
    assert evidence["validation"]["stable_target_pass_count"] >= 3
    checkpoint_path = root / "execution_checkpoints" / result.checkpoint_id / "checkpoint.json"
    original_payload = checkpoint_path.read_text()
    payload = json.loads(original_payload)
    payload["target_results"] = []
    checkpoint_path.write_text(json.dumps(payload))
    try:
        with pytest.raises(RuntimeError, match="ARTIFACT_INCONSISTENT"):
            _execution_component_evidence(root, {"checkpoint_id": result.checkpoint_id})
    finally:
        checkpoint_path.write_text(original_payload)
