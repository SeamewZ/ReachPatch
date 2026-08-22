from __future__ import annotations

from dataclasses import replace
import copy

from reachpatch.models.evidence import ConfirmedFailure, CounterexamplePacket
from reachpatch.models.reach_avoid import (
    ChallengeRoundResult, Decision, MechanicalResult, RegressionItem,
    RegressionPlan, TransitionEvidence,
)
from reachpatch.models.graphs import (
    BindingGraph, BindingStatus, ChallengeGraph, ChallengeStatus, GraphStack,
    ImpactCone, PathClass, ProgramGraph, ProgramNode,
)
from reachpatch.challenge_graph.models import challenge_obligation_key
from reachpatch.reach_avoid.regression import materialize_trial_challenges
from reachpatch.reach_avoid.transition import (
    _pass_sets, compute_transition_evidence,
    decide_reach_avoid_transition,
)


def evidence(**overrides):
    values = dict(
        mechanical=MechanicalResult(True, (), False, False, False, False),
        target_pass_ids_before=(),
        target_pass_ids_after=(),
        hard_pass_ids_before=(),
        hard_pass_ids_after=(),
        confirmed_failures_closed=(),
        counterexamples_closed=(),
        counterexamples_opened=(),
        locked_targets_lost=(),
        target_regressions=(),
        preservation_regressions=(),
        new_executable_frontier=False,
        environment_unknown=False,
        causal_progress_reasons=(),
    )
    values.update(overrides)
    return TransitionEvidence(**values)


def decide(state, item):
    return decide_reach_avoid_transition(state, state.graph_stack, item)


def test_transition_is_not_unconditionally_committed(state_factory):
    assert decide(state_factory(), evidence()).decision is Decision.ROLLBACK


def test_target_progress_commits_working(state_factory):
    result = decide(state_factory(), evidence(target_pass_ids_after=("req",)))
    assert result.decision is Decision.COMMIT_WORKING
    assert result.promote_to_best


def test_target_fixed_with_regression_keeps_provisional(state_factory):
    result = decide(state_factory(), evidence(
        target_pass_ids_after=("req",), preservation_regressions=("challenge",),
    ))
    assert result.decision is Decision.KEEP_PROVISIONAL
    assert result.promote_to_working and not result.promote_to_best
    assert result.next_objective_kind == "PRESERVATION_REGRESSION"


def test_no_progress_with_regression_rolls_back(state_factory):
    assert decide(state_factory(), evidence(
        preservation_regressions=("challenge",),
    )).decision is Decision.ROLLBACK


def test_preservation_hard_pass_without_target_progress_cannot_commit(state_factory):
    state = state_factory()
    result = decide(state, evidence(
        hard_pass_ids_after=("preservation-requirement",),
    ))
    assert result.decision is Decision.ROLLBACK
    assert not result.promote_to_best


def test_no_progress_preservation_regression_is_not_hard_avoid(state_factory):
    result = decide(state_factory(), evidence(
        preservation_regressions=("challenge",),
    ))
    assert result.decision is Decision.ROLLBACK
    assert not result.hard_avoid
    assert result.repairable_regression


def test_causal_progress_keeps_provisional(state_factory):
    result = decide(state_factory(), evidence(
        causal_progress_reasons=("original BindingUnit passes",),
    ))
    assert result.decision is Decision.KEEP_PROVISIONAL
    assert result.causal_progress


def test_locked_target_loss_rolls_back(state_factory):
    result = decide(state_factory(), evidence(
        target_pass_ids_after=("req",), locked_targets_lost=("check",),
    ))
    assert result.decision is Decision.ROLLBACK


def test_target_tradeoff_keeps_same_working_patch_provisional(state_factory):
    result = decide(state_factory(), evidence(
        target_pass_ids_before=("old-target",),
        target_pass_ids_after=("new-target",),
        target_regressions=("old-target-challenge",),
    ))
    assert result.decision is Decision.KEEP_PROVISIONAL
    assert result.next_objective_kind == "TARGET_REGRESSION"


