"""Reconcile already-run harness reports without rerunning containers.

Some SWE-bench harness releases write ``<arm>.<run_id>.json`` relative to
the process cwd even when ``--report_dir`` is supplied.  This post-seal tool
only reads those immutable reports and rebuilds the cohort outcome ledger.
It never calls the model or evaluation harness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reconcile(root: Path) -> dict:
    code = Path(__file__).resolve().parents[1]
    protocol = json.loads((root / "protocol.json").read_text())
    seal = json.loads((root / "sealed_study.json").read_text())
    if digest((root / "protocol.json").read_bytes()) != seal["protocol_sha256"]:
        raise ValueError("cohort protocol changed after sealing")
    outcomes = []
    reports = {}
    for repetition in range(protocol["repetitions"]):
        for arm in protocol["arms"]:
            location = root / "evaluation" / f"r{repetition}-{arm}"
            predictions = location / "predictions.jsonl"
            run_id = f"evidence-{digest(predictions.read_bytes())[:16]}"
            candidates = list(location.glob(f"*.{run_id}.json"))
            root_report = code / f"{arm}.{run_id}.json"
            if root_report.is_file():
                candidates.append(root_report)
            candidates = list(dict.fromkeys(candidates))
            if len(candidates) != 1:
                raise FileNotFoundError(f"expected one immutable report for {arm}: {candidates}")
            report_path = candidates[0]
            report = json.loads(report_path.read_text())
            if report.get("error_ids"):
                raise RuntimeError(f"harness report contains errors: {report_path}")
            reports[arm] = {"path": str(report_path),
                            "sha256": digest(report_path.read_bytes()),
                            "resolved": len(report.get("resolved_ids", [])),
                            "completed": len(report.get("completed_ids", [])),
                            "unresolved": len(report.get("unresolved_ids", [])),
                            "empty_patch": len(report.get("empty_patch_ids", []))}
            cells = [r for r in seal["cells"]
                     if r["repetition"] == repetition and r["arm"] == arm]
            for row in cells:
                instance_id = row["instance_id"]
                resolved = (True if instance_id in report.get("resolved_ids", []) else
                            False if instance_id in report.get("unresolved_ids", [])
                            or row["status"] == "GENERATION_FAILED" else None)
                outcomes.append({"instance_id": instance_id, "arm": arm,
                                 "repetition": repetition, "resolved": resolved,
                                 "patch_sha256": row["patch_sha256"]})
    result = {"rows": outcomes, "reports": reports,
              "reconciliation": "IMMUTABLE_HARNESS_REPORTS_NO_RERUN",
              "arms": {arm: {
                  "n": len([r for r in outcomes if r["arm"] == arm]),
                  "resolved": sum(r["resolved"] is True for r in outcomes if r["arm"] == arm),
                  "missing": sum(r["resolved"] is None for r in outcomes if r["arm"] == arm),
                  "lower": sum(r["resolved"] is True for r in outcomes if r["arm"] == arm) / len([r for r in outcomes if r["arm"] == arm]),
                  "upper": sum(r["resolved"] is not False for r in outcomes if r["arm"] == arm) / len([r for r in outcomes if r["arm"] == arm]),
              } for arm in protocol["arms"]}}
    (root / "official_outcomes.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    result = reconcile(args.root.resolve())
    print(json.dumps(result["arms"], indent=2))
