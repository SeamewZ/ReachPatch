from __future__ import annotations

import copy
import json
import os
import platform
import resource
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from reachpatch.adapters import select_adapter
from reachpatch.binding_graph import build_binding_graph
from reachpatch.challenge_graph.dicc import (
    diff_induced_challenge_plan,
    finalize_diff_induced_challenge_closure,
)
from reachpatch.challenge_graph.materialize import execute_challenges, materialize_challenges
from reachpatch.challenge_graph.models import DiffClosureCertificate
from reachpatch.evidence import build_semantic_graph, freeze_assignment
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
from reachpatch.oracle.discriminator import HypothesisDiscriminator
from reachpatch.program_graph.builder import build_augmented_program_graph
from reachpatch.program_graph.tracing import merge_trace_bundles
from reachpatch.reach_avoid.gates import in_target_set, terminal_avoid_reason
from reachpatch.reach_avoid.persistence import RunArtifacts
from reachpatch.reach_avoid.state import outcomes_from_challenges
from reachpatch.reach_avoid.transition import evaluate_single_update
from reachpatch.repair.ablation import (
    AblationValidation,
    EditRetentionAblation,
    edit_retention_ablation,
)
from reachpatch.repair.policy import next_untried_repair_intent, select_losing_core
from reachpatch.repair.recovery import root_recovery
from reachpatch.repair.session import ActionProvider, PersistentGeneratorSession
from reachpatch.requirement_graph import compile_assignment_overlay, compile_requirement_paths


