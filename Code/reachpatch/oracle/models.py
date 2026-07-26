from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reachpatch.models.base import SerializableRecord
from reachpatch.models.enums import Authority, OracleLifecycle, OutcomeStatus


@dataclass(frozen=True, slots=True)
class ObservationContract(SerializableRecord):
    contract_id: str
    channels: tuple[str, ...]
    normalize_return: bool = True
    exception_type: bool = True
    exception_message_category: bool = True
    exception_phase: bool = True
    object_type: bool = True
    object_fields: tuple[str, ...] = ()
    abstract_shape: bool = True
    visible_state_keys: tuple[str, ...] = ()
    capture_calls: bool = False
    capture_protocol_selection: bool = False
    capture_side_effects: bool = True
    multi_trace_relation: str | None = None


@dataclass(frozen=True, slots=True)
class Oracle(SerializableRecord):
    oracle_id: str
    input_domain: dict[str, Any]
    observation_channels: tuple[str, ...]
    relation: dict[str, Any]
    authority: Authority
    authority_source: str
    evidence_ids: tuple[str, ...]
    evidence_cluster_id: str
    applicability_condition: str
    counterexample_condition: str
    unknown_condition: str
    stability_repeats: int
    disagreement_repeat: int
    lifecycle: OracleLifecycle
    executable: bool
    kind: str

    @property
    def active_and_trusted(self) -> bool:
        return (
            self.authority.trusted
            and self.lifecycle == OracleLifecycle.ACTIVE
            and self.executable
        )


@dataclass(frozen=True, slots=True)
class ExecutableScenario(SerializableRecord):
    scenario_id: str
    binding_unit_id: str
    assignment_scope: str
    setup: tuple[dict[str, Any], ...]
    stimulus: tuple[dict[str, Any], ...]
    observe: ObservationContract
    oracle: Oracle
    evidence_ids: tuple[str, ...]
    isolation: dict[str, Any]
    timeout_seconds: float
    kind: str
    source_hashes: dict[str, str]
    evidence_cluster_id: str


@dataclass(frozen=True, slots=True)
class RunObservation(SerializableRecord):
    execution_id: str
    environment_signature: str
    stage: str
    observation_reached: bool
    mechanical_failure: bool
    setup_failure: bool
    dependency_failure: bool
    global_timeout: bool
    status: OutcomeStatus
    channels: dict[str, Any]
    observation_schema: tuple[str, ...]
    raw_stdout: str
    raw_stderr: str
    repeat_index: int
    source_hash: str


@dataclass(frozen=True, slots=True)
class OracleEvaluation(SerializableRecord):
    status: OutcomeStatus
    reason: str
    expected: Any
    actual: Any
    channel: str | None


@dataclass(frozen=True, slots=True)
class PairClassification(SerializableRecord):
    status: OutcomeStatus
    reason: str
    base_evaluation: OracleEvaluation | None
    patch_evaluation: OracleEvaluation | None
    comparable: bool
    failure_origin: str
