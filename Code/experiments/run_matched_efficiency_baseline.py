"""Frozen efficiency ablation; generation uses only the existing public sandbox.

This coordinator never changes the repair implementation. Failed generation
stays in the denominator, and all recorded attempts contribute to cost.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
from experiments.reachavoid_51 import runner


def read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


def write(path, value):
    runner._write_json(path, value)


def attempt_metrics(budget):
    events = budget.get("events", [])
    models = [x for x in events if x.get("kind") == "MODEL"]
    usage = budget.get("usage", {})
    return {
        "model_calls": budget.get("model_calls", len(models)),
        "reported_tokens": usage.get("total_tokens", 0),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "uncached_prompt_tokens": usage.get("prompt_cache_miss_tokens", 0),
        "cached_prompt_tokens": usage.get("prompt_cache_hit_tokens", 0),
        "charged_tokens_including_reservations": budget.get("tokens", 0),
        "calls_without_reported_usage": sum(
            x.get("kind") in {"MODEL", "MODEL_ERROR"} and not x.get("usage_reported")
            for x in events
        ),
        "tool_actions": sum(x.get("tool_call_count", 0) for x in models),
        "duplicate_tool_actions": sum(x.get("duplicate_tool_action_count", 0) for x in models),
        "empty_response_calls": sum(
            x.get("tool_call_count", 0) == 0 and not x.get("has_text_response") for x in models
        ),
        "duplicate_only_calls": sum(
            x.get("tool_call_count", 0) > 0 and x.get("novel_tool_action_count", 0) == 0
            and not x.get("has_text_response") for x in models
        ),
        "exact_duplicate_requests": sum(bool(x.get("exact_request_duplicate")) for x in models),
        "recovery_model_calls": sum(x.get("stage") == "TARGET_RECOVERY" for x in models),
        "execution_seconds_sum": budget.get("execution_seconds", 0),
    }


def case_cost(root, case_id, selected):
    selected_root = Path(selected["run_root"]) if selected else None
    paths = set((root / "runs").glob(case_id))
    paths.update((root / "runs").glob(case_id + ".attempt-*"))
    paths.update((root / "failed_attempts" / case_id).glob("*/run"))
    if selected_root:
        paths.add(selected_root)
    totals = Counter()
    attempts = []
    for path in sorted(paths):
        if not path.is_dir():
            continue
        efficiency = read(path / "token_efficiency.json")
        budget = read(path / "case_budget.json") or efficiency.get("budget", {})
        metrics = attempt_metrics(budget)
        metrics.update({k: efficiency.get(k, 0) for k in (
            "context_original_bytes", "context_sent_bytes", "context_elided_bytes",
            "question_reopen_count", "question_resolution_count",
        )})
        for key in ("SOURCE_READ_REUSED", "VALIDATION_CACHE_HIT", "DUPLICATE_ACTION_PREVENTED"):
            metrics[key.lower()] = efficiency.get("events", {}).get(key, 0)
        totals.update(metrics)
        attempts.append({"path": str(path), "budget_available": bool(budget),
                         "selected": path == selected_root, "metrics": metrics})
    return {
        "totals": dict(totals), "attempts": attempts,
        "attempts_missing_budget": sum(not x["budget_available"] for x in attempts),
        "selected_attempt_metrics": next(
            (x["metrics"] for x in attempts if x["selected"]), None),
        "selected_duration_seconds": selected.get("duration_seconds") if selected else None,
    }


def outcome(stage, cid, generation_failed=False):
    if generation_failed:
        return "GENERATION_FAILED"
    if not stage:
        return "PENDING"
    if cid in stage.get("error_ids", []):
        return "EVALUATION_ERROR"
    if cid in stage.get("resolved_ids", []):
        return "RESOLVED"
    if cid in stage.get("unresolved_ids", []):
        return "UNRESOLVED"
    return "INCOMPLETE"


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "mean": None, "median": None, "p90": None}
    return {"count": len(values), "mean": statistics.mean(values),
            "median": statistics.median(values),
            "p90": ordered[math.ceil(len(ordered) * .9) - 1]}


def report(root):
    protocol = read(root / "protocol.json")
    reference = Path(protocol["reference_root"])
    source = Path(protocol["reference_generation_root"])
    ref_results = {x["instance_id"]: x for x in read(reference / "generation_summary.json")["results"]}
    base_results = {x["instance_id"]: x for x in read(root / "generation_summary.json").get("results", [])}
    rh = read(reference / "harness/harness_summary.json").get("final", {})
    bh = read(root / "harness/harness_summary.json").get("final", {})
    rows = []
    for cid in protocol["instance_ids"]:
        row = {"instance_id": cid}
        for arm, data, location, stage in (
            ("method", ref_results, source, rh), ("baseline", base_results, root, bh)
        ):
            row[arm] = case_cost(location, cid, data.get(cid))
            row[arm]["outcome"] = outcome(stage, cid, bool(bh) and cid not in data)
        rows.append(row)
    arms = {}
    for arm in ("method", "baseline"):
        totals = Counter()
        for row in rows:
            totals.update(row[arm]["totals"])
        outcomes = Counter(row[arm]["outcome"] for row in rows)
        arms[arm] = {
            "totals_all_recorded_attempts": dict(totals), "outcomes": dict(outcomes),
            "attempts_missing_budget": sum(row[arm]["attempts_missing_budget"] for row in rows),
            "per_case": {key: distribution([row[arm]["totals"].get(key, 0) for row in rows])
                         for key in ("model_calls", "reported_tokens", "prompt_tokens", "tool_actions")},
            "resolved_at_1_lower_bound": outcomes["RESOLVED"] / len(rows),
            "reported_tokens_per_resolved": (
                totals["reported_tokens"] / outcomes["RESOLVED"] if outcomes["RESOLVED"] else None),
            "duplicate_tool_action_rate": (
                totals["duplicate_tool_actions"] / totals["tool_actions"] if totals["tool_actions"] else None),
        }
    observed = {"RESOLVED", "UNRESOLVED", "GENERATION_FAILED"}
    paired = [r for r in rows if all(r[a]["outcome"] in observed for a in arms)]
    wins = [r["instance_id"] for r in paired if r["method"]["outcome"] == "RESOLVED" and r["baseline"]["outcome"] != "RESOLVED"]
    losses = [r["instance_id"] for r in paired if r["baseline"]["outcome"] == "RESOLVED" and r["method"]["outcome"] != "RESOLVED"]
    reductions = {}
    for key in ("reported_tokens", "model_calls", "prompt_tokens", "tool_actions"):
        b = arms["baseline"]["totals_all_recorded_attempts"].get(key, 0)
        m = arms["method"]["totals_all_recorded_attempts"].get(key, 0)
        reductions[key] = 1 - m / b if b and bh else None
    payload = {
        "schema": "matched-efficiency-comparison-v1", "cohort_count": len(rows),
        "arms": arms, "paired_valid_count": len(paired), "method_wins": wins,
        "method_losses": losses, "observed_reductions": reductions, "rows": rows,
        "claim": "NOT_ESTABLISHED",
        "limitations": [
            "Historical method arm; backend load and provider model revision are not controlled.",
            "No seed was transmitted by the shared client; temperature zero is not a reproducibility guarantee.",
            "Historical retries were not uniformly capped at two; all readable attempts are included.",
            "Missing budget artifacts and provider usage mean recorded token totals are lower bounds.",
            "Duplicate actions use stage/name/raw-argument fingerprints, not state-aware semantic redundancy.",
            "Execution seconds are sums across concurrent checks, not case wall time.",
            "Evaluation errors are unknown outcomes, not confirmed patch regressions.",
            "This baseline retains the graph; it measures the two efficiency switches, not graph-vs-no-graph.",
        ], "updated_at": runner.utc_now(),
    }
    write(root / "matched_comparison.json", payload)
    lines = ["# Matched efficiency baseline", "", f"Frozen cohort: {len(rows)} cases.", "",
             "| Metric | Method | Baseline |", "|---|---:|---:|"]
    for key in ("model_calls", "reported_tokens", "prompt_tokens", "tool_actions", "duplicate_tool_actions", "empty_response_calls", "duplicate_only_calls"):
        lines.append(f"| {key} (all recorded attempts) | {arms['method']['totals_all_recorded_attempts'].get(key, 0)} | {arms['baseline']['totals_all_recorded_attempts'].get(key, 0)} |")
    lines.extend(["", f"Valid paired evaluations: {len(paired)}; method wins: {len(wins)}; method losses: {len(losses)}.",
                  "", "No superiority or non-inferiority claim is established.", "", *payload["limitations"]])
    (root / "matched_comparison.md").write_text("\n".join(lines) + "\n")
    return payload


def environment(protocol, root):
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("REACHPATCH_") or name in {"DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"}:
            del env[name]
    cfg = protocol["baseline_configuration"]
    env.update({
        "PATH": str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", ""),
        "PYTHONPATH": str(CODE), "PYTHONUNBUFFERED": "1",
        "REACHPATCH_RA51_ROOT": str(root),
        "REACHPATCH_SOURCE_TREE_ROOT": protocol["source_tree_root"],
        "REACHPATCH_CASE_WORKERS": "4", "REACHPATCH_CASE_RETRIES": "2",
        "REACHPATCH_CASE_WALL_SECONDS": str(cfg["execution_budget_seconds"]),
        "REACHPATCH_CASE_MODEL_CALLS": str(cfg["max_case_model_calls"]),
        "REACHPATCH_CASE_TOKENS": str(cfg["max_case_tokens"]),
        "REACHPATCH_FINAL_VALIDATION_RESERVE": str(cfg["final_validation_reserve_seconds"]),
        "REACHPATCH_VALIDATION_WORKERS": str(cfg["validation_workers"]),
        "REACHPATCH_EVIDENCE_REUSE_ENABLED": "0",
        "REACHPATCH_DEMAND_DRIVEN_INTERACTION_ENABLED": "0",
        "DEEPSEEK_MODEL": protocol["model"],
    })
    return env


def checked_process(command, env, root, stage):
    with (root / f"{stage}.log").open("a") as log:
        process = subprocess.Popen(command, cwd=CODE, env=env, stdout=log, stderr=subprocess.STDOUT)
        while True:
            write(root / "progress.json", {"stage": stage, "pid": os.getpid(),
                "child_pid": process.pid, "result_files": len(list((root / "results").glob("*.json"))),
                "updated_at": runner.utc_now()})
            try:
                return process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                continue


def prepare(root, reference, source, trees):
    manifest = read(source / "generation_manifest.json")
    selected = read(reference / "sealed_generation.json")
    if manifest["implementation_hash"] != runner._implementation_hash():
        raise RuntimeError("Reference implementation differs; cannot claim a matched ablation")
    ids = [x for x in manifest["cohort_instance_ids"] if x in selected["instance_ids"]]
    if len(ids) != selected["case_count"]:
        raise RuntimeError("Reference cohort mismatch")
    config = dict(manifest["case_configuration"])
    baseline = {**config, "evidence_reuse_enabled": False, "demand_driven_interaction_enabled": False}
    protocol = {
        "schema": "matched-efficiency-protocol-v1", "created_at": runner.utc_now(),
        "reference_root": str(reference), "reference_generation_root": str(source),
        "source_tree_root": str(trees), "instance_ids": ids,
        "cohort_hash": runner.content_hash(ids), "implementation_hash": runner._implementation_hash(),
        "public_dataset_sha256": runner._sha256(runner.PUBLIC_PATH),
        "model": manifest["model"], "temperature": 0, "thinking": "disabled", "seed": None,
        "method_configuration": config, "baseline_configuration": baseline,
        "max_revisions": manifest["max_revisions"], "case_retries": 2, "case_workers": 4,
        "harness_workers": 4, "harness_timeout": 1800,
        "generation_failure_policy": "Keep failed cases in denominator with empty sealed predictions; no outcome-conditioned retries.",
        "primary_metrics": ["official_resolved_at_1", "all_attempt_tokens", "all_attempt_model_calls"],
        "interpretation": "Retrospective same-code ablation, not a randomized contemporaneous comparison or external SOTA baseline.",
    }
    root.mkdir(parents=True, exist_ok=False)
    write(root / "protocol.json", protocol)
    # Archive source for reproducibility without copying datasets or secrets.
    shutil.copytree(CODE / "reachpatch", root / "implementation_snapshot/reachpatch",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(Path(runner.__file__), root / "implementation_snapshot/runner.py")
    report(root)


def seal_with_failures(root, protocol):
    summary = read(root / "generation_summary.json")
    if not summary or summary["implementation_hash"] != protocol["implementation_hash"]:
        raise RuntimeError("Generation summary missing or implementation drifted")
    results = {x["instance_id"]: x for x in summary.get("results", [])}
    if not results:
        raise RuntimeError("No valid generated patches; resolve infrastructure before evaluation")
    sealed = {"schema": runner.SCHEMA, "case_count": len(protocol["instance_ids"]),
              "instance_ids": protocol["instance_ids"], "sealed_at": runner.utc_now(),
              "implementation_hash": protocol["implementation_hash"],
              "generation_failed_ids": [c for c in protocol["instance_ids"] if c not in results]}
    for stage in ("p0", "final"):
        rows = []
        for cid in protocol["instance_ids"]:
            result = results.get(cid)
            path = Path(result[f"{stage}_patch_path"]) if result else None
            if result and runner._sha256(path) != result[f"{stage}_patch_sha256"]:
                raise RuntimeError(f"Patch changed before sealing: {cid}")
            rows.append({"instance_id": cid, "model_name_or_path": f"reachpatch-{stage}",
                         "model_patch": path.read_text() if path else ""})
        prediction = root / f"harness/sealed_{stage}_predictions.jsonl"
        runner._write_jsonl(prediction, rows)
        sealed[f"{stage}_predictions_sha256"] = runner._sha256(prediction)
    write(root / "sealed_generation.json", sealed)


def run(root, key_path):
    protocol = read(root / "protocol.json")
    with (root / "coordinator.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if runner._implementation_hash() != protocol["implementation_hash"]:
            raise RuntimeError("Implementation changed after preregistration")
        env = environment(protocol, root)
        os.environ.update({k: v for k, v in env.items() if k.startswith("REACHPATCH_")})
        if asdict(runner._case_configuration(protocol["max_revisions"])) != protocol["baseline_configuration"]:
            raise RuntimeError("Effective baseline configuration does not match protocol")
        script = [sys.executable, str(Path(runner.__file__))]
        if not (root / "generation_summary.json").exists():
            command = [*script, "generate", "--key-path", str(key_path), "--model", protocol["model"],
                       "--max-revisions", str(protocol["max_revisions"])]
            for cid in protocol["instance_ids"]:
                command.extend(["--only", cid])
            checked_process(command, env, root, "generation")
        if runner._implementation_hash() != protocol["implementation_hash"]:
            raise RuntimeError("Implementation changed during generation")
        seal_with_failures(root, protocol)
        report(root)
        # Official information is read only after all baseline attempts end and seal.
        for label, location in (("baseline", root), ("method", Path(protocol["reference_root"]))):
            local_env = {**env, "REACHPATCH_RA51_ROOT": str(location)}
            # Preserve prior reports; swebench reuses completed per-case reports
            # and retries cases that lack a report without changing any patch.
            backup = root / f"{label}_harness_before_retry"
            backup.mkdir(exist_ok=True)
            for stage in ("p0", "final"):
                stage_dir = location / "harness" / stage
                for path in [*stage_dir.glob("*.json"), *stage_dir.glob("*.log")]:
                    dest = backup / f"{stage}-{path.name}"
                    if not dest.exists():
                        shutil.copy2(path, dest)
            checked_process([*script, "harness", "--workers", str(protocol["harness_workers"]),
                             "--timeout", str(protocol["harness_timeout"])], local_env, root, label + "_harness")
        payload = report(root)
        pending = sum(v["outcomes"].get("PENDING", 0) + v["outcomes"].get("EVALUATION_ERROR", 0)
                      + v["outcomes"].get("INCOMPLETE", 0) for v in payload["arms"].values())
        write(root / "progress.json", {"stage": "FINISHED_WITH_UNCERTAINTY" if pending else "FINISHED",
                                       "updated_at": runner.utc_now(), "unresolved_evaluations": pending})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run", "report"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--trees", type=Path)
    parser.add_argument("--key-path", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "prepare":
        prepare(root, args.reference.resolve(), args.source.resolve(), args.trees.resolve())
    elif args.command == "report":
        report(root)
    else:
        try:
            run(root, args.key_path.resolve())
        except Exception as exc:
            write(root / "progress.json", {"stage": "FAILED", "error": str(exc), "updated_at": runner.utc_now()})
            raise


if __name__ == "__main__":
    main()
