from __future__ import annotations

import json
from pathlib import Path

from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidController
from reachpatch.reach_avoid.repair_player import RepairPlayer


def _run_pending(tools):
    status = tools.validation_status()
    while status["pending_commands"]:
        tools.run_allowed_public_check(status["pending_commands"][0])
        status = tools.validation_status()


class _ImportRepair:
    def revise(self, objective, tools, initial=False):
        if initial:
            patch = (
                "diff --git a/calc.py b/calc.py\n"
                "--- a/calc.py\n+++ b/calc.py\n"
                "@@ -1,2 +1,2 @@\n def calc():\n"
                "-    return None\n+    return numbers.Real\n"
            )
        else:
            patch = (
                "diff --git a/calc.py b/calc.py\n"
                "--- a/calc.py\n+++ b/calc.py\n"
                "@@ -1,2 +1,3 @@\n+import numbers\n def calc():\n"
                "     return numbers.Real\n"
            )
        tools.apply_patch(patch)
        _run_pending(tools)
        tools.finish_revision("edit", "import-fix")
        return {"summary": "edit", "mechanism": "import-fix"}


def test_undefined_name_fix_is_advanced_and_cumulative(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def calc():\n    return None\n", encoding="utf-8")
    instance = Instance(
        "toy-undefined-name", str(repo), "base",
        "calc must return numbers.Real.",
        public_metadata={"public_checks": ({
            "check_id": "target",
            "command": ("python", "-c", "from calc import calc; import numbers; assert calc() is numbers.Real"),
            "role": "TARGET", "authority": "A",
            "symbol_references": ("calc",),
        },)},
    )
    run_root = tmp_path / "run"
    result = ReachAvoidController(RepairPlayer(_ImportRepair())).run(instance, run_root=run_root)
    assert result.status == "REACHED"
    assert "+import numbers" in result.unified_diff
    assert "+    return numbers.Real" in result.unified_diff
    summary = json.loads((run_root / "execution_summary.json").read_text(encoding="utf-8"))
    assert summary["transition_count"] >= 1
    assert summary["p0_patch_hash"] != summary["final_patch_hash"]
    transition_files = tuple((run_root / "transitions").glob("*.json"))
    assert transition_files
    assert any(json.loads(path.read_text(encoding="utf-8"))["decision"] in {"ADVANCE_SAFE", "REACHED"} for path in transition_files)


def test_structured_patch_action_is_supported_by_execution_repair_tools(tmp_path):
    repo = tmp_path / "repo-structured"
    repo.mkdir()
    (repo / "calc.py").write_text("def calc():\n    return 0\n", encoding="utf-8")

    class StructuredRepair:
        def revise(self, objective, tools, initial=False):
            if initial:
                tools.apply_patch(
                    "*** Begin Patch\n"
                    "*** Update File: calc.py\n"
                    "@@\n"
                    " def calc():\n"
                    "-    return 0\n"
                    "+    return 1\n"
                    "*** End Patch\n"
                )
                tools.finish_revision("structured initial edit", "structured")
            return {"summary": "structured", "mechanism": "structured"}

    instance = Instance(
        "toy-structured-patch", str(repo), "base", "calc must return 1.",
        public_metadata={"public_checks": ({
            "check_id": "target",
            "command": ("python", "-c", "from calc import calc; assert calc() == 1"),
            "role": "TARGET", "authority": "A", "symbol_references": ("calc",),
        },)},
    )
    result = ReachAvoidController(RepairPlayer(StructuredRepair())).run(
        instance, run_root=tmp_path / "run-structured"
    )
    assert result.status == "REACHED"
    assert "+    return 1" in result.unified_diff


class _RegressionRepair:
    def revise(self, objective, tools, initial=False):
        if initial:
            patch = (
                "diff --git a/calc.py b/calc.py\n"
                "--- a/calc.py\n+++ b/calc.py\n"
                "@@ -1,2 +1,4 @@\n def calc(value):\n"
                "-    return 0\n+    if value == 1:\n+        return 2\n+    return 0\n"
            )
        else:
            if objective.active_failure and objective.active_failure.kind == "TARGET":
                patch = (
                    "diff --git a/calc.py b/calc.py\n"
                    "--- a/calc.py\n+++ b/calc.py\n"
                    "@@ -1,4 +1,4 @@\n def calc(value):\n"
                    "-    if value == 1:\n-        return 2\n-    return 0\n"
                    "+    if value == 1:\n+        return 3\n+    return 3\n"
                )
            else:
                patch = (
                    "diff --git a/calc.py b/calc.py\n"
                    "--- a/calc.py\n+++ b/calc.py\n"
                    "@@ -1,4 +1,6 @@\n def calc(value):\n     if value == 1:\n         return 3\n-    return 3\n+    if value == 0:\n+        return 0\n+    return 3\n"
                )
        tools.apply_patch(patch)
        _run_pending(tools)
        tools.finish_revision("edit", "preservation-fix")
        return {"summary": "edit", "mechanism": "preservation-fix"}


def test_target_success_is_preserved_while_fixing_regression(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def calc(value):\n    return 0\n", encoding="utf-8")
    instance = Instance(
        "toy-target-regression", str(repo), "base",
        "calc must return 3 for value 1 and preserve zero for value 0.",
        public_metadata={"public_checks": (
            {"check_id": "target", "command": ("python", "-c", "from calc import calc; assert calc(1) == 3"), "role": "TARGET", "authority": "A", "symbol_references": ("calc",)},
            {"check_id": "preserve", "command": ("python", "-c", "from calc import calc; assert calc(0) == 0"), "role": "PRESERVATION", "authority": "A", "symbol_references": ("calc",)},
        )},
    )
    run_root = tmp_path / "run"
    result = ReachAvoidController(RepairPlayer(_RegressionRepair())).run(instance, run_root=run_root)
    assert result.status == "REACHED"
    assert "+    if value == 0:" in result.unified_diff
    assert "+    return 3" in result.unified_diff
    summary = json.loads((run_root / "execution_summary.json").read_text(encoding="utf-8"))
    assert summary["revision_count"] >= 2
    decisions = [json.loads(path.read_text(encoding="utf-8"))["decision"] for path in (run_root / "transitions").glob("*.json")]
    assert "KEEP_REPAIRING" in decisions
    assert any(decision in {"ADVANCE_SAFE", "REACHED"} for decision in decisions)


def test_partial_distance_progress_advances_working_patch(tmp_path):
    repo = tmp_path / "repo-distance"
    repo.mkdir()
    (repo / "calc.py").write_text("def calc():\n    return 0\n", encoding="utf-8")

    class DistanceRepair:
        def revise(self, objective, tools, initial=False):
            if initial:
                value = 1
            elif "return 1" in tools.read_file("calc.py")["content"]:
                value = 2
            else:
                value = 3
            _run_pending(tools)
            tools.apply_patch(
                f"diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def calc():\n-    return {value - 1}\n+    return {value}\n"
            )
            _run_pending(tools)
            tools.finish_revision("reduce target distance", "distance-step")
            return {"summary": "distance step", "mechanism": "distance-step"}

    instance = Instance(
        "toy-distance", str(repo), "base", "calc must return 3.",
        public_metadata={"public_checks": ({
            "check_id": "target", "command": ("python", "-c", "from calc import calc; print(calc())"),
            "role": "TARGET", "authority": "A", "symbol_references": ("calc",),
            "expected": 3,
        },)},
    )
    run_root = tmp_path / "run-distance"
    result = ReachAvoidController(RepairPlayer(DistanceRepair())).run(instance, run_root=run_root)
    assert result.status == "REACHED"
    assert "+    return 3" in result.unified_diff
    transitions = sorted(
        (
            json.loads(path.read_text(encoding="utf-8"))
            for path in (run_root / "transitions").glob("*.json")
        ),
        key=lambda item: item["revision_index"],
    )
    first_progress = transitions[0]["atomic_progress"]["target"]
    assert transitions[0]["decision"] == "ADVANCE_SAFE"
    assert first_progress["parent_distance"] == 2
    assert first_progress["trial_distance"] == 1
    assert first_progress["partial_progress"] is True
