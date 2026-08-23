from __future__ import annotations

import dataclasses
import os
import re
import resource
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from reachpatch.challenge_graph.execute import execute_challenge_round
from reachpatch.challenge_graph.models import open_high_challenge_ids
from reachpatch.execution import (
    apply_generator_result, clear_execution_hot_cache, copy_source_tree,
    diff_between, discard_bootstrap_tree, discard_ephemeral_tree, register_runtime_root,
    run_mechanical_checks,
)
from reachpatch.models.base import canonical_json, stable_id
from reachpatch.models.core import Instance
from reachpatch.models.evidence import (
    FailureHistory, LockedCheckSet, ObservationBundle, PublicEvidence,
    discover_diff_public_checks, primary_issue_content,
    public_evidence_from_instance,
)
from reachpatch.models.graphs import ContextRequest, GraphBudget, empty_graph_stack
from reachpatch.models.reach_avoid import (
    CheckpointEvidence, GeneratorSession, PerformanceRecord, ReachAvoidPhase,
    ReachAvoidState, RepairObjective, StateCheckpoint, TerminalResult, ChallengeSelection,
)
from reachpatch.reach_avoid.frontier import (
    FrontierStatus, NextActionKind, RepairFrontierKind, derive_repair_frontiers,
    select_next_action,
)
from reachpatch.repair.objective import compile_repair_objective
from reachpatch.requirement_graph.builder import build_requirement_graph
from reachpatch.program_graph import RepositoryIndex, clear_program_graph_caches

from .checkpoint import (
    CheckpointStore, capture_current_graph_checkpoint, capture_initial_checkpoint,
)
from .gates import evaluate_reach
from .graph_stack import (
    build_graph_stack, latest_graph_metrics, update_graph_stack_after_diff,
)
from .persistence import apply_transition_decision, record_locked_passes
from .repair_player import RepairPlayer
from .transition import evaluate_trial_transition


@dataclass(frozen=True, slots=True)
class ReachAvoidConfig:
    max_real_patch_revisions: int = 8
    max_no_progress_generator_attempts: int = 2
    max_challenge_attempts_per_frontier: int = 3
    max_challenge_rounds: int = 24
    max_challenge_batch: int = 6
    execution_budget_seconds: float = 3600.0
    graph_budget: GraphBudget = field(default_factory=lambda: GraphBudget(
        max_files=8, max_nodes=1500, max_edges=6000, direct_caller_depth=1,
    ))

    def __post_init__(self) -> None:
        if not 1 <= self.max_real_patch_revisions <= 8:
            raise ValueError("max_real_patch_revisions must be between 1 and 8")
        if self.max_no_progress_generator_attempts != 2:
            raise ValueError("max_no_progress_generator_attempts must be 2")
        if self.max_challenge_attempts_per_frontier != 3:
            raise ValueError("max_challenge_attempts_per_frontier must be 3")
        if not 1 <= self.max_challenge_batch <= 6:
            raise ValueError("max_challenge_batch must be between 1 and 6")
        if not 1 <= self.max_challenge_rounds <= 24:
            raise ValueError("max_challenge_rounds must be between 1 and 24")


@dataclass(slots=True)
class _RunContext:
    instance: Instance
    public_evidence: PublicEvidence
    requirement_graph: Any
    store: CheckpointStore
    initial_result: Any = None
    initial_tree: Path | None = None
    initial_mechanical: Any = None


