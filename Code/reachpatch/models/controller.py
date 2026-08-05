from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import SerializableRecord, content_hash, stable_id, utc_now
from .budget import BudgetVector
from .enums import ControllerPhase, Decision, OutcomeStatus


@dataclass(frozen=True, slots=True)
class MechanicalCheck(SerializableRecord):
    check_id: str
    kind: str
    command: tuple[str, ...]
    status: OutcomeStatus
    return_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    source_hash: str


@dataclass(frozen=True, slots=True)
class UnitOutcome(SerializableRecord):
    outcome_id: str
    unit_id: str
    path_obligation_id: str
    scenario_id: str | None
    challenge_id: str | None
    kind: str
    status: OutcomeStatus
    weight: float
    execution_bundle_id: str | None
    failure_origin: str
    stable: bool
    comparable: bool
    observation: dict[str, Any]
    graph_hashes: dict[str, str]


@dataclass(frozen=True, slots=True)
class WorkingPatch(SerializableRecord):
    version: int
    base_commit: str
    canonical_diff: str
    canonical_diff_hash: str
    base_tree_hash: str
    working_tree_hash: str
    parent_patch_hash: str | None
    checkpoint_id: str
    status: str = "EMPTY"


@dataclass(frozen=True, slots=True)
class ExecutableOracle(SerializableRecord):
    """Mechanically evaluable relation attached to one locked check."""

    oracle_id: str
    authority: str
    relation: str
    requirement_id: str | None = None
    is_executable: bool = True


@dataclass(frozen=True, slots=True)
class EvidenceVector(SerializableRecord):
    confirmed_target_pass_count: int = 0
    confirmed_target_failure_count: int = 0
    confirmed_preservation_regression_count: int = 0
    confirmed_counterexample_count: int = 0
    mechanical_failure_count: int = 0
    execution_confirmed_requirement_count: int = 0


@dataclass(frozen=True, slots=True)
class ConfirmedFailure(SerializableRecord):
    failure_id: str
    kind: str
    check_id: str
    oracle_authority: str
    requirement_id: str | None
    binding_unit_id: str | None
    baseline_observation: Any
    before_patch_observation: Any
    expected_relation: ExecutableOracle
    stable_runs: int
    failure_signature: str
    failure_location: str | None
    causal_cut_ids: tuple[str, ...]
    impact_risk_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LockedCheck(SerializableRecord):
    check_id: str
    role: str
    command: tuple[str, ...]
    observation_contract: Any
    oracle: ExecutableOracle
    authority: str
    requirement_ids: tuple[str, ...]
    cwd: str = ""
    environment: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    source_evidence_ids: tuple[str, ...] = ()
    baseline_observation: Any = None
    input_recipe: Any = None
    executable_scenario: Any = None
    baseline_repository: str = ""


@dataclass(frozen=True, slots=True)
class LockedCheckSet(SerializableRecord):
    lock_id: str
    target_checks: tuple[LockedCheck, ...] = ()
    preservation_checks: tuple[LockedCheck, ...] = ()
    counterexample_checks: tuple[LockedCheck, ...] = ()
    mechanical_checks: tuple[LockedCheck, ...] = ()

    def all_checks(self) -> tuple[LockedCheck, ...]:
        result: list[LockedCheck] = []
        seen: set[str] = set()
        for check in (
            *self.target_checks,
            *self.preservation_checks,
            *self.counterexample_checks,
            *self.mechanical_checks,
        ):
            if check.check_id in seen:
                continue
            seen.add(check.check_id)
            result.append(check)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class PatchCheckpoint(SerializableRecord):
    checkpoint_id: str
    revision: int
    patch: WorkingPatch
    patch_hash: str
    evidence_vector: EvidenceVector
    executed_check_ids: tuple[str, ...]
    confirmed_target_pass_ids: tuple[str, ...]
    confirmed_target_failure_ids: tuple[str, ...]
    preservation_regression_ids: tuple[str, ...]
    mechanical_failure_ids: tuple[str, ...]
    parent_checkpoint_id: str | None
    status: str
    snapshot_tree: str = ""


@dataclass(frozen=True, slots=True)
class RevisionRecord(SerializableRecord):
    revision_id: str
    failure_id: str
    action_id: str
    mechanism_id: str
    source_checkpoint_id: str
    trial_checkpoint_id: str
    locked_check_set_id: str
    executed_check_ids: tuple[str, ...]
    decision: str
    reason: str
    promoted: bool
    rolled_back: bool


