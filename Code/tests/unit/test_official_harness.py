from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from reachpatch.execution.official_harness import run_official_swebench_instance


class _Images:
    def __init__(self, available: bool = True) -> None:
        self.available = available

    def get(self, key: str) -> object:
        if not self.available:
            raise RuntimeError(f"missing {key}")
        return object()


class _Client:
    def __init__(self, available: bool = True) -> None:
        self.images = _Images(available)


def _spec() -> SimpleNamespace:
    return SimpleNamespace(
        instance_image_key="swebench/example:latest",
        eval_script=(
            "git apply -v - <<'EOF'\npublic test patch\nEOF\n"
            ": '>>>>> Start Test Output'\npytest tests/test_public.py\n"
            ": '>>>>> End Test Output'\n"
        ),
        FAIL_TO_PASS=["tests/test_public.py::test_target"],
        PASS_TO_PASS=["tests/test_public.py::test_preserve"],
    )


def test_official_harness_parses_upstream_report(tmp_path: Path) -> None:
    case_id = "owner__repo-1"
    log_root = tmp_path / "official"

    def execute(spec, prediction, **kwargs):
        case_root = log_root / kwargs["run_id"] / "reachpatch" / case_id
        case_root.mkdir(parents=True)
        (case_root / "run_instance.log").write_text(
            ">>>>> Applied Patch\n", encoding="utf-8"
        )
        (case_root / "test_output.txt").write_text("official output", encoding="utf-8")
        (case_root / "report.json").write_text(json.dumps({
            case_id: {
                "patch_successfully_applied": True,
                "resolved": True,
                "tests_status": {
                    "FAIL_TO_PASS": {
                        "success": ["tests/test_public.py::test_target"],
                        "failure": [],
                    },
                    "PASS_TO_PASS": {
                        "success": ["tests/test_public.py::test_preserve"],
                        "failure": [],
                    },
                },
            }
        }), encoding="utf-8")
        return {"completed": True, "resolved": True}

    result = run_official_swebench_instance(
        {
            "instance_id": case_id,
            "test_patch": "public test patch",
        },
        patch_text="diff --git a/a.py b/a.py\n",
        log_root=log_root,
        run_id="run-1",
        timeout=60,
        client=_Client(),
        make_test_spec_fn=lambda raw, namespace: _spec(),
        run_instance_fn=execute,
    )

    assert result["status"] == "PASS"
    assert result["patch_apply"] == {
        "status": "PASS", "official_marker_seen": True,
    }
    assert result["official_test_patch_wired"] is True
    assert result["official_test_patch_applied"] is True
    assert result["official_test_command"] == "pytest tests/test_public.py"
    assert result["fail_to_pass"]["executed_count"] == 1
    assert result["pass_to_pass"]["status"] == "PASS"


def test_official_harness_accepts_upstream_tuple_result(tmp_path: Path) -> None:
    case_id = "owner__repo-tuple"
    log_root = tmp_path / "official"
    report = {
        case_id: {
            "patch_successfully_applied": True,
            "resolved": True,
            "tests_status": {
                "FAIL_TO_PASS": {"success": ["target"], "failure": []},
                "PASS_TO_PASS": {"success": ["preserve"], "failure": []},
            },
        }
    }

    def execute(spec, prediction, **kwargs):
        case_root = log_root / kwargs["run_id"] / "reachpatch" / case_id
        case_root.mkdir(parents=True)
        (case_root / "run_instance.log").write_text(
            ">>>>> Applied Patch\n", encoding="utf-8"
        )
        (case_root / "test_output.txt").write_text("official output", encoding="utf-8")
        (case_root / "report.json").write_text(
            json.dumps(report), encoding="utf-8"
        )
        return case_id, report

    result = run_official_swebench_instance(
        {"instance_id": case_id, "test_patch": "public test patch"},
        patch_text="diff --git a/a.py b/a.py\n",
        log_root=log_root,
        run_id="run-tuple",
        timeout=60,
        client=_Client(),
        make_test_spec_fn=lambda raw, namespace: _spec(),
        run_instance_fn=execute,
    )

    assert result["status"] == "PASS"
    assert result["upstream_result"] == {
        "instance_id": case_id,
        "completed": True,
        "report_available": True,
    }


def test_official_harness_handles_upstream_timeout_result(tmp_path: Path) -> None:
    case_id = "owner__repo-timeout"
    log_root = tmp_path / "official"

    def execute(spec, prediction, **kwargs):
        case_root = log_root / kwargs["run_id"] / "reachpatch" / case_id
        case_root.mkdir(parents=True)
        (case_root / "run_instance.log").write_text(
            "Test timed out after 60 seconds.\n", encoding="utf-8"
        )
        (case_root / "test_output.txt").write_text(
            "Timeout error: 60 seconds exceeded.", encoding="utf-8"
        )
        return None

    result = run_official_swebench_instance(
        {"instance_id": case_id, "test_patch": "public test patch"},
        patch_text="diff --git a/a.py b/a.py\n",
        log_root=log_root,
        run_id="run-timeout",
        timeout=60,
        client=_Client(),
        make_test_spec_fn=lambda raw, namespace: _spec(),
        run_instance_fn=execute,
    )

    assert result["status"] == "UNKNOWN_EXECUTION"
    assert result["completed"] is False
    assert result["upstream_result"] == {}


def test_official_harness_distinguishes_patch_apply_failure(tmp_path: Path) -> None:
    case_id = "owner__repo-2"
    log_root = tmp_path / "official"

    def execute(spec, prediction, **kwargs):
        case_root = log_root / kwargs["run_id"] / "reachpatch" / case_id
        case_root.mkdir(parents=True)
        (case_root / "run_instance.log").write_text(
            ">>>>> Patch Apply Failed\n", encoding="utf-8"
        )
        return {"completed": False, "resolved": False}

    result = run_official_swebench_instance(
        {"instance_id": case_id, "test_patch": "test diff"},
        patch_text="invalid patch",
        log_root=log_root,
        run_id="run-2",
        timeout=60,
        client=_Client(),
        make_test_spec_fn=lambda raw, namespace: _spec(),
        run_instance_fn=execute,
    )

    assert result["status"] == "FAIL_PATCH_APPLY"
    assert result["patch_apply"]["status"] == "FAIL"


def test_official_harness_reports_missing_exact_image(tmp_path: Path) -> None:
    result = run_official_swebench_instance(
        {"instance_id": "owner__repo-3", "test_patch": "test diff"},
        patch_text="patch",
        log_root=tmp_path,
        run_id="run-3",
        timeout=60,
        client=_Client(available=False),
        make_test_spec_fn=lambda raw, namespace: _spec(),
        run_instance_fn=lambda *args, **kwargs: {},
    )

    assert result["status"] == "HARNESS_NOT_RUN"
    assert result["image"] == "swebench/example:latest"
