from __future__ import annotations

import copy
import json
import os
import platform
import resource
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from reachpatch.adapters import select_adapter
from reachpatch.binding_graph import build_active_binding_graph
from reachpatch.binding_graph.models import BindingGraph
from reachpatch.challenge_graph.dicc import (
    diff_induced_challenge_plan,
    finalize_diff_induced_challenge_closure,
)
from reachpatch.challenge_graph.materialize import (
    execute_challenges,
    materialize_active_challenges,
)
from reachpatch.challenge_graph.models import ChallengeGraph, DiffClosureCertificate
from reachpatch.evidence import build_hypothesis_set, build_semantic_graph
from reachpatch.evidence.extract import issue_evidence
from reachpatch.evidence.hypotheses import enumerate_assignments
from reachpatch.execution import TraceExecutor, WorktreeManager
from reachpatch.execution.reconcile import ActualDiff, reconcile_actual_diff
from reachpatch.execution.worktree import tree_hash
from reachpatch.models.base import SerializableRecord, content_hash, stable_id, utc_now
from reachpatch.models.budget import BudgetVector
from reachpatch.models.controller import (
    CounterexamplePacket,
    GeneratorSessionRecord,
    IncumbentCheckpoint,
    MechanismAttempt,
    ReachAvoidState,
    TerminalCertificate,
    TransitionCertificate,
    WorkingPatch,
)
from reachpatch.models.core import Instance
from reachpatch.models.enums import Confidence, ControllerPhase, Decision, OutcomeStatus
from reachpatch.models.isolation import assert_generation_payload, is_official_only_path
from reachpatch.oracle.discriminator import DiscriminatorProbe, HypothesisDiscriminator
from reachpatch.program_graph import (
    Deadline, GraphBudget, build_active_program_slice, build_repository_index,
    recover_repair_slice_seeds, update_active_program_slice,
)
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.reach_avoid.gates import in_target_set, terminal_avoid_reason
from reachpatch.reach_avoid.persistence import RunArtifacts
from reachpatch.reach_avoid.state import outcomes_from_challenges
from reachpatch.reach_avoid.transition import evaluate_patch_revision
from reachpatch.reach_avoid.restore import (
    binding_graph_from_dict, challenge_graph_from_dict, conversation_from_dict,
    hypothesis_set_from_dict, outcome_from_dict, program_graph_from_dict,
    repository_index_from_dict, requirement_graph_from_dict,
)
from reachpatch.repair.ablation import (
    AblationValidation,
    EditRetentionAblation,
    edit_retention_ablation,
)
from reachpatch.repair.session import ActionProvider, PersistentGeneratorSession
from reachpatch.repair.deepseek_agent import (
    ActionConversionStatus, GeneratorBlockedExternal, GeneratorConversation,
    PersistentDeepSeekAgent, convert_revision_action,
)
from reachpatch.repair.tools import RepairToolExecutor
from reachpatch.requirement_graph import (
    compile_assignment_overlay, compile_requirement_core, compile_requirement_paths,
    promote_domains_from_diff, refresh_requirement_paths,
)