@dataclass(slots=True)
class PatchTrajectory(SerializableRecord):
    first_patch: PatchCheckpoint
    best_evidence_patch: PatchCheckpoint
    working_patch: PatchCheckpoint
    trial_patch: PatchCheckpoint | None
    locked_checks: dict[str, LockedCheck]
    confirmed_failures: list[ConfirmedFailure]
    revision_history: list[RevisionRecord]
    regression_repair_attempts: int = 0
    checkpoint_archive: dict[str, PatchCheckpoint] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TrialComparison(SerializableRecord):
    comparison_id: str
    lock_id: str
    before_patch_hash: str
    after_patch_hash: str
    before_results: tuple[Any, ...]
    after_results: tuple[Any, ...]
    executed_check_ids: tuple[str, ...]
    comparable: bool
    confirmed_target_pass_before: tuple[str, ...] = ()
    confirmed_target_pass_after: tuple[str, ...] = ()
    confirmed_target_failure_before: tuple[str, ...] = ()
    confirmed_target_failure_after: tuple[str, ...] = ()
    preservation_regressions_before: tuple[str, ...] = ()
    preservation_regressions_after: tuple[str, ...] = ()
    mechanical_failures_before: tuple[str, ...] = ()
    mechanical_failures_after: tuple[str, ...] = ()
    unknown_check_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IncumbentCheckpoint(SerializableRecord):
    checkpoint_id: str
    parent_checkpoint_id: str | None
    episode_id: str
    assignment_id: str
    base_commit: str
    snapshot_tree: str
    patch: WorkingPatch
    actual_fingerprint: dict[str, Any]
    graph_hashes: dict[str, str]
    environment_hash: str
    pass_pairs: tuple[tuple[str, str], ...]
    fail_pairs: tuple[tuple[str, str], ...]
    unknown_pairs: tuple[tuple[str, str], ...]
    blocked_path_obligation_ids: tuple[str, ...]
    executed_target_deficit: float
    accepted_transition_id: str | None
    generator_session_cursor: str
    remaining_budget: BudgetVector
    safe: bool
    graph_reached: bool
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class LosingCore(SerializableRecord):
    core_id: str
    component_id: str
    unit_ids: tuple[str, ...]
    path_obligation_ids: tuple[str, ...]
    scenario_ids: tuple[str, ...]
    common_causal_cut_ids: tuple[str, ...]
    protected_pass_pairs: tuple[tuple[str, str], ...]
    preservation_ids: tuple[str, ...]
    actual_failure_observations: dict[str, Any]
    pressure: float


@dataclass(frozen=True, slots=True)
class RepairIntent(SerializableRecord):
    intent_id: str
    source_checkpoint_id: str
    losing_core_id: str
    component_id: str
    losing_path_obligation_ids: tuple[str, ...]
    complete_component_path_ids: tuple[str, ...]
    repair_cut_ids: tuple[str, ...]
    root_mechanism_class: str
    actual_failure_execution_ids: tuple[str, ...]
    protected_pass_pairs: tuple[tuple[str, str], ...]
    preservation_ids: tuple[str, ...]
    forbidden_fingerprints: tuple[str, ...]
    frontier_resolution_ids: tuple[str, ...]
    selection_witness: dict[str, Any]
    mechanism_id: str = ""
    requirements_to_satisfy: tuple[str, ...] = ()
    binding_unit_ids: tuple[str, ...] = ()
    counterexample_ids: tuple[str, ...] = ()
    observed_failures: tuple[str, ...] = ()
    root_cause: str = ""
    files_to_modify: tuple[str, ...] = ()
    symbols_to_modify: tuple[str, ...] = ()
    causal_cut_ids: tuple[str, ...] = ()
    behavior_to_preserve: tuple[str, ...] = ()
    expected_effects: tuple[str, ...] = ()
    known_risks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructuredEditIntent(SerializableRecord):
    edit_id: str
    operator: str
    relative_path: str
    target_node_id: str
    expected_span: tuple[int, int] | None
    expected_source: str | None
    replacement: str | None
    payload: dict[str, Any]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    control_flow_effects: tuple[str, ...]
    exception_effects: tuple[str, ...]
    object_shape_effects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepairPlan(SerializableRecord):
    plan_id: str
    session_id: str
    intent_id: str
    checkpoint_id: str
    losing_core_id: str
    component_id: str
    root_mechanism: str
    repair_cut_ids: tuple[str, ...]
    ordered_edit_intents: tuple[StructuredEditIntent, ...]
    coverage_by_path: dict[str, dict[str, Any]]
    protected_pass_pairs: tuple[tuple[str, str], ...]
    preservation_ids: tuple[str, ...]
    expected_graph_invalidations: tuple[str, ...]
    forbidden_fingerprints: tuple[str, ...]
    atomic_compound: bool


