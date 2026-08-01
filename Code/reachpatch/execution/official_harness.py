from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable


class OfficialHarnessUnavailable(RuntimeError):
    """Raised when the isolated SWE-bench evaluation runtime is unavailable."""


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _evaluation_command(eval_script: str) -> str:
    lines = eval_script.splitlines()
    for index, line in enumerate(lines):
        if ">>>>> Start Test Output" not in line:
            continue
        for candidate in lines[index + 1 :]:
            if candidate.strip() and ">>>>> End Test Output" not in candidate:
                return candidate.strip()
    return ""


def _test_result(
    tests_status: dict[str, Any],
    key: str,
    *,
    completed: bool,
) -> dict[str, Any]:
    value = tests_status.get(key, {})
    succeeded = tuple(map(str, value.get("success", ()))) if isinstance(value, dict) else ()
    failed = tuple(map(str, value.get("failure", ()))) if isinstance(value, dict) else ()
    if not completed:
        status = "UNKNOWN_EXECUTION"
    else:
        status = "FAIL" if failed else "PASS"
    return {
        "status": status,
        "success": list(succeeded),
        "failure": list(failed),
        "executed_count": len(succeeded) + len(failed),
    }


def run_official_swebench_instance(
    raw: dict[str, Any],
    *,
    patch_text: str,
    log_root: Path,
    run_id: str,
    timeout: int,
    namespace: str = "swebench",
    client: Any | None = None,
    make_test_spec_fn: Callable[..., Any] | None = None,
    run_instance_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate one sealed patch with the upstream SWE-bench Docker harness.

    Official fields are accepted only here, after generation has sealed the patch.
    The caller chooses a log root outside ArtifactStore so official outcomes cannot
    be restored into a repair session.
    """

    docker_module = None
    upstream_evaluation = None
    make_test_spec = None
    apply_patch_fail = ">>>>> Patch Apply Failed"
    apply_patch_pass = ">>>>> Applied Patch"
    try:
        if client is None:
            import docker as docker_module
        if make_test_spec_fn is None or run_instance_fn is None:
            import swebench.harness.run_evaluation as upstream_evaluation
            from swebench.harness.constants import (
                APPLY_PATCH_FAIL as apply_patch_fail,
                APPLY_PATCH_PASS as apply_patch_pass,
            )
            from swebench.harness.test_spec.test_spec import make_test_spec
    except ImportError as exc:
        raise OfficialHarnessUnavailable(
            "install the official SWE-bench harness and Docker Python client"
        ) from exc

    make_spec = make_test_spec_fn or make_test_spec
    execute = run_instance_fn or upstream_evaluation.run_instance
    test_spec = make_spec(raw, namespace=namespace)
    case_id = str(raw["instance_id"])
    model_name = "reachpatch"
    log_root = log_root.resolve()
    log_root.mkdir(parents=True, exist_ok=True)

    # run_instance imports this path as a module global. It must be absolute so
    # concurrent cases share one isolated root without process-wide chdir calls.
    if upstream_evaluation is not None:
        upstream_evaluation.RUN_EVALUATION_LOG_DIR = log_root
    case_log_root = log_root / run_id / model_name / case_id
    prediction = {
        "instance_id": case_id,
        "model_name_or_path": model_name,
        "model_patch": patch_text,
    }
    try:
        docker_client = client or docker_module.from_env()
    except Exception as exc:
        return {
            "status": "HARNESS_NOT_RUN",
            "error": f"official Docker runtime unavailable: {exc}",
            "log_root": str(case_log_root),
        }
    try:
        docker_client.images.get(test_spec.instance_image_key)
    except Exception as exc:
        return {
            "status": "HARNESS_NOT_RUN",
            "error": f"official SWE-bench image unavailable: {exc}",
            "image": test_spec.instance_image_key,
            "log_root": str(case_log_root),
        }

    started = time.monotonic()
    upstream_result = execute(
        test_spec,
        prediction,
        rm_image=False,
        force_rebuild=False,
        client=docker_client,
        run_id=run_id,
        timeout=timeout,
        rewrite_reports=False,
    )
    duration = time.monotonic() - started
    report_path = case_log_root / "report.json"
    instance_log_path = case_log_root / "run_instance.log"
    test_output_path = case_log_root / "test_output.txt"
    report_payload = _read_json(report_path) or {}
    case_report = report_payload.get(case_id, {})
    if not isinstance(case_report, dict):
        case_report = {}
    if isinstance(upstream_result, tuple) and len(upstream_result) == 2:
        upstream_case_id, upstream_report = upstream_result
        upstream_completed = bool(
            str(upstream_case_id) == case_id
            and isinstance(upstream_report, Mapping)
            and upstream_report
        )
        normalized_upstream_result = {
            "instance_id": str(upstream_case_id),
            "completed": upstream_completed,
            "report_available": bool(upstream_report),
        }
    elif isinstance(upstream_result, Mapping):
        upstream_completed = bool(upstream_result.get("completed"))
        normalized_upstream_result = dict(upstream_result)
    else:
        upstream_completed = False
        normalized_upstream_result = {}
    completed = upstream_completed and bool(case_report)
    tests_status = case_report.get("tests_status", {})
    if not isinstance(tests_status, dict):
        tests_status = {}
    fail_to_pass = _test_result(tests_status, "FAIL_TO_PASS", completed=completed)
    pass_to_pass = _test_result(tests_status, "PASS_TO_PASS", completed=completed)

    instance_log = (
        instance_log_path.read_text(encoding="utf-8", errors="replace")
        if instance_log_path.is_file()
        else ""
    )
    patch_applied = bool(case_report.get("patch_successfully_applied"))
    if completed and bool(case_report.get("resolved")):
        status = "PASS"
    elif completed and pass_to_pass["status"] == "FAIL":
        status = "FAIL_PRESERVATION_REGRESSION"
    elif completed:
        status = "FAIL_TARGET"
    elif apply_patch_fail in instance_log:
        status = "FAIL_PATCH_APPLY"
    elif "timed out" in instance_log.lower() or "timeout" in instance_log.lower():
        status = "UNKNOWN_EXECUTION"
    else:
        status = "HARNESS_NOT_RUN"

    eval_script = str(test_spec.eval_script)
    official_test_patch_wired = (
        "git apply" in eval_script
        and ">>>>> Start Test Output" in eval_script
        and ">>>>> End Test Output" in eval_script
    )
    return {
        "status": status,
        "completed": completed,
        "resolved": bool(case_report.get("resolved")),
        "image": test_spec.instance_image_key,
        "duration_seconds": duration,
        "patch_apply": {
            "status": "PASS" if patch_applied else (
                "FAIL" if apply_patch_fail in instance_log else "UNKNOWN_EXECUTION"
            ),
            "official_marker_seen": apply_patch_pass in instance_log,
        },
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": pass_to_pass,
        "official_test_patch_wired": official_test_patch_wired,
        "official_test_patch_applied": patch_applied,
        "official_test_patch_sha256": hashlib.sha256(
            str(raw.get("test_patch", "")).encode("utf-8")
        ).hexdigest(),
        "official_test_command": _evaluation_command(eval_script),
        "official_fail_to_pass": list(map(str, test_spec.FAIL_TO_PASS)),
        "official_pass_to_pass_count": len(test_spec.PASS_TO_PASS),
        "upstream_result": normalized_upstream_result,
        "log_root": str(case_log_root),
        "report_path": str(report_path),
        "instance_log_path": str(instance_log_path),
        "test_output_path": str(test_output_path),
    }
