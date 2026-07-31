from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def _read_envelopes(run_root: Path) -> list[dict[str, Any]]:
    journal = run_root / "artifacts" / "journal.jsonl"
    if not journal.is_file():
        raise FileNotFoundError(f"artifact journal is missing: {journal}")
    envelopes: list[dict[str, Any]] = []
    for raw_line in journal.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        record = json.loads(raw_line)
        relative = Path(str(record["path"]))
        path = run_root / "artifacts" / relative
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or "payload" not in envelope:
            raise ValueError(f"invalid artifact envelope: {path}")
        envelopes.append(envelope)
    return envelopes


def _artifacts(
    envelopes: Iterable[dict[str, Any]], artifact_type: str
) -> list[dict[str, Any]]:
    return [
        dict(item["payload"])
        for item in envelopes
        if item.get("artifact_type") == artifact_type
    ]


def _candidate_authority(candidate: dict[str, Any]) -> str:
    explicit = str(
        candidate.get("oracle_authority") or candidate.get("authority") or ""
    ).upper()
    if explicit in {"A", "B", "C"}:
        return explicit
    strategy = str(candidate.get("strategy", "")).lower()
    relation = candidate.get("expected_relation")
    # Old directed reproductions allowed the model to provide both the input
    # and expected output. They are exploration evidence even when stable.
    if strategy == "llm_reproduction":
        return "E"
    if strategy in {"related_public_test", "public_repository_test"}:
        if isinstance(relation, dict) and relation.get("baseline_status") == "FAIL":
            return "A"
    if strategy in {"issue_executable_witness", "return_or_exception_api_contract"}:
        return "B"
    if strategy in {
        "baseline_differential_relation", "object_state_or_protocol_relation"
    }:
        return "C"
    return "UNTRUSTED"


