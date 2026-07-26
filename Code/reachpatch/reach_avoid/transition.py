from __future__ import annotations

import copy
import platform
import sys
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from reachpatch.binding_graph import build_binding_graph
from reachpatch.challenge_graph.dicc import (
    diff_induced_challenge_plan,
    finalize_diff_induced_challenge_closure,
)
from reachpatch.challenge_graph.materialize import execute_challenges, materialize_challenges
from reachpatch.challenge_graph.models import ChallengeGraph
from reachpatch.execution import TraceExecutor, WorktreeManager
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
from reachpatch.models.enums import ChallengeTerminalStatus, ControllerPhase, Decision, OutcomeStatus
from reachpatch.program_graph.builder import build_augmented_program_graph
from reachpatch.program_graph.tracing import merge_trace_bundles
from reachpatch.program_graph.impact import guarded_diff_influence_cone
from reachpatch.reach_avoid.certificates import finalize_certificate
from reachpatch.reach_avoid.gates import in_target_set, raw_avoid_reasons
from reachpatch.reach_avoid.metrics import component_shadow_pass, progress_metrics
from reachpatch.reach_avoid.state import outcomes_from_challenges
from reachpatch.repair.counterexamples import packets_for_nonpass_challenges
from reachpatch.repair.diagnosis import mechanism_fingerprint
from reachpatch.repair.operators import apply_registered_operator
from reachpatch.repair.session import PersistentGeneratorSession
from reachpatch.requirement_graph import compile_assignment_overlay, compile_requirement_paths


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
        executed_target_deficit=0.0,
        accepted_transition_id=transition_id,
        generator_session_cursor=str(generator_cursor),
        remaining_budget=state.remaining_budget,
        safe=True,
        graph_reached=False,
    )


def evaluate_single_update(
    state: ReachAvoidState,
    session: PersistentGeneratorSession,
    intent,
    *,
    forbidden_patterns: Iterable[str] = (),
    mechanical_commands: Iterable[Iterable[str]] = (),
) -> TransitionResult | None:
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
        mechanical_check_ids=tuple(item.check_id for item in checks),
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
