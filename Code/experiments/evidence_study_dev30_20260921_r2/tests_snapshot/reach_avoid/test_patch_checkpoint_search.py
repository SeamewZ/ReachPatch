from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    CheckpointState,
    DynamicReachAvoidGraph,
    GraphNodeKind,
    build_distinct_repair_hypotheses,
)
from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidController
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.reach_avoid.execution_checkpoint import select_final_checkpoint
from reachpatch.models.execution import ReachAvoidState, StateCheckpoint, GeneratorSession
from pathlib import Path


def test_checkpoint_tree_records_children_and_reject_backtrack():
    graph = DynamicReachAvoidGraph()
    parent = CheckpointState("p", None, "", "p", status="OPEN")
    graph.register_checkpoint(parent)
    child = CheckpointState("c", "p", "diff", "c", status="OPEN", depth=1)
    graph.register_checkpoint(child)
    graph.record_transition("p", "c", "REJECT_TRIAL", evidence_ids=("failure",))
    assert graph.metrics["graph_backtrack_count"] == 1
    assert graph.checkpoint_tree_view()["checkpoints"]


def test_hypothesis_mechanisms_do_not_duplicate():
    parent = CheckpointState("p", None, "", "p")
    graph = DynamicReachAvoidGraph()
    cut1 = graph.add_node(GraphNodeKind.BRANCH, node_id="cut1", file="pkg.py", symbol="f", line_start=1, line_end=1)
    cut2 = graph.add_node(GraphNodeKind.VALUE, node_id="cut2", file="pkg.py", symbol="x", line_start=2, line_end=2)
    from reachpatch.reach_avoid.dynamic_reach_avoid_graph import CausalCutCandidate
    cuts = (CausalCutCandidate("cut1", "goal", branch_ids=("cut1",)), CausalCutCandidate("cut2", "goal", value_flow_ids=("cut2",)))
    values = build_distinct_repair_hypotheses(parent, "goal", "failure", cuts, limit=2)
    assert len(values) == 2
    assert len({value.proposed_mechanism for value in values}) == 2


def test_toy_search_backtracks_and_preserves_target(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def calc(value):\n    return 0\n", encoding="utf-8")
    class Generator:
        calls = 0
        def revise(self, objective, tools, initial=False):
            self.calls += 1
            if initial:
                patch = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,4 @@\n def calc(value):\n-    return 0\n+    if value == 1:\n+        return 2\n+    return 0\n"
            elif self.calls == 2:
                patch = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,4 +1,4 @@\n def calc(value):\n     if value == 1:\n-        return 2\n+        return missing_name\n     return 0\n"
            elif self.calls == 3:
                patch = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,4 +1,2 @@\n def calc(value):\n-    if value == 1:\n-        return 2\n-    return 0\n+    return 3\n"
            else:
                patch = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,4 @@\n def calc(value):\n-    return 3\n+    if value == 1:\n+        return 3\n+    return 0\n"
            tools.apply_patch(patch)
            pending = tools.validation_status()
            while pending["pending_commands"]:
                tools.run_allowed_public_check(pending["pending_commands"][0])
                pending = tools.validation_status()
            tools.finish_revision("toy", "toy-mechanism")
            return {}
    instance = Instance("search-toy", str(repo), "base", "calc must return 3 for one and preserve zero for zero.", public_metadata={"public_checks": (
        {"check_id": "target", "command": ("python", "-c", "from calc import calc; assert calc(1) == 3"), "role": "TARGET", "authority": "A", "symbol_references": ("calc",)},
        {"check_id": "preserve", "command": ("python", "-c", "from calc import calc; assert calc(0) == 0"), "role": "PRESERVATION", "authority": "A", "symbol_references": ("calc",)},
    )})
    run_root = tmp_path / "run"
    from reachpatch.reach_avoid.controller import ReachAvoidConfig
    from reachpatch.reach_avoid.dynamic_reach_avoid_graph import SearchBudget
    # Explicit diversity experiment; the new production default is one cut.
    result = ReachAvoidController(RepairPlayer(Generator()), ReachAvoidConfig(
        search_budget=SearchBudget(branch_factor=3))).run(instance, run_root=run_root)
    assert result.status == "REACHED"
    summary = __import__("json").loads((run_root / "execution_summary.json").read_text())
    assert summary["p0_patch_hash"] != summary["final_patch_hash"]
    assert summary["graph_metrics"]["graph_generated_hypothesis_count"] > 0
    assert summary["graph_metrics"]["graph_backtrack_count"] > 0


def test_final_selector_excludes_rejected_high_score_checkpoint(tmp_path):
    parent = StateCheckpoint("parent", None, str(tmp_path / "parent"), "parent", "", "OPEN", 0, search_score=(1, 1))
    rejected = StateCheckpoint("rejected", "parent", str(tmp_path / "rejected"), "rejected", "", "REJECTED", 1, search_score=(99, 99))
    state = ReachAvoidState(
        clean_snapshot=tmp_path, working_checkpoint=parent, safe_checkpoint=parent,
        best_checkpoint=parent, certified_checkpoint=None, goal_contracts=(),
        target_checks=(), preservation_checks=(), challenge_checks=(), locked_checks=(),
        active_failure=None, dynamic_failure_graph=None, failure_history={},
        transition_history=[], revision_count=1, instance_id="case", run_id="run",
        base_repository=tmp_path, base_commit="base", run_root=tmp_path,
        generator_session=GeneratorSession("session"), checkpoint_history={"parent": parent, "rejected": rejected},
        rejected_patch_hashes={"rejected"},
    )
    assert select_final_checkpoint(state).checkpoint_id == "parent"


def test_final_selector_does_not_replace_nonempty_p0_with_empty_bootstrap_on_tie(tmp_path):
    bootstrap = StateCheckpoint(
        "bootstrap", None, str(tmp_path / "base"), "base", "", "BOOTSTRAP", 0,
    )
    p0 = StateCheckpoint(
        "p0", "bootstrap", str(tmp_path / "p0"), "patch", "diff --git a/a.py b/a.py\n",
        "P0", 0,
    )
    state = ReachAvoidState(
        clean_snapshot=tmp_path, working_checkpoint=p0, safe_checkpoint=p0,
        best_checkpoint=p0, certified_checkpoint=None, goal_contracts=(),
        target_checks=(), preservation_checks=(), challenge_checks=(), locked_checks=(),
        active_failure=None, dynamic_failure_graph=None, failure_history={},
        transition_history=[], revision_count=0, instance_id="case", run_id="run",
        base_repository=tmp_path, base_commit="base", run_root=tmp_path,
        generator_session=GeneratorSession("session"),
        checkpoint_history={"bootstrap": bootstrap, "p0": p0},
    )

    assert select_final_checkpoint(state).checkpoint_id == "p0"