@dataclass(frozen=True, slots=True)
class RepairAction(SerializableRecord):
    action_id: str
    intent_id: str
    operator: str
    causal_cut_ids: tuple[str, ...]
    edit_intents: tuple[StructuredEditIntent, ...]
    read_set: tuple[str, ...]
    write_set: tuple[str, ...]
    expected_impact_node_ids: tuple[str, ...]
    plan: RepairPlan


@dataclass(frozen=True, slots=True)
class CounterexamplePacket(SerializableRecord):
    counterexample_id: str
    transition_id: str
    path_obligation_id: str | None
    binding_unit_id: str | None
    challenge_id: str | None
    public_trigger_id: str | None
    entrypoint_id: str | None
    guarded_path_edge_ids: tuple[str, ...]
    exit_kind: str | None
    trusted_oracle_id: str | None
    expected_observation: Any
    actual_observation: Any
    minimal_input: dict[str, Any]
    reproduction_recipe_id: str | None
    raw_execution_ids: tuple[str, ...]
    relevant_source_slice_ids: tuple[str, ...]
    causal_touch_witness_ids: tuple[str, ...]
    candidate_repair_cut_ids: tuple[str, ...]
    protected_sibling_path_ids: tuple[str, ...]
    preservation_path_ids: tuple[str, ...]
    forbidden_behavior_ids: tuple[str, ...]
    source_hash: str
    diff_hash: str
    failure_origin: str
    frontier_kind: str | None
    uncertain_information: tuple[str, ...]
    mechanism_fingerprint_hash: str | None
    delivered_session_cursor: str | None = None
    reproduction_command: tuple[str, ...] = ()
    baseline_status: str | None = None
    patched_status: str | None = None
    failure_signature: str | None = None
    first_project_frame: dict[str, Any] | None = None
    relevant_source_ranges: tuple[dict[str, Any], ...] = ()
    causal_cut_candidates: tuple[dict[str, Any], ...] = ()
    previous_diff: str = ""
    protected_behavior: tuple[str, ...] = ()
    environment_valid: bool = True
    requirement_id: str | None = None
    authority: str = "PUBLIC_EXECUTION"
    setup: tuple[str, ...] = ()
    command: str = ""
    concrete_input: Any = None
    input_derivation: tuple[str, ...] = ()
    baseline_observation: Any = None
    patched_observation: Any = None
    expected_relation: str = ""
    oracle_result: str = ""
    failure_location: str = ""
    causal_cut_ids: tuple[str, ...] = ()
    impact_risks: tuple[str, ...] = ()
    suggested_action_families: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GeneratorSessionRecord(SerializableRecord):
    session_id: str
    episode_id: str
    current_checkpoint_id: str
    cursor: int
    delivered_counterexample_ids: tuple[str, ...]
    submitted_transition_ids: tuple[str, ...]
    internal_tool_turns: int
    active: bool


@dataclass(frozen=True, slots=True)
class MechanismAttempt(SerializableRecord):
    component_id: str
    losing_core_id: str
    mechanism_class: str
    fingerprint_hash: str
    result: str
    causal_cut_ids: tuple[str, ...]
    failure_observation_hash: str
    transition_id: str
    equivalent_attempt_count: int
    forbidden_next: bool


@dataclass(frozen=True, slots=True)
class FailureHistory(SerializableRecord):
    failure_signature: str
    attempted_mechanism_ids: tuple[str, ...]
    causal_cut_ids: tuple[str, ...]
    revision_ids: tuple[str, ...]
    confirmed_outcomes: tuple[str, ...]
    affected_symbol_ids: tuple[str, ...] = ()

    @property
    def signature(self) -> str:
        return self.failure_signature

    @property
    def attempted_mechanisms(self) -> tuple[str, ...]:
        return self.attempted_mechanism_ids

    @property
    def affected_symbols(self) -> tuple[str, ...]:
        return self.affected_symbol_ids


