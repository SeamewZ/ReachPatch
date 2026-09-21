"""Bounded live Flash smoke experiment; public fixture only, no hidden oracle.

This smoke verifies integration, not non-inferiority or SWE-bench resolution.
Use reachavoid_51.runner generate --only for isolated public SWE cases.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidController, ReachAvoidConfig
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.repair.deepseek_agent import DeepSeekAgent, DeepSeekConfig, DeepSeekHTTPTransport


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--key-path", required=True, type=Path)
    parser.add_argument("--max-tokens", type=int, default=120000)
    parser.add_argument("--disable-evidence-reuse", action="store_true")
    parser.add_argument("--fixed-interaction", action="store_true")
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/evidence_efficiency"
    instance = Instance("evidence-efficiency-public-smoke", str(fixture), "fixture-v1",
                        (fixture / "issue.txt").read_text(),
                        visible_tests=("python -m pytest -q test_pkg.py",))
    transport = DeepSeekHTTPTransport(args.key_path.read_text().strip(), model="deepseek-flash")
    controller = ReachAvoidController(RepairPlayer(DeepSeekAgent(transport, DeepSeekConfig())),
        ReachAvoidConfig(max_real_patch_revisions=2, max_case_model_calls=18,
                         max_case_tokens=args.max_tokens, execution_budget_seconds=600,
                         evidence_reuse_enabled=not args.disable_evidence_reuse,
                         demand_driven_interaction_enabled=not args.fixed_interaction))
    result = controller.run_case(instance, run_root=args.root)
    report = json.loads((args.root / "token_efficiency.json").read_text())
    # Public acceptance evaluation is separate from the controller's evidence
    # certification. Never relabel EVIDENCE_LIMITED as REACHED just because
    # this small fixture's visible assertions happen to pass.
    blocks = re.findall(r"```python\s*\n(.*?)```", instance.issue, re.S)
    if len(blocks) != 1:
        raise ValueError("Smoke fixture requires one public reproduction block")
    selected = args.root / "execution_checkpoints" / result.checkpoint_id / "working_tree"
    observations = []
    for tree, label in ((args.root / "clean", "baseline"), (selected, "final")):
        for command, kind in (((sys.executable, "-c", blocks[0]), "target"),
                              ((sys.executable, "-m", "pytest", "-q", "test_pkg.py"), "preservation")):
            for run in range(2):
                executed = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=60)
                observations.append({"checkpoint": label, "kind": kind, "run": run,
                    "command": command, "exit_code": executed.returncode,
                    "stdout": executed.stdout, "stderr": executed.stderr})
    public_pass = all((item["exit_code"] != 0) if (item["checkpoint"], item["kind"]) == ("baseline", "target")
                      else item["exit_code"] == 0 for item in observations)
    summary = {"result": result.to_dict(), "cost": report["budget"],
               "arm": {"evidence_reuse": not args.disable_evidence_reuse,
                       "demand_driven_interaction": not args.fixed_interaction},
               "evaluation_scope": "PUBLIC_LIVE_API_SMOKE_NOT_SWE_RESOLVED",
               "controller_certified": result.status == "REACHED",
               "public_acceptance_observations": observations,
               "success": public_pass and bool(result.unified_diff.strip())}
    (args.root / "smoke_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"status": result.status, "success": summary["success"],
                     "model_calls": report["budget"]["model_calls"],
                     "tokens": report["budget"]["tokens"], "root": str(args.root)}))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
