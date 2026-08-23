from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from reachpatch.challenge_graph.execute import execute_challenge_round
from reachpatch.challenge_graph.models import (
    challenge_obligation_key, open_high_challenge_ids,
)
from reachpatch.execution import (
    apply_generator_result, create_trial_tree, diff_between, run_mechanical_checks,
)
from reachpatch.models.base import stable_id
from reachpatch.models.evidence import (
    ExecutableCheck, PairClassification, PublicEvidence,
)
from reachpatch.models.graphs import ChallengeStatus
from reachpatch.models.reach_avoid import (
    AvoidEvaluation, AvoidKind, ChallengeSelection, CheckpointEvidence, Decision,
    GeneratorResult, ProgressEvaluation, ReachAvoidState, ReachEvaluation,
    StateCheckpoint, TransitionDecision, TransitionEvidence, TrialTransition,
)
from reachpatch.reach_avoid.gates import compare_progress, evaluate_avoid, evaluate_reach
from reachpatch.reach_avoid.graph_stack import (
    latest_graph_metrics, set_graph_metrics, update_graph_stack_after_diff,
)
from reachpatch.reach_avoid.regression import (
    build_diff_conditioned_regression_plan, materialize_trial_challenges,
)


def _public_evidence_from_stack(state: ReachAvoidState) -> PublicEvidence:
    checks: dict[str, ExecutableCheck] = {}
    for cell in state.graph_stack.challenge_graph.active_cells():
        if not cell.execution_scenario.command:
            continue
        binding = state.graph_stack.binding_graph.units.get(cell.binding_id)
        if binding is None:
            continue
        role = "PRESERVATION" if cell.kind == "PRESERVATION" else "TARGET"
        bound_ids = (
            binding.preservation_check_ids
            if role == "PRESERVATION" else binding.target_check_ids
        )
        source_check_id = cell.input_recipe.source_check_id
        if source_check_id and not source_check_id.startswith(
            "retargeted-executable-evidence-"
        ):
            check_ids = (source_check_id,)
        elif bound_ids and any(
            not check_id.startswith("retargeted-executable-evidence-")
            for check_id in bound_ids
        ):
            check_ids = tuple(
                check_id for check_id in bound_ids
                if not check_id.startswith("retargeted-executable-evidence-")
            )
        else:
            check_ids = (stable_id(
                "retargeted-executable-evidence",
                cell.requirement_id,
                role,
                cell.execution_scenario.command,
                cell.execution_scenario.cwd,
                cell.execution_scenario.environment,
                cell.input_recipe.concrete_input,
                cell.oracle.authority,
                cell.oracle.relation,
                cell.oracle.expected,
                cell.oracle.source_evidence_ids,
            ),)
        for check_id in check_ids:
            checks[check_id] = ExecutableCheck(
                check_id=check_id,
                command=cell.execution_scenario.command,
                role=role,
                authority=cell.authority,
                requirement_ids=(cell.requirement_id,),
                symbol_references=tuple(
                    state.graph_stack.program_graph.nodes[item].symbol.split(".")[-1]
                    for item in binding.program_symbol_ids
                    if item in state.graph_stack.program_graph.nodes
                ),
                cwd=cell.execution_scenario.cwd,
                environment=cell.execution_scenario.environment,
                timeout_seconds=cell.execution_scenario.timeout_seconds,
                expected=cell.observation_contract.__class__(
                    relation=cell.oracle.relation,
                    expected=cell.oracle.expected,
                    observable=cell.observation_contract.observable,
                    comparator=cell.observation_contract.comparator,
                ),
                concrete_input=cell.input_recipe.concrete_input,
                source_evidence_ids=cell.oracle.source_evidence_ids,
            )
    return PublicEvidence(checks=tuple(checks.values()))


