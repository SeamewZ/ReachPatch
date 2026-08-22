from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import tempfile

from reachpatch.models.base import canonical_json, content_hash, stable_id
from reachpatch.models.evidence import (
    ExecutableOracle, OutcomeStatus, PairClassification, PairedTraceBundle,
    RunObservation, TraceBundle,
)
from reachpatch.models.graphs import ExecutableScenario, InputRecipe
from reachpatch.oracle.observe import observe_oracle

from .trace import run_trace
from .worktree import diff_between, tree_hash


_CACHE_SCHEMA = "reachpatch-paired-execution-v2"
_HOT_CACHE_LIMIT = 12
_HOT_CACHE: OrderedDict[str, PairedTraceBundle] = OrderedDict()


def clear_execution_hot_cache() -> None:
    _HOT_CACHE.clear()


def _hot_get(cache_key: str) -> PairedTraceBundle | None:
    value = _HOT_CACHE.get(cache_key)
    if value is not None:
        _HOT_CACHE.move_to_end(cache_key)
    return value


def _hot_put(cache_key: str, value: PairedTraceBundle) -> None:
    _HOT_CACHE[cache_key] = value
    _HOT_CACHE.move_to_end(cache_key)
    while len(_HOT_CACHE) > _HOT_CACHE_LIMIT:
        _HOT_CACHE.popitem(last=False)


def _observation_from_dict(raw: dict) -> RunObservation:
    return RunObservation(
        status=OutcomeStatus(raw["status"]),
        return_code=raw.get("return_code"),
        stdout=str(raw.get("stdout", "")),
        stderr=str(raw.get("stderr", "")),
        duration_seconds=float(raw.get("duration_seconds", 0.0)),
        value=raw.get("value"),
        exception=raw.get("exception"),
    )


def _trace_from_dict(raw: dict) -> TraceBundle:
    return TraceBundle(
        trace_bundle_id=str(raw["trace_bundle_id"]),
        tree_hash=str(raw["tree_hash"]),
        command=tuple(raw["command"]),
        observation=_observation_from_dict(raw["observation"]),
        executed_symbol_ids=tuple(raw.get("executed_symbol_ids", ())),
        executed_path_ids=tuple(raw.get("executed_path_ids", ())),
        executed_line_ids=tuple(raw.get("executed_line_ids", ())),
        state_reads=tuple(raw.get("state_reads", ())),
        state_writes=tuple(raw.get("state_writes", ())),
        dispatch_routes=tuple(raw.get("dispatch_routes", ())),
        first_project_frame=raw.get("first_project_frame"),
        stable_runs=int(raw.get("stable_runs", 1)),
        comparable=bool(raw.get("comparable", True)),
    )


def _bundle_from_dict(raw: dict) -> PairedTraceBundle:
    previous = raw.get("previous")
    return PairedTraceBundle(
        paired_bundle_id=str(raw["paired_bundle_id"]),
        check_id=str(raw["check_id"]),
        challenge_id=str(raw["challenge_id"]),
        patch_hash=str(raw["patch_hash"]),
        baseline=_trace_from_dict(raw["baseline"]),
        patched=_trace_from_dict(raw["patched"]),
        classification=PairClassification(raw["classification"]),
        oracle_id=str(raw["oracle_id"]),
        oracle_authority=str(raw["oracle_authority"]),
        expected_relation=str(raw["expected_relation"]),
        stable_runs=int(raw["stable_runs"]),
        previous=_trace_from_dict(previous) if previous is not None else None,
    )


