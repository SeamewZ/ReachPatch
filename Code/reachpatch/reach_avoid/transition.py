from __future__ import annotations

import copy
import os
import platform
import sys
import time
from dataclasses import asdict
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from reachpatch.binding_graph import build_active_binding_graph, build_executable_bindings
from reachpatch.challenge_graph.dicc import (
    compile_executable_challenge_evidence,
    diff_induced_challenge_plan,
    evaluate_dicc,
    finalize_diff_induced_challenge_closure,
)
from reachpatch.challenge_graph.materialize import (
    execute_challenges, materialize_active_challenges,
)
from reachpatch.challenge_graph.models import ChallengeGraph
from reachpatch.execution import (
    CheckClassification,
    CheckComparison,
    TraceExecutor,
    WorktreeManager,
    select_project_runner,
)
from reachpatch.execution.mechanical import mechanical_pass, run_mechanical_checks
from reachpatch.execution.reconcile import ActualDiff, reconcile_actual_diff
from reachpatch.execution.worktree import tree_hash
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.budget import BudgetVector
from reachpatch.models.controller import (
    CounterexamplePacket,
    IncumbentCheckpoint,
    MechanismAttempt,
    ReachAvoidState,
    TerminalCertificate,
    TransitionCertificate,
    TransitionResult,
    WorkingPatch,
)
from reachpatch.models.core import Frontier
from reachpatch.models.enums import ChallengeTerminalStatus, ControllerPhase, Decision, OutcomeStatus
from reachpatch.oracle.discriminator import (
    discriminator_probe_from_dict,
    enqueue_discriminator_probes,
)
from reachpatch.program_graph.budget import Deadline, GraphBudget
from reachpatch.program_graph import (
    build_diff_impact_slice, causal_repair_cut,
)
from reachpatch.program_graph.incremental import update_active_program_slice
from reachpatch.program_graph.index import update_repository_index
from reachpatch.program_graph.tracing import merge_trace_bundles
from reachpatch.program_graph.impact import guarded_diff_influence_cone
from reachpatch.reach_avoid.certificates import finalize_certificate
from reachpatch.reach_avoid.gates import in_target_set, raw_avoid_reasons
from reachpatch.reach_avoid.metrics import (
    RevisionEvidence, component_shadow_pass, progress_metrics,
    progress_vector_from_comparisons, should_commit,
)
from reachpatch.reach_avoid.state import outcomes_from_challenges
from reachpatch.repair.counterexamples import (
    counterexample_from_check_comparison, packets_for_nonpass_challenges,
)
from reachpatch.repair.diagnosis import mechanism_fingerprint
from reachpatch.repair.operators import apply_registered_operator
from reachpatch.repair.session import PersistentGeneratorSession
from reachpatch.repair.deepseek_agent import GeneratorRevision, convert_revision_action, ActionConversionStatus
from reachpatch.requirement_graph import (
    compile_requirement_paths, promote_domains_from_diff,
    refresh_requirement_paths,
)


def _enqueue_pending_discriminators(
    state: ReachAvoidState,
    graph: ChallengeGraph,
) -> dict[str, tuple[str, ...]]:
    probes = tuple(
        discriminator_probe_from_dict(raw)
        for raw in state.runtime_metrics.get("discriminator_probes", ())
    )
    return enqueue_discriminator_probes(
        graph,
        probes,
        completed_probe_ids=state.runtime_metrics.get(
            "executed_discriminator_probe_ids", ()
        ),
    )


def _executed_discriminator_probe_ids(
    graph: ChallengeGraph,
    executed_challenge_ids: Iterable[str],
) -> set[str]:
    return {
        str(probe_id)
        for challenge_id in executed_challenge_ids
        if challenge_id in graph.cells
        for probe_id in graph.cells[challenge_id].diff_dependency.get(
            "discriminator_probe_ids", ()
        )
    }


def _reset_for_trial(graph: ChallengeGraph) -> ChallengeGraph:
    trial = copy.deepcopy(graph)
    trial.diff_hash = "BASELINE"
    for challenge_id, cell in list(trial.cells.items()):
        if cell.scenario_id and cell.trigger_recipe_id:
            trial.cells[challenge_id] = replace(
                cell,
                baseline_outcome=None,
                patched_outcome=None,
                stability_status="NOT_EXECUTED",
                terminal_status=ChallengeTerminalStatus.PENDING,
                execution_bundle_id=None,
            )
    return trial


def _causal_touch(
    state: ReachAvoidState,
    actual_diff: ActualDiff,
) -> dict[str, list[str]]:
    touched_files = set(actual_diff.changed_files)
    relations_by_file = {
        relative: [item.relation_id for item in actual_diff.changed_relations if item.file == relative]
        for relative in touched_files
    }
    witnesses: dict[str, list[str]] = {}
    for unit in state.binding_graph.units.values():
        cut_files = {
            str(state.program_graph.nodes[node_id].attributes.get("file", ""))
            for node_id in unit.repair_cut_node_ids
            if node_id in state.program_graph.nodes
        }
        touched = sorted({
            relation_id
            for relative in cut_files & touched_files
            for relation_id in relations_by_file.get(relative, [])
        })
        if touched:
            witnesses[unit.path_obligation_id] = touched
    return witnesses


def _current_pairs(outcomes) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        (item.path_obligation_id, item.scenario_id or "") for item in outcomes
    ))


def _environment_hash() -> str:
    return content_hash({
        "python": sys.version,
        "platform": platform.platform(),
        "implementation": platform.python_implementation(),
    })


def decide_execution_transition(previous, trial: RevisionEvidence) -> Decision:
    if trial.new_regressions > 0 or (
        not trial.safe and not trial.environment_blocked
    ):
        return Decision.ROLLBACK
    if trial.environment_blocked:
        return Decision.KEEP_UNCERTIFIED
    if should_commit(previous, trial):
        return Decision.COMMIT
    return Decision.ROLLBACK


def _changed_nodes_for_diff(graph, actual_diff: ActualDiff) -> set[str]:
    changed: set[str] = set()
    for hunk in actual_diff.hunks:
        lower = hunk.new_start
        upper = hunk.new_start + max(hunk.new_count, 1) - 1
        for node_id in graph.file_index.get(hunk.file, ()):
            node = graph.nodes[node_id]
            line = int(node.attributes.get("line", -1))
            end = int(node.attributes.get("end_line", line))
            if line <= upper and end >= lower:
                changed.add(node_id)
    return changed


def _charge_execution(state: ReachAvoidState, seconds: float) -> None:
    charge = BudgetVector(execution_seconds=seconds, wall_seconds=seconds)
    try:
        state.remaining_budget = state.remaining_budget.subtract(charge)
    except Exception:
        state.remaining_budget.execution_seconds = 0.0
        state.remaining_budget.wall_seconds = 0.0


def _mechanical_packet(
    state: ReachAvoidState,
    transition_id: str,
    actual_diff: ActualDiff,
    reason: str,
) -> CounterexamplePacket:
    return CounterexamplePacket(
        counterexample_id=stable_id("counterexample", transition_id, reason, actual_diff.diff_id),
        transition_id=transition_id,
        path_obligation_id=None,
        binding_unit_id=None,
        challenge_id=None,
        public_trigger_id=None,
        entrypoint_id=None,
        guarded_path_edge_ids=(),
        exit_kind=None,
        trusted_oracle_id=None,
        expected_observation={"mechanical": "PASS"},
        actual_observation={"reason": reason},
        minimal_input={},
        reproduction_recipe_id=None,
        raw_execution_ids=(),
        relevant_source_slice_ids=(),
        causal_touch_witness_ids=(),
        candidate_repair_cut_ids=(),
        protected_sibling_path_ids=tuple(sorted(
            item.path_obligation_id for item in state.outcomes.values()
            if item.status == OutcomeStatus.PASS
        )),
        preservation_path_ids=(),
        forbidden_behavior_ids=(),
        source_hash=state.checkpoint.patch.working_tree_hash,
        diff_hash=actual_diff.canonical_diff_hash,
        failure_origin="PATCH_MECHANICAL",
        frontier_kind=reason,
        uncertain_information=(),
        mechanism_fingerprint_hash=str(actual_diff.fingerprint.get("hash")),
    )