def _target_catalog(
    recoveries: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for recovery in recoveries:
        targets = {
            str(item.get("check_id", "")): item
            for item in recovery.get("targets", ())
        }
        executions = {
            str(item.get("check_id", "")): item
            for item in recovery.get("baseline_executions", ())
        }
        for candidate in recovery.get("candidates", ()):
            target_id = str(candidate.get("target_id", ""))
            if not target_id:
                continue
            execution = executions.get(target_id, {})
            target = targets.get(target_id, {})
            authority = _candidate_authority(candidate)
            catalog[target_id] = {
                "check_id": target_id,
                "authority": authority,
                "strategy": candidate.get("strategy"),
                "stable": bool(execution.get("stable")),
                "baseline_status": execution.get("status"),
                "target_requirement_ids": tuple(
                    map(str, target.get("target_requirement_ids", ()))
                ),
                "executed_symbol_ids": tuple(
                    map(str, candidate.get("executed_symbol_ids", ()))
                ),
                "trusted": authority in {"A", "B", "C"},
            }
    return catalog


def _locked_comparison(transition: dict[str, Any]) -> dict[str, Any]:
    graph_delta = dict(transition.get("graph_delta", {}) or {})
    locked = dict(graph_delta.get("locked_check_set", {}) or {})
    before_ids = tuple(map(str, locked.get("before_check_ids", ())))
    after_ids = tuple(map(str, locked.get("after_check_ids", ())))
    if not before_ids:
        before_ids = tuple(map(str, transition.get("before_executed_check_ids", ())))
    if not after_ids:
        after_ids = tuple(map(str, transition.get("after_executed_check_ids", ())))
    comparable = bool(before_ids) and before_ids == after_ids
    return {
        "before_check_ids": before_ids,
        "after_check_ids": after_ids,
        "comparable": comparable,
    }


def _transition_authority(
    transition: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> str:
    authorities = {
        catalog[check_id]["authority"]
        for check_id in map(str, transition.get("target_comparisons", ()))
        if check_id in catalog
    }
    trusted = sorted(authorities & {"A", "B", "C"})
    if trusted:
        return trusted[0]
    if "E" in authorities:
        return "E"
    return "UNTRUSTED"


def _confirmed_transition(
    transition: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> bool:
    lock = _locked_comparison(transition)
    authority = _transition_authority(transition, catalog)
    target_or_preservation = bool(
        transition.get("target_comparisons")
        or transition.get("preservation_comparisons")
        or transition.get("challenge_comparisons")
    )
    return (
        authority in {"A", "B", "C"}
        and lock["comparable"]
        and target_or_preservation
        and bool(transition.get("mechanical_pass", False))
    )


def replay_monotonic_policy(report: dict[str, Any]) -> dict[str, Any]:
    first_hash = str(report.get("first_patch_hash", ""))
    best_hash = first_hash
    promoted: list[str] = []
    rejected: list[str] = []
    for revision in report.get("revisions", ()):
        transition_id = str(revision.get("transition_id", ""))
        if not revision.get("confirmed_comparable_execution", False):
            rejected.append(transition_id)
            continue
        metrics = dict(revision.get("progress_after", {}) or {})
        target_delta = int(metrics.get("confirmed_target_pass_delta", 0))
        regression_delta = int(metrics.get("confirmed_regression_delta", 0))
        mechanical_failures = int(metrics.get("mechanical_failure_count", 0))
        if (target_delta > 0 or regression_delta < 0) and mechanical_failures == 0:
            best_hash = str(revision.get("after_patch_hash") or best_hash)
            promoted.append(transition_id)
        else:
            rejected.append(transition_id)
    return {
        "selected_patch_hash": best_hash,
        "used_first_patch": best_hash == first_hash,
        "promoted_transition_ids": promoted,
        "rejected_transition_ids": rejected,
    }


def build_revision_trajectory_report(run_root: str | Path) -> dict[str, Any]:
    root = Path(run_root).resolve()
    envelopes = _read_envelopes(root)
    patches = [
        item for item in _artifacts(envelopes, "working_patch")
        if item.get("canonical_diff")
    ]
    if not patches:
        raise ValueError(f"run has no nonempty working patch: {root}")
    first = patches[0]
    patch_by_hash = {
        str(item["canonical_diff_hash"]): item for item in patches
    }
    transitions = _artifacts(envelopes, "transition_certificate")
    recoveries = _artifacts(envelopes, "target_recovery")
    counterexamples = _artifacts(envelopes, "counterexample")
    catalog = _target_catalog(recoveries)
    counterexamples_by_transition: dict[str, list[dict[str, Any]]] = {}
    for packet in counterexamples:
        counterexamples_by_transition.setdefault(
            str(packet.get("transition_id", "")), []
        ).append(packet)
    revisions: list[dict[str, Any]] = []
    first_hash = str(first["canonical_diff_hash"])
    first_creation_seen = False
    for transition in transitions:
        after_hash = str(
            transition.get("after_patch_hash")
            or transition.get("cumulative_diff_hash")
            or ""
        )
        before_hash = str(transition.get("before_patch_hash") or "")
        if after_hash == first_hash and before_hash != first_hash and not first_creation_seen:
            first_creation_seen = True
            continue
        lock = _locked_comparison(transition)
        transition_id = str(transition.get("transition_id", ""))
        packets = counterexamples_by_transition.get(transition_id, ())
        confirmed = _confirmed_transition(transition, catalog)
        revisions.append({
            "transition_id": transition_id,
            "revision": int(transition.get("to_revision", 0)),
            "before_patch_hash": before_hash,
            "after_patch_hash": after_hash,
            "revision_trigger": str(transition.get("mechanism_id", "")),
            "trigger_authority": _transition_authority(transition, catalog),
            "confirmed_failure_ids": [
                str(item.get("counterexample_id", ""))
                for item in packets
                if item.get("counterexample_id")
            ],
            **lock,
            "confirmed_comparable_execution": confirmed,
            "executed_check_ids": tuple(
                map(str, transition.get("executed_check_ids", ()))
            ),
            "promotion_decision": (
                "ELIGIBLE_FOR_POLICY_EVALUATION" if confirmed
                else "REJECT_NO_CONFIRMED_COMPARABLE_EXECUTION"
            ),
            "recorded_decision": str(transition.get("decision", "")),
            "rollback_decision": str(transition.get("rollback_decision", "")),
            "reach_decision": str(transition.get("reach_decision", "")),
            "progress_after": dict(transition.get("progress_after", {}) or {}),
        })
    terminal_path = root / "terminal_certificate.json"
    terminal = (
        json.loads(terminal_path.read_text(encoding="utf-8"))
        if terminal_path.is_file() else {}
    )
    certified_targets = [
        item for item in catalog.values()
        if item["trusted"]
        and item["stable"]
        and item["baseline_status"] == "FAIL"
        and item["target_requirement_ids"]
        and item["executed_symbol_ids"]
    ]
    report = {
        "instance_id": str(terminal.get("instance_id") or root.name),
        "run_root": str(root),
        "first_patch_hash": first_hash,
        "first_patch_checkpoint_id": first.get("checkpoint_id"),
        "patch_hashes": [str(item["canonical_diff_hash"]) for item in patches],
        "revisions": revisions,
        "target_catalog": sorted(catalog.values(), key=lambda item: item["check_id"]),
        "certified_target_ids": [item["check_id"] for item in certified_targets],
        "recorded_final_patch_hash": str(terminal.get("final_diff_hash", "")),
        "recorded_final_checkpoint": str(terminal.get("final_checkpoint_id", "")),
        "recorded_reach": bool(terminal.get("graph_reached", False)),
        "certified_reach": bool(certified_targets),
        "first_patch_was_modified": any(
            item["before_patch_hash"] == first_hash
            and item["after_patch_hash"] != first_hash
            for item in revisions
        ),
        "known_patch_count": len(patch_by_hash),
    }
    report["policy_replay"] = replay_monotonic_policy(report)
    return report
