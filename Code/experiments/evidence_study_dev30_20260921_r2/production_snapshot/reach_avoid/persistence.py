from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from reachpatch.models.base import canonical_json
from reachpatch.models.evidence import (
    FailureHistory, OutcomeStatus, PairedTraceBundle, PairClassification,
)
from reachpatch.models.graphs import ChallengeStatus, GraphStack
from reachpatch.models.reach_avoid import (
    Decision, ReachAvoidState, TransitionCertificate, TrialTransition,
)
from reachpatch.reach_avoid.frontier import FrontierStatus
from reachpatch.reach_avoid.semantics import scenario_semantic_key

from .certificates import build_transition_certificate, verify_transition_certificate
from .checkpoint import (
    CheckpointStore, capture_checkpoint, capture_current_graph_checkpoint,
    restore_parent_working_checkpoint, update_best_checkpoint, update_safe_checkpoint,
    update_working_checkpoint,
)


def record_locked_passes(
    state: ReachAvoidState,
    graph_stack: GraphStack,
    executions: tuple[PairedTraceBundle, ...],
) -> None:
    """Lock only the concrete public checks that actually passed stably."""
    for execution in executions:
        cell = graph_stack.challenge_graph.cells.get(execution.challenge_id)
        if cell is None:
            continue
        requirement = graph_stack.requirement_graph.leaves.get(cell.requirement_id)
        binding = graph_stack.binding_graph.units.get(cell.binding_id)
        check_id = execution.check_id
        if requirement is None or binding is None or not check_id:
            continue
        if (
            execution.oracle_authority not in {"A", "B", "C"}
            or execution.stable_runs < 2
            or execution.patched.observation.status is not OutcomeStatus.PASS
            or cell.terminal_status is not ChallengeStatus.PASS
            or cell.origin != "PUBLIC_CHECK"
            or cell.input_recipe.kind != "PUBLIC_REPLAY"
            or cell.input_recipe.source_check_id != check_id
        ):
            continue
        if requirement.preservation:
            if (
                execution.classification is PairClassification.PASS_PRESERVED
                and check_id in binding.preservation_check_ids
            ):
                state.locked_checks.preservation_ids.add(check_id)
        elif (
            execution.classification in {
                PairClassification.TARGET_FIXED,
                PairClassification.PASS_PRESERVED,
            }
            and check_id in binding.target_check_ids
        ):
            state.locked_checks.target_ids.add(check_id)


def _record_trial_evidence(state: ReachAvoidState, trial: TrialTransition) -> None:
    if not trial.challenge_result:
        return
    for execution in trial.challenge_result.executions:
        cell = trial.graph_stack.challenge_graph.cells.get(execution.challenge_id)
        if cell is not None:
            state.observations.record(execution, cell.requirement_id)
    existing_packets = {item.counterexample_id for item in state.counterexamples}
    state.counterexamples.extend(
        packet for packet in trial.challenge_result.counterexamples
        if packet.counterexample_id not in existing_packets
    )
    existing_failures = {item.failure_id for item in state.confirmed_failures}
    state.confirmed_failures.extend(
        failure for failure in trial.challenge_result.confirmed_failures
        if failure.failure_id not in existing_failures
    )
    for failure in trial.challenge_result.confirmed_failures:
        history = state.failure_history.setdefault(
            failure.failure_signature,
            FailureHistory(failure.failure_signature),
        )
        if failure.counterexample_id not in history.counterexample_ids:
            history.counterexample_ids.append(failure.counterexample_id)