def test_pure_target_regression_rolls_back(state_factory):
    result = decide(state_factory(), evidence(
        target_pass_ids_before=("old-target",),
        target_pass_ids_after=(),
        target_regressions=("old-target-challenge",),
    ))
    assert result.decision is Decision.ROLLBACK


def test_closed_failure_with_target_regression_keeps_provisional(state_factory):
    result = decide(state_factory(), evidence(
        confirmed_failures_closed=("closed-failure",),
        target_regressions=("regressed-target",),
    ))
    assert result.decision is Decision.KEEP_PROVISIONAL
    assert result.next_objective_kind == "TARGET_REGRESSION"


def test_unexecuted_previous_target_is_frontier_not_regression(state_factory):
    result = decide(state_factory(), evidence(
        target_pass_ids_before=("old-target",),
        target_pass_ids_after=(),
        environment_unknown=True,
        new_executable_frontier=True,
    ))
    assert result.decision is Decision.KEEP_PROVISIONAL
    assert result.next_objective_kind == "EXECUTABLE_FRONTIER"


def test_retargeted_recipe_keeps_stable_executable_obligation_identity(state_factory):
    state = state_factory()
    original = state.graph_stack.challenge_graph.cells["challenge-target"]
    retargeted = replace(
        original,
        challenge_id="challenge-retargeted",
        kind="PUBLIC_REPLAY",
        input_recipe=replace(
            original.input_recipe,
            recipe_id="recipe-retargeted",
            kind="PUBLIC_REPLAY",
            source_check_id="retargeted-check",
        ),
        oracle=replace(original.oracle, oracle_id="oracle-retargeted"),
    )
    assert challenge_obligation_key(retargeted) == challenge_obligation_key(original)


