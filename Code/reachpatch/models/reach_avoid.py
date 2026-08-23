from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .base import SerializableRecord
from .evidence import (
    ActualDiff, ConfirmedFailure, CounterexamplePacket, FailureHistory,
    LockedCheckSet, ObservationBundle, PairedTraceBundle,
)
from .graphs import GraphBudget, GraphStack
from reachpatch.reach_avoid.frontier import RepairFrontier


class Decision(StrEnum):
    COMMIT_WORKING = "COMMIT_WORKING"
    KEEP_PROVISIONAL = "KEEP_PROVISIONAL"
    ROLLBACK = "ROLLBACK"
    SEAL = "SEAL"


class ReachAvoidPhase(StrEnum):
    INITIALIZING = "INITIALIZING"
    INITIAL_GENERATION = "INITIAL_GENERATION"
    GRAPH_SYNC = "GRAPH_SYNC"
    CHALLENGE = "CHALLENGE"
    REPAIR = "REPAIR"
    TRANSITION = "TRANSITION"
    SEALED = "SEALED"


class AvoidKind(StrEnum):
    HARD_AVOID = "HARD_AVOID"
    REPAIRABLE_AVOID = "REPAIRABLE_AVOID"
    NOT_AVOID = "NOT_AVOID"


@dataclass(frozen=True, slots=True)
class CheckpointEvidence(SerializableRecord):
    mechanical_pass: bool
    no_known_preservation_regression: bool
    confirmed_target_pass_count: int
    closed_confirmed_failure_count: int
    execution_confirmed_requirement_count: int
    execution_confirmed_binding_count: int
    open_high_challenge_count: int
    open_counterexample_count: int

    def rank(self) -> tuple[int, ...]:
        return (
            int(self.mechanical_pass),
            int(self.no_known_preservation_regression),
            self.confirmed_target_pass_count,
            self.closed_confirmed_failure_count,
            self.execution_confirmed_requirement_count,
            self.execution_confirmed_binding_count,
            -self.open_high_challenge_count,
            -self.open_counterexample_count,
        )


@dataclass(frozen=True, slots=True)
class StateCheckpoint(SerializableRecord):
    checkpoint_id: str
    parent_checkpoint_id: str | None
    snapshot_tree: str
    patch_hash: str
    canonical_diff: str
    graph_hashes: dict[str, str]
    graph_snapshot_dir: str
    evidence: CheckpointEvidence
    locked_check_ids: tuple[str, ...]
    open_counterexample_ids: tuple[str, ...]
    open_high_challenge_ids: tuple[str, ...]
    status: str
    revision: int


@dataclass(slots=True)
class GeneratorSession(SerializableRecord):
    session_id: str
    conversation: list[dict[str, Any]] = field(default_factory=list)
    # Immutable summaries of actual generator attempts.  These are derived
    # from diffs, tool executions, and transition verdicts; model prose is
    # display-only and never used to decide mechanism equality.
    attempt_history: list[dict[str, Any]] = field(default_factory=list)
    structure_recovery_used: bool = False


@dataclass(frozen=True, slots=True)
class RepairObjective(SerializableRecord):
    objective_id: str
    objective_kind: str
    primary_requirement: dict[str, Any]
    related_requirements: tuple[dict[str, Any], ...]
    public_context: tuple[dict[str, Any], ...]
    related_failures: tuple[dict[str, Any], ...]
    counterexamples: tuple[CounterexamplePacket, ...]
    preservation_requirements: tuple[dict[str, Any], ...]
    reproduction_commands: tuple[tuple[str, ...], ...]
    concrete_inputs: tuple[Any, ...]
    input_derivations: tuple[tuple[str, ...], ...]
    oracle_relations: tuple[dict[str, Any], ...]
    observations: tuple[dict[str, Any], ...]
    failure_signatures: tuple[str, ...]
    first_divergences: tuple[Any, ...]
    executed_path_ids: tuple[str, ...]
    guarded_branch_ids: tuple[str, ...]
    causal_guidance: dict[str, Any]
    bindings: tuple[dict[str, Any], ...]
    actual_hunks: tuple[dict[str, Any], ...]
    causal_cuts: tuple[dict[str, Any], ...]
    impact_cone: dict[str, Any] | None
    impact_risks: tuple[str, ...]
    protected_target_ids: tuple[str, ...]
    protected_preservation_ids: tuple[str, ...]
    suggested_action_families: tuple[str, ...]
    locked_check_ids: tuple[str, ...]
    cumulative_diff: str
    failed_mechanisms: tuple[dict[str, Any], ...]
    forbidden_mechanisms: tuple[dict[str, Any], ...]
    editable_source_slices: tuple[dict[str, Any], ...]
    expected_next_effects: tuple[str, ...]
    # Atomic validation records replace the retired parallel command/input/oracle
    # arrays.  The legacy fields above remain readable for old checkpoints, but
    # the controller and DeepSeek tools consume this tuple exclusively.
    validation_obligations: tuple["ValidationObligation", ...] = ()
    selected_frontier: RepairFrontier | None = None
    working_patch_hash: str = ""
    graph_revision: int = 0