def _pass_sets(stack):
    cells = tuple(
        cell for cell in stack.challenge_graph.active_cells()
        if cell.oracle.trusted and cell.oracle.executable
    )
    grouped: dict[str, dict[str, list]] = {}
    for cell in cells:
        grouped.setdefault(cell.requirement_id, {}).setdefault(
            challenge_obligation_key(cell), [],
        ).append(cell)

    def closed(requirement_id: str) -> bool:
        obligations = grouped[requirement_id]
        return bool(obligations) and all(
            any(
                cell.terminal_status is ChallengeStatus.UNREACHABLE
                or (
                    cell.terminal_status is ChallengeStatus.PASS
                    and cell.stability_runs >= 2
                )
                for cell in equivalent_cells
            )
            for equivalent_cells in obligations.values()
        )

    target = tuple(sorted(
        requirement_id for requirement_id, obligations in grouped.items()
        if obligations and next(iter(obligations.values()))[0].kind != "PRESERVATION"
        and closed(requirement_id)
    ))
    hard = tuple(sorted(
        requirement_id for requirement_id, obligations in grouped.items()
        if obligations and any(
            cell.hard
            for equivalent_cells in obligations.values()
            for cell in equivalent_cells
        )
        and closed(requirement_id)
    ))
    return target, hard


