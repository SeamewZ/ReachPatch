import json

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
        elif objective.objective_kind == "FIX_PRESERVATION":
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
    summary = json.loads((tmp_path / "run" / "execution_summary.json").read_text(encoding="utf-8"))
    assert summary["transition_count"] > 0
    assert summary["p0_patch_hash"] != summary["final_patch_hash"]
    transitions = tuple((tmp_path / "run" / "transitions").glob("*.json"))
    assert transitions
    decisions = {json.loads(path.read_text(encoding="utf-8"))["decision"] for path in transitions}
    assert "KEEP_REPAIRING" in decisions or "ADVANCE_SAFE" in decisions


def test_no_executable_target_stops_before_repair_revision(tmp_path):
    repository = tmp_path / "repo-no-target"
    repository.mkdir()
    (repository / "calc.py").write_text("def calc(value):\n    return value\n", encoding="utf-8")

    class Generator:
        revisions = 0

        def revise(self, objective, tools, initial=False):
            if initial:
                tools.apply_patch(
                    "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def calc(value):\n-    return value\n+    return value + 1\n"
                )
                tools.finish_revision("initial edit", "initial")
            else:
                self.revisions += 1
            return {}

    generator = Generator()
    instance = Instance(
        "toy-no-target", str(repository), "base",
        "The implementation needs improvement.",
    )
    result = ReachAvoidController(RepairPlayer(generator)).run(
        instance, run_root=tmp_path / "run-no-target"
    )
    assert result.status == "EVIDENCE_LIMITED"
    assert generator.revisions == 0


def test_recovery_agent_recovers_target_without_initial_public_check(tmp_path):
    """An issue-grounded probe can create the first executable target.

    There is deliberately no executable target entry here (only a preservation
    check).  The recovery conversation is the only source of the target
    command: it writes a restricted probe, observes two stable clean failures,
    and then lets the repair loop fix the P0 failure.  The fake transport follows the exact
    tool-call protocol used by the production recovery agent, so this test
    exercises the real promotion/validation path without spending API budget.
    """
    repository = tmp_path / "repo-recovery"
    repository.mkdir()
    (repository / "calc.py").write_text(
        "def calc(value=1):\n    return 0\n", encoding="utf-8",
    )

    class RecoveryTransport:
        def complete(self, messages, **kwargs):
            import json

            previous = [
                item for item in messages
                if item.get("role") == "tool" and item.get("content")
            ]
            last = json.loads(previous[-1]["content"]) if previous else {}
            probe_id = str(last.get("probe_id", ""))
            turn = len(previous)
            if turn == 0:
                name, arguments = "write_probe", {
                    "name": "calc_target.py",
                    "source": "from calc import calc\nassert calc(1) == 1\n",
                }
            elif turn == 1:
                name, arguments = "register_observation_contract", {
                    "probe_id": probe_id,
                    "contract": {"comparator": "EXIT_ZERO", "expected": {"exit_code": 0}},
                    "authority": "B",
                }
            elif turn in {2, 3}:
                name, arguments = "run_probe_on_clean", {"probe_id": probe_id}
            elif turn in {4, 5}:
                name, arguments = "run_probe_on_working", {"probe_id": probe_id}
            else:
                name, arguments = "finish_target_recovery", {"summary": "probe complete"}
            return {
                "_request_id": f"recovery-{turn}",
                "tool_calls": [{
                    "id": f"call-{turn}",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }],
            }

    transport = RecoveryTransport()

    class Repair:
        def __init__(self):
            self.transport = transport

        def revise(self, objective, tools, initial=False):
            if initial:
                patch = (
                    "diff --git a/calc.py b/calc.py\n"
                    "--- a/calc.py\n+++ b/calc.py\n"
                    "@@ -1,2 +1,2 @@\n def calc(value=1):\n"
                    "-    return 0\n+    return 1\n"
                )
            else:
                patch = (
                    "diff --git a/calc.py b/calc.py\n"
                    "--- a/calc.py\n+++ b/calc.py\n"
                    "@@ -1,2 +1,4 @@\n def calc(value=1):\n"
                    "-    return 1\n+    if value == 0:\n+        return 0\n+    return 1\n"
                )
            _apply(
                tools,
                patch,
            )
            return {"summary": "recovery toy", "mechanism": "return-correction"}

    run_root = tmp_path / "run-recovery"
    instance = Instance(
        "toy-recovery-without-public-target", str(repository), "base",
        "calc must support returning 1.",
        public_metadata={"public_checks": ({
            "check_id": "preserve-zero",
            "command": ("python", "-c", "from calc import calc; assert calc(0) == 0"),
            "role": "PRESERVATION", "authority": "A", "symbol_references": ("calc",),
        },)},
    )
    result = ReachAvoidController(RepairPlayer(Repair())).run(
        instance, run_root=run_root,
    )
    assert result.status == "REACHED"
    assert "+    return 1" in result.unified_diff
    summary = json.loads((run_root / "execution_summary.json").read_text())
    assert summary["transition_count"] > 0
    assert summary["graph_metrics"]["target_recovery_attempt_count"] > 0
    recovery = json.loads((run_root / "target_recovery.json").read_text())
    assert recovery["target_checks"]
    assert recovery["agent_events"]
    observations = [
        event.get("result", {}).get("observation", {})
        for event in recovery["agent_events"]
        if event.get("tool") in {"run_probe_on_clean", "run_probe_on_working"}
    ]
    assert any(item.get("status") == "FAIL" for item in observations)
    assert any(item.get("status") == "PASS" for item in observations)
