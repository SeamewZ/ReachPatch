from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reachpatch.artifacts.store import ArtifactStore
from reachpatch.execution.reconcile import reconcile_actual_diff
from reachpatch.execution.worktree import WorktreeManager, tree_hash
from reachpatch.models.base import SerializableRecord, content_hash
from reachpatch.challenge_graph.dicc import verify_stored_diff_closure_certificate
from reachpatch.challenge_graph.models import DiffClosureCertificate


@dataclass(frozen=True, slots=True)
class RunVerification(SerializableRecord):
    valid: bool
    run_root: str
    artifact_count: int
    checkpoint_count: int
    transition_count: int
    terminal_status: str | None
    checks: dict[str, bool]
    errors: tuple[str, ...]
    verification_hash: str


def _latest_payload(store: ArtifactStore, instance_id: str, artifact_type: str):
    envelope = store.latest(instance_id, artifact_type)
    return envelope.payload if envelope is not None else None


def _certificate_hash(raw: dict[str, Any]) -> str:
    payload = dict(raw)
    payload.pop("recomputation_hash", None)
    return content_hash(payload)


def verify_run(run_root: str | Path) -> RunVerification:
    root = Path(run_root).resolve()
    errors: list[str] = []
    checks: dict[str, bool] = {}
    manifest_path = root / "run_manifest.json"
    if not manifest_path.is_file():
        errors.append("run_manifest.json is missing")
        instance_id = "UNKNOWN"
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            instance_id = str(manifest["instance"]["instance_id"])
            checks["manifest"] = True
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            instance_id = "UNKNOWN"
            checks["manifest"] = False
            errors.append(f"invalid run manifest: {exc}")
    store = ArtifactStore(root / "artifacts")
    store_result = store.verify()
    checks["artifact_store"] = bool(store_result["valid"])
    errors.extend(str(item) for item in store_result["errors"])

    checkpoint = _latest_payload(store, instance_id, "incumbent_checkpoint")
    working_patch = _latest_payload(store, instance_id, "working_patch")
    terminal = _latest_payload(store, instance_id, "terminal_certificate")
    checkpoints_root = root / "worktrees" / "checkpoints"
    checkpoint_count = (
        sum(1 for path in checkpoints_root.iterdir() if (path / "tree").is_dir())
        if checkpoints_root.is_dir() else 0
    )
    if checkpoint is None or working_patch is None:
        checks["checkpoint"] = False
        errors.append("latest incumbent checkpoint or working patch is missing")
    else:
        snapshot = Path(str(checkpoint["snapshot_tree"])).resolve()
        snapshot_local = snapshot.is_relative_to(root) and snapshot.is_dir()
        snapshot_hash = tree_hash(snapshot) if snapshot_local else None
        checks["checkpoint"] = (
            snapshot_local
            and snapshot_hash == working_patch["working_tree_hash"]
            and checkpoint["checkpoint_id"] == working_patch["checkpoint_id"]
        )
        if not checks["checkpoint"]:
            errors.append("checkpoint path, id, or tree hash is inconsistent")
        base_tree = None
        if checkpoints_root.is_dir():
            for candidate in sorted(checkpoints_root.glob("*/tree")):
                if tree_hash(candidate) == working_patch["base_tree_hash"]:
                    base_tree = candidate
                    break
        if base_tree is None or not snapshot_local:
            checks["canonical_diff"] = False
            errors.append("cannot locate base and incumbent trees for diff replay")
        else:
            actual = reconcile_actual_diff(base_tree, snapshot)
            checks["canonical_diff"] = (
                actual.canonical_diff == working_patch["canonical_diff"]
                and actual.canonical_diff_hash == working_patch["canonical_diff_hash"]
            )
            if not checks["canonical_diff"]:
                errors.append("canonical diff does not replay to the incumbent tree")

    transition_envelopes = store.list(
        artifact_type="transition_certificate", instance_id=instance_id
    )
    bad_certificates = [
        item.artifact_id
        for item in transition_envelopes
        if item.payload.get("recomputation_hash") != _certificate_hash(item.payload)
    ]
    checks["transition_certificates"] = not bad_certificates
    if bad_certificates:
        errors.append(f"invalid transition certificates: {bad_certificates}")

    closure_envelopes = store.list(
        artifact_type="diff_closure_certificate", instance_id=instance_id
    )
    bad_closures = []
    for envelope in closure_envelopes:
        try:
            raw = dict(envelope.payload)
            for name in (
                "baseline_path_obligation_ids", "overlay_obligation_ids",
                "obligation_result_ids", "invalidated_node_ids",
                "changed_guard_obligation_ids", "call_exit_obligation_ids",
                "fallback_obligation_ids", "state_dispatch_obligation_ids",
                "bypass_obligation_ids", "preservation_caller_obligation_ids",
                "hard_frontier_ids", "residual_risk_frontier_ids",
                "oracle_change_ids", "stale_record_ids", "changed_edge_ledger_ids",
                "updated_obligations",
            ):
                raw[name] = tuple(raw.get(name, ()))
            certificate = DiffClosureCertificate(**raw)
            if not verify_stored_diff_closure_certificate(certificate):
                bad_closures.append(envelope.artifact_id)
        except (KeyError, TypeError, ValueError):
            bad_closures.append(envelope.artifact_id)
    checks["diff_closure_certificates"] = not bad_closures
    if bad_closures:
        errors.append(f"invalid diff closure certificates: {bad_closures}")

    final_patch_path = root / "final_patch.diff"
    if terminal is None:
        checks["terminal"] = not final_patch_path.exists()
        terminal_status = None
    else:
        terminal_status = str(terminal["status"])
        final_patch = (
            final_patch_path.read_text(encoding="utf-8")
            if final_patch_path.is_file() else None
        )
        certificate_digest = store.verification_digest(
            exclude_types={"terminal_certificate"}
        )
        graph_hashes = checkpoint.get("graph_hashes", {}) if checkpoint else {}
        graph_reach_consistent = (
            not terminal.get("graph_reached", False)
            or (
                terminal.get("target_complete", False)
                and terminal.get("preservation_complete", False)
                and terminal.get("shadow_complete", False)
                and terminal.get("closure_complete", False)
                and not terminal.get("unresolved_path_obligation_ids", ())
            )
        )
        checks["terminal"] = (
            checkpoint is not None
            and terminal["final_checkpoint_id"] == checkpoint["checkpoint_id"]
            and final_patch is not None
            and content_hash(final_patch) == terminal["final_diff_hash"]
            and terminal.get("artifact_verification_hash") == certificate_digest
            and terminal.get("graph_hashes") == graph_hashes
            and graph_reach_consistent
        )
        if not checks["terminal"]:
            errors.append("terminal certificate, checkpoint, and final patch disagree")

    payload: dict[str, Any] = {
        "run_root": str(root),
        "artifact_count": int(store_result["checked"]),
        "checkpoint_count": checkpoint_count,
        "transition_count": len(transition_envelopes),
        "terminal_status": terminal_status,
        "checks": checks,
        "errors": errors,
    }
    return RunVerification(
        valid=not errors and all(checks.values()),
        verification_hash=content_hash(payload),
        errors=tuple(errors),
        **{key: value for key, value in payload.items() if key != "errors"},
    )


def verify_artifacts(run_root: str | Path) -> RunVerification:
    """Compatibility name for the complete run/artifact verification gate."""

    return verify_run(run_root)


def recover_run_storage(run_root: str | Path) -> dict[str, Any]:
    root = Path(run_root).resolve()
    artifact_recovery = ArtifactStore(root / "artifacts").recover()
    transaction_recovery = WorktreeManager(root / "worktrees").recover()
    verification = verify_run(root)
    return {
        "artifact_recovery": artifact_recovery,
        "transaction_recovery": transaction_recovery,
        "verification": verification.to_dict(),
    }
