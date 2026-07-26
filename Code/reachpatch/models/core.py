from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import SerializableRecord, stable_id
from .enums import Authority, Confidence, EvidenceKind, OutcomeStatus


@dataclass(frozen=True, slots=True)
class SourceSpan(SerializableRecord):
    source: str
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0

    def __post_init__(self) -> None:
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("invalid source span lines")


@dataclass(frozen=True, slots=True)
class Instance(SerializableRecord):
    instance_id: str
    repository: str
    base_commit: str
    issue: str
    visible_tests: tuple[str, ...] = ()
    public_metadata: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)

    def repository_path(self) -> Path:
        path = Path(self.repository).resolve()
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path


@dataclass(frozen=True, slots=True)
class Evidence(SerializableRecord):
    evidence_id: str
    kind: EvidenceKind
    source: str
    content: str
    source_span: SourceSpan | None
    independence_cluster: str
    extraction_rule: str
    content_hash: str
    authority: Authority = Authority.PROVISIONAL
    confidence: Confidence = Confidence.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Frontier(SerializableRecord):
    frontier_id: str
    kind: str
    owner_id: str
    reason: str
    resolution_action: str
    hard: bool
    evidence_ids: tuple[str, ...] = ()
    non_reachability_proof_id: str | None = None
    status: str = "OPEN"


@dataclass(frozen=True, slots=True)
class Scenario(SerializableRecord):
    environment: dict[str, Any]
    configuration: dict[str, Any]
    pre_state: dict[str, Any]
    inputs: dict[str, Any]
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ExecutionTrace(SerializableRecord):
    calls: tuple[dict[str, Any], ...]
    data_flow: tuple[dict[str, Any], ...]
    state_trajectory: tuple[dict[str, Any], ...]
    outputs: dict[str, Any]
    exception: dict[str, Any] | None
    temporal_resources: dict[str, Any]
    trace_hash: str


@dataclass(frozen=True, slots=True)
class ExecutionOutcome(SerializableRecord):
    status: OutcomeStatus
    origin: str
    observation: dict[str, Any]
    execution_id: str
    stable: bool
    comparable: bool
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        status: OutcomeStatus,
        origin: str,
        observation: dict[str, Any],
        *,
        stable: bool,
        comparable: bool,
        details: dict[str, Any] | None = None,
    ) -> "ExecutionOutcome":
        execution_id = stable_id("execution", status, origin, observation, stable, comparable, details)
        return cls(
            status=status,
            origin=origin,
            observation=observation,
            execution_id=execution_id,
            stable=stable,
            comparable=comparable,
            details=dict(details or {}),
        )