def compute_transition_evidence(
    state: ReachAvoidState,
    trial_stack,
    challenge_result,
    mechanical,
) -> TransitionEvidence:
    before_target, before_hard = _pass_sets(state.graph_stack)
    after_target, after_hard = _pass_sets(trial_stack)
    classifications = {
        item.challenge_id: item.classification for item in challenge_result.executions
    }
    preservation_regressions = tuple(sorted(
        challenge_id for challenge_id, classification in classifications.items()
        if classification is PairClassification.PRESERVATION_REGRESSION
    ))
    target_regressions = tuple(sorted(
        challenge_id for challenge_id, classification in classifications.items()
        if classification is PairClassification.TARGET_REGRESSED
    ))
    locked_lost = tuple(sorted(
        check_id for check_id in state.locked_checks.target_ids
        if any(
            execution.check_id == check_id
            and execution.classification is PairClassification.TARGET_REGRESSED
            for execution in challenge_result.executions
        )
    ))
    def matching_pass(requirement_id, binding_id, challenge_id):
        old_cell = state.graph_stack.challenge_graph.cells.get(challenge_id)
        old_binding = state.graph_stack.binding_graph.units.get(binding_id)
        return any(
            cell.requirement_id == requirement_id
            and cell.terminal_status is ChallengeStatus.PASS
            and cell.stability_runs >= 2
            and (
                old_cell is None
                or challenge_obligation_key(cell)
                == challenge_obligation_key(old_cell)
            )
            and (
                old_binding is None
                or (
                    (current_binding := trial_stack.binding_graph.units.get(cell.binding_id))
                    is not None
                    and current_binding.status.execution_confirmed
                )
            )
            for cell in trial_stack.challenge_graph.active_cells()
        )

    closed = tuple(
        failure.failure_id for failure in state.confirmed_failures
        if failure.open and failure.patch_hash == state.graph_stack.patch_hash
        and matching_pass(
            failure.requirement_id, failure.binding_id, failure.challenge_id,
        )
    )
    opened = tuple(item.counterexample_id for item in challenge_result.counterexamples)
    old_counterexamples = {
        item.counterexample_id: item for item in state.counterexamples
        if item.patch_hash == state.graph_stack.patch_hash
    }
    closed_counterexamples = tuple(
        counterexample_id for counterexample_id, packet in old_counterexamples.items()
        if matching_pass(packet.requirement_id, packet.binding_id, packet.challenge_id)
    )
    causal_reasons: list[str] = []
    for old in state.confirmed_failures:
        old_binding = state.graph_stack.binding_graph.units.get(old.binding_id)
        downstream = [
            new for new in challenge_result.confirmed_failures
            if new.requirement_id == old.requirement_id
            and new.failure_signature != old.failure_signature
            and (
                new.binding_id != old.binding_id
                or (
                    old_binding is not None
                    and trial_stack.binding_graph.units.get(new.binding_id) is not None
                    and trial_stack.binding_graph.units[new.binding_id].path_class_id
                    != old_binding.path_class_id
                )
            )
        ]
        if downstream:
            causal_reasons.append(
                f"old failure signature disappeared and the same Requirement now fails downstream: {old.failure_id}"
            )
        old_cell = state.graph_stack.challenge_graph.cells.get(old.challenge_id)
        if old_cell is not None:
            matching_partition = [
                cell for cell in trial_stack.challenge_graph.active_cells()
                if cell.requirement_id == old.requirement_id
                and challenge_obligation_key(cell)
                == challenge_obligation_key(old_cell)
            ]
            if matching_partition and all(
                cell.terminal_status is ChallengeStatus.PASS
                and cell.stability_runs >= 2
                for cell in matching_partition
            ):
                causal_reasons.append(f"original failing branch now passes: {old.challenge_id}")
        old_packet = next((
            packet for packet in state.counterexamples
            if packet.counterexample_id == old.counterexample_id
        ), None)
        for new in downstream:
            new_packet = next((
                packet for packet in challenge_result.counterexamples
                if packet.counterexample_id == new.counterexample_id
            ), None)
            old_line = (
                old_packet.first_divergence.get("line")
                if old_packet and isinstance(old_packet.first_divergence, dict) else None
            )
            new_line = (
                new_packet.first_divergence.get("line")
                if new_packet and isinstance(new_packet.first_divergence, dict) else None
            )
            if (
                isinstance(old_line, int) and isinstance(new_line, int)
                and new_line > old_line
                and old_packet is not None
                and old_packet.causal_cut_ids
                and new_packet is not None
                and new_packet.causal_cut_ids
            ):
                causal_reasons.append(
                    f"first divergence moved after the prior causal cut: {old.failure_id}"
                )
    before_bindings = state.graph_stack.binding_graph.units
    after_bindings = trial_stack.binding_graph.units
    for binding_id, before in before_bindings.items():
        after = after_bindings.get(binding_id)
        if after is not None and before.status.value.endswith("FAILING") and after.status.value.endswith("PASSING"):
            causal_reasons.append(f"original BindingUnit passes: {binding_id}")
    for old in state.confirmed_failures:
        old_binding = before_bindings.get(old.binding_id)
        if old_binding is None:
            continue
        related = [
            unit for unit in after_bindings.values()
            if unit.requirement_id == old.requirement_id
            and unit.path_class_id == old_binding.path_class_id
        ]
        if any(unit.status.value.endswith("PASSING") for unit in related) and any(
            unit.status.value.endswith("FAILING") for unit in related
        ):
            causal_reasons.append(
                f"original BindingUnit passes and a downstream BindingUnit fails: {old.binding_id}"
            )
    return TransitionEvidence(
        mechanical=mechanical,
        target_pass_ids_before=before_target,
        target_pass_ids_after=after_target,
        hard_pass_ids_before=before_hard,
        hard_pass_ids_after=after_hard,
        confirmed_failures_closed=closed,
        counterexamples_closed=closed_counterexamples,
        counterexamples_opened=opened,
        locked_targets_lost=locked_lost,
        target_regressions=target_regressions,
        preservation_regressions=preservation_regressions,
        new_executable_frontier=bool(challenge_result.frontiers),
        environment_unknown=any(
            execution.classification is PairClassification.UNKNOWN
            for execution in challenge_result.executions
        ),
        causal_progress_reasons=tuple(causal_reasons),
        target_failures_closed=tuple(sorted(
            failure_id for failure_id in closed
            if any(
                item.failure_id == failure_id
                and not _requirement_is_preservation(state, item.requirement_id)
                for item in state.confirmed_failures
            )
        )),
        target_counterexamples_closed=tuple(sorted(
            counterexample_id for counterexample_id in closed_counterexamples
            if any(
                item.counterexample_id == counterexample_id
                and not _requirement_is_preservation(state, item.requirement_id)
                for item in state.counterexamples
            )
        )),
    )