def apply_transition_decision(
    state: ReachAvoidState,
    trial: TrialTransition,
    store: CheckpointStore,
) -> TransitionCertificate:
    source = state.working_checkpoint
    locked_successes_before = tuple(state.locked_successes)
    controller_state_before = state.to_dict()
    if trial.source_checkpoint_id != source.checkpoint_id:
        raise RuntimeError("transition source is not the current working checkpoint")
    if state.graph_stack.graph_hashes() != source.graph_hashes:
        raise RuntimeError("working GraphStack does not match the transition source checkpoint")
    _record_trial_evidence(state, trial)
    state.transition_counts[trial.decision.value] = (
        state.transition_counts.get(trial.decision.value, 0) + 1
    )
    if trial.decision in {Decision.ADVANCE_SAFE, Decision.KEEP_REPAIRING, Decision.REACHED}:
        closed_failure_ids = set(trial.evidence.confirmed_failures_closed)
        if closed_failure_ids:
            state.confirmed_failures = [
                replace(item, open=False) if item.failure_id in closed_failure_ids else item
                for item in state.confirmed_failures
            ]
            for item in state.confirmed_failures:
                history = state.failure_history.get(item.failure_signature)
                if history is not None and item.failure_id in closed_failure_ids:
                    history.closed = True
        if trial.challenge_result:
            record_locked_passes(
                state, trial.graph_stack, trial.challenge_result.executions,
            )
        if trial.transition_decision.next_objective_kind:
            state.generator_session.conversation.append({
                "role": "system",
                "patch_hash": trial.graph_stack.patch_hash,
                "pending_objective_kind": trial.transition_decision.next_objective_kind,
            })
        state.observations.retain_patch(trial.graph_stack.patch_hash)
        state.last_mechanical_result = trial.evidence.mechanical
        state.atomic_obligations = {
            obligation.key: obligation for obligation in trial.atomic_obligations
        }
        state.atomic_evidence = dict(trial.evidence.atomic_after)
        # A stable trusted FAIL -> PASS belongs to the durable lock set. Future
        # revisions must replay it and a PASS -> FAIL on that same semantic
        # obligation is the only target regression gate.
        for key in trial.evidence.strict_progress_ids:
            item = trial.evidence.atomic_after.get(key)
            if item is not None and item.role == "TARGET" and item.authority in {"A", "B", "C"}:
                if key not in state.locked_successes:
                    state.locked_successes.append(key)
        checkpoint = capture_checkpoint(
            state, trial, store,
            "REACHED" if trial.decision is Decision.REACHED else ("SAFE" if trial.decision is Decision.ADVANCE_SAFE else "WORKING"),
        )
        certificate = build_transition_certificate(
            state, trial, checkpoint,
            locked_successes_before=locked_successes_before,
        )
        update_working_checkpoint(state, checkpoint)
        state.graph_stack = trial.graph_stack
        if trial.decision is Decision.ADVANCE_SAFE:
            update_safe_checkpoint(state, checkpoint)
            update_best_checkpoint(state, checkpoint)
        elif trial.decision is Decision.REACHED:
            update_safe_checkpoint(state, checkpoint)
            update_best_checkpoint(state, checkpoint)
            state.certified_checkpoint = checkpoint
        if trial.decision in {Decision.ADVANCE_SAFE, Decision.REACHED}:
            state.consecutive_evidence_limited_steps = 0
        else:
            state.consecutive_evidence_limited_steps += 1
        if (
            trial.selected_frontier is not None
            and trial.evidence.frontier_delta is not None
            and trial.evidence.frontier_delta.verified_closed
        ):
            old = trial.selected_frontier
            state.repair_frontiers[old.frontier_id] = replace(
                old, status=FrontierStatus.CLOSED,
                closure_evidence=old.closure_evidence + ({
                    "transition": trial.cumulative_diff.patch_hash,
                    "atomic_fail_to_pass": trial.evidence.atomic_fail_to_pass,
                    "progress": trial.evidence.verified_progress,
                },),
            )
        state.checkpoint_history[checkpoint.checkpoint_id] = checkpoint
        state.transition_history.append(certificate)
        result = checkpoint
    else:
        # Rollback restores the source patch and graph stack while retaining
        # the trial's execution evidence and mechanism history in a new
        # content-addressed evidence checkpoint.
        retained_counterexamples = list(state.counterexamples)
        retained_failures = list(state.confirmed_failures)
        retained_failure_history = dict(state.failure_history)
        retained_locked_checks = state.locked_checks
        retained_locked_successes = list(state.locked_successes)
        retained_transition_counts = dict(state.transition_counts)
        retained_transition_history = list(state.transition_history)
        retained_distinct_hashes = set(state.distinct_patch_hashes)
        retained_rejected_hashes = set(state.rejected_patch_hashes)
        retained_revision_count = state.revision_count
        retained_generator_attempt_count = state.generator_attempt_count
        retained_challenge_round_count = state.challenge_round_count
        retained_frontier_attempts = dict(state.frontier_attempts)
        retained_challenge_attempts = dict(state.challenge_attempts)
        retained_generator_session = state.generator_session
        restore_parent_working_checkpoint(state, source, store)
        state.counterexamples = retained_counterexamples
        state.confirmed_failures = retained_failures
        state.failure_history = retained_failure_history
        state.locked_checks = retained_locked_checks
        state.locked_successes = retained_locked_successes
        state.transition_counts = retained_transition_counts
        state.transition_history = retained_transition_history
        state.distinct_patch_hashes = retained_distinct_hashes
        state.rejected_patch_hashes = retained_rejected_hashes
        state.revision_count = retained_revision_count
        state.generator_attempt_count = retained_generator_attempt_count
        state.challenge_round_count = retained_challenge_round_count
        state.frontier_attempts = retained_frontier_attempts
        state.challenge_attempts = retained_challenge_attempts
        state.generator_session = retained_generator_session
        state.observations.retain_patch(source.patch_hash)
        # Trial evidence remains in the transition certificate/history; the
        # incumbent's atomic and mechanical evidence is restored as the live
        # working state, and locked successes are not discarded.
        state.generator_session.conversation.append({
            "role": "system",
            "patch_hash": source.patch_hash,
            "pending_objective_kind": "REPAIR_FRONTIER",
            "rejected_trial_patch_hash": trial.cumulative_diff.patch_hash,
            "rejected_trial_reasons": trial.transition_decision.reasons,
        })
        result = capture_current_graph_checkpoint(state, store, "ROLLBACK_EVIDENCE")
        state.graph_stack.validate()
        certificate = build_transition_certificate(
            state, trial, result,
            locked_successes_before=locked_successes_before,
        )
        update_working_checkpoint(state, result)
        state.checkpoint_history[result.checkpoint_id] = result
        state.rejected_patch_hashes.add(trial.cumulative_diff.patch_hash)
        state.transition_history.append(certificate)
    trial.certificate = certificate
    persist_transition(
        state, trial, certificate, store,
        controller_state_before=controller_state_before,
    )
    verify_transition_certificate(certificate, store)
    return certificate


