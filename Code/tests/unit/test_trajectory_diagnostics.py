from __future__ import annotations

import json

from reachpatch.diagnostics import build_revision_trajectory_report


def _write_artifact(root, artifact_type, artifact_id, payload):
    relative = f"objects/{artifact_id}.json"
    path = root / "artifacts" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "payload": payload,
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with (root / "artifacts" / "journal.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "path": relative,
        }) + "\n")


def test_policy_replay_selects_first_patch_when_revisions_have_no_execution(tmp_path):
    root = tmp_path / "case"
    (root / "artifacts").mkdir(parents=True)
    _write_artifact(root, "working_patch", "empty", {
        "canonical_diff": "", "canonical_diff_hash": "empty",
    })
    _write_artifact(root, "working_patch", "first", {
        "canonical_diff": "--- a/a.py\n+++ b/a.py\n", "canonical_diff_hash": "first",
        "checkpoint_id": "first-checkpoint",
    })
    _write_artifact(root, "working_patch", "blind", {
        "canonical_diff": "--- a/a.py\n+++ b/a.py\n+blind\n",
        "canonical_diff_hash": "blind", "checkpoint_id": "blind-checkpoint",
    })
    _write_artifact(root, "transition_certificate", "initial-transition", {
        "transition_id": "initial", "before_patch_hash": "empty",
        "after_patch_hash": "first", "to_revision": 1,
    })
    _write_artifact(root, "transition_certificate", "blind-transition", {
        "transition_id": "blind-revision", "before_patch_hash": "first",
        "after_patch_hash": "blind", "to_revision": 2,
        "decision": "COMMIT", "reach_decision": "EVIDENCE_LIMITED_COMPLETE",
        "executed_check_ids": [], "target_comparisons": [],
        "preservation_comparisons": [], "mechanical_pass": True,
    })
    (root / "terminal_certificate.json").write_text(json.dumps({
        "instance_id": "case", "final_diff_hash": "blind",
        "final_checkpoint_id": "blind-checkpoint", "graph_reached": False,
    }), encoding="utf-8")

    report = build_revision_trajectory_report(root)

    assert report["first_patch_hash"] == "first"
    assert report["recorded_final_patch_hash"] == "blind"
    assert report["policy_replay"]["selected_patch_hash"] == "first"
    assert report["policy_replay"]["used_first_patch"]
    assert report["revisions"][0]["promotion_decision"] == (
        "REJECT_NO_CONFIRMED_COMPARABLE_EXECUTION"
    )


def test_llm_owned_reproduction_cannot_certify_reach(tmp_path):
    root = tmp_path / "case"
    (root / "artifacts").mkdir(parents=True)
    _write_artifact(root, "working_patch", "first", {
        "canonical_diff": "--- a/a.py\n+++ b/a.py\n", "canonical_diff_hash": "first",
        "checkpoint_id": "first-checkpoint",
    })
    _write_artifact(root, "target_recovery", "recovery", {
        "targets": [{
            "check_id": "llm-target", "target_requirement_ids": [],
        }],
        "baseline_executions": [{
            "check_id": "llm-target", "stable": True, "status": "FAIL",
        }],
        "candidates": [{
            "target_id": "llm-target", "strategy": "llm_reproduction",
            "authority": "ISSUE_PUBLIC_REPRODUCTION",
            "executed_symbol_ids": ["symbol"],
        }],
    })
    (root / "terminal_certificate.json").write_text(json.dumps({
        "instance_id": "case", "final_diff_hash": "first",
        "final_checkpoint_id": "first-checkpoint", "graph_reached": True,
    }), encoding="utf-8")

    report = build_revision_trajectory_report(root)

    assert report["recorded_reach"]
    assert not report["certified_reach"]
    assert report["target_catalog"][0]["authority"] == "E"


def test_policy_replay_reports_missing_nonempty_checkpoint(tmp_path):
    root = tmp_path / "case"
    (root / "artifacts").mkdir(parents=True)
    _write_artifact(root, "working_patch", "empty", {
        "canonical_diff": "", "canonical_diff_hash": "empty",
    })
    (root / "terminal_certificate.json").write_text(json.dumps({
        "instance_id": "case", "final_diff_hash": "",
        "final_checkpoint_id": "", "graph_reached": False,
    }), encoding="utf-8")

    report = build_revision_trajectory_report(root)

    assert report["diagnostic_status"] == "NO_NONEMPTY_CHECKPOINT"
    assert report["known_patch_count"] == 0
    assert report["policy_replay"]["selected_patch_hash"] == ""
    assert not report["policy_replay"]["used_first_patch"]


def test_policy_replay_accepts_stable_locked_mechanical_improvement(tmp_path):
    root = tmp_path / "case"
    (root / "artifacts").mkdir(parents=True)
    _write_artifact(root, "working_patch", "first", {
        "canonical_diff": "--- a/a.py\n+++ b/a.py\n+broken\n",
        "canonical_diff_hash": "first", "checkpoint_id": "first-checkpoint",
    })
    _write_artifact(root, "working_patch", "fixed", {
        "canonical_diff": "--- a/a.py\n+++ b/a.py\n+fixed\n",
        "canonical_diff_hash": "fixed", "checkpoint_id": "fixed-checkpoint",
    })
    _write_artifact(root, "transition_certificate", "initial-transition", {
        "transition_id": "initial", "before_patch_hash": "empty",
        "after_patch_hash": "first", "to_revision": 1,
    })
    locked_result = {
        "check_id": "mechanical", "stable": True, "status": "FAIL",
    }
    _write_artifact(root, "transition_certificate", "fixed-transition", {
        "transition_id": "mechanical-revision", "before_patch_hash": "first",
        "after_patch_hash": "fixed", "to_revision": 2,
        "decision": "COMMIT", "mechanical_pass": True,
        "mechanical_check_ids": ["mechanical"],
        "graph_delta": {
            "locked_trial_comparison": {
                "before_results": [locked_result],
                "after_results": [{**locked_result, "status": "PASS"}],
                "executed_check_ids": ["mechanical"],
                "comparable": True,
                "mechanical_failures_before": ["mechanical"],
                "mechanical_failures_after": [],
            },
            "progress_metrics": {"mechanical_health_delta": 1},
        },
    })
    (root / "terminal_certificate.json").write_text(json.dumps({
        "instance_id": "case", "final_diff_hash": "fixed",
        "final_checkpoint_id": "fixed-checkpoint", "graph_reached": False,
    }), encoding="utf-8")

    report = build_revision_trajectory_report(root)

    assert report["revisions"][0]["confirmed_comparable_execution"]
    assert report["revisions"][0]["trigger_authority"] == "MECHANICAL"
    assert report["policy_replay"]["selected_patch_hash"] == "fixed"
    assert not report["policy_replay"]["used_first_patch"]