def test_duplicate_pending_cell_does_not_hide_stable_obligation_pass(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    passed = state.graph_stack.challenge_graph.cells["challenge-target"]
    pending = replace(
        passed,
        challenge_id="challenge-duplicate-pending",
        input_recipe=replace(passed.input_recipe, recipe_id="recipe-duplicate"),
        trace_bundle_id=None,
        stability_runs=0,
        terminal_status=ChallengeStatus.PENDING,
    )
    state.graph_stack.challenge_graph.cells[pending.challenge_id] = pending
    target_ids, hard_ids = _pass_sets(state.graph_stack)
    assert target_ids == ("req-target",)
    assert hard_ids == ("req-target",)


def test_retargeted_stable_pass_closes_old_failure_and_counterexample(state_factory):
    state = state_factory()
    original = state.graph_stack.challenge_graph.cells["challenge-target"]
    packet = CounterexamplePacket(
        "counterexample-old", original.requirement_id, original.binding_id,
        original.challenge_id, state.graph_stack.patch_hash,
        original.execution_scenario.command, original.input_recipe.concrete_input,
        original.input_recipe.derivation, original.oracle.oracle_id,
        original.oracle.authority, original.oracle.relation,
        {"status": "FAIL"}, {"status": "FAIL"}, "failure-signature",
        {"line": 1}, (original.path_class_id,), original.changed_hunk_ids,
        (), (), (), (), ("causal_edit",),
    )
    failure = ConfirmedFailure(
        "failure-old", original.requirement_id, original.binding_id,
        original.challenge_id, packet.counterexample_id,
        state.graph_stack.patch_hash, packet.failure_signature,
        "causal-component", packet.first_divergence, True, 0,
    )
    state.counterexamples.append(packet)
    state.confirmed_failures.append(failure)
    trial_stack = copy.deepcopy(state.graph_stack)
    trial_stack.patch_hash = "trial-patch"
    trial_stack.program_graph.patch_hash = "trial-patch"
    trial_stack.binding_graph.patch_hash = "trial-patch"
    trial_stack.challenge_graph.patch_hash = "trial-patch"
    retargeted = replace(
        original,
        challenge_id="challenge-retargeted",
        patch_hash="trial-patch",
        kind="PUBLIC_REPLAY",
        input_recipe=replace(
            original.input_recipe,
            recipe_id="recipe-retargeted",
            kind="PUBLIC_REPLAY",
            source_check_id="retargeted-check",
        ),
        oracle=replace(original.oracle, oracle_id="oracle-retargeted"),
        patched_outcome=original.observation_contract.expected,
        trace_bundle_id="paired-retargeted",
        stability_runs=2,
        terminal_status=ChallengeStatus.PASS,
    )
    trial_stack.challenge_graph.cells = {retargeted.challenge_id: retargeted}
    trial_stack.binding_graph.units[original.binding_id] = replace(
        trial_stack.binding_graph.units[original.binding_id],
        status=BindingStatus.TARGET_PASSING,
        challenge_ids=(retargeted.challenge_id,),
        trace_bundle_ids=("paired-retargeted",),
    )
    result = ChallengeRoundResult(
        (retargeted.challenge_id,), (retargeted.challenge_id,), (), (), (),
        trial_stack, (), 0.0, 0,
    )
    transition_evidence = compute_transition_evidence(
        state, trial_stack, result,
        MechanicalResult(True, (), False, False, False, False),
    )
    assert transition_evidence.confirmed_failures_closed == (failure.failure_id,)
    assert transition_evidence.counterexamples_closed == (packet.counterexample_id,)


def _trial_without_old_preservation_obligation(state_factory):
    state = state_factory(
        preservation_status=ChallengeStatus.FAIL, stability_runs=2,
    )
    source = state.graph_stack
    old_cell = source.challenge_graph.cells["challenge-preservation"]
    old_binding = source.binding_graph.units[old_cell.binding_id]
    old_requirement = source.requirement_graph.leaves[old_cell.requirement_id]
    old_cell = replace(
        old_cell,
        authority="C",
        oracle=replace(old_cell.oracle, authority="C"),
    )
    source.requirement_graph.leaves[old_requirement.requirement_id] = replace(
        old_requirement, authority="C",
    )
    source.binding_graph.requirement_hash = source.requirement_graph.graph_hash()
    source.binding_graph.units[old_binding.binding_id] = replace(
        old_binding, authority="C",
    )
    source.challenge_graph.cells[old_cell.challenge_id] = old_cell
    source.challenge_graph.binding_hash = source.binding_graph.graph_hash()
    source.validate()

    packet = CounterexamplePacket(
        "counterexample-preservation", old_cell.requirement_id,
        old_cell.binding_id, old_cell.challenge_id, source.patch_hash,
        old_cell.execution_scenario.command, old_cell.input_recipe.concrete_input,
        old_cell.input_recipe.derivation, old_cell.oracle.oracle_id,
        old_cell.oracle.authority, old_cell.oracle.relation,
        {"exit_code": 0}, {"exit_code": 1}, "preservation-failure",
        {"line": 2}, (old_cell.path_class_id,), old_cell.changed_hunk_ids,
        (), (), (), (old_cell.requirement_id,), ("repair_causal_cut",),
    )
    failure = ConfirmedFailure(
        "failure-preservation", old_cell.requirement_id, old_cell.binding_id,
        old_cell.challenge_id, packet.counterexample_id, source.patch_hash,
        packet.failure_signature, "preservation-component",
        packet.first_divergence, True, 0,
    )
    state.counterexamples.append(packet)
    state.confirmed_failures.append(failure)

    old_node = next(iter(source.program_graph.nodes.values()))
    new_node = ProgramNode(
        "trial-symbol-calc", old_node.kind, old_node.path, old_node.symbol,
        old_node.start_line, old_node.end_line, old_node.editable,
        old_node.metadata,
    )
    old_path = source.program_graph.path_classes[old_cell.path_class_id]
    new_path = PathClass(
        "trial-path-calc", old_path.entrypoint,
        old_path.ordered_guard_outcomes, old_path.dispatch_route,
        old_path.exit_kind, old_path.observed_effect_kind,
        old_path.loop_class, old_path.recursion_class, (new_node.node_id,),
    )
    impact = ImpactCone(
        "trial-impact", ("trial-hunk",), (), (), (), (), (), (), (),
    )
    program = ProgramGraph(
        "trial-patch", source.program_graph.base_commit,
        {new_node.node_id: new_node}, {}, {new_path.path_class_id: new_path},
        dict(source.program_graph.file_hashes), impact_cone=impact,
    )

    target = source.requirement_graph.leaves["req-target"]
    requirement = source.requirement_graph.__class__({target.requirement_id: target})
    target_binding = replace(
        source.binding_graph.units["binding-target"],
        path_class_id=new_path.path_class_id,
        program_symbol_ids=(new_node.node_id,),
        changed_hunk_ids=("trial-hunk",),
    )
    binding = BindingGraph(
        "trial-patch", requirement.graph_hash(), program.graph_hash(),
        {target_binding.binding_id: target_binding},
    )
    target_cell = replace(
        source.challenge_graph.cells["challenge-target"],
        patch_hash="trial-patch", path_class_id=new_path.path_class_id,
        changed_hunk_ids=("trial-hunk",),
    )
    challenge = ChallengeGraph(
        "trial-patch", binding.graph_hash(),
        {target_cell.challenge_id: target_cell},
    )
    trial = GraphStack(
        "trial-patch", 1, requirement, program, binding, challenge,
    )
    trial.validate()
    plan = RegressionPlan(
        (old_cell.challenge_id,), (old_cell.requirement_id,), (),
        ("trial-hunk",),
        (RegressionItem(
            old_cell.requirement_id, "old-impact", old_cell.binding_id,
            old_cell.challenge_id, "trial-hunk",
        ),),
    )
    return state, trial, plan, packet, failure


def test_missing_preservation_obligation_is_retargeted_across_all_four_graphs(
    state_factory,
):
    state, trial, plan, _, failure = _trial_without_old_preservation_obligation(
        state_factory,
    )

    selection = materialize_trial_challenges(state, trial, plan, max_batch=1)

    replay = next(
        cell for cell in trial.challenge_graph.active_cells()
        if cell.origin == "REGRESSION_REPLAY"
    )
    replay_binding = trial.binding_graph.units[replay.binding_id]
    assert selection.challenge_ids == (replay.challenge_id,)
    assert replay.requirement_id in trial.requirement_graph.leaves
    assert replay.path_class_id in trial.program_graph.path_classes
    assert replay_binding.requirement_id == replay.requirement_id
    assert replay_binding.path_class_id == replay.path_class_id
    assert replay_binding.changed_hunk_ids == ("trial-hunk",)
    assert replay.changed_hunk_ids == ("trial-hunk",)
    assert replay.oracle.authority == "C"
    assert replay.terminal_status is ChallengeStatus.PENDING
    assert any(
        item.failure_id == failure.failure_id for item in state.confirmed_failures
    )
    trial.validate()


def test_retargeted_c_preservation_pass_closes_failure_and_commits(state_factory):
    state, trial, plan, packet, failure = _trial_without_old_preservation_obligation(
        state_factory,
    )
    selection = materialize_trial_challenges(state, trial, plan, max_batch=1)
    replay = trial.challenge_graph.cells[selection.challenge_ids[0]]
    passed = replace(
        replay, patched_outcome=replay.observation_contract.expected,
        trace_bundle_id="paired-regression-replay", stability_runs=2,
        terminal_status=ChallengeStatus.PASS,
    )
    unit = replace(
        trial.binding_graph.units[replay.binding_id],
        status=BindingStatus.TARGET_PASSING,
        trace_bundle_ids=("paired-regression-replay",),
    )
    trial.binding_graph.units[unit.binding_id] = unit
    trial.challenge_graph.binding_hash = trial.binding_graph.graph_hash()
    trial.challenge_graph.cells[passed.challenge_id] = passed
    trial.validate()
    result = ChallengeRoundResult(
        selection.challenge_ids, selection.challenge_ids, (), (), (), trial,
        (), 0.0, 0,
    )

    transition_evidence = compute_transition_evidence(
        state, trial, result,
        MechanicalResult(True, (), False, False, False, False),
    )
    decision = decide_reach_avoid_transition(
        state, trial, transition_evidence,
    )

    assert transition_evidence.confirmed_failures_closed == (failure.failure_id,)
    assert transition_evidence.counterexamples_closed == (packet.counterexample_id,)
    assert decision.decision is Decision.COMMIT_WORKING
