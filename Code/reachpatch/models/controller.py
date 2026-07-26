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
    binding_graph: Any
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

    def graph_hashes(self) -> dict[str, str]:
        return {
            "semantic": self.semantic_graph.to_dict()["graph_hash"],
            "requirement": self.requirement_graph.semantic_layer_hash(),
            "program": self.program_graph.program_hash(),
            "binding": self.binding_graph.graph_hash(),
            "challenge": self.challenge_graph.graph_hash(),
        }

    def outcomes_with(self, *statuses: OutcomeStatus) -> tuple[UnitOutcome, ...]:
        allowed = set(statuses)
        return tuple(sorted(
            (item for item in self.outcomes.values() if item.status in allowed),
            key=lambda item: item.outcome_id,
        ))

    def target_deficit(self) -> float:
        target_units = {
            unit.unit_id
            for unit in self.binding_graph.units.values()
            if self.requirement_graph.leaves[unit.leaf_id].authority_class.value != "PRESERVATION"
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
            self.requirement_graph.leaves[self.binding_graph.units[unit_id].leaf_id].weight
            for unit_id in target_units - passed
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
