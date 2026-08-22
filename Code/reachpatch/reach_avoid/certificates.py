from __future__ import annotations

import dataclasses
import json

from reachpatch.execution.worktree import diff_between
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.reach_avoid import (
    AvoidKind, Decision, ReachAvoidState, StateCheckpoint, TransitionCertificate,
    TransitionEvidence, TrialTransition,
)
from reachpatch.models.evidence import ActualDiff

from .checkpoint import CheckpointStore, record_from_dict


def _certificate_hash(
    certificate: TransitionCertificate,
    executions=(),
    trial_graphs: dict | None = None,
    transition_evidence: dict | TransitionEvidence | None = None,
) -> str:
    body = dataclasses.asdict(certificate)
    body.pop("recomputation_hash", None)
    return content_hash({
        "certificate": body,
        "executions": [item.to_dict() if hasattr(item, "to_dict") else item for item in executions],
        "trial_graphs": trial_graphs or {},
        "transition_evidence": (
            transition_evidence.to_dict()
            if hasattr(transition_evidence, "to_dict") else transition_evidence or {}
        ),
    })


def build_transition_certificate(
    state: ReachAvoidState,
    trial: TrialTransition,
    result_checkpoint: StateCheckpoint,
) -> TransitionCertificate:
    challenge = trial.challenge_result
    cell_requirements = {
        cell.challenge_id: cell.requirement_id
        for cell in trial.graph_stack.challenge_graph.cells.values()
    }
    certificate = TransitionCertificate(
        transition_id=stable_id(
            "transition", state.working_checkpoint.checkpoint_id,
            trial.cumulative_diff.patch_hash, state.repair_revision_count,
        ),
        source_checkpoint_id=state.working_checkpoint.checkpoint_id,
        trial_checkpoint_id=(
            result_checkpoint.checkpoint_id if trial.decision is not Decision.ROLLBACK else None
        ),
        result_checkpoint_id=result_checkpoint.checkpoint_id,
        incremental_diff_hash=trial.incremental_diff.patch_hash,
        cumulative_diff_hash=content_hash(trial.cumulative_diff.canonical_diff),
        trial_patch_hash=trial.cumulative_diff.patch_hash,
        trial_patch_changed=trial.trial_patch_changed,
        before_graph_hashes=state.graph_stack.graph_hashes(),
        trial_graph_hashes=trial.graph_stack.graph_hashes(),
        result_graph_hashes=result_checkpoint.graph_hashes,
        selected_challenge_ids=challenge.selected_challenge_ids if challenge else (),
        executed_challenge_ids=challenge.executed_challenge_ids if challenge else (),
        execution_bundle_ids=tuple(
            item.paired_bundle_id for item in challenge.executions
        ) if challenge else (),
        requirements_improved=tuple(sorted(
            (
                set(trial.evidence.target_pass_ids_after)
                - set(trial.evidence.target_pass_ids_before)
            ) | (
                set(trial.evidence.hard_pass_ids_after)
                - set(trial.evidence.hard_pass_ids_before)
            )
        )),
        requirements_regressed=tuple(sorted({
            cell_requirements[challenge_id]
            for challenge_id in (
                trial.evidence.target_regressions
                + trial.evidence.preservation_regressions
            )
            if challenge_id in cell_requirements
        })),
        bindings_confirmed=tuple(
            binding_id for binding_id, unit in trial.graph_stack.binding_graph.units.items()
            if unit.status.execution_confirmed
        ),
        counterexamples_closed=trial.evidence.counterexamples_closed,
        counterexamples_opened=trial.evidence.counterexamples_opened,
        locked_targets_lost=trial.evidence.locked_targets_lost,
        preservation_regressions=trial.evidence.preservation_regressions,
        hard_avoid_reasons=(trial.avoid.reasons if trial.avoid.kind is AvoidKind.HARD_AVOID else ()),
        progress=trial.progress,
        reach=trial.reach,
        avoid=trial.avoid,
        decision=trial.decision,
        decision_reasons=trial.transition_decision.reasons,
        repair_revision_count_before=max(0, state.repair_revision_count - 1),
        repair_revision_count_after=state.repair_revision_count,
        generator_attempt_count=state.generator_attempt_count,
        challenge_round_count=state.challenge_round_count,
        recomputation_hash="",
    )
    executions = challenge.executions if challenge else ()
    trial_graphs = {
        "requirement": trial.graph_stack.requirement_graph.to_dict(),
        "program": trial.graph_stack.program_graph.to_dict(),
        "binding": trial.graph_stack.binding_graph.to_dict(),
        "challenge": trial.graph_stack.challenge_graph.to_dict(),
    }
    return dataclasses.replace(
        certificate,
        recomputation_hash=_certificate_hash(
            certificate, executions, trial_graphs, trial.evidence,
        ),
    )


