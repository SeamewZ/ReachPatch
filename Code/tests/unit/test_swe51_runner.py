from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_generation_worker_count_uses_fixed_requested_concurrency() -> None:
    assert runner._safe_generation_worker_count(4, {
        "memavailable_mib": 1,
    }) == 4
    assert runner._safe_generation_worker_count(10, {
        "memavailable_mib": 0,
    }) == 10
    assert runner._safe_generation_worker_count(20, {}) == 10


def test_method_config_hash_changes_with_dirty_production_source(
    tmp_path: Path, monkeypatch,
) -> None:
    code_root = tmp_path / "Code"
    package = code_root / "reachpatch"
    package.mkdir(parents=True)
    source = package / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    runner_file = code_root / "runner.py"
    runner_file.write_text("RUNNER = 1\n", encoding="utf-8")
    monkeypatch.setattr(runner, "CODE_ROOT", code_root)
    monkeypatch.setattr(runner, "__file__", str(runner_file))

    before = runner._method_config_hash("model", 6)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    after = runner._method_config_hash("model", 6)

    assert before != after


def test_broker_socket_path_is_bounded_for_long_instance_id(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(runner, "RUN_ROOT", tmp_path / "runs")

    path = runner._broker_socket_path("scikit-learn__scikit-learn-" + "9" * 200)

    assert len(str(path).encode()) < 104
    assert path.parent == (tmp_path / "runs" / "_b").resolve()
    assert "scikit-learn" not in path.name


def test_prepare_case_trees_checks_out_exact_public_base_commit(
    tmp_path: Path, monkeypatch,
) -> None:
    origin = tmp_path / "origin"
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(origin)],
        check=True, capture_output=True, text=True,
    )
    (origin / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "module.py"], cwd=origin,
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        [
            "git", "-c", "user.name=ReachPatch Test",
            "-c", "user.email=reachpatch@example.invalid",
            "commit", "-m", "base",
        ],
        cwd=origin, check=True, capture_output=True, text=True,
    )
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    repo_root = tmp_path / "repos"
    tree_root = tmp_path / "trees"
    monkeypatch.setattr(runner, "REPO_ROOT", repo_root)
    monkeypatch.setattr(runner, "TREE_ROOT", tree_root)
    monkeypatch.setattr(runner, "_repository_url", lambda _: str(origin))
    records = [{
        "instance_id": "owner__project-1",
        "repo": "owner/project",
        "base_commit": base_commit,
    }]

    created = runner.prepare_case_trees(records)
    reused = runner.prepare_case_trees(records)

    assert created["created_case_trees"] == ["owner__project-1"]
    assert reused["reused_case_trees"] == ["owner__project-1"]
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tree_root / "owner__project-1",
        check=True, capture_output=True, text=True,
    ).stdout.strip() == base_commit
    assert (tree_root / "owner__project-1" / "module.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 1\n"


def test_generation_preflight_rejects_official_field_before_api_call(
    tmp_path: Path, monkeypatch,
) -> None:
    case_id = "owner__project-1"
    key_path = tmp_path / "key"
    key_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", tmp_path)
    monkeypatch.setattr(runner, "TREE_ROOT", tmp_path / "trees")
    monkeypatch.setattr(runner, "_run_git", lambda *args, **kwargs: "base")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/bin/bwrap")
    monkeypatch.setattr(
        runner, "_memory_snapshot",
        lambda: {"memavailable_mib": 64 * 1024},
    )
    monkeypatch.setattr(
        runner, "_disk_snapshot",
        lambda: {"disk_total_mib": 100_000, "disk_used_mib": 10_000,
                 "disk_free_mib": 90_000},
    )
    image = runner.public_swebench_image(case_id)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=image + "\n", stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="generation preflight failed"):
        runner.validate_generation_preflight([{
            "instance_id": case_id,
            "repo": "owner/project",
            "base_commit": "base",
            "problem_statement": "public issue",
            "test_patch": "official-only",
        }], key_path=key_path)

    report = json.loads(
        (tmp_path / "generation_preflight.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "FAIL"
    assert any(item["check"] == "public_payload" for item in report["failures"])


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
        "instance_id": "case-b", "status": "REACHED",
    })
    monkeypatch.setattr(runner, "PUBLIC_PATH", public_path)
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", experiment_root)
    monkeypatch.setattr(runner, "RESULT_ROOT", result_root)
    monkeypatch.setattr(runner, "prepare_case_trees", lambda _: {})
    monkeypatch.setattr(
        runner, "validate_generation_preflight", lambda *args, **kwargs: {},
    )

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
    assert summary["current_run_generated_count"] == 1
    assert summary["observed_case_count"] == 2
    assert {item["instance_id"] for item in summary["results"]} == {
        "case-a", "case-b",
    }
    assert summary["cache_rejected_count"] == 0


