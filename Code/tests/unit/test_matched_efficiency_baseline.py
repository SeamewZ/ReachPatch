import json

from experiments import run_matched_efficiency_baseline as baseline


def test_empty_response_and_duplicate_only_actions_are_distinct():
    data = {"events": [
        {"kind": "MODEL", "tool_call_count": 0, "has_text_response": False},
        {"kind": "MODEL", "tool_call_count": 2, "novel_tool_action_count": 0,
         "duplicate_tool_action_count": 2, "has_text_response": False},
        {"kind": "MODEL", "tool_call_count": 0, "has_text_response": True},
        {"kind": "MODEL_ERROR", "usage_reported": False},
    ]}
    metrics = baseline.attempt_metrics(data)
    assert metrics["empty_response_calls"] == 1
    assert metrics["duplicate_only_calls"] == 1
    assert metrics["tool_actions"] == 2
    assert baseline.outcome({"error_ids": ["case"]}, "case") == "EVALUATION_ERROR"


def test_cost_includes_archived_attempt_once_and_marks_missing_usage(tmp_path):
    selected = tmp_path / "runs" / "case"
    archived = tmp_path / "failed_attempts" / "case" / "1" / "run"
    missing = tmp_path / "failed_attempts" / "case" / "2" / "run"
    for path, calls in ((selected, 2), (archived, 3)):
        path.mkdir(parents=True)
        budget = {"model_calls": calls, "usage": {"total_tokens": calls * 100}, "events": []}
        (path / "case_budget.json").write_text(json.dumps(budget))
        (path / "token_efficiency.json").write_text(json.dumps({"budget": budget}))
    missing.mkdir(parents=True)
    cost = baseline.case_cost(tmp_path, "case", {"run_root": str(selected)})
    assert cost["totals"]["model_calls"] == 5
    assert cost["totals"]["reported_tokens"] == 500
    assert cost["selected_attempt_metrics"]["model_calls"] == 2
    assert cost["attempts_missing_budget"] == 1


def test_failed_generation_remains_in_sealed_denominator(tmp_path):
    patch = tmp_path / "good.patch"
    patch.write_text("diff --git a/x b/x\n")
    summary = {"implementation_hash": "same", "results": [{
        "instance_id": "good", "p0_patch_path": str(patch), "final_patch_path": str(patch),
        "p0_patch_sha256": baseline.runner._sha256(patch),
        "final_patch_sha256": baseline.runner._sha256(patch),
    }]}
    (tmp_path / "generation_summary.json").write_text(json.dumps(summary))
    baseline.seal_with_failures(tmp_path, {"implementation_hash": "same", "instance_ids": ["good", "failed"]})
    sealed = baseline.read(tmp_path / "sealed_generation.json")
    predictions = [json.loads(x) for x in (tmp_path / "harness/sealed_final_predictions.jsonl").read_text().splitlines()]
    assert sealed["case_count"] == 2
    assert sealed["generation_failed_ids"] == ["failed"]
    assert predictions[1] == {"instance_id": "failed", "model_name_or_path": "reachpatch-final", "model_patch": ""}