def decide_reach_avoid_transition(
    before_state: ReachAvoidState,
    trial_graph_stack,
    evidence: TransitionEvidence,
) -> TransitionDecision:
    target_progress = bool(
        set(evidence.target_pass_ids_after) - set(evidence.target_pass_ids_before)
    )
    closed_failures = set(evidence.confirmed_failures_closed)
    target_failure_closed = bool(evidence.target_failures_closed) or bool(before_state and any(
        failure.failure_id in closed_failures
        and not _requirement_is_preservation(before_state, failure.requirement_id)
        for failure in before_state.confirmed_failures
    ))
    if (
        not evidence.target_failures_closed
        and closed_failures
        and before_state is not None
        and not before_state.confirmed_failures
    ):
        # A synthetic transition may carry only the closure ids.  With no
        # requirement record available it is safer to treat that explicit
        # closure as target evidence than to let an unrelated HARD progress
        # field certify the patch.
        target_failure_closed = True
    closed_counterexamples = set(evidence.counterexamples_closed)
    target_counterexample_closed = bool(evidence.target_counterexamples_closed) or bool(before_state and any(
        packet.counterexample_id in closed_counterexamples
        and not _requirement_is_preservation(before_state, packet.requirement_id)
        for packet in before_state.counterexamples
    ))
    # A missing PASS on the new patch can be an unexecuted frontier. Only a
    # stable paired TARGET_REGRESSED execution is a target regression.
    target_lost = bool(evidence.target_regressions)
    # A preservation requirement can be HARD, but an ordinary preservation
    # PASS is not target progress.  Closing a stable, confirmed failure is
    # different: it is evidence that a previously observed defect is gone and
    # therefore is strict progress even when the closed failure is a
    # preservation failure.
    confirmed_failure_closed = bool(evidence.confirmed_failures_closed)
    counterexample_closed = bool(evidence.counterexamples_closed)
    strict_progress = bool(
        target_progress
        or target_failure_closed
        or target_counterexample_closed
        or confirmed_failure_closed
        or counterexample_closed
    )
    causal_progress = bool(evidence.causal_progress_reasons)
    regression = bool(evidence.preservation_regressions)
    hard_avoid_reasons = []
    rollback_reasons = []
    if not evidence.mechanical.passed:
        hard_avoid_reasons.extend(evidence.mechanical.failure_reasons)
    if evidence.locked_targets_lost:
        hard_avoid_reasons.append("locked target lost")
    if evidence.mechanical.unsafe_api_break or evidence.mechanical.high_risk_side_effect:
        hard_avoid_reasons.append("unsafe API break or high-risk side effect")
    if not target_progress and regression:
        rollback_reasons.append("no target progress with new preservation regression")
    if target_lost and not strict_progress and not causal_progress:
        rollback_reasons.append("target behavior regressed without compensating progress")
    if hard_avoid_reasons or rollback_reasons:
        decision = Decision.ROLLBACK
        next_kind = None
    elif target_lost and (target_progress or strict_progress or causal_progress):
        decision = Decision.KEEP_PROVISIONAL
        next_kind = "TARGET_REGRESSION"
    elif target_progress and regression:
        decision = Decision.KEEP_PROVISIONAL
        next_kind = "PRESERVATION_REGRESSION"
    elif strict_progress and not regression:
        decision = Decision.COMMIT_WORKING
        next_kind = None
    elif causal_progress and not regression:
        decision = Decision.KEEP_PROVISIONAL
        next_kind = "DOWNSTREAM_TARGET_FAILURE"
    elif evidence.environment_unknown and evidence.new_executable_frontier:
        decision = Decision.KEEP_PROVISIONAL
        next_kind = "EXECUTABLE_FRONTIER"
    else:
        decision = Decision.ROLLBACK
        next_kind = None
    if hard_avoid_reasons or rollback_reasons:
        reasons = tuple(hard_avoid_reasons + rollback_reasons)
    elif decision is Decision.KEEP_PROVISIONAL and target_lost:
        reasons = ("evidence-grounded progress with a repairable target regression",)
    elif decision is Decision.KEEP_PROVISIONAL and regression:
        reasons = ("target progress with executable preservation regression",)
    elif decision is Decision.COMMIT_WORKING:
        reasons = ("target failure closure or target pass without regression",)
    elif causal_progress:
        reasons = ("causal progress without regression",)
    elif evidence.environment_unknown and evidence.new_executable_frontier:
        reasons = ("environment remains unknown with a new executable frontier",)
    else:
        reasons = ("no evidence-grounded progress",)
    return TransitionDecision(
        decision=decision,
        reasons=reasons,
        strict_progress=strict_progress,
        causal_progress=causal_progress,
        hard_avoid=bool(hard_avoid_reasons),
        repairable_regression=(
            regression
        ) or (
            target_lost and (target_progress or strict_progress or causal_progress)
        ),
        promote_to_working=decision in {Decision.COMMIT_WORKING, Decision.KEEP_PROVISIONAL},
        promote_to_best=decision is Decision.COMMIT_WORKING,
        next_objective_kind=next_kind,
    )


