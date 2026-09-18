"""Sealed Diagnostic 10 acceptance from generation and official result artifacts.

No official file is opened before the all-case generation seal is verified.
Patch construction and selection never import this evaluation-only module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_diagnostic(root: Path) -> dict[str, Any]:
    seal_path = root / "sealed_generation.json"
    if not seal_path.is_file():
        return {"status": "NOT_COMPLETED", "unmet": ["ALL_TEN_PATCHES_NOT_SEALED"], "cases": []}
    seal = _json(seal_path)
    ids = tuple(seal.get("instance_ids", ()))
    if seal.get("case_count") != 10 or len(ids) != 10 or len(set(ids)) != 10:
        return {"status": "NOT_COMPLETED", "unmet": ["INVALID_TEN_CASE_SEAL"], "cases": []}
    cases = []
    for case_id in ids:
        result = _json(root / "results" / f"{case_id}.json")
        for kind in ("p0", "final"):
            patch = Path(result[f"{kind}_patch_path"])
            if hashlib.sha256(patch.read_bytes()).hexdigest() != result[f"{kind}_patch_sha256"]:
                raise ValueError(f"{case_id}: {kind} patch changed after generation")
        run = Path(result["run_root"])
        summary = _json(run / "execution_summary.json")
        view = _json(run / "checkpoint_tree_view.json")
        checkpoints = view["checkpoints"]
        parents = Counter(item["parent_checkpoint_id"] for item in checkpoints
                          if item.get("parent_checkpoint_id"))
        transitions = [json.loads(line) for line in (run / "transitions.jsonl").read_text().splitlines() if line]
        selection = _json(run / "final_selection.json")
        recovery = _json(run / "target_recovery.json")
        metrics = summary["graph_metrics"]
        cases.append({
            "instance_id": case_id, "p0_hash": result["p0_patch_hash"], "final_hash": result["final_patch_hash"],
            "checkpoint_graph_view": view, "transitions": transitions, "selection": selection,
            "has_sibling_search": any(count >= 2 for count in parents.values()),
            "has_parent_backtrack": any(item["decision"] == "REJECT_TRIAL" for item in transitions)
                                   and metrics.get("graph_backtrack_count", 0) > 0,
            "recovery_success": bool(recovery.get("target_checks")),
            "recovery_exhausted_reasons": recovery.get("exhausted_reasons", ()),
            "entered_repair_loop": bool(metrics.get("entered_repair_loop")),
            "graph_metrics": metrics, "budget": _json(run / "case_budget.json"),
        })
    unmet = []
    conditions = {
        "ALL_FINAL_PATCHES_EQUAL_P0": any(item["p0_hash"] != item["final_hash"] for item in cases),
        "NO_TARGET_RECOVERY": any(item["recovery_success"] for item in cases),
        "NO_REAL_SIBLING_SEARCH": any(item["has_sibling_search"] for item in cases),
        "NO_PARENT_BACKTRACK": any(item["has_parent_backtrack"] for item in cases),
        "NO_GRAPH_LOCALIZATION": any(item["graph_metrics"].get("graph_localization_decision_count", 0) for item in cases),
    }
    unmet.extend(reason for reason, passed in conditions.items() if not passed)
    # Generation seal and per-patch hashes have now been checked for all ten.
    official_path = root / "harness" / "harness_summary.json"
    official = {}
    if not official_path.is_file():
        unmet.append("OFFICIAL_P0_FINAL_EVALUATION_MISSING")
    else:
        official = _json(official_path)
        p0 = set(official["p0"]["resolved_ids"])
        final = set(official["final"]["resolved_ids"])
        if not p0.union(final).issubset(ids):
            raise ValueError("official result includes cases outside generation seal")
        if len(final) < len(p0):
            unmet.append("FINAL_RESOLVED_BELOW_P0")
        if not final - p0:
            unmet.append("NO_P0_TO_FINAL_IMPROVEMENT")
        for case in cases:
            case.update(p0_resolved=case["instance_id"] in p0, final_resolved=case["instance_id"] in final)
        official = {"p0_resolved": len(p0), "final_resolved": len(final),
                    "improved_ids": sorted(final - p0), "regressed_ids": sorted(p0 - final)}
    return {"status": "NOT_COMPLETED" if unmet else "DIAGNOSTIC10_PASSED",
            "unmet": unmet, "cases": cases, "official_comparison": official,
            "resolved_at_1_uplift_claim": False,
            "note": "Diagnostic acceptance is not the A/B/C/D or 51-case acceptance."}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_diagnostic(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "unmet": report["unmet"]}))
    return 0 if report["status"] == "DIAGNOSTIC10_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