@dataclass(frozen=True, slots=True)
class RootRecoveryRecord(SerializableRecord):
    recovery_id: str
    core_id: str
    trigger: str
    old_graph_hashes: dict[str, str]
    new_graph_hashes: dict[str, str]
    invalidated_unit_ids: tuple[str, ...]
    new_cut_ids: tuple[str, ...]
    classification: str
    resolution: str


@dataclass(frozen=True, slots=True)
class TransitionCertificate(SerializableRecord):
    transition_id: str
    update_id: str
    source_checkpoint_id: str
    result_checkpoint_id: str | None
    incremental_diff_hash: str
    cumulative_diff_hash: str
    actual_edit_ids: tuple[str, ...]
    causal_cut_ids: tuple[str, ...]
    graph_delta: dict[str, Any]
    mechanical_check_ids: tuple[str, ...]
    outcome_ids: tuple[str, ...]
    new_counterexample_ids: tuple[str, ...]
    eliminated_counterexample_ids: tuple[str, ...]
    impact_regression_ids: tuple[str, ...]
    adjacent_partition_obligation_ids: tuple[str, ...]
    hard_frontier_ids: tuple[str, ...]
    old_target_deficit: float
    new_target_deficit: float
    repaired_losing_path_ids: tuple[str, ...]
    mechanical_pass: bool
    forbidden_edit: bool
    oracle_contamination: bool
    established_successes_pass: bool
    preservation_pass: bool
    component_shadow_pass: bool
    diff_safety_pass: bool
    safe: bool
    strict_target_progress: bool
    reach: bool
    avoid: bool
    progress: bool
    decision: Decision
    restoration_or_commit_receipt: str
    input_artifact_ids: tuple[str, ...]
    recomputation_hash: str
    instance_id: str = ""
    generation_run_id: str = ""
    from_revision: int = 0
    to_revision: int = 0
    before_patch_hash: str = ""
    after_patch_hash: str = ""
    action_id: str = ""
    mechanism_id: str = ""
    active_binding_graph_hash: str = ""
    affected_binding_unit_ids: tuple[str, ...] = ()
    executed_check_ids: tuple[str, ...] = ()
    target_comparisons: tuple[str, ...] = ()
    preservation_comparisons: tuple[str, ...] = ()
    challenge_comparisons: tuple[str, ...] = ()
    requirements_improved: tuple[str, ...] = ()
    requirements_regressed: tuple[str, ...] = ()
    counterexamples_closed: tuple[str, ...] = ()
    counterexamples_opened: tuple[str, ...] = ()
    progress_before: dict[str, Any] = field(default_factory=dict)
    progress_after: dict[str, Any] = field(default_factory=dict)
    reach_decision: str = "NOT_REACHED"
    avoid_decision: str = "NOT_AVOIDED"
    rollback_decision: str = ""
    code_commit_sha: str = ""
    config_hash: str = ""
    prompt_hash: str = ""
    patch_hash: str = ""
    evidence_hashes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TransitionResult(SerializableRecord):
    transition_id: str
    accepted: bool
    decision: Decision
    certificate: TransitionCertificate
    counterexamples: tuple[CounterexamplePacket, ...]
    checkpoint: IncumbentCheckpoint
    action: RepairAction
    reason: str


