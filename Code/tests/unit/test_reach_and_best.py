from __future__ import annotations

from dataclasses import replace

from reachpatch.models.evidence import CounterexamplePacket, ExecutableOracle
from reachpatch.models.graphs import ChallengePartition, ChallengeStatus, ImpactCone
from reachpatch.challenge_graph.models import open_high_challenge_ids
from reachpatch.reach_avoid.gates import evaluate_reach


def test_reach_requires_execution_confirmed_binding(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    binding = state.graph_stack.binding_graph.units["binding-target"]
    state.graph_stack.binding_graph.units[binding.binding_id] = replace(
        binding, status="STATIC_ACTIONABLE",
    )
    assert not evaluate_reach(state).reached


def test_reach_requires_locked_preservation(state_factory):
    state = state_factory(
        target_status=ChallengeStatus.PASS,
        preservation_status=ChallengeStatus.PENDING,
        stability_runs=2,
    )
    state.locked_checks.preservation_ids.add("check-preservation")
    assert not evaluate_reach(state).reached


def test_reach_requires_no_open_counterexample(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    state.counterexamples.append(CounterexamplePacket(
        "counterexample", "req-target", "binding-target", "challenge-target",
        state.graph_stack.patch_hash, ("python",), None, (), "oracle-target", "A",
        "calc must return 2", {}, {}, "failure", None, (), ("hunk-calc",),
        (), (), (), (), (),
    ))
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    state.graph_stack.challenge_graph.cells[cell.challenge_id] = replace(
        cell, terminal_status=ChallengeStatus.FAIL,
    )
    assert not evaluate_reach(state).reached


def test_reach_requires_diff_closure(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    state.graph_stack.requirement_graph.challenge_partitions["partition"] = ChallengePartition(
        "partition", "req-target", "BRANCH_FALSE", "x is false",
        "branch", "hunk-calc", "path-calc",
    )
    assert not evaluate_reach(state).reached


def test_untrusted_oracle_cannot_reach(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    state.graph_stack.challenge_graph.cells[cell.challenge_id] = replace(
        cell,
        oracle=ExecutableOracle("guess", "PROVISIONAL", "guess", 2, False),
        authority="PROVISIONAL",
    )
    assert not evaluate_reach(state).reached


def test_reach_succeeds_for_closed_current_four_graph_stack(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    assert evaluate_reach(state).reached


def test_reach_requires_all_impact_consumer_and_public_check_replays(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    state.graph_stack.program_graph.impact_cone = ImpactCone(
        "cone", ("hunk-calc",), (), (), (), (),
        ("missing-reverse",), ("missing-renderer",), ("missing-public-check",),
    )
    state.graph_stack.binding_graph.program_hash = state.graph_stack.program_graph.graph_hash()
    state.graph_stack.challenge_graph.binding_hash = state.graph_stack.binding_graph.graph_hash()
    reach = evaluate_reach(state)
    assert not reach.reached
    assert "changed behavior consumer was not replayed: missing-reverse" in reach.reasons
    assert "changed behavior consumer was not replayed: missing-renderer" in reach.reasons
    assert "changed public check was not replayed: missing-public-check" in reach.reasons


def test_proven_unreachable_partition_does_not_block_reach(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    unreachable = replace(
        cell,
        challenge_id="challenge-unreachable",
        terminal_status=ChallengeStatus.UNREACHABLE,
        trace_bundle_id=None,
        stability_runs=0,
    )
    state.graph_stack.challenge_graph.cells[unreachable.challenge_id] = unreachable
    state.graph_stack.binding_graph.units[cell.binding_id] = replace(
        state.graph_stack.binding_graph.units[cell.binding_id],
        challenge_ids=(cell.challenge_id, unreachable.challenge_id),
    )
    state.graph_stack.challenge_graph.binding_hash = (
        state.graph_stack.binding_graph.graph_hash()
    )
    assert evaluate_reach(state).reached


def test_equivalent_pending_cell_does_not_hide_stable_reach_evidence(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    duplicate = replace(
        cell,
        challenge_id="challenge-pending-duplicate",
        input_recipe=replace(cell.input_recipe, recipe_id="recipe-pending-duplicate"),
        trace_bundle_id=None,
        stability_runs=0,
        terminal_status=ChallengeStatus.PENDING,
    )
    state.graph_stack.challenge_graph.cells[duplicate.challenge_id] = duplicate
    assert evaluate_reach(state).reached
    assert open_high_challenge_ids(
        state.graph_stack.challenge_graph.active_cells()
    ) == ()


def test_distinct_pending_input_still_blocks_reach(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    distinct = replace(
        cell,
        challenge_id="challenge-pending-distinct",
        input_recipe=replace(
            cell.input_recipe,
            recipe_id="recipe-pending-distinct",
            concrete_input={"value": "different"},
        ),
        trace_bundle_id=None,
        stability_runs=0,
        terminal_status=ChallengeStatus.PENDING,
    )
    state.graph_stack.challenge_graph.cells[distinct.challenge_id] = distinct
    assert not evaluate_reach(state).reached
    assert open_high_challenge_ids(
        state.graph_stack.challenge_graph.active_cells()
    ) == (distinct.challenge_id,)
