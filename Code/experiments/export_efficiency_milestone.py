"""Export a small, reproducible interim report without prompts or credentials."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--method", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol = read(args.baseline / "protocol.json")
    candidates = sorted((read(p) for p in (args.baseline / "results").glob("*.json")),
                        key=lambda x: x["completed_at"])
    # This milestone was reported at this cutoff, before any baseline harness.
    cutoff = "2026-09-20T09:42:08.182309+00:00"
    selected = [x for x in candidates if x["completed_at"] <= cutoff]
    if len(selected) != 20:
        raise ValueError("The original 20-case milestone cannot be reconstructed")
    method = {x["instance_id"]: x for x in read(args.method / "generation_summary.json")["results"]}
    rows = []
    for b in selected:
        row = {"instance_id": b["instance_id"], "baseline_completed_at": b["completed_at"]}
        for arm, result in (("baseline", b), ("method", method[b["instance_id"]])):
            if result["implementation_hash"] != protocol["implementation_hash"]:
                raise ValueError("Implementation hash mismatch")
            budget_path = Path(result["run_root"]) / "case_budget.json"
            budget = read(budget_path)
            row.update({f"{arm}_model_calls": budget["model_calls"],
                        f"{arm}_total_tokens": budget["usage"]["total_tokens"],
                        f"{arm}_budget_sha256": sha(budget_path),
                        f"{arm}_p0_patch_sha256": result["p0_patch_sha256"],
                        f"{arm}_final_patch_sha256": result["final_patch_sha256"],
                        f"{arm}_terminal_status": result["status"]})
        rows.append(row)
    totals = {key: sum(r[key] for r in rows) for key in (
        "baseline_model_calls", "method_model_calls", "baseline_total_tokens", "method_total_tokens")}
    if totals != {"baseline_model_calls": 439, "method_model_calls": 323,
                  "baseline_total_tokens": 6264375, "method_total_tokens": 4428109}:
        raise ValueError(f"Published milestone does not match artifacts: {totals}")
    summary = {
        "status": "INTERIM_COST_RESULT_NOT_COMPLETED", "case_count": 20,
        "cutoff_utc": cutoff, "implementation_hash": protocol["implementation_hash"],
        "model": protocol["model"], "temperature": protocol["temperature"],
        "thinking": protocol["thinking"], "seed": protocol["seed"],
        "totals": totals,
        "model_call_reduction": 1 - totals["method_model_calls"] / totals["baseline_model_calls"],
        "token_reduction": 1 - totals["method_total_tokens"] / totals["baseline_total_tokens"],
        "counting_scope": "Selected successful generation attempt only; excludes archived attempts.",
        "cohort_selection": "All 20 baseline results present at the recorded cutoff, matched by instance ID.",
        "baseline_official_result": None,
        "limitations": ["Partial completion cohort, not a random sample.",
                        "Repair non-inferiority and full-cohort token reduction are not established.",
                        "Historical method arm has unequal retries and missing usage artifacts.",
                        "Reported controller REACHED is not official resolved."]}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "interim_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (args.output / "paired_cases.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    portable_protocol = {k: v for k, v in protocol.items() if not k.endswith("_root")}
    (args.output / "protocol.json").write_text(json.dumps(portable_protocol, indent=2) + "\n")
    harness = read(args.method / "harness/harness_summary.json")
    portable_harness = {stage: {k: h[k] for k in (
        "submitted_instances", "completed_instances", "resolved_instances", "resolved_ids",
        "unresolved_ids", "error_ids", "predictions_sha256", "report_sha256", "completed_at")}
        for stage, h in harness.items() if stage in {"p0", "final"}}
    (args.output / "method49_official_summary.json").write_text(json.dumps(portable_harness, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
