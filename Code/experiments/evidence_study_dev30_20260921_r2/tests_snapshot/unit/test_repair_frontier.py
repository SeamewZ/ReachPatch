from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.legacy_graph

from reachpatch.models.evidence import ObservationContract, OutcomeStatus, RunObservation
from reachpatch.models.graphs import (
    BindingGraph, BindingStatus, BindingUnit, ChallengeGraph, PathClass,
    ProgramGraph, ProgramNode, ProgramNodeKind, RequirementGraph,
    RequirementLeaf,
)
from reachpatch.reach_avoid.controller import ReachAvoidController
from reachpatch.reach_avoid.frontier import (
    FrontierStatus, NextActionKind, RepairFrontier, RepairFrontierKind,
    derive_repair_frontiers, select_next_action,
)
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.models.reach_avoid import AtomicEvidence, build_frontier_measure


def test_frontier_id_is_stable_and_content_addressed():
    first = RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE, patch_hash="p", graph_revision=1,
        requirement_ids=("r2", "r1"), binding_ids=("b",),
        expected_contract={"relation": " return 2 ", "expected": 2},
        failure_location={"line": 4},
    )
    second = RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE, patch_hash="p", graph_revision=1,
        requirement_ids=("r1", "r2"), binding_ids=("b",),
        expected_contract={"expected": 2, "relation": "return 2"},
        failure_location={"line": 4},
    )
    assert first.frontier_id == second.frontier_id


def test_observation_contract_normalization_is_not_free_text_equality():
    contract = ObservationContract("must equal 2", 2, comparator="equals")
    observation = RunObservation(OutcomeStatus.PASS, 0, "", "", 0.0, value=2)
    assert contract.contract_id
    assert contract.matches(observation)
    assert not ObservationContract("ignored prose", 3, comparator="equals").matches(observation)


def test_action_selector_repairs_frontier_after_challenges():
    class Dummy: pass
    state = Dummy()
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.MECHANICAL_FAILURE, patch_hash="p", graph_revision=0,
        priority=-1, failure_location={"path": "calc.py", "line": 1},
        recovery_recipes=({"stderr": "SyntaxError"},),
    )
    state.repair_frontiers = {frontier.frontier_id: frontier}
    state.graph_stack = Dummy()
    state.graph_stack.patch_hash = "p"
    state.graph_stack.revision = 0
    state.graph_stack.challenge_graph = Dummy()
    state.graph_stack.challenge_graph.active_cells = lambda: ()
    action = select_next_action(state)
    assert action.kind is NextActionKind.REPAIR
    assert action.frontier_id == frontier.frontier_id


def test_behavior_frontier_requires_slice_and_executable_evidence():
    unsupported = RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE, patch_hash="p", graph_revision=0,
        requirement_ids=("requirement",), challenge_ids=("challenge",),
        expected_contract={"expected": 2}, failure_location={"line": 1},
    )
    assert not unsupported.actionable

    supported = replace(unsupported, repair_slice_ids=("symbol-calc",))
    assert supported.actionable


def test_localization_frontier_becomes_editable_only_after_trace_recovery():
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.LOCALIZATION_FAILURE, patch_hash="p", graph_revision=0,
        requirement_ids=("requirement",), challenge_ids=("challenge",),
        repair_slice_ids=("symbol-real-target",),
        execution_route=("target.py:12",),
        first_project_frame="target.py:12",
        failure_location="other.py:5",
        status=FrontierStatus.ACTIONABLE,
    )
    assert frontier.actionable


def test_coverage_measure_requires_stable_project_execution():
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.REQUIREMENT_COVERAGE_GAP,
        patch_hash="p", graph_revision=0, requirement_ids=("requirement",),
        input_partition_id="missing-partition", failure_location="target.py",
    )
    premature = AtomicEvidence(
        "atomic", "UNKNOWN", requirement_id="requirement",
        input_partition_id="missing-partition", stability_runs=1,
        entered_project_code=False,
    )
    assert not build_frontier_measure(frontier, {"atomic": premature}).covered_partition_ids

    executed = replace(
        premature, status="FAIL", stability_runs=2, entered_project_code=True,
    )
    assert build_frontier_measure(
        frontier, {"atomic": executed},
    ).covered_partition_ids == {"missing-partition"}


