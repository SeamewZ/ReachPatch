from __future__ import annotations

import dataclasses
import enum
import json
import os
import shutil
import tempfile
import types
import typing
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

from reachpatch.challenge_graph.models import open_high_challenge_ids
from reachpatch.execution.worktree import copy_source_tree, diff_between, tree_hash
from reachpatch.models.base import canonical_json, content_hash, stable_id
from reachpatch.models.evidence import (
    ConfirmedFailure, CounterexamplePacket, FailureHistory, LockedCheckSet,
    ObservationBundle,
)
from reachpatch.models.graphs import (
    BindingGraph, ChallengeGraph, GraphStack, ProgramGraph, RequirementGraph,
)
from reachpatch.models.reach_avoid import (
    CheckpointEvidence, CheckpointRuntimeState, GeneratorSession,
    ReachAvoidPhase, ReachAvoidState, RepairObjective, StateCheckpoint,
    TrialTransition,
)


SCHEMA_NAME = "reachpatch-reach-avoid-v2"
T = TypeVar("T")


class IncompatibleArtifactError(RuntimeError):
    """Raised when a checkpoint predates the integrated architecture."""


def _decode(value: Any, annotation):
    if annotation is Any or annotation is typing.Any:
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {typing.Union, types.UnionType}:
        if value is None and type(None) in args:
            return None
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _decode(value, candidate)
            except (TypeError, ValueError, KeyError):
                continue
        raise TypeError(f"cannot decode union {annotation}")
    if origin is tuple:
        item_type = args[0] if args else Any
        if len(args) > 1 and args[-1] is not Ellipsis:
            return tuple(_decode(item, kind) for item, kind in zip(value, args))
        return tuple(_decode(item, item_type) for item in value)
    if origin is list:
        item_type = args[0] if args else Any
        return [_decode(item, item_type) for item in value]
    if origin is set:
        item_type = args[0] if args else Any
        return {_decode(item, item_type) for item in value}
    if origin is dict:
        key_type, value_type = args or (Any, Any)
        return {
            _decode(key, key_type): _decode(item, value_type)
            for key, item in value.items()
        }
    if annotation is Path:
        return Path(value)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return annotation(value)
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        hints = get_type_hints(annotation)
        return annotation(**{
            field.name: _decode(value[field.name], hints.get(field.name, Any))
            for field in dataclasses.fields(annotation)
            if field.name in value
        })
    if annotation in {str, int, float, bool}:
        return annotation(value)
    return value