def persist_transition(
    state: ReachAvoidState,
    trial: TrialTransition,
    certificate: TransitionCertificate,
    store: CheckpointStore,
    *,
    controller_state_before: dict | None = None,
) -> Path:
    transitions = state.run_root / "transitions"
    transitions.mkdir(parents=True, exist_ok=True)
    path = transitions / f"{certificate.transition_id}.json"
    temporary = path.with_suffix(".tmp")
    payload = {
        "schema": "reachpatch-reach-avoid-v2",
        "certificate": certificate.to_dict(),
        "incremental_diff": trial.incremental_diff.to_dict(),
        "cumulative_diff": trial.cumulative_diff.to_dict(),
        "transition_evidence": trial.evidence.to_dict(),
        "executions": [
            item.to_dict() for item in (
                trial.challenge_result.executions if trial.challenge_result else ()
            )
        ],
        "trial_graphs": {
            "requirement": trial.graph_stack.requirement_graph.to_dict(),
            "program": trial.graph_stack.program_graph.to_dict(),
            "binding": trial.graph_stack.binding_graph.to_dict(),
            "challenge": trial.graph_stack.challenge_graph.to_dict(),
        },
    }
    temporary.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    temporary.replace(path)
    # Every evaluated nonempty trial has an independently inspectable evidence
    # directory. The aggregate certificate above remains the restart-safe
    # index, while this layout is deliberately convenient for sealed audit.
    audit_root = transitions / certificate.transition_id
    audit_root.mkdir(parents=True, exist_ok=True)
    incumbent_patch_hash = store.load(certificate.source_checkpoint_id).patch_hash

    def write_json(name: str, value) -> None:
        destination = audit_root / name
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(canonical_json(value) + "\n", encoding="utf-8")
        temporary.replace(destination)

    (audit_root / "incremental.patch").write_text(
        trial.incremental_diff.canonical_diff, encoding="utf-8",
    )
    (audit_root / "cumulative.patch").write_text(
        trial.cumulative_diff.canonical_diff, encoding="utf-8",
    )
    write_json("generator_objective.json", (
        state.current_repair_objective.to_dict()
        if state.current_repair_objective is not None else {}
    ))
    events = list(state.generator_session.attempt_history[-1:])
    (audit_root / "generator_events.jsonl").write_text(
        "".join(canonical_json(item) + "\n" for item in events), encoding="utf-8",
    )
    write_json("selected_frontier_before.json", (
        trial.selected_frontier.to_dict() if trial.selected_frontier is not None else {}
    ))
    selected_after = next((
        frontier for frontier in state.repair_frontiers.values()
        if trial.selected_frontier is not None
        and frontier.semantic_key == trial.selected_frontier.semantic_key
    ), None)
    write_json("selected_frontier_after.json", (
        selected_after.to_dict() if selected_after is not None else {}
    ))
    write_json("atomic_evidence_before.json", {
        key: value.to_dict() for key, value in trial.evidence.atomic_before.items()
    })
    write_json("atomic_evidence_after.json", {
        key: value.to_dict() for key, value in trial.evidence.atomic_after.items()
    })
    write_json("frontier_delta.json", (
        trial.evidence.frontier_delta.to_dict()
        if trial.evidence.frontier_delta is not None else {}
    ))
    write_json("challenge_results.json", {
        "selection": (trial.challenge_result.selected_challenge_ids if trial.challenge_result else ()),
        "executions": [item.to_dict() for item in (
            trial.challenge_result.executions if trial.challenge_result else ()
        )],
        "counterexamples": [item.to_dict() for item in (
            trial.challenge_result.counterexamples if trial.challenge_result else ()
        )],
    })
    write_json("validation_batch.json", {
        "selected_frontier_key": trial.evidence.selected_frontier_key,
        "obligations": [item.to_dict() for item in trial.atomic_obligations],
        "selected_challenge_ids": (
            trial.challenge_result.selected_challenge_ids
            if trial.challenge_result else ()
        ),
    })
    write_json("transition_evidence.json", trial.evidence.to_dict())
    selected_atomic_keys = set()
    if trial.evidence.frontier_delta is not None:
        selected_atomic_keys.update(
            trial.evidence.frontier_delta.before.passed_atomic_keys
            | trial.evidence.frontier_delta.before.failed_atomic_keys
            | trial.evidence.frontier_delta.before.unknown_atomic_keys
            | trial.evidence.frontier_delta.after.passed_atomic_keys
            | trial.evidence.frontier_delta.after.failed_atomic_keys
            | trial.evidence.frontier_delta.after.unknown_atomic_keys
        )
    selected_scenario_keys = sorted({
        scenario_semantic_key(
            requirement_contract_id=obligation.requirement_contract_id,
            role=obligation.role, input_recipe=obligation.input_recipe,
            observation_contract=obligation.oracle_contract,
        )
        for obligation in trial.atomic_obligations
        if obligation.key in selected_atomic_keys
        and obligation.role != "MECHANICAL"
    })
    write_json("transition_decision.json", {
        "decision": trial.decision.value,
        "selected_frontier_key": trial.evidence.selected_frontier_key,
        "selected_frontier_kind": trial.evidence.selected_frontier_kind,
        "selected_scenario_keys": selected_scenario_keys,
        "selected_fail_to_pass": sorted(
            set(trial.evidence.atomic_fail_to_pass).intersection(
                trial.evidence.frontier_delta.before.failed_atomic_keys
                if trial.evidence.frontier_delta is not None else set()
            )
        ),
        "locked_pass_to_fail": list(trial.evidence.locked_targets_lost),
        "verified_progress": list(trial.evidence.verified_progress),
        "material_progress": list(trial.evidence.material_progress),
        "trusted_regressions": list(trial.evidence.trusted_regressions),
        "hard_avoid_violations": list(trial.evidence.hard_avoid_violations),
        "rollback_reasons": list(trial.transition_decision.reasons if trial.decision is Decision.ROLLBACK else ()),
        "incumbent_patch_sha": incumbent_patch_hash,
        "trial_patch_sha": trial.cumulative_diff.patch_hash,
        "resulting_working_patch_sha": state.working_checkpoint.patch_hash,
        "artifact_directory": str(audit_root.relative_to(state.run_root)),
    })
    write_json("controller_state_before.json", controller_state_before or {})
    write_json("controller_state_after.json", state.to_dict())
    write_json("validation_backlog.json", {
        key: item.to_dict() for key, item in state.validation_backlog.items()
    })
    return path