def _build_checkpoint(
    state: ReachAvoidState,
    checkpoint_id: str,
    snapshot_tree: str,
    cumulative: ActualDiff,
    outcomes,
    transition_id: str,
    generator_cursor: int,
    *,
    safe: bool = False,
    executed_target_deficit: float | None = None,
    patch_status: str = "WORKING_UNCERTIFIED",
) -> IncumbentCheckpoint:
    passed = [item for item in outcomes.values() if item.status == OutcomeStatus.PASS]
    failed = [item for item in outcomes.values() if item.status == OutcomeStatus.FAIL]
    unknown = [
        item for item in outcomes.values()
        if item.status not in {OutcomeStatus.PASS, OutcomeStatus.FAIL}
    ]
    patch = WorkingPatch(
        version=state.checkpoint.patch.version + 1,
        base_commit=state.base_commit,
        canonical_diff=cumulative.canonical_diff,
        canonical_diff_hash=cumulative.canonical_diff_hash,
        base_tree_hash=cumulative.base_tree_hash,
        working_tree_hash=cumulative.trial_tree_hash,
        parent_patch_hash=state.checkpoint.patch.canonical_diff_hash,
        checkpoint_id=checkpoint_id,
        status=patch_status,
    )
    return IncumbentCheckpoint(
        checkpoint_id=checkpoint_id,
        parent_checkpoint_id=state.checkpoint.checkpoint_id,
        episode_id=state.episode_id,
        assignment_id=state.assignment.assignment_id,
        base_commit=state.base_commit,
        snapshot_tree=snapshot_tree,
        patch=patch,
        actual_fingerprint=cumulative.fingerprint,
        graph_hashes={},
        environment_hash=_environment_hash(),
        pass_pairs=_current_pairs(passed),
        fail_pairs=_current_pairs(failed),
        unknown_pairs=_current_pairs(unknown),
        blocked_path_obligation_ids=tuple(sorted({
            item.path_obligation_id for item in unknown
        })),
        executed_target_deficit=(
            float(executed_target_deficit)
            if executed_target_deficit is not None
            else float(len(getattr(
                getattr(state, "target_recovery", None), "targets", ()
            )))
        ),
        accepted_transition_id=transition_id,
        generator_session_cursor=str(generator_cursor),
        remaining_budget=state.remaining_budget,
        safe=safe,
        graph_reached=False,
    )