def record_from_dict(cls: type[T], value: dict[str, Any]) -> T:
    return _decode(value, cls)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(canonical_json(value))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class CheckpointStore:
    def __init__(self, run_root: Path) -> None:
        self.root = run_root.resolve() / "checkpoint_store"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, checkpoint_id: str) -> Path:
        return self.root / checkpoint_id

    def load(self, checkpoint_id: str) -> StateCheckpoint:
        path = self.path(checkpoint_id) / "checkpoint.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != SCHEMA_NAME:
            raise IncompatibleArtifactError(
                "Artifact was produced by the retired pre-integrated architecture."
            )
        return record_from_dict(StateCheckpoint, raw["checkpoint"])

    def graph_stack(self, checkpoint: StateCheckpoint) -> GraphStack:
        root = self.path(checkpoint.checkpoint_id)
        requirement = record_from_dict(
            RequirementGraph, json.loads((root / "requirement_graph.json").read_text(encoding="utf-8")),
        )
        program = record_from_dict(
            ProgramGraph, json.loads((root / "program_graph.json").read_text(encoding="utf-8")),
        )
        binding = record_from_dict(
            BindingGraph, json.loads((root / "binding_graph.json").read_text(encoding="utf-8")),
        )
        challenge = record_from_dict(
            ChallengeGraph, json.loads((root / "challenge_graph.json").read_text(encoding="utf-8")),
        )
        stack = GraphStack(
            checkpoint.patch_hash, checkpoint.revision,
            requirement, program, binding, challenge,
        )
        stack.validate()
        if stack.graph_hashes() != checkpoint.graph_hashes:
            raise RuntimeError("checkpoint graph hash mismatch")
        return stack

    def observations(self, checkpoint_id: str) -> ObservationBundle:
        raw = json.loads((self.path(checkpoint_id) / "observations.json").read_text(encoding="utf-8"))
        return record_from_dict(ObservationBundle, raw)

    def counterexamples(self, checkpoint_id: str) -> list[CounterexamplePacket]:
        raw = json.loads((self.path(checkpoint_id) / "counterexamples.json").read_text(encoding="utf-8"))
        return [record_from_dict(CounterexamplePacket, item) for item in raw]

    def locked_checks(self, checkpoint_id: str) -> LockedCheckSet:
        raw = json.loads((self.path(checkpoint_id) / "locked_checks.json").read_text(encoding="utf-8"))
        return record_from_dict(LockedCheckSet, raw)

    def runtime_state(self, checkpoint_id: str) -> CheckpointRuntimeState:
        raw = json.loads(
            (self.path(checkpoint_id) / "runtime_state.json").read_text(encoding="utf-8")
        )
        return record_from_dict(CheckpointRuntimeState, raw)

    def validate(self, checkpoint: StateCheckpoint, base_repository: Path) -> None:
        loaded = self.load(checkpoint.checkpoint_id)
        if loaded != checkpoint:
            raise RuntimeError("checkpoint metadata mismatch")
        snapshot = Path(checkpoint.snapshot_tree)
        if not snapshot.is_dir():
            raise FileNotFoundError(snapshot)
        actual = diff_between(base_repository, snapshot)
        if actual.patch_hash != checkpoint.patch_hash:
            raise RuntimeError("checkpoint patch hash mismatch")
        if actual.canonical_diff != checkpoint.canonical_diff:
            raise RuntimeError("checkpoint canonical diff mismatch")
        raw = json.loads(
            (self.path(checkpoint.checkpoint_id) / "checkpoint.json").read_text(
                encoding="utf-8",
            )
        )
        if raw.get("tree_hash") != tree_hash(snapshot):
            raise RuntimeError("checkpoint working tree hash mismatch")
        expected_root = self.path(checkpoint.checkpoint_id)
        if Path(checkpoint.graph_snapshot_dir) != expected_root:
            raise RuntimeError("checkpoint graph snapshot directory mismatch")
        if not checkpoint.status:
            raise RuntimeError("checkpoint status is empty")
        stack = self.graph_stack(checkpoint)
        self.observations(checkpoint.checkpoint_id)
        counterexamples = self.counterexamples(checkpoint.checkpoint_id)
        locked = self.locked_checks(checkpoint.checkpoint_id)
        if checkpoint.locked_check_ids != locked.all_ids():
            raise RuntimeError("checkpoint locked check set mismatch")
        if checkpoint.open_counterexample_ids != _open_counterexample_ids(
            stack, counterexamples,
        ):
            raise RuntimeError("checkpoint open counterexample set mismatch")
        open_high = open_high_challenge_ids(
            stack.challenge_graph.active_cells()
        )
        if checkpoint.open_high_challenge_ids != open_high:
            raise RuntimeError("checkpoint open high Challenge set mismatch")
        self.runtime_state(checkpoint.checkpoint_id)


def _runtime_state(state: ReachAvoidState | None) -> CheckpointRuntimeState:
    if state is None:
        return CheckpointRuntimeState(
            confirmed_failures=(),
            failure_history={},
            generator_session=GeneratorSession("checkpoint-bootstrap"),
            current_repair_objective=None,
            repair_revision_count=0,
            generator_attempt_count=0,
            challenge_round_count=0,
            no_progress_generator_attempts=0,
            frontier_attempts={},
            phase=ReachAvoidPhase.GRAPH_SYNC,
            termination_status=None,
            execution_budget_seconds=0.0,
            remaining_wall_seconds=0.0,
        )
    return record_from_dict(CheckpointRuntimeState, {
        "confirmed_failures": [item.to_dict() for item in state.confirmed_failures],
        "failure_history": {
            key: value.to_dict() for key, value in state.failure_history.items()
        },
        "generator_session": state.generator_session.to_dict(),
        "current_repair_objective": (
            state.current_repair_objective.to_dict()
            if state.current_repair_objective is not None else None
        ),
        "repair_revision_count": state.repair_revision_count,
        "generator_attempt_count": state.generator_attempt_count,
        "challenge_round_count": state.challenge_round_count,
        "no_progress_generator_attempts": state.no_progress_generator_attempts,
        "frontier_attempts": dict(state.frontier_attempts),
        "phase": state.phase.value,
        "termination_status": state.termination_status,
        "execution_budget_seconds": state.execution_budget_seconds,
        "remaining_wall_seconds": state.remaining_wall_seconds,
        "repair_frontiers": {
            key: value.to_dict() for key, value in state.repair_frontiers.items()
        },
        "challenge_attempts": dict(state.challenge_attempts),
        "transition_counts": dict(state.transition_counts),
        "last_mechanical_result": (
            state.last_mechanical_result.to_dict()
            if state.last_mechanical_result is not None else None
        ),
        "atomic_obligations": {
            key: value.to_dict() for key, value in state.atomic_obligations.items()
        },
        "atomic_evidence": {
            key: value.to_dict() for key, value in state.atomic_evidence.items()
        },
        "probe_registrations": {
            key: value.to_dict()
            for key, value in state.probe_registrations.items()
        },
        "consecutive_provisional_without_progress": state.consecutive_provisional_without_progress,
    })