@dataclass(frozen=True, slots=True)
class TerminalCertificate(SerializableRecord):
    instance_id: str
    episode_id: str
    status: str
    final_checkpoint_id: str
    final_diff_hash: str
    graph_reached: bool
    target_complete: bool
    preservation_complete: bool
    shadow_complete: bool
    closure_complete: bool
    unresolved_path_obligation_ids: tuple[str, ...]
    unresolved_frontier_ids: tuple[str, ...]
    terminal_reason: str
    graph_hashes: dict[str, str]
    environment_hash: str
    remaining_budget: BudgetVector
    artifact_verification_hash: str
    sealed_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ReachAvoidState:
    state_id: str
    instance_id: str
    run_id: str
    episode_id: str
    base_repository: str
    base_commit: str
    run_root: str
    assignment: Any
    semantic_graph: Any
    requirement_graph: Any
    program_graph: Any
    active_binding_graph: Any
    challenge_graph: Any
    checkpoint: IncumbentCheckpoint
    outcomes: dict[str, UnitOutcome]
    trace_bundles: dict[str, Any]
    counterexamples: list[CounterexamplePacket]
    repair_history: list[TransitionCertificate]
    mechanism_memory: dict[str, list[MechanismAttempt]]
    root_recoveries: list[RootRecoveryRecord]
    diff_closure_certificates: list[Any]
    generator_session: GeneratorSessionRecord
    remaining_budget: BudgetVector
    phase: ControllerPhase
    artifact_ids: dict[str, list[str]]
    hypothesis_set: Any | None = None
    repository_index: Any | None = None
    generator_conversation: Any | None = None
    runtime_config: dict[str, Any] = field(default_factory=dict)
    runtime_metrics: dict[str, Any] = field(default_factory=dict)
    termination_status: str | None = None
    transition_index: int = 0
    phase_history: list[dict[str, Any]] = field(default_factory=list)
    target_recovery: Any | None = None
    executable_requirement_overlay: Any | None = None
    target_slice: Any | None = None
    causal_slices: tuple[Any, ...] = ()
    impact_slice: Any | None = None
    check_comparisons: tuple[Any, ...] = ()
    dicc_certificate: Any | None = None
    environment_frontiers: tuple[Any, ...] = ()
    working_trial: dict[str, Any] | None = None
    observations: Any | None = None
    requirement_coverage: Any | None = None
    verified_safe_patch: WorkingPatch | None = None
    patch_trajectory: PatchTrajectory | None = None
    checkpoint_history: dict[str, IncumbentCheckpoint] = field(default_factory=dict)
    confirmed_failures: list[ConfirmedFailure] = field(default_factory=list)
    current_locked_check_set: LockedCheckSet | None = None
    failure_histories: dict[str, Any] = field(default_factory=dict)
    prohibited_mechanisms: set[str] = field(default_factory=set)
    unresolved_frontier: list[Any] = field(default_factory=list)
    reach_status: str = "NOT_REACHED"
    avoid_status: str = "NOT_AVOIDED"
    generation_run_id: str = ""
    code_commit_sha: str = ""
    method_config_hash: str = ""
    prompt_hash: str = ""
    current_patch_hash: str = ""

    def graph_hashes(self) -> dict[str, str]:
        return {
            "semantic": self.semantic_graph.to_dict()["graph_hash"],
            "requirement": self.requirement_graph.semantic_layer_hash(),
            "program": self.program_graph.program_hash(),
            "binding": self.active_binding_graph.graph_hash(),
            "challenge": self.challenge_graph.graph_hash(),
        }

    def outcomes_with(self, *statuses: OutcomeStatus) -> tuple[UnitOutcome, ...]:
        allowed = set(statuses)
        return tuple(sorted(
            (item for item in self.outcomes.values() if item.status in allowed),
            key=lambda item: item.outcome_id,
        ))

    def target_deficit(self) -> float:
        # Patch-first production measures deficit from paired executions.  The
        # graph-derived fallback is retained only for explicitly enabled legacy
        # runs that do not have a TargetRecoveryResult.
        if self.target_recovery is not None:
            from reachpatch.execution.models import CheckClassification

            target_ids = {
                item.check_id for item in self.target_recovery.targets
            }
            latest = {
                item.check_id: item for item in self.check_comparisons
                if item.check_id in target_ids
            }
            return float(sum(
                check_id not in latest
                or latest[check_id].classification
                != CheckClassification.TARGET_FIXED
                for check_id in target_ids
            ))
        target_units = {
            unit.unit_id
            for unit in self.active_binding_graph.units.values()
            if unit.preservation_check_ids == ()
        }
        by_unit = {
            unit_id: [
                item for item in self.outcomes.values()
                if item.kind == "TARGET" and item.unit_id == unit_id
            ]
            for unit_id in target_units
        }
        passed = {
            unit_id for unit_id, outcomes in by_unit.items()
            if outcomes and all(item.status == OutcomeStatus.PASS for item in outcomes)
        }
        return sum(
            1.0 for unit_id in target_units - passed
        )

    def to_dict(self) -> dict[str, Any]:
        body = {
            "state_id": self.state_id,
            "instance_id": self.instance_id,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "base_repository": self.base_repository,
            "base_commit": self.base_commit,
            "run_root": self.run_root,
            "assignment": self.assignment.to_dict(),
            "hypothesis_set": self.hypothesis_set.to_dict() if self.hypothesis_set is not None else None,
            "repository_index": (
                {
                    "repository_root": self.repository_index.repository_root,
                    "scanned_files": self.repository_index.scanned_files,
                    "build_seconds": self.repository_index.build_seconds,
                    "source_hashes": self.repository_index.source_hashes,
                }
                if self.repository_index is not None else None
            ),
            "generator_conversation": (
                self.generator_conversation.to_dict()
                if self.generator_conversation is not None else None
            ),
            "runtime_config": self.runtime_config,
            "runtime_metrics": self.runtime_metrics,
            "graph_hashes": self.graph_hashes(),
            "checkpoint": self.checkpoint.to_dict(),
            "working_patch_hash": self.checkpoint.patch.canonical_diff_hash,
            "active_binding_graph": self.active_binding_graph.to_dict(),
            "outcomes": [self.outcomes[key].to_dict() for key in sorted(self.outcomes)],
            "trace_bundle_ids": sorted(self.trace_bundles),
            "counterexample_ids": [item.counterexample_id for item in self.counterexamples],
            "repair_history_ids": [item.transition_id for item in self.repair_history],
            "mechanism_memory": {
                key: [item.to_dict() for item in value]
                for key, value in sorted(self.mechanism_memory.items())
            },
            "root_recovery_ids": [item.recovery_id for item in self.root_recoveries],
            "diff_closure_ids": [item.closure_id for item in self.diff_closure_certificates],
            "generator_session": self.generator_session.to_dict(),
            "remaining_budget": self.remaining_budget.to_dict(),
            "phase": self.phase.value,
            "artifact_ids": self.artifact_ids,
            "termination_status": self.termination_status,
            "transition_index": self.transition_index,
            "phase_history": self.phase_history,
            "target_recovery": (
                self.target_recovery.to_dict() if self.target_recovery else None
            ),
            "executable_requirement_overlay": (
                self.executable_requirement_overlay.to_dict()
                if self.executable_requirement_overlay else None
            ),
            "target_slice": self.target_slice.to_dict() if self.target_slice else None,
            "causal_slices": [item.to_dict() for item in self.causal_slices],
            "impact_slice": self.impact_slice.to_dict() if self.impact_slice else None,
            "check_comparisons": [item.to_dict() for item in self.check_comparisons],
            "dicc_certificate": (
                self.dicc_certificate.to_dict() if self.dicc_certificate else None
            ),
            "environment_frontiers": [
                item.to_dict() for item in self.environment_frontiers
            ],
            "working_trial": self.working_trial,
            "observations": (
                self.observations.to_dict() if self.observations is not None else None
            ),
            "requirement_coverage": (
                self.requirement_coverage.to_dict()
                if self.requirement_coverage is not None else None
            ),
            "verified_safe_patch": (
                self.verified_safe_patch.to_dict()
                if self.verified_safe_patch is not None else None
            ),
            "patch_trajectory": (
                self.patch_trajectory.to_dict()
                if self.patch_trajectory is not None else None
            ),
            "checkpoint_history": {
                key: value.to_dict()
                for key, value in sorted(self.checkpoint_history.items())
            },
            "confirmed_failures": [
                item.to_dict() for item in self.confirmed_failures
            ],
            "current_locked_check_set": (
                self.current_locked_check_set.to_dict()
                if self.current_locked_check_set is not None else None
            ),
            "failure_histories": {
                key: value.to_dict() if hasattr(value, "to_dict") else value
                for key, value in sorted(self.failure_histories.items())
            },
            "prohibited_mechanisms": sorted(self.prohibited_mechanisms),
            "unresolved_frontier": [
                item.to_dict() if hasattr(item, "to_dict") else item
                for item in self.unresolved_frontier
            ],
            "reach_status": self.reach_status,
            "avoid_status": self.avoid_status,
            "generation_run_id": self.generation_run_id or self.run_id,
            "code_commit_sha": self.code_commit_sha,
            "method_config_hash": self.method_config_hash,
            "prompt_hash": self.prompt_hash,
            "current_patch_hash": (
                self.current_patch_hash or self.checkpoint.patch.canonical_diff_hash
            ),
        }
        body["content_hash"] = content_hash(body)
        return body

    def refresh_id(self) -> None:
        self.state_id = stable_id(
            "state", self.instance_id, self.checkpoint.checkpoint_id,
            self.transition_index, self.graph_hashes(),
        )

    def transition_phase(
        self,
        target: ControllerPhase,
        *,
        event: str,
        artifact_ids: tuple[str, ...] = (),
    ) -> None:
        from reachpatch.reach_avoid.machine import phase_transition

        record = phase_transition(
            self.phase, target, event=event, artifact_ids=artifact_ids
        )
        self.phase = target
        self.phase_history.append(record.to_dict())
