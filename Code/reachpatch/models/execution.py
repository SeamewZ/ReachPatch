from __future__ import annotations

"""Graph-free records used by the production execution repair loop.

Historical graph artifacts are decoded by :mod:`reachpatch.models.reach_avoid`.
Nothing in this module imports a requirement, program, binding, or challenge
graph, which makes it a suitable dependency boundary for production control.
"""

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

from reachpatch.reach_avoid.dynamic_failure_graph import DynamicFailureGraph

from .base import SerializableRecord


class CheckRole(StrEnum):
    TARGET = "TARGET"
    PRESERVATION = "PRESERVATION"
    CHALLENGE = "CHALLENGE"
    MECHANICAL = "MECHANICAL"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class ActiveFailureKind(StrEnum):
    MECHANICAL = "MECHANICAL"
    PRESERVATION = "PRESERVATION"
    TARGET = "TARGET"
    CHALLENGE = "CHALLENGE"


class FailureStage(IntEnum):
    PATCH_OR_SYNTAX_BLOCKER = 0
    IMPORT_OR_NAME_BLOCKER = 1
    PRE_TARGET_RUNTIME_BLOCKER = 2
    TARGET_CONTRACT_FAILURE = 3
    TARGET_PASS = 4


class TransitionDecision(StrEnum):
    REACHED = "REACHED"
    ADVANCE_SAFE = "ADVANCE_SAFE"
    KEEP_REPAIRING = "KEEP_REPAIRING"
    REJECT_TRIAL = "REJECT_TRIAL"


class ReachAvoidPhase(StrEnum):
    INITIALIZING = "INITIALIZING"
    INITIAL_GENERATION = "INITIAL_GENERATION"
    TARGET_RECOVERY = "TARGET_RECOVERY"
    REPAIR = "REPAIR"
    TRANSITION = "TRANSITION"
    SEALED = "SEALED"


@dataclass(frozen=True, slots=True)
class EvidenceSpan(SerializableRecord):
    start: int
    end: int
    quote: str


@dataclass(frozen=True, slots=True)
class GoalContract(SerializableRecord):
    goal_id: str
    operation: str
    target_symbols: tuple[str, ...]
    comparator: str
    expected: Any
    evidence_spans: tuple[EvidenceSpan, ...]
    authority: str
    hard: bool
    unresolved_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutableCheck(SerializableRecord):
    check_id: str
    goal_id: str | None
    role: CheckRole
    authority: str
    command: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    timeout_seconds: float
    comparator: str
    expected: Any
    evidence_ids: tuple[str, ...]
    target_symbols: tuple[str, ...]
    input_recipe: Any

    @property
    def trusted(self) -> bool:
        return self.authority in {"A", "B", "C"}


@dataclass(frozen=True, slots=True)
class CheckExecution(SerializableRecord):
    check_id: str
    status: CheckStatus
    observation: Any
    trace: Any = None
    runs: int = 0
    stable: bool = False
    semantic_signature: str = ""
    entered_project_code: bool = False
    failure_stage: FailureStage | None = None
    distance: float | int | None = None
    # Copied from the grounded check so Reach certification can verify hard
    # goal coverage without consulting graph or prose state.
    goal_id: str | None = None
    # Grounding metadata copied from the executable check at run time. These
    # remain optional so historical/toy positional fixtures still deserialize;
    # when present they are enforced by Reach certification.
    role: CheckRole | str | None = None
    authority: str | None = None


@dataclass(frozen=True, slots=True)
class ActiveFailure(SerializableRecord):
    failure_id: str
    kind: ActiveFailureKind
    check_id: str
    goal_id: str | None
    command: tuple[str, ...]
    comparator: str
    expected: Any
    actual: Any
    stdout: str
    stderr: str
    exit_code: int | None
    exception: str | None
    traceback_frames: tuple[str, ...]
    entered_project_code: bool
    first_project_frame: str | None
    failure_stage: FailureStage | None
    signature: str
    same_signature_count: int
    authority: str


@dataclass(frozen=True, slots=True)
class FailureHistory(SerializableRecord):
    signature: str
    count: int
    check_id: str
    last_patch_hash: str = ""
    last_observation_hash: str = ""


@dataclass(frozen=True, slots=True)
class AtomicProgress(SerializableRecord):
    check_id: str
    parent_status: str
    trial_status: str
    parent_stage: FailureStage | None
    trial_stage: FailureStage | None
    parent_distance: float | int | None
    trial_distance: float | int | None
    strict_progress: bool
    partial_progress: bool
    regression: bool
    reason: str


