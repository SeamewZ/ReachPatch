from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import asdict
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable


CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from reachpatch.models.base import SCHEMA_VERSION, canonical_json, content_hash, utc_now
from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidConfig, ReachAvoidController
from reachpatch.reach_avoid.execution_checkpoint import EXECUTION_SCHEMA_NAME
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.repair import DeepSeekAgent, DeepSeekConfig, DeepSeekHTTPTransport
from reachpatch.reporting import PatchOutcomeComparison, summarize_patch_outcomes


DATASET_ROOT = CODE_ROOT / "dataset" / "patchpsro_55_unique51"
PUBLIC_PATH = DATASET_ROOT / "generation_public_instances.jsonl"
OFFICIAL_PATH = DATASET_ROOT / "official_instances.jsonl"
DIAGNOSTIC_OFFICIAL_PATH = CODE_ROOT / "dataset" / "diagnostic10_official_instances.jsonl"
SOURCE_TREE_ROOT = Path(os.environ.get(
    "REACHPATCH_SOURCE_TREE_ROOT",
    (
        CODE_ROOT / "experiments" / "reachavoid_diagnostic10_sources_20260908" / "case_trees"
        if os.environ.get("REACHPATCH_DIAGNOSTIC10") == "1"
        and (CODE_ROOT / "experiments" / "reachavoid_diagnostic10_sources_20260908" / "case_trees").is_dir()
        else CODE_ROOT / "experiments" / "swe51" / "case_trees"
    ),
)).resolve()
EXPERIMENT_ROOT = Path(os.environ.get(
    "REACHPATCH_RA51_ROOT",
    CODE_ROOT / "experiments" / "reachavoid_51_20260813",
)).resolve()
RUN_ROOT = EXPERIMENT_ROOT / "runs"
RESULT_ROOT = EXPERIMENT_ROOT / "results"
HARNESS_ROOT = EXPERIMENT_ROOT / "harness"
GENERATION_MANIFEST = EXPERIMENT_ROOT / "generation_manifest.json"
SEALED_MANIFEST = EXPERIMENT_ROOT / "sealed_generation.json"
GENERATION_SANDBOX_ENV = "REACHPATCH_RA51_PUBLIC_SANDBOX"
SCHEMA = "reachpatch-51-reach-avoid-v2"
FORBIDDEN_PUBLIC_KEYS = {
    "test_patch", "patch", "gold_patch", "hidden_tests", "harness_logs",
    "fail_to_pass", "pass_to_pass", "FAIL_TO_PASS", "PASS_TO_PASS",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _archive_failed_attempt(case_id: str) -> None:
    source_run = RUN_ROOT / case_id
    source_result = RESULT_ROOT / f"{case_id}.json"
    if not source_run.exists() and not source_result.exists():
        return
    destination = EXPERIMENT_ROOT / "failed_attempts" / case_id / str(time.time_ns())
    destination.mkdir(parents=True)
    if source_run.exists():
        try:
            source_run.replace(destination / "run")
        except PermissionError:
            # Older sandbox workers may have created the directory as
            # ``nobody``.  It is still readable evidence, but the invoking
            # user cannot rename it; leave it in place and let the next
            # attempt use an isolated suffixed run directory.
            marker = destination / "run_move_blocked.txt"
            marker.write_text(
                "The failed run directory could not be moved because its "
                "owner is not writable by the coordinator.\n",
                encoding="utf-8",
            )
    if source_result.exists():
        source_result.replace(destination / "result.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _implementation_hash() -> str:
    digest = hashlib.sha256()
    paths = sorted((CODE_ROOT / "reachpatch").rglob("*.py"))
    paths.append(Path(__file__).resolve())
    for path in paths:
        digest.update(path.relative_to(CODE_ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_worktree_digest(tree: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tree, capture_output=True,
        text=True, check=False, timeout=30,
    )
    if completed.returncode:
        raise RuntimeError(f"cannot inspect source tree {tree}")
    return content_hash(completed.stdout)


def _assert_public_value(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in FORBIDDEN_PUBLIC_KEYS:
                raise ValueError(f"official-only generation field: {path}.{key}")
            _assert_public_value(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_public_value(item, f"{path}[{index}]")


def _public_rows() -> list[dict[str, Any]]:
    rows = _read_jsonl(PUBLIC_PATH)
    if len(rows) != 51:
        raise RuntimeError(f"expected 51 public instances, found {len(rows)}")
    ids = [str(row.get("instance_id", "")) for row in rows]
    if len(set(ids)) != len(ids):
        raise RuntimeError("public generation instance IDs are not unique")
    for index, row in enumerate(rows):
        _assert_public_value(row, f"public[{index}]")
    return rows


def _cohort_rows(rows: list[dict[str, Any]], only: set[str]) -> list[dict[str, Any]]:
    """Freeze an explicit public-only cohort before any official data is read."""
    known = {str(row["instance_id"]) for row in rows}
    if only - known:
        raise ValueError(f"unknown instance IDs: {sorted(only - known)}")
    if only:
        return [row for row in rows if str(row["instance_id"]) in only]
    raw_size = os.environ.get("REACHPATCH_COHORT_SIZE", "").strip()
    if not raw_size:
        return rows
    size = int(raw_size)
    if size < 1 or size > len(rows):
        raise ValueError(f"REACHPATCH_COHORT_SIZE must be in 1..{len(rows)}")
    # Dataset order is part of the public input and is persisted in the seal.
    return rows[:size]


def _source_tree(row: dict[str, Any]) -> Path:
    case_id = str(row["instance_id"])
    tree = (SOURCE_TREE_ROOT / case_id).resolve()
    if not tree.is_dir():
        raise FileNotFoundError(tree)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True,
        text=True, check=False, timeout=30,
    )
    if head.returncode or head.stdout.strip() != str(row["base_commit"]):
        raise RuntimeError(f"{case_id}: source tree is not at the public base commit")
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tree,
        capture_output=True, text=True, check=False, timeout=30,
    )
    if dirty.returncode or dirty.stdout.strip():
        raise RuntimeError(f"{case_id}: source tree has modifications or untracked files")
    return tree


def _generation_instance(row: dict[str, Any]) -> Instance:
    _assert_public_value(row)
    issue = str(row["problem_statement"])
    hints = str(row.get("hints_text", "")).strip()
    if hints:
        issue = f"{issue.rstrip()}\n\nPublic maintainer hints:\n{hints}\n"
    return Instance(
        instance_id=str(row["instance_id"]),
        repository=str(_source_tree(row)),
        base_commit=str(row["base_commit"]),
        issue=issue,
        visible_tests=tuple(map(str, row.get("visible_tests", ()))),
        public_metadata={
            "repo": str(row["repo"]),
            "version": row.get("version"),
            "environment_setup_commit": row.get("environment_setup_commit"),
            "hints_text": hints,
            "generation_source": PUBLIC_PATH.name,
        },
    )


def _execution_image(row: dict[str, Any]) -> str | None:
    repo_owner, repo_name = str(row["repo"]).split("/", 1)
    case_suffix = str(row["instance_id"]).rsplit("-", 1)[-1]
    candidate = (
        f"swebench/sweb.eval.x86_64.{repo_owner}_1776_"
        f"{repo_name}-{case_suffix}:latest"
    )
    inspected = subprocess.run(
        ["docker", "image", "inspect", candidate],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if inspected.returncode != 0:
        return None
    provenance = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "--entrypoint", "/bin/bash", candidate, "-lc",
            "test -z \"$(git status --porcelain)\" && "
            "git cat-file -e \"$1^{commit}\"",
            "reachpatch-image-check", str(row["base_commit"]),
        ],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if provenance.returncode != 0:
        return None
    return candidate


def _sandbox_command(command: list[str], key_path: Path) -> list[str]:
    bubblewrap = shutil.which("bwrap")
    if not bubblewrap:
        raise RuntimeError("bubblewrap is required for public-only generation")
    for path in (EXPERIMENT_ROOT, RUN_ROOT, RESULT_ROOT):
        path.mkdir(parents=True, exist_ok=True)
    sandbox = [
        bubblewrap,
        "--die-with-parent",
        "--unshare-pid",
        # Keep worker-owned artifacts removable by the invoking user.  Without
        # an explicit uid/gid bubblewrap falls back to nobody inside the
        # namespace; a later retry then cannot archive its run directory.
        "--uid", str(os.getuid()),
        "--gid", str(os.getgid()),
        "--ro-bind", "/", "/",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        # The worker sees the public JSONL at its normal path, but no official
        # dataset, gold patch, hidden test patch, or prior harness outputs.
        "--tmpfs", str(CODE_ROOT / "dataset"),
        "--dir", str(DATASET_ROOT),
        "--ro-bind", str(PUBLIC_PATH), str(PUBLIC_PATH),
        # Persist only generation state. In particular, a worker cannot see a
        # future harness directory created beside these mounts.
        "--bind", str(RUN_ROOT), str(RUN_ROOT),
        "--bind", str(RESULT_ROOT), str(RESULT_ROOT),
        "--setenv", GENERATION_SANDBOX_ENV, "1",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--chdir", str(CODE_ROOT),
    ]
    # bubblewrap does not guarantee that the caller's ad-hoc environment is
    # visible inside the worker namespace.  Explicitly carry the ReachPatch
    # experiment/configuration variables into the public-only child.  In
    # particular this keeps REACHPATCH_MAX_CHALLENGE_ROUNDS and the selected
    # experiment root identical between the coordinator and case worker; a
    # silent fallback to the controller default would otherwise make the
    # sealed timing/transition evidence non-reproducible.
    for name, value in sorted(os.environ.items()):
        if name.startswith("REACHPATCH_"):
            sandbox.extend(("--setenv", name, value))
    if key_path.is_file() and key_path.stat().st_size:
        sandbox.extend(("--ro-bind", str(key_path), str(key_path)))
    docker_socket = Path("/run/docker.sock")
    if docker_socket.exists():
        sandbox.extend(("--dev-bind", str(docker_socket), str(docker_socket)))
    for experiment in sorted((CODE_ROOT / "experiments").iterdir()):
        if not experiment.is_dir():
            continue
        for private_name in ("results", "runs", "harness"):
            private_path = experiment / private_name
            if (
                private_path.is_dir()
                and private_path.resolve() not in {RUN_ROOT, RESULT_ROOT}
            ):
                sandbox.extend(("--tmpfs", str(private_path)))
    resolver = Path("/etc/resolv.conf").resolve()
    if resolver.is_file() and resolver.is_relative_to(Path("/run")):
        current = Path("/run")
        for part in resolver.parent.relative_to("/run").parts:
            current /= part
            sandbox.extend(("--dir", str(current)))
        sandbox.extend(("--ro-bind", str(resolver), str(resolver)))
    return [*sandbox, *command]


def _initial_checkpoint(run_root: Path) -> dict[str, Any]:
    values = []
    for path in sorted((run_root / "execution_checkpoints").glob("*/checkpoint.json")):
        raw = _read_json(path) or {}
        if raw.get("schema") != EXECUTION_SCHEMA_NAME:
            raise RuntimeError("incompatible execution checkpoint schema")
        checkpoint = raw.get("checkpoint", {})
        if checkpoint.get("status") == "P0" and checkpoint.get("revision") == 0:
            values.append({**checkpoint, "_directory": str(path.parent)})
    if len(values) != 1:
        raise RuntimeError(f"expected one execution P0, found {len(values)}")
    return values[0]


def _initial_checkpoint_diff(checkpoint: dict[str, Any]) -> str:
    if "cumulative_diff" not in checkpoint:
        raise RuntimeError("initial checkpoint has no cumulative diff")
    return str(checkpoint["cumulative_diff"])


def _transition_payloads(run_root: Path) -> list[dict[str, Any]]:
    values = []
    for path in sorted((run_root / "transitions").glob("*.json")):
        raw = _read_json(path) or {}
        if not raw.get("certificate_id"):
            raise RuntimeError("incompatible transition certificate schema")
        values.append(raw)
    return values


def _component_evidence(run_root: Path, terminal: dict[str, Any]) -> dict[str, Any]:
    return _execution_component_evidence(run_root, terminal)


def _execution_component_evidence(run_root: Path, terminal: dict[str, Any]) -> dict[str, Any]:
    summary = _read_json(run_root / "execution_summary.json") or {}
    graph = _read_json(run_root / "dynamic_graph.json") or {}
    nodes = graph.get("nodes", {})
    metrics = dict(summary.get("graph_metrics", {}))
    causal_keys = ("graph_localization_decision_count", "graph_generated_hypothesis_count",
                   "graph_generated_challenge_count", "graph_derived_validation_count")
    used = any(metrics.get(key, 0) for key in causal_keys)
    checkpoint_path = run_root / "execution_checkpoints" / str(terminal["checkpoint_id"]) / "checkpoint.json"
    checkpoint = _read_json(checkpoint_path) or {}
    if checkpoint.get("schema") != EXECUTION_SCHEMA_NAME:
        raise RuntimeError("selected checkpoint is missing or incompatible")
    if graph:
        from reachpatch.reach_avoid.dynamic_reach_avoid_graph import DynamicReachAvoidGraph
        from reachpatch.models.base import stable_id
        manifest = _read_json(run_root / "evidence_manifest.json") or {}
        actual_hash = DynamicReachAvoidGraph.from_dict(graph).digest()
        if (manifest.get("consistency") != "VERIFIED" or manifest.get("graph_hash") != actual_hash
            or summary.get("graph_hash") != actual_hash
            or manifest.get("checkpoint_id") != terminal["checkpoint_id"]
            or manifest.get("patch_hash") != checkpoint["checkpoint"].get("patch_hash")):
            raise RuntimeError("ARTIFACT_INCONSISTENT: evidence seal")
        for role in ("target", "preservation", "challenge"):
            results = checkpoint[role + "_results"]
            refs = {item["check_id"]: stable_id("check-observation", item["check_id"], item["status"], item["semantic_signature"])
                    for item in results}
            if refs != manifest.get("observation_references", {}).get(role):
                raise RuntimeError("ARTIFACT_INCONSISTENT: execution result references")
    targets = tuple(checkpoint.get("target_results", ()))
    preservation = tuple(checkpoint.get("preservation_results", ()))
    challenges = tuple(checkpoint.get("challenge_results", ()))
    transitions = _transition_payloads(run_root)
    decisions = Counter(item["decision"] for item in transitions)
    recovery = _read_json(run_root / "target_recovery.json") or {}
    return {
        "dynamic_reach_avoid_graph": {
            **metrics, "graph_hash": summary.get("graph_hash"),
            "node_count": len(nodes), "edge_count": len(graph.get("edges", {})),
            "frontier_count": len(graph.get("frontiers", ())),
            "status": "GRAPH_CAUSALLY_USED" if used else "GRAPH_PRESENT_BUT_CAUSALLY_UNUSED",
            "participated": used,
            "causal_benefit_established": False,
            "usage_level": "DECISION_RECORDED" if metrics.get("graph_recorded_action_count", 0) else
                           "CONTEXT_OR_DERIVATION_ONLY" if used else "UNUSED",
            "requirement_ids": sorted(key for key, node in nodes.items() if node.get("kind") == "REQUIREMENT"),
            "checkpoint_ids": sorted(key for key, node in nodes.items() if node.get("kind") == "CHECKPOINT"),
        },
        "validation": {
            "target_results": targets, "preservation_results": preservation,
            "challenge_results": challenges,
            "executed_challenge_ids": sorted(item["check_id"] for item in challenges),
            "stable_target_pass_count": sum(item.get("stable") and item.get("status") == "PASS" for item in targets),
            "participated": bool(targets or preservation or challenges),
        },
        "target_recovery": {
            "attempt_count": metrics.get("target_recovery_attempt_count", 0),
            "success": bool(metrics.get("target_recovery_success")),
            "exhausted_reasons": recovery.get("exhausted_reasons", ()),
            "participated": bool(metrics.get("target_recovery_attempt_count")),
        },
        "reach_avoid": {
            "transition_count": len(transitions), "decision_counts": dict(decisions),
            "backtrack_count": metrics.get("graph_backtrack_count", 0),
            "transitions": transitions, "participated": bool(transitions),
        },
        "case_budget": _read_json(run_root / "case_budget.json") or {},
    }


def _validate_component_evidence(case_id: str, evidence: dict[str, Any]) -> None:
    graph = evidence.get("dynamic_reach_avoid_graph", {})
    if not graph.get("graph_hash"):
        raise RuntimeError(f"{case_id}: missing unified graph hash")
    causal_keys = ("graph_localization_decision_count", "graph_generated_hypothesis_count",
                  "graph_generated_challenge_count", "graph_derived_validation_count")
    if graph.get("participated") and not any(graph.get(key, 0) for key in causal_keys):
        raise RuntimeError(f"{case_id}: graph participation has no causal-use evidence")
    # Evidence-limited patches are sealed honestly, never discarded and
    # regenerated merely because recovery found no target or adjacent oracle.
    if "validation" not in evidence or "case_budget" not in evidence:
        raise RuntimeError(f"{case_id}: missing validation or budget audit")


def _case_configuration(max_revisions: int) -> ReachAvoidConfig:
    enabled = lambda name: os.environ.get(name, "1").strip().casefold() not in {"0", "false", "no", "off"}
    fine_policy = {name: enabled("REACHPATCH_" + name.upper())
                   for name in ("suppress_same_version_actions", "reuse_source_evidence",
                                "compact_duplicate_payloads", "reuse_validation_results",
                                "recovery_on_gap", "falsification_on_question")
                   if "REACHPATCH_" + name.upper() in os.environ}
    return ReachAvoidConfig(
        **fine_policy,
        max_real_patch_revisions=max_revisions,
        execution_budget_seconds=float(os.environ.get("REACHPATCH_CASE_WALL_SECONDS", "3600")),
        max_case_model_calls=int(os.environ.get("REACHPATCH_CASE_MODEL_CALLS", "160")),
        max_case_tokens=int(os.environ.get("REACHPATCH_CASE_TOKENS", "1000000")),
        final_validation_reserve_seconds=float(os.environ.get("REACHPATCH_FINAL_VALIDATION_RESERVE", "30")),
        validation_workers=int(os.environ.get("REACHPATCH_VALIDATION_WORKERS", "2")),
        evidence_reuse_enabled=enabled("REACHPATCH_EVIDENCE_REUSE_ENABLED"),
        demand_driven_interaction_enabled=enabled("REACHPATCH_DEMAND_DRIVEN_INTERACTION_ENABLED"),
    )


def generate_case(case_id: str, key_path: Path, model: str, max_revisions: int) -> dict[str, Any]:
    if os.environ.get(GENERATION_SANDBOX_ENV) != "1":
        raise RuntimeError("case generation must run in the public-only sandbox")
    row = next(item for item in _public_rows() if str(item["instance_id"]) == case_id)
    execution_image = _execution_image(row)
    if execution_image:
        os.environ["REACHPATCH_EXECUTION_IMAGE"] = execution_image
        os.environ["REACHPATCH_EXECUTION_BASE_COMMIT"] = str(row["base_commit"])
    else:
        os.environ.pop("REACHPATCH_EXECUTION_IMAGE", None)
        os.environ.pop("REACHPATCH_EXECUTION_BASE_COMMIT", None)
    run_root = RUN_ROOT / case_id
    if run_root.exists() and not os.access(run_root, os.W_OK):
        # Do not let an unremovable artifact from a pre-uid sandbox block a
        # legitimate retry.  The result records the suffixed run root and the
        # stale directory remains available for audit.
        attempt = os.environ.get("REACHPATCH_RA51_ATTEMPT", "1")
        run_root = RUN_ROOT / f"{case_id}.attempt-{attempt}-{time.time_ns()}"
        while run_root.exists():
            run_root = RUN_ROOT / f"{case_id}.attempt-{attempt}-{time.time_ns()}"
    result_path = RESULT_ROOT / f"{case_id}.json"
    if run_root.exists() or result_path.exists():
        raise FileExistsError(f"refusing to overwrite generation artifact for {case_id}")
    # Trace probes must live beside the actual (possibly suffixed) run root;
    # using a stale pre-uid case directory can make baseline execution fail
    # with PermissionError before a terminal patch is written.
    os.environ["REACHPATCH_TRACE_TEMP_ROOT"] = str(run_root / "trace_tmp")
    key = (
        key_path.read_text(encoding="utf-8").strip()
        if key_path.is_file() else
        os.environ.get("DEEPSEEK_API_KEY", "").strip()
    )
    if not key:
        raise ValueError("DeepSeek API key is empty")
    transport = DeepSeekHTTPTransport(
        key,
        model=model,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    controller = ReachAvoidController(
        RepairPlayer(DeepSeekAgent(transport, DeepSeekConfig.from_environment())),
        _case_configuration(max_revisions),
    )
    started = time.monotonic()
    terminal = controller.run_case(_generation_instance(row), run_root=run_root).to_dict()
    if terminal["status"] in {"GENERATOR_BLOCKED_EXTERNAL", "MECHANICAL_BLOCKED"}:
        errors_path = run_root / "controller_errors.jsonl"
        detail = errors_path.read_text(encoding="utf-8")[-8000:] if errors_path.is_file() else ""
        raise RuntimeError(f"{case_id}: {terminal['status']}: {detail}")
    initial = _initial_checkpoint(run_root)
    p0_path = run_root / "p0.patch"
    # Only the full base-to-checkpoint diff belongs in a sealed prediction.
    try:
        initial_diff = _initial_checkpoint_diff(initial)
    except RuntimeError as exc:
        raise RuntimeError(f"{case_id}: {exc}") from exc
    p0_path.write_text(initial_diff, encoding="utf-8")
    final_path = Path(str(terminal["output_path"])).resolve()
    if not p0_path.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"{case_id}: initial p0 is empty")
    if not final_path.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"{case_id}: final patch is empty")
    component_evidence = _component_evidence(run_root, terminal)
    _validate_component_evidence(case_id, component_evidence)
    result = {
        "schema": SCHEMA,
        "instance_id": case_id,
        "status": terminal["status"],
        "run_root": str(run_root),
        "p0_patch_path": str(p0_path),
        "p0_patch_hash": str(initial["patch_hash"]),
        "p0_patch_sha256": _sha256(p0_path),
        "final_patch_path": str(final_path),
        "final_patch_hash": str(terminal["patch_hash"]),
        "final_patch_sha256": _sha256(final_path),
        "final_checkpoint_id": terminal["checkpoint_id"],
        "duration_seconds": time.monotonic() - started,
        "implementation_hash": _implementation_hash(),
        "case_configuration": asdict(_case_configuration(max_revisions)),
        "execution_backend": (
            {"kind": "DEPENDENCY_IMAGE", "image": execution_image}
            if execution_image else {"kind": "HOST"}
        ),
        "public_dataset_sha256": _sha256(PUBLIC_PATH),
        "component_evidence": component_evidence,
        "completed_at": utc_now(),
    }
    _write_json(result_path, result)
    print(canonical_json({"instance_id": case_id, "status": terminal["status"]}), flush=True)
    return result


def _generation_result_valid(result: dict[str, Any], row: dict[str, Any]) -> bool:
    if result.get("schema") != SCHEMA or result.get("instance_id") != row.get("instance_id"):
        return False
    if result.get("implementation_hash") != _implementation_hash():
        return False
    if result.get("public_dataset_sha256") != _sha256(PUBLIC_PATH):
        return False
    for prefix in ("p0", "final"):
        path = Path(str(result.get(f"{prefix}_patch_path", "")))
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            return False
        if _sha256(path) != result.get(f"{prefix}_patch_sha256"):
            return False
    return True


def _generation_preflight(rows: list[dict[str, Any]], key_path: Path) -> None:
    if not (key_path.is_file() and key_path.stat().st_size) and not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise FileNotFoundError("DeepSeek key path is missing or empty")
    for row in rows:
        _source_tree(row)
    if not shutil.which("bwrap"):
        raise RuntimeError("bubblewrap is unavailable")


def generate(key_path: Path, model: str, max_revisions: int, only: set[str]) -> dict[str, Any]:
    rows = _public_rows()
    selected = _cohort_rows(rows, only)
    _generation_preflight(selected, key_path)
    EXPERIMENT_ROOT.mkdir(parents=True, exist_ok=True)
    case_retries = max(1, int(os.environ.get("REACHPATCH_CASE_RETRIES", "3")))
    case_workers = max(1, int(os.environ.get("REACHPATCH_CASE_WORKERS", "1")))
    manifest = _read_json(GENERATION_MANIFEST)
    diagnostic = os.environ.get("REACHPATCH_DIAGNOSTIC10") == "1"
    expected = {
        "schema": SCHEMA,
        "public_dataset_sha256": _sha256(PUBLIC_PATH),
        "implementation_hash": _implementation_hash(),
        "model": model,
        "max_revisions": max_revisions,
        "case_configuration": asdict(_case_configuration(max_revisions)),
        "case_retries": case_retries,
        "case_workers": case_workers,
        "cohort_instance_ids": [str(row["instance_id"]) for row in selected],
        "cohort_hash": content_hash([str(row["instance_id"]) for row in selected]),
        **({"diagnostic_instance_ids": sorted(only)} if diagnostic else {}),
    }
    if manifest is None:
        manifest = {**expected, "started_at": utc_now()}
        _write_json(GENERATION_MANIFEST, manifest)
    elif any(manifest.get(key) != value for key, value in expected.items()):
        raise RuntimeError("existing generation manifest belongs to a different method run")
    def run_selected(index: int, row: dict[str, Any]) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
        case_id = str(row["instance_id"])
        tree = _source_tree(row)
        tree_before = _git_worktree_digest(tree)
        result_path = RESULT_ROOT / f"{case_id}.json"
        existing = _read_json(result_path)
        if existing and _generation_result_valid(existing, row):
            print(canonical_json({"instance_id": case_id, "status": "REUSED", "index": index}), flush=True)
            return case_id, existing, None
        if existing or (RUN_ROOT / case_id).exists():
            _archive_failed_attempt(case_id)
        command = [
            sys.executable, str(Path(__file__).resolve()), "case",
            "--instance-id", case_id,
            "--key-path", str(key_path.resolve()),
            "--model", model,
            "--max-revisions", str(max_revisions),
        ]
        completed = None
        for retry in range(1, case_retries + 1):
            if retry > 1:
                _archive_failed_attempt(case_id)
                print(canonical_json({
                    "instance_id": case_id,
                    "status": "RETRY",
                    "attempt": retry,
                    "index": index,
                }), flush=True)
            child_environment = os.environ.copy()
            child_environment["REACHPATCH_RA51_ATTEMPT"] = str(retry)
            completed = subprocess.run(
                _sandbox_command(command, key_path.resolve()), cwd=CODE_ROOT,
                env=child_environment, text=True, capture_output=True, check=False,
            )
            if _git_worktree_digest(tree) != tree_before:
                raise RuntimeError(f"{case_id}: generation mutated the immutable base tree")
            result = _read_json(result_path)
            if completed.returncode == 0 and result and _generation_result_valid(result, row):
                print(canonical_json({
                    "instance_id": case_id,
                    "status": result["status"],
                    "attempt": retry,
                    "index": index,
                }), flush=True)
                return case_id, result, None
            if result_path.exists() and not _generation_result_valid(result or {}, row):
                result_path.unlink()
        failure = {
            "instance_id": case_id,
            "attempts": case_retries,
            "return_code": completed.returncode if completed else None,
            "stdout_tail": completed.stdout[-4000:] if completed else "",
            "stderr_tail": completed.stderr[-8000:] if completed else "",
        }
        print(canonical_json({
            "instance_id": case_id,
            "status": "ERROR",
            "attempts": case_retries,
            "index": index,
        }), flush=True)
        return case_id, None, failure

    current_results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    if case_workers == 1:
        completed_rows = (
            run_selected(index, row) for index, row in enumerate(selected, 1)
        )
        for case_id, result, failure in completed_rows:
            if result is not None:
                current_results[case_id] = result
            if failure is not None:
                failures.append(failure)
    else:
        with ThreadPoolExecutor(max_workers=min(case_workers, len(selected))) as pool:
            futures = {
                pool.submit(run_selected, index, row): str(row["instance_id"])
                for index, row in enumerate(selected, 1)
            }
            for future in as_completed(futures):
                case_id, result, failure = future.result()
                if result is not None:
                    current_results[case_id] = result
                if failure is not None:
                    failures.append(failure)
    failures.sort(key=lambda item: str(item["instance_id"]))
    all_results = {}
    for row in rows:
        path = RESULT_ROOT / f"{row['instance_id']}.json"
        result = _read_json(path)
        if result and _generation_result_valid(result, row):
            all_results[str(row["instance_id"])] = result
    sealed_results = {
        str(row["instance_id"]): all_results[str(row["instance_id"])]
        for row in selected if str(row["instance_id"]) in all_results
    }
    expected_case_count = len(selected)
    summary = {
        **expected,
        "case_count": expected_case_count,
        "public_case_count": len(rows),
        "selected_count": len(selected),
        "sealed_case_count": len(sealed_results),
        "current_result_count": len(current_results),
        "failures": failures,
        "status_counts": dict(sorted(Counter(
            str(item["status"]) for item in all_results.values()
        ).items())),
        "results": [sealed_results[key] for key in sorted(sealed_results)],
        "updated_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "generation_summary.json", summary)
    build_efficiency_report()
    if failures:
        failed_ids = ", ".join(str(item["instance_id"]) for item in failures)
        raise RuntimeError(
            f"generation incomplete: {len(failures)} case(s) produced no valid patch: {failed_ids}"
        )
    if len(sealed_results) == expected_case_count and not failures:
        sealed = {
            "schema": SCHEMA,
            "sealed_at": utc_now(),
            "public_dataset_sha256": _sha256(PUBLIC_PATH),
            "implementation_hash": _implementation_hash(),
            "case_count": expected_case_count,
            "instance_ids": sorted(sealed_results),
            "p0_predictions_sha256": _seal_predictions(sealed_results, "p0"),
            "final_predictions_sha256": _seal_predictions(sealed_results, "final"),
            "results_sha256": content_hash(sealed_results),
        }
        _write_json(SEALED_MANIFEST, sealed)
        summary["sealed_generation"] = sealed
        _write_json(EXPERIMENT_ROOT / "generation_summary.json", summary)
    return summary


def _seal_predictions(results: dict[str, dict[str, Any]], kind: str) -> str:
    rows = []
    for case_id, result in sorted(results.items()):
        patch_path = Path(result[f"{kind}_patch_path"])
        rows.append({
            "instance_id": case_id,
            "model_name_or_path": f"reachpatch-{kind}",
            "model_patch": patch_path.read_text(encoding="utf-8"),
        })
    path = HARNESS_ROOT / f"sealed_{kind}_predictions.jsonl"
    _write_jsonl(path, rows)
    return _sha256(path)


def _official_rows_after_seal() -> list[dict[str, Any]]:
    sealed = _read_json(SEALED_MANIFEST)
    diagnostic = os.environ.get("REACHPATCH_DIAGNOSTIC10") == "1"
    expected_count = int((sealed or {}).get("case_count", 0))
    if not sealed or expected_count < 1:
        raise RuntimeError(
            "all cohort generation results must be sealed before official data is read"
        )
    source = DIAGNOSTIC_OFFICIAL_PATH if diagnostic else OFFICIAL_PATH
    rows = _read_jsonl(source)
    if diagnostic and len(rows) != expected_count:
        raise RuntimeError(
            f"expected {expected_count} official instances, found {len(rows)}"
        )
    public_ids = {str(item) for item in sealed.get("instance_ids", ())}
    official_ids = {str(row["instance_id"]) for row in rows}
    if not public_ids or (official_ids != public_ids if diagnostic else not public_ids <= official_ids):
        raise RuntimeError("official/public instance sets differ")
    selected = [row for row in rows if str(row["instance_id"]) in public_ids]
    if len(selected) != expected_count:
        raise RuntimeError("sealed cohort is not fully represented in official data")
    return selected


def _harness_report_path(stage: str, run_id: str) -> Path:
    candidates = list((HARNESS_ROOT / stage).glob(f"*.{run_id}.json"))
    if len(candidates) != 1:
        raise RuntimeError(f"expected one official {stage} report, found {len(candidates)}")
    return candidates[0]


def _run_harness_stage(
    stage: str, workers: int, timeout: int, official_path: Path,
) -> dict[str, Any]:
    predictions = HARNESS_ROOT / f"sealed_{stage}_predictions.jsonl"
    sealed = _read_json(SEALED_MANIFEST) or {}
    expected_sha = sealed.get(f"{stage}_predictions_sha256")
    if not predictions.is_file() or _sha256(predictions) != expected_sha:
        raise RuntimeError(f"sealed {stage} predictions hash mismatch")
    stage_root = HARNESS_ROOT / stage
    stage_root.mkdir(parents=True, exist_ok=True)
    run_id = f"reachavoid51-{stage}-{expected_sha[:12]}"
    command = [
        sys.executable, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", str(official_path),
        "--split", "test",
        "--predictions_path", str(predictions),
        "--max_workers", str(workers),
        "--timeout", str(timeout),
        "--run_id", run_id,
        "--namespace", "swebench",
        "--cache_level", "instance",
        "--clean", "False",
        "--report_dir", str(stage_root),
    ]
    log_path = stage_root / "harness.log"
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command, cwd=stage_root, stdout=log, stderr=subprocess.STDOUT,
            text=True, check=False,
        )
    if completed.returncode:
        raise RuntimeError(f"official {stage} harness failed; inspect {log_path}")
    report_path = _harness_report_path(stage, run_id)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "stage": stage,
        "run_id": run_id,
        "predictions_sha256": expected_sha,
        "report_path": str(report_path),
        "report_sha256": _sha256(report_path),
        "resolved_ids": sorted(map(str, report.get("resolved_ids", ()))),
        "unresolved_ids": sorted(map(str, report.get("unresolved_ids", ()))),
        "error_ids": sorted(map(str, report.get("error_ids", ()))),
        "submitted_instances": int(report.get("submitted_instances", 0)),
        "completed_instances": int(report.get("completed_instances", 0)),
        "resolved_instances": int(report.get("resolved_instances", 0)),
        "log_path": str(log_path),
        "completed_at": utc_now(),
    }


def harness(workers: int, timeout: int) -> dict[str, Any]:
    official_rows = _official_rows_after_seal()
    official_path = HARNESS_ROOT / "sealed_official_cohort.jsonl"
    _write_jsonl(official_path, official_rows)
    p0 = _run_harness_stage("p0", workers, timeout, official_path)
    final = _run_harness_stage("final", workers, timeout, official_path)
    summary = {"schema": SCHEMA, "p0": p0, "final": final, "completed_at": utc_now()}
    _write_json(HARNESS_ROOT / "harness_summary.json", summary)
    build_efficiency_report()
    build_effectiveness_report()
    return summary


def _case_efficiency(result: dict[str, Any]) -> dict[str, Any]:
    run_root = Path(str(result["run_root"]))
    efficiency = _read_json(run_root / "token_efficiency.json") or {}
    budget = dict(efficiency.get("budget", {}))
    usage = dict(budget.get("usage", {}))
    model_events = [
        item for item in budget.get("events", ())
        if item.get("kind") == "MODEL"
    ]
    state = _read_json(run_root / "execution_state.json") or {}
    attempts = (
        state.get("state", {}).get("generator_session", {}).get("attempt_history", ())
    )
    attempt_counts = Counter(str(item.get("result_kind", "UNKNOWN")) for item in attempts)
    patch_fingerprints = [
        (str(item.get("source_patch_hash", "")), str(item.get("incremental_patch_hash", "")))
        for item in attempts if item.get("incremental_patch_hash")
    ]
    duplicate_patch_attempts = len(patch_fingerprints) - len(set(patch_fingerprints))
    graph_events = dict(efficiency.get("events", {}))
    exact_request_duplicates = sum(bool(item.get("exact_request_duplicate")) for item in model_events)
    duplicate_tool_actions = sum(int(item.get("duplicate_tool_action_count", 0)) for item in model_events)
    novel_action_calls = sum(int(item.get("novel_tool_action_count", 0)) > 0 for item in model_events)
    no_action_calls = sum(
        int(item.get("novel_tool_action_count", 0)) == 0
        and not bool(item.get("has_text_response"))
        for item in model_events
    )
    recovery_model_calls = sum(item.get("stage") == "TARGET_RECOVERY" for item in model_events)
    original_bytes = int(efficiency.get("context_original_bytes", 0))
    elided_bytes = int(efficiency.get("context_elided_bytes", 0))
    return {
        "instance_id": str(result["instance_id"]),
        "terminal_status": str(result["status"]),
        "duration_seconds": float(result.get("duration_seconds", 0.0)),
        "model_calls": int(budget.get("model_calls", len(model_events))),
        "total_tokens": int(usage.get("total_tokens", budget.get("tokens", 0)) or 0),
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "uncached_prompt_tokens": int(usage.get("prompt_cache_miss_tokens", 0) or 0),
        "cached_prompt_tokens": int(usage.get("prompt_cache_hit_tokens", 0) or 0),
        "execution_seconds": float(budget.get("execution_seconds", 0.0)),
        "novel_action_model_calls": novel_action_calls,
        "no_action_model_calls": no_action_calls,
        "exact_duplicate_model_requests": exact_request_duplicates,
        "duplicate_tool_actions": duplicate_tool_actions,
        "target_recovery_model_calls": recovery_model_calls,
        "duplicate_actions_prevented": int(graph_events.get("DUPLICATE_ACTION_PREVENTED", 0)),
        "source_reads_reused": int(graph_events.get("SOURCE_READ_REUSED", 0)),
        "validation_cache_hits": int(graph_events.get("VALIDATION_CACHE_HIT", 0)),
        "context_original_bytes": original_bytes,
        "context_sent_bytes": int(efficiency.get("context_sent_bytes", 0)),
        "context_elided_bytes": elided_bytes,
        "context_elision_rate": (elided_bytes / original_bytes if original_bytes else 0.0),
        "generator_attempt_count": len(attempts),
        "empty_patch_attempts": int(attempt_counts.get("NO_NEW_DIFF", 0)),
        "generator_error_attempts": int(attempt_counts.get("GENERATOR_ERROR", 0)),
        "duplicate_patch_attempts": duplicate_patch_attempts,
        "target_recovery_success": bool(
            result.get("component_evidence", {}).get("target_recovery", {}).get("success")
        ),
        "entered_repair_loop": bool(
            result.get("component_evidence", {}).get("dynamic_reach_avoid_graph", {})
            .get("entered_repair_loop", 0)
        ),
        "final_differs_from_p0": result.get("p0_patch_hash") != result.get("final_patch_hash"),
    }


def build_efficiency_report() -> dict[str, Any]:
    generation = _read_json(EXPERIMENT_ROOT / "generation_summary.json") or {}
    rows = [_case_efficiency(item) for item in generation.get("results", ())]
    harness_summary = _read_json(HARNESS_ROOT / "harness_summary.json") or {}
    resolved_ids = set(harness_summary.get("final", {}).get("resolved_ids", ()))
    for row in rows:
        row["official_final_resolved"] = (
            row["instance_id"] in resolved_ids if harness_summary else None
        )
    totals = {
        key: sum(float(row[key]) for row in rows)
        for key in (
            "duration_seconds", "model_calls", "total_tokens", "prompt_tokens",
            "completion_tokens", "uncached_prompt_tokens", "cached_prompt_tokens",
            "execution_seconds", "novel_action_model_calls", "no_action_model_calls",
            "exact_duplicate_model_requests", "duplicate_tool_actions",
            "target_recovery_model_calls", "duplicate_actions_prevented",
            "source_reads_reused", "validation_cache_hits", "context_original_bytes",
            "context_sent_bytes", "context_elided_bytes", "generator_attempt_count",
            "empty_patch_attempts", "generator_error_attempts", "duplicate_patch_attempts",
        )
    }
    model_calls = totals.get("model_calls", 0.0)
    resolved_count = len(resolved_ids) if harness_summary else None
    report = {
        "schema": "reachpatch-evidence-efficiency-experiment-v1",
        "case_count": len(rows),
        "implementation_hash": generation.get("implementation_hash"),
        "cohort_hash": generation.get("cohort_hash"),
        "metric_definitions": {
            "novel_action_model_call_rate": "fraction of provider calls returning at least one previously unseen stage/tool/argument action",
            "no_action_model_call_rate": "fraction of provider calls returning neither a tool action nor nonempty text",
            "uncached_prompt_tokens": "provider-reported prompt_cache_miss_tokens",
            "duplicate_patch_attempt": "same parent patch hash and incremental patch hash attempted more than once",
            "token_reduction_claim": "requires a matched baseline cohort; this report alone measures consumption, not savings",
        },
        "totals": totals,
        "means": {key: value / len(rows) for key, value in totals.items()} if rows else {},
        "novel_action_model_call_rate": (
            totals.get("novel_action_model_calls", 0.0) / model_calls if model_calls else 0.0
        ),
        "no_action_model_call_rate": (
            totals.get("no_action_model_calls", 0.0) / model_calls if model_calls else 0.0
        ),
        "context_elision_rate": (
            totals.get("context_elided_bytes", 0.0) / totals.get("context_original_bytes", 1.0)
            if totals.get("context_original_bytes", 0.0) else 0.0
        ),
        "official_final_resolved_count": resolved_count,
        "calls_per_resolved_case": (
            model_calls / resolved_count if resolved_count else None
        ),
        "tokens_per_resolved_case": (
            totals.get("total_tokens", 0.0) / resolved_count if resolved_count else None
        ),
        "rows": rows,
        "completed_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "evidence_efficiency_report.json", report)
    return report


def build_effectiveness_report() -> dict[str, Any]:
    generation = _read_json(EXPERIMENT_ROOT / "generation_summary.json") or {}
    harness_summary = _read_json(HARNESS_ROOT / "harness_summary.json")
    if not harness_summary:
        raise RuntimeError("official p0/final harness summary is unavailable")
    p0_resolved = set(harness_summary["p0"]["resolved_ids"])
    final_resolved = set(harness_summary["final"]["resolved_ids"])
    comparisons = [
        PatchOutcomeComparison(
            instance_id=str(item["instance_id"]),
            initial_resolved=str(item["instance_id"]) in p0_resolved,
            final_resolved=str(item["instance_id"]) in final_resolved,
        )
        for item in generation.get("results", ())
    ]
    outcome_summary = summarize_patch_outcomes(comparisons)
    outcome_by_id = {item["instance_id"]: item for item in outcome_summary["outcomes"]}
    rows = []
    component_names = (
        "dynamic_reach_avoid_graph", "validation", "target_recovery", "reach_avoid",
    )
    component_totals = {
        name: {"participated": 0, "effective_on_improved_case": 0, "present_but_case_regressed": 0}
        for name in component_names
    }
    for item in generation.get("results", ()):
        case_id = str(item["instance_id"])
        outcome = outcome_by_id[case_id]
        components = {}
        for name in component_names:
            evidence = dict(item.get("component_evidence", {}).get(name, {}))
            participated = bool(evidence.get("participated"))
            patch_changed = item["p0_patch_hash"] != item["final_patch_hash"]
            effective = participated and patch_changed and outcome["outcome"] == "IMPROVED"
            regressed = participated and outcome["outcome"] == "REGRESSED"
            components[name] = {
                "participated": participated,
                "effective_on_improved_case": effective,
                "present_but_case_regressed": regressed,
                "evidence": evidence,
            }
            component_totals[name]["participated"] += int(participated)
            component_totals[name]["effective_on_improved_case"] += int(effective)
            component_totals[name]["present_but_case_regressed"] += int(regressed)
        rows.append({
            "instance_id": case_id,
            "p0_resolved": outcome["initial_resolved"],
            "final_resolved": outcome["final_resolved"],
            "outcome": outcome["outcome"],
            "p0_patch_hash": item["p0_patch_hash"],
            "final_patch_hash": item["final_patch_hash"],
            "components": components,
        })
    report = {
        "schema": SCHEMA,
        "outcome_summary": outcome_summary,
        "component_totals": component_totals,
        "attribution_note": (
            "effective_on_improved_case means the component has persisted causal participation "
            "evidence and the sealed final patch changed an unresolved p0 into a resolved case. "
            "It is evidence-backed attribution, not an independent ablation proof."
        ),
        "rows": rows,
        "completed_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "component_effectiveness.json", report)
    markdown = [
        "# Reach-Avoid 51 Component Effectiveness",
        "",
        "| Instance | p0 | final | outcome | Unified graph | Validation | Recovery | Search |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        markdown.append(
            f"| `{row['instance_id']}` | {int(row['p0_resolved'])} | {int(row['final_resolved'])} | "
            f"{row['outcome']} | "
            + " | ".join(
                "effective" if row["components"][name]["effective_on_improved_case"]
                else ("participated" if row["components"][name]["participated"] else "not-used")
                for name in component_names
            )
            + " |"
        )
    (EXPERIMENT_ROOT / "component_effectiveness.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--key-path", required=True)
    generate_parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    generate_parser.add_argument("--max-revisions", type=int, default=8)
    generate_parser.add_argument("--only", action="append", default=[])
    case_parser = subparsers.add_parser("case")
    case_parser.add_argument("--instance-id", required=True)
    case_parser.add_argument("--key-path", required=True)
    case_parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    case_parser.add_argument("--max-revisions", type=int, default=8)
    harness_parser = subparsers.add_parser("harness")
    harness_parser.add_argument("--workers", type=int, default=2)
    harness_parser.add_argument("--timeout", type=int, default=1800)
    subparsers.add_parser("report")
    args = parser.parse_args()
    try:
        if args.command == "case":
            generate_case(args.instance_id, Path(args.key_path).resolve(), args.model, args.max_revisions)
            return 0
        if args.command == "generate":
            result = generate(
                Path(args.key_path).resolve(), args.model, args.max_revisions,
                set(args.only),
            )
        elif args.command == "harness":
            result = harness(args.workers, args.timeout)
        else:
            result = build_effectiveness_report()
        print(canonical_json({
            "command": args.command,
            "status": "COMPLETE",
            "summary": result.get("status_counts", result.get("outcome_summary", {})),
        }))
        return 0
    except Exception as exc:
        print(canonical_json({
            "command": args.command,
            "status": "ERROR",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
