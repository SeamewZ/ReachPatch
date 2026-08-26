from dataclasses import replace

from reachpatch.models.reach_avoid import (
    AvoidEvaluation, AvoidKind, CheckpointScore, ReachEvaluation,
    MechanicalResult, TransitionDecision, TransitionEvidence,
)
from reachpatch.reach_avoid.transition import decide_reach_avoid_transition


def _checkpoint(parent, *, applicable=True, patch_hash="trial"):
    return replace(parent, patch_hash=patch_hash, patch_is_applicable=applicable)


def _evidence(**kwargs):
    state = kwargs.pop("state")
    return replace(
        TransitionEvidence(
            mechanical=state.last_mechanical_result or MechanicalResult(True, (), False, False, False, False),
            target_pass_ids_before=(), target_pass_ids_after=(),
            hard_pass_ids_before=(), hard_pass_ids_after=(),
            confirmed_failures_closed=(), counterexamples_closed=(),
            counterexamples_opened=(), locked_targets_lost=(),
            target_regressions=(), preservation_regressions=(),
            new_executable_frontier=False, environment_unknown=False,
            causal_progress_reasons=(), target_failures_closed=(),
            target_counterexamples_closed=(), selected_frontier_key=None,
            selected_frontier_kind=None,
        ),
        **kwargs,
    )


def test_unknown_keeps_repairing(state_factory):
    state = state_factory()
    evidence = _evidence(state=state, environment_unknown=True)
    result = decide_reach_avoid_transition(
        state.working_checkpoint, _checkpoint(state.working_checkpoint), evidence,
        ReachEvaluation(False, (), 1, 0, 0),
        AvoidEvaluation(AvoidKind.NOT_AVOID, (), (), ()),
    )
    assert result.decision is TransitionDecision.KEEP_REPAIRING


def test_partial_progress_advances_safe(state_factory):
    state = state_factory()
    evidence = _evidence(state=state, partial_progress_ids=("x",))
    result = decide_reach_avoid_transition(
        state.working_checkpoint, _checkpoint(state.working_checkpoint), evidence,
        ReachEvaluation(False, (), 1, 0, 0),
        AvoidEvaluation(AvoidKind.NOT_AVOID, (), (), ()),
    )
    assert result.decision is TransitionDecision.ADVANCE_SAFE


def test_duplicate_or_unapplyable_is_rejected(state_factory):
    state = state_factory()
    evidence = _evidence(state=state, is_exact_duplicate_patch=True)
    result = decide_reach_avoid_transition(
        state.working_checkpoint, _checkpoint(state.working_checkpoint), evidence,
        ReachEvaluation(False, (), 1, 0, 0),
        AvoidEvaluation(AvoidKind.NOT_AVOID, (), (), ()),
    )
    assert result.decision is TransitionDecision.REJECT_TRIAL
    result = decide_reach_avoid_transition(
        state.working_checkpoint, _checkpoint(state.working_checkpoint, applicable=False), evidence,
        _evidence(state=state),
        AvoidEvaluation(AvoidKind.HARD_AVOID, ("apply",), (), ()),
    )
    assert result.decision is TransitionDecision.REJECT_TRIAL


def test_reach_has_priority(state_factory):
    state = state_factory()
    result = decide_reach_avoid_transition(
        state.working_checkpoint, _checkpoint(state.working_checkpoint), _evidence(state=state),
        ReachEvaluation(True, (), 1, 1, 1),
        AvoidEvaluation(AvoidKind.NOT_AVOID, (), (), ()),
    )
    assert result.decision is TransitionDecision.REACHED
