from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reachpatch.models.base import SerializableRecord
from reachpatch.models.enums import OutcomeStatus
from reachpatch.oracle.models import PairClassification, RunObservation
from reachpatch.program_graph.tracing import DynamicTraceEvent


@dataclass(frozen=True, slots=True)
class ExecutionRun(SerializableRecord):
    run: RunObservation
    trace_events: tuple[DynamicTraceEvent, ...]
    state_snapshots: tuple[dict[str, Any], ...]
    side_effects: tuple[dict[str, Any], ...]
    object_shapes: tuple[dict[str, Any], ...]
    duration_seconds: float
    worker_status: str
    raw_result_hash: str


@dataclass(frozen=True, slots=True)
class TraceBundle(SerializableRecord):
    bundle_id: str
    recipe_id: str
    repository_role: str
    runs: tuple[ExecutionRun, ...]
    stability_status: str
    stable_status: OutcomeStatus
    environment_signature: str
    source_hash: str
    unresolved_reason: str | None


@dataclass(frozen=True, slots=True)
class PairedTraceBundle(SerializableRecord):
    paired_bundle_id: str
    recipe_id: str
    scenario_id: str
    base_bundle: TraceBundle
    patch_bundle: TraceBundle
    classifications: tuple[PairClassification, ...]
    status: OutcomeStatus
    stability_status: str
    first_divergence: dict[str, Any] | None
