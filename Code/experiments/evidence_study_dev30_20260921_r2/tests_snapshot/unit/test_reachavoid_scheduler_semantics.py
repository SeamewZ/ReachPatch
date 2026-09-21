from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.legacy_graph

from reachpatch.models.evidence import (
    OutcomeStatus, PairedTraceBundle, PairClassification, RunObservation,
    TraceBundle,
)
from reachpatch.models.graphs import BindingStatus, ChallengeStatus
from reachpatch.models.reach_avoid import (
    AtomicEvidence, AtomicObligation, ChallengeSelection,
)
from reachpatch.reach_avoid.frontier import (
    FrontierStatus, RepairFrontier, RepairFrontierKind,
    derive_repair_frontiers,
)
from reachpatch.reach_avoid.controller import ReachAvoidConfig, ReachAvoidController
from reachpatch.reach_avoid.scheduler import (
    schedule_primary_action, select_next_scheduled_action,
    select_primary_repair_frontier,
)
from reachpatch.reach_avoid.semantics import (
    input_partition_semantic_key, scenario_semantic_key,
    normalize_execution_contract, normalize_target_cell,
)
from reachpatch.models.evidence import ExecutableOracle, ObservationContract
from reachpatch.repair.objective import (
    atomic_obligation_from_validation, validation_obligation_from_challenge,
)
from reachpatch.reach_avoid.transition import (
    build_transition_validation_batch, compute_selected_frontier_delta,
)
from reachpatch.reach_avoid.validation_backlog import (
    ValidationBacklogItem, derive_validation_backlog,
)


def _scheduler_state(*frontiers):
    return SimpleNamespace(
        repair_frontiers={item.frontier_id: item for item in frontiers},
        validation_backlog={},
        frontier_attempts={},
        challenge_attempts={},
        graph_stack=SimpleNamespace(
            patch_hash="working",
            challenge_graph=SimpleNamespace(active_cells=lambda: ()),
        ),
    )


def _behavior_frontier(*, status=FrontierStatus.ACTIONABLE):
    return RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE,
        patch_hash="working",
        graph_revision=1,
        authority="A",
        requirement_ids=("req-target",),
        challenge_ids=("target-scenario",),
        repair_slice_ids=("symbol-target",),
        expected_contract={"observable": "return_value", "expected": 3},
        failure_location={"path": "calc.py", "line": 2},
        requirement_contract_id="contract-target",
        input_partition_id="partition-target",
        source_symbol="symbol-target",
        failure_signature="target-fails",
        status=status,
    )


def _trace(trace_id: str, status: OutcomeStatus) -> TraceBundle:
    return TraceBundle(
        trace_id, "tree", ("python", "check.py"),
        RunObservation(status, 0 if status is OutcomeStatus.PASS else 1, "", "", 0.1),
        ("symbol-calc",), ("path-calc",),
        first_project_frame="calc.py:2", stable_runs=2,
    )


def test_actionable_target_is_not_starved_by_large_impact_backlog():
    target = _behavior_frontier()
    state = _scheduler_state(target)
    state.validation_backlog = {
        str(index): ValidationBacklogItem.create(
            kind="IMPACT_REPLAY", source_symbol=f"consumer_{index}",
            risk_rank=index, authority="A",
        )
        for index in range(100)
    }

    scheduled = schedule_primary_action(state, target)

    assert select_primary_repair_frontier(state) == target
    assert scheduled.action == "REPAIR_PRIMARY"


def test_actionable_target_beats_unrelated_evidence_recovery():
    target = _behavior_frontier()
    localization = RepairFrontier.create(
        kind=RepairFrontierKind.LOCALIZATION_FAILURE,
        patch_hash="working", graph_revision=1, authority="A",
        requirement_ids=("unrelated",), repair_slice_ids=("symbol-other",),
        challenge_ids=("other-scenario",),
        failure_location={"path": "other.py", "line": 4},
        status=FrontierStatus.IN_EVIDENCE_RECOVERY,
    )
    state = _scheduler_state(target, localization)

    assert schedule_primary_action(state).action == "REPAIR_PRIMARY"


