from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from reachpatch.artifacts import ArtifactStore
from reachpatch.artifacts.verify import verify_run


def _atomic_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _context(run_root: Path) -> tuple[str, ArtifactStore, dict[str, Any]]:
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    instance_id = str(manifest["instance"]["instance_id"])
    return instance_id, ArtifactStore(run_root / "artifacts"), manifest


def export_patch(run_root: str | Path, destination: str | Path | None = None) -> Path:
    root = Path(run_root).resolve()
    instance_id, store, _ = _context(root)
    patch = store.latest(instance_id, "working_patch")
    if patch is None:
        raise FileNotFoundError("working_patch artifact")
    target = Path(destination).resolve() if destination else root / "exports" / "final.patch"
    if not target.is_relative_to(root):
        raise ValueError("patch export must remain inside the run root")
    return _atomic_text(target, str(patch.payload["canonical_diff"]))


def build_run_report(run_root: str | Path) -> dict[str, Any]:
    root = Path(run_root).resolve()
    instance_id, store, manifest = _context(root)
    verification = verify_run(root)
    state = store.latest(instance_id, "reach_avoid_state")
    terminal = store.latest(instance_id, "terminal_certificate")
    transitions = store.list(
        artifact_type="transition_certificate", instance_id=instance_id
    )
    counterexamples = store.list(
        artifact_type="counterexample", instance_id=instance_id
    )
    report = {
        "instance_id": instance_id,
        "run_root": str(root),
        "base_commit": manifest["instance"]["base_commit"],
        "status": terminal.payload["status"] if terminal else "ACTIVE",
        "graph_reached": bool(terminal and terminal.payload["graph_reached"]),
        "checkpoint_id": state.payload["checkpoint"]["checkpoint_id"] if state else None,
        "working_patch_hash": state.payload["working_patch_hash"] if state else None,
        "transition_count": len(transitions),
        "commit_count": sum(item.payload.get("decision") == "COMMIT" for item in transitions),
        "rollback_count": sum(item.payload.get("decision") == "ROLLBACK" for item in transitions),
        "keep_uncertified_count": sum(
            item.payload.get("decision") == "KEEP_UNCERTIFIED"
            for item in transitions
        ),
        "counterexample_count": len(counterexamples),
        "check_comparison_count": len(
            state.payload.get("check_comparisons", ()) if state else ()
        ),
        "dicc_status": (
            (state.payload.get("dicc_certificate") or {}).get("status")
            if state else None
        ),
        "root_cause_labels": (
            state.payload.get("runtime_metrics", {}).get("root_cause_labels", [])
            if state else []
        ),
        "outcome_counts": {},
        "artifact_verification": verification.to_dict(),
    }
    if state:
        counts: dict[str, int] = {}
        for outcome in state.payload.get("outcomes", []):
            status = str(outcome["status"])
            counts[status] = counts.get(status, 0) + 1
        report["outcome_counts"] = counts
    report_dir = root / "reports"
    _atomic_text(
        report_dir / "run_report.json",
        json.dumps(report, sort_keys=True, indent=2) + "\n",
    )
    markdown = (
        f"# ReachPatch Run {instance_id}\n\n"
        f"- Status: `{report['status']}`\n"
        f"- Graph reached: `{report['graph_reached']}`\n"
        f"- Checkpoint: `{report['checkpoint_id']}`\n"
        f"- Patch hash: `{report['working_patch_hash']}`\n"
        f"- Transitions: {report['transition_count']} "
        f"({report['commit_count']} commit, {report['rollback_count']} rollback, "
        f"{report['keep_uncertified_count']} keep uncertified)\n"
        f"- Counterexamples: {report['counterexample_count']}\n"
        f"- Paired check comparisons: {report['check_comparison_count']}\n"
        f"- DICC: `{report['dicc_status']}`\n"
        f"- Root causes: `{', '.join(report['root_cause_labels'])}`\n"
        f"- Artifacts valid: `{verification.valid}`\n"
    )
    _atomic_text(report_dir / "run_report.md", markdown)
    return report
