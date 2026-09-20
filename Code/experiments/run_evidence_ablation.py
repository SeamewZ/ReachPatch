"""Run the four matched public-evidence cost ablation arms.

This is an integration/cost experiment, not an official SWE-bench evaluation.
Every arm uses DeepSeek Flash, temperature zero, the same fixture and the same
hard call/token/wall limits.  Results preserve controller certification and
public acceptance as separate fields.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ARMS = (
    ("A_fixed_no_reuse", False, False),
    ("B_fixed_reuse", True, False),
    ("C_demand_no_reuse", False, True),
    ("D_demand_reuse", True, True),
)


def implementation_hash(code_root: Path) -> str:
    """Hash production Python sources used by every sequential arm."""
    digest = hashlib.sha256()
    for path in sorted((code_root / "reachpatch").rglob("*.py")):
        digest.update(str(path.relative_to(code_root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--key-path", required=True, type=Path)
    parser.add_argument("--max-tokens", type=int, default=200_000)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    code_root = Path(__file__).resolve().parents[1]
    initial_implementation_hash = implementation_hash(code_root)
    records = []
    for name, reuse, demand in ARMS:
        arm_root = args.root / name
        command = [
            sys.executable, "-m", "experiments.run_evidence_efficiency",
            "--root", str(arm_root), "--key-path", str(args.key_path),
            "--max-tokens", str(args.max_tokens),
        ]
        if not reuse:
            command.append("--disable-evidence-reuse")
        if not demand:
            command.append("--fixed-interaction")
        before_hash = implementation_hash(code_root)
        completed = subprocess.run(command, cwd=code_root,
                                   text=True, capture_output=True, check=False)
        after_hash = implementation_hash(code_root)
        summary_path = arm_root / "smoke_summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.is_file() else {}
        cost = summary.get("cost", {})
        elapsed_seconds = cost.get("elapsed_seconds")
        if elapsed_seconds is None and isinstance(cost.get("remaining_wall_seconds"), (int, float)):
            elapsed_seconds = max(0.0, 600.0 - float(cost["remaining_wall_seconds"]))
        records.append({
            "arm": name, "evidence_reuse": reuse,
            "demand_driven_interaction": demand,
            "return_code": completed.returncode,
            "public_acceptance": bool(summary.get("success")),
            "controller_certified": bool(summary.get("controller_certified")),
            "model_calls": cost.get("model_calls"), "tokens": cost.get("tokens"),
            "wall_seconds": elapsed_seconds,
            "implementation_hash_before": before_hash,
            "implementation_hash_after": after_hash,
            "stdout_tail": completed.stdout[-1000:], "stderr_tail": completed.stderr[-1000:],
        })
    implementation_stable = all(
        item["implementation_hash_before"] == initial_implementation_hash
        and item["implementation_hash_after"] == initial_implementation_hash
        for item in records
    )
    report = {
        "scope": "PUBLIC_MATCHED_ABLATION_NOT_OFFICIAL_RESOLVED",
        "model": "deepseek-flash", "temperature": 0,
        "thinking": "disabled",
        "implementation_hash": initial_implementation_hash,
        "implementation_stable": implementation_stable,
        "budget": {"max_model_calls": 18, "max_tokens": args.max_tokens,
                   "wall_seconds": 600, "fixed_recovery_max_agent_turns": 6},
        "max_tokens_per_arm": args.max_tokens,
        "repair_preserved_across_arms": all(item["public_acceptance"] for item in records),
        "records": records,
        "claims": {
            "token_reduction": "SUPPORTED" if (
                records[-1]["public_acceptance"] == records[0]["public_acceptance"]
                and isinstance(records[-1]["tokens"], int)
                and isinstance(records[0]["tokens"], int)
                and records[-1]["tokens"] < records[0]["tokens"]
            ) else "NOT_ESTABLISHED",
            "official_non_inferiority": "REQUIRES_MULTI_CASE_OFFICIAL_HARNESS",
        },
    }
    (args.root / "ablation_summary.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({"records": records, "claims": report["claims"]}))
    return 0 if implementation_stable and all(item["return_code"] == 0 for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