def test_primary_recovery_is_bounded_then_allows_evidence_limited_repair():
    primary = _behavior_frontier(status=FrontierStatus.IN_EVIDENCE_RECOVERY)
    state = _scheduler_state(primary)

    assert schedule_primary_action(state, primary).action == "RECOVER_PRIMARY_EVIDENCE"

    state.frontier_attempts[f"recovery:{primary.semantic_key}"] = 2
    assert (
        schedule_primary_action(state, primary).action
        == "REPAIR_EVIDENCE_LIMITED"
    )


def test_primary_recovery_caps_each_paired_stability_run_to_its_wall_budget():
    config = ReachAvoidConfig()

    # Recovery has two baseline and two incumbent runs.  Its 120-second budget
    # therefore becomes a 30-second cap for the selected scenario, while a
    # naturally shorter public check is left unchanged.
    assert config.max_primary_recovery_seconds == 120.0
    assert ReachAvoidController._primary_recovery_scenario_timeout(
        90.0, config.max_primary_recovery_seconds,
    ) == 30.0
    assert ReachAvoidController._primary_recovery_scenario_timeout(5.0, 120.0) == 5.0
    assert ReachAvoidController._primary_recovery_scenario_timeout(5.0, 0.0) is None


def test_validation_backlog_without_repair_frontier_cannot_call_generator():
    state = _scheduler_state()
    state.validation_backlog = {
        "impact": ValidationBacklogItem.create(kind="IMPACT_REPLAY"),
    }

    assert select_next_scheduled_action(state).action == "SEAL"


def test_reach_set_seals_before_any_backlog_work(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)

    assert select_next_scheduled_action(state).action == "SEAL"


def test_semantic_scenario_key_ignores_patch_and_challenge_identity(state_factory):
    state = state_factory()
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    changed_ids = replace(
        cell, challenge_id="trial-challenge", patch_hash="trial-patch",
        input_recipe=replace(
            cell.input_recipe, recipe_id="trial-recipe",
            trace_symbols=cell.input_recipe.trace_symbols + ("trial-node-id",),
        ),
    )

    original = scenario_semantic_key(
        requirement_contract_id=cell.observation_contract.contract_id,
        role="TARGET", input_recipe=cell.input_recipe,
        observation_contract=cell.observation_contract,
    )
    trial = scenario_semantic_key(
        requirement_contract_id=changed_ids.observation_contract.contract_id,
        role="TARGET", input_recipe=changed_ids.input_recipe,
        observation_contract=changed_ids.observation_contract,
    )

    assert original == trial
    assert input_partition_semantic_key(cell.input_recipe) == (
        input_partition_semantic_key(changed_ids.input_recipe)
    )
    changed_input = replace(
        cell.input_recipe, concrete_input={"value": "another partition"},
    )
    assert input_partition_semantic_key(changed_input) != (
        input_partition_semantic_key(cell.input_recipe)
    )


def test_pending_cells_do_not_expand_into_repair_frontiers(state_factory):
    state = state_factory(target_status=ChallengeStatus.PENDING)
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    for index in range(100):
        duplicate = replace(
            cell, challenge_id=f"pending-{index}",
            input_recipe=replace(cell.input_recipe, recipe_id=f"pending-recipe-{index}"),
        )
        state.graph_stack.challenge_graph.cells[duplicate.challenge_id] = duplicate

    frontiers = derive_repair_frontiers(
        state, state.graph_stack.requirement_graph, state.graph_stack.program_graph,
        state.graph_stack.binding_graph, state.graph_stack.challenge_graph,
        state.observations, None,
    )

    assert len(frontiers) <= 2
    assert all(
        item.kind is not RepairFrontierKind.BEHAVIOR_FAILURE
        for item in frontiers.values()
    )