def verify_transition_certificate(
    certificate: TransitionCertificate,
    store: CheckpointStore,
) -> None:
    transition_path = (
        store.root.parent / "transitions" / f"{certificate.transition_id}.json"
    )
    if not transition_path.is_file():
        raise FileNotFoundError(transition_path)
    transition = json.loads(transition_path.read_text(encoding="utf-8"))
    if transition.get("schema") != "reachpatch-reach-avoid-v2":
        raise RuntimeError("transition certificate schema mismatch")
    executions = tuple(transition.get("executions", ()))
    trial_graphs = dict(transition.get("trial_graphs", {}))
    evidence_raw = transition.get("transition_evidence")
    if not isinstance(evidence_raw, dict):
        raise RuntimeError("transition evidence is missing")
    evidence = record_from_dict(TransitionEvidence, evidence_raw)
    incremental_raw = transition.get("incremental_diff")
    cumulative_raw = transition.get("cumulative_diff")
    if not isinstance(incremental_raw, dict) or not isinstance(cumulative_raw, dict):
        raise RuntimeError("transition patch evidence is missing")
    incremental_diff = record_from_dict(ActualDiff, incremental_raw)
    cumulative_diff = record_from_dict(ActualDiff, cumulative_raw)
    if incremental_diff.patch_hash != certificate.incremental_diff_hash:
        raise RuntimeError("transition incremental patch evidence mismatch")
    if content_hash(cumulative_diff.canonical_diff) != certificate.cumulative_diff_hash:
        raise RuntimeError("transition cumulative patch evidence mismatch")
    if cumulative_diff.patch_hash != certificate.trial_patch_hash:
        raise RuntimeError("transition trial patch evidence mismatch")
    if _certificate_hash(
        certificate, executions, trial_graphs, evidence,
    ) != certificate.recomputation_hash:
        raise RuntimeError("transition certificate recomputation hash mismatch")
    source = store.load(certificate.source_checkpoint_id)
    result = store.load(certificate.result_checkpoint_id)
    if source.graph_hashes != certificate.before_graph_hashes:
        raise RuntimeError("transition source graph hash mismatch")
    if result.graph_hashes != certificate.result_graph_hashes:
        raise RuntimeError("transition result graph hash mismatch")
    if certificate.decision is Decision.ROLLBACK:
        if (
            result.patch_hash != source.patch_hash
            or result.graph_hashes != source.graph_hashes
            or result.parent_checkpoint_id != source.checkpoint_id
        ):
            raise RuntimeError("rollback certificate does not restore its source patch and graphs")
    elif result.patch_hash != certificate.trial_patch_hash:
        raise RuntimeError("transition result patch hash mismatch")
    if certificate.decision is not Decision.ROLLBACK:
        result_incremental = diff_between(source.snapshot_tree, result.snapshot_tree)
        if result_incremental.patch_hash != certificate.incremental_diff_hash:
            raise RuntimeError("transition incremental patch hash mismatch")
        if content_hash(result.canonical_diff) != certificate.cumulative_diff_hash:
            raise RuntimeError("transition cumulative patch hash mismatch")
    graph_hashes = {}
    for name, value in trial_graphs.items():
        graph_value = dict(value)
        if name == "program":
            for metric in ("files_reparsed", "symbols_expanded", "cache_hits"):
                graph_value.pop(metric, None)
        graph_hashes[name] = content_hash(graph_value)
    if graph_hashes != certificate.trial_graph_hashes:
        raise RuntimeError("transition trial graph hash mismatch")
    observations = store.observations(result.checkpoint_id)
    known_executions = {item.paired_bundle_id for item in observations.by_challenge.values()}
    persisted_execution_ids = {
        str(item.get("paired_bundle_id")) for item in executions
    }
    if persisted_execution_ids != set(certificate.execution_bundle_ids):
        raise RuntimeError("transition execution bundle hash input mismatch")
    missing = set(certificate.execution_bundle_ids) - known_executions
    if missing and certificate.decision is not Decision.ROLLBACK:
        raise RuntimeError(f"transition execution evidence is missing: {sorted(missing)}")
    from .transition import decide_reach_avoid_transition
    recomputed = decide_reach_avoid_transition(None, None, evidence)
    if (
        recomputed.decision is not certificate.decision
        or recomputed.reasons != certificate.decision_reasons
    ):
        raise RuntimeError("transition decision does not recompute from its evidence")
