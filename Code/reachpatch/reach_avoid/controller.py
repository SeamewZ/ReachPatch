from __future__ import annotations

import ast
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
from reachpatch.binding_graph import (
    ActiveBindingGraph, active_binding_graph_from_dict,
    build_active_binding_graph, empty_active_binding_graph, update_active_binding_graph,
)
from reachpatch.challenge_graph.dicc import (
    compile_executable_challenge_evidence,
    diff_induced_challenge_plan,
    evaluate_dicc,
    finalize_diff_induced_challenge_closure,
)
from reachpatch.challenge_graph.materialize import (
    execute_challenges,
    materialize_active_challenges,
)
from reachpatch.challenge_graph.models import ChallengeGraph, DiffClosureCertificate
from reachpatch.evidence import (
    build_hypothesis_set, build_semantic_graph, public_discussion_evidence,
)
from reachpatch.evidence.hypotheses import enumerate_assignments
from reachpatch.execution import (
    CheckClassification, CheckComparison, TraceExecutor, WorktreeManager,
    mechanical_pass, run_mechanical_checks,
    is_executable_test_path, recover_executable_targets, select_project_runner,
)
from reachpatch.execution.target_recovery import (
    TargetRecoveryResult, recover_executable_targets_bounded,
)
from reachpatch.execution.reconcile import ActualDiff, reconcile_actual_diff
from reachpatch.execution.worktree import tree_hash
from reachpatch.models.base import SerializableRecord, content_hash, stable_id, utc_now
from reachpatch.models.budget import BudgetVector
from reachpatch.models.controller import (
    ConfirmedFailure,
    CounterexamplePacket,
    ExecutableOracle,
    FailureHistory,
    GeneratorSessionRecord,
    IncumbentCheckpoint,
    LockedCheck,
    LockedCheckSet,
    MechanismAttempt,
    PatchCheckpoint,
    PatchTrajectory,
    RevisionRecord,
    ReachAvoidState,
    TerminalCertificate,
    TransitionCertificate,
    WorkingPatch,
)
from reachpatch.models.core import Instance
from reachpatch.models.enums import Confidence, ControllerPhase, Decision, OutcomeStatus
from reachpatch.models.isolation import assert_generation_payload, is_official_only_path
from reachpatch.oracle.discriminator import (
    HypothesisDiscriminator,
    discriminator_probe_from_dict,
)
from reachpatch.program_graph import (
    ContextRequest, Deadline, GraphBudget,
    build_active_program_slice, build_diff_impact_slice,
    build_repository_index, build_target_slice, recover_causal_slice,
    prioritize_target_repair_seeds, recover_repair_slice_seeds,
    update_active_program_slice,
)
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.reach_avoid.gates import (
    evidence_limited_complete, in_target_set, terminal_avoid_reason,
)
from reachpatch.reach_avoid.persistence import RunArtifacts
from reachpatch.reach_avoid.trajectory import (
    build_locked_check_set,
    finalize_best_patch,
    initialize_patch_trajectory,
    refresh_confirmed_failures,
    select_confirmed_failure,
)
from reachpatch.reach_avoid.state import outcomes_from_challenges
from reachpatch.reach_avoid.observations import ObservationBundle
from reachpatch.reach_avoid.transition import evaluate_patch_revision
from reachpatch.reach_avoid.restore import (
    challenge_graph_from_dict, conversation_from_dict,
    check_comparison_from_dict, causal_slice_from_dict, dicc_certificate_from_dict,
    environment_frontier_from_dict,
    executable_overlay_from_dict, hypothesis_set_from_dict, impact_slice_from_dict,
    outcome_from_dict, program_graph_from_dict, repository_index_from_dict,
    requirement_graph_from_dict, target_recovery_from_dict,
    target_slice_from_dict,
)
from reachpatch.repair.ablation import (
    AblationValidation,
    EditRetentionAblation,
    edit_retention_ablation,
)
from reachpatch.repair.session import ActionProvider, PersistentGeneratorSession
from reachpatch.repair.policy import next_untried_repair_intent
from reachpatch.repair.deepseek_agent import (
    ActionConversionStatus, GeneratorBlockedExternal, GeneratorConversation,
    PersistentDeepSeekAgent, convert_revision_action,
)
from reachpatch.repair.tools import RepairToolExecutor
from reachpatch.requirement_graph import (
    compile_assignment_overlay, compile_executable_requirement_overlay,
    compile_requirement_core, compile_requirement_paths, promote_domains_from_diff,
    refresh_requirement_paths,
    update_requirement_coverage,
)


class AnalysisBlocked(RuntimeError):
    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ReachPatchConfig(SerializableRecord):
    selection_mode: str = "hypothesis_set"
    max_submitted_revisions: int = 6
    max_total_revisions: int = 6
    initial_generator_max_turns: int = 14
    revision_generator_max_turns: int = 8
    root_recovery_max_turns: int = 8
    initial_generator_wall_time_s: float = 300.0
    revision_generator_wall_time_s: float = 180.0
    initial_generator_token_budget: int = 32_000
    revision_generator_token_budget: int = 16_000
    max_internal_tool_turns_per_revision: int = 6
    equivalent_failures_before_new_mechanism: int = 2
    nonprogress_before_root_recovery: int = 3
    max_same_mechanism_failures: int = 2
    max_distinct_mechanisms_per_failure: int = 3
    target_recovery_wall_time_s: float = 45.0
    target_recovery_max_candidates: int = 3
    max_llm_reproduction_attempts: int = 2
    max_stability_runs: int = 2
    max_ablation_groups: int = 32
    enable_ablation: bool = False
    max_index_files: int = 10_000
    max_precise_files: int = 12
    max_precise_functions: int = 40
    active_slice_max_files: int = 12
    active_slice_max_symbols: int = 40
    direct_caller_depth: int = 2
    impact_cone_depth: int = 2
    max_program_nodes: int = 8_000
    max_program_edges: int = 24_000
    max_protocol_candidates_per_operation: int = 8
    max_path_classes_per_leaf: int = 24
    max_active_target_bindings: int = 20
    max_active_preservation_bindings: int = 20
    max_active_challenges: int = 40
    max_parallel_challenge_executions: int = 2
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
            self.max_total_revisions,
            self.initial_generator_max_turns,
            self.revision_generator_max_turns,
            self.root_recovery_max_turns,
            self.initial_generator_wall_time_s,
            self.revision_generator_wall_time_s,
            self.initial_generator_token_budget,
            self.revision_generator_token_budget,
            self.equivalent_failures_before_new_mechanism,
            self.nonprogress_before_root_recovery,
            self.max_same_mechanism_failures,
            self.max_distinct_mechanisms_per_failure,
            self.target_recovery_wall_time_s,
            self.target_recovery_max_candidates,
            self.max_llm_reproduction_attempts,
            self.max_stability_runs,
            self.max_index_files, self.max_precise_files,
            self.max_precise_functions, self.max_program_nodes,
            self.max_program_edges, self.max_protocol_candidates_per_operation,
            self.active_slice_max_files, self.active_slice_max_symbols,
            self.direct_caller_depth, self.impact_cone_depth,
            self.max_path_classes_per_leaf, self.max_active_target_bindings,
            self.max_active_preservation_bindings, self.max_active_challenges,
            self.max_parallel_challenge_executions,
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


def _inferred_public_test_paths(
    issue: str,
    repository_index: Any,
    repository: Path,
    *,
    limit: int,
) -> tuple[str, ...]:
    """Rank public tests by real issue-symbol references before word overlap."""

    issue_identifiers = {
        token.lower()
        for token in re.findall(r"[A-Za-z_]\w*", issue)
        if len(token) >= 3
    }
    indexed_symbols = {
        str(symbol).rsplit(".", 1)[-1].lower()
        for symbol in getattr(repository_index, "symbols", {})
    }
    issue_symbols = issue_identifiers & indexed_symbols
    ranked: list[tuple[int, int, int, str]] = []
    for relative, references in getattr(
        repository_index, "test_references", {},
    ).items():
        if not is_executable_test_path(relative) or is_official_only_path(relative):
            continue
        normalized_references = {
            str(name).rsplit(".", 1)[-1].lower() for name in references
        }
        symbol_overlap = issue_symbols & normalized_references
        compact_path = re.sub(r"[^a-z0-9]", "", str(relative).lower())
        path_symbols = {
            symbol for symbol in issue_symbols
            if len(symbol) >= 4 and symbol in compact_path
        }
        if issue_symbols and not (symbol_overlap or path_symbols):
            continue
        lexical_overlap = {
            token for token in issue_identifiers & normalized_references
            if len(token) >= 5
        }
        if not issue_symbols and not lexical_overlap:
            continue
        ranked.append((
            -sum(len(symbol) for symbol in symbol_overlap | path_symbols),
            -len(symbol_overlap | path_symbols),
            -sum(len(token) for token in lexical_overlap),
            str(relative),
        ))
    selected: list[str] = []
    for *_score, relative in sorted(ranked):
        if len(selected) >= max(0, limit):
            break
        test_path = repository / relative
        selectors: list[tuple[int, str]] = []
        try:
            source = test_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(relative))
        except (OSError, SyntaxError, UnicodeError):
            tree = None
        if tree is not None and issue_symbols:
            functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append((node.name, node))
                elif isinstance(node, ast.ClassDef):
                    functions.extend(
                        (f"{node.name}::{child.name}", child)
                        for child in node.body
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    )
            for qualified_name, function in functions:
                leaf_name = qualified_name.rsplit("::", 1)[-1]
                if not leaf_name.startswith("test"):
                    continue
                referenced = {
                    child.id.lower()
                    for child in ast.walk(function)
                    if isinstance(child, ast.Name)
                }
                referenced.update(
                    child.attr.lower()
                    for child in ast.walk(function)
                    if isinstance(child, ast.Attribute)
                )
                overlap = referenced & issue_symbols
                if overlap:
                    selectors.append((
                        -sum(len(symbol) for symbol in overlap), qualified_name,
                    ))
        if selectors:
            for _function_score, qualified_name in sorted(selectors)[:2]:
                selected.append(f"{test_path}::{qualified_name}")
                if len(selected) >= max(0, limit):
                    break
        else:
            selected.append(str(test_path))
    return tuple(selected)