def test_hard_target_binding_gap_becomes_bounded_issue_mismatch(state_factory):
    state = state_factory(target_status=ChallengeStatus.PENDING)
    target = state.graph_stack.challenge_graph.cells.pop("challenge-target")
    state.graph_stack.binding_graph.units.pop(target.binding_id)
    state.graph_stack.binding_graph.gaps = (
        {
            "gap_id": "gap-target",
            "requirement_id": target.requirement_id,
            "gap_type": "NO_CANDIDATE_PATH",
        },
    )
    state.graph_stack.binding_graph.requirement_hash = (
        state.graph_stack.requirement_graph.graph_hash()
    )
    state.graph_stack.challenge_graph.binding_hash = (
        state.graph_stack.binding_graph.graph_hash()
    )

    frontiers = derive_repair_frontiers(
        state, state.graph_stack.requirement_graph, state.graph_stack.program_graph,
        state.graph_stack.binding_graph, state.graph_stack.challenge_graph,
        state.observations, None,
    )

    mismatch = next(
        item for item in frontiers.values()
        if item.kind is RepairFrontierKind.ISSUE_DIFF_MISMATCH
    )
    assert mismatch.actionable
    assert mismatch.requirement_ids == (target.requirement_id,)
    assert mismatch.repair_slice_ids
    assert mismatch.recovery_recipes[0]["gap_ids"] == ("gap-target",)


def test_initial_p0_preservation_difference_is_not_a_repair_frontier(state_factory):
    state = state_factory(
        target_status=ChallengeStatus.PENDING,
        preservation_status=ChallengeStatus.FAIL,
        stability_runs=2,
    )
    cell = state.graph_stack.challenge_graph.cells["challenge-preservation"]
    p0_only = PairedTraceBundle(
        "paired-p0", "preservation-check", cell.challenge_id,
        state.graph_stack.patch_hash, _trace("baseline", OutcomeStatus.PASS),
        _trace("p0", OutcomeStatus.FAIL),
        PairClassification.PRESERVATION_REGRESSION, "oracle", "A",
        "preserve", 2, previous=None,
    )
    state.observations.record(p0_only, cell.requirement_id)

    frontiers = derive_repair_frontiers(
        state, state.graph_stack.requirement_graph, state.graph_stack.program_graph,
        state.graph_stack.binding_graph, state.graph_stack.challenge_graph,
        state.observations, None,
    )

    assert not any(
        item.kind is RepairFrontierKind.PRESERVATION_REGRESSION
        for item in frontiers.values()
    )


def test_trusted_incumbent_to_trial_preservation_failure_becomes_frontier(state_factory):
    state = state_factory(
        target_status=ChallengeStatus.PENDING,
        preservation_status=ChallengeStatus.FAIL,
        stability_runs=2,
    )
    cell = state.graph_stack.challenge_graph.cells["challenge-preservation"]
    trial_regression = PairedTraceBundle(
        "paired-trial", "preservation-check", cell.challenge_id,
        state.graph_stack.patch_hash, _trace("baseline", OutcomeStatus.PASS),
        _trace("trial", OutcomeStatus.FAIL),
        PairClassification.PRESERVATION_REGRESSION, "oracle", "A",
        "preserve", 2, previous=_trace("incumbent", OutcomeStatus.PASS),
    )
    state.observations.record(trial_regression, cell.requirement_id)

    frontiers = derive_repair_frontiers(
        state, state.graph_stack.requirement_graph, state.graph_stack.program_graph,
        state.graph_stack.binding_graph, state.graph_stack.challenge_graph,
        state.observations, None,
    )

    preservation = next(
        item for item in frontiers.values()
        if item.kind is RepairFrontierKind.PRESERVATION_REGRESSION
    )
    assert preservation.authority == "A"
    assert preservation.status is FrontierStatus.ACTIONABLE


def test_coverage_frontier_uses_cell_input_partition_semantics(state_factory):
    state = state_factory(target_status=ChallengeStatus.PENDING)
    binding = state.graph_stack.binding_graph.units["binding-target"]
    state.graph_stack.binding_graph.units[binding.binding_id] = replace(
        binding, alignment_status="DISJOINT",
    )
    state.graph_stack.challenge_graph.binding_hash = (
        state.graph_stack.binding_graph.graph_hash()
    )
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]

    frontiers = derive_repair_frontiers(
        state, state.graph_stack.requirement_graph, state.graph_stack.program_graph,
        state.graph_stack.binding_graph, state.graph_stack.challenge_graph,
        state.observations, None,
    )

    coverage = next(
        item for item in frontiers.values()
        if item.kind is RepairFrontierKind.REQUIREMENT_COVERAGE_GAP
    )
    assert coverage.input_partition_id == input_partition_semantic_key(cell.input_recipe)