def checkpoint_identity(
    parent_id: str | None,
    patch_hash: str,
    graph_hashes: dict[str, str],
    status: str,
    evidence: CheckpointEvidence,
    locked_checks: LockedCheckSet,
    observations: ObservationBundle,
    counterexamples: list[CounterexamplePacket],
    runtime_state: CheckpointRuntimeState,
    source_tree_hash: str,
) -> str:
    return stable_id(
        "checkpoint", parent_id, patch_hash, graph_hashes, status, evidence,
        locked_checks, observations, counterexamples, runtime_state,
        source_tree_hash,
    )


def _open_counterexample_ids(
    graph_stack: GraphStack,
    counterexamples: list[CounterexamplePacket],
) -> tuple[str, ...]:
    cells = graph_stack.challenge_graph.cells
    return tuple(sorted(
        item.counterexample_id
        for item in counterexamples
        if item.patch_hash == graph_stack.patch_hash
        and item.challenge_id in cells
        and cells[item.challenge_id].terminal_status.value == "FAIL"
    ))


def _write_snapshot(
    *,
    store: CheckpointStore,
    source_tree: Path,
    parent_id: str | None,
    patch_hash: str,
    canonical_diff: str,
    graph_stack: GraphStack,
    evidence: CheckpointEvidence,
    locked_checks: LockedCheckSet,
    observations: ObservationBundle,
    counterexamples: list[CounterexamplePacket],
    runtime_state: CheckpointRuntimeState,
    status: str,
    revision: int,
) -> StateCheckpoint:
    graph_stack.validate()
    source_tree_hash = tree_hash(source_tree)
    checkpoint_id = checkpoint_identity(
        parent_id, patch_hash, graph_stack.graph_hashes(), status, evidence,
        locked_checks, observations, counterexamples, runtime_state,
        source_tree_hash,
    )
    final = store.path(checkpoint_id)
    if final.exists():
        return store.load(checkpoint_id)
    temporary = Path(tempfile.mkdtemp(prefix=f".{checkpoint_id}.", dir=store.root))
    snapshot = final / "working_tree"
    checkpoint = StateCheckpoint(
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id=parent_id,
        snapshot_tree=str(snapshot),
        patch_hash=patch_hash,
        canonical_diff=canonical_diff,
        graph_hashes=graph_stack.graph_hashes(),
        graph_snapshot_dir=str(final),
        evidence=evidence,
        locked_check_ids=locked_checks.all_ids(),
        open_counterexample_ids=_open_counterexample_ids(graph_stack, counterexamples),
        open_high_challenge_ids=open_high_challenge_ids(
            graph_stack.challenge_graph.active_cells()
        ),
        status=status,
        revision=revision,
    )
    try:
        _atomic_json(temporary / "checkpoint.json", {
            "schema": SCHEMA_NAME,
            "checkpoint": checkpoint.to_dict(),
            "tree_hash": source_tree_hash,
        })
        _atomic_json(temporary / "requirement_graph.json", graph_stack.requirement_graph)
        _atomic_json(temporary / "program_graph.json", graph_stack.program_graph)
        _atomic_json(temporary / "binding_graph.json", graph_stack.binding_graph)
        _atomic_json(temporary / "challenge_graph.json", graph_stack.challenge_graph)
        _atomic_json(temporary / "observations.json", observations)
        _atomic_json(temporary / "counterexamples.json", counterexamples)
        _atomic_json(temporary / "locked_checks.json", locked_checks)
        _atomic_json(temporary / "runtime_state.json", runtime_state)
        # Checkpoint trees are immutable. Hard links keep repeated Challenge
        # checkpoints content-addressed without duplicating repository data;
        # generator and trial trees are still ordinary copies and cannot
        # mutate a historical snapshot through a shared inode.
        copy_source_tree(
            source_tree,
            temporary / "working_tree",
            hardlink_files=True,
        )
        written_hashes = {
            "requirement": record_from_dict(RequirementGraph, json.loads(
                (temporary / "requirement_graph.json").read_text(encoding="utf-8")
            )).graph_hash(),
            "program": record_from_dict(ProgramGraph, json.loads(
                (temporary / "program_graph.json").read_text(encoding="utf-8")
            )).graph_hash(),
            "binding": record_from_dict(BindingGraph, json.loads(
                (temporary / "binding_graph.json").read_text(encoding="utf-8")
            )).graph_hash(),
            "challenge": record_from_dict(ChallengeGraph, json.loads(
                (temporary / "challenge_graph.json").read_text(encoding="utf-8")
            )).graph_hash(),
        }
        if written_hashes != checkpoint.graph_hashes:
            raise RuntimeError("serialized graph hash mismatch")
        os.replace(temporary, final)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return checkpoint


