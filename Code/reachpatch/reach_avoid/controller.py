"""Execution-driven Reach-Avoid production controller."""
from __future__ import annotations

import time
import urllib.error
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from reachpatch.execution import (
    copy_source_tree, diff_between, discard_ephemeral_tree,
    register_runtime_root, run_mechanical_checks, tree_hash,
)
from reachpatch.execution.checks import execute_check
from reachpatch.execution.target_recovery import (
    TargetRecoveryConfig, materialize_diff_checks, recover_target_checks,
)
from reachpatch.models.base import canonical_json, content_hash, stable_id
from reachpatch.models.core import Instance
from reachpatch.models.evidence import PublicEvidence, public_evidence_from_instance
from reachpatch.models.execution import (
    ActiveFailureKind, CheckExecution, CheckStatus, ExecutableCheck, FailureHistory,
    GeneratorSession, LockedCheck, ReachAvoidPhase, ReachAvoidState,
    StateCheckpoint, TerminalResult, TransitionCertificate,
    TransitionDecision,
)
from reachpatch.reach_avoid.active_failure import select_active_failure
from reachpatch.reach_avoid.dynamic_failure_graph import (
    DynamicFailureGraphBudget, build_dynamic_failure_graph,
)
from reachpatch.reach_avoid.execution_checkpoint import (
    ExecutionCheckpointStore, restore_parent_working_checkpoint,
    select_final_checkpoint, update_best_checkpoint, update_safe_checkpoint,
    update_working_checkpoint,
)
from reachpatch.reach_avoid.execution_transition import (
    all_reach_conditions_pass, compute_execution_atomic_progress,
    compute_mechanical_atomic_progress, decide_transition,
)
from reachpatch.repair.execution_objective import (
    InitialPatchObjective, RepairObjective,
    compile_execution_repair_objective,
)
from reachpatch.repair.initial_agent import InitialPatchAgent
from reachpatch.requirement_graph.compiler import compile_goal_contracts

from .repair_player import RepairPlayer


def incremental_mechanism_hash(incremental_diff: str) -> str:
    return stable_id("generator-incremental-diff", incremental_diff)


@dataclass(frozen=True, slots=True)
class ReachAvoidConfig:
    max_real_patch_revisions: int = 8
    max_no_progress_generator_attempts: int = 2
    execution_budget_seconds: float = 3600.0
    target_recovery_attempts: int = 1
    target_recovery_max_probes: int = 6
    target_recovery_stability_runs: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.max_real_patch_revisions <= 8:
            raise ValueError("max_real_patch_revisions must be between 1 and 8")
        if self.max_no_progress_generator_attempts < 1:
            raise ValueError("max_no_progress_generator_attempts must be positive")
        if self.execution_budget_seconds <= 0:
            raise ValueError("execution_budget_seconds must be positive")
        if self.target_recovery_max_probes < 1:
            raise ValueError("target_recovery_max_probes must be positive")
        if self.target_recovery_stability_runs < 2:
            raise ValueError("target_recovery_stability_runs must be at least two")


@dataclass(slots=True)
class _RunContext:
    instance: Instance
    public_evidence: PublicEvidence
    store: ExecutionCheckpointStore
    p0_patch_hash: str = ""