def test_equivalent_pending_cells_collapse_in_validation_backlog(state_factory):
    state = state_factory(target_status=ChallengeStatus.PENDING)
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    for index in range(100):
        duplicate = replace(
            cell, challenge_id=f"pending-equivalent-{index}",
            input_recipe=replace(
                cell.input_recipe, recipe_id=f"recipe-equivalent-{index}",
                trace_symbols=cell.input_recipe.trace_symbols
                + (f"temporary-node-{index}",),
            ),
        )
        state.graph_stack.challenge_graph.cells[duplicate.challenge_id] = duplicate

    backlog = derive_validation_backlog(state)
    target_items = [
        item for item in backlog.values()
        if item.requirement_id == cell.requirement_id
        and item.kind == "ADJACENT_PARTITION"
    ]

    assert len(target_items) == 1


def test_validation_backlog_semantic_key_ignores_rank_and_authority():
    low = ValidationBacklogItem.create(
        kind="IMPACT_REPLAY", requirement_id="requirement",
        scenario_key="scenario", source_symbol="module.function",
        risk_rank=1, authority="A",
    )
    reranked = ValidationBacklogItem.create(
        kind="IMPACT_REPLAY", requirement_id="requirement",
        scenario_key="scenario", source_symbol="module.function",
        risk_rank=99, authority="PROVISIONAL",
    )

    assert low.semantic_key == reranked.semantic_key


def test_selected_partition_precedes_adjacent_primary_scenario(state_factory):
    state = state_factory(target_status=ChallengeStatus.FAIL, stability_runs=2)
    selected = state.graph_stack.challenge_graph.cells["challenge-target"]
    adjacent = replace(
        selected, challenge_id="adjacent-first",
        input_recipe=replace(
            selected.input_recipe, recipe_id="adjacent-recipe",
            concrete_input={"value": "adjacent"},
        ),
    )
    state.graph_stack.challenge_graph.cells[adjacent.challenge_id] = adjacent
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.BEHAVIOR_FAILURE,
        patch_hash=state.graph_stack.patch_hash,
        graph_revision=state.graph_stack.revision, authority="A",
        requirement_ids=(selected.requirement_id,),
        binding_ids=(selected.binding_id,), challenge_ids=(selected.challenge_id,),
        repair_slice_ids=("symbol-calc",),
        expected_contract=selected.observation_contract,
        failure_location={"path": "calc.py", "line": 2},
        requirement_contract_id=selected.observation_contract.contract_id,
        input_partition_id=input_partition_semantic_key(selected.input_recipe),
        source_symbol="calc", failure_signature="target-failure",
    )

    batch = build_transition_validation_batch(
        state, frontier, None, trial_stack=state.graph_stack,
        selection=ChallengeSelection(
            (adjacent.challenge_id, selected.challenge_id),
        ),
    )

    assert batch[0].input_partition_id == frontier.input_partition_id


def test_selected_objective_obligation_enters_batch_without_challenge_cell(state_factory):
    state = state_factory(target_status=ChallengeStatus.PENDING)
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.ISSUE_DIFF_MISMATCH,
        patch_hash=state.graph_stack.patch_hash, graph_revision=0,
        requirement_ids=("req-target",), repair_slice_ids=("symbol-calc",),
        requirement_contract_id="contract-target", source_symbol="calc",
        failure_signature="target-binding-gap",
    )
    obligation = AtomicObligation(
        key="selected-obligation", requirement_id="req-target",
        requirement_contract_id="contract-target", role="TARGET",
        input_recipe={"command": ("python", "check.py")},
        authority="PROVISIONAL", source="repair-objective-validation",
    )
    state.current_repair_objective = SimpleNamespace(
        selected_frontier=frontier, atomic_obligations=(obligation,),
    )

    batch = build_transition_validation_batch(
        state, frontier, None, trial_stack=state.graph_stack,
        selection=ChallengeSelection(()),
    )

    assert [item.role for item in batch] == ["TARGET", "MECHANICAL"]
    assert batch[0].key == "selected-obligation"