def capture_initial_checkpoint(
    *,
    store: CheckpointStore,
    base_repository: Path,
    source_tree: Path,
    graph_stack: GraphStack,
    evidence: CheckpointEvidence,
    locked_checks: LockedCheckSet,
    observations: ObservationBundle,
    status: str,
    revision: int = 0,
    state: ReachAvoidState | None = None,
) -> StateCheckpoint:
    actual = diff_between(base_repository, source_tree)
    checkpoint = _write_snapshot(
        store=store, source_tree=source_tree, parent_id=None,
        patch_hash=actual.patch_hash, canonical_diff=actual.canonical_diff,
        graph_stack=graph_stack, evidence=evidence, locked_checks=locked_checks,
        observations=observations, counterexamples=[],
        runtime_state=_runtime_state(state), status=status, revision=revision,
    )
    store.validate(checkpoint, base_repository)
    return checkpoint


def capture_checkpoint(
    state: ReachAvoidState,
    trial: TrialTransition,
    store: CheckpointStore,
    status: str,
) -> StateCheckpoint:
    if not trial.trial_tree:
        raise ValueError("transition has no trial tree")
    observations = record_from_dict(ObservationBundle, state.observations.to_dict())
    if trial.challenge_result:
        for execution in trial.challenge_result.executions:
            cell = trial.graph_stack.challenge_graph.cells.get(execution.challenge_id)
            if cell:
                observations.record(execution, cell.requirement_id)
    by_counterexample = {item.counterexample_id: item for item in state.counterexamples}
    if trial.challenge_result:
        by_counterexample.update(
            (item.counterexample_id, item) for item in trial.challenge_result.counterexamples
        )
    counterexamples = list(by_counterexample.values())
    evidence = CheckpointEvidence(
        mechanical_pass=trial.evidence.mechanical.passed,
        no_known_preservation_regression=not trial.evidence.preservation_regressions,
        confirmed_target_pass_count=len(trial.evidence.target_pass_ids_after),
        closed_confirmed_failure_count=(
            state.working_checkpoint.evidence.closed_confirmed_failure_count
            + len(trial.evidence.confirmed_failures_closed)
        ),
        execution_confirmed_requirement_count=len({
            unit.requirement_id for unit in trial.graph_stack.binding_graph.units.values()
            if unit.status.execution_confirmed
        }),
        execution_confirmed_binding_count=sum(
            unit.status.execution_confirmed
            for unit in trial.graph_stack.binding_graph.units.values()
        ),
        open_high_challenge_count=len(open_high_challenge_ids(
            trial.graph_stack.challenge_graph.active_cells()
        )),
        open_counterexample_count=len(_open_counterexample_ids(
            trial.graph_stack, counterexamples,
        )),
    )
    checkpoint = _write_snapshot(
        store=store,
        source_tree=Path(trial.trial_tree),
        parent_id=state.working_checkpoint.checkpoint_id,
        patch_hash=trial.cumulative_diff.patch_hash,
        canonical_diff=trial.cumulative_diff.canonical_diff,
        graph_stack=trial.graph_stack,
        evidence=evidence,
        locked_checks=state.locked_checks,
        observations=observations,
        counterexamples=counterexamples,
        runtime_state=_runtime_state(state),
        status=status,
        revision=state.repair_revision_count,
    )
    store.validate(checkpoint, state.base_repository)
    return checkpoint