def test_disjoint_hard_binding_becomes_recoverable_coverage_frontier():
    contract = ObservationContract("equals 2", 2, comparator="equals")
    leaf = RequirementLeaf(
        "req", "TARGET_BEHAVIOR", "FOR_ALL", (), (), (),
        "combine", contract, None, False, "B", ("issue",), (),
        OutcomeStatus.UNKNOWN, True,
    )
    requirement = RequirementGraph({leaf.requirement_id: leaf})
    node = ProgramNode(
        "symbol-combine", ProgramNodeKind.FUNCTION, "target.py",
        "combine", 1, 2, True, {},
    )
    path = PathClass(
        "path-combine", "combine", (), "DIRECT", "RETURN",
        "PURE", node_ids=(node.node_id,),
    )
    program = ProgramGraph("patch", "base", {node.node_id: node}, {}, {
        path.path_class_id: path,
    }, {"target.py": "hash"})
    unit = BindingUnit(
        binding_id="binding-combine",
        requirement_id=leaf.requirement_id,
        path_class_id=path.path_class_id,
        program_symbol_ids=(node.node_id,),
        branch_partition_ids=(),
        changed_hunk_ids=(),
        causal_cut_ids=(),
        impact_cone_ids=(),
        target_check_ids=(),
        preservation_check_ids=(),
        challenge_ids=(),
        trace_bundle_ids=(),
        counterexample_ids=(),
        authority="B",
        status=BindingStatus.STATIC_ACTIONABLE,
        evidence_ids=("issue",),
        alignment_status="DISJOINT",
    )
    binding = BindingGraph(
        "patch", requirement.graph_hash(), program.graph_hash(),
        {unit.binding_id: unit}, (),
    )
    challenge = ChallengeGraph("patch", binding.graph_hash(), {})
    state = SimpleNamespace(
        graph_stack=SimpleNamespace(patch_hash="patch", revision=3),
        locked_checks=SimpleNamespace(preservation_ids=set()),
    )

    frontiers = derive_repair_frontiers(
        state, requirement, program, binding, challenge,
        SimpleNamespace(by_challenge={}), None,
    )

    coverage = next(
        frontier for frontier in frontiers.values()
        if frontier.kind is RepairFrontierKind.REQUIREMENT_COVERAGE_GAP
        and frontier.binding_ids == (unit.binding_id,)
    )
    assert coverage.repair_slice_ids == (node.node_id,)
    assert coverage.path_class_ids == (path.path_class_id,)
    assert coverage.status is FrontierStatus.IN_EVIDENCE_RECOVERY
    assert not coverage.actionable


def test_closed_frontier_state_survives_new_patch_instance(state_factory):
    state = state_factory()
    controller = ReachAvoidController(RepairPlayer(object()))
    controller._refresh_repair_frontiers(state)
    before = next(iter(state.repair_frontiers.values()))
    closed_before = replace(
        before, status=FrontierStatus.CLOSED,
        closure_evidence=({"trusted_atomic_fail_to_pass": True},),
    )
    state.repair_frontiers = {closed_before.frontier_id: closed_before}
    stack = state.graph_stack
    next_patch = "new-working-patch"
    program = replace(stack.program_graph, patch_hash=next_patch)
    binding = replace(
        stack.binding_graph, patch_hash=next_patch, program_hash=program.graph_hash(),
    )
    challenge = replace(
        stack.challenge_graph, patch_hash=next_patch,
        binding_hash=binding.graph_hash(),
        cells={
            cell_id: replace(cell, patch_hash=next_patch)
            for cell_id, cell in stack.challenge_graph.cells.items()
        },
    )
    state.graph_stack = replace(
        stack, patch_hash=next_patch, revision=stack.revision + 1,
        program_graph=program, binding_graph=binding, challenge_graph=challenge,
    )

    controller._refresh_repair_frontiers(state)

    after = next(
        frontier for frontier in state.repair_frontiers.values()
        if frontier.semantic_key == before.semantic_key
        and frontier.patch_hash == next_patch
    )
    assert after.status is FrontierStatus.CLOSED
    assert after.closure_evidence == closed_before.closure_evidence