def test_frontier_delta_ignores_unrelated_atomic_progress():
    frontier = _behavior_frontier()
    selected_key = "selected"
    unrelated_key = "unrelated"
    before = {
        selected_key: AtomicEvidence(
            selected_key, "FAIL", requirement_id="req-target",
            input_partition_id="partition-target", authority="A",
            stability_runs=2,
        ),
        unrelated_key: AtomicEvidence(
            unrelated_key, "FAIL", requirement_id="other",
            input_partition_id="other-partition", authority="A",
            stability_runs=2,
        ),
    }
    after = {
        selected_key: replace(before[selected_key]),
        unrelated_key: replace(before[unrelated_key], status="PASS"),
    }

    delta = compute_selected_frontier_delta(frontier, before, after)

    assert not delta.material_progress
    assert not delta.verified_closed


def test_issue_mismatch_accepts_stable_aligned_structural_evidence_as_material_progress():
    frontier = RepairFrontier.create(
        kind=RepairFrontierKind.ISSUE_DIFF_MISMATCH, patch_hash="working",
        graph_revision=1, authority="PROVISIONAL",
        requirement_ids=("req-target",), repair_slice_ids=("symbol-target",),
        requirement_contract_id="contract-target", source_symbol="calc",
        failure_signature="target-binding-gap",
    )
    before = {
        "target": AtomicEvidence(
            "target", "UNKNOWN", requirement_id="req-target",
            authority="PROVISIONAL", stability_runs=0,
        ),
    }
    after = {
        "target": replace(
            before["target"], stability_runs=2,
            entered_project_code=True, binding_alignment="ALIGNED",
        ),
    }

    delta = compute_selected_frontier_delta(frontier, before, after)

    assert delta.material_progress
    assert not delta.verified_closed
    assert delta.progress_reasons == ("selected mismatch scenario entered project code",)


def test_provisional_behavior_does_not_preempt_target_localization():
    provisional = replace(_behavior_frontier(), authority="PROVISIONAL")
    localization = RepairFrontier.create(
        kind=RepairFrontierKind.LOCALIZATION_FAILURE,
        patch_hash="working", graph_revision=1, authority="A",
        requirement_ids=("req-target",), challenge_ids=("target",),
        repair_slice_ids=("calc",), execution_route=("calc.py:2",),
        failure_location={"path": "calc.py", "line": 2},
        requirement_contract_id="contract-localization",
        input_partition_id="partition-localization",
        source_symbol="calc", failure_signature="disjoint",
    )
    state = _scheduler_state(provisional, localization)

    assert select_primary_repair_frontier(state) == localization


def test_target_success_relation_discards_historical_traceback_expected_value():
    contract = ObservationContract(
        "The following should not fail but instead should return empty lists/arrays:",
        "old traceback", observable="return", comparator="forbidden",
    )
    normalized = normalize_execution_contract(contract, role="TARGET")

    assert normalized.expected == {"exit_code": 0}
    assert normalized.observable == "process"
    assert normalized.normalized_comparator == "EXIT_ZERO"


def test_target_success_text_variants_share_one_typed_contract():
    contracts = tuple(
        normalize_execution_contract(
            ObservationContract(text, "traceback"), role="TARGET",
        )
        for text in ("should not fail", "must not raise", "should succeed")
    )

    assert {item.normalized_comparator for item in contracts} == {"EXIT_ZERO"}
    assert all(item.expected == {"exit_code": 0} for item in contracts)


def test_preservation_contract_is_not_rewritten_as_process_success():
    contract = ObservationContract(
        "existing callers should not fail", "baseline", observable="return",
    )

    assert normalize_execution_contract(contract, role="PRESERVATION") is contract


def test_normalized_target_cell_and_objective_use_same_structured_contract(state_factory):
    state = state_factory(target_status=ChallengeStatus.FAIL, stability_runs=2)
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    bad_contract = ObservationContract(
        "the command should not fail", "historical traceback", comparator="forbidden",
    )
    bad_cell = replace(
        cell,
        observation_contract=bad_contract,
        oracle=ExecutableOracle(
            "oracle-raw", "B", bad_contract.relation,
            "historical traceback", True, (),
        ),
    )
    normalized_cell = normalize_target_cell(bad_cell)
    obligation = validation_obligation_from_challenge(
        normalized_cell, source="test",
    )
    atomic = atomic_obligation_from_validation(obligation)

    assert obligation.expected_observation == {"exit_code": 0}
    assert atomic.oracle_contract.normalized_comparator == "EXIT_ZERO"
    assert atomic.oracle_contract.expected == {"exit_code": 0}
