"""Blocked prospective runner. One attempt per cell; seal all before harness."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import threading

from .analysis import summarize, write_paper_table, missing_outcome_bounds
from .protocol import CODE, digest, freeze_protocol, implementation_hash, load_protocol, make_protocol


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def environment(protocol, cell, root):
    env = {k: v for k, v in os.environ.items() if not k.startswith("REACHPATCH_")}
    config = protocol["arms"][cell["arm"]]
    env.update({"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
                "PYTHONPATH": str(CODE), "PYTHONDONTWRITEBYTECODE": "1",
                "REACHPATCH_RA51_ROOT": str(root), "REACHPATCH_CASE_RETRIES": "1",
                "REACHPATCH_CASE_WORKERS": "1", "REACHPATCH_VALIDATION_WORKERS": "2",
                "REACHPATCH_CASE_WALL_SECONDS": str(config["config"]["execution_budget_seconds"]),
                "REACHPATCH_CASE_MODEL_CALLS": str(config["config"]["max_case_model_calls"]),
                "REACHPATCH_CASE_TOKENS": str(config["config"]["max_case_tokens"]),
                "REACHPATCH_EVIDENCE_REUSE_ENABLED": str(int(config["reuse"])),
                "REACHPATCH_DEMAND_DRIVEN_INTERACTION_ENABLED": str(int(config["demand"]))})
    for name, enabled in config["policy"].items():
        env["REACHPATCH_" + name.upper()] = str(int(enabled))
    if protocol.get("source_tree_root"):
        env["REACHPATCH_SOURCE_TREE_ROOT"] = protocol["source_tree_root"]
    return env


def checked_process(command, env, log_path, seconds):
    with log_path.open("w") as log:
        process = subprocess.Popen(command, cwd=CODE, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        write_json(log_path.with_suffix(".owner.json"), {"pid": process.pid,
                   "started_epoch": time.time(), "command_hash": digest(json.dumps(command).encode())})
        try:
            return process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return 124


def run_cell(root, protocol, cell, key_path):
    location = root / "cells" / cell["cell_id"]
    terminal = location / "cell_result.json"
    if terminal.exists():
        return json.loads(terminal.read_text())
    if implementation_hash() != protocol["implementation_hash"]:
        raise RuntimeError("implementation drift; no new cell admitted")
    if shutil.disk_usage(root).free < protocol["minimum_free_disk_gib"] * 1024 ** 3:
        raise RuntimeError("STUDY_DISK_RESERVE; no new cell admitted")
    location.mkdir(parents=True, exist_ok=True)
    started_path = location / "attempt.json"
    backend = location / "backend"
    if started_path.exists():
        owner = location / "generation.owner.json"
        if owner.exists():
            pid = json.loads(owner.read_text())["pid"]
            if Path(f"/proc/{pid}").exists():
                raise RuntimeError("previous cell process may still be alive; refusing double run")
        # No whole-case retry is allowed by this protocol, including a crash.
        code = 125
        started = json.loads(started_path.read_text())["started_epoch"]
    else:
        started = time.time()
        write_json(started_path, {**cell, "attempt_id": cell["cell_id"] + "-a1",
                                  "started_epoch": started,
                                  "implementation_hash": protocol["implementation_hash"]})
        if protocol["scope"] == "public_smoke":
            command = [sys.executable, "-m", "experiments.run_evidence_efficiency",
                       "--root", str(backend), "--key-path", str(key_path)]
            arm = protocol["arms"][cell["arm"]]
            if not arm["reuse"]:
                command.append("--disable-evidence-reuse")
            if not arm["demand"]:
                command.append("--fixed-interaction")
        else:
            command = [sys.executable, "-m", "experiments.reachavoid_51.runner", "generate",
                       "--only", cell["instance_id"], "--key-path", str(key_path),
                       "--model", protocol["model"], "--max-revisions",
                       str(protocol["arms"][cell["arm"]]["config"]["max_real_patch_revisions"])]
        code = checked_process(command, environment(protocol, cell, backend),
                               location / "generation.log",
                               protocol["arms"][cell["arm"]]["config"]["execution_budget_seconds"] + 240)
    if protocol["scope"] == "public_smoke":
        path = backend / "smoke_summary.json"
        summary = json.loads(path.read_text()) if path.exists() else {}
        run_root = backend
        patch = run_root / "final.patch"
        certified = summary.get("controller_certified", False)
        public_pass = summary.get("success", False)
    else:
        path = backend / "results" / (cell["instance_id"] + ".json")
        summary = json.loads(path.read_text()) if path.exists() and code == 0 else {}
        run_root = Path(summary.get("run_root", backend / "runs" / cell["instance_id"]))
        patch = Path(summary["final_patch_path"]) if summary else location / "unavailable.patch"
        certified = summary.get("status") == "REACHED"
        public_pass = False  # Not the outcome measure for repository experiments.
    final_diff = patch.read_text() if patch.exists() and summary else ""
    sealed_patch = location / "sealed_final.patch"
    sealed_patch.write_text(final_diff)
    row = {"status": "GENERATED" if summary and final_diff else "GENERATION_FAILED",
           "return_code": code, "elapsed_seconds": time.time() - started,
           "journal_path": str(run_root / "request_journal.jsonl"),
           "run_root": str(run_root), "controller_certified": certified,
           "public_acceptance": public_pass,
           "patch_path": str(sealed_patch), "patch_sha256": digest(sealed_patch.read_bytes()),
           "attempt_id": cell["cell_id"] + "-a1"}
    write_json(terminal, row)
    print(json.dumps({**cell, "status": row["status"], "public_acceptance": public_pass}), flush=True)
    return row


def seal_study(root, protocol):
    load_protocol(root)
    rows = []
    for cell in protocol["cells"]:
        path = root / "cells" / cell["cell_id"] / "cell_result.json"
        if not path.exists():
            raise RuntimeError("unsealed cells; official evaluation forbidden")
        row = json.loads(path.read_text())
        if digest(Path(row["patch_path"]).read_bytes()) != row["patch_sha256"]:
            raise RuntimeError("patch changed after cell sealing")
        rows.append({**cell, **row})
    seal = {"protocol_sha256": digest((root / "protocol.json").read_bytes()),
            "implementation_hash": protocol["implementation_hash"], "cells": rows}
    existing = root / "sealed_study.json"
    if existing.exists() and json.loads(existing.read_text()) != seal:
        raise RuntimeError("refusing to replace an existing cohort seal")
    write_json(root / "sealed_study.json", seal)
    return seal


def evaluate(root, protocol):
    if protocol["scope"] != "development":
        raise ValueError("public smoke has no official harness outcome")
    # This is the first access to official data. Both the cohort seal and all
    # underlying patches are checked before the file is opened.
    if not (root / "sealed_study.json").exists():
        raise RuntimeError("all study cells must seal before official data is opened")
    stored = json.loads((root / "sealed_study.json").read_text())
    seal = seal_study(root, protocol)
    if stored != seal:
        raise RuntimeError("study seal changed")
    official_path = CODE / "dataset/patchpsro_55_unique51/official_instances.jsonl"
    official = [json.loads(line) for line in official_path.read_text().splitlines() if line.strip()]
    official = [r for r in official if r["instance_id"] in protocol["case_ids"]]
    if len(official) != len(protocol["case_ids"]):
        raise RuntimeError("official cohort incomplete")
    outcomes = []
    for repetition in range(protocol["repetitions"]):
        for arm in protocol["arms"]:
            location = root / "evaluation" / f"r{repetition}-{arm}"
            location.mkdir(parents=True, exist_ok=True)
            dataset = location / "sealed_official.jsonl"
            dataset.write_text("".join(json.dumps(r) + "\n" for r in official))
            cells = [r for r in seal["cells"] if r["repetition"] == repetition and r["arm"] == arm]
            predictions = location / "predictions.jsonl"
            predictions.write_text("".join(json.dumps({"instance_id": r["instance_id"],
                "model_name_or_path": arm, "model_patch": Path(r["patch_path"]).read_text()}) + "\n" for r in cells))
            run_id = f"evidence-{digest(predictions.read_bytes())[:16]}"
            command = [sys.executable, "-m", "swebench.harness.run_evaluation",
                       "--dataset_name", str(dataset), "--split", "test",
                       "--predictions_path", str(predictions), "--max_workers", "2",
                       "--timeout", "1800", "--run_id", run_id, "--namespace", "swebench",
                       "--cache_level", "instance", "--clean", "False", "--report_dir", str(location)]
            report = {}
            for attempt in range(1 + protocol["infra_evaluation_retries"]):
                checked_process(command, environment(protocol, cells[0], location),
                                location / f"harness-{attempt}.log", 1800 * len(cells) + 600)
                candidates = list(location.glob(f"*.{run_id}.json"))
                if len(candidates) == 1:
                    report = json.loads(candidates[0].read_text())
                    if not report.get("error_ids"):
                        break
            for row in cells:
                cid = row["instance_id"]
                resolved = True if cid in report.get("resolved_ids", []) else (
                    False if cid in report.get("unresolved_ids", []) or row["status"] == "GENERATION_FAILED" else None)
                outcomes.append({"instance_id": cid, "arm": arm, "repetition": repetition, "resolved": resolved,
                                 "patch_sha256": row["patch_sha256"]})
            write_json(root / "official_outcomes.json", {"rows": outcomes,
                "arms": {name: missing_outcome_bounds([r["resolved"] for r in outcomes if r["arm"] == name])
                         for name in protocol["arms"]}})


def run(root, key_path, do_evaluate):
    protocol = load_protocol(root)
    with (root / "coordinator.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        blocks = {}
        for cell in protocol["cells"]:
            blocks.setdefault(cell["block"], []).append(cell)
        progress_lock = threading.Lock()
        completed_cells = {cell["cell_id"] for cell in protocol["cells"]
                           if (root / "cells" / cell["cell_id"] / "cell_result.json").exists()}

        def execute_block(cells):
            for cell in cells:
                run_cell(root, protocol, cell, key_path)
                with progress_lock:
                    completed_cells.add(cell["cell_id"])
                    write_json(root / "progress.json", {"phase": "GENERATION",
                        "completed_cells": len(completed_cells), "registered_cells": len(protocol["cells"]),
                        "last_cell": cell["cell_id"], "updated_epoch": time.time()})

        with ThreadPoolExecutor(max_workers=protocol["parallel_blocks"]) as pool:
            list(pool.map(execute_block, blocks.values()))
        seal_study(root, protocol)
        result = summarize(root, protocol)
        if protocol["scope"] == "public_smoke":
            write_paper_table(result, root / "paper_table.tex")
        if do_evaluate:
            write_json(root / "progress.json", {"phase": "OFFICIAL_EVALUATION",
                "completed_cells": len(completed_cells), "registered_cells": len(protocol["cells"])})
            evaluate(root, protocol)
        if protocol["scope"] == "public_smoke" or do_evaluate:
            from experiments.publish_evidence_study import publish
            publish(root, compile_pdf=True)
        write_json(root / "progress.json", {"phase": "COMPLETED",
            "completed_cells": len(completed_cells), "registered_cells": len(protocol["cells"]),
            "official_evaluation_performed": do_evaluate, "updated_epoch": time.time()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "run", "analyze", "evaluate"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--scope", choices=("public_smoke", "development"), default="public_smoke")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260920)
    parser.add_argument("--key-path", type=Path)
    parser.add_argument("--source-tree-root", type=Path)
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "init":
        freeze_protocol(root, make_protocol(args.scope, args.count, args.repetitions, args.seed, args.source_tree_root))
    elif args.command == "run":
        if args.key_path is None:
            parser.error("run requires --key-path")
        run(root, args.key_path.resolve(), args.evaluate)
    elif args.command == "evaluate":
        evaluate(root, load_protocol(root))
    else:
        result = summarize(root, load_protocol(root))
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