def _requirement_is_preservation(
    state: ReachAvoidState,
    requirement_id: str,
) -> bool:
    leaf = state.graph_stack.requirement_graph.leaves.get(requirement_id)
    return bool(leaf and leaf.preservation)


def _virtual_state(
    state: ReachAvoidState,
    stack,
    cumulative_diff,
    mechanical,
    challenge_result,
):
    evidence = CheckpointEvidence(
        mechanical_pass=mechanical.passed,
        no_known_preservation_regression=not any(
            item.classification is PairClassification.PRESERVATION_REGRESSION
            for item in challenge_result.executions
        ),
        confirmed_target_pass_count=len(_pass_sets(stack)[0]),
        closed_confirmed_failure_count=0,
        execution_confirmed_requirement_count=len({
            unit.requirement_id for unit in stack.binding_graph.units.values()
            if unit.status.execution_confirmed
        }),
        execution_confirmed_binding_count=sum(
            unit.status.execution_confirmed for unit in stack.binding_graph.units.values()
        ),
        open_high_challenge_count=len(open_high_challenge_ids(
            stack.challenge_graph.active_cells()
        )),
        open_counterexample_count=len(challenge_result.counterexamples),
    )
    checkpoint = replace(
        state.working_checkpoint,
        patch_hash=cumulative_diff.patch_hash,
        canonical_diff=cumulative_diff.canonical_diff,
        graph_hashes=stack.graph_hashes(),
        evidence=evidence,
    )
    return replace(
        state,
        graph_stack=stack,
        working_checkpoint=checkpoint,
        counterexamples=state.counterexamples + list(challenge_result.counterexamples),
        confirmed_failures=state.confirmed_failures + list(challenge_result.confirmed_failures),
    )