class ReachAvoidController:
    def __init__(
        self,
        repair_player: RepairPlayer,
        config: ReachAvoidConfig | None = None,
    ) -> None:
        self.repair_player = repair_player
        self.config = config or ReachAvoidConfig()
        self._contexts: dict[str, _RunContext] = {}

    def _run_root(self, instance: Instance, run_root: str | Path | None) -> Path:
        if run_root:
            result = Path(run_root).resolve()
        else:
            result = Path.cwd() / "runs" / stable_id(
                "run", instance.instance_id, instance.base_commit, time.time_ns(),
            )
        result.mkdir(parents=True, exist_ok=False)
        return result

    def initialize(
        self,
        instance: Instance,
        *,
        run_root: str | Path | None = None,
    ) -> ReachAvoidState:
        repository = instance.repository_path()
        root = self._run_root(instance, run_root)
        register_runtime_root(root)
        public = public_evidence_from_instance(
            instance.issue, instance.visible_tests, instance.public_metadata, repository,
        )
        requirement = build_requirement_graph(instance.issue, public)
        bootstrap = root / "bootstrap_working"
        copy_source_tree(
            repository,
            bootstrap,
            exclude_paths=(root,) if root.is_relative_to(repository) else (),
        )
        actual = diff_between(repository, bootstrap)
        stack = empty_graph_stack(instance.base_commit, actual.patch_hash)
        evidence = CheckpointEvidence(False, True, 0, 0, 0, 0, 0, 0)
        placeholder = StateCheckpoint(
            checkpoint_id=stable_id("bootstrap", instance.instance_id, actual.patch_hash),
            parent_checkpoint_id=None,
            snapshot_tree=str(bootstrap),
            patch_hash=actual.patch_hash,
            canonical_diff=actual.canonical_diff,
            graph_hashes=stack.graph_hashes(),
            graph_snapshot_dir="",
            evidence=evidence,
            locked_check_ids=(),
            open_counterexample_ids=(),
            open_high_challenge_ids=(),
            status="BOOTSTRAP",
            revision=0,
        )
        run_id = stable_id("run", instance.instance_id, root)
        state = ReachAvoidState(
            instance_id=instance.instance_id,
            run_id=run_id,
            base_repository=repository,
            base_commit=instance.base_commit,
            run_root=root,
            graph_stack=stack,
            working_checkpoint=placeholder,
            best_checkpoint=placeholder,
            certified_checkpoint=None,
            checkpoint_history={},
            observations=ObservationBundle(),
            counterexamples=[],
            locked_checks=LockedCheckSet(),
            confirmed_failures=[],
            failure_history={},
            generator_session=GeneratorSession(stable_id("generator-session", run_id)),
            current_repair_objective=None,
            repair_revision_count=0,
            generator_attempt_count=0,
            challenge_round_count=0,
            no_progress_generator_attempts=0,
            frontier_attempts={},
            phase=ReachAvoidPhase.INITIALIZING,
            termination_status=None,
            execution_budget_seconds=self.config.execution_budget_seconds,
            remaining_wall_seconds=self.config.execution_budget_seconds,
            graph_budget=self.config.graph_budget,
        )
        store = CheckpointStore(root)
        self._contexts[run_id] = _RunContext(instance, public, requirement, store)
        (root / "run.json").write_text(canonical_json({
            "schema": "reachpatch-reach-avoid-v2",
            "instance": instance.to_dict(),
            "config": dataclasses.asdict(self.config),
        }) + "\n", encoding="utf-8")
        return state

    def _initial_slices(self, state: ReachAvoidState, issue: str) -> tuple[dict[str, Any], ...]:
        context = self._contexts[state.run_id]
        primary_issue = primary_issue_content(issue)
        public_discussion = issue[len(primary_issue):]
        protected = {
            argument.replace("\\", "/").removeprefix("./")
            for check in context.public_evidence.checks
            for argument in check.command
            if argument.endswith(".py")
        }
        identifiers = tuple(dict.fromkeys(
            [
                part
                for leaf in context.requirement_graph.leaves.values()
                if not leaf.preservation
                for part in reversed(leaf.operation.split("."))
            ]
            + [
                part
                for value in re.findall(
                    r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`", primary_issue,
                )
                for part in value.split(".")
            ]
            + [
                part
                for value in re.findall(
                    r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(", primary_issue,
                )
                for part in value.split(".")
            ]
            + re.findall(r"\b([A-Z][A-Za-z0-9_]{2,})\b", primary_issue)
            + re.findall(r"\b([a-z_][a-z0-9_]{3,})\s*=", primary_issue)
            + [
                symbol for check in context.public_evidence.checks
                for symbol in check.symbol_references
            ]
        ))[:40]
        result = []
        index = RepositoryIndex.build(
            state.base_repository, state.base_commit, identifiers,
            self.config.graph_budget.max_files,
        )
        focused_identifiers = tuple(dict.fromkeys(
            value.split(".")[-1]
            for text in (public_discussion, primary_issue)
            for value in re.findall(
                r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b", text,
            )
        ))[:8]
        for identifier in focused_identifiers:
            index.expand_symbol(identifier, max_matches=8)
        candidates = {
            relative for values in index.symbol_files.values() for relative in values
        }
        candidates.update(
            str(item).replace("\\", "/").removeprefix("./")
            for item in context.instance.public_metadata.get("public_source_paths", ())
            if isinstance(item, str)
        )
        candidates.update(
            argument.replace("\\", "/").removeprefix("./")
            for check in context.public_evidence.checks
            for argument in check.command
            if argument.endswith(".py")
        )
        candidates.update(
            match.replace("\\", "/").removeprefix("./")
            for match in re.findall(r"(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py", issue)
        )
        dotted_modules = re.findall(
            r"\b(?:from|import)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)", issue,
        )
        candidates.update(
            module.replace(".", "/") + ".py"
            for module in dotted_modules
        )
        candidates.update(
            module.replace(".", "/") + "/__init__.py"
            for module in dotted_modules
        )
        def candidate_rank(relative: str) -> tuple[Any, ...]:
            supporting = [
                identifier for identifier in identifiers
                if relative in index.symbol_files.get(identifier, ())
            ]
            rarity = min(
                (len(index.symbol_files[identifier]) for identifier in supporting),
                default=10**9,
            )
            path_tokens = {
                Path(relative).stem.casefold(),
                *(part.casefold() for part in Path(relative).parts),
            }
            exact_stem = any(
                identifier.casefold() == Path(relative).stem.casefold()
                for identifier in supporting if len(identifier) >= 3
            )
            path_support = sum(
                identifier.casefold() in path_tokens
                for identifier in supporting if len(identifier) >= 3
            )
            return (
                rarity,
                not exact_stem,
                -path_support,
                -len(supporting),
                len(Path(relative).parts),
                relative,
            )

        ranked_candidates = sorted(candidates, key=candidate_rank)
        for relative_name in ranked_candidates:
            if relative_name in protected:
                continue
            path = state.base_repository / relative_name
            if not path.is_file() or path.suffix != ".py":
                continue
            relative = path.relative_to(state.base_repository)
            text = path.read_text(encoding="utf-8", errors="replace")
            match_line = next((
                number
                for name in identifiers
                for number, line in enumerate(text.splitlines(), 1)
                if re.search(rf"\b{re.escape(name)}\b", line)
            ), None)
            if match_line is None and any(
                identifier.casefold() in relative.stem.casefold()
                for identifier in identifiers if len(identifier) >= 3
            ):
                match_line = 1
            if match_line is None:
                continue
            lines = text.splitlines()
            start = max(1, match_line - 25)
            end = min(len(lines), match_line + 100)
            result.append({
                "path": relative.as_posix(),
                "start_line": start,
                "end_line": end,
                "content": "\n".join(
                    f"{number}: {lines[number - 1]}" for number in range(start, end + 1)
                ),
            })
            if len(result) >= 4:
                break
        return tuple(result)

    def _initial_objective(self, state: ReachAvoidState) -> RepairObjective:
        context = self._contexts[state.run_id]
        leaves = tuple(context.requirement_graph.leaves.values())
        primary_issue = primary_issue_content(context.instance.issue)
        discussion = context.instance.issue[len(primary_issue):].strip()
        public_context = ({
            "source": "ISSUE_REPORT",
            "authority": "B",
            "normative": True,
            "content": primary_issue[:8000],
        },)
        if discussion:
            public_context += ({
                "source": "PUBLIC_DISCUSSION",
                "authority": "PROVISIONAL",
                "normative": False,
                "content": discussion[:16000],
            },)
        primary = leaves[0].to_dict() if leaves else {
            "requirement_id": "issue", "operation": context.instance.issue,
        }
        return RepairObjective(
            objective_id=stable_id("initial-objective", state.instance_id, context.instance.issue),
            objective_kind="INITIAL_PATCH",
            primary_requirement=primary,
            related_requirements=tuple(item.to_dict() for item in leaves),
            public_context=public_context,
            related_failures=(),
            counterexamples=(),
            preservation_requirements=tuple(
                item.to_dict() for item in leaves if item.preservation
            ),
            reproduction_commands=tuple(check.command for check in context.public_evidence.checks),
            concrete_inputs=tuple(check.concrete_input for check in context.public_evidence.checks),
            input_derivations=tuple(
                (f"Public check {check.check_id}",)
                for check in context.public_evidence.checks
            ),
            oracle_relations=tuple({
                "authority": check.authority,
                "expected": check.expected.to_dict() if check.expected else {"exit_code": 0},
            } for check in context.public_evidence.checks),
            observations=(),
            failure_signatures=(),
            first_divergences=(),
            executed_path_ids=(),
            guarded_branch_ids=(),
            causal_guidance={},
            bindings=(),
            actual_hunks=(),
            causal_cuts=(),
            impact_cone=None,
            impact_risks=(),
            protected_target_ids=(),
            protected_preservation_ids=(),
            suggested_action_families=("EDIT_REQUIREMENT_RELEVANT_SOURCE",),
            locked_check_ids=(),
            cumulative_diff="",
            failed_mechanisms=(),
            forbidden_mechanisms=(),
            editable_source_slices=self._initial_slices(state, context.instance.issue),
            expected_next_effects=("Produce a non-empty patch that satisfies every HARD target",),
        )

    def generate_initial_patch(self, state: ReachAvoidState) -> None:
        state.phase = ReachAvoidPhase.INITIAL_GENERATION
        objective = self._initial_objective(state)
        state.current_repair_objective = objective
        started = time.monotonic()
        result = self.repair_player.revise_working_patch(state, objective, initial=True)
        state.generator_attempt_count += 1
        self._record_generator_attempt(
            state, result, objective_kind=objective.objective_kind,
            initial=True, duration_seconds=time.monotonic() - started,
        )
        context = self._contexts[state.run_id]
        context.initial_result = result
        if not result.has_new_nonempty_diff:
            raise RuntimeError(result.error_kind or "initial generator returned an empty patch")
        initial_tree = state.run_root / "initial_working"
        self._record_initial_stage(state, "COPY_INITIAL_TREE")
        copy_source_tree(Path(state.working_checkpoint.snapshot_tree), initial_tree)
        self._record_initial_stage(state, "APPLY_GENERATOR_RESULT")
        apply_generator_result(initial_tree, result)
        self._record_initial_stage(state, "DISCARD_GENERATOR_STAGING")
        discard_ephemeral_tree(result.modified_tree, state.run_root)
        self._record_initial_stage(state, "COMPUTE_INITIAL_DIFF")
        actual = diff_between(state.base_repository, initial_tree)
        if actual.empty:
            raise RuntimeError("initial generator did not change the working tree")
        context.initial_tree = initial_tree
        self._record_initial_stage(
            state, "RUN_INITIAL_MECHANICAL", patch_hash=actual.patch_hash,
        )
        context.initial_mechanical = run_mechanical_checks(
            initial_tree,
            actual,
            source_tree=state.base_repository,
            oracle_paths=tuple(
                argument
                for check in context.public_evidence.checks
                for argument in check.command
                if argument.endswith(".py")
            ),
        )
        self._record_initial_stage(
            state, "INITIAL_PATCH_READY", patch_hash=actual.patch_hash,
        )

    def initialize_graph_stack(self, state: ReachAvoidState) -> None:
        state.phase = ReachAvoidPhase.GRAPH_SYNC
        context = self._contexts[state.run_id]
        if context.initial_tree is None:
            raise RuntimeError("initial patch must be generated before graph initialization")
        actual = diff_between(state.base_repository, context.initial_tree)
        discovered_checks = discover_diff_public_checks(
            state.base_repository, actual, context.public_evidence.checks,
            max_checks=self.config.max_challenge_batch,
        )
        if discovered_checks:
            context.public_evidence = replace(
                context.public_evidence,
                checks=context.public_evidence.checks + discovered_checks,
            )
            context.requirement_graph = build_requirement_graph(
                context.instance.issue, context.public_evidence,
            )
        stack = build_graph_stack(
            context.initial_tree,
            state.base_commit,
            context.instance.issue,
            context.requirement_graph,
            actual,
            context.public_evidence,
            self.config.graph_budget,
            revision=0,
        )
        mechanical = context.initial_mechanical
        state.last_mechanical_result = mechanical
        cells = stack.challenge_graph.active_cells()
        evidence = CheckpointEvidence(
            mechanical_pass=mechanical.passed,
            no_known_preservation_regression=True,
            confirmed_target_pass_count=0,
            closed_confirmed_failure_count=0,
            execution_confirmed_requirement_count=0,
            execution_confirmed_binding_count=0,
            open_high_challenge_count=len(open_high_challenge_ids(cells)),
            open_counterexample_count=0,
        )
        checkpoint = capture_initial_checkpoint(
            store=context.store,
            base_repository=state.base_repository,
            source_tree=context.initial_tree,
            graph_stack=stack,
            evidence=evidence,
            locked_checks=state.locked_checks,
            observations=state.observations,
            status="INITIAL_WORKING",
            state=state,
        )
        state.graph_stack = stack
        state.working_checkpoint = checkpoint
        state.best_checkpoint = checkpoint
        state.checkpoint_history = {checkpoint.checkpoint_id: checkpoint}
        context.initial_result = None
        context.initial_mechanical = None
        context.initial_tree = None
        discard_bootstrap_tree(state.run_root / "initial_working", state.run_root)
        discard_bootstrap_tree(state.run_root / "bootstrap_working", state.run_root)
        self._refresh_repair_frontiers(state)

    def _refresh_confirmed_failures(self, state: ReachAvoidState) -> None:
        cells = state.graph_stack.challenge_graph.cells
        state.confirmed_failures = [
            failure if (
                failure.patch_hash != state.graph_stack.patch_hash
                or not failure.open
            ) else replace(
                failure,
                open=not (
                    failure.challenge_id in cells
                    and cells[failure.challenge_id].terminal_status.value == "PASS"
                ),
            )
            for failure in state.confirmed_failures
        ]
        for failure in state.confirmed_failures:
            history = state.failure_history.get(failure.failure_signature)
            if history is not None:
                history.closed = not failure.open

    def _apply_challenge_result(self, state: ReachAvoidState, result) -> None:
        state.graph_stack = result.updated_graph_stack
        for execution in result.executions:
            cell = state.graph_stack.challenge_graph.cells.get(execution.challenge_id)
            if cell:
                state.observations.record(execution, cell.requirement_id)
        record_locked_passes(state, state.graph_stack, result.executions)
        known_packets = {item.counterexample_id for item in state.counterexamples}
        state.counterexamples.extend(
            item for item in result.counterexamples if item.counterexample_id not in known_packets
        )
        known_failures = {item.failure_id for item in state.confirmed_failures}
        state.confirmed_failures.extend(
            item for item in result.confirmed_failures if item.failure_id not in known_failures
        )
        for failure in result.confirmed_failures:
            history = state.failure_history.setdefault(
                failure.failure_signature, FailureHistory(failure.failure_signature),
            )
            if failure.counterexample_id not in history.counterexample_ids:
                history.counterexample_ids.append(failure.counterexample_id)
        for challenge_id in result.selected_challenge_ids:
            state.frontier_attempts[challenge_id] = state.frontier_attempts.get(challenge_id, 0) + 1
            cell = state.graph_stack.challenge_graph.cells.get(challenge_id)
            if cell is not None:
                for frontier_id, recipe_ids in state.graph_stack.challenge_graph.frontier_attempts.items():
                    if cell.input_recipe.recipe_id in recipe_ids:
                        state.frontier_attempts[frontier_id] = (
                            state.frontier_attempts.get(frontier_id, 0) + 1
                        )
        context = self._contexts[state.run_id]
        checkpoint = capture_current_graph_checkpoint(state, context.store, "CHALLENGE_EVIDENCE")
        state.working_checkpoint = checkpoint
        state.checkpoint_history[checkpoint.checkpoint_id] = checkpoint
        if checkpoint.evidence.rank() > state.best_checkpoint.evidence.rank():
            state.best_checkpoint = checkpoint
        self._refresh_repair_frontiers(state)

    def _refresh_repair_frontiers(self, state: ReachAvoidState) -> None:
        """Synchronize RepairFrontier state after every evidence/graph event."""
        context = self._contexts[state.run_id]
        previous = state.repair_frontiers
        mechanical = state.last_mechanical_result
        derived = derive_repair_frontiers(
            state, state.graph_stack.requirement_graph, state.graph_stack.program_graph,
            state.graph_stack.binding_graph, state.graph_stack.challenge_graph,
            state.observations, mechanical,
        )
        # Preserve closure/exhaustion evidence across incremental graph rebuilds.
        for frontier_id, old in previous.items():
            current = derived.get(frontier_id)
            if current is None:
                if old.patch_hash == state.graph_stack.patch_hash:
                    derived[frontier_id] = replace(old, status=FrontierStatus.CLOSED,
                                                   closure_evidence=old.closure_evidence + ({"event": "evidence-change"},))
            elif old.status in {FrontierStatus.CLOSED, FrontierStatus.EXHAUSTED, FrontierStatus.SUPERSEDED}:
                derived[frontier_id] = replace(current, status=old.status,
                                               closure_evidence=old.closure_evidence)
        state.repair_frontiers = derived

    def _action_selection(self, state: ReachAvoidState):
        self._refresh_repair_frontiers(state)
        return select_next_action(
            state, max_challenge_rounds=self.config.max_challenge_rounds,
        )

    def _expand_binding_frontier(self, state: ReachAvoidState, selection) -> None:
        requests = []
        gaps = {gap.gap_id: gap for gap in state.graph_stack.binding_graph.gaps}
        for requirement_id, action in selection.recovery_actions:
            gap = next((item for item in gaps.values() if item.requirement_id == requirement_id), None)
            symbols = gap.attempted_symbols if gap else ()
            for symbol in symbols[:3] or (requirement_id,):
                requests.append(ContextRequest(
                    stable_id("context-request", requirement_id, action, symbol),
                    action,
                    symbol,
                    2 if state.frontier_attempts.get(requirement_id, 0) else 1,
                ))
            state.frontier_attempts[requirement_id] = state.frontier_attempts.get(requirement_id, 0) + 1
        context = self._contexts[state.run_id]
        actual = diff_between(state.base_repository, state.working_checkpoint.snapshot_tree)
        expanded = update_graph_stack_after_diff(
            state.graph_stack, actual, Path(state.working_checkpoint.snapshot_tree),
            state.base_repository, context.instance.issue, context.public_evidence,
            replace(
                self.config.graph_budget,
                direct_caller_depth=max(
                    (request.depth for request in requests), default=1,
                ),
            ),
            context_requests=tuple(requests),
        )
        state.graph_stack = replace(expanded, revision=state.graph_stack.revision)
        state.graph_stack.validate()
        checkpoint = capture_current_graph_checkpoint(state, context.store, "FRONTIER_EXPANSION")
        state.working_checkpoint = checkpoint
        state.checkpoint_history[checkpoint.checkpoint_id] = checkpoint

    def _performance(self, state: ReachAvoidState, record: PerformanceRecord) -> None:
        path = state.run_root / "performance.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(record) + "\n")

    def _record_generator_attempt(
        self,
        state: ReachAvoidState,
        result: Any,
        *,
        objective_kind: str,
        initial: bool,
        duration_seconds: float,
    ) -> None:
        event = {
            "attempt_id": stable_id("generator-attempt", state.run_id, state.generator_attempt_count, result.result_id),
            "initial": initial,
            "objective_kind": objective_kind,
            "result_id": result.result_id,
            "result_kind": "PENDING_TRANSITION" if not initial else "INITIAL",
            "source_patch_hash": state.graph_stack.patch_hash,
            "changed_files": tuple(),
            "has_new_nonempty_diff": result.has_new_nonempty_diff,
            "incremental_diff_hash": stable_id("generator-incremental-diff", result.incremental_diff),
            "mechanism": result.mechanism,
            "summary": result.summary[-4000:],
            "error_kind": result.error_kind,
            "structure_recovery_attempted": result.structure_recovery_attempted,
            "duration_seconds": duration_seconds,
        }
        state.generator_session.attempt_history.append(event)
        path = state.run_root / "generator_attempts.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(event | {
                "attempt": state.generator_attempt_count,
            }) + "\n")

    @staticmethod
    def _finalize_generator_attempt(
        state: ReachAvoidState,
        failure,
        trial,
    ) -> None:
        """Attach the Reach-Avoid verdict to the generated-diff record."""

        # The generator attempt is recorded before transition evaluation.  The
        # newest pending record is therefore the only valid parent for this
        # trial; checkpoint ids and patch hashes are deliberately distinct.
        pending = next((
            item for item in reversed(state.generator_session.attempt_history)
            if item.get("result_kind") == "PENDING_TRANSITION"
        ), None)
        if pending is None:
            return
        failure_id = getattr(failure, "failure_id", getattr(failure, "frontier_id", ""))
        failure_signature = getattr(failure, "failure_signature", failure_id)
        pending.update({
            "trial_patch_hash": trial.cumulative_diff.patch_hash,
            "cumulative_diff_hash": stable_id(
                "cumulative-diff", trial.cumulative_diff.canonical_diff,
            ),
            "changed_hunk_ids": tuple(
                hunk.hunk_id for hunk in trial.cumulative_diff.hunks
            ),
            "changed_symbols": trial.cumulative_diff.changed_symbols,
            "transition_decision": trial.decision.value,
            "transition_reasons": trial.transition_decision.reasons,
            "executed_classifications": tuple(
                execution.classification.value
                for execution in (trial.challenge_result.executions
                                  if trial.challenge_result else ())
            ),
            "rejection_reasons": (
                trial.transition_decision.reasons
                if trial.decision.value == "ROLLBACK" else ()
            ),
            "remaining_failure_signature": (
                None if failure_id in trial.evidence.confirmed_failures_closed
                else failure_signature
            ),
            "result_kind": (
                "REJECTED_BY_TRANSITION"
                if trial.decision.value == "ROLLBACK" else
                "PROMOTED_PROVISIONAL"
                if trial.decision.value == "KEEP_PROVISIONAL" else
                "COMMITTED"
            ),
        })
        if trial.decision.value != "ROLLBACK":
            return
        history = state.failure_history.setdefault(
            failure_signature, FailureHistory(failure_signature),
        )
        mechanism_record = {
            key: pending.get(key)
            for key in (
                "attempt_id", "source_patch_hash", "trial_patch_hash",
                "incremental_diff_hash", "cumulative_diff_hash",
                "changed_files", "changed_hunk_ids", "changed_symbols",
                "validation", "executed_classifications", "rejection_reasons",
                "remaining_failure_signature",
            )
        }
        mechanism_record["mechanism_id"] = stable_id(
            "failed-mechanism", mechanism_record,
        )
        if not any(
            item.get("mechanism_id") == mechanism_record["mechanism_id"]
            for item in history.mechanism_failures
        ):
            history.mechanism_failures.append(mechanism_record)

    def _record_repair_objective(
        self,
        state: ReachAvoidState,
        objective: RepairObjective,
    ) -> None:
        directory = state.run_root / "repair_objectives"
        directory.mkdir(parents=True, exist_ok=True)
        attempt = state.generator_attempt_count + 1
        destination = directory / f"attempt-{attempt:02d}-{objective.objective_id}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(canonical_json({
            "schema": "reachpatch-repair-objective-v1",
            "generator_attempt": attempt,
            "patch_hash": state.graph_stack.patch_hash,
            "graph_hashes": state.graph_stack.graph_hashes(),
            "objective": objective.to_dict(),
        }) + "\n", encoding="utf-8")
        temporary.replace(destination)

    def _record_controller_error(
        self,
        state: ReachAvoidState,
        *,
        stage: str,
        error: Exception,
    ) -> None:
        path = state.run_root / "controller_errors.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json({
                "phase": state.phase,
                "stage": stage,
                "error_kind": type(error).__name__,
                "error": str(error)[-4000:],
                "generator_attempt_count": state.generator_attempt_count,
                "repair_revision_count": state.repair_revision_count,
                "challenge_round_count": state.challenge_round_count,
            }) + "\n")

    def _record_initial_stage(
        self,
        state: ReachAvoidState,
        stage: str,
        *,
        patch_hash: str | None = None,
    ) -> None:
        path = state.run_root / "initial_generation_stages.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json({
                "stage": stage,
                "patch_hash": patch_hash,
                "generator_attempt_count": state.generator_attempt_count,
            }) + "\n")

    def run(
        self,
        instance: Instance,
        *,
        run_root: str | Path | None = None,
    ) -> TerminalResult:
        existing_contexts = set(self._contexts)
        try:
            return self._run_active(instance, run_root=run_root)
        finally:
            clear_execution_hot_cache()
            clear_program_graph_caches()
            for run_id in set(self._contexts) - existing_contexts:
                self._contexts.pop(run_id, None)

    def _run_active(
        self,
        instance: Instance,
        *,
        run_root: str | Path | None = None,
    ) -> TerminalResult:
        started = time.monotonic()
        state = self.initialize(instance, run_root=run_root)
        initial_deepseek = 0.0
        while True:
            deepseek_started = time.monotonic()
            try:
                self.generate_initial_patch(state)
            except RuntimeError as exc:
                initial_deepseek += time.monotonic() - deepseek_started
                self._record_controller_error(
                    state, stage="INITIAL_GENERATION", error=exc,
                )
                state.no_progress_generator_attempts += 1
                context = self._contexts[state.run_id]
                if context.initial_result is not None:
                    discard_ephemeral_tree(
                        context.initial_result.modified_tree, state.run_root,
                    )
                context.initial_result = None
                context.initial_tree = None
                context.initial_mechanical = None
                # A missing/empty p0 is not a terminal case.  Keep the same
                # bootstrap tree and retry the DeepSeek call; no final patch
                # may be emitted before a non-empty candidate exists.
                if state.no_progress_generator_attempts >= self.config.max_no_progress_generator_attempts:
                    # Exhausting the bounded initial-generation recovery budget
                    # is an external generator failure.  Seal the unchanged
                    # bootstrap snapshot without making a third model call.
                    # The bootstrap tree is deliberately retained for audit
                    # and for the terminal result's single current patch.
                    return self._output_single_patch(
                        state, state.working_checkpoint,
                        "GENERATOR_BLOCKED_EXTERNAL",
                    )
                discard_bootstrap_tree(
                    state.run_root / "initial_working", state.run_root,
                )
                continue
            initial_deepseek += time.monotonic() - deepseek_started
            state.no_progress_generator_attempts = 0
            break
        self.initialize_graph_stack(state)
        self._performance(state, PerformanceRecord(
            revision=0,
            program_update_seconds=latest_graph_metrics()["program_update_seconds"],
            requirement_update_seconds=latest_graph_metrics()["requirement_update_seconds"],
            binding_update_seconds=latest_graph_metrics()["binding_update_seconds"],
            challenge_materialization_seconds=latest_graph_metrics()["challenge_materialization_seconds"],
            challenge_execution_seconds=0.0,
            deepseek_seconds=initial_deepseek,
            peak_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            cache_hit_count=state.graph_stack.program_graph.cache_hits,
            files_reparsed=state.graph_stack.program_graph.files_reparsed,
            symbols_expanded=state.graph_stack.program_graph.symbols_expanded,
        ))
        # Mechanical failures are a first-class RepairFrontier.  They must
        # stay in the active loop so DeepSeek can repair the current patch.

        while state.repair_revision_count < self.config.max_real_patch_revisions:
            state.remaining_wall_seconds = max(
                0.0, state.execution_budget_seconds - (time.monotonic() - started),
            )
            if state.remaining_wall_seconds <= 0:
                return self.seal_best_effort(state, "FRONTIER_EXHAUSTED")
            reach = evaluate_reach(state)
            if reach.reached:
                return self.seal_reached(state)
            self._refresh_confirmed_failures(state)
            action = self._action_selection(state)
            if action.kind is NextActionKind.SEAL:
                return self.seal_best_effort(state, "FRONTIER_EXHAUSTED")
            if action.kind is NextActionKind.RUN_CHALLENGE:
                state.phase = ReachAvoidPhase.CHALLENGE
                selection = ChallengeSelection((action.challenge_id,), ())
                state.challenge_round_count += 1
                if selection.challenge_ids:
                    result = execute_challenge_round(
                        state, selection, state.base_repository,
                        Path(state.working_checkpoint.snapshot_tree),
                    )
                    self._apply_challenge_result(state, result)
                    challenge_seconds = result.execution_seconds
                    cache_hits = result.cache_hits
                else:
                    self._expand_binding_frontier(state, selection)
                    challenge_seconds = 0.0
                    cache_hits = state.graph_stack.program_graph.cache_hits
                self._performance(state, PerformanceRecord(
                    revision=state.repair_revision_count,
                    program_update_seconds=latest_graph_metrics()["program_update_seconds"],
                    requirement_update_seconds=latest_graph_metrics()["requirement_update_seconds"],
                    binding_update_seconds=latest_graph_metrics()["binding_update_seconds"],
                    challenge_materialization_seconds=latest_graph_metrics()["challenge_materialization_seconds"],
                    challenge_execution_seconds=challenge_seconds,
                    deepseek_seconds=0.0,
                    peak_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    cache_hit_count=cache_hits,
                    files_reparsed=state.graph_stack.program_graph.files_reparsed,
                    symbols_expanded=state.graph_stack.program_graph.symbols_expanded,
                ))
                continue
            if action.kind is NextActionKind.RECOVER_EVIDENCE:
                state.phase = ReachAvoidPhase.CHALLENGE
                frontier = state.repair_frontiers.get(action.frontier_id or "")
                if frontier is not None:
                    requirement_id = next(iter(frontier.requirement_ids), "")
                    selection = ChallengeSelection(
                        (), ((requirement_id, "EXPAND_DIRECT_CALLER"),),
                    )
                    self._expand_binding_frontier(state, selection)
                    state.challenge_round_count += 1
                    continue
                continue

            state.phase = ReachAvoidPhase.REPAIR
            frontier = state.repair_frontiers.get(action.frontier_id or "")
            if frontier is None:
                continue
            objective = compile_repair_objective(state, frontier)
            state.current_repair_objective = objective
            self._record_repair_objective(state, objective)
            deepseek_started = time.monotonic()
            generator_result = self.repair_player.revise_working_patch(state, objective)
            deepseek_seconds = time.monotonic() - deepseek_started
            state.generator_attempt_count += 1
            self._record_generator_attempt(
                state, generator_result, objective_kind=objective.objective_kind,
                initial=False, duration_seconds=deepseek_seconds,
            )
            if not generator_result.has_new_nonempty_diff:
                discard_ephemeral_tree(generator_result.modified_tree, state.run_root)
                state.no_progress_generator_attempts += 1
                if state.no_progress_generator_attempts >= self.config.max_no_progress_generator_attempts:
                    # A nonempty, mechanically valid working patch already exists.
                    # Repeating a rejected/no-op revision exhausts this repair
                    # frontier; it is not an external generator outage. Seal the
                    # evidence-ranked checkpoint rather than discarding the patch.
                    return self.seal_best_effort(state, "FRONTIER_EXHAUSTED")
                continue
            state.phase = ReachAvoidPhase.TRANSITION
            trial = evaluate_trial_transition(state, generator_result)
            if not trial.trial_patch_changed or not trial.entered_evaluation:
                discard_ephemeral_tree(trial.trial_tree, state.run_root)
                discard_ephemeral_tree(generator_result.modified_tree, state.run_root)
                state.no_progress_generator_attempts += 1
                if state.no_progress_generator_attempts >= self.config.max_no_progress_generator_attempts:
                    return self.seal_best_effort(state, "FRONTIER_EXHAUSTED")
                continue
            if trial.challenge_result is not None:
                state.challenge_round_count += 1
            state.no_progress_generator_attempts = 0
            state.repair_revision_count += 1
            self._finalize_generator_attempt(state, frontier, trial)
            apply_transition_decision(state, trial, self._contexts[state.run_id].store)
            state.last_mechanical_result = (
                None if trial.decision.value == "ROLLBACK" else trial.evidence.mechanical
            )
            self._refresh_repair_frontiers(state)
            trial_program = trial.graph_stack.program_graph
            self._performance(state, PerformanceRecord(
                revision=state.repair_revision_count,
                program_update_seconds=latest_graph_metrics()["program_update_seconds"],
                requirement_update_seconds=latest_graph_metrics()["requirement_update_seconds"],
                binding_update_seconds=latest_graph_metrics()["binding_update_seconds"],
                challenge_materialization_seconds=latest_graph_metrics()["challenge_materialization_seconds"],
                challenge_execution_seconds=(
                    trial.challenge_result.execution_seconds if trial.challenge_result else 0.0
                ),
                deepseek_seconds=deepseek_seconds,
                peak_rss_kb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                cache_hit_count=trial_program.cache_hits,
                files_reparsed=trial_program.files_reparsed,
                symbols_expanded=trial_program.symbols_expanded,
            ))
            discard_ephemeral_tree(trial.trial_tree, state.run_root)
            discard_ephemeral_tree(generator_result.modified_tree, state.run_root)
            del trial

        if evaluate_reach(state).reached:
            return self.seal_reached(state)
        return self.seal_best_effort(state, "REVISION_LIMIT")

    def _seal_current(self, state: ReachAvoidState, status: str) -> TerminalResult:
        # Final output is always the single currently accepted working patch.
        # Historical checkpoints are audit/recovery artifacts, never a selector.
        checkpoint = state.working_checkpoint
        state.termination_status = status
        return self._output_single_patch(state, checkpoint, status)

    def seal_reached(self, state: ReachAvoidState) -> TerminalResult:
        if not evaluate_reach(state).reached:
            raise RuntimeError("cannot seal a state that has not reached")
        state.certified_checkpoint = state.working_checkpoint
        state.termination_status = "REACHED"
        return self._output_single_patch(state, state.working_checkpoint, "REACHED")

    def seal_best_effort(self, state: ReachAvoidState, reason: str) -> TerminalResult:
        checkpoint = state.working_checkpoint
        status = f"BEST_EFFORT_{reason}"
        state.termination_status = status
        return self._output_single_patch(state, checkpoint, status)

    def _output_single_patch(
        self,
        state: ReachAvoidState,
        checkpoint: StateCheckpoint,
        status: str,
    ) -> TerminalResult:
        state.phase = ReachAvoidPhase.SEALED
        output = state.run_root / "final.patch"
        output.write_text(checkpoint.canonical_diff, encoding="utf-8")
        result = TerminalResult(
            instance_id=state.instance_id,
            run_id=state.run_id,
            status=status,
            checkpoint_id=checkpoint.checkpoint_id,
            patch_hash=checkpoint.patch_hash,
            unified_diff=checkpoint.canonical_diff,
            output_path=str(output),
        )
        (state.run_root / "terminal.json").write_text(
            canonical_json({"schema": "reachpatch-reach-avoid-v2", "result": result.to_dict()}) + "\n",
            encoding="utf-8",
        )
        return result