def _legacy_evaluate_single_update(
    state: ReachAvoidState,
    session: PersistentGeneratorSession,
    intent,
    *,
    forbidden_patterns: Iterable[str] = (),
    mechanical_commands: Iterable[Iterable[str]] = (),
) -> TransitionResult | None:
    """Read old repair-intent artifacts; patch-first production never calls this."""

    if os.environ.get("REACHPATCH_ENABLE_LEGACY_FULL_GRAPH") != "1":
        raise RuntimeError("legacy full-graph transition is disabled")
    from reachpatch.binding_graph import build_binding_graph
    from reachpatch.challenge_graph.materialize import materialize_challenges
    from reachpatch.program_graph.builder import build_augmented_program_graph
    from reachpatch.requirement_graph import compile_assignment_overlay
    state.transition_phase(
        ControllerPhase.GENERATOR_REVISE, event="repair_intent_submitted"
    )
    action = session.propose_action(state, intent)
    state.generator_session = session.record
    if action is None:
        return None
    transition_id = stable_id(
        "transition", state.checkpoint.checkpoint_id, intent.intent_id,
        state.transition_index + 1,
    )
    session.record_submission(transition_id)
    manager = WorktreeManager(Path(state.run_root) / "worktrees")
    trial = manager.begin_trial(state.checkpoint.checkpoint_id)
    checkpoint_tree = Path(state.checkpoint.snapshot_tree)
    state.transition_phase(
        ControllerPhase.DIFF_RECONCILE, event="transactional_trial_started"
    )
    try:
        application = apply_registered_operator(
            action,
            checkpoint_tree,
            trial.tree,
            state.program_graph,
            forbidden_patterns=forbidden_patterns,
        )
        incremental = application.actual_diff
    except Exception as exc:
        incremental = reconcile_actual_diff(
            checkpoint_tree, trial.tree, forbidden_patterns=forbidden_patterns
        )
        receipt = manager.rollback(trial)
        packet = _mechanical_packet(state, transition_id, incremental, f"EDIT_APPLY:{exc}")
        certificate = finalize_certificate(TransitionCertificate(
            transition_id=transition_id,
            update_id=incremental.diff_id,
            source_checkpoint_id=state.checkpoint.checkpoint_id,
            result_checkpoint_id=None,
            incremental_diff_hash=incremental.canonical_diff_hash,
            cumulative_diff_hash=state.checkpoint.patch.canonical_diff_hash,
            actual_edit_ids=tuple(edit.edit_id for edit in action.edit_intents),
            causal_cut_ids=action.causal_cut_ids,
            graph_delta={},
            mechanical_check_ids=(),
            outcome_ids=(),
            new_counterexample_ids=(packet.counterexample_id,),
            eliminated_counterexample_ids=(),
            impact_regression_ids=(),
            adjacent_partition_obligation_ids=(),
            hard_frontier_ids=(),
            old_target_deficit=state.target_deficit(),
            new_target_deficit=state.target_deficit(),
            repaired_losing_path_ids=(),
            mechanical_pass=False,
            forbidden_edit=bool(incremental.forbidden_paths),
            oracle_contamination=bool(incremental.oracle_contamination_paths),
            established_successes_pass=False,
            preservation_pass=False,
            component_shadow_pass=False,
            diff_safety_pass=False,
            safe=False,
            strict_target_progress=False,
            reach=False,
            avoid=True,
            progress=False,
            decision=Decision.ROLLBACK,
            restoration_or_commit_receipt=receipt.receipt_id,
            input_artifact_ids=(),
            recomputation_hash="",
        ))
        state.counterexamples.append(packet)
        state.repair_history.append(certificate)
        state.transition_index += 1
        session.resume(state.checkpoint.checkpoint_id, (packet.counterexample_id,))
        state.generator_session = session.record
        state.transition_phase(
            ControllerPhase.COUNTEREXAMPLE_FEEDBACK, event="edit_apply_failed"
        )
        return TransitionResult(
            transition_id, False, Decision.ROLLBACK, certificate,
            (packet,), state.checkpoint, action, "EDIT_APPLY_FAILURE",
        )
    checks = run_mechanical_checks(
        trial.tree,
        incremental,
        commands=mechanical_commands,
        baseline_root=checkpoint_tree,
    )
    state.transition_phase(
        ControllerPhase.DICC_VALIDATE, event="actual_diff_reconciled"
    )
    trial_program = build_augmented_program_graph(trial.tree)
    trial_challenges = _reset_for_trial(state.challenge_graph)
    plan = diff_induced_challenge_plan(
        state.requirement_graph,
        state.program_graph,
        trial_program,
        state.binding_graph,
        trial_challenges,
        incremental,
        update_id=incremental.diff_id,
    )
    executor = TraceExecutor(temporary_root=Path(state.run_root) / "tmp")
    bundles = execute_challenges(
        trial_challenges,
        executor,
        state.base_repository,
        trial.tree,
    )
    # Dynamic observations refine only the trial graph.  They are consumed by
    # the accepted incremental rebuild below; a rejected trial never mutates
    # the incumbent graph.
    baseline_trace_delta = merge_trace_bundles(
        trial_program, bundles, role="BASELINE"
    )
    patched_trace_delta = merge_trace_bundles(
        trial_program, bundles, role="PATCH"
    )
    changed_nodes = _changed_nodes_for_diff(trial_program, incremental)
    try:
        diff_impact_cone = guarded_diff_influence_cone(
            state.program_graph, trial_program, changed_nodes
        ) if changed_nodes else None
    except (KeyError, ValueError):
        diff_impact_cone = None
    bundle_map = {item.paired_bundle_id: item for item in bundles}
    state.trace_bundles.update(bundle_map)
    validation_outcomes = outcomes_from_challenges(state, trial_challenges, bundles)
    causal_touch = _causal_touch(state, incremental)
    impact_nodes = set(diff_impact_cone.downstream_node_ids) if diff_impact_cone else set()
    impact_path_ids = {
        unit.path_obligation_id
        for unit in state.binding_graph.units.values()
        if impact_nodes & (set(unit.interaction_path_ids) | set(unit.preservation_node_ids))
    }
    closure = finalize_diff_induced_challenge_closure(
        plan,
        trial_challenges,
        checkpoint_id=state.checkpoint.checkpoint_id,
        transition_index=state.transition_index + 1,
        causal_touch_witnesses=causal_touch,
    )
    progress = progress_metrics(
        state,
        validation_outcomes,
        causal_touch,
        new_requirement_graph=state.requirement_graph,
        new_program_graph=trial_program,
        new_binding_graph=state.binding_graph,
        new_challenge_graph=trial_challenges,
    )
    shadow_pass = component_shadow_pass(intent, validation_outcomes)
    state.transition_phase(
        ControllerPhase.TRANSITION_GATE, event="diff_challenge_closure_computed"
    )
    established_ids = {
        item.challenge_id for item in state.outcomes.values()
        if item.status == OutcomeStatus.PASS and item.challenge_id
    }
    avoid_reasons = raw_avoid_reasons(
        state,
        validation_outcomes,
        checks,
        forbidden_edit=bool(incremental.forbidden_paths),
        oracle_contamination=bool(incremental.oracle_contamination_paths),
        diff_safety_pass=closure.commit_safety_closed,
    )
    if not shadow_pass:
        avoid_reasons = tuple(sorted(set(avoid_reasons) | {"COMPONENT_SHADOW_NOT_CLOSED"}))
    safe = not avoid_reasons
    accepted = safe and bool(progress["strict_target_progress"])
    packets = packets_for_nonpass_challenges(
        state,
        trial_challenges,
        incremental,
        transition_id=transition_id,
        executor=executor,
        base_repository=state.base_repository,
        patch_repository=trial.tree,
    )
    execution_seconds = sum(
        run.duration_seconds
        for bundle in bundles
        for trace_bundle in (bundle.base_bundle, bundle.patch_bundle)
        for run in trace_bundle.runs
    )
    _charge_execution(state, execution_seconds)
    cumulative = reconcile_actual_diff(
        state.base_repository,
        trial.tree,
        forbidden_patterns=forbidden_patterns,
    )
    result_checkpoint_id = None
    if accepted:
        result_checkpoint_id = stable_id(
            "checkpoint", state.episode_id, cumulative.canonical_diff_hash,
            state.transition_index + 1,
        )
        receipt = manager.commit(trial, result_checkpoint_id)
        snapshot = manager.checkpoint_tree(result_checkpoint_id)
        accepted_requirements = compile_assignment_overlay(
            state.semantic_graph, state.assignment
        )
        compile_requirement_paths(accepted_requirements, trial_program)
        accepted_binding = build_binding_graph(accepted_requirements, trial_program)
        accepted_challenges = materialize_challenges(
            accepted_requirements,
            trial_program,
            accepted_binding,
            diff_hash=cumulative.canonical_diff_hash,
        )
        accepted_bundles = execute_challenges(
            accepted_challenges,
            executor,
            state.base_repository,
            snapshot,
        )
        old_requirement = state.requirement_graph
        old_binding = state.binding_graph
        state.requirement_graph = accepted_requirements
        state.program_graph = trial_program
        state.binding_graph = accepted_binding
        state.challenge_graph = accepted_challenges
        accepted_outcomes = outcomes_from_challenges(
            state, accepted_challenges, accepted_bundles
        )
        checkpoint = _build_checkpoint(
            state,
            result_checkpoint_id,
            str(snapshot),
            cumulative,
            accepted_outcomes,
            transition_id,
            session.record.cursor,
        )
        checkpoint = replace(
            checkpoint,
            graph_hashes=state.graph_hashes(),
            executed_target_deficit=sum(
                state.requirement_graph.leaves[unit.leaf_id].weight
                for unit in state.binding_graph.units.values()
                if any(
                    outcome.unit_id == unit.unit_id and outcome.status != OutcomeStatus.PASS
                    for outcome in accepted_outcomes.values()
                )
            ),
        )
        state.checkpoint = checkpoint
        state.outcomes = accepted_outcomes
        state.trace_bundles.update({
            item.paired_bundle_id: item for item in accepted_bundles
        })
        graph_delta = {
            "old_requirement": old_requirement.semantic_layer_hash(),
            "new_requirement": accepted_requirements.semantic_layer_hash(),
            "old_binding": old_binding.graph_hash(),
            "new_binding": accepted_binding.graph_hash(),
            "program_version": trial_program.version,
            "dynamic_trace": {
                "baseline": baseline_trace_delta,
                "patched": patched_trace_delta,
            },
            "diff_impact_cone": diff_impact_cone.to_dict() if diff_impact_cone else None,
        }
    else:
        receipt = manager.rollback(trial)
        checkpoint = state.checkpoint
        graph_delta = {
            "trial_program_hash": trial_program.program_hash(),
            "committed": False,
            "dynamic_trace": {
                "baseline": baseline_trace_delta,
                "patched": patched_trace_delta,
            },
            "diff_impact_cone": diff_impact_cone.to_dict() if diff_impact_cone else None,
        }
    state.diff_closure_certificates.append(closure)
    validation_by_challenge = {
        item.challenge_id: item for item in validation_outcomes.values() if item.challenge_id
    }
    established_pass = all(
        challenge_id in validation_by_challenge
        and validation_by_challenge[challenge_id].status == OutcomeStatus.PASS
        for challenge_id in established_ids
    )
    preservation_pass = all(
        item.status == OutcomeStatus.PASS
        for item in validation_outcomes.values() if item.kind == "PRESERVATION"
    )
    certificate = finalize_certificate(TransitionCertificate(
        transition_id=transition_id,
        update_id=incremental.diff_id,
        source_checkpoint_id=intent.source_checkpoint_id,
        result_checkpoint_id=result_checkpoint_id,
        incremental_diff_hash=incremental.canonical_diff_hash,
        cumulative_diff_hash=(
            cumulative.canonical_diff_hash if accepted
            else state.checkpoint.patch.canonical_diff_hash
        ),
        actual_edit_ids=tuple(edit.edit_id for edit in action.edit_intents),
        causal_cut_ids=action.causal_cut_ids,
        graph_delta=graph_delta,
        mechanical_check_ids=(
            tuple(item.check_id for item in checks)
            + tuple(
                str(item.get("check_id"))
                for item in graph_delta.get("public_check_comparisons", ())
                if item.get("check_id")
            )
        ),
        outcome_ids=tuple(sorted(validation_outcomes)),
        new_counterexample_ids=tuple(item.counterexample_id for item in packets),
        eliminated_counterexample_ids=tuple(sorted({
            item.counterexample_id for item in state.counterexamples
            if item.path_obligation_id in progress["repaired_losing_path_ids"]
        })),
        impact_regression_ids=tuple(
            item.outcome_id for item in validation_outcomes.values()
            if item.status != OutcomeStatus.PASS
            and (
                item.kind == "PRESERVATION"
                or item.path_obligation_id in impact_path_ids
            )
        ),
        adjacent_partition_obligation_ids=plan.overlay_obligation_ids,
        hard_frontier_ids=closure.hard_frontier_ids,
        old_target_deficit=float(progress["old_target_deficit"]),
        new_target_deficit=float(progress["new_target_deficit"]),
        repaired_losing_path_ids=tuple(progress["repaired_losing_path_ids"]),
        mechanical_pass=mechanical_pass(checks),
        forbidden_edit=bool(incremental.forbidden_paths),
        oracle_contamination=bool(incremental.oracle_contamination_paths),
        established_successes_pass=established_pass,
        preservation_pass=preservation_pass,
        component_shadow_pass=shadow_pass,
        diff_safety_pass=closure.commit_safety_closed,
        safe=safe,
        strict_target_progress=bool(progress["strict_target_progress"]),
        reach=accepted and in_target_set(state),
        avoid=bool(avoid_reasons),
        progress=bool(progress["strict_target_progress"]),
        decision=Decision.COMMIT if accepted else Decision.ROLLBACK,
        restoration_or_commit_receipt=receipt.receipt_id,
        input_artifact_ids=tuple(
            identifiers[-1]
            for identifiers in state.artifact_ids.values() if identifiers
        ),
        recomputation_hash="",
    ))
    fingerprint = mechanism_fingerprint(incremental)
    attempts = state.mechanism_memory.setdefault(intent.losing_core_id, [])
    equivalent = sum(
        item.fingerprint_hash == fingerprint["fingerprint_hash"] for item in attempts
    ) + 1
    attempts.append(MechanismAttempt(
        component_id=intent.component_id,
        losing_core_id=intent.losing_core_id,
        mechanism_class=intent.root_mechanism_class,
        fingerprint_hash=str(fingerprint["fingerprint_hash"]),
        result="COMMIT" if accepted else "ROLLBACK",
        causal_cut_ids=action.causal_cut_ids,
        failure_observation_hash=content_hash([
            item.actual_observation for item in packets
        ]),
        transition_id=transition_id,
        equivalent_attempt_count=equivalent,
        forbidden_next=not accepted and equivalent >= 2,
    ))
    state.counterexamples.extend(packets)
    state.repair_history.append(certificate)
    state.transition_index += 1
    session.resume(
        state.checkpoint.checkpoint_id,
        tuple(item.counterexample_id for item in packets),
    )
    state.generator_session = session.record
    state.transition_phase(
        ControllerPhase.COUNTEREXAMPLE_FEEDBACK,
        event="transition_committed" if accepted else "transition_rolled_back",
    )
    state.refresh_id()
    reason = "SAFE_STRICT_PROGRESS" if accepted else (
        ",".join(avoid_reasons) if avoid_reasons else "NO_STRICT_TARGET_PROGRESS"
    )
    return TransitionResult(
        transition_id=transition_id,
        accepted=accepted,
        decision=Decision.COMMIT if accepted else Decision.ROLLBACK,
        certificate=certificate,
        counterexamples=packets,
        checkpoint=state.checkpoint,
        action=action,
        reason=reason,
    )


