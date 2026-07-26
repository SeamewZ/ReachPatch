from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from reachpatch.models.base import utc_now
from reachpatch.models.isolation import GenerationInstance, HarnessEvaluationInstance
from reachpatch.reach_avoid.controller import (
    AnalysisBlocked,
    ReachPatchConfig,
    ReachPatchController,
)
from reachpatch.repair.deepseek_agent import (
    DeepSeekHTTPTransport,
    PersistentDeepSeekAgent,
)


CODE_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = CODE_ROOT / "dataset" / "patchpsro_55_unique51"
PUBLIC_PATH = DATASET_ROOT / "generation_public_instances.jsonl"
OFFICIAL_PATH = DATASET_ROOT / "official_instances.jsonl"
EXPERIMENT_ROOT = CODE_ROOT / "experiments" / "swe51"
REPO_ROOT = EXPERIMENT_ROOT / "repos"
TREE_ROOT = EXPERIMENT_ROOT / "case_trees"
RUN_ROOT = EXPERIMENT_ROOT / "runs"
RESULT_ROOT = EXPERIMENT_ROOT / "results"
HARNESS_ROOT = EXPERIMENT_ROOT / "harness"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _failure_rows(stage_summary: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in stage_summary.get("results", []):
        status = str(item.get("status", "UNKNOWN"))
        success = status == "PASS" or (stage == "generation" and status == "GRAPH_REACHED")
        if success:
            continue
        rows.append({
            "instance_id": item.get("instance_id"),
            "stage": stage,
            "status": status,
            "error": item.get("error"),
            "error_traceback": item.get("error_traceback"),
            "started_at": item.get("started_at"),
            "finished_at": item.get("finished_at"),
            "run_root": item.get("run_root"),
            "result_path": (
                str(RESULT_ROOT / f"{item.get('instance_id')}.json")
                if stage == "generation" and item.get("instance_id")
                else None
            ),
            "patch_path": item.get("patch_path"),
            "transition_count": item.get("transition_count"),
            "analysis_timings": item.get("analysis_timings", {}),
            "analysis_resources": item.get("analysis_resources", {}),
            "analysis_stats": item.get("analysis_stats", {}),
            "graph_summary": item.get("graph_summary", {}),
            "reach_avoid": item.get("reach_avoid"),
            "component_effectiveness": item.get("component_effectiveness", []),
            "worker_return_code": item.get("worker_return_code"),
            "worker_stdout": item.get("worker_stdout", "")[-12000:],
            "worker_stderr": item.get("worker_stderr", "")[-12000:],
            "patch_apply": item.get("patch_apply"),
            "fail_to_pass": item.get("fail_to_pass"),
            "pass_to_pass": item.get("pass_to_pass"),
            "deepseek_calls": item.get("deepseek_calls", []),
        })
    return rows


def _failure_point(row: dict[str, Any]) -> str:
    if row.get("stage") == "official_harness":
        return "official_harness"
    status = str(row.get("status", "UNKNOWN"))
    if status == "SEMANTIC_BLOCKED":
        return "semantic_analysis"
    if status == "NO_LEGAL_ACTION":
        return "repair_action_selection"
    resources = row.get("analysis_resources", {})
    in_progress = [
        stage
        for stage, samples in resources.items()
        if any(str(key).startswith("in_progress_") for key in samples)
        and not any(str(key).startswith("complete_") for key in samples)
    ]
    if in_progress:
        return sorted(in_progress)[-1]
    timings = row.get("analysis_timings", {})
    completed = [
        key.removesuffix("_seconds")
        for key in timings
        if key.endswith("_seconds") and key != "analysis_total_seconds"
    ]
    return sorted(completed)[-1] if completed else "generation"


def write_failure_report(
    *,
    generation_summary: dict[str, Any] | None = None,
    harness_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generation_summary = generation_summary or {}
    harness_summary = harness_summary or {}
    rows = _failure_rows(generation_summary, "generation") + _failure_rows(harness_summary, "official_harness")
    report = {
        "generated_at": utc_now(),
        "case_count": max(
            int(generation_summary.get("case_count", 0)),
            int(harness_summary.get("case_count", 0)),
        ),
        "failure_count": len(rows),
        "failures": rows,
    }
    _write_json(EXPERIMENT_ROOT / "failure_report.json", report)
    lines = [
        "# SWE51 Failure Report",
        "",
        f"- Cases observed: `{report['case_count']}`",
        f"- Failure/unknown rows: `{report['failure_count']}`",
        "",
        "| Case | Stage | Status | Reason | Run root |",
        "|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda item: (str(item.get("instance_id")), str(item.get("stage")))):
        reason = str(row.get("error") or "see stage details").replace("|", "/").replace("\n", " ")[:240]
        lines.append(
            f"| `{row.get('instance_id')}` | `{row.get('stage')}` | `{row.get('status')}` | {reason} | `{row.get('run_root') or ''}` |"
        )
    lines.extend(["", "## Per-case diagnostics", ""])
    for row in sorted(rows, key=lambda item: (str(item.get("instance_id")), str(item.get("stage")))):
        components = row.get("component_effectiveness", [])
        outcome_counts: dict[str, int] = {}
        for component in components:
            for status, count in component.get("outcome_counts", {}).items():
                outcome_counts[str(status)] = outcome_counts.get(str(status), 0) + int(count)
        graph_summary = row.get("graph_summary", {})
        reach_avoid = row.get("reach_avoid") or {}
        timings = row.get("analysis_timings", {})
        resources = row.get("analysis_resources", {})
        lines.extend([
            f"### `{row.get('instance_id')}`",
            "",
            f"- Failure point: `{_failure_point(row)}`",
            f"- Status: `{row.get('status')}`",
            f"- Reason: {str(row.get('error') or 'no legal repair transition was available')}",
            f"- Graph closure: `{graph_summary.get('graph_count', 0)}/{graph_summary.get('expected_full_closure_graph_count', 5)}`",
            f"- Transitions: `{row.get('transition_count') or 0}`",
        ])
        if components:
            lines.append(
                f"- Repair components: `{sum(bool(item.get('effective')) for item in components)}/{len(components)}` effective; outcomes `{json.dumps(outcome_counts, sort_keys=True)}`"
            )
        if reach_avoid:
            lines.append(
                f"- Reach-Avoid: phase `{reach_avoid.get('phase')}`, hard frontier `{reach_avoid.get('hard_frontier_count')}`, PASS/FAIL/UNKNOWN `{reach_avoid.get('pass_pairs')}/{reach_avoid.get('fail_pairs')}/{reach_avoid.get('unknown_pairs')}`"
            )
        if timings:
            lines.append(f"- Stage timings: `{json.dumps(timings, sort_keys=True)}`")
        if resources:
            lines.append(f"- Stage memory: `{json.dumps(resources, sort_keys=True)}`")
        lines.extend([
            f"- Result JSON: `{row.get('result_path') or ''}`",
            f"- Run manifest: `{Path(str(row.get('run_root') or '')) / 'run_manifest.json' if row.get('run_root') else ''}`",
            "",
        ])
    lines.append("Captured traceback/stdout/stderr, patch application results, component outcomes, and DeepSeek call records are in `failure_report.json`; older workers may not have captured a traceback.")
    (EXPERIMENT_ROOT / "failure_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def write_experiment_report(
    generation_summary: dict[str, Any],
    harness_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    harness_summary = harness_summary or {}
    generation_by_id = {str(item["instance_id"]): item for item in generation_summary.get("results", [])}
    harness_by_id = {str(item["instance_id"]): item for item in harness_summary.get("results", [])}
    ids = sorted(set(generation_by_id) | set(harness_by_id))
    rows = [
        {
            "instance_id": case_id,
            "generation_status": generation_by_id.get(case_id, {}).get("status", "MISSING"),
            "harness_status": harness_by_id.get(case_id, {}).get("status", "PENDING"),
            "graph_reached": generation_by_id.get(case_id, {}).get("graph_reached"),
            "patch_hash": generation_by_id.get(case_id, {}).get("patch_hash"),
            "run_root": generation_by_id.get(case_id, {}).get("run_root"),
            "transition_count": generation_by_id.get(case_id, {}).get("transition_count", 0),
            "effective_component_count": sum(
                bool(item.get("effective"))
                for item in generation_by_id.get(case_id, {}).get("component_effectiveness", [])
            ),
            "component_count": len(generation_by_id.get(case_id, {}).get("component_effectiveness", [])),
            "accepted_transition_id": generation_by_id.get(case_id, {}).get("reach_avoid", {}).get("accepted_transition_id"),
            "analysis_timings": generation_by_id.get(case_id, {}).get("analysis_timings", {}),
            "analysis_resources": generation_by_id.get(case_id, {}).get("analysis_resources", {}),
            "analysis_stats": generation_by_id.get(case_id, {}).get("analysis_stats", {}),
            "graph_summary": generation_by_id.get(case_id, {}).get("graph_summary", {}),
            "harness_detail": {
                "fail_to_pass": harness_by_id.get(case_id, {}).get("fail_to_pass", {}).get("status"),
                "pass_to_pass": harness_by_id.get(case_id, {}).get("pass_to_pass", {}).get("status"),
                "patch_apply": harness_by_id.get(case_id, {}).get("patch_apply", {}).get("status"),
            },
        }
        for case_id in ids
    ]
    def counts(values: list[str]) -> dict[str, int]:
        output: dict[str, int] = {}
        for value in values:
            output[value] = output.get(value, 0) + 1
        return output
    timing_summary: dict[str, dict[str, float | int]] = {}
    for row in rows:
        for key, value in row.get("analysis_timings", {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            current = timing_summary.setdefault(key, {"count": 0, "sum_seconds": 0.0, "max_seconds": 0.0})
            current["count"] = int(current["count"]) + 1
            current["sum_seconds"] = float(current["sum_seconds"]) + number
            current["max_seconds"] = max(float(current["max_seconds"]), number)
    for value in timing_summary.values():
        value["mean_seconds"] = float(value["sum_seconds"]) / int(value["count"])
    memory_summary: dict[str, dict[str, float | int]] = {}
    for row in rows:
        for stage, samples in row.get("analysis_resources", {}).items():
            values = [float(value) for key, value in samples.items() if key.endswith("peak_rss_mib")]
            if not values:
                continue
            peak = max(values)
            current = memory_summary.setdefault(
                stage, {"count": 0, "sum_peak_rss_mib": 0.0, "max_peak_rss_mib": 0.0}
            )
            current["count"] = int(current["count"]) + 1
            current["sum_peak_rss_mib"] = float(current["sum_peak_rss_mib"]) + peak
            current["max_peak_rss_mib"] = max(float(current["max_peak_rss_mib"]), peak)
    for value in memory_summary.values():
        value["mean_peak_rss_mib"] = float(value["sum_peak_rss_mib"]) / int(value["count"])
    report = {
        "generated_at": utc_now(),
        "case_count": len(rows),
        "generation_counts": counts([str(row["generation_status"]) for row in rows]),
        "harness_counts": counts([str(row["harness_status"]) for row in rows]),
        "graph_timing_summary": timing_summary,
        "graph_memory_summary": memory_summary,
        "results": rows,
        "failure_report": str(EXPERIMENT_ROOT / "failure_report.json"),
    }
    _write_json(EXPERIMENT_ROOT / "experiment_report.json", report)
    lines = [
        "# SWE51 Experiment Report",
        "",
        f"- Cases: `{report['case_count']}`",
        f"- Generation counts: `{json.dumps(report['generation_counts'], sort_keys=True)}`",
        f"- Harness counts: `{json.dumps(report['harness_counts'], sort_keys=True)}`",
        "",
        "| Case | Generation | Harness | F2P | P2P | Patch apply | Graphs | Components effective | Transitions | Graph reached |",
        "|---|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        detail = row["harness_detail"]
        lines.append(
            f"| `{row['instance_id']}` | `{row['generation_status']}` | `{row['harness_status']}` | "
            f"`{detail['fail_to_pass'] or ''}` | `{detail['pass_to_pass'] or ''}` | "
            f"`{detail['patch_apply'] or ''}` | `{row['graph_summary'].get('graph_count', 0)}/5` | "
            f"`{row['effective_component_count']}/{row['component_count']}` | "
            f"`{row['transition_count']}` | `{row['graph_reached']}` |"
        )
    lines.extend([
        "",
        "## Graph Timing Summary",
        "",
        "| Stage | Cases | Mean seconds | Max seconds | Total seconds |",
        "|---|---:|---:|---:|---:|",
    ])
    for key, value in sorted(timing_summary.items()):
        lines.append(
            f"| `{key}` | {int(value['count'])} | {float(value['mean_seconds']):.3f} | "
            f"{float(value['max_seconds']):.3f} | {float(value['sum_seconds']):.3f} |"
        )
    lines.extend([
        "",
        "## Graph Memory Summary",
        "",
        "| Stage | Cases | Mean peak RSS MiB | Max peak RSS MiB |",
        "|---|---:|---:|---:|",
    ])
    for key, value in sorted(memory_summary.items()):
        lines.append(
            f"| `{key}` | {int(value['count'])} | {float(value['mean_peak_rss_mib']):.1f} | "
            f"{float(value['max_peak_rss_mib']):.1f} |"
        )
    lines.extend(["", "Detailed failure rows and reasons: `failure_report.md` and `failure_report.json`."])
    (EXPERIMENT_ROOT / "experiment_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _public_instance(raw: dict[str, Any], tree: Path):
    return GenerationInstance.from_public_record(raw).to_controller_instance(tree)


def _component_effectiveness(state) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component_id, component in sorted(state.binding_graph.components.items()):
        unit_ids = tuple(component.unit_ids)
        outcomes = [item for item in state.outcomes.values() if item.unit_id in unit_ids]
        counts: dict[str, int] = {}
        for item in outcomes:
            status = item.status.value if hasattr(item.status, "value") else str(item.status)
            counts[status] = counts.get(status, 0) + 1
        pass_count = counts.get("PASS", 0)
        rows.append({
            "component_id": component_id,
            "unit_ids": list(unit_ids),
            "unit_count": len(unit_ids),
            "outcome_count": len(outcomes),
            "outcome_counts": counts,
            "effective": bool(outcomes) and pass_count == len(outcomes),
            "pass_ratio": pass_count / len(outcomes) if outcomes else 0.0,
            "legal_repair_cut_ids": list(component.legal_repair_cut_ids),
            "preservation_node_ids": list(component.preservation_node_ids),
        })
    return rows


def _graph_summary(state) -> dict[str, Any]:
    graph_values = {
        "semantic_hypothesis_graph": {
            "hash": state.semantic_graph.to_dict().get("graph_hash"),
            "artifact_ids": list(state.artifact_ids.get("semantic_hypothesis_graph", ())),
        },
        "requirement_graph": {
            "hash": state.requirement_graph.semantic_layer_hash(),
            "artifact_ids": list(state.artifact_ids.get("requirement_graph", ())),
        },
        "program_graph": {
            "hash": state.program_graph.program_hash(),
            "artifact_ids": list(state.artifact_ids.get("program_graph", ())),
        },
        "binding_graph": {
            "hash": state.binding_graph.graph_hash(),
            "artifact_ids": list(state.artifact_ids.get("binding_graph", ())),
        },
        "challenge_graph": {
            "hash": state.challenge_graph.graph_hash(),
            "artifact_ids": list(state.artifact_ids.get("challenge_graph", ())),
        },
    }
    built = {
        name: value
        for name, value in graph_values.items()
        if value["artifact_ids"] or value["hash"]
    }
    return {
        "graph_count": len(built),
        "graph_names": sorted(built),
        "graphs": built,
        "expected_full_closure_graph_count": 5,
        "full_closure": len(built) == 5,
    }


def _generate_one(
    raw: dict[str, Any],
    transport: DeepSeekHTTPTransport,
    max_revisions: int,
) -> dict[str, Any]:
    case_id = str(raw["instance_id"])
    tree = TREE_ROOT / case_id
    run_root = RUN_ROOT / case_id
    result_path = RESULT_ROOT / f"{case_id}.json"
    if result_path.exists():
        return json.loads(result_path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {
        "instance_id": case_id,
        "repo": raw["repo"],
        "base_commit": raw["base_commit"],
        "started_at": utc_now(),
        "generation_source": "generation_public_instances.jsonl",
    }
    if not tree.is_dir():
        result.update({"status": "BLOCKED_REPOSITORY", "error": str(tree)})
        _write_json(result_path, result)
        return result
    if run_root.exists():
        interrupted_root = RUN_ROOT / "_interrupted" / f"{case_id}-{int(time.time())}"
        interrupted_root.parent.mkdir(parents=True, exist_ok=True)
        run_root.rename(interrupted_root)
    try:
        instance = _public_instance(raw, tree)
        agent = PersistentDeepSeekAgent(transport, max_tool_turns=12)
        controller = ReachPatchController(
            config=ReachPatchConfig(
                selection_mode="hypothesis_set",
                max_submitted_revisions=max_revisions,
                max_internal_tool_turns_per_revision=12,
            ),
            generator_agent=agent,
            implementation_root=CODE_ROOT,
        )
        state, certificate = controller.run(instance, run_root=run_root)
        manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
        result.update({
            "status": certificate.status,
            "graph_reached": bool(certificate.graph_reached),
            "run_root": str(run_root),
            "terminal_certificate": certificate.to_dict(),
            "patch_path": str(run_root / "final_patch.diff"),
            "patch_hash": state.checkpoint.patch.canonical_diff_hash,
            "transition_count": state.transition_index,
            "reach_avoid": {
                "termination_status": state.termination_status,
                "target_deficit": state.target_deficit(),
                "phase": state.phase.value,
                "graph_reached": bool(state.graph_reached if hasattr(state, "graph_reached") else certificate.graph_reached),
                "hard_frontier_count": len(state.challenge_graph.frontiers),
                "counterexample_count": len(state.counterexamples),
                "accepted_transition_id": state.checkpoint.accepted_transition_id,
                "pass_pairs": len(state.checkpoint.pass_pairs),
                "fail_pairs": len(state.checkpoint.fail_pairs),
                "unknown_pairs": len(state.checkpoint.unknown_pairs),
            },
            "component_effectiveness": _component_effectiveness(state),
            "graph_summary": _graph_summary(state),
            "transition_certificates": [item.to_dict() for item in state.repair_history],
            "phase_history": list(state.phase_history),
            "artifact_ids": state.artifact_ids,
            "analysis_timings": manifest.get("analysis_timings", {}),
            "analysis_resources": manifest.get("analysis_resources", {}),
            "analysis_stats": manifest.get("analysis_stats", {}),
        })
    except AnalysisBlocked as exc:
        result.update({"status": exc.status, "error": exc.detail, "run_root": str(run_root)})
    except Exception as exc:
        result.update({
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "error_traceback": traceback.format_exc(),
            "run_root": str(run_root),
        })
    manifest_path = run_root / "run_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            result["analysis_timings"] = manifest.get("analysis_timings", {})
            result["analysis_resources"] = manifest.get("analysis_resources", {})
            result["analysis_stats"] = manifest.get("analysis_stats", {})
            result["graph_summary"] = manifest.get("graph_summary", {})
        except (OSError, json.JSONDecodeError):
            result["analysis_timings"] = {}
            result["analysis_resources"] = {}
            result["analysis_stats"] = {}
            result["graph_summary"] = {}
    result["deepseek_calls"] = list(transport.calls)
    result["finished_at"] = utc_now()
    _write_json(result_path, result)
    return result


def _run_case_subprocess(
    raw: dict[str, Any],
    *,
    key_path: Path,
    model: str,
    max_revisions: int,
    timeout: int | None,
) -> dict[str, Any]:
    case_id = str(raw["instance_id"])
    result_path = RESULT_ROOT / f"{case_id}.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "case",
        "--instance-id",
        case_id,
        "--key-path",
        str(key_path),
        "--model",
        model,
        "--max-revisions",
        str(max_revisions),
    ]
    try:
        process = subprocess.run(
            command,
            cwd=CODE_ROOT,
            capture_output=True,
            text=True,
            timeout=None if timeout is None or timeout <= 0 else timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "instance_id": case_id,
            "repo": raw["repo"],
            "base_commit": raw["base_commit"],
            "status": "UNKNOWN_EXECUTION",
            "error": f"generation timeout after {timeout}s",
            "run_root": str(RUN_ROOT / case_id),
            "stdout": str(exc.stdout or "")[-12000:],
            "stderr": str(exc.stderr or "")[-12000:],
            "finished_at": utc_now(),
        }
        _write_json(result_path, result)
        return result
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["worker_stdout"] = process.stdout[-12000:]
        result["worker_stderr"] = process.stderr[-12000:]
        result["worker_return_code"] = process.returncode
        _write_json(result_path, result)
        return result
    result = {
        "instance_id": case_id,
        "repo": raw["repo"],
        "base_commit": raw["base_commit"],
        "status": "ERROR",
        "error": "case subprocess exited without a result artifact",
        "run_root": str(RUN_ROOT / case_id),
        "stdout": process.stdout[-12000:],
        "stderr": process.stderr[-12000:],
        "worker_return_code": process.returncode,
        "finished_at": utc_now(),
    }
    _write_json(result_path, result)
    return result


def generate(
    max_workers: int,
    max_revisions: int,
    model: str,
    key_path: Path,
    only: set[str] | None = None,
    case_timeout: int | None = None,
) -> dict[str, Any]:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    all_public = _read_jsonl(PUBLIC_PATH)
    public = all_public
    if only:
        public = [item for item in all_public if str(item["instance_id"]) in only]
    # Incremental batches must not erase results from earlier batches. Load
    # both the prior summary and per-case result artifacts, then replace only
    # the cases selected for this invocation.
    prior_by_id: dict[str, dict[str, Any]] = {}
    summary_path = EXPERIMENT_ROOT / "generation_summary.json"
    if summary_path.is_file():
        try:
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
            prior_by_id.update({str(item["instance_id"]): item for item in prior.get("results", [])})
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            print(json.dumps({
                "warning": "generation_summary_ignored",
                "path": str(summary_path),
                "error": f"{type(exc).__name__}: {exc}",
            }, sort_keys=True), file=sys.stderr, flush=True)
    for result_path in sorted(RESULT_ROOT.glob("*.json")):
        try:
            item = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and item.get("instance_id"):
                prior_by_id[str(item["instance_id"])] = item
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, 10), thread_name_prefix="swe51-gen") as pool:
        futures = [
            pool.submit(
                _run_case_subprocess,
                item,
                key_path=key_path,
                model=model,
                max_revisions=max_revisions,
                timeout=case_timeout,
            )
            for item in public
        ]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({
                "instance_id": result["instance_id"],
                "status": result.get("status"),
                "graph_reached": result.get("graph_reached"),
            }, sort_keys=True), flush=True)
            prior_by_id[str(result["instance_id"])] = result
    merged_results = sorted(prior_by_id.values(), key=lambda item: str(item.get("instance_id", "")))
    summary = {
        "stage": "generation",
        "case_count": len(all_public),
        "observed_case_count": len(merged_results),
        "selected_case_count": len(public),
        "results": merged_results,
        "deepseek_model": model,
        "deepseek_concurrency": min(max_workers, 10),
        "case_timeout_seconds": None if case_timeout is None or case_timeout <= 0 else case_timeout,
        "completed_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "generation_summary.json", summary)
    return summary


def generate_case(instance_id: str, key_path: Path, model: str, max_revisions: int) -> dict[str, Any]:
    raw = next(
        item for item in _read_jsonl(PUBLIC_PATH)
        if str(item["instance_id"]) == instance_id
    )
    api_key = key_path.read_text(encoding="utf-8").strip()
    if not api_key:
        raise ValueError("DeepSeek API key is empty")
    transport = DeepSeekHTTPTransport(
        api_key,
        model=model,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        max_concurrency=1,
    )
    result = _generate_one(raw, transport, max_revisions)
    print(json.dumps({"instance_id": instance_id, "status": result.get("status")}, sort_keys=True), flush=True)
    return result


def _run_command(command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        return {
            "command": command,
            "return_code": process.returncode,
            "stdout": process.stdout[-30000:],
            "stderr": process.stderr[-30000:],
            "duration_seconds": time.monotonic() - started,
            "status": "PASS" if process.returncode == 0 else "FAIL",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "return_code": None,
            "stdout": str(exc.stdout or "")[-30000:],
            "stderr": str(exc.stderr or "")[-30000:],
            "duration_seconds": time.monotonic() - started,
            "status": "UNKNOWN_EXECUTION",
        }
    except OSError as exc:
        return {
            "command": command,
            "return_code": None,
            "stdout": "",
            "stderr": str(exc),
            "duration_seconds": time.monotonic() - started,
            "status": "BLOCKED_EXTERNAL",
        }


def _apply_patch(base_tree: Path, patch_path: Path, target: Path) -> dict[str, Any]:
    shutil.copytree(base_tree, target, symlinks=True)
    patch_text = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else ""
    if not patch_text.strip():
        return {"status": "PASS", "stdout": "empty patch", "stderr": "", "return_code": 0}
    result = _run_command(["patch", "-p1", "--batch", "--forward", "--input", str(patch_path)], target, 120)
    return result


def _harness_one(raw: dict[str, Any], generation: dict[str, Any], timeout: int) -> dict[str, Any]:
    case_id = str(raw["instance_id"])
    root = HARNESS_ROOT / case_id
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    base_tree = TREE_ROOT / case_id
    patch_path = Path(str(generation.get("patch_path", "")))
    evaluation = HarnessEvaluationInstance.from_official_record(
        raw, patch_path=patch_path
    )
    result: dict[str, Any] = {
        "instance_id": case_id,
        "generation_status": generation.get("status"),
        "patch_path": str(patch_path),
        "official_source": "official_instances.jsonl (post-generation only)",
        "started_at": utc_now(),
    }
    if not base_tree.is_dir() or not patch_path.is_file():
        result.update({"status": "BLOCKED_GENERATION", "error": "missing base tree or generated patch"})
        return result
    _write_json(root / "harness_evaluation_instance.json", evaluation.to_dict())
    patch_result = _apply_patch(base_tree, patch_path, root / "patched")
    result["patch_apply"] = patch_result
    if patch_result.get("status") != "PASS":
        result.update({"status": "FAIL_PATCH_APPLY", "finished_at": utc_now()})
        return result
    fail_to_pass = list(evaluation.fail_to_pass)
    pass_to_pass = list(evaluation.pass_to_pass)
    repo = evaluation.repository_name
    if repo == "django/django":
        runner = [sys.executable, "tests/runtests.py"]
    else:
        runner = [sys.executable, "-m", "pytest", "-q"]
    fail_result = _run_command(runner + fail_to_pass, root / "patched", timeout)
    pass_result = _run_command(runner + pass_to_pass, root / "patched", timeout)
    result.update({"fail_to_pass": fail_result, "pass_to_pass": pass_result})
    if fail_result["status"] == "PASS" and pass_result["status"] == "PASS":
        status = "PASS"
    elif fail_result["status"] in {"UNKNOWN_EXECUTION", "BLOCKED_EXTERNAL"} or pass_result["status"] in {"UNKNOWN_EXECUTION", "BLOCKED_EXTERNAL"}:
        status = "UNKNOWN_EXECUTION"
    elif pass_result["status"] != "PASS":
        status = "FAIL_PRESERVATION_REGRESSION"
    else:
        status = "FAIL_TARGET"
    result.update({"status": status, "finished_at": utc_now()})
    return result


def harness(max_workers: int, timeout: int) -> dict[str, Any]:
    official = {str(item["instance_id"]): item for item in _read_jsonl(OFFICIAL_PATH)}
    summary_path = EXPERIMENT_ROOT / "generation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    generated = {str(item["instance_id"]): item for item in summary.get("results", [])}
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, 10), thread_name_prefix="swe51-harness") as pool:
        futures = [
            pool.submit(_harness_one, item, generated.get(case_id, {}), timeout)
            for case_id, item in sorted(official.items())
        ]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({"instance_id": result["instance_id"], "status": result.get("status")}, sort_keys=True), flush=True)
    output = {
        "stage": "official_harness",
        "case_count": len(official),
        "results": sorted(results, key=lambda item: item["instance_id"]),
        "completed_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "harness_summary.json", output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--workers", type=int, default=10)
    gen.add_argument("--max-revisions", type=int, default=10)
    gen.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    gen.add_argument("--key-path", default="/home/slt/ReachPatch/ds_pwd.txt")
    gen.add_argument("--only", action="append", default=[])
    gen.add_argument(
        "--case-timeout",
        type=int,
        default=0,
        help="Outer per-case generation timeout in seconds; 0 disables it so graph construction is not truncated.",
    )
    case = sub.add_parser("case")
    case.add_argument("--instance-id", required=True)
    case.add_argument("--key-path", default="/home/slt/ReachPatch/ds_pwd.txt")
    case.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    case.add_argument("--max-revisions", type=int, default=10)
    har = sub.add_parser("harness")
    har.add_argument("--workers", type=int, default=10)
    har.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if args.command == "generate":
        summary = generate(
            args.workers,
            args.max_revisions,
            args.model,
            Path(args.key_path),
            set(args.only),
            args.case_timeout,
        )
        write_failure_report(generation_summary=summary)
        write_experiment_report(summary)
    elif args.command == "case":
        generate_case(args.instance_id, Path(args.key_path), args.model, args.max_revisions)
        return 0
    else:
        summary = harness(args.workers, args.timeout)
        generation_path = EXPERIMENT_ROOT / "generation_summary.json"
        generation_summary = json.loads(generation_path.read_text(encoding="utf-8")) if generation_path.is_file() else {}
        write_failure_report(generation_summary=generation_summary, harness_summary=summary)
        write_experiment_report(generation_summary, summary)
    counts: dict[str, int] = {}
    for item in summary.get("results", []):
        status = str(item.get("status"))
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"stage": args.command, "counts": counts, "case_count": len(summary.get("results", []))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
