from __future__ import annotations

import json
from pathlib import Path

from experiments.reachavoid_51 import runner as experiment_runner
from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidController
from reachpatch.reach_avoid.repair_player import RepairPlayer


INITIAL = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,4 @@
 def calc(value):
-    return 0
+    if value == 1:
+        return 2
+    return 0
"""

TARGET_FIX_WITH_REGRESSION = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,4 +1,2 @@
 def calc(value):
-    if value == 1:
-        return 2
-    return 0
+    return 3
"""

REGRESSION_FIX = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,4 @@
 def calc(value):
-    return 3
+    if value == 0:
+        return 0
+    return 3
"""


class FakeGenerator:
    def __init__(self) -> None:
        self.objective_kinds = []
        self.working_values = []

    def revise(self, objective, tools, initial=False):
        self.objective_kinds.append(objective.objective_kind)
        self.working_values.append(tools.read_file("calc.py")["content"])
        if initial:
            patch = INITIAL
        elif objective.primary_requirement.get("preservation"):
            patch = REGRESSION_FIX
        else:
            patch = TARGET_FIX_WITH_REGRESSION
        tools.apply_patch(patch)
        validation = tools.validation_status()
        while validation["pending_commands"]:
            tools.run_allowed_public_check(validation["pending_commands"][0])
            validation = tools.validation_status()
        tools.finish_revision("apply the next evidence-grounded repair", "causal-return")
        return {"summary": "revised current working patch", "mechanism": "causal-return"}


def test_partial_patch_counterexample_provisional_regression_repair_reaches(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "calc.py").write_text(
        "def calc(value):\n    return 0\n", encoding="utf-8",
    )
    (repository / "target_check.py").write_text(
        "from calc import calc\nassert calc(1) == 3\n", encoding="utf-8",
    )
    (repository / "preservation_check.py").write_text(
        "from calc import calc\nassert calc(0) == 0\n", encoding="utf-8",
    )
    public_checks = (
        {
            "check_id": "target-check",
            "command": ("python", "target_check.py"),
            "role": "TARGET",
            "authority": "A",
            "symbol_references": ("calc",),
            "concrete_input": 1,
        },
        {
            "check_id": "preservation-check",
            "command": ("python", "preservation_check.py"),
            "role": "PRESERVATION",
            "authority": "A",
            "symbol_references": ("calc",),
            "concrete_input": 0,
        },
    )
    instance = Instance(
        "integrated",
        str(repository),
        "base",
        "`calc` must return 3 for positive input.",
        public_metadata={"public_checks": public_checks},
    )
    fake = FakeGenerator()
    result = ReachAvoidController(RepairPlayer(fake)).run(
        instance, run_root=tmp_path / "run",
    )
    assert result.status == "REACHED"
    assert result.unified_diff.count("diff --git") == 1
    assert "+    if value == 0:" in result.unified_diff
    assert fake.objective_kinds == [
        "INITIAL_PATCH", "BEHAVIOR_FAILURE", "PRESERVATION_REGRESSION",
    ]
    assert "return 2" in fake.working_values[1]
    assert "return 3" in fake.working_values[2]
    assert not any((tmp_path / "run" / "trials").iterdir())
    assert not any((tmp_path / "run" / "generator_staging").iterdir())
    assert tuple((tmp_path / "run" / "execution_cache").glob("*.json"))
    assert not (tmp_path / "run" / "bootstrap_working").exists()
    assert not (tmp_path / "run" / "initial_working").exists()
    performance = tuple(
        json.loads(line)
        for line in (tmp_path / "run" / "performance.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
    )
    assert performance[0]["revision"] == 0
    # Base-commit AST/index data is intentionally persistent.  A warm cache
    # is valid initial graph participation and must not be reported as a
    # failed graph build merely because no file needed reparsing.
    assert (
        performance[0]["files_reparsed"]
        + performance[0]["cache_hit_count"]
    ) > 0
    assert performance[0]["program_update_seconds"] > 0
    assert any(
        record["revision"] > 0 and record["files_reparsed"] > 0
        for record in performance
    )
    initial = experiment_runner._initial_checkpoint(tmp_path / "run")
    assert initial["status"] == "INITIAL_WORKING"
    assert initial["canonical_diff"].strip()
    evidence = experiment_runner._component_evidence(
        tmp_path / "run", result.to_dict(),
    )
    assert evidence["reach_avoid"]["transition_count"] == 2
    assert evidence["reach_avoid"]["decision_counts"] == {
        "COMMIT_WORKING": 1,
        "KEEP_PROVISIONAL": 1,
    }
    assert evidence["requirement_graph"]["participated"]
    assert evidence["program_graph"]["participated"]
    assert evidence["binding_graph"]["participated"]
    assert evidence["challenge_graph"]["participated"]
    assert evidence["challenge_graph"]["counterexamples_opened"]