def test_generation_sandbox_hides_official_inputs_and_harness_outputs(
    tmp_path: Path, monkeypatch,
) -> None:
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is unavailable")
    dataset_collection_root = tmp_path / "datasets"
    dataset_root = dataset_collection_root / "current"
    public_path = dataset_root / "generation_public_instances.jsonl"
    official_path = dataset_root / "official_instances.jsonl"
    raw_cases = dataset_root / "cases"
    sibling_official = dataset_collection_root / "prior" / "official_instances.jsonl"
    experiment_root = tmp_path / "experiment"
    harness_root = experiment_root / "harness"
    repo_root = experiment_root / "repos"
    run_root = experiment_root / "runs"
    result_root = experiment_root / "results"
    raw_cases.mkdir(parents=True)
    harness_root.mkdir(parents=True)
    repo_root.mkdir(parents=True)
    run_root.mkdir(parents=True)
    result_root.mkdir(parents=True)
    public_path.write_text('{"instance_id":"public"}\n', encoding="utf-8")
    official_path.write_text('{"test_patch":"secret"}\n', encoding="utf-8")
    sibling_official.parent.mkdir(parents=True)
    sibling_official.write_text('{"test_patch":"prior-secret"}\n', encoding="utf-8")
    (raw_cases / "case.json").write_text("secret", encoding="utf-8")
    (harness_root / "report.json").write_text("secret", encoding="utf-8")
    (experiment_root / "harness_summary.json").write_text("secret", encoding="utf-8")

    monkeypatch.setattr(runner, "DATASET_COLLECTION_ROOT", dataset_collection_root)
    monkeypatch.setattr(runner, "DATASET_ROOT", dataset_root)
    monkeypatch.setattr(runner, "PUBLIC_PATH", public_path)
    monkeypatch.setattr(runner, "OFFICIAL_PATH", official_path)
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", experiment_root)
    monkeypatch.setattr(runner, "HARNESS_ROOT", harness_root)
    monkeypatch.setattr(runner, "REPO_ROOT", repo_root)
    monkeypatch.setattr(runner, "RUN_ROOT", run_root)
    monkeypatch.setattr(runner, "RESULT_ROOT", result_root)

    probe = f"""
import json
import os
import stat
from pathlib import Path
p = Path({str(dataset_root)!r})
e = Path({str(experiment_root)!r})
try:
    (e / "harness_summary.json").read_text()
except OSError:
    prior_summary = "BLOCKED"
else:
    prior_summary = "READABLE"
print(json.dumps({{
    "isolated": os.environ.get("REACHPATCH_GENERATION_ISOLATED"),
    "public": (p / "generation_public_instances.jsonl").is_file(),
    "official": (p / "official_instances.jsonl").exists(),
    "sibling_official": Path({str(sibling_official)!r}).exists(),
    "raw_cases": (p / "cases").exists(),
    "harness_report": (e / "harness" / "report.json").exists(),
    "prior_summary": prior_summary,
    "docker_socket": (
        Path("/run/docker.sock").exists()
        and stat.S_ISSOCK(Path("/run/docker.sock").stat().st_mode)
    ),
    "resolver_available": bool(Path("/etc/resolv.conf").read_text().strip()),
    "proc_pid_count": len(list(Path("/proc").glob("[0-9]*"))),
}}))
"""
    command = runner._generation_sandbox_command(
        [sys.executable, "-c", probe]
    )
    completed = subprocess.run(
        command, cwd=runner.CODE_ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed.pop("proc_pid_count") <= 3
    assert observed == {
        "isolated": "1",
        "public": True,
        "official": False,
        "sibling_official": False,
        "raw_cases": False,
        "harness_report": False,
        "prior_summary": "BLOCKED",
        "docker_socket": False,
        "resolver_available": True,
    }


def test_direct_case_generation_requires_public_only_sandbox(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv(runner.GENERATION_ISOLATION_ENV, raising=False)

    with pytest.raises(RuntimeError, match="public-only sandbox"):
        runner.generate_case(
            "case-a", tmp_path / "key", "deepseek-chat", 1,
        )


def test_harness_cached_patch_is_not_reexecuted(tmp_path: Path, monkeypatch) -> None:
    result_root = tmp_path / "harness" / "results"
    result_root.mkdir(parents=True)
    monkeypatch.setattr(runner, "HARNESS_RESULT_ROOT", result_root)
    monkeypatch.setattr(runner, "HARNESS_ROOT", tmp_path / "harness")
    patch = tmp_path / "final_patch.diff"
    patch.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
    patch_hash = runner.content_hash(patch.read_text(encoding="utf-8"))
    patch_sha = __import__("hashlib").sha256(
        patch.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    harness_run_id = f"sealed-{patch_hash[:16]}"
    lineage = {
        "instance_id": "case-a",
        "code_commit_sha": "code",
        "implementation_tree_hash": "tree",
        "method_config_hash": "config",
        "prompt_hash": "prompt",
        "generation_run_id": "generation",
        "patch_hash": patch_hash,
        "patch_file_sha256": patch_sha,
        "dataset_manifest_hash": "dataset",
        "harness_engine": "official_swebench_docker_v1",
        "harness_run_id": harness_run_id,
    }
    runner._write_json(result_root / "case-a.json", {
        "instance_id": "case-a",
        "status": "PASS",
        "generation_patch_hash": patch_hash,
        **lineage,
    })

    result = runner._harness_one(
        {"instance_id": "case-a"},
        {"patch_hash": patch_hash, "patch_path": str(patch), **{
            key: value for key, value in lineage.items() if key != "instance_id"
        }},
        1,
    )

    assert result["status"] == "PASS"
    assert not (tmp_path / "harness" / "case-a").exists()


def test_host_harness_cache_is_not_treated_as_official(
    tmp_path: Path, monkeypatch,
) -> None:
    patch = tmp_path / "final_patch.diff"
    patch.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
    patch_hash = runner.content_hash(patch.read_text(encoding="utf-8"))
    result_root = tmp_path / "harness" / "results"
    result_root.mkdir(parents=True)
    monkeypatch.setattr(runner, "HARNESS_ROOT", tmp_path / "harness")
    monkeypatch.setattr(runner, "HARNESS_CASE_ROOT", tmp_path / "harness" / "cases")
    monkeypatch.setattr(runner, "HARNESS_RESULT_ROOT", result_root)
    monkeypatch.setattr(runner, "HARNESS_HISTORY_ROOT", tmp_path / "history")
    monkeypatch.setattr(runner, "HARNESS_LOG_ROOT", tmp_path / "official")
    runner._write_json(result_root / "case-a.json", {
        "instance_id": "case-a",
        "status": "UNKNOWN_EXECUTION",
        "generation_patch_hash": patch_hash,
    })
    calls: list[str] = []

    def fake_official(raw, **kwargs):
        calls.append(raw["instance_id"])
        return {"status": "PASS"}

    monkeypatch.setattr(runner, "run_official_swebench_instance", fake_official)
    result = runner._harness_one(
        {
            "instance_id": "case-a", "repo": "o/r", "base_commit": "base",
            "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]",
        },
        {"patch_path": str(patch), "patch_hash": patch_hash},
        30,
    )

    assert calls == ["case-a"]
    assert result["status"] == "PASS"
    assert result["harness_engine"] == "official_swebench_docker_v1"


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
    assert {item["instance_id"] for item in summary["results"]} == {"case-a"}
    assert summary["cache_rejected_count"] >= 1


def test_case_process_report_records_graphs_and_successful_transition(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(runner, "EXPERIMENT_ROOT", tmp_path)
    generation = {
        "results": [{
            "instance_id": "case-a",
            "status": "REACHED",
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
        "status": "REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE",
        "analysis_resources": {
            "peak_rss_mib": 512.0,
            "active_program_slice": {
                "complete_peak_rss_mib": 500.0,
            },
        },
        "analysis_timings": {"active_program_slice_seconds": 2.0},
    }

    assert runner._failure_point(row) == "active_program_slice"


def test_harness_uses_sealed_patch_with_official_adapter(
    tmp_path: Path, monkeypatch,
) -> None:
    patch = tmp_path / "final_patch.diff"
    patch.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
    result_root = tmp_path / "harness" / "results"
    monkeypatch.setattr(runner, "HARNESS_ROOT", tmp_path / "harness")
    monkeypatch.setattr(runner, "HARNESS_CASE_ROOT", tmp_path / "harness" / "cases")
    monkeypatch.setattr(runner, "HARNESS_RESULT_ROOT", result_root)
    monkeypatch.setattr(runner, "HARNESS_LOG_ROOT", tmp_path / "harness" / "official")
    captured: dict = {}

    def fake_official(raw, **kwargs):
        captured.update({"raw": raw, **kwargs})
        return {
            "status": "PASS",
            "patch_apply": {"status": "PASS"},
            "fail_to_pass": {"status": "PASS"},
            "pass_to_pass": {"status": "PASS"},
        }

    monkeypatch.setattr(runner, "run_official_swebench_instance", fake_official)
    patch_text = patch.read_text(encoding="utf-8")
    result = runner._harness_one(
        {
            "instance_id": "case-a",
            "repo": "owner/repo",
            "base_commit": "abc",
            "FAIL_TO_PASS": "[]",
            "PASS_TO_PASS": "[]",
        },
        {
            "status": "SEALED",
            "patch_hash": runner.content_hash(patch_text),
            "patch_path": str(patch),
        },
        30,
        force=True,
    )

    assert result["status"] == "PASS"
    assert captured["patch_text"] == patch_text
    assert captured["log_root"] == tmp_path / "harness" / "official"
    assert result["patch_file_sha256"] == hashlib.sha256(
        patch_text.encode("utf-8")
    ).hexdigest()
    assert (tmp_path / "harness" / "cases" / "case-a" /
            "harness_evaluation_instance.json").is_file()


def test_sealed_predictions_excludes_empty_patches(tmp_path: Path) -> None:
    nonempty = tmp_path / "nonempty.diff"
    empty = tmp_path / "empty.diff"
    nonempty.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
    empty.write_text("", encoding="utf-8")

    rows = runner._sealed_predictions({
        "case-a": {"patch_path": str(nonempty)},
        "case-b": {"patch_path": str(empty)},
        "case-c": {"patch_path": str(tmp_path / "missing.diff")},
    })

    assert rows == [{
        "instance_id": "case-a",
        "model_name_or_path": "reachpatch",
        "model_patch": "diff --git a/a.py b/a.py\n",
    }]
