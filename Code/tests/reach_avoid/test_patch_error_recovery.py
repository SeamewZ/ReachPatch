"""Production recovery from malformed and non-applicable model edits."""
import json

import pytest

from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidController, ReachAvoidConfig
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.repair.deepseek_agent import DeepSeekAgent


@pytest.mark.parametrize("bad_arguments", ["mismatch", "[]", "null", "{bad-json"])
def test_failed_patch_refreshes_source_and_retries_without_crashing(tmp_path, bad_arguments):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "def target(values):\n    return [values[0]]\n"
    (repo / "pkg.py").write_text(source)
    good_patch = "*** Begin Patch\n*** Update File: pkg.py\n@@\n-    return [values[0]]\n+    return [values[0]] if len(values) else []\n*** End Patch"
    bad_patch = good_patch.replace("-    return [values[0]]", "-    return DOES_NOT_EXIST")

    class Provider:
        calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            assert self.calls <= 3
            if self.calls == 1:
                name, arguments = "read_file", json.dumps({"path": "pkg.py"})
            elif self.calls == 2:
                name = "apply_patch"
                arguments = json.dumps({"patch": bad_patch}) if bad_arguments == "mismatch" else bad_arguments
            else:
                tool_reply = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))
                assert "error" in tool_reply
                assert "exact_current_source" in tool_reply
                assert "return [values[0]]" in json.dumps(tool_reply["exact_current_source"])
                name, arguments = "apply_patch", json.dumps({"patch": good_patch})
            return {"role": "assistant", "content": "", "_usage": {"total_tokens": 100},
                    "tool_calls": [{"id": str(self.calls), "type": "function",
                                    "function": {"name": name, "arguments": arguments}}]}

    provider = Provider()
    result = ReachAvoidController(RepairPlayer(DeepSeekAgent(provider))).run_case(
        Instance("patch-error-recovery", str(repo), "base",
                 "target should return an empty list for empty input:\n\n```python\nfrom pkg import target\nassert target([]) == []\n```"),
        run_root=tmp_path / "run")
    assert result.status == "REACHED"
    assert provider.calls == 3
    assert (repo / "pkg.py").read_text() == source
    budget = json.loads((tmp_path / "run/case_budget.json").read_text())
    assert budget["model_calls"] == 3 and budget["usage"]["total_tokens"] == 300
    updates = [json.loads(line) for line in (tmp_path / "run/graph_updates.jsonl").read_text().splitlines()]
    assert any(event.get("event") == "PATCH_APPLY_FAILED" for event in updates)


@pytest.mark.parametrize("suppression", [False, True])
def test_tool_duplicate_suppression_obeys_actual_ablation_policy(tmp_path, suppression):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg.py").write_text("def target(values):\n    return [values[0]]\n")
    patch = "*** Begin Patch\n*** Update File: pkg.py\n@@\n-    return [values[0]]\n+    return [values[0]] if len(values) else []\n*** End Patch"

    class Provider:
        calls = 0
        def complete(self, messages, **kwargs):
            self.calls += 1
            assert self.calls <= 3
            if self.calls == 3:
                reply = json.loads(next(m["content"] for m in reversed(messages) if m["role"] == "tool"))
                assert (reply.get("status") == "REUSED_EVIDENCE") == suppression
                name, arguments = "apply_patch", {"patch": patch}
            else:
                name, arguments = "read_file", {"path": "pkg.py"}
            return {"role": "assistant", "content": "", "_usage": {"total_tokens": 100},
                    "tool_calls": [{"id": str(self.calls), "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments)}}]}

    result = ReachAvoidController(RepairPlayer(DeepSeekAgent(Provider())),
        ReachAvoidConfig(suppress_same_version_actions=suppression,
                         compact_duplicate_payloads=False)).run_case(
        Instance("suppression-policy", str(repo), "base",
                 "target should return an empty list for empty input:\n\n```python\nfrom pkg import target\nassert target([]) == []\n```"),
        run_root=tmp_path / "run")
    assert result.status == "REACHED"