@dataclass(frozen=True, slots=True)
class MechanicalResult(SerializableRecord):
    passed: bool
    failure_reasons: tuple[str, ...]
    forbidden_edit: bool
    oracle_contamination: bool
    unsafe_api_break: bool
    high_risk_side_effect: bool
    command_results: tuple[dict[str, Any], ...] = ()
    undefined_name_findings: tuple[Any, ...] = ()
    import_smoke_failures: tuple[dict[str, Any], ...] = ()
    static_blocker_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LockedCheck(SerializableRecord):
    check: ExecutableCheck
    passing_observation_hash: str
    patch_hash_when_locked: str

    @property
    def check_id(self) -> str:
        return self.check.check_id


@dataclass(frozen=True, slots=True)
class StateCheckpoint(SerializableRecord):
    checkpoint_id: str
    parent_checkpoint_id: str | None
    snapshot_tree: str
    patch_hash: str
    cumulative_diff: str
    status: str
    revision: int
    patch_is_applicable: bool = True
    repository_corrupted: bool = False
    forbidden_path_changed: bool = False
    final_eligible: bool = False
    mechanical_result_hash: str = ""
    mechanical_blockers: tuple[str, ...] = ()
    target_observation_hashes: dict[str, str] = field(default_factory=dict)
    preservation_observation_hashes: dict[str, str] = field(default_factory=dict)
    challenge_observation_hashes: dict[str, str] = field(default_factory=dict)
    locked_checks: tuple[LockedCheck, ...] = ()
    active_failure: ActiveFailure | None = None
    dynamic_failure_graph_hash: str | None = None
    working_tree_hash: str = ""
    transition_certificate_id: str | None = None

    @property
    def canonical_diff(self) -> str:
        return self.cumulative_diff

    @property
    def locked_check_ids(self) -> tuple[str, ...]:
        return tuple(item.check_id for item in self.locked_checks)


@dataclass(slots=True)
class GeneratorSession(SerializableRecord):
    session_id: str
    conversation: list[dict[str, Any]] = field(default_factory=list)
    attempt_history: list[dict[str, Any]] = field(default_factory=list)
    structure_recovery_used: bool = False


@dataclass(frozen=True, slots=True)
class GeneratorResult(SerializableRecord):
    result_id: str
    incremental_diff: str
    mechanism: str
    summary: str
    modified_tree: str | None = None
    error_kind: str | None = None
    structure_recovery_attempted: bool = False

    @property
    def has_new_nonempty_diff(self) -> bool:
        return bool(self.incremental_diff.strip()) and self.error_kind is None


@dataclass(slots=True)
class ReachAvoidState(SerializableRecord):
    clean_snapshot: Path
    working_checkpoint: StateCheckpoint
    safe_checkpoint: StateCheckpoint | None
    best_checkpoint: StateCheckpoint | None
    certified_checkpoint: StateCheckpoint | None
    goal_contracts: tuple[GoalContract, ...]
    target_checks: tuple[ExecutableCheck, ...]
    preservation_checks: tuple[ExecutableCheck, ...]
    challenge_checks: tuple[ExecutableCheck, ...]
    locked_checks: tuple[LockedCheck, ...]
    active_failure: ActiveFailure | None
    dynamic_failure_graph: DynamicFailureGraph | None
    failure_history: dict[str, FailureHistory]
    transition_history: list["TransitionCertificate"]
    revision_count: int
    instance_id: str
    run_id: str
    base_repository: Path
    base_commit: str
    run_root: Path
    generator_session: GeneratorSession
    current_repair_objective: Any = None
    generator_attempt_count: int = 0
    phase: ReachAvoidPhase = ReachAvoidPhase.INITIALIZING
    termination_status: str | None = None
    execution_budget_seconds: float = 0.0
    remaining_wall_seconds: float = 0.0
    last_mechanical_result: MechanicalResult | None = None
    target_recovery: Any = None
    distinct_patch_hashes: set[str] = field(default_factory=set)
    rejected_patch_hashes: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class TransitionCertificate(SerializableRecord):
    certificate_id: str
    case_id: str
    revision_index: int
    parent_checkpoint_id: str
    trial_checkpoint_id: str
    result_checkpoint_id: str
    parent_patch_hash: str
    trial_patch_hash: str
    result_patch_hash: str
    decision: TransitionDecision
    active_failure_id: str
    active_failure_kind: str
    exact_failure_command: tuple[str, ...]
    check_ids: tuple[str, ...]
    observation_hashes: tuple[str, ...]
    atomic_progress: dict[str, AtomicProgress]
    mechanical_blockers_before: tuple[str, ...]
    mechanical_blockers_after: tuple[str, ...]
    locked_checks_before: tuple[str, ...]
    locked_checks_after: tuple[str, ...]
    regressions: tuple[str, ...]
    dynamic_failure_graph_hash: str | None
    decision_reason: str
    model_request_ids: tuple[str, ...]
    timestamp: str


@dataclass(frozen=True, slots=True)
class TerminalResult(SerializableRecord):
    instance_id: str
    run_id: str
    status: str
    checkpoint_id: str
    patch_hash: str
    unified_diff: str
    output_path: str
