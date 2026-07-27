from __future__ import annotations

import json
import sys
from pathlib import Path

from experiments.swe51 import runner


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
    )


def test_archive_path_preserves_each_attempt(tmp_path: Path) -> None:
    result = tmp_path / "case.json"
    result.write_text('{"status":"ERROR"}\n', encoding="utf-8")
    archived = runner._archive_path(result, tmp_path / "history", "case")

    assert archived is not None
    assert not result.exists()
    assert archived.read_text(encoding="utf-8") == '{"status":"ERROR"}\n'


def test_generate_only_replaces_selected_and_retains_other_results(
    tmp_path: Path, monkeypatch,
) -> None:
    public_path = tmp_path / "public.jsonl"
    experiment_root = tmp_path / "experiment"
    result_root = experiment_root / "results"
    _write_jsonl(public_path, [
        {"instance_id": "case-a", "repo": "o/r", "base_commit": "a"},
        {"instance_id": "case-b", "repo": "o/r", "base_commit": "b"},
    ])
    result_root.mkdir(parents=True)
    runner._write_json(result_root / "case-b.json", {
        "instance_id": "case-b", "status": "GRAPH_REACHED",
    })
    monkeypatch.setattr(runner, "PUBLIC_PATH", public_path)
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", experiment_root)
    monkeypatch.setattr(runner, "RESULT_ROOT", result_root)

    calls: list[tuple[str, bool]] = []

    def fake_run(raw, **kwargs):
        calls.append((raw["instance_id"], kwargs["force"]))
        return {"instance_id": raw["instance_id"], "status": "ERROR"}

    monkeypatch.setattr(runner, "_run_case_subprocess", fake_run)
    summary = runner.generate(
        2, 3, "deepseek-chat", tmp_path / "key",
        only={"case-a"}, force=True,
    )

    assert calls == [("case-a", True)]
    assert summary["selected_case_count"] == 1
    assert {item["instance_id"] for item in summary["results"]} == {
        "case-a", "case-b",
    }
    retained = next(
        item for item in summary["results"] if item["instance_id"] == "case-b"
    )
    assert retained["status"] == "GRAPH_REACHED"


def test_harness_cached_patch_is_not_reexecuted(tmp_path: Path, monkeypatch) -> None:
    result_root = tmp_path / "harness" / "results"
    result_root.mkdir(parents=True)
    monkeypatch.setattr(runner, "HARNESS_RESULT_ROOT", result_root)
    monkeypatch.setattr(runner, "HARNESS_ROOT", tmp_path / "harness")
    runner._write_json(result_root / "case-a.json", {
        "instance_id": "case-a",
        "status": "PASS",
        "generation_patch_hash": "patch-1",
    })

    result = runner._harness_one(
        {"instance_id": "case-a"},
        {"patch_hash": "patch-1"},
        1,
    )

    assert result["status"] == "PASS"
    assert not (tmp_path / "harness" / "case-a").exists()


def test_harness_only_merges_previous_case_results(tmp_path: Path, monkeypatch) -> None:
    experiment_root = tmp_path / "experiment"
    official_path = tmp_path / "official.jsonl"
    harness_results = experiment_root / "harness" / "results"
    _write_jsonl(official_path, [
        {"instance_id": "case-a"},
        {"instance_id": "case-b"},
    ])
    runner._write_json(experiment_root / "generation_summary.json", {
        "results": [
            {"instance_id": "case-a", "patch_hash": "a"},
            {"instance_id": "case-b", "patch_hash": "b"},
        ],
    })
    runner._write_json(experiment_root / "harness_summary.json", {
        "results": [{"instance_id": "case-b", "status": "PASS"}],
    })
    monkeypatch.setattr(runner, "OFFICIAL_PATH", official_path)
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", experiment_root)
    monkeypatch.setattr(runner, "HARNESS_RESULT_ROOT", harness_results)

    calls: list[str] = []

    def fake_harness(raw, generation, timeout, *, force=False):
        calls.append(raw["instance_id"])
        return {"instance_id": raw["instance_id"], "status": "FAIL_TARGET"}

    monkeypatch.setattr(runner, "_harness_one", fake_harness)
    summary = runner.harness(2, 10, only={"case-a"}, force=True)

    assert calls == ["case-a"]
    assert summary["selected_case_count"] == 1
    assert {item["instance_id"] for item in summary["results"]} == {
        "case-a", "case-b",
    }
    retained = next(
        item for item in summary["results"] if item["instance_id"] == "case-b"
    )
    assert retained["status"] == "PASS"


def test_case_process_report_records_graphs_and_successful_transition(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", tmp_path)
    generation = {
        "results": [{
            "instance_id": "case-a",
            "status": "GRAPH_REACHED",
            "analysis_timings": {
                "semantic_analysis_seconds": 1,
                "repository_index_seconds": 2,
                "requirement_core_seconds": 3,
                "active_program_slice_seconds": 4,
                "binding_graph_update_seconds": 5,
                "challenge_graph_update_seconds": 6,
                "first_patch_generation_seconds": 7,
            },
            "transition_certificates": [{
                "transition_id": "transition-1",
                "decision": "COMMIT",
                "actual_edit_ids": ["edit-1"],
                "eliminated_counterexample_ids": ["counterexample-1"],
            }],
            "component_effectiveness": [{"component_id": "c", "effective": True}],
        }],
    }

    report = runner.write_case_process_report(generation)
    row = report["results"][0]

    assert row["graph_timings_seconds"] == {
        "semantic_graph": 1.0,
        "repository_index": 2.0,
        "requirement_graph": 3.0,
        "program_graph": 4.0,
        "binding_graph": 5.0,
        "challenge_graph": 6.0,
        "initial_generation": 7.0,
    }
    assert row["accepted_transition_count"] == 1
    assert row["successful_steps"][0]["edit_ids"] == ["edit-1"]
    assert (tmp_path / "case_process_report.json").is_file()
    assert "case-a" in (tmp_path / "case_process_report.md").read_text(
        encoding="utf-8"
    )


def test_failure_point_accepts_scalar_resource_metrics() -> None:
    row = {
        "status": "BUDGET_EXHAUSTED",
        "analysis_resources": {
            "peak_rss_mib": 512.0,
            "active_program_slice": {
                "complete_peak_rss_mib": 500.0,
            },
        },
        "analysis_timings": {"active_program_slice_seconds": 2.0},
    }

    assert runner._failure_point(row) == "active_program_slice"


def test_harness_missing_pytest_is_blocked_external(tmp_path: Path) -> None:
    result = runner._run_command(
        [sys.executable, "-c", "raise ModuleNotFoundError('No module named pytest')"],
        tmp_path,
        10,
    )

    assert result["status"] == "BLOCKED_EXTERNAL"