@dataclass(frozen=True, slots=True)
class ValidationObligation(SerializableRecord):
    validation_id: str
    role: str
    authority: str
    command: tuple[str, ...]
    cwd: str
    environment: dict[str, str]
    timeout_seconds: int
    backend: str
    concrete_input: Any
    input_derivation: str
    oracle_id: str | None
    expected_relation: str | None
    expected_observation: Any
    requirement_id: str
    binding_id: str | None = None
    challenge_id: str | None = None


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


@dataclass(frozen=True, slots=True)
class MechanicalResult(SerializableRecord):
    passed: bool
    failure_reasons: tuple[str, ...]
    forbidden_edit: bool
    oracle_contamination: bool
    unsafe_api_break: bool
    high_risk_side_effect: bool
    command_results: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ProgressEvaluation(SerializableRecord):
    strict_progress: bool
    causal_progress: bool
    target_pass_delta: int
    hard_requirement_pass_delta: int
    closed_failure_ids: tuple[str, ...]
    closed_counterexample_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReachEvaluation(SerializableRecord):
    reached: bool
    reasons: tuple[str, ...]
    trusted_target_count: int
    stable_target_pass_count: int
    execution_confirmed_target_count: int


@dataclass(frozen=True, slots=True)
class AvoidEvaluation(SerializableRecord):
    kind: AvoidKind
    reasons: tuple[str, ...]
    locked_targets_lost: tuple[str, ...]
    preservation_regressions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TransitionDecision(SerializableRecord):
    decision: Decision
    reasons: tuple[str, ...]
    strict_progress: bool
    causal_progress: bool
    hard_avoid: bool
    repairable_regression: bool
    promote_to_working: bool
    promote_to_best: bool
    next_objective_kind: str | None


@dataclass(frozen=True, slots=True)
class TransitionEvidence(SerializableRecord):
    mechanical: MechanicalResult
    target_pass_ids_before: tuple[str, ...]
    target_pass_ids_after: tuple[str, ...]
    hard_pass_ids_before: tuple[str, ...]
    hard_pass_ids_after: tuple[str, ...]
    confirmed_failures_closed: tuple[str, ...]
    counterexamples_closed: tuple[str, ...]
    counterexamples_opened: tuple[str, ...]
    locked_targets_lost: tuple[str, ...]
    target_regressions: tuple[str, ...]
    preservation_regressions: tuple[str, ...]
    new_executable_frontier: bool
    environment_unknown: bool
    causal_progress_reasons: tuple[str, ...]
    target_failures_closed: tuple[str, ...] = ()
    target_counterexamples_closed: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChallengeSelection(SerializableRecord):
    challenge_ids: tuple[str, ...]
    recovery_actions: tuple[tuple[str, str], ...] = ()
    exhausted: bool = False


@dataclass(frozen=True, slots=True)
class ChallengeRoundResult(SerializableRecord):
    selected_challenge_ids: tuple[str, ...]
    executed_challenge_ids: tuple[str, ...]
    executions: tuple[PairedTraceBundle, ...]
    counterexamples: tuple[CounterexamplePacket, ...]
    confirmed_failures: tuple[ConfirmedFailure, ...]
    updated_graph_stack: GraphStack
    frontiers: tuple[str, ...]
    execution_seconds: float
    cache_hits: int


@dataclass(frozen=True, slots=True)
class RegressionItem(SerializableRecord):
    requirement_id: str
    impact_path_id: str
    binding_id: str
    challenge_id: str
    changed_hunk_id: str


@dataclass(frozen=True, slots=True)
class RegressionPlan(SerializableRecord):
    challenge_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    impact_path_ids: tuple[str, ...]
    changed_hunk_ids: tuple[str, ...]
    items: tuple[RegressionItem, ...] = ()


