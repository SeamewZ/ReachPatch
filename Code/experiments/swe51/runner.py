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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any

# Keep direct `python experiments/swe51/runner.py ...` invocations equivalent
# to module execution when the repository has not been installed as a package.
CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from reachpatch.models.base import content_hash, utc_now
from reachpatch.models.isolation import (
    GenerationInstance,
    HarnessEvaluationInstance,
    assert_generation_payload,
)
from reachpatch.execution.official_harness import (
    OfficialHarnessUnavailable,
    run_official_swebench_instance,
)
from reachpatch.execution.public_docker import (
    PUBLIC_IMAGE_ENV,
    PublicDockerExecutionBroker,
    public_swebench_image,
)
from reachpatch.reach_avoid.controller import (
    AnalysisBlocked,
    ReachPatchConfig,
    ReachPatchController,
)
from reachpatch.repair.deepseek_agent import (
    DeepSeekHTTPTransport,
    PersistentDeepSeekAgent,
)


def _configured_root(environment_name: str, default: Path) -> Path:
    raw = os.environ.get(environment_name)
    configured = Path(raw).expanduser().resolve() if raw else default.resolve()
    if not configured.is_relative_to(CODE_ROOT):
        raise ValueError(
            f"{environment_name} must stay inside the ReachPatch Code root: "
            f"{configured}"
        )
    return configured


DATASET_COLLECTION_ROOT = CODE_ROOT / "dataset"
DATASET_ROOT = _configured_root(
    "REACHPATCH_DATASET_ROOT",
    DATASET_COLLECTION_ROOT / "patchpsro_55_unique51",
)
if not DATASET_ROOT.is_relative_to(DATASET_COLLECTION_ROOT):
    raise ValueError("REACHPATCH_DATASET_ROOT must stay inside Code/dataset")
PUBLIC_PATH = DATASET_ROOT / "generation_public_instances.jsonl"
OFFICIAL_PATH = DATASET_ROOT / "official_instances.jsonl"
EXPERIMENT_ROOT = _configured_root(
    "REACHPATCH_EXPERIMENT_ROOT",
    CODE_ROOT / "experiments" / "swe51",
)
EXPERIMENT_LABEL = os.environ.get("REACHPATCH_EXPERIMENT_LABEL", "SWE51")
REPO_ROOT = EXPERIMENT_ROOT / "repos"
TREE_ROOT = EXPERIMENT_ROOT / "case_trees"
RUN_ROOT = EXPERIMENT_ROOT / "runs"
RESULT_ROOT = EXPERIMENT_ROOT / "results"
HARNESS_ROOT = EXPERIMENT_ROOT / "harness"
HARNESS_RESULT_ROOT = HARNESS_ROOT / "results"
HARNESS_CASE_ROOT = HARNESS_ROOT / "cases"
HARNESS_LOG_ROOT = HARNESS_ROOT / "official_logs"
HARNESS_PREDICTIONS_PATH = HARNESS_ROOT / "sealed_predictions.jsonl"
GENERATION_HISTORY_ROOT = RESULT_ROOT / "_history"
HARNESS_HISTORY_ROOT = HARNESS_ROOT / "_history"
PUBLIC_RUNTIME_DEPENDENCY_ROOT = CODE_ROOT / ".reachpatch_runtime_deps" / "python"
GENERATION_ISOLATION_ENV = "REACHPATCH_GENERATION_ISOLATED"
GENERATION_ISOLATION_ENGINE = "bubblewrap_public_only_v1"
GENERATION_MEMORY_RESERVE_MIB = 16 * 1024
GENERATION_MEMORY_PER_WORKER_MIB = 16 * 1024
GENERATION_DISK_RESERVE_MIB = 40 * 1024
LINEAGE_ENV = "REACHPATCH_GENERATION_LINEAGE"


def _code_commit_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=CODE_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _dataset_manifest_hash() -> str:
    try:
        return hashlib.sha256(PUBLIC_PATH.read_bytes()).hexdigest()
    except OSError:
        return ""


