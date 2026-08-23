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

from .certificates import build_transition_certificate, verify_transition_certificate
from .checkpoint import (
    CheckpointStore, capture_checkpoint, capture_current_graph_checkpoint,
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
    if trial.source_checkpoint_id != source.checkpoint_id:
        raise RuntimeError("transition source is not the current working checkpoint")
    if state.graph_stack.graph_hashes() != source.graph_hashes:
        raise RuntimeError("working GraphStack does not match the transition source checkpoint")
    _record_trial_evidence(state, trial)
    state.transition_counts[trial.decision.value] = (
        state.transition_counts.get(trial.decision.value, 0) + 1
    )
    if trial.decision in {Decision.COMMIT_WORKING, Decision.KEEP_PROVISIONAL}:
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
        checkpoint = capture_checkpoint(
            state, trial, store,
            "WORKING" if trial.decision is Decision.COMMIT_WORKING else "PROVISIONAL",
        )
        certificate = build_transition_certificate(state, trial, checkpoint)
        state.working_checkpoint = checkpoint
        state.graph_stack = trial.graph_stack
        if trial.decision is Decision.COMMIT_WORKING:
            state.certified_checkpoint = checkpoint
        state.checkpoint_history[checkpoint.checkpoint_id] = checkpoint
        if trial.transition_decision.promote_to_best and (
            checkpoint.evidence.rank() > state.best_checkpoint.evidence.rank()
        ):
            state.best_checkpoint = checkpoint
        result = checkpoint
    else:
        # Rollback restores the source patch and graph stack while retaining
        # the trial's execution evidence and mechanism history in a new
        # content-addressed evidence checkpoint.
        state.graph_stack = store.graph_stack(source)
        state.observations.retain_patch(source.patch_hash)
        state.generator_session.conversation.append({
            "role": "system",
            "patch_hash": source.patch_hash,
            "pending_objective_kind": "CONFIRMED_FAILURE",
            "rejected_trial_patch_hash": trial.cumulative_diff.patch_hash,
            "rejected_trial_reasons": trial.transition_decision.reasons,
        })
        result = capture_current_graph_checkpoint(state, store, "ROLLBACK_EVIDENCE")
        state.graph_stack.validate()
        certificate = build_transition_certificate(state, trial, result)
        state.working_checkpoint = result
        state.checkpoint_history[result.checkpoint_id] = result
    trial.certificate = certificate
    persist_transition(state, trial, certificate, store)
    verify_transition_certificate(certificate, store)
    return certificate


def persist_transition(
    state: ReachAvoidState,
    trial: TrialTransition,
    certificate: TransitionCertificate,
    store: CheckpointStore,
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
    return path
