from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from .base import SerializableRecord, content_hash, stable_id
from .evidence import (
    ActualDiff, ConfirmedFailure, CounterexamplePacket, FailureHistory,
    LockedCheckSet, ObservationBundle, ObservationContract, PairedTraceBundle,
)
from .graphs import GraphBudget, GraphStack, InputRecipe
from reachpatch.reach_avoid.frontier import RepairFrontier


@dataclass(frozen=True, slots=True)
class AtomicObligation(SerializableRecord):
    """One independently executable contract used by Reach--Avoid.

    The key is semantic and intentionally excludes patch/challenge identities so
    incumbent and trial evidence can be compared across revisions.
    """
    key: str
    requirement_id: str
    requirement_contract_id: str
    role: Literal["TARGET", "PRESERVATION", "IMPACT", "MECHANICAL"]
    input_recipe: Any = None
    input_partition_id: str | None = None
    oracle_contract: Any = None
    authority: Literal["A", "B", "C", "PROVISIONAL"] = "PROVISIONAL"
    hard: bool = True
    source: str = "graph"


def normalize_input_recipe_semantics(recipe: Any) -> Any:
    if recipe is None:
        return None
    if hasattr(recipe, "to_dict"):
        recipe = recipe.to_dict()
    if isinstance(recipe, dict):
        return {key: normalize_input_recipe_semantics(value) for key, value in sorted(recipe.items())
                if key not in {"recipe_id", "source_check_id"}}
    if isinstance(recipe, (list, tuple)):
        return tuple(normalize_input_recipe_semantics(item) for item in recipe)
    return recipe


def atomic_obligation_key(obligation: AtomicObligation) -> str:
    contract = obligation.oracle_contract
    if hasattr(contract, "contract_id"):
        contract_id = contract.contract_id
    elif hasattr(contract, "normalized"):
        contract_id = content_hash(contract.normalized())
    else:
        contract_id = content_hash(contract)
    return stable_id(
        "atomic-obligation", obligation.requirement_contract_id, obligation.role,
        normalize_input_recipe_semantics(obligation.input_recipe),
        obligation.input_partition_id, contract_id,
    )


AtomicEvidenceStatus = Literal["PASS", "FAIL", "UNKNOWN", "UNEXECUTABLE", "BLOCKED"]


@dataclass(frozen=True, slots=True)
class AtomicEvidence(SerializableRecord):
    obligation_key: str
    status: AtomicEvidenceStatus
    requirement_id: str = ""
    role: str = "TARGET"
    input_partition_id: str | None = None
    observed_payload: Any = None
    expected_payload: Any = None
    comparator: str = "RELATION_HOLDS"
    stability_runs: int = 0
    entered_project_code: bool = False
    first_project_frame: Any = None
    trace_ids: tuple[str, ...] = ()
    authority: str = "PROVISIONAL"
    command: tuple[str, ...] = ()
    cwd: str = "."
    environment_fingerprint: str = ""
    binding_alignment: str = "UNKNOWN"
    backend: str = "shared-executor"


@dataclass(frozen=True, slots=True)
class ProbeRegistration(SerializableRecord):
    """A sandboxed probe promoted into the Reach--Avoid evidence state.

    Probes are never project-source edits.  Their inputs, contract, linked
    frontier, and baseline/incumbent/trial evidence are durable so a later
    transition can execute and compare the exact same obligation.
    """
    probe_id: str
    source_path: str
    input_recipe: InputRecipe
    observation_contract: ObservationContract
    linked_frontier_key: str
    authority: str
    requirement_id: str
    binding_id: str | None = None
    path_class_id: str | None = None
    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 60.0
    backend: str = "shared-executor"
    execution_results: dict[str, AtomicEvidence] = field(default_factory=dict)

    @property
    def atomic_obligation(self) -> AtomicObligation:
        raw = AtomicObligation(
            key="", requirement_id=self.requirement_id,
            requirement_contract_id=self.observation_contract.contract_id,
            role="TARGET", input_recipe={
                "kind": self.input_recipe.kind,
                "concrete_input": self.input_recipe.concrete_input,
                "derivation": self.input_recipe.derivation,
                "command": self.input_recipe.command,
                "cwd": self.cwd,
                "environment": self.environment,
                "timeout_seconds": self.timeout_seconds,
                "backend": self.backend,
            },
            input_partition_id=stable_id(
                "probe-input-partition", self.input_recipe.kind,
                self.input_recipe.concrete_input, self.input_recipe.derivation,
            ),
            oracle_contract=self.observation_contract,
            authority=(self.authority if self.authority in {"A", "B", "C", "PROVISIONAL"}
                       else "PROVISIONAL"),
            hard=True, source=f"probe:{self.probe_id}",
        )
        return AtomicObligation(
            key=atomic_obligation_key(raw),
            requirement_id=raw.requirement_id,
            requirement_contract_id=raw.requirement_contract_id,
            role=raw.role, input_recipe=raw.input_recipe,
            input_partition_id=raw.input_partition_id,
            oracle_contract=raw.oracle_contract, authority=raw.authority,
            hard=raw.hard, source=raw.source,
        )