def _named_public_checks(state: ReachAvoidState) -> dict[str, tuple[str, ...]]:
    if state.target_recovery is not None:
        return {
            item.check_id: item.command
            for item in (
                *state.target_recovery.targets,
                *state.target_recovery.preservation_checks,
            )
        }
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
        initial_generator_tokens=32_000,
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


def _execution(raw: dict | None) -> Any:
    if not raw:
        return None
    from reachpatch.execution.models import CheckExecution, CheckStatus
    values = dict(raw)
    values["status"] = CheckStatus(values["status"])
    values["executed_symbol_ids"] = tuple(values.get("executed_symbol_ids", ()))
    return CheckExecution(**values)


def _oracle(raw: dict) -> ExecutableOracle:
    return ExecutableOracle(**dict(raw))


def _locked_check(raw: dict) -> LockedCheck:
    from reachpatch.oracle.models import ObservationContract
    values = dict(raw)
    values["command"] = tuple(values.get("command", ()))
    values["requirement_ids"] = tuple(values.get("requirement_ids", ()))
    values["source_evidence_ids"] = tuple(values.get("source_evidence_ids", ()))
    values["oracle"] = _oracle(values["oracle"])
    contract = values.get("observation_contract")
    values["observation_contract"] = (
        ObservationContract(**contract) if isinstance(contract, dict) else contract
    )
    values["baseline_observation"] = _execution(values.get("baseline_observation"))
    if isinstance(values.get("input_recipe"), dict):
        from reachpatch.reach_avoid.restore import _recipe
        values["input_recipe"] = _recipe(values["input_recipe"])
    if isinstance(values.get("executable_scenario"), dict):
        from reachpatch.reach_avoid.restore import _scenario
        values["executable_scenario"] = _scenario(values["executable_scenario"])
    return LockedCheck(**values)


def _locked_check_set(raw: dict | None) -> LockedCheckSet | None:
    if not raw:
        return None
    return LockedCheckSet(
        lock_id=str(raw["lock_id"]),
        target_checks=tuple(_locked_check(item) for item in raw.get("target_checks", ())),
        preservation_checks=tuple(_locked_check(item) for item in raw.get("preservation_checks", ())),
        counterexample_checks=tuple(_locked_check(item) for item in raw.get("counterexample_checks", ())),
        mechanical_checks=tuple(_locked_check(item) for item in raw.get("mechanical_checks", ())),
    )


def _patch_checkpoint(raw: dict) -> PatchCheckpoint:
    from reachpatch.models.controller import EvidenceVector
    values = dict(raw)
    values["patch"] = _working_patch(values["patch"])
    values["evidence_vector"] = EvidenceVector(**values.get("evidence_vector", {}))
    for name in (
        "executed_check_ids", "confirmed_target_pass_ids",
        "confirmed_target_failure_ids", "preservation_regression_ids",
        "mechanical_failure_ids",
    ):
        values[name] = tuple(values.get(name, ()))
    return PatchCheckpoint(**values)


def _trajectory(raw: dict | None) -> PatchTrajectory | None:
    if not raw:
        return None
    return PatchTrajectory(
        first_patch=_patch_checkpoint(raw["first_patch"]),
        best_evidence_patch=_patch_checkpoint(raw["best_evidence_patch"]),
        working_patch=_patch_checkpoint(raw["working_patch"]),
        trial_patch=(
            _patch_checkpoint(raw["trial_patch"])
            if raw.get("trial_patch") else None
        ),
        locked_checks={
            key: _locked_check(value)
            for key, value in raw.get("locked_checks", {}).items()
        },
        confirmed_failures=[
            _confirmed_failure(value)
            for value in raw.get("confirmed_failures", ())
        ],
        revision_history=[
            RevisionRecord(
                **{
                    **dict(value),
                    "executed_check_ids": tuple(value.get("executed_check_ids", ())),
                }
            )
            for value in raw.get("revision_history", ())
        ],
        regression_repair_attempts=int(raw.get("regression_repair_attempts", 0)),
        checkpoint_archive={
            key: _patch_checkpoint(value)
            for key, value in raw.get("checkpoint_archive", {}).items()
        },
    )


def _confirmed_failure(raw: dict) -> ConfirmedFailure:
    values = dict(raw)
    values["expected_relation"] = _oracle(values["expected_relation"])
    values["baseline_observation"] = _execution(values.get("baseline_observation"))
    values["before_patch_observation"] = _execution(values.get("before_patch_observation"))
    for name in ("causal_cut_ids", "impact_risk_ids"):
        values[name] = tuple(values.get(name, ()))
    return ConfirmedFailure(**values)