def _validate_repair_scope(state: ReachAvoidState, incremental, mechanical):
    objective = state.current_repair_objective
    if objective is None or not objective.editable_source_slices:
        return mechanical
    allowed: dict[str, list[tuple[int, int]]] = {}
    for item in objective.editable_source_slices:
        path = str(item.get("path", ""))
        if not path:
            continue
        allowed.setdefault(path, []).append((
            int(item.get("start_line", 1)), int(item.get("end_line", 10**9)),
        ))
    causal_nodes: set[str] = set()
    for cut in objective.causal_cuts:
        causal_nodes.update(
            str(node_id) for key in (
                "observation_node_id", "earliest_editable_node_id",
            )
            for node_id in (cut.get(key),) if node_id
        )
        causal_nodes.update(
            str(node_id) for key in (
                "responsible_node_ids", "preservation_consumer_ids",
            )
            for node_id in cut.get(key, ())
        )
    for key in (
        "failing_path_symbols", "protected_target_path_symbols",
        "target_only_path_symbols", "failure_only_path_symbols",
        "shared_changed_symbols", "causal_cut_symbols",
    ):
        causal_nodes.update(
            str(item["node_id"])
            for item in objective.causal_guidance.get(key, ())
            if item.get("node_id")
        )
    causal_nodes.update(
        str(node_id)
        for binding in objective.bindings
        for node_id in binding.get("program_symbol_ids", ())
    )
    causal_windows = [
        (node.path, node.start_line, node.end_line)
        for node_id in causal_nodes
        if (node := state.graph_stack.program_graph.nodes.get(node_id)) is not None
    ]
    if not causal_windows:
        causal_windows = [
            (path, start, end)
            for path, spans in allowed.items()
            for start, end in spans
        ]
    touched_causal_component = False
    for hunk in incremental.hunks:
        hunk_end = hunk.new_start + max(hunk.new_count, 1) - 1
        if any(
            hunk.path == path and hunk_end >= start and hunk.new_start <= end
            for path, start, end in causal_windows
        ):
            touched_causal_component = True
    violations = []
    if incremental.hunks and not touched_causal_component:
        violations.append("revision does not touch the selected causal component")
    if not violations:
        return mechanical
    return replace(
        mechanical,
        passed=False,
        forbidden_edit=True,
        failure_reasons=tuple(dict.fromkeys(mechanical.failure_reasons + tuple(violations))),
    )


def _public_check_paths(state: ReachAvoidState) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        argument.replace("\\", "/").removeprefix("./")
        for cell in state.graph_stack.challenge_graph.active_cells()
        if cell.origin == "PUBLIC_CHECK"
        for argument in cell.execution_scenario.command
        if argument.endswith(".py")
    ))