@dataclass(frozen=True, slots=True)
class FrontierMeasure(SerializableRecord):
    frontier_key: str
    frontier_kind: str
    mechanical_ok: bool | None = None
    entered_project_code: bool | None = None
    binding_alignment: Literal["ALIGNED", "DISJOINT", "UNKNOWN"] | None = None
    executable_obligation_count: int = 0
    stable_observation_count: int = 0
    passed_atomic_keys: frozenset[str] = frozenset()
    failed_atomic_keys: frozenset[str] = frozenset()
    unknown_atomic_keys: frozenset[str] = frozenset()
    covered_partition_ids: frozenset[str] = frozenset()
    replayed_impact_ids: frozenset[str] = frozenset()
    open_risk_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class FrontierDelta(SerializableRecord):
    selected_frontier_key: str
    selected_frontier_kind: str
    before: FrontierMeasure
    after: FrontierMeasure
    verified_closed: bool = False
    material_progress: bool = False
    progress_reasons: tuple[str, ...] = ()
    regression_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TransitionTraceBundle(SerializableRecord):
    baseline: dict[str, AtomicEvidence] = field(default_factory=dict)
    incumbent: dict[str, AtomicEvidence] = field(default_factory=dict)
    trial: dict[str, AtomicEvidence] = field(default_factory=dict)


def build_frontier_measure(frontier: Any, evidence: dict[str, AtomicEvidence],
                           mechanical_ok: bool | None = None) -> FrontierMeasure:
    key = getattr(frontier, "semantic_key", None) or getattr(frontier, "frontier_id", "")
    kind = str(getattr(frontier, "kind", ""))
    requirement_ids = set(getattr(frontier, "requirement_ids", ()))
    partition_id = getattr(frontier, "input_partition_id", None)
    selected = {
        item_key: item for item_key, item in evidence.items()
        if not requirement_ids or item.requirement_id in requirement_ids
        if not partition_id or item.role == "MECHANICAL"
        or item.input_partition_id == partition_id
    }
    passed = frozenset(k for k, item in selected.items() if item.status == "PASS")
    failed = frozenset(k for k, item in selected.items() if item.status == "FAIL")
    unknown = frozenset(k for k, item in selected.items() if item.status not in {"PASS", "FAIL"})
    # A coverage partition is evidence only after its concrete scenario ran
    # stably through project code.  Creating a graph node, compiling a recipe,
    # or receiving an unexecutable result must not make coverage look solved.
    partitions = frozenset(
        item.input_partition_id for item in selected.values()
        if (
            item.input_partition_id
            and item.status in {"PASS", "FAIL", "UNKNOWN"}
            and item.stability_runs >= 2
            and item.entered_project_code
        )
    )
    replayed = frozenset(
        item.requirement_id for item in selected.values()
        if item.role in {"PRESERVATION", "IMPACT"}
        and item.status == "PASS" and item.stability_runs >= 2
    )
    return FrontierMeasure(
        frontier_key=key, frontier_kind=kind, mechanical_ok=mechanical_ok,
        entered_project_code=any(item.entered_project_code for item in selected.values()),
        binding_alignment=next((item.binding_alignment for item in selected.values() if item.binding_alignment != "UNKNOWN"), "UNKNOWN"),
        executable_obligation_count=sum(item.status not in {"UNEXECUTABLE", "BLOCKED"} for item in selected.values()),
        stable_observation_count=sum(item.stability_runs >= 2 for item in selected.values()),
        passed_atomic_keys=passed, failed_atomic_keys=failed, unknown_atomic_keys=unknown,
        covered_partition_ids=partitions, replayed_impact_ids=replayed, open_risk_ids=unknown,
    )


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
    # Every command, input, and oracle travels as one atomic record.
    validation_obligations: tuple["ValidationObligation", ...] = ()
    selected_frontier: RepairFrontier | None = None
    working_patch_hash: str = ""
    graph_revision: int = 0
    atomic_obligations: tuple[AtomicObligation, ...] = ()


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
    selected_frontier_key: str | None = None
    selected_frontier_kind: str | None = None
    atomic_before: dict[str, AtomicEvidence] = field(default_factory=dict)
    atomic_after: dict[str, AtomicEvidence] = field(default_factory=dict)
    atomic_fail_to_pass: tuple[str, ...] = ()
    atomic_pass_to_fail: tuple[str, ...] = ()
    frontier_delta: FrontierDelta | None = None
    trace_bundle: TransitionTraceBundle | None = None
    verified_progress: tuple[str, ...] = ()
    material_progress: tuple[str, ...] = ()
    trusted_regressions: tuple[str, ...] = ()
    hard_avoid_violations: tuple[str, ...] = ()


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
    selected_frontier: RepairFrontier | None = None
    trace_bundle: TransitionTraceBundle | None = None
    atomic_obligations: tuple[AtomicObligation, ...] = ()

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
    selected_frontier_key: str | None = None
    selected_frontier_kind: str | None = None


@dataclass(slots=True)
class ReachAvoidState(SerializableRecord):
    instance_id: str
    run_id: str
    base_repository: Path
    base_commit: str
    run_root: Path
    graph_stack: GraphStack
    working_checkpoint: StateCheckpoint
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
    # The controller owns graph resource policy; state construction must make
    # that policy explicit rather than silently creating a second default.
    graph_budget: GraphBudget
    repair_frontiers: dict[str, RepairFrontier] = field(default_factory=dict)
    challenge_attempts: dict[str, int] = field(default_factory=dict)
    transition_counts: dict[str, int] = field(default_factory=dict)
    last_mechanical_result: MechanicalResult | None = None
    atomic_obligations: dict[str, AtomicObligation] = field(default_factory=dict)
    atomic_evidence: dict[str, AtomicEvidence] = field(default_factory=dict)
    probe_registrations: dict[str, ProbeRegistration] = field(default_factory=dict)
    consecutive_provisional_without_progress: int = 0


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
    last_mechanical_result: MechanicalResult | None = None
    atomic_obligations: dict[str, AtomicObligation] = field(default_factory=dict)
    atomic_evidence: dict[str, AtomicEvidence] = field(default_factory=dict)
    probe_registrations: dict[str, ProbeRegistration] = field(default_factory=dict)
    consecutive_provisional_without_progress: int = 0