def _failure_history(raw: dict) -> FailureHistory:
    values = dict(raw)
    values.setdefault(
        "failure_signature", values.pop("signature", ""),
    )
    values.setdefault(
        "attempted_mechanism_ids", values.pop("attempted_mechanisms", ()),
    )
    legacy_revisions = values.pop("revisions", ())
    values.setdefault(
        "revision_ids", tuple(map(str, legacy_revisions)),
    )
    values.setdefault("confirmed_outcomes", ())
    values.setdefault(
        "affected_symbol_ids", values.pop("affected_symbols", ()),
    )
    for name in (
        "attempted_mechanism_ids", "causal_cut_ids", "revision_ids",
        "confirmed_outcomes", "affected_symbol_ids",
    ):
        values[name] = tuple(values.get(name, ()))
    return FailureHistory(**values)


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
        "uncertain_information", "setup", "input_derivation", "causal_cut_ids",
        "impact_risks", "suggested_action_families",
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
        "affected_binding_unit_ids", "executed_check_ids", "target_comparisons",
        "preservation_comparisons", "challenge_comparisons",
        "requirements_improved", "requirements_regressed",
        "counterexamples_closed", "counterexamples_opened", "evidence_hashes",
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
    check_comparisons: tuple[Any, ...]
    impact_slice: Any
    active_binding_graph: Any
    dicc_certificate: Any
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
            # PersistentDeepSeekAgent counts the initial generation in
            # ``revision_count``.  Controller revision budgets count only
            # ConfirmedFailure-driven repair attempts.  Reserve one invocation
            # for the permanent first patch and one bounded continuation when
            # that pass requests a real program-slice expansion.
            self.generator_agent.max_revisions = (
                2 + self.config.max_total_revisions
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
        hint_evidence = (
            tuple(public_discussion_evidence(public_hints))
            if public_hints else ()
        )
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
            "confirmed_revision_count": 0,
            "submitted_generator_revisions": 0,
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
        inferred_public_tests = _inferred_public_test_paths(
            instance.issue,
            repository_index,
            repository,
            limit=min(20, self.config.max_precise_files),
        )
        selected_visible_tests = tuple(dict.fromkeys(
            (*visible_tests, *inferred_public_tests)
        ))
        if selected_visible_tests != visible_tests:
            visible_tests = selected_visible_tests
            timings["public_test_recovery_seconds"] = 0.0
            phase_metrics["inferred_public_test_count"] = len(
                inferred_public_tests
            )
        project_runner = select_project_runner(
            repository,
            artifact_root=root / "execution",
            base_commit=instance.base_commit,
        )
        project_runner.explicit_commands = tuple(self.config.mechanical_commands)
        recovery_instance = replace(instance, visible_tests=visible_tests)
        # Recovery is deliberately deferred until after the first complete
        # Generator attempt. A missing public oracle is evidence state, never
        # generation permission.
        target_recovery = TargetRecoveryResult.unavailable()
        timings["target_recovery_seconds"] = 0.0
        requirement_started = time.perf_counter()
        requirements = compile_requirement_core(
            semantic_result.graph, hypothesis_set, repository_index,
            issue_text=instance.issue,
        )
        executable_overlay = compile_executable_requirement_overlay(
            requirements,
            target_recovery,
            hypothesis_assignment_ids=hypothesis_set.active_assignment_ids,
        )
        timings["requirement_core_seconds"] = time.perf_counter() - requirement_started
        seed_started = time.perf_counter()
        seeds = recover_repair_slice_seeds(
            instance.issue, visible_tests, repository_index,
        )
        seeds = prioritize_target_repair_seeds(
            seeds, target_recovery, repository_index,
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
        target_slice = build_target_slice(
            target_recovery, repository_index, program,
        )
        causal_slices = []
        target_ids = {item.check_id for item in target_recovery.targets}
        target_check_by_id = {
            item.check_id: item for item in target_recovery.targets
        }
        for execution in target_recovery.baseline_executions:
            if execution.check_id not in target_ids:
                continue
            causal_slices.append(recover_causal_slice(
                execution,
                repository_index,
                program,
                GraphBudget.from_limits(
                    seconds=self.config.program_slice_deadline_seconds,
                    max_nodes=self.config.max_program_nodes,
                    max_edges=self.config.max_program_edges,
                    max_files=self.config.max_precise_files,
                    max_functions=self.config.max_precise_functions,
                    max_rss_mib=self.config.graph_memory_limit_mib,
                    max_protocol_candidates_per_operation=(
                        self.config.max_protocol_candidates_per_operation
                    ),
                ),
                target_check_by_id[execution.check_id],
            ))
        timings["active_program_slice_seconds"] = slice_result.elapsed_seconds
        # The first Generator call must precede path enumeration and product
        # materialization.  Empty sparse products keep the state schema and
        # hashes well-defined; evaluate_patch_revision() fills them from the
        # first actual diff and executable evidence.
        requirement_hash = requirements.semantic_layer_hash()
        program_hash = program.program_hash()
        binding = empty_active_binding_graph(
            instance_id=instance.instance_id,
            requirement_graph=requirements,
            program_slice=program,
        )
        challenges = ChallengeGraph(
            requirement_graph_hash=requirement_hash,
            program_graph_hash=program_hash,
            binding_graph_hash=binding.graph_hash(),
        )
        timings["requirement_graph_initial_seconds"] = 0.0
        timings["binding_graph_initial_seconds"] = 0.0
        timings["challenge_graph_initial_seconds"] = 0.0
        initial_graph_record = {
            "kind": "initial_localization",
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
            "truncated": bool(slice_result.truncated_reason),
            "products_materialized": False,
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
            status="EMPTY",
        )
        baseline_by_check = {
            item.check_id: item for item in target_recovery.baseline_executions
        }
        initial_comparisons = tuple(
            CheckComparison.create(
                baseline_by_check[check.check_id],
                baseline_by_check[check.check_id],
                check.role,
            )
            for check in (
                *target_recovery.targets,
                *target_recovery.preservation_checks,
            )
            if check.check_id in baseline_by_check
        )
        initial_dicc = evaluate_dicc(
            target_recovery.targets,
            initial_comparisons,
            empty_diff,
            None,
            compile_executable_challenge_evidence(
                binding, initial_comparisons, empty_diff, None,
            ),
            path_obligation_count=len(
                requirements.leaves
            ),
            active_binding_count=binding.executable_unit_count,
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
            blocked_path_obligation_ids=(),
            executed_target_deficit=float(len(target_recovery.targets)),
            accepted_transition_id=None, generator_session_cursor="0",
            remaining_budget=remaining, safe=False, graph_reached=False,
        )
        conversation = GeneratorConversation.create(instance.instance_id)
        state = ReachAvoidState(
            state_id=stable_id("state", episode_id, checkpoint_id),
            instance_id=instance.instance_id, run_id=root.name,
            episode_id=episode_id, base_repository=str(snapshot),
            base_commit=instance.base_commit, run_root=str(root),
            assignment=assignment, semantic_graph=semantic_result.graph,
            requirement_graph=requirements, program_graph=program,
            active_binding_graph=binding, challenge_graph=challenges,
            checkpoint=checkpoint, outcomes={}, trace_bundles={},
            counterexamples=[], repair_history=[], mechanism_memory={},
            root_recoveries=[], diff_closure_certificates=[],
            generator_session=legacy_session.record, remaining_budget=remaining,
            phase=ControllerPhase.SEMANTIC, artifact_ids={},
            hypothesis_set=hypothesis_set, repository_index=repository_index,
            generator_conversation=conversation,
            runtime_config={
                **self.config.to_dict(),
                "primary_issue": instance.issue,
                "generation_hints": str(
                    instance.public_metadata.get("hints_text", "")
                ),
                "visible_test_paths": [
                    str(Path(path).resolve().relative_to(repository)).replace("\\", "/")
                    for path in visible_tests
                ],
                "public_check_commands": [
                    list(check.command) for check in (
                        *target_recovery.targets,
                        *target_recovery.preservation_checks,
                    )
                ],
                "project_runner": project_runner.name,
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
                "normative_requirement_path_obligations": len(
                    requirements.path_obligations
                ),
                "executable_requirement_obligations": len(
                    executable_overlay.executable_requirements
                ),
                "requirement_path_obligations": (
                    len(requirements.path_obligations)
                    + len(executable_overlay.executable_requirements)
                ),
                "candidate_binding_count": binding.build_stats.get("candidate_count", 0),
                "normative_active_binding_count": binding.build_stats.get(
                    "active_count", 0
                ),
                "active_binding_count": binding.executable_unit_count,
                "deferred_binding_count": binding.build_stats.get("deferred_count", 0),
                "normative_challenge_cell_count": len(challenges.cells),
                "active_challenge_count": 0,
                "high_value_pending_challenge_ids": tuple(sorted(
                    challenge_id for challenge_id, cell in challenges.cells.items()
                    if cell.hard
                )),
                "diff_adequacy_closed": False,
                "executable_target_count": len(target_recovery.targets),
                "executable_preservation_count": len(
                    target_recovery.preservation_checks
                ),
                "baseline_real_execution_count": len(
                    target_recovery.baseline_executions
                ),
                "environment_frontier_count": len(
                    target_recovery.environment_frontiers
                ),
                "directed_reproduction_requests": (
                    target_recovery.directed_reproduction_requests
                ),
                "dicc_status": initial_dicc.status.value,
                "graph_build_records": [initial_graph_record],
                "discriminator_probes": [
                    item.to_dict() for item in discriminator_probes
                ],
                "executed_discriminator_probe_ids": [],
                **phase_metrics,
            },
            target_recovery=target_recovery,
            executable_requirement_overlay=executable_overlay,
            target_slice=target_slice,
            causal_slices=tuple(causal_slices),
            check_comparisons=initial_comparisons,
            dicc_certificate=initial_dicc,
            environment_frontiers=target_recovery.environment_frontiers,
            observations=ObservationBundle.create(
                revision=0, check_comparisons=initial_comparisons,
                environment_frontier_ids=(
                    item.frontier_id for item in target_recovery.environment_frontiers
                ),
            ),
            requirement_coverage=update_requirement_coverage(
                None, binding, initial_comparisons, (),
            ),
            generation_run_id=root.name,
            method_config_hash=content_hash(self.config.to_dict()),
            prompt_hash=content_hash(instance.issue),
            current_patch_hash=working_patch.canonical_diff_hash,
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
        artifacts.put(
            "target_recovery", target_recovery, state=state,
            producer="reachpatch.target-recovery", confidence=Confidence.CONFIRMED,
            status=target_recovery.status,
        )
        artifacts.put(
            "executable_requirement_overlay", executable_overlay, state=state,
            producer="reachpatch.requirement-overlay", confidence=Confidence.CONFIRMED,
        )
        artifacts.put(
            "dicc_certificate", initial_dicc, state=state,
            producer="reachpatch.dicc", confidence=Confidence.CONFIRMED,
            status=initial_dicc.status.value,
        )
        first_patch_started = time.perf_counter()
        if self.generator_agent is not None:
            revision = None
            initial_error: GeneratorBlockedExternal | None = None
            initial_invocations = 0
            initial_tool_turns = 0
            context_continuations = 0
            # Initial recovery remains one persistent patch trajectory. A
            # fourth bounded invocation is reserved for an explicitly rejected
            # staged patch whose validator names a concrete correction; this is
            # not an evidence-free post-checkpoint revision.
            max_initial_invocations = 5
            max_context_continuations = 2
            tools = None
            reuse_rejected_staged_tools = False
            while initial_invocations < max_initial_invocations:
                self.generator_agent.max_tool_turns = (
                    self.config.initial_generator_max_turns
                    if initial_invocations == 0
                    else self.config.root_recovery_max_turns
                )
                self.generator_agent.max_wall_time_seconds = (
                    self.config.initial_generator_wall_time_s
                )
                self.generator_agent.max_completion_tokens = (
                    self.config.initial_generator_token_budget
                )
                if tools is None or not reuse_rejected_staged_tools:
                    tools = self._repair_tools(state, initial=True)
                reuse_rejected_staged_tools = False
                for api_attempt in range(2):
                    try:
                        recovery_method = getattr(
                            self.generator_agent,
                            "recover_initial_patch",
                            self.generator_agent.generate_initial_patch,
                        )
                        revision = (
                            self.generator_agent.generate_initial_patch(
                                state, conversation, tools,
                            )
                            if initial_invocations == 0
                            else recovery_method(state, conversation, tools)
                        )
                        initial_error = None
                        break
                    except GeneratorBlockedExternal as exc:
                        initial_error = exc
                        state.runtime_metrics["initial_generator_retry_count"] = (
                            api_attempt + 1
                        )
                initial_invocations += 1
                if revision is not None:
                    initial_tool_turns += revision.tool_turns
                if initial_error is not None or revision is None:
                    break
                if revision.status == "STAGED_PATCH_REVIEW_REJECTED":
                    state.runtime_metrics[
                        "initial_staged_patch_review_rejection_count"
                    ] = int(state.runtime_metrics.get(
                        "initial_staged_patch_review_rejection_count", 0,
                    )) + 1
                    state.runtime_metrics.setdefault(
                        "root_cause_labels", [],
                    ).append("STAGED_PATCH_REVIEW_REJECTED")
                    if initial_invocations < max_initial_invocations:
                        if tools.discard_rejected_import_only_stage():
                            state.runtime_metrics[
                                "discarded_rejected_import_only_stage_count"
                            ] = int(state.runtime_metrics.get(
                                "discarded_rejected_import_only_stage_count", 0,
                            )) + 1
                        elif tools.discard_quality_rejected_stage():
                            state.runtime_metrics[
                                "discarded_quality_rejected_stage_count"
                            ] = int(state.runtime_metrics.get(
                                "discarded_quality_rejected_stage_count", 0,
                            )) + 1
                        # The rejected edit set was never checkpointed. Reuse
                        # the same transactional executor and its retained diff,
                        # but reopen staging on repository source so recovery can
                        # change a local executable statement instead of being
                        # pinned to repeated whole-set replacements.
                        reuse_rejected_staged_tools = True
                        state.runtime_metrics[
                            "initial_review_replacement_continuation_count"
                        ] = int(state.runtime_metrics.get(
                            "initial_review_replacement_continuation_count", 0,
                        )) + 1
                        continue
                    state.runtime_metrics[
                        "initial_staged_patch_review_exhausted"
                    ] = True
                    break
                if revision.edits:
                    break
                if (
                    revision.context_requests
                    and context_continuations < max_context_continuations
                ):
                    expanded = self._expand_generator_context(
                        state, tuple(revision.context_requests),
                    )
                    state.runtime_metrics["initial_context_expansion_requested"] = True
                    state.runtime_metrics["initial_context_expansion_succeeded"] = expanded
                    if not expanded:
                        # A request can name symbols that are already present in
                        # the bounded graph (or cannot enlarge it under its cap).
                        # That is generator nonprogress, not an external blocker.
                        # Preserve the first task and inspected source and use the
                        # single initial-recovery invocation to synthesize the edit.
                        state.runtime_metrics[
                            "initial_context_expansion_redundant"
                        ] = True
                        if initial_invocations < max_initial_invocations:
                            continue
                        break
                    context_continuations += 1
                    state.runtime_metrics["initial_context_continuation_count"] = (
                        context_continuations
                    )
                    continue
                recoverable_nonprogress = revision.status in {
                    "CONTEXT_ONLY",
                    "DECLARED_BLOCKER",
                    "GENERATOR_BROWSE_LOOP",
                }
                if (
                    # A contextless model has no new executable evidence to
                    # consume. Keep the bounded recovery useful for a short
                    # initial diagnosis, but do not spend the expanded
                    # quality-recovery allowance on repeated browse loops.
                    initial_invocations < min(max_initial_invocations, 3)
                    and recoverable_nonprogress
                ):
                    state.runtime_metrics["initial_nonprogress_recovery_count"] = int(
                        state.runtime_metrics.get(
                            "initial_nonprogress_recovery_count", 0,
                        )
                    ) + 1
                    continue
                break
            if initial_error is not None:
                self._record_generator_block(state, initial_error)
            timings["first_patch_generation_seconds"] = (
                time.perf_counter() - first_patch_started
            )
            state.runtime_metrics["deepseek_initial_generation_count"] = 1
            state.runtime_metrics["deepseek_initial_invocation_count"] = (
                initial_invocations
            )
            if revision is not None:
                state.runtime_metrics["deepseek_tool_turns"] = initial_tool_turns
                state.generator_session = replace(
                    state.generator_session,
                    cursor=state.generator_session.cursor + initial_invocations,
                    internal_tool_turns=(
                        state.generator_session.internal_tool_turns
                        + initial_tool_turns
                    ),
                )
                readiness = dict(
                    state.runtime_metrics.get("first_patch_readiness", {})
                )
                required_readiness = (
                    "target_definition_read",
                    "root_cause_identified",
                    "requirements_accounted_for",
                    "preservation_risks_identified",
                    "final_diff_reviewed",
                )
                missing_readiness = tuple(
                    key for key in required_readiness if not readiness.get(key, False)
                )
                state.runtime_metrics["first_patch_readiness_passed"] = not missing_readiness
                state.runtime_metrics["first_patch_readiness_missing"] = list(
                    missing_readiness
                )
                if revision.edits and missing_readiness:
                    # Readiness is a quality signal, not permission to erase a
                    # real first edit.  The generator may have staged a
                    # reachable patch before a later browse/review call failed
                    # (for example because a replacement used a stale source
                    # anchor).  Keep that edit set in the normal initial
                    # transition so mechanical validation and the immutable
                    # first checkpoint can preserve it.  It remains explicitly
                    # uncertified and cannot support Reach or an evidence-free
                    # revision.
                    state.runtime_metrics[
                        "first_patch_readiness_blocked"
                    ] = True
                    state.runtime_metrics.setdefault("root_cause_labels", []).append(
                        "FIRST_PATCH_READINESS_INCOMPLETE"
                    )
                    state.runtime_metrics[
                        "first_patch_readiness_preserved"
                    ] = True
            recovery_started = time.perf_counter()
            try:
                target_recovery = recover_executable_targets_bounded(
                    recovery_instance,
                    repository_index,
                    project_runner,
                    self.generator_agent,
                    root / "execution",
                    max_target_candidates=self.config.target_recovery_max_candidates,
                    max_llm_reproduction_attempts=(
                        self.config.max_llm_reproduction_attempts
                    ),
                    max_stability_runs=self.config.max_stability_runs,
                    wall_time_seconds=self.config.target_recovery_wall_time_s,
                )
            except GeneratorBlockedExternal as exc:
                target_recovery = TargetRecoveryResult.unavailable()
                state.runtime_metrics["target_recovery_generator_error"] = str(exc)
            timings["target_recovery_seconds"] = (
                time.perf_counter() - recovery_started
            )
            state.target_recovery = target_recovery
            state.environment_frontiers = target_recovery.environment_frontiers
            executable_overlay = compile_executable_requirement_overlay(
                state.requirement_graph,
                target_recovery,
                hypothesis_assignment_ids=hypothesis_set.active_assignment_ids,
            )
            state.executable_requirement_overlay = executable_overlay
            state.target_slice = build_target_slice(
                target_recovery, repository_index, state.program_graph,
            )
            target_checks_by_id = {
                item.check_id: item for item in target_recovery.targets
            }
            state.causal_slices = tuple(
                recover_causal_slice(
                    execution,
                    repository_index,
                    state.program_graph,
                    GraphBudget.from_limits(
                        seconds=self.config.program_slice_deadline_seconds,
                        max_nodes=self.config.max_program_nodes,
                        max_edges=self.config.max_program_edges,
                        max_files=self.config.active_slice_max_files,
                        max_functions=self.config.active_slice_max_symbols,
                        max_rss_mib=self.config.graph_memory_limit_mib,
                        max_protocol_candidates_per_operation=(
                            self.config.max_protocol_candidates_per_operation
                        ),
                    ),
                    target_checks_by_id[execution.check_id],
                )
                for execution in target_recovery.baseline_executions
                if execution.check_id in target_checks_by_id
            )
            baseline_by_check = {
                item.check_id: item for item in target_recovery.baseline_executions
            }
            state.check_comparisons = tuple(
                CheckComparison.create(
                    baseline_by_check[check.check_id],
                    baseline_by_check[check.check_id],
                    check.role,
                )
                for check in (
                    *target_recovery.targets,
                    *target_recovery.preservation_checks,
                )
                if check.check_id in baseline_by_check
            )
            state.observations = ObservationBundle.create(
                revision=0,
                check_comparisons=state.check_comparisons,
                environment_frontier_ids=(
                    item.frontier_id for item in target_recovery.environment_frontiers
                ),
            )
            state.runtime_config["public_check_commands"] = [
                list(check.command) for check in (
                    *target_recovery.targets,
                    *target_recovery.preservation_checks,
                )
            ]
            state.runtime_metrics.update({
                "target_recovery_status": target_recovery.status,
                "target_recovery_timed_out": target_recovery.timed_out,
                "executable_target_count": len(target_recovery.targets),
                "executable_preservation_count": len(
                    target_recovery.preservation_checks
                ),
                "baseline_real_execution_count": len(
                    target_recovery.baseline_executions
                ),
                "environment_frontier_count": len(
                    target_recovery.environment_frontiers
                ),
                "directed_reproduction_requests": (
                    target_recovery.directed_reproduction_requests
                ),
            })
            artifacts.put(
                "target_recovery", target_recovery, state=state,
                producer="reachpatch.target-recovery",
                confidence=Confidence.CONFIRMED,
                status=target_recovery.status,
            )
            if (
                revision is not None
                and revision.edits
                and revision.status != "STAGED_PATCH_REVIEW_REJECTED"
            ):
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
                    elif result.decision == Decision.KEEP_UNCERTIFIED:
                        state.runtime_metrics["kept_uncertified_transitions"] = 1
                    else:
                        state.runtime_metrics["rolled_back_transitions"] = 1
                        state.runtime_metrics.setdefault(
                            "failed_generator_mechanisms", []
                        ).append(revision.mechanism)
                    self._persist_transition(state, result)
                    if (
                        state.checkpoint.patch.canonical_diff
                        and state.patch_trajectory is None
                    ):
                        trajectory = initialize_patch_trajectory(state)
                        for trajectory_checkpoint in (
                            trajectory.checkpoint_archive.values()
                        ):
                            artifacts.put(
                                "patch_checkpoint", trajectory_checkpoint,
                                state=state,
                                producer="reachpatch.patch-trajectory",
                                confidence=Confidence.CONFIRMED,
                                status=trajectory_checkpoint.status,
                            )
                        artifacts.put(
                            "patch_trajectory", trajectory, state=state,
                            producer="reachpatch.patch-trajectory",
                            confidence=Confidence.CONFIRMED,
                        )
                        artifacts.persist_state(state)
                else:
                    self._record_action_rejection(state, revision, conversion)
            elif revision is not None and revision.context_requests:
                state.runtime_metrics["initial_context_only"] = True
            elif (
                revision is not None
                and revision.status == "STAGED_PATCH_REVIEW_REJECTED"
            ):
                state.runtime_metrics["initial_generator_nonprogress"] = True
            elif revision is not None and revision.status == "GENERATOR_BROWSE_LOOP":
                state.runtime_metrics.setdefault("root_cause_labels", []).append(
                    "GENERATOR_BROWSE_LOOP"
                )
                state.runtime_metrics["initial_generator_nonprogress"] = True
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
                    "binding_graph": {"hash": state.active_binding_graph.graph_hash()},
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
            executed_target_deficit=sum(
                requirement_graph.leaves[unit.leaf_id].weight
                for unit in binding_graph.units.values()
                if requirement_graph.leaves[unit.leaf_id].authority_class.value
                != "PRESERVATION"
            ),
            accepted_transition_id=None,
            generator_session_cursor="0",
            remaining_budget=remaining,
            safe=False,
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
            active_binding_graph=binding,
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
        state.active_binding_graph = binding
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
        if state.dicc_certificate is not None:
            artifacts.put(
                "dicc_certificate", state.dicc_certificate, state=state,
                producer="reachpatch.dicc",
                confidence=Confidence.CONFIRMED,
                status=state.dicc_certificate.status.value,
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
        if state.patch_trajectory is not None:
            for trajectory_checkpoint in (
                state.patch_trajectory.checkpoint_archive.values()
            ):
                artifacts.put(
                    "patch_checkpoint", trajectory_checkpoint, state=state,
                    producer="reachpatch.patch-trajectory",
                    confidence=Confidence.CONFIRMED,
                    status=trajectory_checkpoint.status,
                )
            artifacts.put(
                "patch_trajectory", state.patch_trajectory, state=state,
                producer="reachpatch.patch-trajectory",
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
        persisted_probe_ids = set(
            state.runtime_metrics.get("persisted_discriminator_result_ids", ())
        )
        challenge_mapping = state.runtime_metrics.get(
            "discriminator_probe_challenges", {}
        )
        for raw in state.runtime_metrics.get("discriminator_probes", ()):
            probe = discriminator_probe_from_dict(raw)
            if (
                probe.probe_id not in executed_probe_ids
                or probe.probe_id in persisted_probe_ids
            ):
                continue
            bundle_ids = {
                state.challenge_graph.cells[challenge_id].execution_bundle_id
                for challenge_id in challenge_mapping.get(probe.probe_id, ())
                if challenge_id in state.challenge_graph.cells
                and state.challenge_graph.cells[challenge_id].execution_bundle_id
            }
            observations = tuple(
                dict(run.run.channels)
                for bundle_id in sorted(bundle_ids)
                if bundle_id in state.trace_bundles
                for run in state.trace_bundles[bundle_id].patch_bundle.runs
                if run.run.observation_reached
            )
            if observations:
                discriminator_result = HypothesisDiscriminator().record(
                    probe, observations[:20], evidence_ids=(), selected_claim_id=None,
                )
                artifacts.put(
                    "discriminator_result", discriminator_result, state=state,
                    producer="reachpatch.discriminator-executor",
                    confidence=Confidence.CONFIRMED,
                    status=discriminator_result.correctness_status,
                )
                persisted_probe_ids.add(probe.probe_id)
        state.runtime_metrics["persisted_discriminator_result_ids"] = sorted(
            persisted_probe_ids
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
                "requirement_graph", "program_graph", "active_binding_graph",
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
        binding = active_binding_graph_from_dict(
            graph_envelopes["active_binding_graph"].payload
        )
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
            else conversation_from_dict(state_raw["generator_conversation"])
            if state_raw.get("generator_conversation")
            else GeneratorConversation.create(instance.instance_id)
        )
        target_recovery = (
            target_recovery_from_dict(state_raw["target_recovery"])
            if state_raw.get("target_recovery") else None
        )
        executable_overlay = (
            executable_overlay_from_dict(state_raw["executable_requirement_overlay"])
            if state_raw.get("executable_requirement_overlay") else None
        )
        target_slice = (
            target_slice_from_dict(state_raw["target_slice"])
            if state_raw.get("target_slice") else None
        )
        causal_slices = tuple(
            causal_slice_from_dict(item)
            for item in state_raw.get("causal_slices", ())
        )
        impact_slice = (
            impact_slice_from_dict(state_raw["impact_slice"])
            if state_raw.get("impact_slice") else None
        )
        comparisons = tuple(
            check_comparison_from_dict(item)
            for item in state_raw.get("check_comparisons", ())
        )
        dicc_certificate = (
            dicc_certificate_from_dict(state_raw["dicc_certificate"])
            if state_raw.get("dicc_certificate") else None
        )
        environment_frontiers = tuple(
            environment_frontier_from_dict(item)
            for item in state_raw.get("environment_frontiers", ())
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
            active_binding_graph=binding,
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
            target_recovery=target_recovery,
            executable_requirement_overlay=executable_overlay,
            target_slice=target_slice,
            causal_slices=causal_slices,
            impact_slice=impact_slice,
            check_comparisons=comparisons,
            dicc_certificate=dicc_certificate,
            environment_frontiers=environment_frontiers,
            working_trial=(
                dict(state_raw["working_trial"])
                if state_raw.get("working_trial") is not None else None
            ),
            observations=ObservationBundle.create(
                revision=int(state_raw.get("transition_index", 0)),
                check_comparisons=comparisons,
                environment_frontier_ids=(
                    item.frontier_id for item in environment_frontiers
                ),
            ),
            requirement_coverage=update_requirement_coverage(
                None, binding, comparisons, counterexamples,
            ),
            verified_safe_patch=(
                _working_patch(state_raw["verified_safe_patch"])
                if state_raw.get("verified_safe_patch") else None
            ),
            patch_trajectory=_trajectory(state_raw.get("patch_trajectory")),
            checkpoint_history={
                key: _checkpoint(value)
                for key, value in state_raw.get("checkpoint_history", {}).items()
            },
            confirmed_failures=[
                _confirmed_failure(value)
                for value in state_raw.get("confirmed_failures", ())
            ],
            current_locked_check_set=_locked_check_set(
                state_raw.get("current_locked_check_set")
            ),
            prohibited_mechanisms=set(state_raw.get("prohibited_mechanisms", ())),
            failure_histories={
                key: _failure_history(value)
                for key, value in state_raw.get("failure_histories", {}).items()
            },
            reach_status=str(state_raw.get("reach_status", "NOT_REACHED")),
            avoid_status=str(state_raw.get("avoid_status", "NOT_AVOIDED")),
            generation_run_id=str(state_raw.get("generation_run_id", root.name)),
            code_commit_sha=str(state_raw.get("code_commit_sha", "")),
            method_config_hash=str(state_raw.get("method_config_hash", "")),
            prompt_hash=str(state_raw.get("prompt_hash", "")),
            current_patch_hash=str(
                state_raw.get("current_patch_hash", checkpoint.patch.canonical_diff_hash)
            ),
        )
        passed = [item for item in state.outcomes.values() if item.status == OutcomeStatus.PASS]
        failed = [item for item in state.outcomes.values() if item.status == OutcomeStatus.FAIL]
        unknown = [
            item for item in state.outcomes.values()
            if item.status not in {OutcomeStatus.PASS, OutcomeStatus.FAIL}
        ]
        comparison_by_check = {item.check_id: item for item in comparisons}
        target_ids = {
            item.check_id for item in getattr(target_recovery, "targets", ())
        }
        restored_target_deficit = (
            sum(
                comparison_by_check.get(check_id) is None
                or comparison_by_check[check_id].classification.value != "TARGET_FIXED"
                for check_id in target_ids
            )
            if target_recovery is not None else checkpoint.executed_target_deficit
        )
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
            executed_target_deficit=float(restored_target_deficit),
            remaining_budget=remaining,
            safe=checkpoint.safe,
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

    def _repair_tools(
        self, state: ReachAvoidState, *, initial: bool = False,
    ) -> RepairToolExecutor:
        if state.repository_index is None:
            raise RuntimeError("patch-first repair requires RepositoryIndex")
        public_checks = _named_public_checks(state)
        baseline_results = {
            item.check_id: item.to_dict()
            for item in getattr(
                state.target_recovery, "baseline_executions", ()
            )
        }
        return RepairToolExecutor(
            repository_root=Path(state.checkpoint.snapshot_tree),
            repository_index=state.repository_index,
            current_diff=state.checkpoint.patch.canonical_diff,
            public_checks=public_checks,
            allowed_test_paths=set(
                state.runtime_config.get("visible_test_paths", ())
            ),
            current_tree_hash=state.checkpoint.patch.working_tree_hash,
            public_check_results=baseline_results,
            max_search_calls=4 if initial else 2,
            max_read_calls=8 if initial else 4,
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
        cumulative = reconcile_actual_diff(state.base_repository, snapshot)
        requirement_seconds = time.perf_counter() - graph_started
        graph_started = time.perf_counter()
        binding = build_active_binding_graph(
            requirements,
            delta.graph,
            cumulative,
            state.target_recovery,
            (
                *state.target_recovery.targets,
                *state.target_recovery.preservation_checks,
            ) if state.target_recovery is not None else (),
            previous_graph=state.active_binding_graph,
            instance_id=state.instance_id,
            revision=state.transition_index + 1,
            active_slice_max_files=self.config.active_slice_max_files,
            active_slice_max_symbols=self.config.active_slice_max_symbols,
            direct_caller_depth=self.config.direct_caller_depth,
            impact_cone_depth=self.config.impact_cone_depth,
        )
        binding_seconds = time.perf_counter() - graph_started
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
        state.active_binding_graph = binding
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
        if state.termination_status == "GENERATOR_BLOCKED_EXTERNAL":
            # The initial call already exhausted its one structural retry and
            # persisted the external API failure.  It is a permitted hard stop,
            # not generator non-progress and not a revision trigger.
            return state, self.seal(state, session)
        conversation = state.generator_conversation
        if state.checkpoint.patch.canonical_diff and state.patch_trajectory is None:
            initialize_patch_trajectory(state)
        submitted = int(state.runtime_metrics.get(
            "confirmed_revision_count", 0,
        ))
        revision_limit = min(
            self.config.max_submitted_revisions, self.config.max_total_revisions
        )
        while submitted < revision_limit:
            if in_target_set(state):
                break
            if state.patch_trajectory is None:
                state.termination_status = (
                    "GENERATOR_NONPROGRESS"
                    if not state.checkpoint.patch.canonical_diff
                    else "EVIDENCE_LIMITED_COMPLETE"
                )
                break
            refresh_confirmed_failures(state)
            failure = select_confirmed_failure(state)
            if failure is None:
                state.termination_status = "EVIDENCE_LIMITED_COMPLETE"
                state.reach_status = "EVIDENCE_LIMITED_COMPLETE"
                state.runtime_metrics["revision_stopped_without_confirmed_failure"] = True
                break
            terminal = terminal_avoid_reason(state)
            if terminal:
                state.termination_status = terminal
                break
            state.runtime_metrics["selected_confirmed_failure_id"] = failure.failure_id
            state.runtime_metrics["selected_confirmed_failure_kind"] = failure.kind
            failure_history = state.failure_histories.get(failure.failure_signature)
            failed_outcomes = tuple(
                item for item in (
                    failure_history.confirmed_outcomes
                    if failure_history is not None else ()
                )
                if not item.startswith("PROMOTE:")
            )
            if (
                failure_history is not None
                and len(set(failure_history.attempted_mechanism_ids))
                >= self.config.max_distinct_mechanisms_per_failure
                and len(failed_outcomes)
                >= self.config.max_distinct_mechanisms_per_failure
            ):
                state.patch_trajectory.working_patch = (
                    state.patch_trajectory.best_evidence_patch
                )
                state.termination_status = "NO_NEW_REPAIR_EVIDENCE"
                break
            root_recovery_signatures = set(state.runtime_metrics.get(
                "root_recovery_completed_signatures", (),
            ))
            use_root_recovery = bool(
                failure_history is not None
                and len(failed_outcomes) >= 3
                and failure.failure_signature not in root_recovery_signatures
            )
            if use_root_recovery:
                unit = state.active_binding_graph.units.get(
                    failure.binding_unit_id or ""
                )
                symbols = tuple(dict.fromkeys(
                    str(state.program_graph.nodes[node_id].attributes.get(
                        "qualified_name", state.program_graph.nodes[node_id].label,
                    ))
                    for node_id in (
                        unit.program_symbol_ids if unit is not None else ()
                    )
                    if node_id in state.program_graph.nodes
                ))[: self.config.active_slice_max_symbols]
                if symbols:
                    self._expand_generator_context(state, (ContextRequest(
                        symbols=symbols,
                        relation_kinds=(
                            "calls", "dispatch", "state_read", "state_write",
                            "return_flow", "exception_flow",
                        ),
                        reason=(
                            "third repeated ConfirmedFailure bounded root recovery"
                        ),
                    ),))
                root_recovery_signatures.add(failure.failure_signature)
                state.runtime_metrics["root_recovery_completed_signatures"] = sorted(
                    root_recovery_signatures
                )
            build_locked_check_set(
                state, failure, state.patch_trajectory.working_patch,
            )
            packets = tuple(
                packet for packet in state.counterexamples
                if (
                    (
                        packet.public_trigger_id == failure.check_id
                        or packet.counterexample_id == failure.failure_id
                        or (
                            failure.binding_unit_id is not None
                            and packet.binding_unit_id == failure.binding_unit_id
                        )
                        or (
                            failure.kind == "CONFIRMED_MECHANICAL_FAILURE"
                            and any(
                                str(item.get("check_id", "")) == failure.check_id
                                for item in getattr(
                                    packet, "actual_observation", {}
                                ).get("failed_checks", ())
                            )
                        )
                    )
                )
            )
            intent = next_untried_repair_intent(state)
            state.runtime_metrics["current_repair_intent"] = (
                intent.to_dict() if intent is not None else None
            )
            if state.phase == ControllerPhase.TRANSITION_GATE:
                state.transition_phase(
                    ControllerPhase.COUNTEREXAMPLE_FEEDBACK,
                    event="revision_requires_further_evidence",
                )
            if not packets:
                state.termination_status = "CONFIRMED_FAILURE_PACKET_MISSING"
                state.runtime_metrics.setdefault("root_cause_labels", []).append(
                    "CONFIRMED_FAILURE_WITHOUT_EXECUTION_PACKET"
                )
                break
            if state.phase != ControllerPhase.REPAIR_GENERATION:
                state.transition_phase(
                    ControllerPhase.REPAIR_GENERATION,
                    event="confirmed_counterexample_repair_requested",
                )
            tools = self._repair_tools(state)
            try:
                self.generator_agent.max_tool_turns = (
                    self.config.root_recovery_max_turns
                    if use_root_recovery
                    else self.config.revision_generator_max_turns
                )
                self.generator_agent.max_wall_time_seconds = (
                    self.config.revision_generator_wall_time_s
                )
                self.generator_agent.max_completion_tokens = (
                    self.config.revision_generator_token_budget
                )
                revision = (
                    self.generator_agent.root_recovery(
                        state, conversation, tools,
                    )
                    if use_root_recovery
                    else self.generator_agent.repair_from_counterexamples(
                        state, conversation, packets[:1], tools,
                    )
                )
                state.runtime_metrics["deepseek_repair_count"] = int(
                    state.runtime_metrics.get("deepseek_repair_count", 0)
                ) + 1
            except GeneratorBlockedExternal as exc:
                self._record_generator_block(state, exc)
                break
            submitted += 1
            state.runtime_metrics["confirmed_revision_count"] = submitted
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
                if (
                    not revision.context_requests
                    and not state.checkpoint.patch.canonical_diff
                    and self.generator_agent.requested_max_tool_turns <= 2
                ):
                    state.termination_status = "GENERATOR_NONPROGRESS"
                    state.runtime_metrics.setdefault("root_cause_labels", []).append(
                        "GENERATOR_CONTEXTLESS"
                    )
                    break
                if revision.status == "NO_NEW_REPAIR_EVIDENCE":
                    state.runtime_metrics["same_evidence_revision_count"] = int(
                        state.runtime_metrics.get("same_evidence_revision_count", 0)
                    ) + 1
                    break
                if revision.status == "DECLARED_BLOCKER":
                    state.runtime_metrics.setdefault("root_cause_labels", []).append(
                        "GENERATOR_DECLARED_BLOCKER"
                    )
                    state.termination_status = "GENERATOR_NONPROGRESS"
                    break
                if revision.status == "GENERATOR_BROWSE_LOOP":
                    state.runtime_metrics.setdefault("root_cause_labels", []).append(
                        "GENERATOR_BROWSE_LOOP"
                    )
                    state.termination_status = "GENERATOR_NONPROGRESS"
                    break
                if revision.status == "REVISION_BUDGET_EXHAUSTED":
                    state.termination_status = (
                        "REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH"
                        if state.checkpoint.patch.canonical_diff
                        else "REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE"
                    )
                    break
                conversation.pending_context_requests.extend(
                    request for request in revision.context_requests
                    if request not in conversation.pending_context_requests
                )
                if not revision.context_requests:
                    contextless = int(
                        state.runtime_metrics.get("generator_contextless_revisions", 0)
                    ) + 1
                    state.runtime_metrics["generator_contextless_revisions"] = contextless
                    # A persistent conversation is useful only while it is
                    # producing evidence, context requests, or edits.  Do
                    # not repeatedly reset root recovery and spend every
                    # submitted revision on the same browse-only response.
                else:
                    state.runtime_metrics["pending_context_only_revision"] = True
                    expanded = self._expand_generator_context(
                        state, tuple(revision.context_requests),
                    )
                    if expanded:
                        for request in revision.context_requests:
                            if request in conversation.pending_context_requests:
                                conversation.pending_context_requests.remove(request)
                        continue
                break
            conversion = convert_revision_action(state, revision)
            if revision.mechanism in state.prohibited_mechanisms:
                state.runtime_metrics.setdefault(
                    "rejected_prohibited_mechanisms", []
                ).append(revision.mechanism)
                continue
            if conversion.status not in {
                ActionConversionStatus.ACCEPTED,
                ActionConversionStatus.NEEDS_SLICE_EXPANSION,
            }:
                self._record_action_rejection(state, revision, conversion)
                continue
            revision = conversion.revision
            result = evaluate_patch_revision(state, revision)
            if (
                failure.kind == "CONFIRMED_PRESERVATION_REGRESSION"
                and state.patch_trajectory is not None
            ):
                promoted = (
                    result.accepted
                    and state.patch_trajectory.best_evidence_patch.checkpoint_id
                    == state.checkpoint.checkpoint_id
                )
                if not promoted:
                    state.patch_trajectory.regression_repair_attempts += 1
                    if (
                        state.patch_trajectory.regression_repair_attempts
                        >= 2
                    ):
                        best = state.patch_trajectory.best_evidence_patch
                        state.patch_trajectory.working_patch = best
                        restored = state.checkpoint_history.get(best.checkpoint_id)
                        if restored is not None:
                            state.checkpoint = restored
                        state.termination_status = (
                            "CONFIRMED_REGRESSION_REPAIR_EXHAUSTED"
                        )
            if result.accepted:
                state.runtime_metrics["accepted_transitions"] = int(
                    state.runtime_metrics.get("accepted_transitions", 0)
                ) + 1
            elif result.decision == Decision.KEEP_UNCERTIFIED:
                state.runtime_metrics["kept_uncertified_transitions"] = int(
                    state.runtime_metrics.get("kept_uncertified_transitions", 0)
                ) + 1
            else:
                state.runtime_metrics["rolled_back_transitions"] = int(
                    state.runtime_metrics.get("rolled_back_transitions", 0)
                ) + 1
                state.runtime_metrics.setdefault(
                    "failed_generator_mechanisms", []
                ).append(revision.mechanism)
            self._persist_transition(state, result)
            refresh_confirmed_failures(state)
            if state.termination_status == "CONFIRMED_REGRESSION_REPAIR_EXHAUSTED":
                break
        finalize_best_patch(state)
        refresh_confirmed_failures(state)
        if submitted >= revision_limit and not in_target_set(state):
            mechanical_only = bool(state.confirmed_failures) and all(
                item.kind == "CONFIRMED_MECHANICAL_FAILURE"
                for item in state.confirmed_failures
            )
            # A malformed first submission is retained as the immutable
            # best-effort artifact when the bounded structural retry itself
            # cannot produce a comparable patch.  Do not relabel that
            # preservation fallback as a target-budget exhaustion.
            if (
                mechanical_only
                and state.patch_trajectory is not None
                and state.patch_trajectory.best_evidence_patch.checkpoint_id
                == state.patch_trajectory.first_patch.checkpoint_id
            ):
                state.termination_status = "EVIDENCE_LIMITED_COMPLETE"
                state.reach_status = "EVIDENCE_LIMITED_COMPLETE"
            else:
                state.termination_status = (
                    "REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH"
                    if state.checkpoint.patch.canonical_diff
                    else "REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE"
                )
        elif not in_target_set(state) and state.termination_status is None:
            state.termination_status = "EVIDENCE_LIMITED_COMPLETE"
        return state, self.seal(state, session)

    def _drive(
        self,
        state: ReachAvoidState,
        session: PersistentGeneratorSession,
    ) -> tuple[ReachAvoidState, TerminalCertificate]:
        if self.generator_agent is not None:
            return self._drive_patch_first(state, session)
        state.termination_status = "GENERATOR_BLOCKED_EXTERNAL"
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
        comparisons: Iterable[CheckComparison] = (),
        mechanical_checks: Iterable[Any] = (),
    ) -> None:
        seconds = sum(
            run.duration_seconds
            for paired in bundles
            for bundle in (paired.base_bundle, paired.patch_bundle)
            for run in bundle.runs
        )
        seconds += sum(
            item.patched.duration_seconds for item in comparisons
        )
        seconds += sum(
            float(item.duration_seconds) for item in mechanical_checks
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
        project_runner = select_project_runner(
            state.base_repository,
            artifact_root=Path(state.run_root) / "execution" / "ablation",
            base_commit=state.base_commit,
        )
        recovered_checks = tuple((
            *state.target_recovery.targets,
            *state.target_recovery.preservation_checks,
        ))
        baseline_by_check = {
            item.check_id: item
            for item in state.target_recovery.baseline_executions
        }
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
                if previous_evaluation else state.active_binding_graph
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
                candidate_requirements,
                candidate_program,
                incremental,
                state.target_recovery,
                (
                    *state.target_recovery.targets,
                    *state.target_recovery.preservation_checks,
                ) if state.target_recovery is not None else (),
                previous_graph=current_binding,
                instance_id=state.instance_id,
                revision=state.transition_index + attempt_number,
                active_slice_max_files=self.config.active_slice_max_files,
                active_slice_max_symbols=self.config.active_slice_max_symbols,
                direct_caller_depth=self.config.direct_caller_depth,
                impact_cone_depth=self.config.impact_cone_depth,
            )
            cumulative = reconcile_actual_diff(
                state.base_repository,
                trial_tree,
                forbidden_patterns=self.config.forbidden_patterns,
            )
            candidate_mechanical_checks = run_mechanical_checks(
                trial_tree, cumulative, baseline_root=state.base_repository,
            )
            trial_hash = tree_hash(trial_tree)
            public_comparisons = tuple(
                CheckComparison.create(
                    baseline_by_check[check.check_id],
                    project_runner.run_check(
                        check, repository=trial_tree, tree_hash=trial_hash,
                    ),
                    check.role,
                )
                for check in recovered_checks
                if check.check_id in baseline_by_check
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
            self._charge_ablation_execution(
                state, bundles, public_comparisons, candidate_mechanical_checks,
            )

            candidate_state = copy.copy(state)
            candidate_state.requirement_graph = candidate_requirements
            candidate_state.program_graph = candidate_program
            candidate_state.active_binding_graph = candidate_binding
            candidate_state.challenge_graph = candidate_challenges
            candidate_state.outcomes = outcomes_from_challenges(
                candidate_state, candidate_challenges, candidate_bundles
            )
            impact_slice = build_diff_impact_slice(
                cumulative, state.repository_index, candidate_program,
                GraphBudget.from_limits(
                    seconds=self.config.program_slice_deadline_seconds,
                    max_nodes=self.config.max_program_nodes,
                    max_edges=self.config.max_program_edges,
                    max_files=self.config.max_precise_files,
                    max_functions=self.config.max_precise_functions,
                    max_rss_mib=self.config.graph_memory_limit_mib,
                    max_protocol_candidates_per_operation=(
                        self.config.max_protocol_candidates_per_operation
                    ),
                ),
            )
            executable_challenges = compile_executable_challenge_evidence(
                candidate_binding, public_comparisons, cumulative, impact_slice,
                trace_results=(candidate_bundles, dicc_bundles),
                checks=recovered_checks,
                repository_index=state.repository_index,
            )
            executable_obligation_count = len(candidate_requirements.leaves)
            active_executable_binding_count = candidate_binding.executable_unit_count
            dicc = evaluate_dicc(
                state.target_recovery.targets,
                public_comparisons,
                cumulative,
                impact_slice,
                executable_challenges,
                path_obligation_count=executable_obligation_count,
                active_binding_count=active_executable_binding_count,
            )
            preservation_pass = not any(
                item.classification
                == CheckClassification.PRESERVATION_REGRESSION
                for item in public_comparisons
            )
            execution_environment_valid = not any(
                item.classification in {
                    CheckClassification.SAME_INFRA_FAILURE,
                    CheckClassification.NEW_INFRA_FAILURE,
                    CheckClassification.FLAKY_RESULT,
                    CheckClassification.UNSUPPORTED_CHECK,
                }
                for item in public_comparisons
            )
            safe = all((
                mechanical_pass(candidate_mechanical_checks),
                preservation_pass,
                execution_environment_valid,
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
                status="WORKING_UNCERTIFIED",
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
            candidate_state.check_comparisons = public_comparisons
            candidate_state.impact_slice = impact_slice
            candidate_state.active_binding_graph = candidate_binding
            candidate_state.dicc_certificate = dicc
            candidate_state.runtime_metrics = {
                **state.runtime_metrics,
                "normative_requirement_path_obligations": len(
                    candidate_requirements.path_obligations
                ),
                "executable_requirement_obligations": executable_obligation_count,
                "requirement_path_obligations": (
                    len(candidate_requirements.path_obligations)
                    + executable_obligation_count
                ),
                "normative_active_binding_count": candidate_binding.build_stats.get(
                    "active_count", 0
                ),
                "active_binding_count": active_executable_binding_count,
                "normative_challenge_cell_count": len(candidate_challenges.cells),
                "active_challenge_count": len(executable_challenges.challenge_ids),
                "real_execution_challenge_count": (
                    executable_challenges.real_execution_count
                ),
                "executed_challenge_ids": list(
                    executable_challenges.challenge_ids
                ),
                "dicc_status": dicc.status.value,
            }
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
                check_comparisons=public_comparisons,
                impact_slice=impact_slice,
                active_binding_graph=candidate_binding,
                dicc_certificate=dicc,
                safe=safe,
                graph_reached=graph_reached,
            )
            return AblationValidation(
                graph_reached=graph_reached,
                safe=safe,
                closure_closed=dicc.status.value == "CLOSED",
                details={
                    "candidate_diff_hash": candidate_diff.canonical_diff_hash,
                    "cumulative_diff_hash": cumulative.canonical_diff_hash,
                    "closure_id": closure.closure_id,
                    "graph_hashes": candidate_state.graph_hashes(),
                    "outcome_ids": sorted(candidate_state.outcomes),
                    "paired_bundle_ids": sorted(bundles_by_id),
                    "preservation_pass": preservation_pass,
                    "mechanical_check_ids": [
                        item.check_id for item in candidate_mechanical_checks
                    ],
                    "public_check_comparisons": [
                        item.to_dict() for item in public_comparisons
                    ],
                    "dicc_status": dicc.status.value,
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
            state.active_binding_graph = evaluation.binding_graph
            state.challenge_graph = evaluation.challenge_graph
            state.outcomes = evaluation.outcomes
            state.check_comparisons = evaluation.check_comparisons
            state.impact_slice = evaluation.impact_slice
            state.active_binding_graph = evaluation.active_binding_graph
            state.dicc_certificate = evaluation.dicc_certificate
            state.runtime_metrics.update({
                "normative_requirement_path_obligations": len(
                    evaluation.requirement_graph.path_obligations
                ),
                "executable_requirement_obligations": (
                    evaluation.dicc_certificate.path_obligation_count
                ),
                "requirement_path_obligations": (
                    len(evaluation.requirement_graph.path_obligations)
                    + evaluation.dicc_certificate.path_obligation_count
                ),
                "normative_active_binding_count": (
                    evaluation.binding_graph.build_stats.get("active_count", 0)
                ),
                "active_binding_count": (
                    evaluation.dicc_certificate.active_binding_count
                ),
                "normative_challenge_cell_count": len(
                    evaluation.challenge_graph.cells
                ),
                "active_challenge_count": len(
                    evaluation.dicc_certificate.executed_challenge_ids
                ),
                "real_execution_challenge_count": (
                    evaluation.dicc_certificate.real_challenge_execution_count
                ),
                "executed_challenge_ids": list(
                    evaluation.dicc_certificate.executed_challenge_ids
                ),
                "dicc_status": evaluation.dicc_certificate.status.value,
            })
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
                status="REACHED",
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
        limited = evidence_limited_complete(state)
        status = "REACHED" if reached else "EVIDENCE_LIMITED_COMPLETE" if limited else (
            state.termination_status or (
                "REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH"
                if state.checkpoint.patch.canonical_diff
                else "REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE"
            )
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
        root_causes = set(map(str, state.runtime_metrics.get("root_cause_labels", ())))
        if not reached:
            if not state.requirement_graph.leaves:
                root_causes.add("NO_REQUIREMENT_LEAF")
            if (
                not state.requirement_graph.path_obligations
                and not getattr(
                    state.executable_requirement_overlay,
                    "executable_requirements", (),
                )
            ):
                root_causes.add("NO_PATH_OBLIGATION")
            if not state.active_binding_graph.units:
                root_causes.add("NO_ACTIVE_BINDING")
            if (
                state.dicc_certificate is None
                or state.dicc_certificate.real_challenge_execution_count == 0
            ):
                root_causes.add("NO_EXECUTABLE_CHALLENGE")
            if any(
                getattr(item.status, "value", item.status) == "INVALID_SELECTOR"
                for item in getattr(state.target_recovery, "health_checks", ())
            ):
                root_causes.add("INVALID_PUBLIC_RUNNER")
            if state.environment_frontiers:
                root_causes.add("ENVIRONMENT_UNHEALTHY")
            if not any(
                item.classification.value == "TARGET_FIXED"
                for item in state.check_comparisons
            ):
                root_causes.add("NO_TARGET_PROGRESS")
            if (
                getattr(state.target_recovery, "targets", ())
                and not any(
                    item.candidate_cut_node_ids for item in state.causal_slices
                )
            ):
                root_causes.add("CAUSAL_CUT_EMPTY")
        if (
            state.dicc_certificate is not None
            and state.dicc_certificate.status.value == "CLOSED"
            and (
                not getattr(state.target_recovery, "targets", ())
                or state.dicc_certificate.real_target_execution_count == 0
                or state.dicc_certificate.path_obligation_count == 0
                or state.dicc_certificate.active_binding_count == 0
                or state.dicc_certificate.real_challenge_execution_count == 0
            )
        ):
            root_causes.add("FALSE_DICC_CLOSURE")
        state.runtime_metrics["root_cause_labels"] = sorted(root_causes)
        state.runtime_metrics.update({
            "relevant_binding_count": len(state.active_binding_graph.units),
            "static_actionable_count": sum(
                unit.status == "STATIC_ACTIONABLE"
                for unit in state.active_binding_graph.units.values()
            ),
            "execution_confirmed_count": sum(
                unit.status == "EXECUTION_CONFIRMED"
                for unit in state.active_binding_graph.units.values()
            ),
            "confirmed_failing_count": sum(
                unit.status in {
                    "TARGET_FAILING", "PRESERVATION_RISK", "COUNTEREXAMPLE_OPEN",
                }
                for unit in state.active_binding_graph.units.values()
            ),
            "confirmed_passing_count": sum(
                unit.status == "TARGET_PASSING"
                for unit in state.active_binding_graph.units.values()
            ),
            "binding_gap_count": len(
                state.active_binding_graph.unresolved_gaps
            ),
            "normative_candidate_binding_count": sum(
                unit.status == "CANDIDATE"
                for unit in state.active_binding_graph.units.values()
            ),
            "candidate_binding_count": sum(
                bool(unit.changed_hunk_ids)
                for unit in state.active_binding_graph.units.values()
            ),
            "normative_active_binding_count": sum(
                unit.status in {"ACTIVE", "READY"}
                for unit in state.active_binding_graph.units.values()
            ),
            "active_binding_count": sum(
                bool(
                    unit.target_check_ids
                    or unit.preservation_check_ids
                    or unit.challenge_check_ids
                )
                for unit in state.active_binding_graph.units.values()
            ),
            "deferred_binding_count": sum(
                unit.status == "DEFERRED"
                for unit in state.active_binding_graph.units.values()
            ),
            "normative_challenge_cell_count": len(state.challenge_graph.cells),
            "active_challenge_count": (
                len(state.dicc_certificate.executed_challenge_ids)
                if state.dicc_certificate is not None else 0
            ),
            "real_execution_challenge_count": (
                state.dicc_certificate.real_challenge_execution_count
                if state.dicc_certificate is not None else 0
            ),
            "final_patch_nonempty": bool(state.checkpoint.patch.canonical_diff),
            "final_patch_hash": state.checkpoint.patch.canonical_diff_hash,
            "final_status": status,
            "transition_count": state.transition_index,
            "dicc_status": (
                state.dicc_certificate.status.value
                if state.dicc_certificate is not None else "NOT_EVALUABLE"
            ),
        })
        final_patch = Path(state.run_root) / "final_patch.diff"
        final_patch.write_text(state.checkpoint.patch.canonical_diff, encoding="utf-8")
        artifacts = RunArtifacts(state.run_root, state.instance_id)
        if state.dicc_certificate is not None:
            artifacts.put(
                "dicc_certificate", state.dicc_certificate, state=state,
                producer="reachpatch.dicc",
                confidence=Confidence.CONFIRMED,
                status=state.dicc_certificate.status.value,
            )
        if state.generator_conversation is not None:
            artifacts.put(
                "generator_conversation", state.generator_conversation,
                state=state,
                producer="reachpatch.deepseek-agent",
                confidence=Confidence.CONFIRMED,
                status=status,
            )
        artifacts.persist_state(state)
        self._update_run_manifest(state)
        verification = artifacts.store.verify()
        if not verification["valid"]:
            raise RuntimeError(
                "refusing to seal an artifact-inconsistent run: "
                + "; ".join(str(item) for item in verification["errors"])
            )
        executable_targets = tuple(
            getattr(state.target_recovery, "targets", ())
        )
        comparison_by_check = {
            item.check_id: item for item in state.check_comparisons
        }
        if state.target_recovery is not None:
            unresolved_paths = tuple(sorted(
                check.check_id for check in executable_targets
                if check.check_id not in comparison_by_check
                or comparison_by_check[check.check_id].classification.value
                != "TARGET_FIXED"
            ))
        else:
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
                state.active_binding_graph.frontiers.values(),
                state.challenge_graph.frontiers.values(),
            )
            for frontier in source
            if frontier.status == "OPEN"
        }))
        if state.target_recovery is not None:
            target_complete = bool(executable_targets) and not unresolved_paths
            preservation_complete = all(
                comparison_by_check.get(check.check_id) is not None
                and comparison_by_check[check.check_id].classification.value
                == "PASS_PRESERVED"
                for check in state.target_recovery.preservation_checks
            )
        else:
            target_outcomes = [
                item for item in state.outcomes.values() if item.kind == "TARGET"
            ]
            preservation_outcomes = [
                item for item in state.outcomes.values()
                if item.kind == "PRESERVATION"
            ]
            target_complete = bool(target_outcomes) and all(
                item.status == OutcomeStatus.PASS for item in target_outcomes
            )
            preservation_complete = all(
                item.status == OutcomeStatus.PASS
                for item in preservation_outcomes
            )
        shadow_complete = all(
            item.component_shadow_pass for item in state.repair_history
        ) if state.repair_history else not bool(state.checkpoint.patch.canonical_diff)
        closure_complete = (
            state.dicc_certificate is not None
            and state.dicc_certificate.status.value == "CLOSED"
        ) if state.target_recovery is not None else (
            all(
                item.diff_challenge_closed
                for item in state.diff_closure_certificates
            ) if state.diff_closure_certificates else not bool(
                state.checkpoint.patch.canonical_diff
            )
        )
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
