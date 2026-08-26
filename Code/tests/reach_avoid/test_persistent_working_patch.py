from pathlib import Path

from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidController
from reachpatch.reach_avoid.repair_player import RepairPlayer


def _apply(tools, diff):
    tools.apply_patch(diff)
    status = tools.validation_status()
    while status["pending_commands"]:
        tools.run_allowed_public_check(status["pending_commands"][0])
        status = tools.validation_status()
    tools.finish_revision("repair applied", "toy-causal-cut")


class _ToyRepair:
    def revise(self, objective, tools, initial=False):
        current = tools.read_file("calc.py")["content"]
        if not initial and "if value == 0:" in current:
            _apply(tools, "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,4 +1,4 @@\n def calc(value):\n     if value == 0:\n         return 0\n-    return 3\n+    return 3.0\n")
            return {"summary": "complete", "mechanism": "complete"}
        if initial:
            diff = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,4 @@\n def calc(value):\n-    return 0\n+    if value == 1:\n+        return 2\n+    return 0\n"
        elif objective.objective_kind == "PRESERVATION_REGRESSION":
            diff = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,4 @@\n def calc(value):\n-    return 3\n+    if value == 0:\n+        return 0\n+    return 3\n"
        else:
            diff = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,4 +1,2 @@\n def calc(value):\n-    if value == 1:\n-        return 2\n-    return 0\n+    return 3\n"
        _apply(tools, diff)
        return {"summary": "toy revision", "mechanism": objective.objective_kind}


def test_revision_keeps_complete_cumulative_working_diff(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "calc.py").write_text("def calc(value):\n    return 0\n", encoding="utf-8")
    (repository / "target_check.py").write_text("from calc import calc\nassert calc(1) == 3\n", encoding="utf-8")
    (repository / "preservation_check.py").write_text("from calc import calc\nassert calc(0) == 0\n", encoding="utf-8")
    instance = Instance("toy-persistent", str(repository), "base", "calc must return 3 for positive input.", public_metadata={"public_checks": (
        {"check_id": "target", "command": ("python", "target_check.py"), "role": "TARGET", "authority": "A", "symbol_references": ("calc",)},
        {"check_id": "preserve", "command": ("python", "preservation_check.py"), "role": "PRESERVATION", "authority": "A", "symbol_references": ("calc",)},
    )})
    result = ReachAvoidController(RepairPlayer(_ToyRepair())).run(instance, run_root=tmp_path / "run")
    assert result.status == "REACHED"
    assert "+    if value == 0:" in result.unified_diff
    assert "+    return 3" in result.unified_diff
    assert result.patch_hash


def test_checkpoint_runtime_round_trip_preserves_working_identity(state_factory, tmp_path):
    from reachpatch.reach_avoid.checkpoint import CheckpointStore, capture_initial_checkpoint

    state = state_factory()
    state.run_root = tmp_path / "run"
    store = CheckpointStore(state.run_root)
    checkpoint = capture_initial_checkpoint(
        store=store, base_repository=state.base_repository,
        source_tree=Path(state.working_checkpoint.snapshot_tree),
        graph_stack=state.graph_stack, evidence=state.working_checkpoint.evidence,
        locked_checks=state.locked_checks, observations=state.observations,
        status="WORKING", state=state,
    )
    loaded = store.load(checkpoint.checkpoint_id)
    runtime = store.runtime_state(checkpoint.checkpoint_id)
    assert loaded.patch_hash == checkpoint.patch_hash
    assert loaded.graph_hashes == checkpoint.graph_hashes
    assert runtime.safe_checkpoint_id is None
    assert runtime.best_checkpoint_id is None
    assert runtime.certified_checkpoint_id is None