class AnalysisBlocked(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ReachPatchConfig(SerializableRecord):
    selection_mode: str = "certified"
    max_submitted_revisions: int = 10
    max_internal_tool_turns_per_revision: int = 12
    equivalent_failures_before_new_mechanism: int = 2
    nonprogress_before_root_recovery: int = 3
    max_ablation_groups: int = 32
    forbidden_patterns: tuple[str, ...] = (
        "tests/**", "test/**", "**/test_*.py", "**/*_test.py",
    )
    mechanical_commands: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self) -> None:
        if self.selection_mode not in {"certified", "benchmark"}:
            raise ValueError("selection_mode must be certified or benchmark")
        if self.max_submitted_revisions < 1:
            raise ValueError("max_submitted_revisions must be positive")
        if self.max_ablation_groups < 1:
            raise ValueError("max_ablation_groups must be positive")


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
        implementation_root: str | Path | None = None,
    ) -> None:
        self.config = config or ReachPatchConfig()
        self.action_provider = action_provider
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
            "full_closure": True,
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
            "repair_action", result.action, state=state,
            producer="reachpatch.repair-policy",
            confidence=Confidence.HIGH,
        )
        for packet in result.counterexamples:
            artifacts.put(
                "counterexample", packet, state=state,
                producer="reachpatch.transition-gate",
                confidence=Confidence.CONFIRMED,
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
        if result.accepted:
            artifacts.persist_graph_stack(state)
        self._persist_traces(artifacts, state)
        artifacts.persist_state(state)

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
        assignment = freeze_assignment(
            semantic_result.graph, selection_mode=self.config.selection_mode
        )
        if assignment is None or assignment.assignment_id != checkpoint.assignment_id:
            raise AnalysisBlocked(
                "SEMANTIC_RESTART",
                "replayed public evidence no longer produces the checkpoint assignment",
            )
        program = build_augmented_program_graph(snapshot)
        requirements = compile_assignment_overlay(semantic_result.graph, assignment)
        compile_requirement_paths(requirements, program)
        binding = build_binding_graph(requirements, program)
        challenges = materialize_challenges(
            requirements,
            program,
            binding,
            diff_hash=(
                checkpoint.patch.canonical_diff_hash
                if checkpoint.patch.canonical_diff else "BASELINE"
            ),
        )
        executor = TraceExecutor(temporary_root=root / "tmp")
        bundles = execute_challenges(
            challenges, executor, base_snapshot, snapshot
        )

        state_raw = state_envelope.payload
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
        execution_seconds = sum(
            run.duration_seconds
            for paired in bundles
            for trace_bundle in (paired.base_bundle, paired.patch_bundle)
            for run in trace_bundle.runs
        )
        try:
            remaining = remaining.subtract(BudgetVector(
                execution_seconds=execution_seconds,
                wall_seconds=execution_seconds,
            ))
        except Exception:
            remaining.execution_seconds = 0.0
            remaining.wall_seconds = 0.0
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
            outcomes={},
            trace_bundles={item.paired_bundle_id: item for item in bundles},
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
            phase=ControllerPhase.INCUMBENT_CLOSE,
            artifact_ids={
                key: list(values)
                for key, values in state_raw.get("artifact_ids", {}).items()
            },
            transition_index=int(state_raw.get("transition_index", 0)),
            phase_history=list(state_raw.get("phase_history", ())),
        )
        state.outcomes = outcomes_from_challenges(state, challenges, bundles)
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
                "replayed_bundle_ids": sorted(state.trace_bundles),
            },
            state=state,
            producer="reachpatch.recovery",
            confidence=Confidence.CONFIRMED,
        )
        artifacts.persist_graph_stack(state)
        self._persist_traces(artifacts, state)
        artifacts.persist_state(state)
        return state

    def _drive(
        self,
        state: ReachAvoidState,
        session: PersistentGeneratorSession,
    ) -> tuple[ReachAvoidState, TerminalCertificate]:
        nonprogress_by_core: dict[str, int] = {}
        while state.transition_index < self.config.max_submitted_revisions:
            state.transition_phase(
                ControllerPhase.INCUMBENT_CLOSE, event="close_incumbent"
            )
            if in_target_set(state):
                break
            terminal = terminal_avoid_reason(state)
            if terminal:
                state.termination_status = terminal
                break
            state.transition_phase(
                ControllerPhase.CORE_SELECT, event="select_losing_core"
            )
            core = select_losing_core(state)
            if core is None:
                state.termination_status = "LOCALIZATION_BLOCKED"
                break
            if nonprogress_by_core.get(core.core_id, 0) >= self.config.nonprogress_before_root_recovery:
                state.transition_phase(
                    ControllerPhase.ROOT_RECOVERY, event="nonprogress_threshold"
                )
                recovery = root_recovery(state, core)
                RunArtifacts(state.run_root, state.instance_id).put(
                    "root_recovery", recovery, state=state,
                    producer="reachpatch.root-recovery",
                    confidence=Confidence.HIGH,
                )
                nonprogress_by_core[core.core_id] = 0
                if recovery.classification in {
                    "NO_LEGAL_ACTION", "ENVIRONMENT_BLOCKED", "SEMANTIC_DISPUTE", "ORACLE_DISPUTE",
                }:
                    state.termination_status = recovery.classification
                    break
                continue
            state.transition_phase(
                ControllerPhase.INTENT_SELECT, event="core_selected"
            )
            intent = next_untried_repair_intent(state, core)
            if intent is None:
                state.transition_phase(
                    ControllerPhase.ROOT_RECOVERY, event="no_legal_intent"
                )
                recovery = root_recovery(state, core)
                if recovery.classification != "NEW_CUT":
                    state.termination_status = recovery.classification
                    break
                continue
            RunArtifacts(state.run_root, state.instance_id).put(
                "repair_intent", intent, state=state,
                producer="reachpatch.repair-policy",
                confidence=Confidence.HIGH,
            )
            result = evaluate_single_update(
                state,
                session,
                intent,
                forbidden_patterns=self.config.forbidden_patterns,
                mechanical_commands=self.config.mechanical_commands,
            )
            if result is None:
                nonprogress_by_core[core.core_id] = nonprogress_by_core.get(core.core_id, 0) + 1
                if nonprogress_by_core[core.core_id] >= self.config.nonprogress_before_root_recovery:
                    state.transition_phase(
                        ControllerPhase.ROOT_RECOVERY,
                        event="generator_nonprogress_threshold",
                    )
                    recovery = root_recovery(state, core)
                    if recovery.classification != "NEW_CUT":
                        state.termination_status = recovery.classification
                        break
                continue
            self._persist_transition(state, result)
            if result.accepted:
                nonprogress_by_core[core.core_id] = 0
            else:
                nonprogress_by_core[core.core_id] = nonprogress_by_core.get(core.core_id, 0) + 1
        if state.transition_index >= self.config.max_submitted_revisions and not in_target_set(state):
            state.termination_status = "BUDGET_EXHAUSTED"
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
            current_program = build_augmented_program_graph(current_tree)
            current_requirements = compile_assignment_overlay(
                state.semantic_graph, state.assignment
            )
            compile_requirement_paths(current_requirements, current_program)
            current_binding = build_binding_graph(current_requirements, current_program)
            dicc_challenges = materialize_challenges(
                current_requirements, current_program, current_binding
            )

            candidate_program = build_augmented_program_graph(trial_tree)
            candidate_requirements = compile_assignment_overlay(
                state.semantic_graph, state.assignment
            )
            compile_requirement_paths(candidate_requirements, candidate_program)
            candidate_binding = build_binding_graph(
                candidate_requirements, candidate_program
            )
            cumulative = reconcile_actual_diff(
                state.base_repository,
                trial_tree,
                forbidden_patterns=self.config.forbidden_patterns,
            )
            candidate_challenges = materialize_challenges(
                candidate_requirements,
                candidate_program,
                candidate_binding,
                diff_hash=(
                    cumulative.canonical_diff_hash
                    if cumulative.canonical_diff else "BASELINE"
                ),
            )
            candidate_bundles = execute_challenges(
                candidate_challenges,
                executor,
                state.base_repository,
                trial_tree,
            )

            incremental = reconcile_actual_diff(
                current_tree,
                trial_tree,
                forbidden_patterns=self.config.forbidden_patterns,
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
        if reached and state.checkpoint.patch.canonical_diff:
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
        final_patch = Path(state.run_root) / "final_patch.diff"
        final_patch.write_text(state.checkpoint.patch.canonical_diff, encoding="utf-8")
        artifacts = RunArtifacts(state.run_root, state.instance_id)
        artifacts.persist_state(state)
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