def _apply_revision_edits(tree: Path, revision: GeneratorRevision) -> None:
    by_file: dict[str, list] = {}
    prepared: dict[str, list[str]] = {}
    for edit in revision.edits:
        path = (tree / edit.relative_path).resolve()
        if not path.is_relative_to(tree.resolve()) or not path.is_file():
            raise ValueError(f"invalid revision path: {edit.relative_path}")
        lines = prepared.setdefault(
            edit.relative_path,
            path.read_text(encoding="utf-8", errors="strict").splitlines(),
        )
        actual = "\n".join(lines[edit.start_line - 1:edit.end_line])
        if actual != edit.expected_source.rstrip("\n"):
            raise ValueError(
                f"expected source mismatch: {edit.relative_path}:{edit.start_line}"
            )
        by_file.setdefault(edit.relative_path, []).append(edit)
    for relative, edits in by_file.items():
        ordered = sorted(edits, key=lambda item: (item.start_line, item.end_line), reverse=True)
        previous_start = len(prepared[relative]) + 1
        for edit in ordered:
            if edit.end_line >= previous_start:
                raise ValueError(f"overlapping edits in {relative}")
            previous_start = edit.start_line
            replacement = edit.replacement.splitlines()
            prepared[relative][edit.start_line - 1:edit.end_line] = replacement
        original = (tree / relative).read_text(encoding="utf-8", errors="strict")
        trailing = "\n" if original.endswith("\n") else ""
        (tree / relative).write_text(
            "\n".join(prepared[relative]) + trailing, encoding="utf-8"
        )


def _revision_certificate(
    state: ReachAvoidState,
    revision: GeneratorRevision,
    transition_id: str,
    incremental: ActualDiff,
    checks,
    *,
    decision: Decision,
    receipt_id: str,
    result_checkpoint_id: str | None,
    graph_delta: dict,
    outcomes: dict,
    packets: tuple[CounterexamplePacket, ...],
    safe: bool,
    progress: bool,
    reach: bool,
    avoid_reasons: tuple[str, ...],
) -> TransitionCertificate:
    failed = [item for item in outcomes.values() if item.status == OutcomeStatus.FAIL]
    preservation_pass = not any(
        item.kind == "PRESERVATION" and item.status == OutcomeStatus.FAIL and item.stable
        for item in outcomes.values()
    )
    established_pass = not any(
        old.status == OutcomeStatus.PASS
        and any(
            current.unit_id == old.unit_id and current.status == OutcomeStatus.FAIL
            for current in outcomes.values()
        )
        for old in state.outcomes.values()
    )
    old_target_deficit = float(
        graph_delta.get("old_target_deficit", state.target_deficit())
    )
    comparison_rows = tuple(graph_delta.get("public_check_comparisons", ()))
    if comparison_rows and state.target_recovery is not None:
        target_ids = {
            item.check_id for item in state.target_recovery.targets
        }
        classifications = {
            str(item.get("check_id", "")): str(item.get("classification", ""))
            for item in comparison_rows
        }
        new_target_deficit = float(sum(
            classifications.get(check_id) != CheckClassification.TARGET_FIXED.value
            for check_id in target_ids
        ))
    else:
        new_target_deficit = old_target_deficit
    return finalize_certificate(TransitionCertificate(
        transition_id=transition_id, update_id=incremental.diff_id,
        source_checkpoint_id=state.checkpoint.checkpoint_id,
        result_checkpoint_id=result_checkpoint_id,
        incremental_diff_hash=incremental.canonical_diff_hash,
        cumulative_diff_hash=(
            graph_delta.get("cumulative_diff_hash")
            or state.checkpoint.patch.canonical_diff_hash
        ),
        actual_edit_ids=tuple(stable_id("proposed-edit", asdict(item)) for item in revision.edits),
        causal_cut_ids=tuple(sorted({
            node_id for unit in state.binding_graph.units.values()
            for node_id in unit.repair_cut_node_ids
            if any(
                state.program_graph.nodes.get(node_id)
                and state.program_graph.nodes[node_id].attributes.get("file") == edit.relative_path
                for edit in revision.edits
            )
        })),
        graph_delta=graph_delta,
        mechanical_check_ids=tuple(item.check_id for item in checks),
        outcome_ids=tuple(sorted(outcomes)),
        new_counterexample_ids=tuple(item.counterexample_id for item in packets),
        eliminated_counterexample_ids=(),
        impact_regression_ids=tuple(item.outcome_id for item in failed if item.kind == "PRESERVATION"),
        adjacent_partition_obligation_ids=tuple(graph_delta.get("active_challenge_ids", ())),
        hard_frontier_ids=tuple(graph_delta.get("hard_frontier_ids", ())),
        old_target_deficit=old_target_deficit,
        new_target_deficit=new_target_deficit,
        repaired_losing_path_ids=tuple(graph_delta.get("repaired_path_ids", ())),
        mechanical_pass=mechanical_pass(checks),
        forbidden_edit=bool(incremental.forbidden_paths),
        oracle_contamination=bool(incremental.oracle_contamination_paths),
        established_successes_pass=established_pass,
        preservation_pass=preservation_pass,
        component_shadow_pass=True,
        diff_safety_pass=bool(graph_delta.get("diff_adequacy_closed", False)),
        safe=safe, strict_target_progress=progress,
        reach=reach, avoid=bool(avoid_reasons), progress=progress,
        decision=decision,
        restoration_or_commit_receipt=receipt_id,
        input_artifact_ids=tuple(
            values[-1] for values in state.artifact_ids.values() if values
        ),
        recomputation_hash="",
    ))