class AnalysisBlocked(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ReachPatchConfig(SerializableRecord):
    selection_mode: str = "hypothesis_set"
    max_submitted_revisions: int = 10
    max_internal_tool_turns_per_revision: int = 12
    equivalent_failures_before_new_mechanism: int = 2
    nonprogress_before_root_recovery: int = 3
    max_ablation_groups: int = 32
    enable_ablation: bool = False
    max_index_files: int = 10_000
    max_precise_files: int = 40
    max_precise_functions: int = 200
    max_program_nodes: int = 50_000
    max_program_edges: int = 150_000
    max_protocol_candidates_per_operation: int = 8
    max_path_classes_per_leaf: int = 24
    max_active_target_bindings: int = 20
    max_active_preservation_bindings: int = 20
    max_active_challenges: int = 40
    repository_index_deadline_seconds: float = 60.0
    program_slice_deadline_seconds: float = 90.0
    requirement_deadline_seconds: float = 30.0
    binding_deadline_seconds: float = 15.0
    challenge_deadline_seconds: float = 15.0
    graph_memory_limit_mib: int = 2048
    forbidden_patterns: tuple[str, ...] = (
        "tests/**", "test/**", "**/test_*.py", "**/*_test.py",
    )
    mechanical_commands: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if self.selection_mode not in {"hypothesis_set", "certified", "benchmark"}:
            raise ValueError("invalid selection_mode")
        if self.max_submitted_revisions < 1:
            raise ValueError("max_submitted_revisions must be positive")
        if self.max_ablation_groups < 1:
            raise ValueError("max_ablation_groups must be positive")
        positive = (
            self.max_internal_tool_turns_per_revision,
            self.equivalent_failures_before_new_mechanism,
            self.nonprogress_before_root_recovery,
            self.max_index_files, self.max_precise_files,
            self.max_precise_functions, self.max_program_nodes,
            self.max_program_edges, self.max_protocol_candidates_per_operation,
            self.max_path_classes_per_leaf, self.max_active_target_bindings,
            self.max_active_preservation_bindings, self.max_active_challenges,
            self.repository_index_deadline_seconds,
            self.program_slice_deadline_seconds, self.requirement_deadline_seconds,
            self.binding_deadline_seconds, self.challenge_deadline_seconds,
            self.graph_memory_limit_mib,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("graph and agent limits must be positive")


def _public_check_commands(
    visible_test_paths: Iterable[str],
    configured_commands: Iterable[Iterable[str]],
    *,
    max_visible_checks: int = 5,
) -> tuple[tuple[str, ...], ...]:
    """Compile a small public-only check set shared by tools and transitions."""

    commands = [tuple(map(str, command)) for command in configured_commands if command]
    seen = set(commands)
    selected = 0
    for relative in visible_test_paths:
        normalized = str(relative).replace("\\", "/")
        if is_official_only_path(normalized) or selected >= max_visible_checks:
            continue
        command = (sys.executable, "-m", "pytest", "-q", normalized)
        if command in seen:
            continue
        commands.append(command)
        seen.add(command)
        selected += 1
    return tuple(commands)


def _named_public_checks(state: ReachAvoidState) -> dict[str, tuple[str, ...]]:
    configured = state.runtime_config.get("public_check_commands")
    if configured is None:
        configured = _public_check_commands(
            state.runtime_config.get("visible_test_paths", ()),
            state.runtime_config.get("mechanical_commands", ()),
        )
    return {
        f"public-check-{index}": tuple(map(str, command))
        for index, command in enumerate(configured)
        if command
    }


def default_budget() -> BudgetVector:
    return BudgetVector(
        semantic_tokens=10_000,
        graph_tokens=20_000,
        initial_generator_tokens=20_000,
        repair_generator_tokens=80_000,
        challenge_materializer_tokens=10_000,
        discriminator_tokens=5_000,
        execution_seconds=7_200.0,
        tracing_seconds=1_800.0,
        wall_seconds=10_800.0,
    )


def _working_patch(raw: dict) -> WorkingPatch:
    return WorkingPatch(**raw)


def _checkpoint(raw: dict) -> IncumbentCheckpoint:
    values = dict(raw)
    values["patch"] = _working_patch(values["patch"])
    values["remaining_budget"] = BudgetVector(**values["remaining_budget"])
    for name in (
        "pass_pairs", "fail_pairs", "unknown_pairs",
        "blocked_path_obligation_ids",
    ):
        values[name] = tuple(
            tuple(item) if isinstance(item, list) else item
            for item in values.get(name, ())
        )
    return IncumbentCheckpoint(**values)


def _generator_record(raw: dict) -> GeneratorSessionRecord:
    values = dict(raw)
    for name in (
        "delivered_counterexample_ids", "submitted_transition_ids",
    ):
        values[name] = tuple(values.get(name, ()))
    return GeneratorSessionRecord(**values)


def _mechanism_attempt(raw: dict) -> MechanismAttempt:
    values = dict(raw)
    values["causal_cut_ids"] = tuple(values.get("causal_cut_ids", ()))
    return MechanismAttempt(**values)


def _counterexample(raw: dict) -> CounterexamplePacket:
    values = dict(raw)
    for name in (
        "guarded_path_edge_ids", "raw_execution_ids",
        "relevant_source_slice_ids", "causal_touch_witness_ids",
        "candidate_repair_cut_ids", "protected_sibling_path_ids",
        "preservation_path_ids", "forbidden_behavior_ids",
        "uncertain_information",
    ):
        values[name] = tuple(values.get(name, ()))
    return CounterexamplePacket(**values)


def _transition_certificate(raw: dict) -> TransitionCertificate:
    values = dict(raw)
    for name in (
        "actual_edit_ids", "causal_cut_ids", "mechanical_check_ids",
        "outcome_ids", "new_counterexample_ids",
        "eliminated_counterexample_ids", "impact_regression_ids",
        "adjacent_partition_obligation_ids", "hard_frontier_ids",
        "repaired_losing_path_ids", "input_artifact_ids",
    ):
        values[name] = tuple(values.get(name, ()))
    values["decision"] = Decision(values["decision"])
    return TransitionCertificate(**values)


def _diff_closure(raw: dict) -> DiffClosureCertificate:
    values = dict(raw)
    for name in (
        "baseline_path_obligation_ids", "overlay_obligation_ids",
        "obligation_result_ids", "invalidated_node_ids",
        "changed_guard_obligation_ids", "call_exit_obligation_ids",
        "fallback_obligation_ids", "state_dispatch_obligation_ids",
        "bypass_obligation_ids", "preservation_caller_obligation_ids",
        "hard_frontier_ids", "residual_risk_frontier_ids",
        "oracle_change_ids", "stale_record_ids", "changed_edge_ledger_ids",
    ):
        values[name] = tuple(values.get(name, ()))
    values["updated_obligations"] = tuple(values.get("updated_obligations", ()))
    return DiffClosureCertificate(**values)


@dataclass(slots=True)
class _AblationEvaluation:
    requirement_graph: Any
    program_graph: Any
    binding_graph: Any
    challenge_graph: Any
    outcomes: dict[str, Any]
    bundles: tuple[Any, ...]
    closure: DiffClosureCertificate
    cumulative_diff: ActualDiff
    safe: bool
    graph_reached: bool


class ReachPatchController:
    def __init__(
        self,
        *,
        config: ReachPatchConfig | None = None,
        action_provider: ActionProvider | None = None,
        generator_agent: PersistentDeepSeekAgent | None = None,
        implementation_root: str | Path | None = None,
    ) -> None:
        self.config = config or ReachPatchConfig()
        self.action_provider = action_provider
        self.generator_agent = generator_agent
        if self.generator_agent is not None:
            self.generator_agent.max_tool_turns = min(
                self.generator_agent.max_tool_turns,
                self.config.max_internal_tool_turns_per_revision,
            )
        self.implementation_root = Path(
            implementation_root or Path(__file__).parents[2]
        ).resolve()

    def _assert_local(self, path: Path, purpose: str) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.implementation_root):
            raise ValueError(
                f"{purpose} must be inside {self.implementation_root}: {resolved}"
            )
        return resolved

    def _run_root(self, instance_id: str, supplied: str | Path | None) -> Path:
        if supplied is not None:
            root = self._assert_local(Path(supplied), "run root")
        else:
            run_id = stable_id("run", instance_id, utc_now())
            root = self.implementation_root / "runs" / instance_id / run_id
        root.mkdir(parents=True, exist_ok=False)
        return root

    @staticmethod
    def _environment_hash() -> str:
        return content_hash({
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        })

    def analyze(
        self,
        instance: Instance,
        *,
        run_root: str | Path | None = None,
        budget: BudgetVector | None = None,
    ) -> ReachAvoidState:
        """Create and, when configured, immediately revise a patch-first state."""

        repository = self._assert_local(instance.repository_path(), "repository")
        if not instance.issue.strip():
            raise AnalysisBlocked("SEMANTIC_BLOCKED", "issue text is empty")
        assert_generation_payload(
            instance.public_metadata, path="instance.public_metadata"
        )
        assert_generation_payload(
            instance.environment, path="instance.environment"
        )
        root = self._run_root(instance.instance_id, run_root)
        started = time.perf_counter()
        visible_tests = tuple(
            str(self._assert_local(
                Path(path) if Path(path).is_absolute() else repository / path,
                "visible test",
            ))
            for path in instance.visible_tests
            if not is_official_only_path(str(path))
        )
        if len(visible_tests) != len(instance.visible_tests):
            raise AnalysisBlocked(
                "ORACLE_CONTAMINATION",
                "GenerationInstance visible_tests contains official-only evidence",
            )
        public_hints = str(instance.public_metadata.get("hints_text", "")).strip()
        hint_evidence = tuple(issue_evidence(public_hints)) if public_hints else ()
        semantic_started = time.perf_counter()
        semantic_result = build_semantic_graph(
            instance.issue,
            visible_test_paths=visible_tests,
            extra_evidence=hint_evidence,
        )
        hypothesis_set = build_hypothesis_set(semantic_result.graph)
        if not hypothesis_set.alternatives:
            raise AnalysisBlocked(
                "SEMANTIC_BLOCKED", "public evidence produced no coherent authority-complete hypothesis"
            )
        assignment = next(
            item for item in hypothesis_set.alternatives
            if item.assignment_id == hypothesis_set.preferred_assignment_id
        )
        semantic_decisions, _ = enumerate_assignments(semantic_result.graph)
        discriminator_probes = HypothesisDiscriminator().plan(
            semantic_decisions, hypothesis_set.alternatives
        )
        timings = {"semantic_analysis_seconds": time.perf_counter() - semantic_started}
        phase_metrics: dict[str, Any] = {
            "deepseek_initial_generation_count": 0,
            "deepseek_repair_count": 0,
            "deepseek_tool_turns": 0,
            "accepted_transitions": 0,
            "rolled_back_transitions": 0,
        }
        index_started = time.perf_counter()
        repository_index = build_repository_index(
            repository,
            max_files=self.config.max_index_files,
            deadline=Deadline.after(self.config.repository_index_deadline_seconds),
        )
        timings["repository_index_seconds"] = time.perf_counter() - index_started
        issue_symbols = {
            token.rsplit(".", 1)[-1].lower()
            for token in re.findall(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", instance.issue)
        }
        inferred_public_tests = tuple(
            str(repository / relative)
            for relative, references in sorted(repository_index.test_references.items())
            if issue_symbols & {name.rsplit(".", 1)[-1].lower() for name in references}
            and not is_official_only_path(relative)
        )[: min(20, self.config.max_precise_files)]
        selected_visible_tests = tuple(dict.fromkeys(
            (*visible_tests, *inferred_public_tests)
        ))
        if selected_visible_tests != visible_tests:
            visible_tests = selected_visible_tests
            timings["public_test_recovery_seconds"] = 0.0
            phase_metrics["inferred_public_test_count"] = len(
                inferred_public_tests
            )
        requirement_started = time.perf_counter()
        requirements = compile_requirement_core(
            semantic_result.graph, hypothesis_set, repository_index,
        )
        timings["requirement_core_seconds"] = time.perf_counter() - requirement_started
        seed_started = time.perf_counter()
        seeds = recover_repair_slice_seeds(
            instance.issue, visible_tests, repository_index,
        )
        timings["initial_localization_seconds"] = time.perf_counter() - seed_started
        graph_budget = GraphBudget.from_limits(
            seconds=self.config.program_slice_deadline_seconds,
            max_nodes=self.config.max_program_nodes,
            max_edges=self.config.max_program_edges,
            max_files=self.config.max_precise_files,
            max_functions=self.config.max_precise_functions,
            max_rss_mib=self.config.graph_memory_limit_mib,
            max_protocol_candidates_per_operation=(
                self.config.max_protocol_candidates_per_operation
            ),
        )
        slice_result = build_active_program_slice(
            repository, repository_index, seeds,
            previous=None, budget=graph_budget,
        )
        program = slice_result.graph
        timings["active_program_slice_seconds"] = slice_result.elapsed_seconds
        # Build only the bounded active product needed to give the initial
        # generator useful executable context.  This is deliberately not the
        # historical full closure: path construction is capped per leaf and
        # binding/challenge materialization is capped by the active budgets.
        requirement_paths_started = time.perf_counter()
        compile_requirement_paths(
            requirements,
            program,
            max_open_world_seeds=min(32, self.config.max_precise_functions),
            max_observation_nodes=min(96, self.config.max_program_nodes),
            max_paths_per_entry=self.config.max_path_classes_per_leaf,
            max_path_classes_per_leaf=self.config.max_path_classes_per_leaf,
            promote_all_program_predicates=False,
            deadline=time.monotonic() + self.config.requirement_deadline_seconds,
        )
        timings["requirement_graph_initial_seconds"] = (
            time.perf_counter() - requirement_paths_started
        )
        binding_started = time.perf_counter()
        binding = build_active_binding_graph(
            requirements,
            program,
            previous=None,
            affected_leaf_ids=set(requirements.leaves),
            affected_path_ids=set(requirements.path_obligations),
            max_target_units=self.config.max_active_target_bindings,
            max_preservation_units=self.config.max_active_preservation_bindings,
            deadline=time.monotonic() + self.config.binding_deadline_seconds,
        )
        timings["binding_graph_initial_seconds"] = time.perf_counter() - binding_started
        challenge_started = time.perf_counter()
        challenges = materialize_active_challenges(
            requirements,
            program,
            binding,
            actual_diff=None,
            previous_outcomes={},
            max_challenges=self.config.max_active_challenges,
            deadline=time.monotonic() + self.config.challenge_deadline_seconds,
        )
        timings["challenge_graph_initial_seconds"] = time.perf_counter() - challenge_started
        initial_graph_record = {
            "kind": "initial_active",
            "program_graph_seconds": float(timings["active_program_slice_seconds"]),
            "requirement_graph_seconds": float(timings["requirement_graph_initial_seconds"]),
            "binding_graph_seconds": float(timings["binding_graph_initial_seconds"]),
            "challenge_graph_seconds": float(timings["challenge_graph_initial_seconds"]),
            "program_nodes": len(program.nodes),
            "program_edges": len(program.edges),
            "requirement_leaves": len(requirements.leaves),
            "requirement_path_obligations": len(requirements.path_obligations),
            "binding_units": len(binding.units),
            "active_binding_units": binding.build_stats.get("active_count", 0),
            "deferred_binding_units": binding.build_stats.get("deferred_count", 0),
            "challenge_cells": len(challenges.cells),
            "frontier_count": (
                len(requirements.frontiers) + len(program.frontiers)
                + len(binding.frontiers) + len(challenges.frontiers)
            ),
            "truncated": bool(
                slice_result.truncated_reason
                or any(
                    frontier.kind == "ANALYSIS_TRUNCATED"
                    for graph in (requirements, program, binding, challenges)
                    for frontier in getattr(graph, "frontiers", {}).values()
                )
            ),
        }
        episode_id = stable_id(
            "patch-first-episode", instance.instance_id,
            hypothesis_set.active_assignment_ids, program.source_hash,
        )
        checkpoint_id = stable_id(
            "checkpoint", episode_id, "base", tree_hash(repository)
        )
        manager = WorktreeManager(root / "worktrees")
        manager.initialize(repository, checkpoint_id)
        snapshot = manager.checkpoint_tree(checkpoint_id)
        empty_diff = reconcile_actual_diff(snapshot, snapshot)
        working_patch = WorkingPatch(
            version=0, base_commit=instance.base_commit,
            canonical_diff=empty_diff.canonical_diff,
            canonical_diff_hash=empty_diff.canonical_diff_hash,
            base_tree_hash=empty_diff.base_tree_hash,
            working_tree_hash=empty_diff.trial_tree_hash,
            parent_patch_hash=None, checkpoint_id=checkpoint_id,
        )
        remaining = budget or default_budget()
        legacy_session = PersistentGeneratorSession(
            episode_id, checkpoint_id, action_provider=self.action_provider,
        )
        checkpoint = IncumbentCheckpoint(
            checkpoint_id=checkpoint_id, parent_checkpoint_id=None,
            episode_id=episode_id, assignment_id=assignment.assignment_id,
            base_commit=instance.base_commit, snapshot_tree=str(snapshot),
            patch=working_patch, actual_fingerprint=empty_diff.fingerprint,
            graph_hashes={}, environment_hash=self._environment_hash(),
            pass_pairs=(), fail_pairs=(), unknown_pairs=(),
            blocked_path_obligation_ids=(), executed_target_deficit=0.0,
            accepted_transition_id=None, generator_session_cursor="0",
            remaining_budget=remaining, safe=True, graph_reached=False,
        )
        conversation = GeneratorConversation.create(instance.instance_id)
        state = ReachAvoidState(
            state_id=stable_id("state", episode_id, checkpoint_id),
            instance_id=instance.instance_id, run_id=root.name,
            episode_id=episode_id, base_repository=str(snapshot),
            base_commit=instance.base_commit, run_root=str(root),
            assignment=assignment, semantic_graph=semantic_result.graph,
            requirement_graph=requirements, program_graph=program,
            binding_graph=binding, challenge_graph=challenges,
            checkpoint=checkpoint, outcomes={}, trace_bundles={},
            counterexamples=[], repair_history=[], mechanism_memory={},
            root_recoveries=[], diff_closure_certificates=[],
            generator_session=legacy_session.record, remaining_budget=remaining,
            phase=ControllerPhase.SEMANTIC, artifact_ids={},
            hypothesis_set=hypothesis_set, repository_index=repository_index,
            generator_conversation=conversation,
            runtime_config={
                **self.config.to_dict(),
                "generation_hints": str(
                    instance.public_metadata.get("hints_text", "")
                ),
                "visible_test_paths": [
                    str(Path(path).resolve().relative_to(repository)).replace("\\", "/")
                    for path in visible_tests
                ],
                "public_check_commands": [
                    list(command) for command in _public_check_commands(
                        (
                            str(Path(path).resolve().relative_to(repository)).replace("\\", "/")
                            for path in visible_tests
                        ),
                        self.config.mechanical_commands,
                    )
                ],
            }, runtime_metrics={
                "repository_index_seconds": repository_index.build_seconds,
                "repository_index_files": repository_index.scanned_files,
                "active_program_slice_seconds": slice_result.elapsed_seconds,
                "program_nodes": len(program.nodes),
                "program_edges": len(program.edges),
                "precise_files": len(slice_result.analyzed_files),
                "precise_functions": len(program.cfgs),
                "peak_rss_mib": slice_result.peak_rss_mib,
                "requirement_leaves": len(requirements.leaves),
                "requirement_partitions": len(requirements.partitions),
                "requirement_path_obligations": len(requirements.path_obligations),
                "candidate_binding_count": binding.build_stats.get("candidate_count", 0),
                "active_binding_count": binding.build_stats.get("active_count", 0),
                "deferred_binding_count": binding.build_stats.get("deferred_count", 0),
                "active_challenge_count": len(challenges.cells),
                "high_value_pending_challenge_ids": tuple(sorted(
                    challenge_id for challenge_id, cell in challenges.cells.items()
                    if cell.hard
                )),
                "diff_adequacy_closed": False,
                "graph_build_records": [initial_graph_record],
                "discriminator_probes": [
                    item.to_dict() for item in discriminator_probes
                ],
                "executed_discriminator_probe_ids": [],
                **phase_metrics,
            },
        )
        state.transition_phase(ControllerPhase.INDEX, event="semantic_hypothesis_set_built")
        state.transition_phase(ControllerPhase.INITIAL_LOCALIZATION, event="repository_index_built")
        state.transition_phase(ControllerPhase.INITIAL_GENERATION, event="active_slice_localized")
        checkpoint = replace(checkpoint, graph_hashes=state.graph_hashes())
        state.checkpoint = checkpoint
        artifacts = RunArtifacts(root, instance.instance_id)
        adapter_observation = select_adapter(repository).observe(repository)
        artifacts.put(
            "adapter_observation", adapter_observation,
            producer="reachpatch.adapter", confidence=Confidence.CONFIRMED,
            status=adapter_observation.status,
        )
        for evidence in semantic_result.evidence:
            artifacts.put(
                "evidence", evidence, producer="reachpatch.evidence",
                authority=evidence.authority, confidence=evidence.confidence,
            )
        artifacts.put(
            "hypothesis_set", hypothesis_set, producer="reachpatch.evidence",
            confidence=Confidence.HIGH,
        )
        artifacts.put(
            "repository_index", repository_index,
            producer="reachpatch.program-index", confidence=Confidence.CONFIRMED,
            status="ANALYSIS_TRUNCATED" if repository_index.parse_frontiers else "COMPLETE",
        )
        for probe in discriminator_probes:
            artifacts.put(
                "discriminator_probe", probe,
                producer="reachpatch.discriminator",
                confidence=Confidence.UNKNOWN,
                status=probe.correctness_authority,
            )
        artifacts.persist_graph_stack(state)
        artifacts.put(
            "working_patch", working_patch, state=state,
            producer="reachpatch.checkpoint", confidence=Confidence.CONFIRMED,
        )
        artifacts.put(
            "incumbent_checkpoint", checkpoint, state=state,
            producer="reachpatch.checkpoint", confidence=Confidence.CONFIRMED,
        )
        first_patch_started = time.perf_counter()
        if self.generator_agent is not None:
            public_checks = _named_public_checks(state)
            tools = RepairToolExecutor(
                repository_root=snapshot, repository_index=repository_index,
                current_diff="", public_checks=public_checks,
                allowed_test_paths=set(
                    state.runtime_config.get("visible_test_paths", ())
                ),
            )
            try:
                revision = self.generator_agent.generate_initial_patch(
                    state, conversation, tools
                )
            except GeneratorBlockedExternal as exc:
                revision = None
                self._record_generator_block(state, exc)
            timings["first_patch_generation_seconds"] = (
                time.perf_counter() - first_patch_started
            )
            state.runtime_metrics["deepseek_initial_generation_count"] = 1
            if revision is not None:
                state.runtime_metrics["deepseek_tool_turns"] = revision.tool_turns
                state.generator_session = replace(
                    state.generator_session,
                    cursor=state.generator_session.cursor + 1,
                    internal_tool_turns=(
                        state.generator_session.internal_tool_turns
                        + revision.tool_turns
                    ),
                )
            if revision is not None and revision.edits:
                validation_started = time.perf_counter()
                conversion = convert_revision_action(state, revision)
                if conversion.status in {
                    ActionConversionStatus.ACCEPTED,
                    ActionConversionStatus.NEEDS_SLICE_EXPANSION,
                }:
                    revision = conversion.revision
                    result = evaluate_patch_revision(state, revision)
                    timings["initial_revision_validation_seconds"] = (
                        time.perf_counter() - validation_started
                    )
                    if result.accepted:
                        state.runtime_metrics["accepted_transitions"] = 1
                    else:
                        state.runtime_metrics["rolled_back_transitions"] = 1
                        state.runtime_metrics.setdefault(
                            "failed_generator_mechanisms", []
                        ).append(revision.mechanism)
                    self._persist_transition(state, result)
                else:
                    self._record_action_rejection(state, revision, conversion)
            elif revision is not None and revision.context_requests:
                state.runtime_metrics["initial_context_only"] = True
        else:
            timings["first_patch_generation_seconds"] = 0.0
        timings["analysis_total_seconds"] = time.perf_counter() - started
        manifest = {
            "instance": instance.to_dict(), "config": self.config.to_dict(),
            "budget": remaining.to_dict(), "repository": str(repository),
            "created_at": utc_now(), "analysis_timings": timings,
            "patch_first": True,
            "graph_summary": {
                "graph_count": 5,
                "full_closure": False,
                "active_stack": True,
                "graphs": {
                    "semantic_hypothesis_graph": {"hash": semantic_result.graph.to_dict()["graph_hash"]},
                    "requirement_graph": {"hash": state.requirement_graph.semantic_layer_hash()},
                    "program_graph": {"hash": state.program_graph.program_hash()},
                    "binding_graph": {"hash": state.binding_graph.graph_hash()},
                    "challenge_graph": {"hash": state.challenge_graph.graph_hash()},
                },
            },
            "acceptance_metrics": state.runtime_metrics,
            "analysis_stats": dict(state.runtime_metrics),
            "analysis_resources": {
                "repository_index": {
                    "file_count": repository_index.scanned_files,
                },
                "active_program_slice": {
                    "peak_rss_mib": slice_result.peak_rss_mib,
                    "precise_files": len(slice_result.analyzed_files),
                    "precise_functions": len(program.cfgs),
                },
            },
        }
        temporary = root / ".run_manifest.tmp"
        temporary.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, root / "run_manifest.json")
        artifacts.put(
            "generator_conversation", conversation, state=state,
            producer="reachpatch.deepseek-agent", confidence=Confidence.CONFIRMED,
        )
        artifacts.persist_state(state)
        return state

    def _analyze_full_legacy(
        self,
        instance: Instance,
        *,
        run_root: str | Path | None = None,
        budget: BudgetVector | None = None,
    ) -> ReachAvoidState:
        if os.environ.get("REACHPATCH_ENABLE_LEGACY_FULL_GRAPH") != "1":
            raise AnalysisBlocked(
                "LEGACY_PATH_DISABLED",
                "full-repository analysis is isolated from patch-first production",
            )
        from reachpatch.binding_graph import build_binding_graph
        from reachpatch.challenge_graph.materialize import materialize_challenges
        from reachpatch.evidence import freeze_assignment
        from reachpatch.program_graph.builder import build_augmented_program_graph
        from reachpatch.program_graph.tracing import merge_trace_bundles

        repository = self._assert_local(instance.repository_path(), "repository")
        root = self._run_root(instance.instance_id, run_root)
        visible_tests = tuple(
            str(self._assert_local(
                Path(path) if Path(path).is_absolute() else repository / path,
                "visible test",
            ))
            for path in instance.visible_tests
        )
        analysis_started = time.perf_counter()
        semantic_started = time.perf_counter()
        semantic_result = build_semantic_graph(
            instance.issue,
            visible_test_paths=visible_tests,
        )
        assignment = freeze_assignment(
            semantic_result.graph,
            selection_mode=self.config.selection_mode,
        )
        adapter_observation = select_adapter(repository).observe(repository)
        manifest = {
            "instance": instance.to_dict(),
            "config": self.config.to_dict(),
            "budget": (budget or default_budget()).to_dict(),
            "repository": str(repository),
            "adapter": adapter_observation.to_dict(),
            "created_at": utc_now(),
        }
        manifest_path = root / "run_manifest.json"
        analysis_timings: dict[str, float] = {
            "semantic_analysis_seconds": time.perf_counter() - semantic_started,
        }
        analysis_resources: dict[str, dict[str, float]] = {}
        analysis_stats: dict[str, dict[str, int]] = {}
        analysis_progress: dict[str, Any] = {
            "current_stage": None,
            "current_substage": None,
            "completed_stages": ["semantic_analysis"],
        }

        def flush_analysis_progress(stage: str | None = None, *, status: str = "complete") -> None:
            if stage is not None:
                analysis_progress["current_stage"] = stage if status == "in_progress" else None
                if status != "in_progress":
                    analysis_progress["current_substage"] = None
                if status == "complete" and stage not in analysis_progress["completed_stages"]:
                    analysis_progress["completed_stages"].append(stage)
            manifest["analysis_timings"] = dict(analysis_timings)
            if stage is not None:
                peak_rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                if sys.platform == "darwin":
                    peak_rss /= 1024.0 * 1024.0
                else:
                    peak_rss /= 1024.0
                analysis_resources.setdefault(stage, {})[
                    f"{status}_peak_rss_mib"
                ] = peak_rss
            manifest["analysis_resources"] = {
                key: dict(value) for key, value in analysis_resources.items()
            }
            manifest["analysis_stats"] = {
                key: dict(value) for key, value in analysis_stats.items()
            }
            manifest["analysis_progress"] = {
                **analysis_progress,
                "status": status,
                "updated_at": utc_now(),
            }
            temporary_manifest = manifest_path.with_suffix(".progress.tmp")
            temporary_manifest.write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_manifest, manifest_path)

        def substage_callback(prefix: str):
            def update(name: str, status: str, elapsed: float | None) -> None:
                key = f"{prefix}_{name}_seconds"
                if elapsed is not None and status == "complete":
                    analysis_timings[key] = analysis_timings.get(key, 0.0) + elapsed
                analysis_progress["current_substage"] = (
                    f"{prefix}:{name}" if status in {"in_progress", "progress"} else None
                )
                flush_analysis_progress(
                    status="error" if status == "error" else "in_progress"
                )
            return update

        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        flush_analysis_progress("semantic_analysis")
        artifacts = RunArtifacts(root, instance.instance_id)
        artifacts.put(
            "adapter_observation",
            adapter_observation,
            producer="reachpatch.adapter",
            confidence=Confidence.CONFIRMED,
            status=adapter_observation.status,
        )
        for evidence in semantic_result.evidence:
            artifacts.put(
                "evidence",
                evidence,
                producer="reachpatch.evidence",
                authority=evidence.authority,
                confidence=evidence.confidence,
            )
        if assignment is None:
            artifacts.put(
                "semantic_hypothesis_graph",
                semantic_result.graph,
                producer="reachpatch.evidence",
                confidence=Confidence.HIGH,
                status="SEMANTIC_BLOCKED",
            )
            decisions, candidates = enumerate_assignments(semantic_result.graph)
            for probe in HypothesisDiscriminator().plan(decisions, candidates):
                artifacts.put(
                    "discriminator_probe",
                    probe,
                    producer="reachpatch.discriminator",
                    confidence=Confidence.UNKNOWN,
                    status=probe.correctness_authority,
                )
            manifest["analysis_timings"] = {
                **analysis_timings,
                "analysis_total_seconds": time.perf_counter() - analysis_started,
            }
            analysis_progress["current_stage"] = None
            analysis_progress["status"] = "blocked"
            manifest["analysis_progress"] = analysis_progress
            manifest["graph_summary"] = {
                "graph_count": 1,
                "graph_names": ["semantic_hypothesis_graph"],
                "graphs": {
                    "semantic_hypothesis_graph": {
                        "hash": semantic_result.graph.to_dict().get("graph_hash"),
                        "artifact_ids": [],
                    },
                },
                "expected_full_closure_graph_count": 5,
                "full_closure": False,
            }
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            raise AnalysisBlocked(
                "SEMANTIC_BLOCKED",
                "public evidence leaves multiple mutually exclusive semantic assignments",
            )
        stage_started = time.perf_counter()
        flush_analysis_progress("program_graph_initial", status="in_progress")
        program = build_augmented_program_graph(
            repository,
            progress_callback=substage_callback("program_graph"),
        )
        analysis_timings.update({
            f"program_graph_{key}": value
            for key, value in program.build_timings.items()
        })
        analysis_stats["program_graph"] = dict(program.build_stats)
        analysis_timings["program_graph_initial_seconds"] = time.perf_counter() - stage_started
        flush_analysis_progress("program_graph_initial")
        stage_started = time.perf_counter()
        flush_analysis_progress("requirement_graph_initial", status="in_progress")
        requirements = compile_assignment_overlay(semantic_result.graph, assignment)
        compile_requirement_paths(
            requirements,
            program,
            progress_callback=substage_callback("requirement_graph"),
        )
        analysis_timings.update({
            f"requirement_graph_{key}": value
            for key, value in requirements.build_timings.items()
        })
        analysis_stats["requirement_graph"] = dict(requirements.build_stats)
        analysis_timings["requirement_graph_initial_seconds"] = time.perf_counter() - stage_started
        flush_analysis_progress("requirement_graph_initial")
        stage_started = time.perf_counter()
        flush_analysis_progress("binding_graph_initial", status="in_progress")
        binding = build_binding_graph(
            requirements,
            program,
            progress_callback=substage_callback("binding_graph"),
        )
        analysis_timings.update({
            f"binding_graph_{key}": value
            for key, value in binding.build_timings.items()
        })
        analysis_stats["binding_graph"] = dict(binding.build_stats)
        analysis_timings["binding_graph_initial_seconds"] = time.perf_counter() - stage_started
        flush_analysis_progress("binding_graph_initial")
        stage_started = time.perf_counter()
        flush_analysis_progress("challenge_graph_initial", status="in_progress")
        challenges = materialize_challenges(requirements, program, binding)
        analysis_timings["challenge_graph_initial_seconds"] = time.perf_counter() - stage_started
        flush_analysis_progress("challenge_graph_initial")
        episode_id = stable_id(
            "episode", instance.instance_id, assignment.assignment_id,
            program.source_hash,
        )
        checkpoint_id = stable_id("checkpoint", episode_id, "base", tree_hash(repository))
        manager = WorktreeManager(root / "worktrees")
        manager.initialize(repository, checkpoint_id)
        snapshot = manager.checkpoint_tree(checkpoint_id)
        empty_diff = reconcile_actual_diff(snapshot, snapshot)
        patch = WorkingPatch(
            version=0,
            base_commit=instance.base_commit,
            canonical_diff=empty_diff.canonical_diff,
            canonical_diff_hash=empty_diff.canonical_diff_hash,
            base_tree_hash=empty_diff.base_tree_hash,
            working_tree_hash=empty_diff.trial_tree_hash,
            parent_patch_hash=None,
            checkpoint_id=checkpoint_id,
        )
        remaining = budget or default_budget()
        session = PersistentGeneratorSession(
            episode_id,
            checkpoint_id,
            action_provider=self.action_provider,
        )
        checkpoint = IncumbentCheckpoint(
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=None,
            episode_id=episode_id,
            assignment_id=assignment.assignment_id,
            base_commit=instance.base_commit,
            snapshot_tree=str(snapshot),
            patch=patch,
            actual_fingerprint=empty_diff.fingerprint,
            graph_hashes={},
            environment_hash=self._environment_hash(),
            pass_pairs=(),
            fail_pairs=(),
            unknown_pairs=(),
            blocked_path_obligation_ids=(),
            executed_target_deficit=0.0,
            accepted_transition_id=None,
            generator_session_cursor="0",
            remaining_budget=remaining,
            safe=True,
            graph_reached=False,
        )
        state = ReachAvoidState(
            state_id=stable_id("state", episode_id, checkpoint_id),
            instance_id=instance.instance_id,
            run_id=root.name,
            episode_id=episode_id,
            base_repository=str(snapshot),
            base_commit=instance.base_commit,
            run_root=str(root),
            assignment=assignment,
            semantic_graph=semantic_result.graph,
            requirement_graph=requirements,
            program_graph=program,
            binding_graph=binding,
            challenge_graph=challenges,
            checkpoint=checkpoint,
            outcomes={},
            trace_bundles={},
            counterexamples=[],
            repair_history=[],
            mechanism_memory={},
            root_recoveries=[],
            diff_closure_certificates=[],
            generator_session=session.record,
            remaining_budget=remaining,
            phase=ControllerPhase.SEMANTIC,
            artifact_ids={},
        )
        state.transition_phase(
            ControllerPhase.GRAPH_BUILD, event="semantic_assignment_frozen"
        )
        state.transition_phase(
            ControllerPhase.INCUMBENT_CLOSE, event="graph_stack_materialized"
        )
        executor = TraceExecutor(temporary_root=root / "tmp")
        stage_started = time.perf_counter()
        bundles = execute_challenges(
            challenges,
            executor,
            snapshot,
            snapshot,
        )
        analysis_timings["baseline_execution_initial_seconds"] = time.perf_counter() - stage_started
        # Baseline execution is also a targeted dynamic-analysis pass.  Merge
        # only stable observations, then rebuild the requirement-program
        # product so selected protocol targets and observed object shapes are
        # available to the first incumbent validation rather than stranded in
        # TraceBundle artifacts.
        stage_started = time.perf_counter()
        merge_trace_bundles(program, bundles, role="BASELINE")
        analysis_timings["program_graph_dynamic_merge_seconds"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        requirements = compile_assignment_overlay(semantic_result.graph, assignment)
        compile_requirement_paths(requirements, program)
        analysis_timings["requirement_graph_dynamic_rebuild_seconds"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        binding = build_binding_graph(requirements, program)
        analysis_timings["binding_graph_dynamic_rebuild_seconds"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        challenges = materialize_challenges(requirements, program, binding)
        analysis_timings["challenge_graph_dynamic_rebuild_seconds"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        bundles = execute_challenges(
            challenges,
            executor,
            snapshot,
            snapshot,
        )
        analysis_timings["baseline_execution_replay_seconds"] = time.perf_counter() - stage_started
        state.requirement_graph = requirements
        state.program_graph = program
        state.binding_graph = binding
        state.challenge_graph = challenges
        state.trace_bundles.update({item.paired_bundle_id: item for item in bundles})
        state.outcomes = outcomes_from_challenges(state, challenges, bundles)
        passed = [item for item in state.outcomes.values() if item.status == OutcomeStatus.PASS]
        failed = [item for item in state.outcomes.values() if item.status == OutcomeStatus.FAIL]
        unknown = [
            item for item in state.outcomes.values()
            if item.status not in {OutcomeStatus.PASS, OutcomeStatus.FAIL}
        ]
        checkpoint = replace(
            checkpoint,
            graph_hashes=state.graph_hashes(),
            pass_pairs=tuple(sorted((item.path_obligation_id, item.scenario_id or "") for item in passed)),
            fail_pairs=tuple(sorted((item.path_obligation_id, item.scenario_id or "") for item in failed)),
            unknown_pairs=tuple(sorted((item.path_obligation_id, item.scenario_id or "") for item in unknown)),
            blocked_path_obligation_ids=tuple(sorted({item.path_obligation_id for item in unknown})),
            executed_target_deficit=state.target_deficit(),
            safe=not any(
                item.kind == "PRESERVATION" and item.status != OutcomeStatus.PASS
                for item in state.outcomes.values()
            ),
        )
        state.checkpoint = checkpoint
        state.refresh_id()
        analysis_timings["analysis_total_seconds"] = time.perf_counter() - analysis_started
        manifest["analysis_timings"] = analysis_timings
        manifest["graph_summary"] = {
            "graph_count": 5,
            "graph_names": [
                "semantic_hypothesis_graph",
                "requirement_graph",
                "program_graph",
                "binding_graph",
                "challenge_graph",
            ],
            "graphs": {
                "semantic_hypothesis_graph": {"hash": semantic_result.graph.to_dict().get("graph_hash"), "artifact_ids": []},
                "requirement_graph": {"hash": requirements.semantic_layer_hash(), "artifact_ids": []},
                "program_graph": {"hash": program.program_hash(), "artifact_ids": []},
                "binding_graph": {"hash": binding.graph_hash(), "artifact_ids": []},
                "challenge_graph": {"hash": challenges.graph_hash(), "artifact_ids": []},
            },
            "expected_full_closure_graph_count": 5,
            "full_closure": False,
        }
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        artifacts.persist_graph_stack(state)
        self._persist_traces(artifacts, state)
        artifacts.put(
            "working_patch", patch, state=state,
            producer="reachpatch.checkpoint",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.put(
            "incumbent_checkpoint", checkpoint, state=state,
            producer="reachpatch.checkpoint",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.put(
            "generator_session", session.record, state=state,
            producer="reachpatch.generator-session",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.persist_state(state)
        return state

    @staticmethod
    def _persist_traces(artifacts: RunArtifacts, state: ReachAvoidState) -> None:
        for paired in state.trace_bundles.values():
            for bundle in (paired.base_bundle, paired.patch_bundle):
                artifacts.put(
                    "trace_bundle", bundle, state=state,
                    producer="reachpatch.executor",
                    confidence=Confidence.CONFIRMED,
                )

    def _persist_transition(self, state: ReachAvoidState, result) -> None:
        artifacts = RunArtifacts(state.run_root, state.instance_id)
        artifacts.put(
            "generator_revision" if hasattr(result.action, "revision_id") else "repair_action",
            result.action, state=state,
            producer="reachpatch.deepseek-agent" if hasattr(result.action, "revision_id") else "reachpatch.repair-policy",
            confidence=Confidence.HIGH,
        )
        for packet in result.counterexamples:
            artifacts.put(
                "counterexample", packet, state=state,
                producer="reachpatch.transition-gate",
                confidence=Confidence.CONFIRMED,
            )
        for comparison in result.certificate.graph_delta.get(
            "public_check_comparisons", ()
        ):
            artifacts.put(
                "public_check_comparison", comparison, state=state,
                producer="reachpatch.public-check-executor",
                confidence=Confidence.CONFIRMED,
                status=str(comparison.get("classification", "UNKNOWN_EXECUTION")),
            )
        if state.diff_closure_certificates:
            artifacts.put(
                "diff_closure_certificate",
                state.diff_closure_certificates[-1],
                state=state,
                producer="reachpatch.dicc",
                confidence=Confidence.CONFIRMED,
            )
        artifacts.put(
            "transition_certificate", result.certificate, state=state,
            producer="reachpatch.transition-gate",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.put(
            "working_patch", state.checkpoint.patch, state=state,
            producer="reachpatch.checkpoint",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.put(
            "incumbent_checkpoint", state.checkpoint, state=state,
            producer="reachpatch.checkpoint",
            confidence=Confidence.CONFIRMED,
        )
        if state.mechanism_memory:
            artifacts.put(
                "mechanism_memory",
                {key: [item.to_dict() for item in value] for key, value in state.mechanism_memory.items()},
                state=state,
                producer="reachpatch.repair-policy",
                confidence=Confidence.CONFIRMED,
            )
        artifacts.put(
            "generator_session", state.generator_session, state=state,
            producer="reachpatch.generator-session",
            confidence=Confidence.CONFIRMED,
        )
        if state.generator_conversation is not None:
            artifacts.put(
                "generator_conversation", state.generator_conversation,
                state=state, producer="reachpatch.deepseek-agent",
                confidence=Confidence.CONFIRMED,
            )
        if result.accepted:
            artifacts.persist_graph_stack(state)
            if state.repository_index is not None:
                artifacts.put(
                    "repository_index", state.repository_index, state=state,
                    producer="reachpatch.program-index-incremental",
                    confidence=Confidence.CONFIRMED,
                    status=(
                        "ANALYSIS_TRUNCATED"
                        if state.repository_index.parse_frontiers else "COMPLETE"
                    ),
                )
        self._persist_traces(artifacts, state)
        executed_probe_ids = set(
            state.runtime_metrics.get("executed_discriminator_probe_ids", ())
        )
        observations = tuple(
            dict(run.run.channels)
            for paired in state.trace_bundles.values()
            for run in paired.patch_bundle.runs
            if run.run.observation_reached
        )
        if observations:
            for raw in state.runtime_metrics.get("discriminator_probes", ()):
                probe = DiscriminatorProbe(
                    probe_id=str(raw["probe_id"]),
                    decision_id=str(raw["decision_id"]),
                    alternative_claim_ids=tuple(raw.get("alternative_claim_ids", ())),
                    observation_channels=tuple(raw.get("observation_channels", ())),
                    recipe_hint=dict(raw.get("recipe_hint", {})),
                    correctness_authority=str(raw.get("correctness_authority", "NONE")),
                )
                if probe.probe_id in executed_probe_ids:
                    continue
                discriminator_result = HypothesisDiscriminator().record(
                    probe, observations[:20], evidence_ids=(), selected_claim_id=None,
                )
                artifacts.put(
                    "discriminator_result", discriminator_result, state=state,
                    producer="reachpatch.discriminator-executor",
                    confidence=Confidence.CONFIRMED,
                    status=discriminator_result.correctness_status,
                )
                executed_probe_ids.add(probe.probe_id)
            state.runtime_metrics["executed_discriminator_probe_ids"] = sorted(
                executed_probe_ids
            )
        artifacts.persist_state(state)
        self._update_run_manifest(state)

    @staticmethod
    def _update_run_manifest(state: ReachAvoidState) -> None:
        path = Path(state.run_root) / "run_manifest.json"
        if not path.is_file():
            return
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["acceptance_metrics"] = dict(state.runtime_metrics)
        manifest["analysis_stats"] = dict(state.runtime_metrics)
        records = [
            dict(item) for item in state.runtime_metrics.get("graph_build_records", ())
            if isinstance(item, dict)
        ]
        if records:
            manifest["graph_build_records"] = records
            analysis_timings = dict(manifest.get("analysis_timings", {}))
            for key in (
                "program_graph_seconds", "requirement_graph_seconds",
                "binding_graph_seconds", "challenge_graph_seconds",
            ):
                total = sum(
                    float(item.get(key, 0.0) or 0.0)
                    for item in records
                    if item.get("kind") != "initial_active"
                )
                if not total:
                    continue
                analysis_timings[key.replace("_seconds", "_incremental_seconds")] = total
            manifest["analysis_timings"] = analysis_timings
            resources = dict(manifest.get("analysis_resources", {}))
            resources["graph_build_records"] = records
            resources["peak_rss_mib"] = max(
                [float(item.get("peak_rss_mib", 0.0) or 0.0) for item in records]
                + [float(resources.get("peak_rss_mib", 0.0) or 0.0)]
            )
            manifest["analysis_resources"] = resources
        manifest["graph_summary"] = {
            "graph_count": 5,
            "full_closure": in_target_set(state),
            "active_stack": True,
            "graphs": {
                "semantic_hypothesis_graph": {"hash": state.graph_hashes()["semantic"]},
                "requirement_graph": {"hash": state.graph_hashes()["requirement"]},
                "program_graph": {"hash": state.graph_hashes()["program"]},
                "binding_graph": {"hash": state.graph_hashes()["binding"]},
                "challenge_graph": {"hash": state.graph_hashes()["challenge"]},
            },
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def rebuild(self, run_root: str | Path) -> ReachAvoidState:
        root = self._assert_local(Path(run_root), "run root")
        manifest_path = root / "run_manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        instance_raw = dict(manifest["instance"])
        instance_raw["visible_tests"] = tuple(instance_raw.get("visible_tests", ()))
        instance = Instance(**instance_raw)
        artifacts = RunArtifacts(root, instance.instance_id)
        worktrees = WorktreeManager(root / "worktrees")
        transaction_recovery = worktrees.recover()

        checkpoint_envelope = artifacts.store.latest(
            instance.instance_id, "incumbent_checkpoint"
        )
        state_envelope = artifacts.store.latest(instance.instance_id, "reach_avoid_state")
        session_envelope = artifacts.store.latest(instance.instance_id, "generator_session")
        if checkpoint_envelope is None or state_envelope is None or session_envelope is None:
            raise AnalysisBlocked(
                "RECOVERY_BLOCKED",
                "run lacks a persisted checkpoint, state, or generator session",
            )
        checkpoint = _checkpoint(checkpoint_envelope.payload)
        snapshot = self._assert_local(Path(checkpoint.snapshot_tree), "checkpoint snapshot")
        if not snapshot.is_dir():
            raise AnalysisBlocked("RECOVERY_BLOCKED", "checkpoint snapshot is missing")
        if tree_hash(snapshot) != checkpoint.patch.working_tree_hash:
            raise AnalysisBlocked(
                "RECOVERY_BLOCKED", "checkpoint tree hash does not match WorkingPatch"
            )
        base_snapshot = None
        for candidate in sorted((root / "worktrees" / "checkpoints").glob("*/tree")):
            if tree_hash(candidate) == checkpoint.patch.base_tree_hash:
                base_snapshot = candidate.resolve()
                break
        if base_snapshot is None:
            raise AnalysisBlocked(
                "RECOVERY_BLOCKED", "base checkpoint for cumulative patch is missing"
            )

        state_raw = state_envelope.payload
        repository = self._assert_local(instance.repository_path(), "repository")
        visible_tests = tuple(
            str(self._assert_local(
                Path(path) if Path(path).is_absolute() else repository / path,
                "visible test",
            ))
            for path in instance.visible_tests
        )
        semantic_result = build_semantic_graph(
            instance.issue, visible_test_paths=visible_tests
        )
        current_hypotheses = build_hypothesis_set(semantic_result.graph)
        assignment = next((
            item for item in current_hypotheses.alternatives
            if item.assignment_id == checkpoint.assignment_id
        ), None)
        if assignment is None:
            raise AnalysisBlocked(
                "SEMANTIC_RESTART",
                "public evidence no longer contains the checkpoint hypothesis",
            )
        graph_envelopes = {
            artifact_type: artifacts.store.latest(instance.instance_id, artifact_type)
            for artifact_type in (
                "requirement_graph", "program_graph", "binding_graph",
                "challenge_graph", "repository_index", "hypothesis_set",
            )
        }
        missing_graphs = [
            key for key, envelope in graph_envelopes.items() if envelope is None
        ]
        if missing_graphs:
            raise AnalysisBlocked(
                "RECOVERY_BLOCKED",
                "active graph artifacts are missing: " + ", ".join(missing_graphs),
            )
        requirements = requirement_graph_from_dict(
            graph_envelopes["requirement_graph"].payload
        )
        program = program_graph_from_dict(graph_envelopes["program_graph"].payload)
        binding = binding_graph_from_dict(graph_envelopes["binding_graph"].payload)
        challenges = challenge_graph_from_dict(
            graph_envelopes["challenge_graph"].payload
        )
        repository_index = repository_index_from_dict(
            graph_envelopes["repository_index"].payload
        )
        persisted_hypotheses = hypothesis_set_from_dict(
            graph_envelopes["hypothesis_set"].payload
        )
        restored_hashes = {
            "semantic": semantic_result.graph.to_dict()["graph_hash"],
            "requirement": requirements.semantic_layer_hash(),
            "program": program.program_hash(),
            "binding": binding.graph_hash(),
            "challenge": challenges.graph_hash(),
        }
        stale = {
            key: (checkpoint.graph_hashes.get(key), digest)
            for key, digest in restored_hashes.items()
            if checkpoint.graph_hashes.get(key) not in {None, digest}
        }
        if stale:
            raise AnalysisBlocked(
                "RECOVERY_BLOCKED", f"persisted active graph hash mismatch: {stale}"
            )
        session_record = _generator_record(session_envelope.payload)
        mechanism_envelope = artifacts.store.latest(
            instance.instance_id, "mechanism_memory"
        )
        mechanism_raw = (
            mechanism_envelope.payload
            if mechanism_envelope is not None
            else state_raw.get("mechanism_memory", {})
        )
        counterexamples = [
            _counterexample(item.payload)
            for item in artifacts.store.list(
                artifact_type="counterexample", instance_id=instance.instance_id
            )
        ]
        history = [
            _transition_certificate(item.payload)
            for item in artifacts.store.list(
                artifact_type="transition_certificate", instance_id=instance.instance_id
            )
        ]
        closures = [
            _diff_closure(item.payload)
            for item in artifacts.store.list(
                artifact_type="diff_closure_certificate", instance_id=instance.instance_id
            )
        ]
        remaining = BudgetVector(**state_raw.get(
            "remaining_budget", checkpoint.remaining_budget.to_dict()
        ))
        conversation_envelope = artifacts.store.latest(
            instance.instance_id, "generator_conversation"
        )
        conversation = (
            conversation_from_dict(conversation_envelope.payload)
            if conversation_envelope is not None
            else GeneratorConversation.create(instance.instance_id)
        )
        state = ReachAvoidState(
            state_id=stable_id(
                "state", instance.instance_id, checkpoint.checkpoint_id,
                state_raw.get("transition_index", 0), "rebuild",
            ),
            instance_id=instance.instance_id,
            run_id=root.name,
            episode_id=checkpoint.episode_id,
            base_repository=str(base_snapshot),
            base_commit=instance.base_commit,
            run_root=str(root),
            assignment=assignment,
            semantic_graph=semantic_result.graph,
            requirement_graph=requirements,
            program_graph=program,
            binding_graph=binding,
            challenge_graph=challenges,
            checkpoint=checkpoint,
            outcomes={
                item.outcome_id: item
                for item in (
                    outcome_from_dict(raw) for raw in state_raw.get("outcomes", ())
                )
            },
            trace_bundles={},
            counterexamples=counterexamples,
            repair_history=history,
            mechanism_memory={
                key: [_mechanism_attempt(item) for item in values]
                for key, values in mechanism_raw.items()
            },
            root_recoveries=[],
            diff_closure_certificates=closures,
            generator_session=session_record,
            remaining_budget=remaining,
            phase=ControllerPhase(state_raw.get("phase", ControllerPhase.COUNTEREXAMPLE_FEEDBACK.value)),
            artifact_ids={
                key: list(values)
                for key, values in state_raw.get("artifact_ids", {}).items()
            },
            transition_index=int(state_raw.get("transition_index", 0)),
            phase_history=list(state_raw.get("phase_history", ())),
            hypothesis_set=persisted_hypotheses,
            repository_index=repository_index,
            generator_conversation=conversation,
            runtime_config=dict(state_raw.get("runtime_config", manifest.get("config", {}))),
            runtime_metrics=dict(state_raw.get("runtime_metrics", {})),
            termination_status=state_raw.get("termination_status"),
        )
        passed = [item for item in state.outcomes.values() if item.status == OutcomeStatus.PASS]
        failed = [item for item in state.outcomes.values() if item.status == OutcomeStatus.FAIL]
        unknown = [
            item for item in state.outcomes.values()
            if item.status not in {OutcomeStatus.PASS, OutcomeStatus.FAIL}
        ]
        state.checkpoint = replace(
            checkpoint,
            graph_hashes=state.graph_hashes(),
            pass_pairs=tuple(sorted(
                (item.path_obligation_id, item.scenario_id or "") for item in passed
            )),
            fail_pairs=tuple(sorted(
                (item.path_obligation_id, item.scenario_id or "") for item in failed
            )),
            unknown_pairs=tuple(sorted(
                (item.path_obligation_id, item.scenario_id or "") for item in unknown
            )),
            blocked_path_obligation_ids=tuple(sorted({
                item.path_obligation_id for item in unknown
            })),
            executed_target_deficit=state.target_deficit(),
            remaining_budget=remaining,
            safe=not any(
                item.kind == "PRESERVATION" and item.status != OutcomeStatus.PASS
                for item in state.outcomes.values()
            ),
            graph_reached=False,
        )
        state.refresh_id()
        artifacts.put(
            "recovery_audit",
            {
                "run_root": str(root),
                "checkpoint_id": state.checkpoint.checkpoint_id,
                "transaction_recovery": transaction_recovery,
                "graph_hashes": state.graph_hashes(),
                "replayed_bundle_ids": [],
                "restored_from_artifacts": True,
            },
            state=state,
            producer="reachpatch.recovery",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.persist_state(state)
        return state

    def _repair_tools(self, state: ReachAvoidState) -> RepairToolExecutor:
        if state.repository_index is None:
            raise RuntimeError("patch-first repair requires RepositoryIndex")
        public_checks = _named_public_checks(state)
        return RepairToolExecutor(
            repository_root=Path(state.checkpoint.snapshot_tree),
            repository_index=state.repository_index,
            current_diff=state.checkpoint.patch.canonical_diff,
            public_checks=public_checks,
            allowed_test_paths=set(
                state.runtime_config.get("visible_test_paths", ())
            ),
        )

    def _expand_generator_context(
        self,
        state: ReachAvoidState,
        requests: tuple[Any, ...],
    ) -> bool:
        """Expand only requested active files and reconnect affected products."""

        if not requests or state.repository_index is None:
            return False
        snapshot = Path(state.checkpoint.snapshot_tree)
        empty = reconcile_actual_diff(snapshot, snapshot)
        budget = GraphBudget.from_limits(
            seconds=self.config.program_slice_deadline_seconds,
            max_nodes=self.config.max_program_nodes,
            max_edges=self.config.max_program_edges,
            max_files=self.config.max_precise_files,
            max_functions=self.config.max_precise_functions,
            max_rss_mib=self.config.graph_memory_limit_mib,
            max_protocol_candidates_per_operation=(
                self.config.max_protocol_candidates_per_operation
            ),
        )
        previous_program = state.program_graph
        graph_started = time.perf_counter()
        delta = update_active_program_slice(
            previous_program, state.repository_index, snapshot, empty, None,
            tuple(requests), budget,
        )
        program_seconds = time.perf_counter() - graph_started
        if (
            not delta.added_node_ids and not delta.modified_node_ids
            and not delta.added_edge_ids
        ):
            return False
        graph_started = time.perf_counter()
        requirements = copy.deepcopy(state.requirement_graph)
        old_paths = set(requirements.path_obligations)
        selected_leaves = set(requirements.leaves)
        if requirements.path_obligations:
            requirements, removed_paths, added_paths = refresh_requirement_paths(
                requirements, delta.graph, affected_leaf_ids=selected_leaves,
                max_path_classes_per_leaf=self.config.max_path_classes_per_leaf,
                deadline=time.monotonic() + self.config.requirement_deadline_seconds,
            )
            affected_paths = set(removed_paths) | set(added_paths)
        else:
            compile_requirement_paths(
                requirements, delta.graph,
                max_open_world_seeds=min(64, self.config.max_precise_functions),
                max_observation_nodes=min(128, self.config.max_program_nodes),
                max_paths_per_entry=self.config.max_path_classes_per_leaf,
                max_path_classes_per_leaf=self.config.max_path_classes_per_leaf,
                promote_all_program_predicates=False,
                deadline=time.monotonic() + self.config.requirement_deadline_seconds,
            )
            affected_paths = set(requirements.path_obligations) - old_paths
        affected_paths.update(
            obligation.path_obligation_id
            for obligation in requirements.path_obligations.values()
            if set(obligation.path_edge_ids) & set(delta.added_edge_ids)
        )
        requirement_seconds = time.perf_counter() - graph_started
        graph_started = time.perf_counter()
        binding = build_active_binding_graph(
            requirements, delta.graph, previous=state.binding_graph,
            affected_leaf_ids=selected_leaves,
            affected_path_ids=affected_paths,
            max_target_units=self.config.max_active_target_bindings,
            max_preservation_units=self.config.max_active_preservation_bindings,
            deadline=time.monotonic() + self.config.binding_deadline_seconds,
        )
        binding_seconds = time.perf_counter() - graph_started
        cumulative = reconcile_actual_diff(state.base_repository, snapshot)
        graph_started = time.perf_counter()
        challenges = materialize_active_challenges(
            requirements, delta.graph, binding, actual_diff=cumulative,
            previous_outcomes=state.outcomes,
            max_challenges=self.config.max_active_challenges,
            deadline=time.monotonic() + self.config.challenge_deadline_seconds,
        )
        challenge_seconds = time.perf_counter() - graph_started
        state.requirement_graph = requirements
        state.program_graph = delta.graph
        state.binding_graph = binding
        state.challenge_graph = challenges
        state.checkpoint = replace(
            state.checkpoint, graph_hashes=state.graph_hashes()
        )
        state.runtime_metrics["targeted_slice_expansions"] = int(
            state.runtime_metrics.get("targeted_slice_expansions", 0)
        ) + 1
        state.runtime_metrics["last_context_added_nodes"] = len(delta.added_node_ids)
        state.runtime_metrics.setdefault("graph_build_records", []).append({
            "kind": "context_expansion",
            "program_graph_seconds": program_seconds,
            "requirement_graph_seconds": requirement_seconds,
            "binding_graph_seconds": binding_seconds,
            "challenge_graph_seconds": challenge_seconds,
            "total_seconds": program_seconds + requirement_seconds + binding_seconds + challenge_seconds,
            "program_nodes": len(delta.graph.nodes),
            "program_edges": len(delta.graph.edges),
            "requirement_leaves": len(requirements.leaves),
            "requirement_path_obligations": len(requirements.path_obligations),
            "binding_units": len(binding.units),
            "active_binding_units": binding.build_stats.get("active_count", 0),
            "deferred_binding_units": binding.build_stats.get("deferred_count", 0),
            "challenge_cells": len(challenges.cells),
            "peak_rss_mib": delta.build.peak_rss_mib,
            "truncated": bool(delta.build.truncated_reason),
        })
        state.refresh_id()
        artifacts = RunArtifacts(state.run_root, state.instance_id)
        artifacts.persist_graph_stack(state)
        artifacts.persist_state(state)
        return True

    def _record_generator_block(
        self,
        state: ReachAvoidState,
        error: GeneratorBlockedExternal,
    ) -> None:
        state.termination_status = "GENERATOR_BLOCKED_EXTERNAL"
        state.runtime_metrics.update({
            "generator_external_error": str(error),
            "generator_external_operation": error.operation,
        })
        artifacts = RunArtifacts(state.run_root, state.instance_id)
        artifacts.put(
            "generator_failure",
            {
                "operation": error.operation,
                "error_type": error.cause_type,
                "detail": error.detail,
            },
            state=state,
            producer="reachpatch.deepseek-agent",
            confidence=Confidence.CONFIRMED,
            status="BLOCKED_EXTERNAL",
        )
        if state.generator_conversation is not None:
            artifacts.put(
                "generator_conversation", state.generator_conversation,
                state=state, producer="reachpatch.deepseek-agent",
                confidence=Confidence.CONFIRMED,
                status="BLOCKED_EXTERNAL",
            )
        artifacts.persist_state(state)
        self._update_run_manifest(state)

    def _record_action_rejection(
        self,
        state: ReachAvoidState,
        revision,
        conversion,
    ) -> None:
        rejection = {
            "revision_id": revision.revision_id,
            "status": conversion.status.value,
            "reasons": list(conversion.reasons),
        }
        state.runtime_metrics.setdefault("rejected_generator_actions", []).append(
            rejection
        )
        state.runtime_metrics.setdefault("failed_generator_mechanisms", []).append(
            revision.mechanism
        )
        artifacts = RunArtifacts(state.run_root, state.instance_id)
        artifacts.put(
            "generator_action_rejection", rejection, state=state,
            producer="reachpatch.action-conversion",
            confidence=Confidence.CONFIRMED,
            status=conversion.status.value,
        )
        if state.generator_conversation is not None:
            artifacts.put(
                "generator_conversation", state.generator_conversation,
                state=state, producer="reachpatch.deepseek-agent",
                confidence=Confidence.CONFIRMED,
            )
        artifacts.persist_state(state)
        self._update_run_manifest(state)

    def _drive_patch_first(
        self,
        state: ReachAvoidState,
        session: PersistentGeneratorSession,
    ) -> tuple[ReachAvoidState, TerminalCertificate]:
        if self.generator_agent is None or state.generator_conversation is None:
            raise RuntimeError("patch-first drive requires a persistent generator agent")
        conversation = state.generator_conversation
        nonprogress = 0
        submitted = int(state.runtime_metrics.get(
            "submitted_generator_revisions",
            state.runtime_metrics.get("deepseek_initial_generation_count", 0),
        ))
        # analyze() owns the initial generation. A context-only initial answer
        # reaches this loop with transition_index == 0 and is retried after the
        # requested local slice has been materialized.
        while submitted < self.config.max_submitted_revisions:
            if in_target_set(state):
                break
            terminal = terminal_avoid_reason(state)
            if terminal:
                state.termination_status = terminal
                break
            pending_requests = tuple(conversation.pending_context_requests)
            if pending_requests:
                self._expand_generator_context(state, pending_requests)
                conversation.pending_context_requests.clear()
            packets = tuple(
                packet for packet in state.counterexamples
                if packet.counterexample_id not in conversation.delivered_counterexamples
            )
            if state.phase == ControllerPhase.TRANSITION_GATE:
                state.transition_phase(
                    ControllerPhase.COUNTEREXAMPLE_FEEDBACK,
                    event="revision_requires_further_evidence",
                )
            if state.phase == ControllerPhase.INITIAL_GENERATION:
                state.transition_phase(
                    ControllerPhase.ROOT_RECOVERY,
                    event="initial_generation_requested_context",
                )
            if nonprogress >= self.config.nonprogress_before_root_recovery:
                state.transition_phase(
                    ControllerPhase.ROOT_RECOVERY, event="patch_first_nonprogress"
                )
                tools = self._repair_tools(state)
                try:
                    revision = self.generator_agent.root_recovery(
                        state, conversation, tools
                    )
                except GeneratorBlockedExternal as exc:
                    self._record_generator_block(state, exc)
                    break
                nonprogress = 0
            else:
                if state.phase != ControllerPhase.REPAIR_GENERATION:
                    state.transition_phase(
                        ControllerPhase.REPAIR_GENERATION,
                        event="counterexample_repair_requested",
                    )
                tools = self._repair_tools(state)
                try:
                    if packets:
                        revision = self.generator_agent.repair_from_counterexamples(
                            state, conversation, packets, tools
                        )
                        state.runtime_metrics["deepseek_repair_count"] = int(
                            state.runtime_metrics.get("deepseek_repair_count", 0)
                        ) + 1
                    else:
                        revision = self.generator_agent.root_recovery(
                            state, conversation, tools
                        )
                        state.runtime_metrics["deepseek_root_recovery_count"] = int(
                            state.runtime_metrics.get("deepseek_root_recovery_count", 0)
                        ) + 1
                except GeneratorBlockedExternal as exc:
                    self._record_generator_block(state, exc)
                    break
            submitted += 1
            state.runtime_metrics["submitted_generator_revisions"] = submitted
            state.runtime_metrics["deepseek_tool_turns"] = int(
                state.runtime_metrics.get("deepseek_tool_turns", 0)
            ) + revision.tool_turns
            state.generator_session = replace(
                state.generator_session,
                cursor=state.generator_session.cursor + 1,
                internal_tool_turns=(
                    state.generator_session.internal_tool_turns
                    + revision.tool_turns
                ),
            )
            if not revision.edits:
                conversation.pending_context_requests.extend(
                    request for request in revision.context_requests
                    if request not in conversation.pending_context_requests
                )
                nonprogress += 1
                if not revision.context_requests:
                    state.runtime_metrics["generator_contextless_revisions"] = int(
                        state.runtime_metrics.get("generator_contextless_revisions", 0)
                    ) + 1
                continue
            conversion = convert_revision_action(state, revision)
            if conversion.status not in {
                ActionConversionStatus.ACCEPTED,
                ActionConversionStatus.NEEDS_SLICE_EXPANSION,
            }:
                self._record_action_rejection(state, revision, conversion)
                nonprogress += 1
                continue
            revision = conversion.revision
            result = evaluate_patch_revision(state, revision)
            if result.accepted:
                state.runtime_metrics["accepted_transitions"] = int(
                    state.runtime_metrics.get("accepted_transitions", 0)
                ) + 1
                nonprogress = 0
            else:
                state.runtime_metrics["rolled_back_transitions"] = int(
                    state.runtime_metrics.get("rolled_back_transitions", 0)
                ) + 1
                state.runtime_metrics.setdefault(
                    "failed_generator_mechanisms", []
                ).append(revision.mechanism)
                nonprogress += 1
            self._persist_transition(state, result)
        if submitted >= self.config.max_submitted_revisions and not in_target_set(state):
            state.termination_status = "BUDGET_EXHAUSTED"
        elif not in_target_set(state) and state.termination_status is None:
            state.termination_status = "GENERATOR_NONPROGRESS"
        return state, self.seal(state, session)

    def _drive(
        self,
        state: ReachAvoidState,
        session: PersistentGeneratorSession,
    ) -> tuple[ReachAvoidState, TerminalCertificate]:
        if self.generator_agent is not None:
            return self._drive_patch_first(state, session)
        state.termination_status = "GENERATOR_UNAVAILABLE"
        state.runtime_metrics["generator_unavailable"] = True
        return state, self.seal(state, session)

    def run(
        self,
        instance: Instance,
        *,
        run_root: str | Path | None = None,
        budget: BudgetVector | None = None,
    ) -> tuple[ReachAvoidState, TerminalCertificate]:
        state = self.analyze(instance, run_root=run_root, budget=budget)
        session = PersistentGeneratorSession.from_record(
            state.generator_session,
            action_provider=self.action_provider,
        )
        return self._drive(state, session)

    def resume(
        self,
        run_root: str | Path,
    ) -> tuple[ReachAvoidState, TerminalCertificate]:
        root = self._assert_local(Path(run_root), "run root")
        if (root / "terminal_certificate.json").exists():
            raise AnalysisBlocked("RUN_SEALED", "a sealed run cannot accept more transitions")
        state = self.rebuild(root)
        session = PersistentGeneratorSession.from_record(
            state.generator_session,
            action_provider=self.action_provider,
        )
        return self._drive(state, session)

    @staticmethod
    def _charge_ablation_execution(
        state: ReachAvoidState,
        bundles: Iterable[Any],
    ) -> None:
        seconds = sum(
            run.duration_seconds
            for paired in bundles
            for bundle in (paired.base_bundle, paired.patch_bundle)
            for run in bundle.runs
        )
        try:
            state.remaining_budget = state.remaining_budget.subtract(BudgetVector(
                execution_seconds=seconds,
                wall_seconds=seconds,
            ))
        except Exception:
            state.remaining_budget.execution_seconds = 0.0
            state.remaining_budget.wall_seconds = 0.0

    def _ablate_reached_patch(
        self,
        state: ReachAvoidState,
        session: PersistentGeneratorSession | None,
    ) -> EditRetentionAblation:
        state.transition_phase(
            ControllerPhase.ABLATE, event="graph_reached_begin_edit_retention"
        )
        manager = WorktreeManager(Path(state.run_root) / "worktrees")
        executor = TraceExecutor(temporary_root=Path(state.run_root) / "tmp")
        evaluations: dict[str, _AblationEvaluation] = {}
        attempt_closures: list[DiffClosureCertificate] = []
        attempt_number = 0

        def validate(
            current_tree: Path,
            trial_tree: Path,
            candidate_diff: ActualDiff,
        ) -> AblationValidation:
            nonlocal attempt_number
            attempt_number += 1
            previous_evaluation = evaluations.get(tree_hash(current_tree))
            current_program = (
                previous_evaluation.program_graph
                if previous_evaluation else state.program_graph
            )
            current_requirements = (
                previous_evaluation.requirement_graph
                if previous_evaluation else state.requirement_graph
            )
            current_binding = (
                previous_evaluation.binding_graph
                if previous_evaluation else state.binding_graph
            )
            incremental = reconcile_actual_diff(
                current_tree,
                trial_tree,
                forbidden_patterns=self.config.forbidden_patterns,
            )
            if state.repository_index is None:
                raise RuntimeError("ablation requires the persisted RepositoryIndex")
            graph_budget = GraphBudget.from_limits(
                seconds=self.config.program_slice_deadline_seconds,
                max_nodes=self.config.max_program_nodes,
                max_edges=self.config.max_program_edges,
                max_files=self.config.max_precise_files,
                max_functions=self.config.max_precise_functions,
                max_rss_mib=self.config.graph_memory_limit_mib,
                max_protocol_candidates_per_operation=(
                    self.config.max_protocol_candidates_per_operation
                ),
            )
            program_delta = update_active_program_slice(
                current_program, state.repository_index, trial_tree,
                incremental, None, (), graph_budget,
            )
            candidate_program = program_delta.graph
            candidate_requirements = copy.deepcopy(current_requirements)
            requirement_delta = promote_domains_from_diff(
                candidate_requirements, candidate_program, incremental, None,
                deadline=(
                    time.monotonic() + self.config.requirement_deadline_seconds
                ),
            )
            changed_nodes = set(
                program_delta.added_node_ids + program_delta.removed_node_ids
                + program_delta.modified_node_ids
            )
            affected_paths = {
                unit.path_obligation_id
                for unit in current_binding.units.values()
                if changed_nodes & set(unit.interaction_path_ids)
            }
            affected_leaves = set(requirement_delta.affected_leaf_ids)
            affected_leaves.update(
                current_requirements.path_obligations[path_id].leaf_id
                for path_id in affected_paths
                if path_id in current_requirements.path_obligations
            )
            if affected_leaves:
                candidate_requirements, removed_paths, added_paths = refresh_requirement_paths(
                    candidate_requirements, candidate_program,
                    affected_leaf_ids=affected_leaves,
                    max_path_classes_per_leaf=self.config.max_path_classes_per_leaf,
                    deadline=time.monotonic() + self.config.requirement_deadline_seconds,
                )
                affected_paths.update(removed_paths)
                affected_paths.update(added_paths)
            candidate_binding = build_active_binding_graph(
                candidate_requirements, candidate_program,
                previous=current_binding, affected_leaf_ids=affected_leaves,
                affected_path_ids=affected_paths,
                max_target_units=self.config.max_active_target_bindings,
                max_preservation_units=self.config.max_active_preservation_bindings,
                deadline=time.monotonic() + self.config.binding_deadline_seconds,
            )
            cumulative = reconcile_actual_diff(
                state.base_repository,
                trial_tree,
                forbidden_patterns=self.config.forbidden_patterns,
            )
            candidate_challenges = materialize_active_challenges(
                candidate_requirements,
                candidate_program,
                candidate_binding,
                actual_diff=cumulative,
                previous_outcomes=state.outcomes,
                max_challenges=self.config.max_active_challenges,
                deadline=time.monotonic() + self.config.challenge_deadline_seconds,
            )
            candidate_bundles = execute_challenges(
                candidate_challenges,
                executor,
                state.base_repository,
                trial_tree,
            )

            dicc_challenges = materialize_active_challenges(
                current_requirements, current_program, current_binding,
                actual_diff=incremental, previous_outcomes=state.outcomes,
                max_challenges=self.config.max_active_challenges,
                deadline=time.monotonic() + self.config.challenge_deadline_seconds,
            )
            update_id = stable_id(
                "ablation-update",
                current_tree.parent.name,
                incremental.canonical_diff_hash,
                attempt_number,
            )
            plan = diff_induced_challenge_plan(
                current_requirements,
                current_program,
                candidate_program,
                current_binding,
                dicc_challenges,
                incremental,
                update_id=update_id,
                trial_requirement_graph=candidate_requirements,
            )
            dicc_bundles = execute_challenges(
                dicc_challenges,
                executor,
                state.base_repository,
                trial_tree,
            )
            closure = finalize_diff_induced_challenge_closure(
                plan,
                dicc_challenges,
                checkpoint_id=current_tree.parent.name,
                transition_index=state.transition_index + attempt_number,
                causal_touch_witnesses={},
            )
            attempt_closures.append(closure)

            bundles_by_id = {
                item.paired_bundle_id: item
                for item in (*candidate_bundles, *dicc_bundles)
            }
            bundles = tuple(bundles_by_id.values())
            state.trace_bundles.update(bundles_by_id)
            self._charge_ablation_execution(state, bundles)

            candidate_state = copy.copy(state)
            candidate_state.requirement_graph = candidate_requirements
            candidate_state.program_graph = candidate_program
            candidate_state.binding_graph = candidate_binding
            candidate_state.challenge_graph = candidate_challenges
            candidate_state.outcomes = outcomes_from_challenges(
                candidate_state, candidate_challenges, candidate_bundles
            )
            preservation_pass = all(
                item.status == OutcomeStatus.PASS
                for item in candidate_state.outcomes.values()
                if item.kind == "PRESERVATION"
            )
            safe = all((
                closure.commit_safety_closed,
                preservation_pass,
                not cumulative.forbidden_paths,
                not cumulative.oracle_contamination_paths,
            ))
            candidate_patch = WorkingPatch(
                version=state.checkpoint.patch.version + attempt_number,
                base_commit=state.base_commit,
                canonical_diff=cumulative.canonical_diff,
                canonical_diff_hash=cumulative.canonical_diff_hash,
                base_tree_hash=cumulative.base_tree_hash,
                working_tree_hash=cumulative.trial_tree_hash,
                parent_patch_hash=state.checkpoint.patch.canonical_diff_hash,
                checkpoint_id=stable_id(
                    "ablation-candidate", update_id, cumulative.canonical_diff_hash
                ),
            )
            candidate_state.checkpoint = replace(
                state.checkpoint,
                patch=candidate_patch,
                snapshot_tree=str(trial_tree),
                graph_hashes=candidate_state.graph_hashes(),
                remaining_budget=state.remaining_budget,
                safe=safe,
                graph_reached=False,
            )
            candidate_state.diff_closure_certificates = [
                *state.diff_closure_certificates,
                closure,
            ]
            graph_reached = in_target_set(candidate_state)
            evaluations[cumulative.trial_tree_hash] = _AblationEvaluation(
                requirement_graph=candidate_requirements,
                program_graph=candidate_program,
                binding_graph=candidate_binding,
                challenge_graph=candidate_challenges,
                outcomes=candidate_state.outcomes,
                bundles=bundles,
                closure=closure,
                cumulative_diff=cumulative,
                safe=safe,
                graph_reached=graph_reached,
            )
            return AblationValidation(
                graph_reached=graph_reached,
                safe=safe,
                closure_closed=closure.diff_challenge_closed,
                details={
                    "candidate_diff_hash": candidate_diff.canonical_diff_hash,
                    "cumulative_diff_hash": cumulative.canonical_diff_hash,
                    "closure_id": closure.closure_id,
                    "graph_hashes": candidate_state.graph_hashes(),
                    "outcome_ids": sorted(candidate_state.outcomes),
                    "paired_bundle_ids": sorted(bundles_by_id),
                    "preservation_pass": preservation_pass,
                },
            )

        source_checkpoint = state.checkpoint
        result = edit_retention_ablation(
            manager,
            base_tree=state.base_repository,
            checkpoint_id=source_checkpoint.checkpoint_id,
            validate=validate,
            max_groups=self.config.max_ablation_groups,
        )
        if result.removed_group_ids:
            evaluation = evaluations.get(result.final_diff.trial_tree_hash)
            if evaluation is None:
                raise RuntimeError("committed ablation lacks its validation snapshot")
            state.requirement_graph = evaluation.requirement_graph
            state.program_graph = evaluation.program_graph
            state.binding_graph = evaluation.binding_graph
            state.challenge_graph = evaluation.challenge_graph
            state.outcomes = evaluation.outcomes
            state.diff_closure_certificates.append(evaluation.closure)
            if session is not None:
                session.resume(result.final_checkpoint_id, ())
                state.generator_session = session.record
            patch = WorkingPatch(
                version=source_checkpoint.patch.version + len(result.removed_group_ids),
                base_commit=state.base_commit,
                canonical_diff=result.final_diff.canonical_diff,
                canonical_diff_hash=result.final_diff.canonical_diff_hash,
                base_tree_hash=result.final_diff.base_tree_hash,
                working_tree_hash=result.final_diff.trial_tree_hash,
                parent_patch_hash=source_checkpoint.patch.canonical_diff_hash,
                checkpoint_id=result.final_checkpoint_id,
            )
            passed = [
                item for item in state.outcomes.values()
                if item.status == OutcomeStatus.PASS
            ]
            failed = [
                item for item in state.outcomes.values()
                if item.status == OutcomeStatus.FAIL
            ]
            unknown = [
                item for item in state.outcomes.values()
                if item.status not in {OutcomeStatus.PASS, OutcomeStatus.FAIL}
            ]
            state.checkpoint = replace(
                source_checkpoint,
                checkpoint_id=result.final_checkpoint_id,
                parent_checkpoint_id=source_checkpoint.checkpoint_id,
                snapshot_tree=result.final_snapshot_tree,
                patch=patch,
                actual_fingerprint=result.final_diff.fingerprint,
                graph_hashes=state.graph_hashes(),
                pass_pairs=tuple(sorted(
                    (item.path_obligation_id, item.scenario_id or "") for item in passed
                )),
                fail_pairs=tuple(sorted(
                    (item.path_obligation_id, item.scenario_id or "") for item in failed
                )),
                unknown_pairs=tuple(sorted(
                    (item.path_obligation_id, item.scenario_id or "") for item in unknown
                )),
                blocked_path_obligation_ids=tuple(sorted({
                    item.path_obligation_id for item in unknown
                })),
                executed_target_deficit=state.target_deficit(),
                generator_session_cursor=str(state.generator_session.cursor),
                remaining_budget=state.remaining_budget,
                safe=evaluation.safe,
                graph_reached=evaluation.graph_reached,
                created_at=utc_now(),
            )
            state.refresh_id()
        else:
            state.checkpoint = replace(
                state.checkpoint,
                remaining_budget=state.remaining_budget,
            )

        artifacts = RunArtifacts(state.run_root, state.instance_id)
        for closure in attempt_closures:
            artifacts.put(
                "diff_closure_certificate",
                closure,
                state=state,
                producer="reachpatch.edit-retention-ablation",
                confidence=Confidence.CONFIRMED,
            )
        if result.removed_group_ids:
            artifacts.persist_graph_stack(state)
        self._persist_traces(artifacts, state)
        artifacts.put(
            "working_patch",
            state.checkpoint.patch,
            state=state,
            producer="reachpatch.edit-retention-ablation",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.put(
            "incumbent_checkpoint",
            state.checkpoint,
            state=state,
            producer="reachpatch.edit-retention-ablation",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.put(
            "edit_retention_ablation",
            result,
            state=state,
            producer="reachpatch.edit-retention-ablation",
            confidence=Confidence.CONFIRMED,
            status="REMOVED" if result.removed_group_ids else "RETAINED",
        )
        state.transition_phase(
            ControllerPhase.INCUMBENT_CLOSE, event="edit_retention_complete"
        )
        artifacts.persist_state(state)
        return result

    def seal(
        self,
        state: ReachAvoidState,
        session: PersistentGeneratorSession | None = None,
    ) -> TerminalCertificate:
        reached = in_target_set(state)
        if (
            reached and state.checkpoint.patch.canonical_diff
            and self.config.enable_ablation
            and state.remaining_budget.available()
        ):
            if state.phase != ControllerPhase.INCUMBENT_CLOSE:
                state.transition_phase(
                    ControllerPhase.INCUMBENT_CLOSE,
                    event="prepare_edit_retention_ablation",
                )
            self._ablate_reached_patch(state, session)
            reached = in_target_set(state)
        status = "GRAPH_REACHED" if reached else (
            state.termination_status or "UNCERTIFIED_BUDGET_EXHAUSTED"
        )
        if session is not None:
            session.seal()
            state.generator_session = session.record
        state.transition_phase(ControllerPhase.SEALED, event="seal_terminal")
        state.termination_status = status
        state.checkpoint = replace(
            state.checkpoint,
            graph_reached=reached,
        )
        state.runtime_metrics.update({
            "candidate_binding_count": sum(
                unit.status == "CANDIDATE"
                for unit in state.binding_graph.units.values()
            ),
            "active_binding_count": sum(
                unit.status in {"ACTIVE", "READY"}
                for unit in state.binding_graph.units.values()
            ),
            "deferred_binding_count": sum(
                unit.status == "DEFERRED"
                for unit in state.binding_graph.units.values()
            ),
            "active_challenge_count": len(state.challenge_graph.cells),
            "final_patch_nonempty": bool(state.checkpoint.patch.canonical_diff),
            "final_patch_hash": state.checkpoint.patch.canonical_diff_hash,
            "final_status": status,
            "transition_count": state.transition_index,
        })
        final_patch = Path(state.run_root) / "final_patch.diff"
        final_patch.write_text(state.checkpoint.patch.canonical_diff, encoding="utf-8")
        artifacts = RunArtifacts(state.run_root, state.instance_id)
        artifacts.persist_state(state)
        self._update_run_manifest(state)
        verification = artifacts.store.verify()
        if not verification["valid"]:
            raise RuntimeError(
                "refusing to seal an artifact-inconsistent run: "
                + "; ".join(str(item) for item in verification["errors"])
            )
        unresolved_paths = tuple(sorted({
            item.path_obligation_id
            for item in state.outcomes.values()
            if item.status != OutcomeStatus.PASS
        }))
        unresolved_frontiers = tuple(sorted({
            frontier.frontier_id
            for source in (
                state.requirement_graph.frontiers.values(),
                state.program_graph.frontiers.values(),
                state.binding_graph.frontiers.values(),
                state.challenge_graph.frontiers.values(),
            )
            for frontier in source
            if frontier.status == "OPEN"
        }))
        target_outcomes = [
            item for item in state.outcomes.values() if item.kind == "TARGET"
        ]
        preservation_outcomes = [
            item for item in state.outcomes.values() if item.kind == "PRESERVATION"
        ]
        target_complete = bool(target_outcomes) and all(
            item.status == OutcomeStatus.PASS for item in target_outcomes
        )
        preservation_complete = all(
            item.status == OutcomeStatus.PASS for item in preservation_outcomes
        )
        shadow_complete = all(
            item.component_shadow_pass for item in state.repair_history
        ) if state.repair_history else not bool(state.checkpoint.patch.canonical_diff)
        closure_complete = all(
            item.diff_challenge_closed for item in state.diff_closure_certificates
        ) if state.diff_closure_certificates else not bool(state.checkpoint.patch.canonical_diff)
        certificate = TerminalCertificate(
            instance_id=state.instance_id,
            episode_id=state.episode_id,
            status=status,
            final_checkpoint_id=state.checkpoint.checkpoint_id,
            final_diff_hash=state.checkpoint.patch.canonical_diff_hash,
            graph_reached=reached,
            target_complete=target_complete and not unresolved_paths,
            preservation_complete=preservation_complete,
            shadow_complete=shadow_complete,
            closure_complete=closure_complete,
            unresolved_path_obligation_ids=unresolved_paths,
            unresolved_frontier_ids=unresolved_frontiers,
            terminal_reason=status,
            graph_hashes=state.graph_hashes(),
            environment_hash=state.checkpoint.environment_hash,
            remaining_budget=state.remaining_budget,
            artifact_verification_hash=artifacts.store.verification_digest(
                exclude_types={"terminal_certificate"}
            ),
        )
        artifacts.put(
            "terminal_certificate", certificate, state=state,
            producer="reachpatch.controller",
            confidence=Confidence.CONFIRMED,
            status=status,
        )
        (Path(state.run_root) / "terminal_certificate.json").write_text(
            json.dumps(certificate.to_dict(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return certificate