def capture_current_graph_checkpoint(
    state: ReachAvoidState,
    store: CheckpointStore,
    status: str,
) -> StateCheckpoint:
    cells = state.graph_stack.challenge_graph.active_cells()
    checkpoint_evidence = CheckpointEvidence(
        mechanical_pass=state.working_checkpoint.evidence.mechanical_pass,
        no_known_preservation_regression=not any(
            cell.kind == "PRESERVATION" and cell.terminal_status.value == "FAIL"
            for cell in cells
        ),
        confirmed_target_pass_count=len({
            cell.requirement_id for cell in cells
            if cell.kind != "PRESERVATION"
            and cell.terminal_status.value == "PASS" and cell.stability_runs >= 2
        }),
        closed_confirmed_failure_count=sum(not item.open for item in state.confirmed_failures),
        execution_confirmed_requirement_count=len({
            unit.requirement_id for unit in state.graph_stack.binding_graph.units.values()
            if unit.status.execution_confirmed
        }),
        execution_confirmed_binding_count=sum(
            unit.status.execution_confirmed
            for unit in state.graph_stack.binding_graph.units.values()
        ),
        open_high_challenge_count=len(open_high_challenge_ids(cells)),
        open_counterexample_count=len(_open_counterexample_ids(
            state.graph_stack, state.counterexamples,
        )),
    )
    checkpoint = _write_snapshot(
        store=store,
        source_tree=Path(state.working_checkpoint.snapshot_tree),
        parent_id=state.working_checkpoint.checkpoint_id,
        patch_hash=state.working_checkpoint.patch_hash,
        canonical_diff=state.working_checkpoint.canonical_diff,
        graph_stack=state.graph_stack,
        evidence=checkpoint_evidence,
        locked_checks=state.locked_checks,
        observations=state.observations,
        counterexamples=state.counterexamples,
        runtime_state=_runtime_state(state),
        status=status,
        revision=state.repair_revision_count,
    )
    store.validate(checkpoint, state.base_repository)
    return checkpoint


def restore_checkpoint(
    state: ReachAvoidState,
    checkpoint: StateCheckpoint,
    store: CheckpointStore,
) -> None:
    store.validate(checkpoint, state.base_repository)
    graph_stack = store.graph_stack(checkpoint)
    observations = store.observations(checkpoint.checkpoint_id)
    counterexamples = store.counterexamples(checkpoint.checkpoint_id)
    locked_checks = store.locked_checks(checkpoint.checkpoint_id)
    runtime = store.runtime_state(checkpoint.checkpoint_id)
    if graph_stack.patch_hash != checkpoint.patch_hash:
        raise RuntimeError("restore would mix a patch with stale graphs")
    state.working_checkpoint = checkpoint
    state.graph_stack = graph_stack
    state.observations = observations
    state.counterexamples = counterexamples
    state.locked_checks = locked_checks
    state.confirmed_failures = list(runtime.confirmed_failures)
    state.failure_history = runtime.failure_history
    state.generator_session = runtime.generator_session
    state.current_repair_objective = runtime.current_repair_objective
    state.repair_revision_count = runtime.repair_revision_count
    state.generator_attempt_count = runtime.generator_attempt_count
    state.challenge_round_count = runtime.challenge_round_count
    state.no_progress_generator_attempts = runtime.no_progress_generator_attempts
    state.frontier_attempts = runtime.frontier_attempts
    state.phase = runtime.phase
    state.termination_status = runtime.termination_status
    state.execution_budget_seconds = runtime.execution_budget_seconds
    state.remaining_wall_seconds = runtime.remaining_wall_seconds
    state.repair_frontiers = runtime.repair_frontiers
    state.challenge_attempts = runtime.challenge_attempts
    state.transition_counts = runtime.transition_counts
    state.last_mechanical_result = runtime.last_mechanical_result
    state.atomic_obligations = dict(runtime.atomic_obligations)
    state.atomic_evidence = dict(runtime.atomic_evidence)
    state.probe_registrations = dict(runtime.probe_registrations)
    state.consecutive_provisional_without_progress = (
        runtime.consecutive_provisional_without_progress
    )