class ReachAvoidController:
    def __init__(self, repair_player: RepairPlayer, config: ReachAvoidConfig | None = None) -> None:
        self.repair_player = repair_player
        self.initial_patch_agent = InitialPatchAgent(repair_player)
        self.config = config or ReachAvoidConfig()
        self._contexts: dict[str, _RunContext] = {}

    @staticmethod
    def _run_root(instance: Instance, run_root: str | Path | None) -> Path:
        root = (
            Path(run_root).resolve() if run_root is not None else
            Path.cwd() / "runs" / stable_id(
                "execution-run", instance.instance_id, instance.base_commit,
                time.time_ns(),
            )
        )
        root.mkdir(parents=True, exist_ok=False)
        return root

    @staticmethod
    def _blockers(result: Any) -> tuple[str, ...]:
        names = tuple(
            f"{item.file}:{item.line}:{item.name}"
            for item in getattr(result, "undefined_name_findings", ())
            if getattr(item, "severity", "BLOCKER") == "BLOCKER"
        )
        return tuple(dict.fromkeys((
            *names,
            *(str(item) for item in getattr(result, "failure_reasons", ())),
        )))

    @classmethod
    def _applicable(cls, result: Any) -> bool:
        if getattr(result, "forbidden_edit", False) or getattr(result, "oracle_contamination", False):
            return False
        return not any(
            token in str(reason).casefold()
            for reason in getattr(result, "failure_reasons", ())
            for token in ("syntax error", "patch apply", "malformed diff")
        )

    @staticmethod
    def _observation_hash(execution: CheckExecution) -> str:
        return stable_id(
            "check-observation", execution.check_id,
            execution.status, execution.semantic_signature,
        )

    def _checkpoint(
        self,
        state: ReachAvoidState,
        tree: Path,
        *,
        parent: StateCheckpoint | None,
        status: str,
        mechanical: Any,
        targets: tuple[CheckExecution, ...],
        preservations: tuple[CheckExecution, ...],
        challenges: tuple[CheckExecution, ...] = (),
        locked_checks: tuple[LockedCheck, ...] | None = None,
        final_eligible: bool = False,
    ) -> StateCheckpoint:
        actual = diff_between(state.clean_snapshot, tree)
        checkpoint_id = stable_id(
            "execution-checkpoint", state.run_id, actual.patch_hash,
            state.revision_count, status,
            parent.checkpoint_id if parent is not None else "root",
        )
        snapshot = state.run_root / "execution_checkpoints" / checkpoint_id / "working_tree"
        checkpoint = StateCheckpoint(
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent.checkpoint_id if parent else None,
            snapshot_tree=str(snapshot), patch_hash=actual.patch_hash,
            cumulative_diff=actual.canonical_diff, status=status,
            revision=state.revision_count,
            patch_is_applicable=self._applicable(mechanical),
            repository_corrupted=any(
                "corrupt" in str(reason).casefold()
                for reason in getattr(mechanical, "failure_reasons", ())
            ),
            forbidden_path_changed=bool(getattr(mechanical, "forbidden_edit", False)),
            final_eligible=final_eligible,
            mechanical_result_hash=content_hash(mechanical),
            mechanical_blockers=self._blockers(mechanical),
            target_observation_hashes={
                item.check_id: self._observation_hash(item) for item in targets
            },
            preservation_observation_hashes={
                item.check_id: self._observation_hash(item) for item in preservations
            },
            challenge_observation_hashes={
                item.check_id: self._observation_hash(item) for item in challenges
            },
            locked_checks=locked_checks if locked_checks is not None else state.locked_checks,
            active_failure=state.active_failure,
            dynamic_failure_graph_hash=(
                state.dynamic_failure_graph.digest()
                if state.dynamic_failure_graph is not None else None
            ),
            working_tree_hash=tree_hash(tree),
        )
        return self._contexts[state.run_id].store.save(
            checkpoint, tree, mechanical=mechanical,
            target_results=targets, preservation_results=preservations,
            challenge_results=challenges,
        )

    @staticmethod
    def _execute_queue(
        tree: Path,
        checks: tuple[ExecutableCheck, ...],
        clean: Path,
    ) -> tuple[CheckExecution, ...]:
        return tuple(
            execute_check(tree, check, stability_runs=2, base_tree=clean)
            for check in checks
        )

    @staticmethod
    def _split(
        results: tuple[CheckExecution, ...], state: ReachAvoidState,
    ) -> tuple[tuple[CheckExecution, ...], tuple[CheckExecution, ...], tuple[CheckExecution, ...]]:
        target_ids = {item.check_id for item in state.target_checks}
        preservation_ids = {item.check_id for item in state.preservation_checks}
        challenge_ids = {item.check_id for item in state.challenge_checks}
        return (
            tuple(item for item in results if item.check_id in target_ids),
            tuple(item for item in results if item.check_id in preservation_ids),
            tuple(item for item in results if item.check_id in challenge_ids),
        )

    @staticmethod
    def _checks(state: ReachAvoidState) -> tuple[ExecutableCheck, ...]:
        unique: dict[str, ExecutableCheck] = {}
        for check in (
            *state.target_checks,
            *state.preservation_checks,
            *state.challenge_checks,
            *(item.check for item in state.locked_checks),
        ):
            unique.setdefault(check.check_id, check)
        return tuple(unique.values())

    @staticmethod
    def _updated_locks(
        state: ReachAvoidState,
        results: tuple[CheckExecution, ...],
        patch_hash: str,
    ) -> tuple[LockedCheck, ...]:
        locks = {item.check_id: item for item in state.locked_checks}
        checks = {item.check_id: item for item in ReachAvoidController._checks(state)}
        for execution in results:
            check = checks.get(execution.check_id)
            if (
                check is None or not check.trusted or not execution.stable
                or execution.status is not CheckStatus.PASS
            ):
                continue
            locks.setdefault(execution.check_id, LockedCheck(
                check=check,
                passing_observation_hash=ReachAvoidController._observation_hash(execution),
                patch_hash_when_locked=patch_hash,
            ))
        return tuple(sorted(locks.values(), key=lambda item: item.check_id))

    def _objective(self, state: ReachAvoidState, active: Any) -> RepairObjective:
        objective = compile_execution_repair_objective(
            state, active, target_checks=state.target_checks,
            preservation_checks=state.preservation_checks,
            challenge_checks=state.challenge_checks,
            dynamic_failure_graph=state.dynamic_failure_graph,
        )
        state.current_repair_objective = objective
        return objective

    @staticmethod
    def _decision_reason(
        decision: TransitionDecision,
        progress: tuple[Any, ...],
        preservation_results: tuple[CheckExecution, ...],
        mechanical_after: Any,
    ) -> str:
        if decision is TransitionDecision.REACHED:
            return "all executable Reach conditions passed"
        reasons = tuple(
            item.reason for item in progress
            if item.strict_progress or item.partial_progress or item.regression
        )
        if reasons:
            return "; ".join(dict.fromkeys(reasons))
        if any(item.status is CheckStatus.FAIL for item in preservation_results):
            return "confirmed preservation regression remains repairable"
        if not mechanical_after.passed:
            return "mechanical blocker remains repairable"
        if decision is TransitionDecision.REJECT_TRIAL:
            return "trial is inapplicable, corrupted, duplicate, or stably worse"
        return "no verified progress and no proof that the trial is worse"

    def _certificate(
        self,
        state: ReachAvoidState,
        parent: StateCheckpoint,
        trial: StateCheckpoint,
        decision: TransitionDecision,
        active: Any,
        progress: tuple[Any, ...],
        clean_results: tuple[CheckExecution, ...],
        parent_results: tuple[CheckExecution, ...],
        trial_results: tuple[CheckExecution, ...],
        mechanical_after: Any,
        preservation_results: tuple[CheckExecution, ...],
        locked_before: tuple[LockedCheck, ...],
        locked_after: tuple[LockedCheck, ...],
    ) -> TransitionCertificate:
        observation_hashes = tuple(
            f"{phase}:{item.check_id}:{self._observation_hash(item)}"
            for phase, results in (
                ("clean", clean_results),
                ("parent", parent_results),
                ("trial", trial_results),
            )
            for item in results
        )
        certificate_id = stable_id(
            "execution-transition", parent.checkpoint_id,
            trial.checkpoint_id, decision,
        )
        result = parent if decision is TransitionDecision.REJECT_TRIAL else trial
        request_ids = tuple(
            str(item["request_id"])
            for item in state.generator_session.conversation
            if isinstance(item, dict) and item.get("request_id")
        )
        regressions = tuple(sorted(
            item.check_id for item in progress if item.regression
        ))
        return TransitionCertificate(
            certificate_id=certificate_id, case_id=state.instance_id,
            revision_index=state.revision_count,
            parent_checkpoint_id=parent.checkpoint_id,
            trial_checkpoint_id=trial.checkpoint_id,
            result_checkpoint_id=result.checkpoint_id,
            parent_patch_hash=parent.patch_hash,
            trial_patch_hash=trial.patch_hash,
            result_patch_hash=result.patch_hash,
            decision=decision, active_failure_id=active.failure_id,
            active_failure_kind=active.kind.value,
            exact_failure_command=active.command,
            check_ids=tuple(item.check_id for item in trial_results),
            observation_hashes=observation_hashes,
            atomic_progress={item.check_id: item for item in progress},
            mechanical_blockers_before=parent.mechanical_blockers,
            mechanical_blockers_after=trial.mechanical_blockers,
            locked_checks_before=tuple(item.check_id for item in locked_before),
            locked_checks_after=tuple(item.check_id for item in locked_after),
            regressions=regressions,
            dynamic_failure_graph_hash=(
                state.dynamic_failure_graph.digest()
                if state.dynamic_failure_graph is not None else None
            ),
            decision_reason=self._decision_reason(
                decision, progress, preservation_results, mechanical_after,
            ),
            model_request_ids=request_ids,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    @staticmethod
    def _persist_transition_evidence(
        state: ReachAvoidState,
        certificate: TransitionCertificate,
        clean_results: tuple[CheckExecution, ...],
        parent_results: tuple[CheckExecution, ...],
        trial_results: tuple[CheckExecution, ...],
    ) -> None:
        transitions = state.run_root / "transitions"
        observations = state.run_root / "transition_observations"
        transitions.mkdir(parents=True, exist_ok=True)
        observations.mkdir(parents=True, exist_ok=True)
        (transitions / f"{certificate.certificate_id}.json").write_text(
            canonical_json(certificate) + "\n", encoding="utf-8",
        )
        (observations / f"{certificate.certificate_id}.json").write_text(
            canonical_json({
                "clean": [item.to_dict() for item in clean_results],
                "parent": [item.to_dict() for item in parent_results],
                "trial": [item.to_dict() for item in trial_results],
            }) + "\n", encoding="utf-8",
        )

    def _output(
        self, state: ReachAvoidState, checkpoint: StateCheckpoint, status: str,
    ) -> TerminalResult:
        state.phase = ReachAvoidPhase.SEALED
        state.termination_status = status
        output = state.run_root / "final.patch"
        output.write_text(checkpoint.cumulative_diff, encoding="utf-8")
        context = self._contexts[state.run_id]
        context.store.write_state(state)
        summary = {
            "schema": "reachpatch-execution-driven-v2",
            "status": status,
            "p0_patch_hash": context.p0_patch_hash,
            "final_patch_hash": checkpoint.patch_hash,
            "revision_count": state.revision_count,
            "transition_count": len(state.transition_history),
            "working_checkpoint": state.working_checkpoint.checkpoint_id,
            "safe_checkpoint": state.safe_checkpoint.checkpoint_id if state.safe_checkpoint else None,
            "best_checkpoint": state.best_checkpoint.checkpoint_id if state.best_checkpoint else None,
            "certified_checkpoint": state.certified_checkpoint.checkpoint_id if state.certified_checkpoint else None,
        }
        (state.run_root / "execution_summary.json").write_text(
            canonical_json(summary) + "\n", encoding="utf-8",
        )
        result = TerminalResult(
            state.instance_id, state.run_id, status, checkpoint.checkpoint_id,
            checkpoint.patch_hash, checkpoint.cumulative_diff, str(output),
        )
        (state.run_root / "terminal.json").write_text(
            canonical_json({"schema": "reachpatch-execution-driven-v2", "result": result}) + "\n",
            encoding="utf-8",
        )
        return result

    def _run_execution_driven(
        self, instance: Instance, *, run_root: str | Path | None = None,
    ) -> TerminalResult:
        started = time.monotonic()
        repository = instance.repository_path()
        root = self._run_root(instance, run_root)
        register_runtime_root(root)
        # Snapshot the exact clean tree before compiling any goal contracts.
        clean = root / "clean"
        copy_source_tree(
            repository, clean,
            exclude_paths=(root,) if root.is_relative_to(repository) else (),
        )
        public = public_evidence_from_instance(
            instance.issue, instance.visible_tests,
            instance.public_metadata, clean,
        )
        transport = getattr(
            getattr(self.repair_player, "generator_agent", None),
            "transport", None,
        )
        goals = compile_goal_contracts(instance.issue, public, (), transport, root)
        run_id = stable_id("execution-run", instance.instance_id, str(root))
        checkpoint_store = ExecutionCheckpointStore(root)
        blank = diff_between(clean, clean)
        boot_id = stable_id("execution-bootstrap", instance.instance_id, blank.patch_hash)
        boot_snapshot = root / "execution_checkpoints" / boot_id / "working_tree"
        boot = StateCheckpoint(
            checkpoint_id=boot_id,
            parent_checkpoint_id=None, snapshot_tree=str(boot_snapshot),
            patch_hash=blank.patch_hash, cumulative_diff=blank.canonical_diff,
            status="BOOTSTRAP", revision=0,
            working_tree_hash=tree_hash(clean),
        )
        state = ReachAvoidState(
            clean_snapshot=clean, working_checkpoint=boot,
            safe_checkpoint=None, best_checkpoint=None,
            certified_checkpoint=None, goal_contracts=goals,
            target_checks=(), preservation_checks=(), challenge_checks=(),
            locked_checks=(), active_failure=None,
            dynamic_failure_graph=None, failure_history={},
            transition_history=[], revision_count=0,
            instance_id=instance.instance_id, run_id=run_id,
            base_repository=repository, base_commit=instance.base_commit,
            run_root=root,
            generator_session=GeneratorSession(stable_id("execution-session", run_id)),
            execution_budget_seconds=self.config.execution_budget_seconds,
            remaining_wall_seconds=self.config.execution_budget_seconds,
            distinct_patch_hashes={blank.patch_hash},
        )
        context = _RunContext(instance, public, checkpoint_store)
        self._contexts[run_id] = context
        boot = context.store.save(
            boot, clean,
            mechanical=run_mechanical_checks(clean, blank, source_tree=clean),
        )
        state.working_checkpoint = boot
        context.store.write_state(state)
        initial = InitialPatchObjective(
            objective_id=stable_id("initial-execution-objective", run_id),
            goal_contracts=goals,
            public_context=({
                "source": "issue", "authority": "B", "content": instance.issue,
            },),
            current_full_diff=blank.canonical_diff,
            current_patch_hash=blank.patch_hash,
        )
        state.phase = ReachAvoidPhase.INITIAL_GENERATION
        state.current_repair_objective = initial
        initial_result = self.initial_patch_agent.generate(state, initial)
        state.generator_attempt_count += 1
        if not initial_result.has_new_nonempty_diff or not initial_result.modified_tree:
            return self._output(state, boot, "GENERATOR_BLOCKED_EXTERNAL")
        p0_tree = Path(initial_result.modified_tree)
        p0_diff = diff_between(clean, p0_tree)
        p0_mechanical = run_mechanical_checks(p0_tree, p0_diff, source_tree=clean)
        p0 = self._checkpoint(
            state, p0_tree, parent=None, status="P0",
            mechanical=p0_mechanical, targets=(), preservations=(),
            # P0 is the initial safe/evidence-limited candidate. It is not
            # certified Reach, but it remains eligible as a final fallback if
            # later trials are retained only for continued repair.
            final_eligible=True,
        )
        context.p0_patch_hash = p0.patch_hash
        discard_ephemeral_tree(p0_tree, root)
        update_working_checkpoint(state, p0)
        update_safe_checkpoint(state, p0)
        update_best_checkpoint(state, p0)

        state.phase = ReachAvoidPhase.TARGET_RECOVERY
        recovery_config = TargetRecoveryConfig(
            max_probes=self.config.target_recovery_max_probes,
            stability_runs=self.config.target_recovery_stability_runs,
        )
        recovery = recover_target_checks(
            repository, clean, Path(p0.snapshot_tree), goals, public,
            transport, root, recovery_config,
        )
        state.target_checks = recovery.target_checks
        state.preservation_checks = recovery.preservation_checks
        state.target_recovery = recovery
        context.store.write_state(state)
        if not state.target_checks:
            return self._output(state, p0, "EVIDENCE_LIMITED")

        hard_goal_ids = tuple(
            str(goal.goal_id) for goal in goals
            if bool(getattr(goal, "hard", False))
        )

        api_failures = 0
        empty_attempts = 0
        recovery_rounds = 0
        terminal_status = "BEST_EFFORT_REVISION_LIMIT"
        while (
            state.revision_count < self.config.max_real_patch_revisions
            and time.monotonic() - started < self.config.execution_budget_seconds
        ):
            context.store.validate(
                state.working_checkpoint, repository, clean_snapshot=clean,
            )
            working = Path(state.working_checkpoint.snapshot_tree)
            current_diff = diff_between(clean, working)
            mechanical_before = run_mechanical_checks(
                working, current_diff, source_tree=clean,
            )
            state.last_mechanical_result = mechanical_before
            checks = self._checks(state)
            parent_results = self._execute_queue(working, checks, clean)
            targets_before, preservation_before, challenges_before = self._split(
                parent_results, state,
            )
            new_locks = self._updated_locks(
                state, (*targets_before, *preservation_before),
                state.working_checkpoint.patch_hash,
            )
            if new_locks != state.locked_checks:
                state.locked_checks = new_locks
                synchronized = context.store.replace_metadata(replace(
                    state.working_checkpoint, locked_checks=new_locks,
                ))
                update_working_checkpoint(state, synchronized)

            if all_reach_conditions_pass(
                mechanical_before, targets_before,
                preservation_before, challenges_before, hard_goal_ids,
            ):
                certified = self._checkpoint(
                    state, working, parent=state.working_checkpoint,
                    status="CERTIFIED", mechanical=mechanical_before,
                    targets=targets_before, preservations=preservation_before,
                    challenges=challenges_before, final_eligible=True,
                )
                update_working_checkpoint(state, certified)
                update_safe_checkpoint(state, certified)
                update_best_checkpoint(state, certified)
                state.certified_checkpoint = certified
                return self._output(state, certified, "REACHED")

            active = select_active_failure(
                mechanical_before, targets_before, preservation_before,
                challenges_before, state.failure_history,
                target_checks=state.target_checks,
                preservation_checks=state.preservation_checks,
                challenge_checks=state.challenge_checks,
            )
            if active is None:
                unresolved = any(
                    item.status in {CheckStatus.UNKNOWN, CheckStatus.BLOCKED}
                    for item in parent_results
                ) or bool(
                    set(hard_goal_ids)
                    - {str(item.goal_id) for item in state.target_checks if item.goal_id}
                )
                if unresolved and recovery_rounds < self.config.target_recovery_attempts:
                    recovery_rounds += 1
                    recovery = recover_target_checks(
                        repository, clean, working, goals, public,
                        transport, root, recovery_config,
                    )
                    state.target_checks = recovery.target_checks
                    state.preservation_checks = recovery.preservation_checks
                    state.target_recovery = recovery
                    continue
                terminal_status = "EVIDENCE_LIMITED" if unresolved else "MECHANISM_EXHAUSTED"
                break

            active_execution = next((
                item for item in parent_results if item.check_id == active.check_id
            ), None)
            state.failure_history[active.signature] = FailureHistory(
                signature=active.signature, count=active.same_signature_count,
                check_id=active.check_id,
                last_patch_hash=state.working_checkpoint.patch_hash,
                last_observation_hash=(
                    self._observation_hash(active_execution)
                    if active_execution is not None else content_hash(active)
                ),
            )
            state.active_failure = active

            graph_eligible = bool(
                active.same_signature_count >= 2
                or active.kind is ActiveFailureKind.PRESERVATION
            )
            if not graph_eligible:
                # A graph is local to one observed failure. Never leak stale
                # context from a prior target into a first-time objective.
                state.dynamic_failure_graph = None
            if graph_eligible and active_execution is not None:
                state.dynamic_failure_graph = build_dynamic_failure_graph(
                    repository, working, current_diff.canonical_diff,
                    active, active_execution.trace,
                    state.dynamic_failure_graph, DynamicFailureGraphBudget(),
                )
                materialized = materialize_diff_checks(
                    working, current_diff.canonical_diff, active,
                    (*state.target_checks, *state.preservation_checks),
                    state.dynamic_failure_graph, state.challenge_checks,
                )
                if materialized != state.challenge_checks:
                    state.challenge_checks = materialized
                    checks = self._checks(state)
                    parent_results = self._execute_queue(working, checks, clean)
                    targets_before, preservation_before, challenges_before = self._split(
                        parent_results, state,
                    )

            clean_results = self._execute_queue(clean, checks, clean)
            objective = self._objective(state, active)
            parent = state.working_checkpoint
            state.phase = ReachAvoidPhase.REPAIR
            try:
                trial_result = self.repair_player.revise_working_patch(state, objective)
            except Exception as error:
                external_failure = isinstance(
                    error, (urllib.error.URLError, TimeoutError, json.JSONDecodeError)
                )
                if external_failure:
                    api_failures += 1
                else:
                    empty_attempts += 1
                with (root / "repair_errors.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(canonical_json({
                        "failure_id": active.failure_id,
                        "kind": "EXTERNAL_API_UNAVAILABLE" if external_failure else "REPAIR_MECHANISM_ERROR",
                        "error": str(error)[-12000:],
                    }) + "\n")
                if (
                    external_failure
                    and api_failures >= self.config.max_no_progress_generator_attempts
                ):
                    terminal_status = "EXTERNAL_API_UNAVAILABLE"
                    break
                if (
                    not external_failure
                    and empty_attempts >= self.config.max_no_progress_generator_attempts
                ):
                    terminal_status = "MECHANISM_EXHAUSTED"
                    break
                continue
            api_failures = 0
            state.generator_attempt_count += 1
            if not trial_result.has_new_nonempty_diff or not trial_result.modified_tree:
                empty_attempts += 1
                if empty_attempts >= self.config.max_no_progress_generator_attempts:
                    terminal_status = "MECHANISM_EXHAUSTED"
                    break
                continue
            empty_attempts = 0
            trial_tree = Path(trial_result.modified_tree)
            trial_diff = diff_between(clean, trial_tree)
            if trial_diff.patch_hash in state.distinct_patch_hashes:
                discard_ephemeral_tree(trial_tree, root)
                continue

            state.revision_count += 1
            state.distinct_patch_hashes.add(trial_diff.patch_hash)
            mechanical_after = run_mechanical_checks(
                trial_tree, trial_diff, source_tree=clean,
            )
            trial_results = self._execute_queue(trial_tree, checks, clean)
            targets_after, preservation_after, challenges_after = self._split(
                trial_results, state,
            )
            parent_by_id = {item.check_id: item for item in parent_results}
            check_by_id = {item.check_id: item for item in checks}
            progress = tuple(
                compute_execution_atomic_progress(
                    parent_by_id[item.check_id], item, check_by_id[item.check_id],
                )
                for item in trial_results if item.check_id in parent_by_id
            ) + (compute_mechanical_atomic_progress(
                mechanical_before, mechanical_after,
            ),)
            locked_before = state.locked_checks
            locked_after = self._updated_locks(
                state, (*targets_after, *preservation_after, *challenges_after), trial_diff.patch_hash,
            )
            trial = self._checkpoint(
                state, trial_tree, parent=parent, status="TRIAL",
                mechanical=mechanical_after, targets=targets_after,
                preservations=preservation_after, challenges=challenges_after,
                locked_checks=locked_after,
            )
            state.phase = ReachAvoidPhase.TRANSITION
            decision = decide_transition(
                parent, trial, mechanical_before, mechanical_after,
                progress, targets_after, preservation_after, challenges_after,
                hard_goal_ids,
            )
            certificate = self._certificate(
                state, parent, trial, decision, active, progress,
                clean_results, parent_results, trial_results,
                mechanical_after, preservation_after,
                locked_before, locked_after,
            )
            self._persist_transition_evidence(
                state, certificate, clean_results,
                parent_results, trial_results,
            )
            state.transition_history.append(certificate)

            if decision is TransitionDecision.REJECT_TRIAL:
                state.rejected_patch_hashes.add(trial.patch_hash)
                restore_parent_working_checkpoint(state, parent, context.store)
            else:
                eligible = decision in {
                    TransitionDecision.ADVANCE_SAFE,
                    TransitionDecision.REACHED,
                }
                accepted = context.store.replace_metadata(replace(
                    trial, final_eligible=eligible,
                    transition_certificate_id=certificate.certificate_id,
                ))
                state.locked_checks = locked_after
                update_working_checkpoint(state, accepted)
                if decision is TransitionDecision.ADVANCE_SAFE:
                    update_safe_checkpoint(state, accepted)
                    update_best_checkpoint(state, accepted)
                elif decision is TransitionDecision.REACHED:
                    update_safe_checkpoint(state, accepted)
                    update_best_checkpoint(state, accepted)
                    state.certified_checkpoint = accepted
                    discard_ephemeral_tree(trial_tree, root)
                    return self._output(state, accepted, "REACHED")
            context.store.write_state(state)
            discard_ephemeral_tree(trial_tree, root)

        state.remaining_wall_seconds = max(
            0.0,
            self.config.execution_budget_seconds - (time.monotonic() - started),
        )
        return self._output(state, select_final_checkpoint(state), terminal_status)

    def run(
        self, instance: Instance, *, run_root: str | Path | None = None,
    ) -> TerminalResult:
        return self._run_execution_driven(instance, run_root=run_root)
