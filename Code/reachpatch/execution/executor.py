from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from reachpatch.challenge_graph.recipes import InputRecipe, RecipeCompiler
from reachpatch.execution.models import ExecutionRun, PairedTraceBundle, TraceBundle
from reachpatch.models.base import canonical_json, content_hash, stable_id
from reachpatch.models.enums import OutcomeStatus
from reachpatch.oracle.classifier import classify_pair
from reachpatch.oracle.models import ExecutableScenario, RunObservation
from reachpatch.program_graph.tracing import trace_event


def _source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _environment_signature(recipe: InputRecipe) -> str:
    return content_hash({
        "python": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "recipe_environment": recipe.environment,
    })


def _stable_observation(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _stable_observation(item)
            for key, item in value.items()
            if key not in {"identity", "return_identity", "timestamp_ns"}
        }
    if isinstance(value, list):
        return [_stable_observation(item) for item in value]
    return value


class TraceExecutor:
    def __init__(
        self,
        *,
        package_root: str | Path | None = None,
        temporary_root: str | Path | None = None,
    ) -> None:
        self.package_root = Path(package_root or Path(__file__).parents[2]).resolve()
        self.temporary_root = Path(
            temporary_root or self.package_root / ".reachpatch" / "tmp"
        ).resolve()
        self.temporary_root.mkdir(parents=True, exist_ok=True)
        self._source_hashes: dict[Path, str] = {}

    def _source_hash(self, root: Path) -> str:
        cached = self._source_hashes.get(root)
        if cached is None:
            cached = _source_hash(root)
            self._source_hashes[root] = cached
        return cached

    @staticmethod
    def _bundle(
        recipe: InputRecipe,
        repository_role: str,
        runs: tuple[ExecutionRun, ...],
        environment_signature: str,
        source_hash: str,
    ) -> TraceBundle:
        signatures = {
            content_hash({
                "status": run.run.status.value,
                "stage": run.run.stage,
                "channels": _stable_observation(run.run.channels),
                "worker_status": run.worker_status,
            })
            for run in runs
        }
        stable = len(signatures) == 1
        status = runs[0].run.status if stable else OutcomeStatus.FLAKY
        bundle_id = stable_id(
            "trace-bundle", recipe.recipe_id, repository_role, source_hash,
            [run.run.execution_id for run in runs],
        )
        return TraceBundle(
            bundle_id=bundle_id,
            recipe_id=recipe.recipe_id,
            repository_role=repository_role,
            runs=runs,
            stability_status="STABLE" if stable else "FLAKY",
            stable_status=status,
            environment_signature=environment_signature,
            source_hash=source_hash,
            unresolved_reason=None if stable else "repeat observations disagree",
        )

    def execute_recipe(
        self,
        recipe: InputRecipe,
        repository: str | Path,
        *,
        repository_role: str,
        repeats: int = 2,
        start_index: int = 0,
    ) -> TraceBundle:
        RecipeCompiler().validate(recipe)
        root = Path(repository).resolve()
        if not root.is_dir():
            raise FileNotFoundError(root)
        if repeats < 1:
            raise ValueError("repeats must be positive")
        environment_signature = _environment_signature(recipe)
        source_hash = self._source_hash(root)
        runs = tuple(
            self._execute_once(
                recipe,
                root,
                repository_role=repository_role,
                repeat_index=index,
                environment_signature=environment_signature,
                source_hash=source_hash,
            )
            for index in range(start_index, start_index + repeats)
        )
        return self._bundle(
            recipe, repository_role, runs, environment_signature, source_hash
        )

    def _execute_once(
        self,
        recipe: InputRecipe,
        root: Path,
        *,
        repository_role: str,
        repeat_index: int,
        environment_signature: str,
        source_hash: str,
    ) -> ExecutionRun:
        with tempfile.TemporaryDirectory(
            prefix="reachpatch-exec-", dir=self.temporary_root
        ) as temp_name:
            temp = Path(temp_name)
            recipe_path = temp / "recipe.json"
            result_path = temp / "result.json"
            recipe_path.write_text(canonical_json(recipe.to_dict()), encoding="utf-8")
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.pathsep.join((str(self.package_root), str(root))),
                "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
                "LC_ALL": "C.UTF-8",
                "LANG": "C.UTF-8",
                **recipe.environment,
            }
            command = [
                sys.executable,
                "-m",
                "reachpatch.execution.worker",
                "--recipe",
                str(recipe_path),
                "--result",
                str(result_path),
                "--repository",
                str(root),
            ]
            started = time.monotonic()
            try:
                process = subprocess.run(
                    command,
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=recipe.resource_limits.timeout_seconds,
                    check=False,
                )
                duration = time.monotonic() - started
            except subprocess.TimeoutExpired as exc:
                duration = time.monotonic() - started
                run = RunObservation(
                    execution_id=stable_id("execution", recipe.recipe_id, repository_role, repeat_index, "timeout"),
                    environment_signature=environment_signature,
                    stage="global_timeout",
                    observation_reached=False,
                    mechanical_failure=False,
                    setup_failure=False,
                    dependency_failure=False,
                    global_timeout=True,
                    status=OutcomeStatus.UNKNOWN,
                    channels={},
                    observation_schema=(),
                    raw_stdout=exc.stdout or "",
                    raw_stderr=exc.stderr or "",
                    repeat_index=repeat_index,
                    source_hash=source_hash,
                )
                return ExecutionRun(run, (), (), (), (), duration, "TIMEOUT", content_hash(run))
            if process.returncode != 0 or not result_path.is_file():
                mechanical = process.returncode < 0
                dependency = "ModuleNotFoundError" in process.stderr or "ImportError" in process.stderr
                run = RunObservation(
                    execution_id=stable_id("execution", recipe.recipe_id, repository_role, repeat_index, process.returncode, process.stderr),
                    environment_signature=environment_signature,
                    stage="worker",
                    observation_reached=False,
                    mechanical_failure=mechanical,
                    setup_failure=not mechanical,
                    dependency_failure=dependency,
                    global_timeout=False,
                    status=OutcomeStatus.FAIL if mechanical else OutcomeStatus.UNKNOWN,
                    channels={},
                    observation_schema=(),
                    raw_stdout=process.stdout,
                    raw_stderr=process.stderr,
                    repeat_index=repeat_index,
                    source_hash=source_hash,
                )
                return ExecutionRun(run, (), (), (), (), duration, "WORKER_FAILURE", content_hash(run))
            try:
                raw = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                run = RunObservation(
                    execution_id=stable_id("execution", recipe.recipe_id, repository_role, repeat_index, "malformed", str(exc)),
                    environment_signature=environment_signature,
                    stage="result_decode",
                    observation_reached=False,
                    mechanical_failure=False,
                    setup_failure=True,
                    dependency_failure=False,
                    global_timeout=False,
                    status=OutcomeStatus.UNKNOWN,
                    channels={},
                    observation_schema=(),
                    raw_stdout=process.stdout,
                    raw_stderr=process.stderr,
                    repeat_index=repeat_index,
                    source_hash=source_hash,
                )
                return ExecutionRun(run, (), (), (), (), duration, "MALFORMED_RESULT", content_hash(run))
            worker_status = str(raw.get("worker_status", "UNKNOWN"))
            stage = str(raw.get("stage", "unknown"))
            observation_reached = bool(raw.get("observation_reached", False))
            exception = raw.get("exception")
            channels = dict(raw.get("observations", {}))
            if exception is not None:
                channels["exception"] = exception
            setup_failure = worker_status != "PASS" and stage in {"setup", "worker"}
            dependency_failure = setup_failure and (
                isinstance(exception, dict)
                and exception.get("type") in {"ImportError", "ModuleNotFoundError"}
            )
            raw_status = (
                OutcomeStatus.PASS
                if observation_reached
                else OutcomeStatus.UNKNOWN if setup_failure else OutcomeStatus.FAIL
            )
            execution_id = stable_id(
                "execution", recipe.recipe_id, repository_role, repeat_index,
                worker_status, stage, channels, source_hash,
            )
            run_observation = RunObservation(
                execution_id=execution_id,
                environment_signature=environment_signature,
                stage=stage,
                observation_reached=observation_reached,
                mechanical_failure=False,
                setup_failure=setup_failure,
                dependency_failure=dependency_failure,
                global_timeout=False,
                status=raw_status,
                channels=channels,
                observation_schema=tuple(sorted(channels)),
                raw_stdout=str(raw.get("stdout", process.stdout)),
                raw_stderr=str(raw.get("stderr", process.stderr)),
                repeat_index=repeat_index,
                source_hash=source_hash,
            )
            events = tuple(
                trace_event(
                    str(item["kind"]),
                    str(item["file"]),
                    int(item["line"]),
                    str(item["function"]),
                    dict(item.get("payload", {})),
                    int(item["timestamp_ns"]),
                )
                for item in raw.get("trace", [])
            )
            return ExecutionRun(
                run=run_observation,
                trace_events=events,
                state_snapshots=tuple(raw.get("state_snapshots", [])),
                side_effects=tuple(raw.get("side_effects", [])),
                object_shapes=tuple(raw.get("object_shapes", [])),
                duration_seconds=float(raw.get("duration_seconds", duration)),
                worker_status=worker_status,
                raw_result_hash=content_hash(raw),
            )

    def execute_paired(
        self,
        recipe: InputRecipe,
        base_repository: str | Path,
        patch_repository: str | Path,
        scenario: ExecutableScenario,
    ) -> PairedTraceBundle:
        base = self.execute_recipe(recipe, base_repository, repository_role="BASE", repeats=2)
        patch = self.execute_recipe(recipe, patch_repository, repository_role="PATCH", repeats=2)
        classifications = [
            classify_pair(base_run.run, patch_run.run, scenario)
            for base_run, patch_run in zip(base.runs, patch.runs, strict=True)
        ]
        statuses = {item.status for item in classifications}
        if len(statuses) > 1:
            base_extra = self.execute_recipe(
                recipe, base_repository, repository_role="BASE", repeats=1, start_index=2
            )
            patch_extra = self.execute_recipe(
                recipe, patch_repository, repository_role="PATCH", repeats=1, start_index=2
            )
            base = self._bundle(
                recipe,
                "BASE",
                base.runs + base_extra.runs,
                base.environment_signature,
                base.source_hash,
            )
            patch = self._bundle(
                recipe,
                "PATCH",
                patch.runs + patch_extra.runs,
                patch.environment_signature,
                patch.source_hash,
            )
            classifications = [
                classify_pair(base_run.run, patch_run.run, scenario)
                for base_run, patch_run in zip(base.runs, patch.runs, strict=True)
            ]
            statuses = {item.status for item in classifications}
        stable = len(statuses) == 1 and base.stability_status == patch.stability_status == "STABLE"
        status = next(iter(statuses)) if stable else OutcomeStatus.FLAKY
        divergence = self._first_divergence(base, patch)
        paired_id = stable_id(
            "paired-trace", recipe.recipe_id, scenario.scenario_id,
            base.bundle_id, patch.bundle_id,
            [item.to_dict() for item in classifications],
        )
        return PairedTraceBundle(
            paired_bundle_id=paired_id,
            recipe_id=recipe.recipe_id,
            scenario_id=scenario.scenario_id,
            base_bundle=base,
            patch_bundle=patch,
            classifications=tuple(classifications),
            status=status,
            stability_status="STABLE" if stable else "FLAKY",
            first_divergence=divergence,
        )

    @staticmethod
    def _first_divergence(base: TraceBundle, patch: TraceBundle) -> dict[str, Any] | None:
        if not base.runs or not patch.runs:
            return {"kind": "missing_run"}
        base_events = base.runs[0].trace_events
        patch_events = patch.runs[0].trace_events
        for index, (left, right) in enumerate(zip(base_events, patch_events, strict=False)):
            comparable_left = (left.kind, left.file, left.line, left.function, left.payload)
            comparable_right = (right.kind, right.file, right.line, right.function, right.payload)
            if comparable_left != comparable_right:
                return {
                    "index": index,
                    "base_event": left.to_dict(),
                    "patch_event": right.to_dict(),
                }
        if len(base_events) != len(patch_events):
            return {
                "index": min(len(base_events), len(patch_events)),
                "base_remaining": len(base_events),
                "patch_remaining": len(patch_events),
            }
        base_channels = base.runs[0].run.channels
        patch_channels = patch.runs[0].run.channels
        if base_channels != patch_channels:
            return {"kind": "observation", "base": base_channels, "patch": patch_channels}
        return None