def _disk_cache_path(cache_dir: Path | None, cache_key: str) -> Path | None:
    if cache_dir is None:
        return None
    root = Path(cache_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{cache_key}.json"


def _load_disk_cache(path: Path, cache_key: str) -> PairedTraceBundle | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = payload["bundle"]
        if (
            payload.get("schema") != _CACHE_SCHEMA
            or payload.get("cache_key") != cache_key
            or payload.get("bundle_hash") != content_hash(raw)
        ):
            return None
        return _bundle_from_dict(raw)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _store_disk_cache(path: Path, cache_key: str, bundle: PairedTraceBundle) -> None:
    raw = bundle.to_dict()
    payload = {
        "schema": _CACHE_SCHEMA,
        "cache_key": cache_key,
        "bundle_hash": content_hash(raw),
        "bundle": raw,
    }
    descriptor, name = tempfile.mkstemp(prefix=f".{cache_key}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(canonical_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _classification(baseline, patched, role: str) -> PairClassification:
    if baseline.status in {OutcomeStatus.BLOCKED, OutcomeStatus.UNSUPPORTED} or patched.status in {
        OutcomeStatus.BLOCKED, OutcomeStatus.UNSUPPORTED,
    }:
        return PairClassification.UNKNOWN
    before_pass = baseline.status is OutcomeStatus.PASS
    after_pass = patched.status is OutcomeStatus.PASS
    if before_pass and after_pass:
        return PairClassification.PASS_PRESERVED
    if not before_pass and after_pass:
        return PairClassification.TARGET_FIXED
    if before_pass and not after_pass:
        return (
            PairClassification.PRESERVATION_REGRESSION
            if role == "PRESERVATION" else PairClassification.TARGET_REGRESSED
        )
    return PairClassification.TARGET_STILL_FAILING


def execute_paired(
    *,
    baseline_tree: Path,
    patched_tree: Path,
    recipe: InputRecipe,
    scenario: ExecutableScenario,
    oracle: ExecutableOracle,
    check_id: str,
    challenge_id: str,
    patch_hash: str,
    role: str,
    previous_tree: Path | None = None,
    stability_runs: int = 2,
    cache_dir: Path | None = None,
) -> tuple[PairedTraceBundle, bool]:
    baseline_overlay: tuple[str, ...] = ()
    patched_overlay = diff_between(baseline_tree, patched_tree).changed_files
    previous_overlay = (
        diff_between(baseline_tree, previous_tree).changed_files
        if previous_tree is not None else ()
    )
    cache_key = content_hash({
        "baseline_tree": tree_hash(baseline_tree),
        "patched_tree": tree_hash(patched_tree),
        "previous_tree": tree_hash(previous_tree) if previous_tree else None,
        "recipe": recipe,
        "oracle": oracle,
        "check_id": check_id,
        "challenge_id": challenge_id,
        "patch_hash": patch_hash,
        "role": role,
        "runs": stability_runs,
        "execution_backend": os.environ.get("REACHPATCH_EXECUTION_IMAGE", "HOST"),
    })
    cached = _hot_get(cache_key)
    if cached is not None:
        return cached, True
    cache_path = _disk_cache_path(cache_dir, cache_key)
    if cache_path is not None:
        cached = _load_disk_cache(cache_path, cache_key)
        if cached is not None:
            _hot_put(cache_key, cached)
            return cached, True
    baseline_runs = [run_trace(
        baseline_tree, scenario.command, cwd=scenario.cwd,
        environment=scenario.environment, timeout_seconds=scenario.timeout_seconds,
        trace_enabled=index == 0,
        overlay_paths=baseline_overlay,
    ) for index in range(stability_runs)]
    patched_runs = [run_trace(
        patched_tree, scenario.command, cwd=scenario.cwd,
        environment=scenario.environment, timeout_seconds=scenario.timeout_seconds,
        trace_enabled=index == 0,
        overlay_paths=patched_overlay,
    ) for index in range(stability_runs)]
    def stable_signature(item):
        observation = item.observation
        def normalize(value: str) -> str:
            value = re.sub(r"\x1b\[[0-9;]*m", "", value)
            value = re.sub(r"\b(?:in\s+)?\d+(?:\.\d+)?s\b", "<duration>", value)
            return value
        expected = oracle.expected
        observed = {
            "status": observation.status,
            "return_code": observation.return_code,
        }
        if isinstance(expected, dict):
            if "stdout" in expected:
                observed["stdout"] = normalize(observation.stdout)
            if "stderr" in expected:
                observed["stderr"] = normalize(observation.stderr)
            if "value" in expected:
                observed["value"] = observation.value
            if "exception" in expected:
                observed["exception"] = observation.exception
        else:
            observed.update({
                "stdout": normalize(observation.stdout),
                "stderr": normalize(observation.stderr),
                "value": observation.value,
                "exception": observation.exception,
            })
        return content_hash(observed)
    baseline_stable = len({stable_signature(item) for item in baseline_runs}) == 1
    patched_stable = len({stable_signature(item) for item in patched_runs}) == 1
    baseline = replace(
        baseline_runs[-1],
        stable_runs=stability_runs if baseline_stable else 1,
        executed_symbol_ids=baseline_runs[0].executed_symbol_ids,
        executed_path_ids=baseline_runs[0].executed_path_ids,
        executed_line_ids=baseline_runs[0].executed_line_ids,
        first_project_frame=baseline_runs[0].first_project_frame,
    )
    patched = replace(
        patched_runs[-1],
        stable_runs=stability_runs if patched_stable else 1,
        executed_symbol_ids=patched_runs[0].executed_symbol_ids,
        executed_path_ids=patched_runs[0].executed_path_ids,
        executed_line_ids=patched_runs[0].executed_line_ids,
        first_project_frame=patched_runs[0].first_project_frame,
    )
    effective_oracle = oracle
    if (
        role == "PRESERVATION"
        and (not oracle.trusted or not oracle.executable)
        and baseline_stable
        and baseline.observation.status is OutcomeStatus.PASS
    ):
        effective_oracle = ExecutableOracle(
            oracle_id=stable_id(
                "oracle", "stable-baseline", recipe.recipe_id,
                baseline.observation,
            ),
            authority="C",
            relation="patched observation preserves stable baseline observation",
            expected=baseline.observation,
            executable=True,
            source_evidence_ids=(baseline.trace_bundle_id,),
        )
    previous = None
    if previous_tree is not None:
        previous = run_trace(
            previous_tree, scenario.command, cwd=scenario.cwd,
            environment=scenario.environment, timeout_seconds=scenario.timeout_seconds,
            trace_enabled=False,
            overlay_paths=previous_overlay,
        )
        previous = replace(
            previous,
            observation=replace(
                previous.observation,
                status=observe_oracle(effective_oracle, previous.observation),
            ),
        )
    baseline = replace(
        baseline,
        observation=replace(
            baseline.observation,
            status=observe_oracle(effective_oracle, baseline.observation),
        ),
    )
    patched = replace(
        patched,
        observation=replace(
            patched.observation,
            status=observe_oracle(effective_oracle, patched.observation),
        ),
    )
    classification = _classification(baseline.observation, patched.observation, role)
    if (
        previous is not None
        and previous.observation.status is OutcomeStatus.PASS
        and patched.observation.status is OutcomeStatus.FAIL
    ):
        classification = (
            PairClassification.PRESERVATION_REGRESSION
            if role == "PRESERVATION" else PairClassification.TARGET_REGRESSED
        )
    if not effective_oracle.executable or not effective_oracle.trusted:
        classification = PairClassification.UNKNOWN
    paired_id = stable_id(
        "paired-trace", cache_key, baseline.trace_bundle_id,
        patched.trace_bundle_id, effective_oracle.oracle_id, classification,
    )
    bundle = PairedTraceBundle(
        paired_bundle_id=paired_id,
        check_id=check_id,
        challenge_id=challenge_id,
        patch_hash=patch_hash,
        baseline=baseline,
        patched=patched,
        classification=classification,
        oracle_id=effective_oracle.oracle_id,
        oracle_authority=effective_oracle.authority,
        expected_relation=effective_oracle.relation,
        stable_runs=min(baseline.stable_runs, patched.stable_runs),
        previous=previous,
    )
    if cache_path is not None:
        _store_disk_cache(cache_path, cache_key, bundle)
    _hot_put(cache_key, bundle)
    return bundle, False