@dataclass(slots=True)
class TrialTransition(SerializableRecord):
    source_checkpoint_id: str
    trial_tree: str | None
    incremental_diff: ActualDiff
    cumulative_diff: ActualDiff
    graph_stack: GraphStack
    challenge_result: ChallengeRoundResult | None
    evidence: TransitionEvidence
    progress: ProgressEvaluation
    reach: ReachEvaluation
    avoid: AvoidEvaluation
    transition_decision: TransitionDecision
    certificate: TransitionCertificate | None = None
    trial_patch_changed: bool = False
    entered_evaluation: bool = False

    @property
    def decision(self) -> Decision:
        return self.transition_decision.decision


@dataclass(frozen=True, slots=True)
class TransitionCertificate(SerializableRecord):
    transition_id: str
    source_checkpoint_id: str
    trial_checkpoint_id: str | None
    result_checkpoint_id: str
    incremental_diff_hash: str
    cumulative_diff_hash: str
    trial_patch_hash: str
    trial_patch_changed: bool
    before_graph_hashes: dict[str, str]
    trial_graph_hashes: dict[str, str]
    result_graph_hashes: dict[str, str]
    selected_challenge_ids: tuple[str, ...]
    executed_challenge_ids: tuple[str, ...]
    execution_bundle_ids: tuple[str, ...]
    requirements_improved: tuple[str, ...]
    requirements_regressed: tuple[str, ...]
    bindings_confirmed: tuple[str, ...]
    counterexamples_closed: tuple[str, ...]
    counterexamples_opened: tuple[str, ...]
    locked_targets_lost: tuple[str, ...]
    preservation_regressions: tuple[str, ...]
    hard_avoid_reasons: tuple[str, ...]
    progress: ProgressEvaluation
    reach: ReachEvaluation
    avoid: AvoidEvaluation
    decision: Decision
    decision_reasons: tuple[str, ...]
    repair_revision_count_before: int
    repair_revision_count_after: int
    generator_attempt_count: int
    challenge_round_count: int
    recomputation_hash: str


@dataclass(slots=True)
class ReachAvoidState(SerializableRecord):
    instance_id: str
    run_id: str
    base_repository: Path
    base_commit: str
    run_root: Path
    graph_stack: GraphStack
    working_checkpoint: StateCheckpoint
    best_checkpoint: StateCheckpoint
    certified_checkpoint: StateCheckpoint | None
    checkpoint_history: dict[str, StateCheckpoint]
    observations: ObservationBundle
    counterexamples: list[CounterexamplePacket]
    locked_checks: LockedCheckSet
    confirmed_failures: list[ConfirmedFailure]
    failure_history: dict[str, FailureHistory]
    generator_session: GeneratorSession
    current_repair_objective: RepairObjective | None
    repair_revision_count: int
    generator_attempt_count: int
    challenge_round_count: int
    no_progress_generator_attempts: int
    frontier_attempts: dict[str, int]
    phase: ReachAvoidPhase
    termination_status: str | None
    execution_budget_seconds: float
    remaining_wall_seconds: float
    repair_frontiers: dict[str, RepairFrontier] = field(default_factory=dict)
    graph_budget: GraphBudget = field(default_factory=GraphBudget)
    challenge_attempts: dict[str, int] = field(default_factory=dict)
    transition_counts: dict[str, int] = field(default_factory=dict)
    last_mechanical_result: MechanicalResult | None = None


@dataclass(frozen=True, slots=True)
class TerminalResult(SerializableRecord):
    instance_id: str
    run_id: str
    status: str
    checkpoint_id: str
    patch_hash: str
    unified_diff: str
    output_path: str


@dataclass(frozen=True, slots=True)
class PerformanceRecord(SerializableRecord):
    revision: int
    program_update_seconds: float
    requirement_update_seconds: float
    binding_update_seconds: float
    challenge_materialization_seconds: float
    challenge_execution_seconds: float
    deepseek_seconds: float
    peak_rss_kb: int
    cache_hit_count: int
    files_reparsed: int
    symbols_expanded: int


@dataclass(frozen=True, slots=True)
class CheckpointRuntimeState(SerializableRecord):
    confirmed_failures: tuple[ConfirmedFailure, ...]
    failure_history: dict[str, FailureHistory]
    generator_session: GeneratorSession
    current_repair_objective: RepairObjective | None
    repair_revision_count: int
    generator_attempt_count: int
    challenge_round_count: int
    no_progress_generator_attempts: int
    frontier_attempts: dict[str, int]
    phase: ReachAvoidPhase
    termination_status: str | None
    execution_budget_seconds: float
    remaining_wall_seconds: float
    repair_frontiers: dict[str, RepairFrontier] = field(default_factory=dict)
    challenge_attempts: dict[str, int] = field(default_factory=dict)
    transition_counts: dict[str, int] = field(default_factory=dict)
