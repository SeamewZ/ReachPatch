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
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from reachpatch.models.base import SCHEMA_VERSION, canonical_json, content_hash, utc_now
from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidConfig, ReachAvoidController
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.repair import DeepSeekAgent, DeepSeekConfig, DeepSeekHTTPTransport
from reachpatch.reporting import PatchOutcomeComparison, summarize_patch_outcomes


DATASET_ROOT = CODE_ROOT / "dataset" / "patchpsro_55_unique51"
PUBLIC_PATH = DATASET_ROOT / "generation_public_instances.jsonl"
OFFICIAL_PATH = DATASET_ROOT / "official_instances.jsonl"
SOURCE_TREE_ROOT = CODE_ROOT / "experiments" / "new_swelite_51" / "case_trees"
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
        source_run.replace(destination / "run")
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
        "--ro-bind", str(key_path), str(key_path),
        "--setenv", GENERATION_SANDBOX_ENV, "1",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--chdir", str(CODE_ROOT),
    ]
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


def _checkpoint_payloads(run_root: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted((run_root / "checkpoint_store").glob("*/checkpoint.json")):
        raw = _read_json(path)
        if raw and raw.get("schema") == SCHEMA_VERSION:
            result.append({**raw["checkpoint"], "_directory": str(path.parent)})
    return result


def _initial_checkpoint(run_root: Path) -> dict[str, Any]:
    values = [
        item for item in _checkpoint_payloads(run_root)
        if item.get("status") == "INITIAL_WORKING" and int(item.get("revision", -1)) == 0
    ]
    if len(values) != 1:
        raise RuntimeError(f"expected one INITIAL_WORKING checkpoint, found {len(values)}")
    return values[0]


def _transition_payloads(run_root: Path) -> list[dict[str, Any]]:
    values = []
    for path in sorted((run_root / "transitions").glob("*.json")):
        raw = _read_json(path)
        if raw and raw.get("schema") == SCHEMA_VERSION:
            values.append(raw)
    return values


def _terminal_graphs(run_root: Path, checkpoint_id: str) -> dict[str, Any]:
    root = run_root / "checkpoint_store" / checkpoint_id
    return {
        name: json.loads((root / f"{name}_graph.json").read_text(encoding="utf-8"))
        for name in ("requirement", "program", "binding", "challenge")
    }


def _objective_evidence(run_root: Path) -> list[dict[str, Any]]:
    objectives: dict[str, dict[str, Any]] = {}
    for path in sorted((run_root / "checkpoint_store").glob("*/runtime_state.json")):
        raw = _read_json(path) or {}
        objective = raw.get("current_repair_objective")
        if isinstance(objective, dict) and objective.get("objective_kind") != "INITIAL_PATCH":
            objectives[str(objective.get("objective_id"))] = objective
    return list(objectives.values())


def _component_evidence(run_root: Path, terminal: dict[str, Any]) -> dict[str, Any]:
    transitions = _transition_payloads(run_root)
    certificates = [item["certificate"] for item in transitions]
    objectives = _objective_evidence(run_root)
    graphs = _terminal_graphs(run_root, str(terminal["checkpoint_id"]))
    requirement = graphs["requirement"]
    program = graphs["program"]
    binding = graphs["binding"]
    challenge = graphs["challenge"]
    executions = {
        execution["paired_bundle_id"]: execution
        for transition in transitions
        for execution in transition.get("executions", ())
    }
    checkpoint_observations = {}
    for path in (run_root / "checkpoint_store").glob("*/observations.json"):
        raw = _read_json(path) or {}
        checkpoint_observations.update(raw.get("by_challenge", {}))
    checkpoint_counterexamples = {}
    for path in (run_root / "checkpoint_store").glob("*/counterexamples.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for packet in raw if isinstance(raw, list) else ():
            if isinstance(packet, dict) and packet.get("counterexample_id"):
                checkpoint_counterexamples[str(packet["counterexample_id"])] = packet
    objective_cut_ids = sorted({
        str(cut.get("cut_id"))
        for objective in objectives for cut in objective.get("causal_cuts", ())
        if isinstance(cut, dict) and cut.get("cut_id")
    })
    objective_binding_ids = sorted({
        str(unit.get("binding_id"))
        for objective in objectives for unit in objective.get("bindings", ())
        if isinstance(unit, dict) and unit.get("binding_id")
    })
    objective_requirement_ids = sorted({
        str(leaf.get("requirement_id"))
        for objective in objectives
        for leaf in (
            objective.get("related_requirements", ())
            + objective.get("preservation_requirements", ())
        )
        if isinstance(leaf, dict) and leaf.get("requirement_id")
    })
    improved_requirements = sorted({
        requirement_id
        for certificate in certificates
        for requirement_id in certificate.get("requirements_improved", ())
    })
    confirmed_bindings = sorted({
        binding_id
        for certificate in certificates
        for binding_id in certificate.get("bindings_confirmed", ())
    })
    selected_challenges = sorted({
        challenge_id
        for certificate in certificates
        for challenge_id in certificate.get("selected_challenge_ids", ())
    } | set(checkpoint_observations))
    executed_challenges = sorted({
        challenge_id
        for certificate in certificates
        for challenge_id in certificate.get("executed_challenge_ids", ())
    } | set(checkpoint_observations))
    opened_counterexamples = sorted({
        item
        for certificate in certificates
        for item in certificate.get("counterexamples_opened", ())
    } | set(checkpoint_counterexamples))
    closed_counterexamples = sorted({
        item
        for certificate in certificates
        for item in certificate.get("counterexamples_closed", ())
    })
    decisions = Counter(str(item.get("decision")) for item in certificates)
    dynamic_edges = sum(
        bool(edge.get("dynamic_confirmed"))
        for edge in program.get("edges", {}).values()
    )
    confirmed_terminal_bindings = [
        binding_id for binding_id, unit in binding.get("units", {}).items()
        if unit.get("status") not in {"UNBOUND", "STATIC_ACTIONABLE", "ORACLE_UNAVAILABLE", "ENVIRONMENT_BLOCKED"}
    ]
    terminal_challenge_counts = Counter(
        str(cell.get("terminal_status"))
        for cell in challenge.get("cells", {}).values()
    )
    execution_bundle_ids = set(executions) | {
        str(execution["paired_bundle_id"])
        for execution in checkpoint_observations.values()
        if isinstance(execution, dict) and execution.get("paired_bundle_id")
    }
    impact = program.get("impact_cone") or {}
    impact_risk_ids = {
        str(risk_id)
        for field in (
            "direct_caller_ids", "return_consumer_ids",
            "exception_handler_ids", "state_reader_ids",
            "reverse_dispatch_ids", "rendering_consumer_ids",
            "public_check_ids",
        )
        for risk_id in impact.get(field, ())
    }
    return {
        "requirement_graph": {
            "leaf_count": len(requirement.get("leaves", {})),
            "partition_count": len(requirement.get("challenge_partitions", {})),
            "objective_requirement_ids": objective_requirement_ids,
            "requirements_improved": improved_requirements,
            "participated": bool(objective_requirement_ids or improved_requirements),
        },
        "program_graph": {
            "node_count": len(program.get("nodes", {})),
            "edge_count": len(program.get("edges", {})),
            "path_class_count": len(program.get("path_classes", {})),
            "dynamic_edge_count": dynamic_edges,
            "causal_cut_count": len(program.get("causal_cuts", {})),
            "objective_causal_cut_ids": objective_cut_ids,
            "impact_risk_count": len(impact_risk_ids),
            "participated": bool(objective_cut_ids or dynamic_edges),
        },
        "binding_graph": {
            "unit_count": len(binding.get("units", {})),
            "gap_count": len(binding.get("gaps", ())),
            "objective_binding_ids": objective_binding_ids,
            "transition_confirmed_binding_ids": confirmed_bindings,
            "terminal_execution_confirmed_binding_ids": sorted(confirmed_terminal_bindings),
            "participated": bool(objective_binding_ids or confirmed_bindings or confirmed_terminal_bindings),
        },
        "challenge_graph": {
            "cell_count": len(challenge.get("cells", {})),
            "selected_challenge_ids": selected_challenges,
            "executed_challenge_ids": executed_challenges,
            "execution_bundle_count": len(execution_bundle_ids),
            "counterexamples_opened": opened_counterexamples,
            "counterexamples_closed": closed_counterexamples,
            "terminal_status_counts": dict(sorted(terminal_challenge_counts.items())),
            "participated": bool(executed_challenges),
        },
        "reach_avoid": {
            "transition_count": len(certificates),
            "decision_counts": dict(sorted(decisions.items())),
            "strict_progress_count": sum(bool(item.get("progress", {}).get("strict_progress")) for item in certificates),
            "causal_progress_count": sum(bool(item.get("progress", {}).get("causal_progress")) for item in certificates),
            "rollback_count": decisions.get("ROLLBACK", 0),
            "provisional_count": decisions.get("KEEP_PROVISIONAL", 0),
            "commit_count": decisions.get("COMMIT_WORKING", 0),
            "participated": bool(certificates or executed_challenges),
        },
    }


def _validate_component_evidence(case_id: str, evidence: dict[str, Any]) -> None:
    requirement_count = int(evidence["requirement_graph"]["leaf_count"])
    challenge_count = int(evidence["challenge_graph"]["cell_count"])
    if requirement_count and not challenge_count:
        raise RuntimeError(
            f"{case_id}: generated patch has requirements but no Challenge cells"
        )
    executed_count = len(evidence["challenge_graph"]["executed_challenge_ids"])
    if challenge_count and not executed_count:
        raise RuntimeError(
            f"{case_id}: final checkpoint leaves every Challenge cell unexecuted"
        )


def generate_case(case_id: str, key_path: Path, model: str, max_revisions: int) -> dict[str, Any]:
    if os.environ.get(GENERATION_SANDBOX_ENV) != "1":
        raise RuntimeError("case generation must run in the public-only sandbox")
    row = next(item for item in _public_rows() if str(item["instance_id"]) == case_id)
    os.environ["REACHPATCH_TRACE_TEMP_ROOT"] = str(RUN_ROOT / case_id / "trace_tmp")
    execution_image = _execution_image(row)
    if execution_image:
        os.environ["REACHPATCH_EXECUTION_IMAGE"] = execution_image
        os.environ["REACHPATCH_EXECUTION_BASE_COMMIT"] = str(row["base_commit"])
    else:
        os.environ.pop("REACHPATCH_EXECUTION_IMAGE", None)
        os.environ.pop("REACHPATCH_EXECUTION_BASE_COMMIT", None)
    run_root = RUN_ROOT / case_id
    result_path = RESULT_ROOT / f"{case_id}.json"
    if run_root.exists() or result_path.exists():
        raise FileExistsError(f"refusing to overwrite generation artifact for {case_id}")
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("DeepSeek API key is empty")
    transport = DeepSeekHTTPTransport(
        key,
        model=model,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    controller = ReachAvoidController(
        RepairPlayer(DeepSeekAgent(transport, DeepSeekConfig.from_environment())),
        ReachAvoidConfig(max_real_patch_revisions=max_revisions),
    )
    started = time.monotonic()
    terminal = controller.run(_generation_instance(row), run_root=run_root).to_dict()
    if terminal["status"] in {"GENERATOR_BLOCKED_EXTERNAL", "MECHANICAL_BLOCKED"}:
        errors_path = run_root / "controller_errors.jsonl"
        detail = errors_path.read_text(encoding="utf-8")[-8000:] if errors_path.is_file() else ""
        raise RuntimeError(f"{case_id}: {terminal['status']}: {detail}")
    initial = _initial_checkpoint(run_root)
    p0_path = run_root / "p0.patch"
    p0_path.write_text(str(initial["canonical_diff"]), encoding="utf-8")
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
    if not key_path.is_file() or not key_path.stat().st_size:
        raise FileNotFoundError("DeepSeek key path is missing or empty")
    for row in rows:
        _source_tree(row)
    if not shutil.which("bwrap"):
        raise RuntimeError("bubblewrap is unavailable")


def generate(key_path: Path, model: str, max_revisions: int, only: set[str]) -> dict[str, Any]:
    rows = _public_rows()
    known = {str(row["instance_id"]) for row in rows}
    if only - known:
        raise ValueError(f"unknown instance IDs: {sorted(only - known)}")
    _generation_preflight(rows, key_path)
    EXPERIMENT_ROOT.mkdir(parents=True, exist_ok=True)
    case_retries = max(1, int(os.environ.get("REACHPATCH_CASE_RETRIES", "3")))
    manifest = _read_json(GENERATION_MANIFEST)
    expected = {
        "schema": SCHEMA,
        "public_dataset_sha256": _sha256(PUBLIC_PATH),
        "implementation_hash": _implementation_hash(),
        "model": model,
        "max_revisions": max_revisions,
        "case_retries": case_retries,
    }
    if manifest is None:
        manifest = {**expected, "started_at": utc_now()}
        _write_json(GENERATION_MANIFEST, manifest)
    elif any(manifest.get(key) != value for key, value in expected.items()):
        raise RuntimeError("existing generation manifest belongs to a different method run")
    selected = [row for row in rows if not only or str(row["instance_id"]) in only]
    current_results: dict[str, dict[str, Any]] = {}
    failures = []
    for index, row in enumerate(selected, 1):
        case_id = str(row["instance_id"])
        tree = _source_tree(row)
        tree_before = _git_worktree_digest(tree)
        result_path = RESULT_ROOT / f"{case_id}.json"
        existing = _read_json(result_path)
        if existing and _generation_result_valid(existing, row):
            current_results[case_id] = existing
            print(canonical_json({"instance_id": case_id, "status": "REUSED", "index": index}), flush=True)
            continue
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
                current_results[case_id] = result
                print(canonical_json({
                    "instance_id": case_id,
                    "status": result["status"],
                    "attempt": retry,
                    "index": index,
                }), flush=True)
                break
            if result_path.exists() and not _generation_result_valid(result or {}, row):
                result_path.unlink()
        else:
            failures.append({
                "instance_id": case_id,
                "attempts": case_retries,
                "return_code": completed.returncode if completed else None,
                "stdout_tail": completed.stdout[-4000:] if completed else "",
                "stderr_tail": completed.stderr[-8000:] if completed else "",
            })
            print(canonical_json({
                "instance_id": case_id,
                "status": "ERROR",
                "attempts": case_retries,
                "index": index,
            }), flush=True)
    all_results = {}
    for row in rows:
        path = RESULT_ROOT / f"{row['instance_id']}.json"
        result = _read_json(path)
        if result and _generation_result_valid(result, row):
            all_results[str(row["instance_id"])] = result
    summary = {
        **expected,
        "case_count": len(rows),
        "selected_count": len(selected),
        "sealed_case_count": len(all_results),
        "current_result_count": len(current_results),
        "failures": failures,
        "status_counts": dict(sorted(Counter(
            str(item["status"]) for item in all_results.values()
        ).items())),
        "results": [all_results[key] for key in sorted(all_results)],
        "updated_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "generation_summary.json", summary)
    if failures:
        failed_ids = ", ".join(str(item["instance_id"]) for item in failures)
        raise RuntimeError(
            f"generation incomplete: {len(failures)} case(s) produced no valid patch: {failed_ids}"
        )
    if len(all_results) == len(rows) and not failures:
        sealed = {
            "schema": SCHEMA,
            "sealed_at": utc_now(),
            "public_dataset_sha256": _sha256(PUBLIC_PATH),
            "implementation_hash": _implementation_hash(),
            "case_count": len(rows),
            "p0_predictions_sha256": _seal_predictions(all_results, "p0"),
            "final_predictions_sha256": _seal_predictions(all_results, "final"),
            "results_sha256": content_hash(all_results),
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
    if not sealed or sealed.get("case_count") != 51:
        raise RuntimeError("all 51 generation results must be sealed before official data is read")
    rows = _read_jsonl(OFFICIAL_PATH)
    if len(rows) != 51:
        raise RuntimeError(f"expected 51 official instances, found {len(rows)}")
    public_ids = {str(row["instance_id"]) for row in _public_rows()}
    official_ids = {str(row["instance_id"]) for row in rows}
    if official_ids != public_ids:
        raise RuntimeError("official/public instance sets differ")
    return rows


def _harness_report_path(stage: str, run_id: str) -> Path:
    candidates = list((HARNESS_ROOT / stage).glob(f"*.{run_id}.json"))
    if len(candidates) != 1:
        raise RuntimeError(f"expected one official {stage} report, found {len(candidates)}")
    return candidates[0]


def _run_harness_stage(stage: str, workers: int, timeout: int) -> dict[str, Any]:
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
        "--dataset_name", str(OFFICIAL_PATH),
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
    _official_rows_after_seal()
    p0 = _run_harness_stage("p0", workers, timeout)
    final = _run_harness_stage("final", workers, timeout)
    summary = {"schema": SCHEMA, "p0": p0, "final": final, "completed_at": utc_now()}
    _write_json(HARNESS_ROOT / "harness_summary.json", summary)
    build_effectiveness_report()
    return summary


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
        "requirement_graph", "program_graph", "binding_graph",
        "challenge_graph", "reach_avoid",
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
        "| Instance | p0 | final | outcome | Requirement | Program | Binding | Challenge | Reach-Avoid |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|",
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