def evaluate_trial_transition(
    state: ReachAvoidState,
    generator_result: GeneratorResult,
) -> TrialTransition:
    source_checkpoint = state.working_checkpoint
    empty = diff_between(source_checkpoint.snapshot_tree, source_checkpoint.snapshot_tree)
    try:
        trial_tree = create_trial_tree(source_checkpoint, state.run_root / "trials")
        apply_generator_result(trial_tree, generator_result)
    except (OSError, RuntimeError, ValueError) as exc:
        mechanical = run_mechanical_checks(Path(source_checkpoint.snapshot_tree), empty)
        mechanical = replace(mechanical, passed=False, failure_reasons=(str(exc),))
        evidence = TransitionEvidence(
            mechanical, (), (), (), (), (), (), (), (), (), (), False, False, (),
        )
        decision = decide_reach_avoid_transition(state, state.graph_stack, evidence)
        progress = ProgressEvaluation(False, False, 0, 0, (), (), ())
        reach = evaluate_reach(state)
        avoid = AvoidEvaluation(AvoidKind.HARD_AVOID, (str(exc),), (), ())
        state.graph_stack.validate()
        return TrialTransition(
            source_checkpoint.checkpoint_id, None, empty, empty,
            state.graph_stack, None, evidence, progress, reach, avoid,
            decision, trial_patch_changed=False, entered_evaluation=False,
        )
    incremental = diff_between(source_checkpoint.snapshot_tree, trial_tree)
    cumulative = diff_between(state.base_repository, trial_tree)
    if incremental.empty or cumulative.empty or cumulative.patch_hash == state.graph_stack.patch_hash:
        mechanical = run_mechanical_checks(trial_tree, cumulative)
        evidence = TransitionEvidence(
            mechanical, (), (), (), (), (), (), (), (), (), (), False, False, (),
        )
        decision = replace(
            decide_reach_avoid_transition(state, state.graph_stack, evidence),
            decision=Decision.ROLLBACK,
            reasons=("empty or identical cumulative patch",),
            promote_to_working=False,
            promote_to_best=False,
        )
        progress = ProgressEvaluation(False, False, 0, 0, (), (), ())
        reach = evaluate_reach(state)
        avoid = AvoidEvaluation(AvoidKind.NOT_AVOID, (), (), ())
        state.graph_stack.validate()
        return TrialTransition(
            source_checkpoint.checkpoint_id, str(trial_tree), incremental, cumulative,
            state.graph_stack, None, evidence, progress, reach, avoid, decision,
            trial_patch_changed=False, entered_evaluation=False,
        )
    mechanical = run_mechanical_checks(
        trial_tree, cumulative, oracle_paths=_public_check_paths(state),
        source_tree=Path(source_checkpoint.snapshot_tree),
    )
    mechanical = _validate_repair_scope(state, incremental, mechanical)
    if not mechanical.passed:
        evidence = TransitionEvidence(
            mechanical, (), (), (), (), (), (), (), (), (), (), False, False, (),
        )
        decision = decide_reach_avoid_transition(state, state.graph_stack, evidence)
        progress = ProgressEvaluation(False, False, 0, 0, (), (), ())
        reach = ReachEvaluation(False, mechanical.failure_reasons, 0, 0, 0)
        avoid = AvoidEvaluation(AvoidKind.HARD_AVOID, mechanical.failure_reasons, (), ())
        state.graph_stack.validate()
        return TrialTransition(
            source_checkpoint.checkpoint_id, str(trial_tree), incremental, cumulative,
            state.graph_stack, None, evidence, progress, reach, avoid, decision,
            trial_patch_changed=True, entered_evaluation=True,
        )
    public_evidence = _public_evidence_from_stack(state)
    trial_stack = update_graph_stack_after_diff(
        state.graph_stack, cumulative, trial_tree, state.base_repository, "",
        public_evidence, state.graph_budget,
    )
    diff_graph_metrics = latest_graph_metrics()
    diff_program = trial_stack.program_graph
    impact = trial_stack.program_graph.impact_cone
    plan = build_diff_conditioned_regression_plan(state, cumulative, impact)
    transient = replace(state, graph_stack=trial_stack)
    selection = materialize_trial_challenges(state, trial_stack, plan)
    challenge_result = execute_challenge_round(
        transient, selection, state.base_repository, trial_tree,
        previous_tree=Path(source_checkpoint.snapshot_tree),
    )
    execution_graph_metrics = latest_graph_metrics()
    set_graph_metrics({
        key: diff_graph_metrics.get(key, 0.0)
        + execution_graph_metrics.get(key, 0.0)
        for key in diff_graph_metrics
    })
    execution_program = challenge_result.updated_graph_stack.program_graph
    combined_program = replace(
        execution_program,
        files_reparsed=(
            diff_program.files_reparsed + execution_program.files_reparsed
        ),
        symbols_expanded=(
            diff_program.symbols_expanded + execution_program.symbols_expanded
        ),
        cache_hits=diff_program.cache_hits + execution_program.cache_hits,
    )
    trial_stack = replace(
        challenge_result.updated_graph_stack,
        program_graph=combined_program,
    )
    challenge_result = replace(
        challenge_result, updated_graph_stack=trial_stack,
    )
    evidence = compute_transition_evidence(state, trial_stack, challenge_result, mechanical)
    placeholder = TrialTransition(
        source_checkpoint.checkpoint_id, str(trial_tree), incremental, cumulative,
        trial_stack, challenge_result, evidence,
        ProgressEvaluation(False, False, 0, 0, (), (), ()),
        ReachEvaluation(False, (), 0, 0, 0),
        AvoidEvaluation(AvoidKind.NOT_AVOID, (), (), ()),
        TransitionDecision(Decision.ROLLBACK, (), False, False, False, False, False, False, None),
        trial_patch_changed=True,
        entered_evaluation=True,
    )
    progress = compare_progress(state, placeholder)
    decision = decide_reach_avoid_transition(state, trial_stack, evidence)
    placeholder.progress = progress
    placeholder.transition_decision = decision
    placeholder.avoid = evaluate_avoid(state, placeholder)
    virtual = _virtual_state(state, trial_stack, cumulative, mechanical, challenge_result)
    placeholder.reach = evaluate_reach(virtual)
    trial_stack.validate()
    return placeholder
