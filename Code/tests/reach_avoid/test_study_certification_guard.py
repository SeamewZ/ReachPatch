"""Regression of the observed false certificate, with real public executions."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidController, ReachAvoidConfig
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.repair.deepseek_agent import DeepSeekAgent


def test_none_regression_is_repaired_before_certification(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "fixtures/evidence_efficiency"
    repository = tmp_path / "repo"
    shutil.copytree(fixture, repository, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

    class Provider:
        calls = 0
        def complete(self, messages, **kwargs):
            self.calls += 1
            assert self.calls <= 2, "public evidence must suffice without recovery dialogue"
            if self.calls == 1:
                change = "-    return [values[0]]\n+    if not values:\n+        return []\n+    return [values[0]]"
            else:
                change = "-    if not values:\n+    if len(values) == 0:"
            patch = "*** Begin Patch\n*** Update File: pkg.py\n@@\n" + change + "\n*** End Patch"
            return {"role": "assistant", "content": "", "_usage": {"total_tokens": 100},
                    "tool_calls": [{"id": str(self.calls), "type": "function", "function": {
                        "name": "apply_patch", "arguments": json.dumps({"patch": patch})}}]}

    provider = Provider()
    controller = ReachAvoidController(RepairPlayer(DeepSeekAgent(provider)),
        ReachAvoidConfig(max_real_patch_revisions=2, max_case_model_calls=6))
    result = controller.run_case(Instance("certificate-regression", str(repository), "fixture",
        (repository / "issue.txt").read_text(), visible_tests=("python -m pytest -q test_pkg.py",)),
        run_root=tmp_path / "run")
    assert provider.calls == 2 and result.status == "REACHED"
    selected = tmp_path / "run/execution_checkpoints" / result.checkpoint_id / "working_tree"
    observation = subprocess.run([sys.executable, "-m", "pytest", "-q", "test_pkg.py"],
                                 cwd=selected, capture_output=True, text=True)
    assert observation.returncode == 0, observation.stdout
    checkpoint = json.loads((selected.parent / "checkpoint.json").read_text())
    assert checkpoint["preservation_results"]
    assert all(item["stable"] and item["status"] == "PASS" for item in checkpoint["preservation_results"])