def _implementation_tree_hash() -> str:
    """Fingerprint production code so dirty-tree runs cannot reuse old caches."""

    digest = hashlib.sha256()
    paths = sorted((CODE_ROOT / "reachpatch").rglob("*.py"))
    paths.append(Path(__file__).resolve())
    for path in paths:
        relative = path.relative_to(CODE_ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            return ""
        digest.update(b"\0")
    return digest.hexdigest()


def _prompt_hash(raw: dict[str, Any]) -> str:
    return content_hash({
        "problem_statement": str(raw.get("problem_statement", "")),
        "hints_text": str(raw.get("hints_text", "")),
    })


def _method_config_hash(model: str, max_revisions: int) -> str:
    return content_hash({
        "implementation": "patch_first_incremental_v1",
        "implementation_tree_hash": _implementation_tree_hash(),
        "model": model,
        "max_revisions": int(max_revisions),
        "controller": "ReachPatchConfig",
    })


def _lineage_for(
    raw: dict[str, Any],
    *,
    generation_run_id: str,
    model: str,
    max_revisions: int,
) -> dict[str, str]:
    implementation_tree_hash = _implementation_tree_hash()
    return {
        "instance_id": str(raw["instance_id"]),
        "code_commit_sha": _code_commit_sha(),
        "implementation_tree_hash": implementation_tree_hash,
        "method_config_hash": _method_config_hash(model, max_revisions),
        "prompt_hash": _prompt_hash(raw),
        "generation_run_id": generation_run_id,
        "dataset_manifest_hash": _dataset_manifest_hash(),
    }


def _lineage_matches(value: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if not isinstance(value, dict):
        return False
    return all(str(value.get(key, "")) == str(expected.get(key, "")) for key in expected)


def _memory_snapshot() -> dict[str, float]:
    values: dict[str, float] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            name, separator, raw = line.partition(":")
            if not separator or name not in {
                "MemTotal", "MemAvailable", "SwapTotal", "SwapFree",
            }:
                continue
            values[f"{name.lower()}_mib"] = float(raw.split()[0]) / 1024.0
    except (OSError, ValueError, IndexError):
        return {}
    return values


def _safe_generation_worker_count(
    requested: int,
    snapshot: dict[str, float] | None = None,
) -> int:
    if requested < 1:
        raise ValueError("generation workers must be positive")
    observed = snapshot if snapshot is not None else _memory_snapshot()
    available = observed.get("memavailable_mib")
    if available is None:
        return min(requested, 2)
    headroom = available - GENERATION_MEMORY_RESERVE_MIB
    if headroom < GENERATION_MEMORY_PER_WORKER_MIB:
        return 0
    return min(requested, 10, int(headroom // GENERATION_MEMORY_PER_WORKER_MIB))


def _disk_snapshot(path: Path = CODE_ROOT) -> dict[str, float]:
    usage = shutil.disk_usage(path)
    divisor = 1024.0 * 1024.0
    return {
        "disk_total_mib": usage.total / divisor,
        "disk_used_mib": usage.used / divisor,
        "disk_free_mib": usage.free / divisor,
    }


def _generation_sandbox_command(command: list[str]) -> list[str]:
    """Hide all official-only inputs and outputs from a generation worker."""

    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        raise RuntimeError("bubblewrap is required for isolated patch generation")
    for path in (DATASET_ROOT, HARNESS_ROOT, REPO_ROOT, RUN_ROOT, RESULT_ROOT):
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
    if not PUBLIC_PATH.is_file():
        raise FileNotFoundError(PUBLIC_PATH)

    sandbox = [
        bubblewrap,
        "--die-with-parent",
        "--unshare-pid",
        "--ro-bind", "/", "/",
        "--dev-bind", "/dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
        # Replace the combined dataset with a filesystem containing exactly
        # the public generation records. Raw cases and official fields do not
        # exist in the worker namespace.
        "--tmpfs", str(DATASET_COLLECTION_ROOT),
        "--dir", str(DATASET_ROOT),
        "--ro-bind", str(PUBLIC_PATH), str(PUBLIC_PATH),
        # Git mirrors may contain later public commits, while prior harness
        # artifacts contain direct oracle outcomes. Neither is generation
        # evidence, so both trees are absent from the worker namespace.
        "--tmpfs", str(REPO_ROOT),
        "--tmpfs", str(HARNESS_ROOT),
        # Generation state is the only writable persistent surface.
        "--bind", str(RUN_ROOT), str(RUN_ROOT),
        "--bind", str(RESULT_ROOT), str(RESULT_ROOT),
        "--setenv", GENERATION_ISOLATION_ENV, "1",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--chdir", str(CODE_ROOT),
    ]
    external_private_root = Path("/home/slt/AAAI2027")
    if external_private_root.is_dir():
        sandbox.extend(("--tmpfs", str(external_private_root)))
    experiments_root = CODE_ROOT / "experiments"
    if experiments_root.is_dir():
        for experiment in experiments_root.iterdir():
            if not experiment.is_dir() or experiment.resolve() == EXPERIMENT_ROOT:
                continue
            for private_directory in (
                "harness", "runs", "results", "repos", "case_trees",
            ):
                path = experiment / private_directory
                if path.is_dir():
                    sandbox.extend(("--tmpfs", str(path)))
            for private_name in (
                "harness_summary.json", "experiment_report.json",
                "case_process_report.json", "failure_report.json",
            ):
                path = experiment / private_name
                if path.is_file():
                    sandbox.extend(("--ro-bind", "/dev/null", str(path)))
    resolver = Path("/etc/resolv.conf").resolve()
    if resolver.is_file() and resolver.is_relative_to(Path("/run")):
        relative_parent = resolver.parent.relative_to("/run")
        current = Path("/run")
        for part in relative_parent.parts:
            current /= part
            sandbox.extend(("--dir", str(current)))
        sandbox.extend(("--ro-bind", str(resolver), str(resolver)))
    # These reports can contain official outcomes after a prior harness run.
    # Mounting /dev/null preserves path resolution while making their contents
    # unavailable to the generation process.
    for name in (
        "harness_summary.json",
        "experiment_report.json",
        "experiment_report.md",
        "failure_report.json",
        "failure_report.md",
        "case_process_report.json",
        "case_process_report.md",
    ):
        path = EXPERIMENT_ROOT / name
        if path.exists():
            sandbox.extend(("--ro-bind", "/dev/null", str(path)))
    return [*sandbox, *command]


def _public_runtime_pythonpath(existing: str = "") -> str:
    entries = [str(CODE_ROOT)]
    if PUBLIC_RUNTIME_DEPENDENCY_ROOT.is_dir():
        entries.append(str(PUBLIC_RUNTIME_DEPENDENCY_ROOT))
    entries.extend(
        item for item in existing.split(os.pathsep)
        if item and item not in entries
    )
    return os.pathsep.join(entries)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _repository_cache_name(repository: str) -> str:
    if not repository or any(
        part in {"", ".", ".."} for part in repository.split("/")
    ) or repository.count("/") != 1:
        raise ValueError(f"invalid public repository name: {repository!r}")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(character not in allowed and character != "/" for character in repository):
        raise ValueError(f"invalid public repository name: {repository!r}")
    return repository.replace("/", "__")


def _repository_url(repository: str) -> str:
    return f"https://github.com/{repository}.git"


def _broker_socket_path(case_id: str) -> Path:
    digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16]
    path = (RUN_ROOT / "_b" / f"{digest}.sock").resolve()
    if len(os.fsencode(path)) >= 104:
        raise OSError(f"public broker socket path is too long: {path}")
    return path


def _run_git(arguments: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=1800,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-4000:]
        raise RuntimeError(f"git {' '.join(arguments)} failed: {detail}")
    return completed.stdout.strip()


def prepare_case_trees(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Prepare exact public base-commit worktrees without official evidence."""

    REPO_ROOT.mkdir(parents=True, exist_ok=True)
    TREE_ROOT.mkdir(parents=True, exist_ok=True)
    repositories: dict[str, Path] = {}
    cloned: list[str] = []
    created: list[str] = []
    reused: list[str] = []
    fetched_commits: list[str] = []
    for raw in records:
        repository = str(raw["repo"])
        cache = repositories.get(repository)
        if cache is None:
            cache = REPO_ROOT / _repository_cache_name(repository)
            repositories[repository] = cache
            if not (cache / ".git").is_dir():
                if cache.exists():
                    raise RuntimeError(
                        f"repository cache exists but is not a Git repository: {cache}"
                    )
                _run_git([
                    "clone", "--filter=blob:none", "--no-checkout",
                    _repository_url(repository), str(cache),
                ])
                cloned.append(repository)

        case_id = str(raw["instance_id"])
        base_commit = str(raw["base_commit"])
        tree = TREE_ROOT / case_id
        if tree.exists():
            # A previous worker can leave a checked-out patch tree behind, and
            # interrupted runs can even leave a copied repository without its
            # own .git metadata.  Never reuse either form: archive it and make a
            # fresh detached worktree at the dataset-declared base commit.
            try:
                observed_root = Path(
                    _run_git(["rev-parse", "--show-toplevel"], cwd=tree)
                ).resolve()
                observed = _run_git(["rev-parse", "HEAD"], cwd=tree)
            except (OSError, RuntimeError):
                observed_root = None
                observed = ""
            if observed_root != tree.resolve() or observed != base_commit:
                _archive_path(tree, TREE_ROOT / "_history", case_id)
                _run_git(["worktree", "prune"], cwd=cache)
            else:
                reused.append(case_id)
                continue
        present = subprocess.run(
            ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"],
            cwd=cache,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if present.returncode != 0:
            _run_git(["fetch", "--filter=blob:none", "origin", base_commit], cwd=cache)
            fetched_commits.append(case_id)
        _run_git([
            "worktree", "add", "--detach", str(tree), base_commit,
        ], cwd=cache)
        created.append(case_id)
    return {
        "repository_count": len(repositories),
        "cloned_repositories": cloned,
        "created_case_trees": created,
        "reused_case_trees": reused,
        "fetched_commit_cases": fetched_commits,
    }


def _project_import_name(repository: str) -> str:
    known = {
        "astropy/astropy": "astropy",
        "django/django": "django",
        "matplotlib/matplotlib": "matplotlib",
        "mwaskom/seaborn": "seaborn",
        "pallets/flask": "flask",
        "psf/requests": "requests",
        "pydata/xarray": "xarray",
        "pylint-dev/pylint": "pylint",
        "pytest-dev/pytest": "pytest",
        "scikit-learn/scikit-learn": "sklearn",
        "sphinx-doc/sphinx": "sphinx",
        "sympy/sympy": "sympy",
    }
    if repository not in known:
        raise ValueError(f"no public smoke import configured for {repository}")
    return known[repository]


def _project_smoke_preflight(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], str]:
    representatives: dict[str, dict[str, Any]] = {}
    for raw in records:
        representatives.setdefault(str(raw["repo"]), raw)
    fingerprint = content_hash([
        {
            "repo": repository,
            "instance_id": raw["instance_id"],
            "base_commit": raw["base_commit"],
            "image": public_swebench_image(str(raw["instance_id"])),
        }
        for repository, raw in sorted(representatives.items())
    ])
    previous = _read_json(EXPERIMENT_ROOT / "generation_preflight.json") or {}
    cached_smoke = previous.get("project_smoke", [])
    if (
        previous.get("project_smoke_fingerprint") == fingerprint
        and isinstance(cached_smoke, list)
        and cached_smoke
        and all(item.get("status") == "PASS" for item in cached_smoke)
    ):
        return list(cached_smoke), [], fingerprint

    observations: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for repository, raw in sorted(representatives.items()):
        case_id = str(raw["instance_id"])
        tree = TREE_ROOT / case_id
        run_root = EXPERIMENT_ROOT / "preflight" / case_id
        run_root.mkdir(parents=True, exist_ok=True)
        image = public_swebench_image(case_id)
        try:
            package = _project_import_name(repository)
            broker = PublicDockerExecutionBroker(
                socket_path=_broker_socket_path(f"preflight:{case_id}"),
                image=image,
                case_tree=tree,
                case_run_root=run_root,
                max_live_containers=1,
            )
            with broker:
                response = broker.execute({
                    "token": broker.token,
                    "repository": str(tree),
                    "command": [
                        "python", "-c",
                        f"import {package}; print({package}.__name__)",
                    ],
                    "environment": {"PYTHONPATH": str(tree)},
                    "timeout": 120,
                })
            return_code = response.get("return_code")
            status = "PASS" if return_code == 0 else "FAIL"
            detail = str(response.get("stderr") or response.get("error") or "")[-4000:]
        except (OSError, RuntimeError, ValueError) as exc:
            status = "FAIL"
            return_code = None
            detail = f"{type(exc).__name__}: {exc}"
        observation = {
            "repo": repository,
            "instance_id": case_id,
            "image": image,
            "status": status,
            "return_code": return_code,
            "detail": detail,
        }
        observations.append(observation)
        if status != "PASS":
            failures.append({
                "instance_id": case_id,
                "check": "project_container_smoke",
                "detail": detail or f"return code {return_code}",
            })
    return observations, failures, fingerprint


def validate_generation_preflight(
    records: list[dict[str, Any]],
    *,
    key_path: Path,
) -> dict[str, Any]:
    """Reject systemic input/runtime failures before any Generator API call."""

    failures: list[dict[str, str]] = []
    case_ids: set[str] = set()
    for raw in records:
        case_id = str(raw.get("instance_id", ""))
        if not case_id or case_id in case_ids:
            failures.append({
                "instance_id": case_id,
                "check": "unique_instance_id",
                "detail": "missing or duplicate public instance id",
            })
            continue
        case_ids.add(case_id)
        try:
            assert_generation_payload(raw, path=f"public[{case_id}]")
            GenerationInstance.from_public_record(raw)
        except (KeyError, TypeError, ValueError) as exc:
            failures.append({
                "instance_id": case_id,
                "check": "public_payload",
                "detail": f"{type(exc).__name__}: {exc}",
            })
        tree = TREE_ROOT / case_id
        try:
            observed = _run_git(["rev-parse", "HEAD"], cwd=tree)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            failures.append({
                "instance_id": case_id,
                "check": "base_tree",
                "detail": f"{type(exc).__name__}: {exc}",
            })
        else:
            expected = str(raw.get("base_commit", ""))
            if observed != expected:
                failures.append({
                    "instance_id": case_id,
                    "check": "base_tree",
                    "detail": f"expected {expected}, observed {observed}",
                })

    available_images = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    image_names = set(available_images.stdout.splitlines())
    if available_images.returncode != 0:
        failures.append({
            "instance_id": "*",
            "check": "docker_runtime",
            "detail": available_images.stderr.strip() or "docker images failed",
        })
    missing_images = [
        str(raw["instance_id"])
        for raw in records
        if public_swebench_image(str(raw["instance_id"])) not in image_names
    ]
    for case_id in missing_images:
        failures.append({
            "instance_id": case_id,
            "check": "public_image",
            "detail": public_swebench_image(case_id),
        })
    if shutil.which("bwrap") is None:
        failures.append({
            "instance_id": "*",
            "check": "generation_isolation",
            "detail": "bubblewrap is unavailable",
        })
    if not key_path.is_file() or key_path.stat().st_size <= 0:
        failures.append({
            "instance_id": "*",
            "check": "generator_key",
            "detail": "DeepSeek API key file is missing or empty",
        })
    memory = _memory_snapshot()
    disk = _disk_snapshot()
    if _safe_generation_worker_count(1, memory) == 0:
        failures.append({
            "instance_id": "*",
            "check": "memory_headroom",
            "detail": json.dumps(memory, sort_keys=True),
        })
    if disk["disk_free_mib"] < GENERATION_DISK_RESERVE_MIB:
        failures.append({
            "instance_id": "*",
            "check": "disk_headroom",
            "detail": json.dumps(disk, sort_keys=True),
        })
    project_smoke: list[dict[str, Any]] = []
    project_smoke_fingerprint = ""
    if not failures:
        project_smoke, smoke_failures, project_smoke_fingerprint = (
            _project_smoke_preflight(records)
        )
        failures.extend(smoke_failures)
    report = {
        "status": "PASS" if not failures else "FAIL",
        "experiment_label": EXPERIMENT_LABEL,
        "case_count": len(records),
        "unique_case_count": len(case_ids),
        "public_image_count": len(records) - len(missing_images),
        "memory": memory,
        "disk": disk,
        "disk_reserve_mib": GENERATION_DISK_RESERVE_MIB,
        "project_smoke_fingerprint": project_smoke_fingerprint,
        "project_smoke": project_smoke,
        "failures": failures,
        "completed_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "generation_preflight.json", report)
    if failures:
        raise RuntimeError(
            f"generation preflight failed with {len(failures)} issue(s); "
            f"see {EXPERIMENT_ROOT / 'generation_preflight.json'}"
        )
    return report


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _archive_path(path: Path, history_root: Path, case_id: str) -> Path | None:
    if not path.exists():
        return None
    destination_root = history_root / case_id
    destination_root.mkdir(parents=True, exist_ok=True)
    stamp = f"{time.time_ns()}"
    suffix = path.suffix if path.is_file() else ""
    destination = destination_root / f"attempt-{stamp}{suffix}"
    try:
        path.rename(destination)
    except OSError as exc:
        if getattr(exc, "errno", None) != 18:
            raise
        if path.is_dir():
            shutil.copytree(path, destination)
            shutil.rmtree(path)
        else:
            shutil.copy2(path, destination)
            path.unlink()
    return destination


def _validate_only_ids(records: list[dict[str, Any]], only: set[str] | None) -> None:
    if not only:
        return
    known = {str(item["instance_id"]) for item in records}
    unknown = sorted(only - known)
    if unknown:
        raise ValueError(
            f"unknown {EXPERIMENT_LABEL} instance ids: {', '.join(unknown)}"
        )


def _failure_rows(stage_summary: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in stage_summary.get("results", []):
        status = str(item.get("status", "UNKNOWN"))
        success = status == "PASS" or (stage == "generation" and status == "REACHED")
        if success:
            continue
        rows.append({
            "instance_id": item.get("instance_id"),
            "stage": stage,
            "status": status,
            "error": item.get("error"),
            "error_traceback": item.get("error_traceback"),
            "started_at": item.get("started_at"),
            "finished_at": item.get("finished_at"),
            "run_root": item.get("run_root"),
            "result_path": (
                str(RESULT_ROOT / f"{item.get('instance_id')}.json")
                if stage == "generation" and item.get("instance_id")
                else None
            ),
            "patch_path": item.get("patch_path"),
            "transition_count": item.get("transition_count"),
            "analysis_timings": item.get("analysis_timings", {}),
            "analysis_resources": item.get("analysis_resources", {}),
            "analysis_stats": item.get("analysis_stats", {}),
            "phase_history": item.get("phase_history", []),
            "graph_summary": item.get("graph_summary", {}),
            "reach_avoid": item.get("reach_avoid"),
            "component_effectiveness": item.get("component_effectiveness", []),
            "worker_return_code": item.get("worker_return_code"),
            "worker_stdout": item.get("worker_stdout", "")[-12000:],
            "worker_stderr": item.get("worker_stderr", "")[-12000:],
            "patch_apply": item.get("patch_apply"),
            "fail_to_pass": item.get("fail_to_pass"),
            "pass_to_pass": item.get("pass_to_pass"),
            "deepseek_calls": item.get("deepseek_calls", []),
        })
    return rows


def _failure_point(row: dict[str, Any]) -> str:
    if row.get("stage") == "official_harness":
        return "official_harness"
    status = str(row.get("status", "UNKNOWN"))
    if status == "SEMANTIC_BLOCKED":
        return "semantic_analysis"
    if status == "NO_LEGAL_ACTION":
        return "repair_action_selection"
    phase_history = row.get("phase_history", ())
    for transition in reversed(phase_history):
        if not isinstance(transition, dict):
            continue
        phase = str(transition.get("to_phase", ""))
        if phase and phase != "SEALED":
            return phase.lower()
    resources = row.get("analysis_resources", {})
    in_progress = [
        stage
        for stage, samples in resources.items()
        if isinstance(samples, dict)
        and any(str(key).startswith("in_progress_") for key in samples)
        and not any(str(key).startswith("complete_") for key in samples)
    ]
    if in_progress:
        return sorted(in_progress)[-1]
    timings = row.get("analysis_timings", {})
    stage_order = (
        "semantic_analysis", "repository_index", "requirement_core",
        "initial_localization", "active_program_slice",
        "requirement_graph_initial", "binding_graph_initial",
        "challenge_graph_initial", "first_patch_generation",
        "initial_revision_validation", "program_graph_incremental",
        "requirement_graph_incremental", "binding_graph_incremental",
        "challenge_graph_incremental",
    )
    completed = {
        key.removesuffix("_seconds")
        for key in timings
        if key.endswith("_seconds") and key != "analysis_total_seconds"
    }
    return next(
        (stage for stage in reversed(stage_order) if stage in completed),
        "generation",
    )


def _failure_reason(row: dict[str, Any]) -> str:
    if row.get("error"):
        return str(row["error"])
    status = str(row.get("status", "UNKNOWN"))
    if row.get("stage") == "official_harness":
        target = row.get("fail_to_pass") or {}
        preservation = row.get("pass_to_pass") or {}
        return (
            "official harness isolated result: "
            f"target={target.get('status', 'UNKNOWN')}, "
            f"preservation={preservation.get('status', 'UNKNOWN')}"
        )
    stats = row.get("analysis_stats", {})
    if status in {
        "REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE",
        "REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH",
    }:
        return (
            "revision budget exhausted before Reach: "
            f"submitted={stats.get('submitted_generator_revisions', 0)}, "
            f"deferred_bindings={stats.get('deferred_binding_count', 0)}, "
            f"active_challenges={stats.get('active_challenge_count', 0)}, "
            f"real_challenge_executions={stats.get('real_execution_challenge_count', 0)}"
        )
    return "no certified Reach transition was available"


def write_failure_report(
    *,
    generation_summary: dict[str, Any] | None = None,
    harness_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generation_summary = generation_summary or {}
    harness_summary = harness_summary or {}
    rows = _failure_rows(generation_summary, "generation") + _failure_rows(harness_summary, "official_harness")
    for row in rows:
        row["failure_point"] = _failure_point(row)
        row["failure_reason"] = _failure_reason(row)
    report = {
        "generated_at": utc_now(),
        "case_count": max(
            int(generation_summary.get("case_count", 0)),
            int(harness_summary.get("case_count", 0)),
        ),
        "failure_count": len(rows),
        "failures": rows,
    }
    _write_json(EXPERIMENT_ROOT / "failure_report.json", report)
    lines = [
        f"# {EXPERIMENT_LABEL} Failure Report",
        "",
        f"- Cases observed: `{report['case_count']}`",
        f"- Failure/unknown rows: `{report['failure_count']}`",
        "",
        "| Case | Stage | Status | Reason | Run root |",
        "|---|---|---|---|---|",
    ]
    for row in sorted(rows, key=lambda item: (str(item.get("instance_id")), str(item.get("stage")))):
        reason = str(row["failure_reason"]).replace("|", "/").replace("\n", " ")[:240]
        lines.append(
            f"| `{row.get('instance_id')}` | `{row.get('stage')}` | `{row.get('status')}` | {reason} | `{row.get('run_root') or ''}` |"
        )
    lines.extend(["", "## Per-case diagnostics", ""])
    for row in sorted(rows, key=lambda item: (str(item.get("instance_id")), str(item.get("stage")))):
        components = row.get("component_effectiveness", [])
        outcome_counts: dict[str, int] = {}
        for component in components:
            for status, count in component.get("outcome_counts", {}).items():
                outcome_counts[str(status)] = outcome_counts.get(str(status), 0) + int(count)
        graph_summary = row.get("graph_summary", {})
        reach_avoid = row.get("reach_avoid") or {}
        timings = row.get("analysis_timings", {})
        resources = row.get("analysis_resources", {})
        lines.extend([
            f"### `{row.get('instance_id')}`",
            "",
            f"- Failure point: `{row['failure_point']}`",
            f"- Status: `{row.get('status')}`",
            f"- Reason: {row['failure_reason']}",
            f"- Graph stack: `{graph_summary.get('graph_count', 0)}` graphs; full closure `{bool(graph_summary.get('full_closure', False))}`",
            f"- Transitions: `{row.get('transition_count') or 0}`",
        ])
        if components:
            lines.append(
                f"- Repair components: `{sum(bool(item.get('effective')) for item in components)}/{len(components)}` effective; outcomes `{json.dumps(outcome_counts, sort_keys=True)}`"
            )
        if reach_avoid:
            lines.append(
                f"- Reach-Avoid: phase `{reach_avoid.get('phase')}`, hard frontier `{reach_avoid.get('hard_frontier_count')}`, PASS/FAIL/UNKNOWN `{reach_avoid.get('pass_pairs')}/{reach_avoid.get('fail_pairs')}/{reach_avoid.get('unknown_pairs')}`"
            )
        if timings:
            lines.append(f"- Stage timings: `{json.dumps(timings, sort_keys=True)}`")
        if resources:
            lines.append(f"- Stage memory: `{json.dumps(resources, sort_keys=True)}`")
        lines.extend([
            f"- Result JSON: `{row.get('result_path') or ''}`",
            f"- Run manifest: `{Path(str(row.get('run_root') or '')) / 'run_manifest.json' if row.get('run_root') else ''}`",
            "",
        ])
    lines.append("Captured traceback/stdout/stderr, patch application results, component outcomes, and DeepSeek call records are in `failure_report.json`; older workers may not have captured a traceback.")
    (EXPERIMENT_ROOT / "failure_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def write_experiment_report(
    generation_summary: dict[str, Any],
    harness_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    harness_summary = harness_summary or {}
    generation_by_id = {str(item["instance_id"]): item for item in generation_summary.get("results", [])}
    harness_by_id = {str(item["instance_id"]): item for item in harness_summary.get("results", [])}
    ids = sorted(set(generation_by_id) | set(harness_by_id))
    rows = [
        {
            "instance_id": case_id,
            "generation_status": generation_by_id.get(case_id, {}).get("status", "MISSING"),
            "harness_status": harness_by_id.get(case_id, {}).get("status", "PENDING"),
            "graph_reached": generation_by_id.get(case_id, {}).get("graph_reached"),
            "patch_hash": generation_by_id.get(case_id, {}).get("patch_hash"),
            "run_root": generation_by_id.get(case_id, {}).get("run_root"),
            "transition_count": generation_by_id.get(case_id, {}).get("transition_count", 0),
            "effective_component_count": sum(
                bool(item.get("effective"))
                for item in generation_by_id.get(case_id, {}).get("component_effectiveness", [])
            ),
            "component_count": len(generation_by_id.get(case_id, {}).get("component_effectiveness", [])),
            "accepted_transition_id": generation_by_id.get(case_id, {}).get("reach_avoid", {}).get("accepted_transition_id"),
            "analysis_timings": generation_by_id.get(case_id, {}).get("analysis_timings", {}),
            "analysis_resources": generation_by_id.get(case_id, {}).get("analysis_resources", {}),
            "analysis_stats": generation_by_id.get(case_id, {}).get("analysis_stats", {}),
            "graph_summary": generation_by_id.get(case_id, {}).get("graph_summary", {}),
            "harness_detail": {
                "fail_to_pass": harness_by_id.get(case_id, {}).get("fail_to_pass", {}).get("status"),
                "pass_to_pass": harness_by_id.get(case_id, {}).get("pass_to_pass", {}).get("status"),
                "patch_apply": harness_by_id.get(case_id, {}).get("patch_apply", {}).get("status"),
            },
        }
        for case_id in ids
    ]
    def counts(values: list[str]) -> dict[str, int]:
        output: dict[str, int] = {}
        for value in values:
            output[value] = output.get(value, 0) + 1
        return output
    timing_summary: dict[str, dict[str, float | int]] = {}
    for row in rows:
        for key, value in row.get("analysis_timings", {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            current = timing_summary.setdefault(key, {"count": 0, "sum_seconds": 0.0, "max_seconds": 0.0})
            current["count"] = int(current["count"]) + 1
            current["sum_seconds"] = float(current["sum_seconds"]) + number
            current["max_seconds"] = max(float(current["max_seconds"]), number)
    for value in timing_summary.values():
        value["mean_seconds"] = float(value["sum_seconds"]) / int(value["count"])
    memory_summary: dict[str, dict[str, float | int]] = {}
    for row in rows:
        for stage, samples in row.get("analysis_resources", {}).items():
            if not isinstance(samples, dict):
                continue
            values = [float(value) for key, value in samples.items() if key.endswith("peak_rss_mib")]
            if not values:
                continue
            peak = max(values)
            current = memory_summary.setdefault(
                stage, {"count": 0, "sum_peak_rss_mib": 0.0, "max_peak_rss_mib": 0.0}
            )
            current["count"] = int(current["count"]) + 1
            current["sum_peak_rss_mib"] = float(current["sum_peak_rss_mib"]) + peak
            current["max_peak_rss_mib"] = max(float(current["max_peak_rss_mib"]), peak)
    for value in memory_summary.values():
        value["mean_peak_rss_mib"] = float(value["sum_peak_rss_mib"]) / int(value["count"])
    report = {
        "generated_at": utc_now(),
        "case_count": len(rows),
        "generation_counts": counts([str(row["generation_status"]) for row in rows]),
        "harness_counts": counts([str(row["harness_status"]) for row in rows]),
        "graph_timing_summary": timing_summary,
        "graph_memory_summary": memory_summary,
        "results": rows,
        "failure_report": str(EXPERIMENT_ROOT / "failure_report.json"),
    }
    _write_json(EXPERIMENT_ROOT / "experiment_report.json", report)
    lines = [
        f"# {EXPERIMENT_LABEL} Experiment Report",
        "",
        f"- Cases: `{report['case_count']}`",
        f"- Generation counts: `{json.dumps(report['generation_counts'], sort_keys=True)}`",
        f"- Harness counts: `{json.dumps(report['harness_counts'], sort_keys=True)}`",
        "",
        "| Case | Generation | Harness | F2P | P2P | Patch apply | Graphs | Components effective | Transitions | Graph reached |",
        "|---|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        detail = row["harness_detail"]
        lines.append(
            f"| `{row['instance_id']}` | `{row['generation_status']}` | `{row['harness_status']}` | "
            f"`{detail['fail_to_pass'] or ''}` | `{detail['pass_to_pass'] or ''}` | "
            f"`{detail['patch_apply'] or ''}` | `{row['graph_summary'].get('graph_count', 0)}/5` | "
            f"`{row['effective_component_count']}/{row['component_count']}` | "
            f"`{row['transition_count']}` | `{row['graph_reached']}` |"
        )
    lines.extend([
        "",
        "## Graph Timing Summary",
        "",
        "| Stage | Cases | Mean seconds | Max seconds | Total seconds |",
        "|---|---:|---:|---:|---:|",
    ])
    for key, value in sorted(timing_summary.items()):
        lines.append(
            f"| `{key}` | {int(value['count'])} | {float(value['mean_seconds']):.3f} | "
            f"{float(value['max_seconds']):.3f} | {float(value['sum_seconds']):.3f} |"
        )
    lines.extend([
        "",
        "## Graph Memory Summary",
        "",
        "| Stage | Cases | Mean peak RSS MiB | Max peak RSS MiB |",
        "|---|---:|---:|---:|",
    ])
    for key, value in sorted(memory_summary.items()):
        lines.append(
            f"| `{key}` | {int(value['count'])} | {float(value['mean_peak_rss_mib']):.1f} | "
            f"{float(value['max_peak_rss_mib']):.1f} |"
        )
    lines.extend(["", "Detailed failure rows and reasons: `failure_report.md` and `failure_report.json`."])
    (EXPERIMENT_ROOT / "experiment_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _graph_stage_timings(item: dict[str, Any]) -> dict[str, float]:
    timings = item.get("analysis_timings", {})
    groups = {
        "semantic_graph": ("semantic_",),
        "repository_index": ("repository_index_",),
        "requirement_graph": ("requirement_core_", "requirement_graph_", "requirement_"),
        "program_graph": ("active_program_slice_", "program_graph_", "program_slice_"),
        "binding_graph": ("binding_graph_", "binding_"),
        "challenge_graph": ("challenge_graph_", "challenge_"),
        "initial_generation": ("first_patch_generation_",),
    }
    output: dict[str, float] = {}
    for stage, prefixes in groups.items():
        values: list[float] = []
        for key, value in timings.items():
            if not any(str(key).startswith(prefix) for prefix in prefixes):
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        output[stage] = sum(values)
    return output


def _transition_process(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, certificate in enumerate(item.get("transition_certificates", ())):
        if not isinstance(certificate, dict):
            continue
        decision = certificate.get("decision")
        if isinstance(decision, dict):
            decision = decision.get("value") or decision.get("name")
        rows.append({
            "index": index + 1,
            "transition_id": certificate.get("transition_id"),
            "decision": str(decision or "UNKNOWN"),
            "mechanical_pass": certificate.get("mechanical_pass"),
            "safe": certificate.get("safe"),
            "progress": certificate.get("progress"),
            "reach": certificate.get("reach"),
            "avoid": certificate.get("avoid"),
            "actual_edit_ids": certificate.get("actual_edit_ids", []),
            "new_counterexample_ids": certificate.get("new_counterexample_ids", []),
            "eliminated_counterexample_ids": certificate.get("eliminated_counterexample_ids", []),
            "graph_delta": certificate.get("graph_delta", {}),
        })
    return rows


def write_case_process_report(
    generation_summary: dict[str, Any],
    harness_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    harness_summary = harness_summary or {}
    generated = {
        str(item["instance_id"]): item
        for item in generation_summary.get("results", ())
        if item.get("instance_id")
    }
    harnessed = {
        str(item["instance_id"]): item
        for item in harness_summary.get("results", ())
        if item.get("instance_id")
    }
    rows: list[dict[str, Any]] = []
    for case_id in sorted(set(generated) | set(harnessed)):
        generation = generated.get(case_id, {})
        harness_result = harnessed.get(case_id, {})
        transitions = _transition_process(generation)
        effective_components = [
            item for item in generation.get("component_effectiveness", ())
            if item.get("effective")
        ]
        rows.append({
            "instance_id": case_id,
            "generation_status": generation.get("status", "MISSING"),
            "harness_status": harness_result.get("status", "PENDING"),
            "attempt": generation.get("attempt"),
            "started_at": generation.get("started_at"),
            "finished_at": generation.get("finished_at"),
            "phase_history": generation.get("phase_history", []),
            "graph_timings_seconds": _graph_stage_timings(generation),
            "graph_build_records": generation.get(
                "analysis_resources", {}
            ).get("graph_build_records", []),
            "analysis_timings": generation.get("analysis_timings", {}),
            "analysis_resources": generation.get("analysis_resources", {}),
            "analysis_stats": generation.get("analysis_stats", {}),
            "graph_summary": generation.get("graph_summary", {}),
            "deepseek_calls": generation.get("deepseek_calls", []),
            "transitions": transitions,
            "accepted_transition_count": sum(
                row["decision"] == "COMMIT" for row in transitions
            ),
            "rolled_back_transition_count": sum(
                row["decision"] == "ROLLBACK" for row in transitions
            ),
            "effective_components": effective_components,
            "all_components": generation.get("component_effectiveness", []),
            "successful_steps": [
                {
                    "transition_id": row["transition_id"],
                    "edit_ids": row["actual_edit_ids"],
                    "eliminated_counterexamples": row["eliminated_counterexample_ids"],
                }
                for row in transitions
                if row["decision"] == "COMMIT"
            ],
            "failure": {
                "error": generation.get("error"),
                "traceback": generation.get("error_traceback"),
                "worker_stderr": generation.get("worker_stderr"),
            } if generation.get("status") != "REACHED" else None,
            "harness": {
                "patch_apply": harness_result.get("patch_apply"),
                "fail_to_pass": harness_result.get("fail_to_pass"),
                "pass_to_pass": harness_result.get("pass_to_pass"),
            } if harness_result else None,
            "patch_path": generation.get("patch_path"),
            "patch_hash": generation.get("patch_hash"),
            "run_root": generation.get("run_root"),
        })
    report = {
        "generated_at": utc_now(),
        "case_count": len(rows),
        "results": rows,
    }
    _write_json(EXPERIMENT_ROOT / "case_process_report.json", report)
    lines = [
        f"# {EXPERIMENT_LABEL} Case Process Report",
        "",
        f"- Cases observed: `{len(rows)}`",
        "- Every row records generation phases, all five graph timings, DeepSeek calls, transitions, component outcomes, and isolated harness results.",
        "",
        "| Case | Generation | Harness | Semantic | Index | Requirement | Program | Binding | Challenge | Initial patch | Commit/Rollback |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        timing = row["graph_timings_seconds"]
        lines.append(
            f"| `{row['instance_id']}` | `{row['generation_status']}` | `{row['harness_status']}` | "
            f"{timing['semantic_graph']:.3f} | {timing['repository_index']:.3f} | "
            f"{timing['requirement_graph']:.3f} | {timing['program_graph']:.3f} | "
            f"{timing['binding_graph']:.3f} | {timing['challenge_graph']:.3f} | "
            f"{timing['initial_generation']:.3f} | "
            f"{row['accepted_transition_count']}/{row['rolled_back_transition_count']} |"
        )
    lines.extend(["", "## Per-case process", ""])
    for row in rows:
        phase_names = [
            str(item.get("phase") or item.get("to") or item)
            if isinstance(item, dict) else str(item)
            for item in row["phase_history"]
        ]
        lines.extend([
            f"### `{row['instance_id']}`",
            "",
            f"- Generation/Harness: `{row['generation_status']}` / `{row['harness_status']}`",
            f"- Phase path: `{' -> '.join(phase_names) if phase_names else 'not recorded'}`",
            f"- Graph timings: `{json.dumps(row['graph_timings_seconds'], sort_keys=True)}`",
            f"- Graph build records: `{len(row['graph_build_records'])}` (initial and every incremental/context update)",
            f"- DeepSeek calls: `{len(row['deepseek_calls'])}`",
            f"- Transitions: `{len(row['transitions'])}`; accepted `{row['accepted_transition_count']}`, rolled back `{row['rolled_back_transition_count']}`",
            f"- Effective components: `{len(row['effective_components'])}/{len(row['all_components'])}`",
        ])
        if row["successful_steps"]:
            lines.append(
                f"- Successful steps: `{json.dumps(row['successful_steps'], sort_keys=True)}`"
            )
        if row["failure"]:
            lines.append(
                f"- Failure reason: `{str(row['failure'].get('error') or 'no certified Reach transition')[:500]}`"
            )
        lines.extend([
            f"- Patch: `{row['patch_path'] or ''}`",
            f"- Full structured process: `case_process_report.json` entry `{row['instance_id']}`",
            "",
        ])
    (EXPERIMENT_ROOT / "case_process_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return report


def _public_instance(raw: dict[str, Any], tree: Path):
    return GenerationInstance.from_public_record(raw).to_controller_instance(tree)


def _component_effectiveness(state) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component_id, component in sorted(
        getattr(state.active_binding_graph, "components", {}).items()
    ):
        unit_ids = tuple(component.unit_ids)
        outcomes = [item for item in state.outcomes.values() if item.unit_id in unit_ids]
        counts: dict[str, int] = {}
        for item in outcomes:
            status = item.status.value if hasattr(item.status, "value") else str(item.status)
            counts[status] = counts.get(status, 0) + 1
        pass_count = counts.get("PASS", 0)
        rows.append({
            "component_id": component_id,
            "unit_ids": list(unit_ids),
            "unit_count": len(unit_ids),
            "outcome_count": len(outcomes),
            "outcome_counts": counts,
            "effective": bool(outcomes) and pass_count == len(outcomes),
            "pass_ratio": pass_count / len(outcomes) if outcomes else 0.0,
            "legal_repair_cut_ids": list(component.legal_repair_cut_ids),
            "preservation_node_ids": list(component.preservation_node_ids),
        })
    return rows


def _graph_summary(state) -> dict[str, Any]:
    graph_values = {
        "semantic_hypothesis_graph": {
            "hash": state.semantic_graph.to_dict().get("graph_hash"),
            "artifact_ids": list(state.artifact_ids.get("semantic_hypothesis_graph", ())),
        },
        "requirement_graph": {
            "hash": state.requirement_graph.semantic_layer_hash(),
            "artifact_ids": list(state.artifact_ids.get("requirement_graph", ())),
        },
        "program_graph": {
            "hash": state.program_graph.program_hash(),
            "artifact_ids": list(state.artifact_ids.get("program_graph", ())),
        },
        "binding_graph": {
            "hash": state.active_binding_graph.graph_hash(),
            "artifact_ids": list(state.artifact_ids.get("active_binding_graph", ())),
        },
        "challenge_graph": {
            "hash": state.challenge_graph.graph_hash(),
            "artifact_ids": list(state.artifact_ids.get("challenge_graph", ())),
        },
    }
    built = {
        name: value
        for name, value in graph_values.items()
        if value["artifact_ids"] or value["hash"]
    }
    return {
        "graph_count": len(built),
        "graph_names": sorted(built),
        "graphs": built,
        "expected_full_closure_graph_count": 5,
        "full_closure": bool(state.checkpoint.graph_reached),
    }


def _generate_one(
    raw: dict[str, Any],
    transport: DeepSeekHTTPTransport,
    max_revisions: int,
    *,
    force: bool = False,
) -> dict[str, Any]:
    case_id = str(raw["instance_id"])
    tree = TREE_ROOT / case_id
    run_root = RUN_ROOT / case_id
    result_path = RESULT_ROOT / f"{case_id}.json"
    raw_lineage = os.environ.get(LINEAGE_ENV, "")
    try:
        lineage = json.loads(raw_lineage) if raw_lineage else {}
    except json.JSONDecodeError:
        lineage = {}
    if not lineage:
        lineage = _lineage_for(
            raw,
            generation_run_id=run_root.name,
            model=getattr(transport, "model", "deepseek-chat"),
            max_revisions=max_revisions,
        )
    else:
        lineage = {
            **lineage,
            "instance_id": case_id,
            "code_commit_sha": str(lineage.get("code_commit_sha", "")),
            "implementation_tree_hash": str(
                lineage.get("implementation_tree_hash", "")
            ),
            "method_config_hash": str(lineage.get("method_config_hash", "")),
            "prompt_hash": _prompt_hash(raw),
            "dataset_manifest_hash": str(lineage.get("dataset_manifest_hash", "")),
        }
    previous = _read_json(result_path)
    if (
        previous is not None
        and not force
        and _lineage_matches(previous, lineage)
        and Path(str(previous.get("patch_path", ""))).is_file()
    ):
        return previous
    if force:
        _archive_path(result_path, GENERATION_HISTORY_ROOT, case_id)
        _archive_path(run_root, RUN_ROOT / "_history", case_id)
    result: dict[str, Any] = {
        "instance_id": case_id,
        "repo": raw["repo"],
        "base_commit": raw["base_commit"],
        "attempt": int((previous or {}).get("attempt", 0)) + 1,
        "started_at": utc_now(),
        "generation_source": "generation_public_instances.jsonl",
        "generation_isolation": os.environ.get(GENERATION_ISOLATION_ENV, "UNISOLATED"),
        "generation_isolation_engine": GENERATION_ISOLATION_ENGINE,
        "public_environment_image": os.environ.get(PUBLIC_IMAGE_ENV, ""),
        "implementation": "patch_first_incremental_v1",
        **lineage,
    }
    if not tree.is_dir():
        result.update({"status": "BLOCKED_REPOSITORY", "error": str(tree)})
        _write_json(result_path, result)
        return result
    if run_root.exists():
        interrupted_root = RUN_ROOT / "_interrupted" / f"{case_id}-{int(time.time())}"
        interrupted_root.parent.mkdir(parents=True, exist_ok=True)
        run_root.rename(interrupted_root)
    try:
        instance = _public_instance(raw, tree)
        agent = PersistentDeepSeekAgent(transport, max_tool_turns=6)
        controller = ReachPatchController(
            config=ReachPatchConfig(
                selection_mode="hypothesis_set",
                max_submitted_revisions=max_revisions,
                max_internal_tool_turns_per_revision=6,
            ),
            generator_agent=agent,
            implementation_root=CODE_ROOT,
        )
        state, certificate = controller.run(instance, run_root=run_root)
        manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
        result.update({
            "status": certificate.status,
            "graph_reached": bool(certificate.graph_reached),
            "run_root": str(run_root),
            "terminal_certificate": certificate.to_dict(),
            "patch_path": str(run_root / "final_patch.diff"),
            "patch_hash": state.checkpoint.patch.canonical_diff_hash,
            "patch_file_sha256": hashlib.sha256(
                (run_root / "final_patch.diff").read_bytes()
            ).hexdigest(),
            "transition_count": state.transition_index,
            "reach_avoid": {
                "termination_status": state.termination_status,
                "target_deficit": state.checkpoint.executed_target_deficit,
                "phase": state.phase.value,
                "graph_reached": bool(state.graph_reached if hasattr(state, "graph_reached") else certificate.graph_reached),
                "hard_frontier_count": len(state.challenge_graph.frontiers),
                "counterexample_count": len(state.counterexamples),
                "accepted_transition_id": state.checkpoint.accepted_transition_id,
                "pass_pairs": len(state.checkpoint.pass_pairs),
                "fail_pairs": len(state.checkpoint.fail_pairs),
                "unknown_pairs": len(state.checkpoint.unknown_pairs),
            },
            "component_effectiveness": _component_effectiveness(state),
            "graph_summary": _graph_summary(state),
            "transition_certificates": [item.to_dict() for item in state.repair_history],
            "phase_history": list(state.phase_history),
            "artifact_ids": state.artifact_ids,
            "analysis_timings": manifest.get("analysis_timings", {}),
            "analysis_resources": manifest.get("analysis_resources", {}),
            "analysis_stats": manifest.get("analysis_stats", {}),
        })
    except AnalysisBlocked as exc:
        result.update({"status": exc.status, "error": exc.detail, "run_root": str(run_root)})
    except Exception as exc:
        result.update({
            "status": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "error_traceback": traceback.format_exc(),
            "run_root": str(run_root),
        })
    manifest_path = run_root / "run_manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            result["analysis_timings"] = manifest.get("analysis_timings", {})
            result["analysis_resources"] = manifest.get("analysis_resources", {})
            result["analysis_stats"] = manifest.get("analysis_stats", {})
            result["graph_summary"] = manifest.get("graph_summary", {})
        except (OSError, json.JSONDecodeError):
            result["analysis_timings"] = {}
            result["analysis_resources"] = {}
            result["analysis_stats"] = {}
            result["graph_summary"] = {}
    result["deepseek_calls"] = list(transport.calls)
    result["finished_at"] = utc_now()
    _write_json(result_path, result)
    return result


def _run_case_subprocess(
    raw: dict[str, Any],
    *,
    key_path: Path,
    model: str,
    max_revisions: int,
    timeout: int | None,
    force: bool,
    lineage: dict[str, str] | None = None,
) -> dict[str, Any]:
    case_id = str(raw["instance_id"])
    result_path = RESULT_ROOT / f"{case_id}.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "case",
        "--instance-id",
        case_id,
        "--key-path",
        str(key_path),
        "--model",
        model,
        "--max-revisions",
        str(max_revisions),
    ]
    if force:
        command.append("--force")
    try:
        command = _generation_sandbox_command(command)
    except (OSError, RuntimeError) as exc:
        result = {
            "instance_id": case_id,
            "repo": raw["repo"],
            "base_commit": raw["base_commit"],
            "status": "ENVIRONMENT_BLOCKED",
            "error": f"generation isolation unavailable: {type(exc).__name__}: {exc}",
            "root_causes": ["HARNESS_ISOLATION_UNAVAILABLE"],
            "generation_isolation": "UNAVAILABLE",
            "run_root": str(RUN_ROOT / case_id),
            "finished_at": utc_now(),
        }
        _write_json(result_path, result)
        return result
    child_environment = dict(os.environ)
    if lineage is not None:
        child_environment[LINEAGE_ENV] = json.dumps(lineage, sort_keys=True)
    child_environment["PYTHONPATH"] = _public_runtime_pythonpath(
        child_environment.get("PYTHONPATH", "")
    )
    broker = PublicDockerExecutionBroker(
        socket_path=_broker_socket_path(case_id),
        image=public_swebench_image(case_id),
        case_tree=TREE_ROOT / case_id,
        case_run_root=RUN_ROOT / case_id,
    )
    try:
        with broker:
            child_environment.update(broker.worker_environment())
            process = subprocess.run(
                command,
                cwd=CODE_ROOT,
                env=child_environment,
                capture_output=True,
                text=True,
                timeout=None if timeout is None or timeout <= 0 else timeout,
                check=False,
            )
    except (OSError, RuntimeError) as exc:
        result = {
            "instance_id": case_id,
            "repo": raw["repo"],
            "base_commit": raw["base_commit"],
            "status": "ENVIRONMENT_BLOCKED",
            "error": f"public runner unavailable: {type(exc).__name__}: {exc}",
            "root_causes": ["INVALID_PUBLIC_RUNNER", "ENVIRONMENT_UNHEALTHY"],
            "generation_isolation": "1",
            "public_environment_image": broker.image,
            "run_root": str(RUN_ROOT / case_id),
            "finished_at": utc_now(),
        }
        _write_json(result_path, result)
        return result
    except subprocess.TimeoutExpired as exc:
        result = {
            "instance_id": case_id,
            "repo": raw["repo"],
            "base_commit": raw["base_commit"],
            "status": "UNKNOWN_EXECUTION",
            "error": f"generation timeout after {timeout}s",
            "run_root": str(RUN_ROOT / case_id),
            "stdout": str(exc.stdout or "")[-12000:],
            "stderr": str(exc.stderr or "")[-12000:],
            "finished_at": utc_now(),
        }
        _write_json(result_path, result)
        return result
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["worker_stdout"] = process.stdout[-12000:]
        result["worker_stderr"] = process.stderr[-12000:]
        result["worker_return_code"] = process.returncode
        _write_json(result_path, result)
        return result
    result = {
        "instance_id": case_id,
        "repo": raw["repo"],
        "base_commit": raw["base_commit"],
        "status": "ERROR",
        "error": "case subprocess exited without a result artifact",
        "run_root": str(RUN_ROOT / case_id),
        "stdout": process.stdout[-12000:],
        "stderr": process.stderr[-12000:],
        "worker_return_code": process.returncode,
        "finished_at": utc_now(),
    }
    _write_json(result_path, result)
    return result


def generate(
    max_workers: int,
    max_revisions: int,
    model: str,
    key_path: Path,
    only: set[str] | None = None,
    case_timeout: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    all_public = _read_jsonl(PUBLIC_PATH)
    _validate_only_ids(all_public, only)
    public = all_public
    if only:
        public = [item for item in all_public if str(item["instance_id"]) in only]
    all_public_ids = {str(item["instance_id"]) for item in all_public}
    selected_ids = {str(item["instance_id"]) for item in public}
    generation_run_id = f"{EXPERIMENT_LABEL.lower()}-{time.time_ns()}"
    lineage_by_id = {
        str(item["instance_id"]): _lineage_for(
            item,
            generation_run_id=generation_run_id,
            model=model,
            max_revisions=max_revisions,
        )
        for item in public
    }
    tree_preparation = prepare_case_trees(public)
    generation_preflight = validate_generation_preflight(
        public, key_path=key_path,
    )
    # Incremental batches must not erase results from earlier batches. Load
    # both the prior summary and per-case result artifacts, then replace only
    # the cases selected for this invocation.
    prior_by_id: dict[str, dict[str, Any]] = {}
    cache_rejected_count = 0
    reused_cache_count = 0
    summary_path = EXPERIMENT_ROOT / "generation_summary.json"
    if summary_path.is_file():
        try:
            prior = json.loads(summary_path.read_text(encoding="utf-8"))
            for item in prior.get("results", []):
                case_id = str(item.get("instance_id", ""))
                expected = lineage_by_id.get(case_id)
                if case_id in all_public_ids and case_id not in selected_ids:
                    prior_by_id[case_id] = item
                elif expected is not None and _lineage_matches(item, expected):
                    prior_by_id[case_id] = item
                elif case_id in lineage_by_id:
                    cache_rejected_count += 1
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            print(json.dumps({
                "warning": "generation_summary_ignored",
                "path": str(summary_path),
                "error": f"{type(exc).__name__}: {exc}",
            }, sort_keys=True), file=sys.stderr, flush=True)
    for result_path in sorted(RESULT_ROOT.glob("*.json")):
        try:
            item = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(item, dict) and item.get("instance_id"):
                case_id = str(item["instance_id"])
                expected = lineage_by_id.get(case_id)
                if case_id in all_public_ids and case_id not in selected_ids:
                    prior_by_id[case_id] = item
                elif expected is not None and _lineage_matches(item, expected):
                    prior_by_id[case_id] = item
                elif case_id in lineage_by_id:
                    cache_rejected_count += 1
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    results: list[dict[str, Any]] = []
    memory_observations: list[dict[str, Any]] = []
    initial_memory = _memory_snapshot()
    effective_workers = _safe_generation_worker_count(max_workers, initial_memory)
    memory_observations.append({"event": "start", **initial_memory})
    if public and effective_workers == 0:
        raise RuntimeError(
            "insufficient memory headroom for one isolated generation worker"
        )
    pending = iter(public)
    exhausted = False
    with ThreadPoolExecutor(
        max_workers=max(1, effective_workers), thread_name_prefix="reachpatch-gen",
    ) as pool:
        active: dict[Any, dict[str, Any]] = {}

        def submit_available() -> None:
            nonlocal exhausted
            if exhausted:
                return
            current = _memory_snapshot()
            current_limit = _safe_generation_worker_count(max_workers, current)
            disk = _disk_snapshot()
            if disk["disk_free_mib"] < GENERATION_DISK_RESERVE_MIB:
                return
            while len(active) < min(effective_workers, current_limit):
                try:
                    item = next(pending)
                except StopIteration:
                    exhausted = True
                    return
                future = pool.submit(
                    _run_case_subprocess,
                    item,
                    key_path=key_path,
                    model=model,
                    max_revisions=max_revisions,
                    timeout=case_timeout,
                    force=force,
                    lineage=lineage_by_id.get(str(item["instance_id"])),
                )
                active[future] = item

        submit_available()
        while active:
            completed, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in completed:
                active.pop(future)
                result = future.result()
                results.append(result)
                print(json.dumps({
                    "instance_id": result["instance_id"],
                    "status": result.get("status"),
                    "graph_reached": result.get("graph_reached"),
                }, sort_keys=True), flush=True)
                prior_by_id[str(result["instance_id"])] = result
            memory_observations.append({
                "event": "case_completed",
                "completed_cases": len(results),
                "active_cases": len(active),
                **_memory_snapshot(),
                **_disk_snapshot(),
            })
            submit_available()
        if not exhausted:
            raise RuntimeError(
                "generation stopped submitting cases because memory headroom "
                "remained below the configured safety threshold"
            )
    merged_results = sorted(prior_by_id.values(), key=lambda item: str(item.get("instance_id", "")))
    summary = {
        "stage": "generation",
        "case_count": len(all_public),
        "observed_case_count": len(merged_results),
        "selected_case_count": len(public),
        "results": merged_results,
        "deepseek_model": model,
        "requested_concurrency": max_workers,
        "deepseek_concurrency": effective_workers,
        "memory_reserve_mib": GENERATION_MEMORY_RESERVE_MIB,
        "memory_per_worker_mib": GENERATION_MEMORY_PER_WORKER_MIB,
        "memory_observations": memory_observations,
        "case_timeout_seconds": None if case_timeout is None or case_timeout <= 0 else case_timeout,
        "forced": force,
        "generation_run_id": generation_run_id,
        "code_commit_sha": _code_commit_sha(),
        "implementation_tree_hash": _implementation_tree_hash(),
        "method_config_hash": _method_config_hash(model, max_revisions),
        "dataset_manifest_hash": _dataset_manifest_hash(),
        "current_run_generated_count": len(results),
        "reused_cache_count": reused_cache_count,
        "cache_rejected_count": cache_rejected_count,
        "missing_generation_count": max(0, len(public) - len(results)),
        "tree_preparation": tree_preparation,
        "generation_preflight": generation_preflight,
        "completed_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "generation_summary.json", summary)
    return summary


def generate_case(
    instance_id: str,
    key_path: Path,
    model: str,
    max_revisions: int,
    *,
    force: bool = False,
) -> dict[str, Any]:
    if os.environ.get(GENERATION_ISOLATION_ENV) != "1":
        raise RuntimeError(
            "direct case generation is forbidden outside the public-only sandbox"
        )
    os.environ["PYTHONPATH"] = _public_runtime_pythonpath(
        os.environ.get("PYTHONPATH", "")
    )
    raw = next(
        item for item in _read_jsonl(PUBLIC_PATH)
        if str(item["instance_id"]) == instance_id
    )
    api_key = key_path.read_text(encoding="utf-8").strip()
    if not api_key:
        raise ValueError("DeepSeek API key is empty")
    transport = DeepSeekHTTPTransport(
        api_key,
        model=model,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        max_concurrency=1,
    )
    result = _generate_one(raw, transport, max_revisions, force=force)
    print(json.dumps({"instance_id": instance_id, "status": result.get("status")}, sort_keys=True), flush=True)
    return result


def _harness_one(
    raw: dict[str, Any],
    generation: dict[str, Any],
    timeout: int,
    *,
    force: bool = False,
) -> dict[str, Any]:
    case_id = str(raw["instance_id"])
    root = HARNESS_CASE_ROOT / case_id
    result_path = HARNESS_RESULT_ROOT / f"{case_id}.json"
    patch_hash = str(generation.get("patch_hash") or "")
    patch_path = Path(str(generation.get("patch_path", "")))
    patch_text = patch_path.read_text(encoding="utf-8") if patch_path.is_file() else ""
    actual_patch_hash = content_hash(patch_text) if patch_text.strip() else ""
    patch_file_sha256 = (
        hashlib.sha256(patch_text.encode("utf-8")).hexdigest()
        if patch_text else ""
    )
    harness_engine = "official_swebench_docker_v1"
    expected_run_id = f"sealed-{(actual_patch_hash or patch_hash)[:16]}"
    cached = _read_json(result_path)
    official_cache = bool(cached) and (
        cached.get("harness_engine") == "official_swebench_docker_v1"
        or (
            cached.get("official_test_patch_wired") is True
            and str(cached.get("image", "")).startswith("swebench/")
        )
    )
    cache_lineage = {
        "instance_id": case_id,
        "code_commit_sha": generation.get("code_commit_sha", ""),
        "implementation_tree_hash": generation.get(
            "implementation_tree_hash", ""
        ),
        "method_config_hash": generation.get("method_config_hash", ""),
        "prompt_hash": generation.get("prompt_hash", ""),
        "generation_run_id": generation.get("generation_run_id", ""),
        "patch_hash": actual_patch_hash or patch_hash,
        "patch_file_sha256": patch_file_sha256,
        "dataset_manifest_hash": generation.get("dataset_manifest_hash", ""),
        "harness_engine": harness_engine,
        "harness_run_id": expected_run_id,
    }
    if (
        cached is not None
        and not force
        and official_cache
        and _lineage_matches(cached, cache_lineage)
    ):
        return {**cached, "cache_reused": True}
    if cached is not None:
        _archive_path(result_path, HARNESS_HISTORY_ROOT, case_id)
    if root.exists():
        _archive_path(root, HARNESS_HISTORY_ROOT / "trees", case_id)
    root.mkdir(parents=True)
    result: dict[str, Any] = {
        "instance_id": case_id,
        "generation_status": generation.get("status"),
        "generation_patch_hash": patch_hash,
        "patch_path": str(patch_path),
        "official_source": "official_instances.jsonl (post-generation only)",
        "harness_engine": harness_engine,
        **cache_lineage,
        "started_at": utc_now(),
        "cache_reused": False,
    }
    if not patch_path.is_file():
        result.update({"status": "BLOCKED_GENERATION", "error": "missing sealed patch"})
        result["finished_at"] = utc_now()
        _write_json(result_path, result)
        return result
    if not patch_text.strip():
        result.update({
            "status": "BLOCKED_GENERATION",
            "error": "sealed generation produced an empty patch",
            "finished_at": utc_now(),
        })
        _write_json(result_path, result)
        return result
    if patch_hash and patch_hash != actual_patch_hash:
        result.update({
            "status": "BLOCKED_GENERATION",
            "error": "sealed patch hash does not match generation summary",
            "actual_patch_hash": actual_patch_hash,
            "patch_file_sha256": patch_file_sha256,
            "finished_at": utc_now(),
        })
        _write_json(result_path, result)
        return result
    evaluation = HarnessEvaluationInstance.from_official_record(
        raw, patch_path=patch_path
    )
    _write_json(root / "harness_evaluation_instance.json", evaluation.to_dict())
    run_id = expected_run_id
    if force:
        run_id += f"-{time.time_ns()}"
    try:
        official = run_official_swebench_instance(
            raw,
            patch_text=patch_text,
            log_root=HARNESS_LOG_ROOT,
            run_id=run_id,
            timeout=timeout,
        )
    except (OfficialHarnessUnavailable, OSError, RuntimeError) as exc:
        official = {"status": "HARNESS_NOT_RUN", "error": str(exc)}
    result.update(official)
    if result.get("status") == "HARNESS_NOT_RUN":
        result["root_cause_labels"] = ["HARNESS_NOT_OFFICIAL"]
    result.update({
        "generation_patch_hash": actual_patch_hash,
        "patch_hash": actual_patch_hash,
        "patch_file_sha256": patch_file_sha256,
        "official_run_id": run_id,
        "harness_run_id": run_id,
        "finished_at": utc_now(),
    })
    _write_json(result_path, result)
    return result


def _sealed_predictions(
    generated: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    predictions: list[dict[str, str]] = []
    for case_id, generation in sorted(generated.items()):
        patch_path = Path(str(generation.get("patch_path", "")))
        if not patch_path.is_file():
            continue
        patch_text = patch_path.read_text(encoding="utf-8")
        if not patch_text.strip():
            continue
        predictions.append({
            "instance_id": case_id,
            "model_name_or_path": "reachpatch",
            "model_patch": patch_text,
        })
    return predictions


def harness(
    max_workers: int,
    timeout: int,
    *,
    only: set[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    official = {str(item["instance_id"]): item for item in _read_jsonl(OFFICIAL_PATH)}
    _validate_only_ids(list(official.values()), only)
    summary_path = EXPERIMENT_ROOT / "generation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    generated = {str(item["instance_id"]): item for item in summary.get("results", [])}
    predictions = _sealed_predictions(generated)
    _write_jsonl(HARNESS_PREDICTIONS_PATH, predictions)
    selected = {
        case_id: item for case_id, item in official.items()
        if not only or case_id in only
    }
    prior_by_id: dict[str, dict[str, Any]] = {}
    cache_rejected_count = 0
    reused_cache_count = 0
    missing_harness_count = 0
    previous_summary = _read_json(EXPERIMENT_ROOT / "harness_summary.json") or {}
    for item in previous_summary.get("results", ()):
        case_id = str(item.get("instance_id", ""))
        generation_item = generated.get(case_id)
        if not generation_item:
            continue
        if _lineage_matches(item, {
            "instance_id": case_id,
            "code_commit_sha": generation_item.get("code_commit_sha", ""),
            "implementation_tree_hash": generation_item.get(
                "implementation_tree_hash", ""
            ),
            "method_config_hash": generation_item.get("method_config_hash", ""),
            "prompt_hash": generation_item.get("prompt_hash", ""),
            "generation_run_id": generation_item.get("generation_run_id", ""),
            "patch_hash": generation_item.get("patch_hash", ""),
            "patch_file_sha256": item.get("patch_file_sha256", ""),
            "dataset_manifest_hash": generation_item.get("dataset_manifest_hash", ""),
            "harness_engine": "official_swebench_docker_v1",
            "harness_run_id": item.get("harness_run_id", item.get("official_run_id", "")),
        }):
            prior_by_id[case_id] = item
        else:
            cache_rejected_count += 1
    for result_path in sorted(HARNESS_RESULT_ROOT.glob("*.json")):
        item = _read_json(result_path)
        if item and item.get("instance_id"):
            case_id = str(item["instance_id"])
            generation_item = generated.get(case_id)
            if generation_item and _lineage_matches(item, {
                "instance_id": case_id,
                "code_commit_sha": generation_item.get("code_commit_sha", ""),
                "implementation_tree_hash": generation_item.get(
                    "implementation_tree_hash", ""
                ),
                "method_config_hash": generation_item.get("method_config_hash", ""),
                "prompt_hash": generation_item.get("prompt_hash", ""),
                "generation_run_id": generation_item.get("generation_run_id", ""),
                "patch_hash": generation_item.get("patch_hash", ""),
                "patch_file_sha256": item.get("patch_file_sha256", ""),
                "dataset_manifest_hash": generation_item.get("dataset_manifest_hash", ""),
                "harness_engine": "official_swebench_docker_v1",
                "harness_run_id": item.get("harness_run_id", item.get("official_run_id", "")),
            }):
                prior_by_id[case_id] = item
            elif case_id in generated:
                cache_rejected_count += 1
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, 10), thread_name_prefix="reachpatch-harness") as pool:
        futures = [
            pool.submit(
                _harness_one, item, generated.get(case_id, {}), timeout,
                force=force,
            )
            for case_id, item in sorted(selected.items())
        ]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            prior_by_id[str(result["instance_id"])] = result
            reused_cache_count += int(bool(result.get("cache_reused")))
            print(json.dumps({"instance_id": result["instance_id"], "status": result.get("status")}, sort_keys=True), flush=True)
    output = {
        "stage": "official_harness",
        "case_count": len(official),
        "observed_case_count": len(prior_by_id),
        "selected_case_count": len(selected),
        "sealed_prediction_count": len(predictions),
        "predictions_path": str(HARNESS_PREDICTIONS_PATH),
        "forced": force,
        "current_run_harness_count": len(results),
        "reused_cache_count": reused_cache_count,
        "cache_rejected_count": cache_rejected_count,
        "missing_harness_count": sum(
            1 for case_id in selected if case_id not in prior_by_id
        ),
        "results": sorted(prior_by_id.values(), key=lambda item: item["instance_id"]),
        "completed_at": utc_now(),
    }
    _write_json(EXPERIMENT_ROOT / "harness_summary.json", output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--workers", type=int, default=4)
    gen.add_argument("--max-revisions", type=int, default=6)
    gen.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    gen.add_argument("--key-path", default="/home/slt/ReachPatch/ds_pwd.txt")
    gen.add_argument("--only", action="append", default=[])
    gen.add_argument(
        "--force", action="store_true",
        help="rerun only the selected cases and archive their previous result/run",
    )
    gen.add_argument(
        "--case-timeout",
        type=int,
        default=0,
        help="Outer per-case generation timeout in seconds; 0 disables it so graph construction is not truncated.",
    )
    case = sub.add_parser("case")
    case.add_argument("--instance-id", required=True)
    case.add_argument("--key-path", default="/home/slt/ReachPatch/ds_pwd.txt")
    case.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    case.add_argument("--max-revisions", type=int, default=6)
    case.add_argument("--force", action="store_true")
    har = sub.add_parser("harness")
    har.add_argument("--workers", type=int, default=4)
    har.add_argument("--timeout", type=int, default=900)
    har.add_argument("--only", action="append", default=[])
    har.add_argument(
        "--force", action="store_true",
        help="rerun only selected harness cases and archive prior evaluation",
    )
    args = parser.parse_args()
    if args.command == "generate":
        summary = generate(
            args.workers,
            args.max_revisions,
            args.model,
            Path(args.key_path),
            set(args.only),
            args.case_timeout,
            args.force,
        )
        write_failure_report(generation_summary=summary)
        write_experiment_report(summary)
        write_case_process_report(summary)
    elif args.command == "case":
        generate_case(
            args.instance_id, Path(args.key_path), args.model,
            args.max_revisions, force=args.force,
        )
        return 0
    else:
        summary = harness(
            args.workers, args.timeout,
            only=set(args.only), force=args.force,
        )
        generation_path = EXPERIMENT_ROOT / "generation_summary.json"
        generation_summary = json.loads(generation_path.read_text(encoding="utf-8")) if generation_path.is_file() else {}
        write_failure_report(generation_summary=generation_summary, harness_summary=summary)
        write_experiment_report(generation_summary, summary)
        write_case_process_report(generation_summary, summary)
    counts: dict[str, int] = {}
    for item in summary.get("results", []):
        status = str(item.get("status"))
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"stage": args.command, "counts": counts, "case_count": len(summary.get("results", []))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