def evaluate_patch_revision(
    state: ReachAvoidState,
    revision: GeneratorRevision,
) -> TransitionResult:
    """Validate one incremental revision against the sole incumbent lineage."""

    conversion = convert_revision_action(state, revision)
    if conversion.status not in {
        ActionConversionStatus.ACCEPTED,
        ActionConversionStatus.NEEDS_SLICE_EXPANSION,
    }:
        raise ValueError(
            f"revision conversion {conversion.status.value}: {'; '.join(conversion.reasons)}"
        )
    state.transition_phase(ControllerPhase.MECHANICAL_VALIDATE, event="generator_revision_submitted")
    previous_target_deficit = state.target_deficit()
    transition_id = stable_id(
        "patch-revision-transition", state.checkpoint.checkpoint_id,
        revision.revision_id, state.transition_index + 1,
    )
    manager = WorktreeManager(Path(state.run_root) / "worktrees")
    trial = manager.begin_trial(state.checkpoint.checkpoint_id)
    trial_tree = Path(trial.tree)
    checkpoint_tree = Path(state.checkpoint.snapshot_tree)
    config = state.runtime_config
    graph_stage_timings: dict[str, float] = {}
    transition_started = time.perf_counter()
    forbidden = tuple(config.get("forbidden_patterns", ()))
    public_commands = tuple(
        tuple(map(str, item))
        for item in config.get(
            "public_check_commands", config.get("mechanical_commands", ())
        )
        if item
    )
    try:
        _apply_revision_edits(trial_tree, revision)
        incremental = reconcile_actual_diff(
            checkpoint_tree, trial_tree, forbidden_patterns=forbidden
        )
    except Exception as exc:
        incremental = reconcile_actual_diff(
            checkpoint_tree, trial_tree, forbidden_patterns=forbidden
        )
        receipt = manager.rollback(trial)
        packet = _mechanical_packet(
            state, transition_id, incremental, f"EDIT_APPLY:{type(exc).__name__}:{exc}"
        )
        certificate = _revision_certificate(
            state, revision, transition_id, incremental, (),
            decision=Decision.ROLLBACK, receipt_id=receipt.receipt_id,
            result_checkpoint_id=None, graph_delta={
                "edit_error": str(exc),
                "actual_diff": incremental.to_dict(),
            },
            outcomes=state.outcomes, packets=(packet,), safe=False,
            progress=False, reach=False, avoid_reasons=("EDIT_APPLY_FAILURE",),
        )
        state.counterexamples.append(packet)
        state.repair_history.append(certificate)
        state.transition_index += 1
        if state.generator_conversation is not None:
            state.generator_conversation.rejected_patch_hashes.append(
                incremental.canonical_diff_hash
            )
        state.transition_phase(ControllerPhase.COUNTEREXAMPLE_FEEDBACK, event="edit_apply_failed")
        return TransitionResult(
            transition_id, False, Decision.ROLLBACK, certificate,
            (packet,), state.checkpoint, revision, "EDIT_APPLY_FAILURE",
        )
    checks = run_mechanical_checks(
        trial_tree, incremental,
        baseline_root=checkpoint_tree,
    )
    mechanical_ok = mechanical_pass(checks)
    avoid_reasons: set[str] = set()
    if incremental.empty:
        avoid_reasons.add("EMPTY_REVISION")
    if not mechanical_ok:
        avoid_reasons.add("MECHANICAL_FAILURE")
    if incremental.forbidden_paths:
        avoid_reasons.add("FORBIDDEN_EDIT")
    if incremental.oracle_contamination_paths:
        avoid_reasons.add("ORACLE_CONTAMINATION")
    if avoid_reasons:
        receipt = manager.rollback(trial)
        packet = _mechanical_packet(
            state, transition_id, incremental, ",".join(sorted(avoid_reasons))
        )
        certificate = _revision_certificate(
            state, revision, transition_id, incremental, checks,
            decision=Decision.ROLLBACK, receipt_id=receipt.receipt_id,
            result_checkpoint_id=None,
            graph_delta={"actual_diff": incremental.to_dict()},
            outcomes=state.outcomes,
            packets=(packet,), safe=False, progress=False, reach=False,
            avoid_reasons=tuple(sorted(avoid_reasons)),
        )
        state.counterexamples.append(packet)
        state.repair_history.append(certificate)
        state.transition_index += 1
        if state.generator_conversation is not None:
            state.generator_conversation.rejected_patch_hashes.append(
                incremental.canonical_diff_hash
            )
        state.transition_phase(ControllerPhase.COUNTEREXAMPLE_FEEDBACK, event="mechanical_rollback")
        return TransitionResult(
            transition_id, False, Decision.ROLLBACK, certificate,
            (packet,), state.checkpoint, revision, ",".join(sorted(avoid_reasons)),
        )
    if state.target_recovery is None or not state.target_recovery.targets:
        receipt = manager.rollback(trial)
        state.termination_status = "TARGET_RECOVERY_BLOCKED"
        certificate = _revision_certificate(
            state, revision, transition_id, incremental, checks,
            decision=Decision.ROLLBACK, receipt_id=receipt.receipt_id,
            result_checkpoint_id=None,
            graph_delta={"actual_diff": incremental.to_dict()},
            outcomes=state.outcomes, packets=(), safe=False,
            progress=False, reach=False,
            avoid_reasons=("NO_EXECUTABLE_TARGET",),
        )
        state.repair_history.append(certificate)
        state.transition_index += 1
        return TransitionResult(
            transition_id, False, Decision.ROLLBACK, certificate, (),
            state.checkpoint, revision, "TARGET_RECOVERY_BLOCKED",
        )
    project_runner = select_project_runner(
        state.base_repository,
        artifact_root=Path(state.run_root) / "execution",
        base_commit=state.base_commit,
    )
    all_checks = tuple((
        *state.target_recovery.targets,
        *state.target_recovery.preservation_checks,
    ))
    check_by_id = {item.check_id: item for item in all_checks}
    baseline_by_id = {
        item.check_id: item for item in state.target_recovery.baseline_executions
    }
    trial_hash = tree_hash(trial_tree)
    public_comparisons = tuple(
        CheckComparison.create(
            baseline_by_id[check.check_id],
            project_runner.run_check(
                check, repository=trial_tree, tree_hash=trial_hash,
            ),
            check.role,
        )
        for check in all_checks
        if check.check_id in baseline_by_id
    )
    _charge_execution(state, sum(
        item.patched.duration_seconds for item in public_comparisons
    ))
    cuts_by_check = {
        comparison.check_id: causal_repair_cut(
            comparison.patched,
            check_by_id[comparison.check_id],
            state.program_graph,
            incremental,
        )
        for comparison in public_comparisons
        if comparison.check_id in check_by_id
        and comparison.classification in {
            CheckClassification.TARGET_STILL_FAILING,
            CheckClassification.TARGET_REGRESSED,
            CheckClassification.PRESERVATION_REGRESSION,
        }
    }
    public_packets = tuple(
        packet
        for item in public_comparisons
        if item.check_id in check_by_id
        for packet in (
            counterexample_from_check_comparison(
                state, check_by_id[item.check_id], item, incremental,
                transition_id=transition_id,
                repair_cuts=cuts_by_check.get(item.check_id, ()),
            ),
        )
        if packet is not None
    )
    public_regressions = tuple(
        item for item in public_comparisons
        if item.classification in {
            CheckClassification.TARGET_REGRESSED,
            CheckClassification.PRESERVATION_REGRESSION,
            CheckClassification.NEW_INFRA_FAILURE,
        }
    )
    if public_regressions:
        receipt = manager.rollback(trial)
        graph_delta = {
            "actual_diff": incremental.to_dict(),
            "public_check_comparisons": [
                item.to_dict() for item in public_comparisons
            ],
            "public_check_classification_counts": {
                classification: sum(
                    item.classification == classification
                    for item in public_comparisons
                )
                for classification in sorted({
                    item.classification for item in public_comparisons
                })
            },
        }
        certificate = _revision_certificate(
            state, revision, transition_id, incremental, checks,
            decision=Decision.ROLLBACK, receipt_id=receipt.receipt_id,
            result_checkpoint_id=None, graph_delta=graph_delta,
            outcomes=state.outcomes, packets=public_packets,
            safe=False, progress=False, reach=False,
            avoid_reasons=("PUBLIC_EXECUTION_REGRESSION",),
        )
        state.counterexamples.extend(public_packets)
        state.repair_history.append(certificate)
        state.transition_index += 1
        state.runtime_metrics["last_public_check_comparisons"] = [
            item.to_dict() for item in public_comparisons
        ]
        state.runtime_metrics["public_check_execution_count"] = int(
            state.runtime_metrics.get("public_check_execution_count", 0)
        ) + len(public_comparisons) * 2
        if state.generator_conversation is not None:
            state.generator_conversation.rejected_patch_hashes.append(
                incremental.canonical_diff_hash
            )
        state.transition_phase(
            ControllerPhase.COUNTEREXAMPLE_FEEDBACK,
            event="public_preservation_regression",
        )
        return TransitionResult(
            transition_id, False, Decision.ROLLBACK, certificate,
            public_packets, state.checkpoint, revision,
            "PUBLIC_EXECUTION_REGRESSION",
        )
    if state.repository_index is None:
        manager.rollback(trial)
        raise RuntimeError("patch-first transition requires a persisted RepositoryIndex")
    graph_budget = GraphBudget.from_limits(
        seconds=float(config.get("program_slice_deadline_seconds", 90.0)),
        max_nodes=int(config.get("max_program_nodes", 50_000)),
        max_edges=int(config.get("max_program_edges", 150_000)),
        max_files=int(config.get("max_precise_files", 40)),
        max_functions=int(config.get("max_precise_functions", 200)),
        max_rss_mib=int(config.get("graph_memory_limit_mib", 2048)),
        max_protocol_candidates_per_operation=int(
            config.get("max_protocol_candidates_per_operation", 8)
        ),
    )
    state.transition_phase(ControllerPhase.ACTIVE_GRAPH_BUILD, event="mechanical_checks_passed")
    graph_stage_timings_started = time.perf_counter()
    trial_repository_index = update_repository_index(
        state.repository_index, trial_tree, tuple(incremental.changed_files),
        deadline=Deadline.after(float(
            config.get("repository_index_deadline_seconds", 60.0)
        )),
    )
    graph_stage_timings["repository_index_seconds"] = time.perf_counter() - graph_stage_timings_started
    graph_stage_timings_started = time.perf_counter()
    graph_delta_result = update_active_program_slice(
        state.program_graph, trial_repository_index, trial_tree,
        incremental, None, tuple(revision.context_requests), graph_budget,
    )
    graph_stage_timings["program_graph_seconds"] = time.perf_counter() - graph_stage_timings_started
    trial_program = graph_delta_result.graph
    graph_stage_timings_started = time.perf_counter()
    trial_requirements = copy.deepcopy(state.requirement_graph)
    requirement_delta = promote_domains_from_diff(
        trial_requirements, trial_program, incremental, None,
        deadline=time.monotonic() + float(
            config.get("requirement_deadline_seconds", 30.0)
        ),
    )
    changed_program_nodes = set(
        graph_delta_result.added_node_ids
        + graph_delta_result.removed_node_ids
        + graph_delta_result.modified_node_ids
    )
    affected_leaf_ids = set(requirement_delta.affected_leaf_ids)
    affected_path_ids = {
        unit.path_obligation_id
        for unit in state.binding_graph.units.values()
        if changed_program_nodes & set(unit.interaction_path_ids)
    }
    affected_leaf_ids.update(
        state.requirement_graph.path_obligations[path_id].leaf_id
        for path_id in affected_path_ids
        if path_id in state.requirement_graph.path_obligations
    )
    if not trial_requirements.path_obligations:
        compile_requirement_paths(
            trial_requirements, trial_program,
            max_open_world_seeds=min(64, int(config.get("max_precise_functions", 200))),
            max_observation_nodes=min(128, int(config.get("max_program_nodes", 50_000))),
            max_paths_per_entry=int(config.get("max_path_classes_per_leaf", 24)),
            max_path_classes_per_leaf=int(config.get("max_path_classes_per_leaf", 24)),
            promote_all_program_predicates=False,
            deadline=time.monotonic() + float(config.get("requirement_deadline_seconds", 30.0)),
        )
        affected_leaf_ids.update(trial_requirements.leaves)
        affected_path_ids.update(trial_requirements.path_obligations)
    elif affected_leaf_ids:
        trial_requirements, removed_paths, added_paths = refresh_requirement_paths(
            trial_requirements, trial_program,
            affected_leaf_ids=affected_leaf_ids,
            max_path_classes_per_leaf=int(
                config.get("max_path_classes_per_leaf", 24)
            ),
            deadline=time.monotonic() + float(
                config.get("requirement_deadline_seconds", 30.0)
            ),
        )
        affected_path_ids.update(removed_paths)
        affected_path_ids.update(added_paths)
    affected_path_ids.update({
        obligation.path_obligation_id
        for obligation in trial_requirements.path_obligations.values()
        if obligation.leaf_id in set(requirement_delta.affected_leaf_ids)
        or set(obligation.path_edge_ids) & set(graph_delta_result.added_edge_ids + graph_delta_result.removed_edge_ids)
    })
    graph_stage_timings["requirement_graph_seconds"] = time.perf_counter() - graph_stage_timings_started
    graph_stage_timings_started = time.perf_counter()
    trial_binding = build_active_binding_graph(
        trial_requirements, trial_program, previous=state.binding_graph,
        affected_leaf_ids=affected_leaf_ids,
        affected_path_ids=affected_path_ids,
        max_target_units=int(config.get("max_active_target_bindings", 20)),
        max_preservation_units=int(config.get("max_active_preservation_bindings", 20)),
        deadline=time.monotonic() + float(config.get("binding_deadline_seconds", 15.0)),
    )
    graph_stage_timings["binding_graph_seconds"] = time.perf_counter() - graph_stage_timings_started
    graph_stage_timings_started = time.perf_counter()
    trial_challenges = materialize_active_challenges(
        trial_requirements, trial_program, trial_binding,
        actual_diff=incremental, previous_outcomes=state.outcomes,
        max_challenges=int(config.get("max_active_challenges", 40)),
        deadline=time.monotonic() + float(config.get("challenge_deadline_seconds", 15.0)),
    )
    discriminator_challenges = _enqueue_pending_discriminators(
        state, trial_challenges
    )
    graph_stage_timings["challenge_graph_seconds"] = time.perf_counter() - graph_stage_timings_started
    state.transition_phase(ControllerPhase.CHALLENGE_EXECUTE, event="active_graph_stack_updated")
    executor = TraceExecutor(temporary_root=Path(state.run_root) / "tmp")
    execution = execute_challenges(
        trial_challenges, executor, state.base_repository, trial_tree,
        max_workers=int(config.get("max_parallel_challenge_executions", 2)),
    )
    targeted_expansion = False
    if execution.real_execution_count == 0:
        trial_challenges.add_frontier(Frontier(
            frontier_id=stable_id(
                "challenge-frontier", "NO_EXECUTABLE_CHALLENGE",
                incremental.canonical_diff_hash,
            ),
            kind="NO_EXECUTABLE_CHALLENGE",
            owner_id=trial_binding.assignment_id,
            reason="no active challenge had both an executable recipe and trusted oracle",
            resolution_action="request a targeted slice or executable public oracle",
            hard=False,
            evidence_ids=(),
        ))
    # A pure-UNKNOWN first pass gets one bounded information-gain expansion
    # while the trial worktree is still alive.  Only after this second pass is
    # the revision committed or rolled back.
    probe_state = copy.copy(state)
    probe_state.requirement_graph = trial_requirements
    probe_state.binding_graph = trial_binding
    first_outcomes = outcomes_from_challenges(
        probe_state, trial_challenges, execution
    )
    pure_unknown = (
        not first_outcomes
        or all(
            item.status not in {OutcomeStatus.PASS, OutcomeStatus.FAIL}
            for item in first_outcomes.values()
        )
    )
    if pure_unknown:
        expanded_challenges = materialize_active_challenges(
            trial_requirements, trial_program, trial_binding,
            actual_diff=incremental, previous_outcomes=state.outcomes,
            max_challenges=min(
                int(config.get("max_active_challenges", 40)) * 2,
                int(config.get("max_active_challenges", 40)) + 40,
            ),
            deadline=time.monotonic() + float(
                config.get("challenge_deadline_seconds", 15.0)
            ),
        )
        expanded_discriminators = _enqueue_pending_discriminators(
            state, expanded_challenges
        )
        if set(expanded_challenges.cells) - set(trial_challenges.cells):
            trial_challenges = expanded_challenges
            discriminator_challenges = expanded_discriminators
            execution = execute_challenges(
                trial_challenges, executor, state.base_repository, trial_tree,
                max_workers=int(
                    config.get("max_parallel_challenge_executions", 2)
                ),
            )
            targeted_expansion = True
    executed_discriminator_ids = _executed_discriminator_probe_ids(
        trial_challenges, execution.executed_challenge_ids
    )
    if execution.real_execution_count > 0 and execution.trace_delta.get("nonempty"):
        merge_trace_bundles(trial_program, execution, role="PATCH")
        trial_binding = build_active_binding_graph(
            trial_requirements, trial_program, previous=trial_binding,
            affected_leaf_ids=set(), affected_path_ids=set(),
            max_target_units=int(config.get("max_active_target_bindings", 20)),
            max_preservation_units=int(
                config.get("max_active_preservation_bindings", 20)
            ),
            deadline=time.monotonic() + float(
                config.get("binding_deadline_seconds", 15.0)
            ),
        )
        trial_challenges.program_graph_hash = trial_program.program_hash()
        trial_challenges.binding_graph_hash = trial_binding.graph_hash()
        for challenge_id, cell in tuple(trial_challenges.cells.items()):
            trial_challenges.cells[challenge_id] = replace(
                cell,
                graph_hashes={
                    **cell.graph_hashes,
                    "program": trial_challenges.program_graph_hash,
                    "binding": trial_challenges.binding_graph_hash,
                },
            )
    trial_state = copy.copy(state)
    trial_state.requirement_graph = trial_requirements
    trial_state.program_graph = trial_program
    trial_state.binding_graph = trial_binding
    trial_state.challenge_graph = trial_challenges
    trial_state.outcomes = outcomes_from_challenges(
        trial_state, trial_challenges, execution
    )
    trial_state.trace_bundles = {
        **state.trace_bundles,
        **{item.paired_bundle_id: item for item in execution},
    }
    coverage_keys = tuple(sorted({
        relation.kind for relation in incremental.changed_relations
        if any(
            node.attributes.get("file") == relation.file
            for node in trial_program.nodes.values()
        )
    } | set(execution.executed_challenge_ids)))
    high_pending = tuple(sorted(
        challenge_id for challenge_id, cell in trial_challenges.cells.items()
        if cell.hard and cell.terminal_status not in {
            ChallengeTerminalStatus.PASS, ChallengeTerminalStatus.INFEASIBLE_PROVED,
        }
    ))
    command_key = lambda comparison: "\0".join(
        check_by_id[comparison.check_id].command
    )
    previous_target_commands = set(
        map(str, state.runtime_metrics.get("public_target_fixed_commands", ()))
    )
    current_by_command = {
        command_key(item): item for item in public_comparisons
    }
    public_target_commands = {
        command_key(item) for item in public_comparisons
        if item.classification == "TARGET_FIXED"
    } | {
        key for key in previous_target_commands
        if key in current_by_command
        and current_by_command[key].classification == "PASS_PRESERVED"
    }
    active_target_unit_ids = {
        unit.unit_id for unit in trial_binding.units.values()
        if unit.status in {"ACTIVE", "READY"}
        and trial_requirements.leaves[unit.leaf_id].authority_class.value
        != "PRESERVATION"
    }
    public_target_evidence_units = set(
        map(str, state.runtime_metrics.get("public_target_evidence_unit_ids", ()))
    )
    if public_target_commands:
        public_target_evidence_units.update(active_target_unit_ids)
    trial_state.runtime_metrics = {
        **state.runtime_metrics,
        "graph_build_records": [
            *[
                dict(item) for item in state.runtime_metrics.get("graph_build_records", ())
                if isinstance(item, dict)
            ],
            {
                "kind": "incremental_transition",
                "transition_id": transition_id,
                **graph_stage_timings,
                "total_seconds": sum(graph_stage_timings.values()),
                "program_nodes": len(trial_program.nodes),
                "program_edges": len(trial_program.edges),
                "precise_files": len(trial_program.file_index),
                "rebuilt_precise_files": len(graph_delta_result.rebuilt_files),
                "precise_functions": len(trial_program.cfgs),
                "requirement_leaves": len(trial_requirements.leaves),
                "requirement_path_obligations": len(trial_requirements.path_obligations),
                "binding_units": len(trial_binding.units),
                "active_binding_units": trial_binding.build_stats.get("active_count", 0),
                "deferred_binding_units": trial_binding.build_stats.get("deferred_count", 0),
                "challenge_cells": len(trial_challenges.cells),
                "peak_rss_mib": max(
                    float(state.runtime_metrics.get("peak_rss_mib", 0.0)),
                    graph_delta_result.build.peak_rss_mib,
                ),
                "truncated": bool(
                    graph_delta_result.build.truncated_reason
                    or any(
                        frontier.kind == "ANALYSIS_TRUNCATED"
                        for graph in (trial_requirements, trial_program, trial_binding, trial_challenges)
                        for frontier in getattr(graph, "frontiers", {}).values()
                    )
                ),
            },
        ],
        "last_graph_build_timings": dict(graph_stage_timings),
        "graph_build_total_seconds": float(
            state.runtime_metrics.get("graph_build_total_seconds", 0.0)
        ) + sum(graph_stage_timings.values()),
        "active_program_slice_seconds": graph_delta_result.build.elapsed_seconds,
        "program_nodes": len(trial_program.nodes),
        "program_edges": len(trial_program.edges),
        "precise_files": len(trial_program.file_index),
        "rebuilt_precise_files": len(graph_delta_result.rebuilt_files),
        "precise_functions": len(trial_program.cfgs),
        "peak_rss_mib": max(
            float(state.runtime_metrics.get("peak_rss_mib", 0.0)),
            graph_delta_result.build.peak_rss_mib,
        ),
        "requirement_leaves": len(trial_requirements.leaves),
        "requirement_partitions": len(trial_requirements.partitions),
        "candidate_binding_count": trial_binding.build_stats.get("candidate_count", 0),
        "active_binding_count": trial_binding.build_stats.get("active_count", 0),
        "deferred_binding_count": trial_binding.build_stats.get("deferred_count", 0),
        "active_challenge_count": len(trial_challenges.cells),
        "diff_adequacy_keys": coverage_keys,
        "diff_adequacy_closed": bool(coverage_keys) and not high_pending,
        "high_value_pending_challenge_ids": high_pending,
        "high_risk_unknowns": len(high_pending),
        "real_execution_challenge_count": execution.real_execution_count,
        "targeted_challenge_expansion": targeted_expansion,
        "discriminator_probe_challenges": {
            probe_id: list(challenge_ids)
            for probe_id, challenge_ids in sorted(
                discriminator_challenges.items()
            )
        },
        "executed_discriminator_probe_ids": sorted(
            set(map(str, state.runtime_metrics.get(
                "executed_discriminator_probe_ids", ()
            ))) | executed_discriminator_ids
        ),
        "last_public_check_comparisons": [
            item.to_dict() for item in public_comparisons
        ],
        "public_check_execution_count": int(
            state.runtime_metrics.get("public_check_execution_count", 0)
        ) + len(public_comparisons) * 2,
        "public_target_fixed_commands": sorted(public_target_commands),
        "public_target_evidence_unit_ids": sorted(public_target_evidence_units),
        "public_stable_fail_commands": sorted(
            command_key(item) for item in public_comparisons
            if item.classification == CheckClassification.TARGET_STILL_FAILING
        ),
        "public_preservation_pass_commands": sorted(
            command_key(item) for item in public_comparisons
            if item.classification == CheckClassification.PASS_PRESERVED
        ),
        "public_unknown_commands": sorted(
            command_key(item) for item in public_comparisons
            if item.classification in {
                CheckClassification.SAME_INFRA_FAILURE,
                CheckClassification.NEW_INFRA_FAILURE,
                CheckClassification.FLAKY_RESULT,
                CheckClassification.UNSUPPORTED_CHECK,
            }
        ),
    }
    challenge_packets = packets_for_nonpass_challenges(
        trial_state, trial_challenges, incremental,
        transition_id=transition_id, executor=executor,
        base_repository=state.base_repository, patch_repository=trial_tree,
    )
    packets = tuple(challenge_packets) + public_packets
    trial_state.counterexamples = state.counterexamples + list(packets)
    graph_metrics = progress_metrics(state, trial_state)
    established_regression = any(
        old.status == OutcomeStatus.PASS
        and any(
            new.unit_id == old.unit_id and new.status == OutcomeStatus.FAIL and new.stable
            for new in trial_state.outcomes.values()
        )
        for old in state.outcomes.values()
    )
    if established_regression:
        avoid_reasons.add("ESTABLISHED_SUCCESS_LOST")
    if graph_metrics.new_preservation_failures:
        avoid_reasons.add("PRESERVATION_FAILURE")
    if any(item.kind == "external_effect_added" for item in incremental.changed_relations):
        avoid_reasons.add("NEW_HIGH_RISK_SIDE_EFFECT")
    cumulative = reconcile_actual_diff(
        state.base_repository, trial_tree, forbidden_patterns=forbidden
    )
    impact_slice = build_diff_impact_slice(
        cumulative,
        trial_repository_index,
        trial_program,
        GraphBudget.from_limits(
            seconds=float(config.get("program_slice_deadline_seconds", 90.0)),
            max_nodes=int(config.get("max_program_nodes", 8_000)),
            max_edges=int(config.get("max_program_edges", 24_000)),
            max_files=int(config.get("max_precise_files", 8)),
            max_functions=int(config.get("max_precise_functions", 40)),
            max_rss_mib=int(config.get("graph_memory_limit_mib", 2048)),
            max_protocol_candidates_per_operation=int(
                config.get("max_protocol_candidates_per_operation", 8)
            ),
        ),
    )
    executable_binding = build_executable_bindings(
        state.executable_requirement_overlay,
        state.target_slice,
        state.causal_slices,
        impact_slice,
    )
    executable_challenges = compile_executable_challenge_evidence(
        executable_binding, public_comparisons, cumulative, impact_slice,
        trace_results=(execution,),
        checks=all_checks,
        repository_index=trial_repository_index,
    )
    executable_obligation_count = len(
        state.executable_requirement_overlay.executable_requirements
    )
    active_executable_binding_count = sum(
        unit.check_id is not None for unit in executable_binding.units
    )
    dicc = evaluate_dicc(
        executable_binding.executable_targets,
        public_comparisons,
        cumulative,
        impact_slice,
        executable_challenges,
        path_obligation_count=executable_obligation_count,
        active_binding_count=active_executable_binding_count,
    )
    trial_state.runtime_metrics.update({
        "normative_requirement_path_obligations": len(
            trial_requirements.path_obligations
        ),
        "executable_requirement_obligations": executable_obligation_count,
        "requirement_path_obligations": (
            len(trial_requirements.path_obligations)
            + executable_obligation_count
        ),
        "normative_active_binding_count": trial_binding.build_stats.get(
            "active_count", 0
        ),
        "active_binding_count": active_executable_binding_count,
        "normative_challenge_cell_count": len(trial_challenges.cells),
        "active_challenge_count": len(executable_challenges.challenge_ids),
        "real_execution_challenge_count": (
            executable_challenges.real_execution_count
        ),
        "executed_challenge_ids": list(executable_challenges.challenge_ids),
        "dicc_status": dicc.status.value,
    })
    execution_progress = progress_vector_from_comparisons(
        state.check_comparisons, public_comparisons,
    )
    infrastructure_blocked = any(
        item.classification in {
            CheckClassification.SAME_INFRA_FAILURE,
            CheckClassification.FLAKY_RESULT,
            CheckClassification.UNSUPPORTED_CHECK,
        }
        for item in public_comparisons
    )
    execution_safe = (
        not avoid_reasons
        and not any(
            item.classification in {
                CheckClassification.TARGET_REGRESSED,
                CheckClassification.PRESERVATION_REGRESSION,
                CheckClassification.NEW_INFRA_FAILURE,
            }
            for item in public_comparisons
        )
    )
    transition_evidence = RevisionEvidence(
        progress=execution_progress,
        safe=execution_safe,
        real_execution_count=len(public_comparisons),
        environment_blocked=infrastructure_blocked,
    )
    transition_decision = decide_execution_transition(None, transition_evidence)
    accepted = transition_decision == Decision.COMMIT
    result_checkpoint_id = None
    if accepted:
        result_checkpoint_id = stable_id(
            "checkpoint", state.episode_id, cumulative.canonical_diff_hash,
            state.transition_index + 1,
        )
        receipt = manager.commit(trial, result_checkpoint_id)
        snapshot = manager.checkpoint_tree(result_checkpoint_id)
        checkpoint = _build_checkpoint(
            state, result_checkpoint_id, str(snapshot), cumulative,
            trial_state.outcomes, transition_id, state.generator_session.cursor,
            safe=execution_safe,
            executed_target_deficit=sum(
                item.classification != CheckClassification.TARGET_FIXED
                for item in public_comparisons
                if item.check_id in {
                    target.check_id for target in state.target_recovery.targets
                }
            ),
            patch_status=(
                "REACHED" if dicc.status.value == "CLOSED"
                else "WORKING_IMPROVED"
            ),
        )
        state.requirement_graph = trial_requirements
        state.program_graph = trial_program
        state.binding_graph = trial_binding
        state.challenge_graph = trial_challenges
        state.outcomes = trial_state.outcomes
        state.trace_bundles = trial_state.trace_bundles
        state.runtime_metrics = trial_state.runtime_metrics
        state.repository_index = trial_repository_index
        state.impact_slice = impact_slice
        state.executable_binding_graph = executable_binding
        state.check_comparisons = public_comparisons
        state.dicc_certificate = dicc
        state.checkpoint = replace(
            checkpoint, graph_hashes=state.graph_hashes(),
            graph_reached=False, safe=execution_safe,
        )
        state.working_trial = None
        if state.generator_conversation is not None:
            state.generator_conversation.accepted_patch_hashes.append(
                cumulative.canonical_diff_hash
            )
            state.generator_conversation.current_working_diff = (
                cumulative.canonical_diff
            )
            state.generator_conversation.passed_preservation_checks.update(
                item.check_id for item in public_comparisons
                if item.classification == CheckClassification.PASS_PRESERVED
            )
            state.generator_conversation.eliminated_counterexamples.update(
                item.counterexample_id for item in state.counterexamples
                if item.failure_signature
                and item.failure_signature not in {
                    comparison.patched.failure_signature
                    for comparison in public_comparisons
                }
            )
    elif transition_decision == Decision.KEEP_UNCERTIFIED:
        receipt = manager.keep_uncertified(trial)
        state.working_trial = {
            "trial_id": trial.trial_id,
            "source_checkpoint_id": trial.source_checkpoint_id,
            "snapshot_tree": receipt.snapshot_tree,
            "tree_hash": receipt.after_tree_hash,
            "canonical_diff": cumulative.canonical_diff,
            "canonical_diff_hash": cumulative.canonical_diff_hash,
            "comparison_ids": [item.comparison_id for item in public_comparisons],
            "status": "WORKING_UNCERTIFIED",
        }
        state.termination_status = "ENVIRONMENT_BLOCKED"
    else:
        receipt = manager.rollback(trial)
        if state.generator_conversation is not None:
            state.generator_conversation.rejected_patch_hashes.append(
                incremental.canonical_diff_hash
            )
            state.generator_conversation.rolled_back_diffs.append(
                incremental.canonical_diff
            )
            state.generator_conversation.unresolved_counterexamples.update(
                item.counterexample_id for item in packets
            )
            state.generator_conversation.mechanism_failure_signatures.setdefault(
                revision.mechanism, []
            ).extend(
                item.failure_signature for item in packets
                if item.failure_signature
            )
    graph_delta = {
        "old_target_deficit": previous_target_deficit,
        "actual_diff": incremental.to_dict(),
        "program": {
            "added_nodes": list(graph_delta_result.added_node_ids),
            "removed_nodes": list(graph_delta_result.removed_node_ids),
            "modified_nodes": list(graph_delta_result.modified_node_ids),
            "added_edges": list(graph_delta_result.added_edge_ids),
            "removed_edges": list(graph_delta_result.removed_edge_ids),
            "rebuilt_files": list(graph_delta_result.rebuilt_files),
            "updated_index_files": list(incremental.changed_files),
        },
        "graph_timings_seconds": dict(graph_stage_timings),
        "graph_build_total_seconds": sum(graph_stage_timings.values()),
        "requirement": asdict(requirement_delta),
        "binding_stats": trial_binding.build_stats,
        "active_challenge_ids": list(trial_challenges.cells),
        "executed_challenge_ids": list(execution.executed_challenge_ids),
        "real_execution_count": execution.real_execution_count,
        "public_check_comparisons": [
            item.to_dict() for item in public_comparisons
        ],
        "public_check_classification_counts": {
            classification: sum(
                item.classification == classification
                for item in public_comparisons
            )
            for classification in sorted({
                item.classification for item in public_comparisons
            })
        },
        "diff_adequacy_closed": bool(trial_state.runtime_metrics["diff_adequacy_closed"]),
        "hard_frontier_ids": list(high_pending),
        "cumulative_diff_hash": cumulative.canonical_diff_hash if accepted else state.checkpoint.patch.canonical_diff_hash,
        "progress_metrics": asdict(execution_progress),
        "graph_progress_metrics": asdict(graph_metrics),
        "dicc": dicc.to_dict(),
        "impact_slice": impact_slice.to_dict(),
        "avoid_reasons": sorted(avoid_reasons),
    }
    reach = accepted and in_target_set(state)
    certificate = _revision_certificate(
        state, revision, transition_id, incremental, checks,
        decision=transition_decision,
        receipt_id=receipt.receipt_id, result_checkpoint_id=result_checkpoint_id,
        graph_delta=graph_delta, outcomes=trial_state.outcomes,
        packets=packets, safe=execution_safe,
        progress=accepted, reach=reach,
        avoid_reasons=tuple(sorted(avoid_reasons)),
    )
    state.counterexamples.extend(packets)
    state.repair_history.append(certificate)
    state.transition_index += 1
    state.transition_phase(
        ControllerPhase.TRANSITION_GATE,
        event=(
            "revision_committed" if accepted
            else "revision_kept_uncertified"
            if transition_decision == Decision.KEEP_UNCERTIFIED
            else "revision_rolled_back"
        ),
    )
    state.refresh_id()
    return TransitionResult(
        transition_id=transition_id, accepted=accepted,
        decision=transition_decision,
        certificate=certificate, counterexamples=packets,
        checkpoint=state.checkpoint, action=revision,
        reason=("SAFE_PROGRESS" if accepted else ",".join(sorted(avoid_reasons)) or "NO_CONFIRMED_PROGRESS"),
    )
