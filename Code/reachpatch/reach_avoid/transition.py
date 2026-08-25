from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from reachpatch.challenge_graph.execute import execute_challenge_round
from reachpatch.challenge_graph.models import (
    challenge_obligation_key, open_high_challenge_ids,
)
from reachpatch.execution import (
    apply_generator_result, create_trial_tree, diff_between, execute_transition_triplet,
    run_mechanical_checks,
)
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.evidence import (
    ExecutableCheck, ExecutableOracle, ObservationContract, PairClassification, PublicEvidence,
)
from reachpatch.models.graphs import ChallengeCell, ChallengeGraph, ChallengeStatus, ExecutableScenario
from reachpatch.models.reach_avoid import (
    AtomicEvidence, AtomicObligation, AvoidEvaluation, AvoidKind, ChallengeSelection, CheckpointEvidence, Decision,
    FrontierDelta, build_frontier_measure,
    GeneratorResult, ProgressEvaluation, ReachAvoidState, ReachEvaluation,
    StateCheckpoint, TransitionDecision, TransitionEvidence, TrialTransition,
    TransitionTraceBundle, atomic_obligation_key, normalize_input_recipe_semantics,
)
from reachpatch.reach_avoid.gates import compare_progress, evaluate_avoid, evaluate_reach
from reachpatch.reach_avoid.graph_stack import (
    latest_graph_metrics, set_graph_metrics, update_graph_stack_after_diff,
)
from reachpatch.reach_avoid.regression import (
    build_diff_conditioned_regression_plan, materialize_trial_challenges,
)


def _tag_selected_frontier(evidence: TransitionEvidence, frontier) -> TransitionEvidence:
    """Keep frontier identity in every transition branch, including rejects."""
    if frontier is None:
        return evidence
    return replace(
        evidence,
        selected_frontier_key=getattr(frontier, "semantic_key", None),
        selected_frontier_kind=getattr(
            getattr(frontier, "kind", None), "value",
            str(getattr(frontier, "kind", "")),
        ),
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


def _frontier_kind_name(frontier) -> str:
    value = getattr(frontier, "kind", "")
    return getattr(value, "value", str(value))


def _frontier_matches_cell(frontier, cell, stack) -> bool:
    if frontier is None:
        return False
    requirement_ids = set(getattr(frontier, "requirement_ids", ()))
    binding_ids = set(getattr(frontier, "binding_ids", ()))
    path_ids = set(getattr(frontier, "path_class_ids", ()))
    unit = stack.binding_graph.units.get(cell.binding_id)
    return bool(
        cell.requirement_id in requirement_ids
        or cell.binding_id in binding_ids
        or cell.path_class_id in path_ids
        or (unit is not None and unit.path_class_id in path_ids)
    )


def _atomic_obligation_from_cell(cell, stack) -> AtomicObligation:
    leaf = stack.requirement_graph.leaves.get(cell.requirement_id)
    contract_id = getattr(
        getattr(leaf, "expected_observation", None), "contract_id", None,
    ) or cell.observation_contract.contract_id
    role = (
        "PRESERVATION" if cell.kind == "PRESERVATION"
        else "IMPACT" if cell.kind == "IMPACT"
        else "TARGET"
    )
    semantic_recipe = {
        "kind": cell.input_recipe.kind,
        "concrete_input": cell.input_recipe.concrete_input,
        "derivation": cell.input_recipe.derivation,
        "command": cell.execution_scenario.command,
        "cwd": cell.execution_scenario.cwd,
        "environment": cell.execution_scenario.environment,
        "timeout_seconds": cell.execution_scenario.timeout_seconds,
        "backend": "shared-executor",
    }
    partition_id = stable_id(
        "input-partition", normalize_input_recipe_semantics({
            "concrete_input": cell.input_recipe.concrete_input,
            "derivation": cell.input_recipe.derivation,
            "kind": cell.input_recipe.kind,
        }),
    )
    # A cell's resolved oracle is the executable authority for a transition.
    # Requirement prose can describe a return value while a public check
    # establishes the same requirement through exit status.  Carrying the
    # prose contract here would make a passing public assertion look like an
    # atomic failure.
    oracle_expected = cell.oracle.expected
    if isinstance(oracle_expected, dict) and set(oracle_expected) == {"exit_code"} and oracle_expected["exit_code"] == 0:
        atomic_contract = ObservationContract(
            relation=cell.oracle.relation, expected=0, observable="exit_code",
            comparator="EXIT_ZERO",
        )
    elif isinstance(oracle_expected, dict) and any(
        key in oracle_expected for key in ("exit_code", "stdout", "stderr", "value", "exception")
    ):
        atomic_contract = ObservationContract(
            relation=cell.oracle.relation, expected=oracle_expected, observable="process",
            comparator="EQUALS",
        )
    else:
        comparator = cell.observation_contract.normalized_comparator
        atomic_contract = ObservationContract(
            relation=cell.oracle.relation, expected=oracle_expected,
            observable=cell.observation_contract.observable,
            comparator=(comparator if comparator != "RELATION_HOLDS" else "EQUALS"),
        )
    raw = AtomicObligation(
        key="", requirement_id=cell.requirement_id,
        requirement_contract_id=contract_id, role=role, input_recipe=semantic_recipe,
        input_partition_id=partition_id, oracle_contract=atomic_contract,
        authority=cell.oracle.authority if cell.oracle.authority in {"A", "B", "C", "PROVISIONAL"} else "PROVISIONAL",
        hard=cell.hard, source=f"challenge:{cell.challenge_id}",
    )
    return replace(raw, key=atomic_obligation_key(raw))


def _transition_atomic_obligations(state, trial_stack, selection, selected_frontier):
    """Return the bounded validation set in the required execution order.

    The selected frontier must be evaluated before locks and impact replays.
    Duplicates are removed by the patch-independent atomic semantic key.
    """
    selected_ids = set(getattr(selection, "challenge_ids", ()))
    candidates = [
        cell for cell in trial_stack.challenge_graph.active_cells()
        if cell.challenge_id in selected_ids and cell.execution_scenario.command
    ]
    candidates.sort(key=lambda cell: (
        not _frontier_matches_cell(selected_frontier, cell, trial_stack),
        cell.kind == "PRESERVATION",
        cell.challenge_id,
    ))
    locked_ids = state.locked_checks.target_ids | state.locked_checks.preservation_ids
    for cell in trial_stack.challenge_graph.active_cells():
        if not cell.execution_scenario.command:
            continue
        if cell.input_recipe.source_check_id in locked_ids and cell not in candidates:
            candidates.append(cell)
    obligations: list[AtomicObligation] = []
    seen: set[str] = set()
    for cell in candidates:
        obligation = _atomic_obligation_from_cell(cell, trial_stack)
        if obligation.key in seen:
            continue
        seen.add(obligation.key)
        obligations.append(obligation)
    # Registered probes are durable state, not tool-call transcripts.  When a
    # probe is linked to this frontier it must be re-run in the controller's
    # triplet and compared alongside the selected challenge.
    selected_key = getattr(selected_frontier, "semantic_key", "")
    for registration in state.probe_registrations.values():
        if registration.linked_frontier_key != selected_key:
            continue
        obligation = registration.atomic_obligation
        if obligation.key in seen:
            continue
        seen.add(obligation.key)
        obligations.append(obligation)
    # A mechanical validation is always present in the transition evidence; it
    # is synthesized from run_mechanical_checks rather than a shell scenario.
    mechanical = AtomicObligation(
        key="", requirement_id="__mechanical__",
        requirement_contract_id="PATCH_APPLIES_AND_IMPORTS", role="MECHANICAL",
        oracle_contract=None, authority="A", hard=True, source="mechanical-check",
    )
    obligations.append(replace(mechanical, key=atomic_obligation_key(mechanical)))
    return tuple(obligations)


def _materialize_registered_probes(state, trial_stack, selected_frontier):
    """Promote linked probe registrations into the local trial graphs.

    The probe was written outside the repository and therefore cannot be a
    patch edit.  Its contract, route and binding instead become a normal
    ChallengeCell on the *trial* graph, where materialization and transition
    evaluation can consume it without mutating the incumbent graph.
    """
    selected_key = getattr(selected_frontier, "semantic_key", "")
    registrations = tuple(
        item for item in state.probe_registrations.values()
        if item.linked_frontier_key == selected_key and item.input_recipe.command
    )
    if not registrations:
        return trial_stack
    cells = dict(trial_stack.challenge_graph.cells)
    units = dict(trial_stack.binding_graph.units)
    changed = False
    for registration in registrations:
        binding_id = registration.binding_id
        if binding_id not in units:
            binding_id = next((item for item in getattr(selected_frontier, "binding_ids", ())
                               if item in units), None)
        if binding_id is None:
            # The evidence remains in AtomicEvidence, but a probe without a
            # binding cannot masquerade as a graph-backed validation.
            continue
        binding = units[binding_id]
        path_class_id = registration.path_class_id or binding.path_class_id
        challenge_id = stable_id(
            "probe-challenge", trial_stack.patch_hash, registration.probe_id,
            binding_id, path_class_id, registration.observation_contract.contract_id,
        )
        if challenge_id in cells:
            continue
        scenario = ExecutableScenario(
            scenario_id=stable_id("probe-scenario", registration.probe_id,
                                  registration.input_recipe.command, registration.cwd),
            command=registration.input_recipe.command, cwd=registration.cwd,
            environment=registration.environment,
            timeout_seconds=registration.timeout_seconds,
        )
        oracle = ExecutableOracle(
            oracle_id=stable_id("probe-oracle", registration.probe_id,
                                registration.observation_contract.contract_id),
            authority=registration.authority,
            relation=registration.observation_contract.relation,
            expected=registration.observation_contract.expected,
            executable=True, source_evidence_ids=(registration.probe_id,),
        )
        cells[challenge_id] = ChallengeCell(
            challenge_id=challenge_id, patch_hash=trial_stack.patch_hash,
            requirement_id=registration.requirement_id, binding_id=binding_id,
            path_class_id=path_class_id, changed_hunk_ids=binding.changed_hunk_ids,
            kind="PROBE", input_recipe=registration.input_recipe,
            execution_scenario=scenario,
            observation_contract=registration.observation_contract, oracle=oracle,
            authority=registration.authority, baseline_outcome=None, patched_outcome=None,
            trace_bundle_id=None, stability_runs=0, terminal_status=ChallengeStatus.PENDING,
            hard=registration.authority in {"A", "B", "C"}, origin="PROBE_REGISTRATION",
        )
        units[binding_id] = replace(
            binding, challenge_ids=tuple(dict.fromkeys(
                binding.challenge_ids + (challenge_id,)
            )),
        )
        changed = True
    if not changed:
        return trial_stack
    binding_graph = replace(trial_stack.binding_graph, units=units)
    challenge_graph = ChallengeGraph(
        patch_hash=trial_stack.patch_hash, binding_hash=binding_graph.graph_hash(),
        cells=cells, frontier_attempts=dict(trial_stack.challenge_graph.frontier_attempts),
    )
    return replace(trial_stack, binding_graph=binding_graph, challenge_graph=challenge_graph)


def compute_transition_evidence(
    state: ReachAvoidState,
    trial_stack,
    challenge_result,
    mechanical,
    selected_frontier=None,
    trace_bundle: TransitionTraceBundle | None = None,
    atomic_obligations: tuple[AtomicObligation, ...] = (),
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
    atomic_before: dict[str, AtomicEvidence] = {}
    atomic_after: dict[str, AtomicEvidence] = {}
    if trace_bundle is not None:
        atomic_before.update(trace_bundle.incumbent)
        atomic_after.update(trace_bundle.trial)
    elif challenge_result is not None:
        # Compatibility for old, sealed checkpoints only. New transitions
        # always supply a full baseline/incumbent/trial bundle below.
        for execution in challenge_result.executions:
            cell = trial_stack.challenge_graph.cells.get(execution.challenge_id)
            if cell is None:
                continue
            obligation = _atomic_obligation_from_cell(cell, trial_stack)
            def evidence(trace):
                if trace is None:
                    return AtomicEvidence(
                        obligation.key, "UNKNOWN", requirement_id=obligation.requirement_id,
                        role=obligation.role, input_partition_id=obligation.input_partition_id,
                        authority=execution.oracle_authority,
                    )
                status = trace.observation.status.value
                return AtomicEvidence(
                    obligation_key=obligation.key,
                    status=status if status in {"PASS", "FAIL", "UNKNOWN", "BLOCKED"} else "UNKNOWN",
                    requirement_id=obligation.requirement_id, role=obligation.role,
                    input_partition_id=obligation.input_partition_id,
                    observed_payload=trace.observation.to_dict(),
                    expected_payload=cell.observation_contract.expected,
                    comparator=cell.observation_contract.normalized_comparator,
                    stability_runs=trace.stable_runs,
                    entered_project_code=bool(trace.first_project_frame or trace.executed_symbol_ids or trace.executed_path_ids),
                    first_project_frame=trace.first_project_frame,
                    trace_ids=(trace.trace_bundle_id,), authority=execution.oracle_authority,
                    command=trace.command,
                )
            atomic_before[obligation.key] = evidence(execution.previous or execution.baseline)
            atomic_after[obligation.key] = evidence(execution.patched)

    mechanical_obligation = next((item for item in atomic_obligations if item.role == "MECHANICAL"), None)
    if mechanical_obligation is not None:
        before_mechanical = (
            state.last_mechanical_result.passed if state.last_mechanical_result is not None
            else state.working_checkpoint.evidence.mechanical_pass
        )
        atomic_before[mechanical_obligation.key] = AtomicEvidence(
            mechanical_obligation.key, "PASS" if before_mechanical else "FAIL",
            requirement_id=mechanical_obligation.requirement_id, role="MECHANICAL",
            authority="A", stability_runs=2,
        )
        atomic_after[mechanical_obligation.key] = AtomicEvidence(
            mechanical_obligation.key, "PASS" if mechanical.passed else "FAIL",
            requirement_id=mechanical_obligation.requirement_id, role="MECHANICAL",
            authority="A", stability_runs=2,
        )

    atomic_fail_to_pass = [
        key for key, before in atomic_before.items()
        if key in atomic_after
        and before.authority in {"A", "B", "C"}
        and atomic_after[key].authority in {"A", "B", "C"}
        and before.stability_runs >= 2 and atomic_after[key].stability_runs >= 2
        and before.status == "FAIL" and atomic_after[key].status == "PASS"
    ]
    atomic_pass_to_fail = [
        key for key, before in atomic_before.items()
        if key in atomic_after
        and before.authority in {"A", "B", "C"}
        and atomic_after[key].authority in {"A", "B", "C"}
        and before.stability_runs >= 2 and atomic_after[key].stability_runs >= 2
        and before.status == "PASS" and atomic_after[key].status == "FAIL"
    ]
    frontier_delta = None
    if selected_frontier is not None:
        before_measure = build_frontier_measure(selected_frontier, atomic_before,
                                                mechanical_ok=getattr(state.last_mechanical_result, "passed", None))
        after_measure = build_frontier_measure(selected_frontier, atomic_after, mechanical_ok=mechanical.passed)
        reasons: list[str] = []
        kind = _frontier_kind_name(selected_frontier)
        # Only a stable A/B/C transition may close a behavior or preservation
        # frontier.  Provisional observations still remain useful evidence,
        # but can at most justify a provisional working patch.
        selected_fail_to_pass = (
            set(atomic_fail_to_pass)
            .intersection(before_measure.failed_atomic_keys)
            .intersection(after_measure.passed_atomic_keys)
        )
        if kind == "MECHANICAL_FAILURE" and before_measure.mechanical_ok is False and after_measure.mechanical_ok is True:
            reasons.append("mechanical fail -> pass")
        elif kind == "BEHAVIOR_FAILURE" and selected_fail_to_pass:
            reasons.append("selected trusted atomic FAIL -> PASS")
        elif kind == "PRESERVATION_REGRESSION" and selected_fail_to_pass and not atomic_pass_to_fail:
            reasons.append("preservation atomic FAIL -> PASS with locked targets retained")
        elif kind == "REPRODUCTION_GAP" and not before_measure.entered_project_code and after_measure.entered_project_code and after_measure.stable_observation_count:
            reasons.append("reproduction entered project code with stable trace")
        elif kind == "LOCALIZATION_FAILURE" and before_measure.binding_alignment != "ALIGNED" and after_measure.binding_alignment == "ALIGNED":
            reasons.append("dynamic trace aligned binding to current source")
        elif kind == "OBSERVATION_GAP" and after_measure.stable_observation_count > before_measure.stable_observation_count:
            reasons.append("stable typed observation obtained")
        elif kind == "REQUIREMENT_COVERAGE_GAP" and after_measure.covered_partition_ids - before_measure.covered_partition_ids:
            reasons.append("missing requirement partition executed")
        elif kind == "IMPACT_RISK" and after_measure.replayed_impact_ids - before_measure.replayed_impact_ids:
            reasons.append("impact consumer replayed without regression")
        verified_closed = bool(reasons) and (
            kind in {"MECHANICAL_FAILURE", "BEHAVIOR_FAILURE", "PRESERVATION_REGRESSION"}
            or (kind == "REPRODUCTION_GAP" and after_measure.entered_project_code)
            or (kind == "LOCALIZATION_FAILURE" and after_measure.binding_alignment == "ALIGNED")
            or (kind == "OBSERVATION_GAP" and after_measure.stable_observation_count > 0)
            or (kind == "REQUIREMENT_COVERAGE_GAP" and
                bool(after_measure.covered_partition_ids - before_measure.covered_partition_ids))
            or (kind == "IMPACT_RISK" and
                bool(after_measure.replayed_impact_ids - before_measure.replayed_impact_ids))
        )
        frontier_delta = FrontierDelta(
            selected_frontier_key=selected_frontier.semantic_key,
            selected_frontier_kind=kind, before=before_measure, after=after_measure,
            verified_closed=verified_closed, material_progress=bool(reasons),
            progress_reasons=tuple(reasons),
            regression_reasons=("trusted atomic PASS -> FAIL",) if atomic_pass_to_fail else (),
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
        selected_frontier_key=(selected_frontier.semantic_key if selected_frontier else None),
        selected_frontier_kind=(str(selected_frontier.kind) if selected_frontier else None),
        atomic_before=atomic_before, atomic_after=atomic_after,
        atomic_fail_to_pass=tuple(sorted(atomic_fail_to_pass)),
        atomic_pass_to_fail=tuple(sorted(atomic_pass_to_fail)),
        frontier_delta=frontier_delta, trace_bundle=trace_bundle,
        verified_progress=tuple(frontier_delta.progress_reasons if frontier_delta and frontier_delta.verified_closed else ()),
        material_progress=tuple(frontier_delta.progress_reasons if frontier_delta and frontier_delta.material_progress else ()),
        trusted_regressions=tuple(sorted(set(atomic_pass_to_fail) | set(preservation_regressions) | set(target_regressions))),
        hard_avoid_violations=tuple(
            reason for reason in mechanical.failure_reasons
            if not mechanical.passed
        ) + (("forbidden edit",) if mechanical.forbidden_edit else ())
        + (("oracle contamination",) if mechanical.oracle_contamination else ()),
    )


def decide_reach_avoid_transition(
    before_state: ReachAvoidState,
    trial_graph_stack,
    evidence: TransitionEvidence,
) -> TransitionDecision:
    target_progress_keys = tuple(
        key for key in evidence.atomic_fail_to_pass
        if evidence.atomic_after.get(key, AtomicEvidence(key, "UNKNOWN")).role == "TARGET"
    )
    trusted_target_progress = bool(target_progress_keys) or bool(evidence.target_failures_closed)
    trusted_atomic_progress = bool(evidence.atomic_fail_to_pass)
    hard_avoid_reasons = list(evidence.hard_avoid_violations)
    if evidence.locked_targets_lost:
        hard_avoid_reasons.append("locked trusted target changed PASS -> FAIL")
    if evidence.mechanical.unsafe_api_break or evidence.mechanical.high_risk_side_effect:
        hard_avoid_reasons.append("confirmed destructive API or data behavior")
    preservation_regression = bool(evidence.preservation_regressions) or any(
        evidence.atomic_after.get(key, AtomicEvidence(key, "UNKNOWN")).role
        in {"PRESERVATION", "IMPACT"}
        for key in evidence.atomic_pass_to_fail
    )
    repairable_regression = preservation_regression and trusted_target_progress
    if hard_avoid_reasons:
        decision = Decision.ROLLBACK
        reasons = tuple(hard_avoid_reasons)
        next_kind = None
    elif preservation_regression and not trusted_target_progress:
        decision = Decision.ROLLBACK
        reasons = ("confirmed preservation regression without target progress",)
        next_kind = None
    elif trusted_target_progress and repairable_regression:
        # Keep the target mechanism and immediately make the preservation
        # counterexample the next repair objective.  Rolling this back loses
        # the only verified target progress and prevents cumulative repair.
        decision = Decision.KEEP_PROVISIONAL
        reasons = ("target progress retained while preservation regression is repairable",)
        next_kind = "PRESERVATION_REGRESSION"
    elif (
        evidence.frontier_delta is not None
        and evidence.frontier_delta.verified_closed
    ) or trusted_atomic_progress:
        decision = Decision.COMMIT_WORKING
        reasons = ("selected frontier closed by trusted atomic evidence",)
        next_kind = None
    elif (
        evidence.frontier_delta is not None
        and evidence.frontier_delta.material_progress
        and not evidence.trusted_regressions
    ):
        decision = Decision.KEEP_PROVISIONAL
        reasons = ("selected frontier made measurable evidence progress",)
        next_kind = evidence.selected_frontier_kind
    else:
        decision = Decision.ROLLBACK
        reasons = ("selected frontier has no verified or material progress",)
        next_kind = None
    strict_progress = trusted_atomic_progress or bool(
        evidence.frontier_delta and evidence.frontier_delta.verified_closed
    )
    causal_progress = bool(
        evidence.frontier_delta and evidence.frontier_delta.material_progress
    )
    return TransitionDecision(
        decision=decision,
        reasons=reasons,
        strict_progress=strict_progress,
        causal_progress=causal_progress,
        hard_avoid=bool(hard_avoid_reasons),
        repairable_regression=repairable_regression,
        promote_to_working=decision in {Decision.COMMIT_WORKING, Decision.KEEP_PROVISIONAL},
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
    # A static scope mismatch is localization evidence, not a mechanical or
    # forbidden-edit violation.  Keep the trial executable so a dynamic trace
    # can rebind the frontier and guide the next revision.
    return mechanical


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
    selected_frontier=None,
) -> TrialTransition:
    source_checkpoint = state.working_checkpoint
    empty = diff_between(source_checkpoint.snapshot_tree, source_checkpoint.snapshot_tree)
    try:
        trial_tree = create_trial_tree(source_checkpoint, state.run_root / "trials")
        apply_generator_result(trial_tree, generator_result)
    except (OSError, RuntimeError, ValueError) as exc:
        mechanical = run_mechanical_checks(Path(source_checkpoint.snapshot_tree), empty)
        mechanical = replace(mechanical, passed=False, failure_reasons=(str(exc),))
        evidence = _tag_selected_frontier(TransitionEvidence(
            mechanical, (), (), (), (), (), (), (), (), (), (), False, False, (),
        ), selected_frontier)
        decision = decide_reach_avoid_transition(state, state.graph_stack, evidence)
        progress = ProgressEvaluation(False, False, 0, 0, (), (), ())
        reach = evaluate_reach(state)
        avoid = AvoidEvaluation(AvoidKind.HARD_AVOID, (str(exc),), (), ())
        state.graph_stack.validate()
        return TrialTransition(
            source_checkpoint.checkpoint_id, None, empty, empty,
            state.graph_stack, None, evidence, progress, reach, avoid,
            decision, trial_patch_changed=False, entered_evaluation=False,
            selected_frontier=selected_frontier,
        )
    incremental = diff_between(source_checkpoint.snapshot_tree, trial_tree)
    cumulative = diff_between(state.base_repository, trial_tree)
    if incremental.empty or cumulative.empty or cumulative.patch_hash == state.graph_stack.patch_hash:
        mechanical = run_mechanical_checks(trial_tree, cumulative)
        evidence = _tag_selected_frontier(TransitionEvidence(
            mechanical, (), (), (), (), (), (), (), (), (), (), False, False, (),
        ), selected_frontier)
        decision = replace(
            decide_reach_avoid_transition(state, state.graph_stack, evidence),
            decision=Decision.ROLLBACK,
            reasons=("empty or identical cumulative patch",),
            promote_to_working=False,
        )
        progress = ProgressEvaluation(False, False, 0, 0, (), (), ())
        reach = evaluate_reach(state)
        avoid = AvoidEvaluation(AvoidKind.NOT_AVOID, (), (), ())
        state.graph_stack.validate()
        return TrialTransition(
            source_checkpoint.checkpoint_id, str(trial_tree), incremental, cumulative,
            state.graph_stack, None, evidence, progress, reach, avoid, decision,
            trial_patch_changed=False, entered_evaluation=False,
            selected_frontier=selected_frontier,
        )
    mechanical = run_mechanical_checks(
        trial_tree, cumulative, oracle_paths=_public_check_paths(state),
        source_tree=Path(source_checkpoint.snapshot_tree),
    )
    mechanical = _validate_repair_scope(state, incremental, mechanical)
    if not mechanical.passed:
        evidence = _tag_selected_frontier(TransitionEvidence(
            mechanical, (), (), (), (), (), (), (), (), (), (), False, False, (),
        ), selected_frontier)
        decision = decide_reach_avoid_transition(state, state.graph_stack, evidence)
        progress = ProgressEvaluation(False, False, 0, 0, (), (), ())
        reach = ReachEvaluation(False, mechanical.failure_reasons, 0, 0, 0)
        avoid = AvoidEvaluation(AvoidKind.HARD_AVOID, mechanical.failure_reasons, (), ())
        state.graph_stack.validate()
        return TrialTransition(
            source_checkpoint.checkpoint_id, str(trial_tree), incremental, cumulative,
            state.graph_stack, None, evidence, progress, reach, avoid, decision,
            trial_patch_changed=True, entered_evaluation=True,
            selected_frontier=selected_frontier,
        )
    public_evidence = _public_evidence_from_stack(state)
    trial_stack = update_graph_stack_after_diff(
        state.graph_stack, cumulative, trial_tree, state.base_repository, "",
        public_evidence, state.graph_budget,
    )
    trial_stack = _materialize_registered_probes(
        state, trial_stack, selected_frontier,
    )
    diff_graph_metrics = latest_graph_metrics()
    diff_program = trial_stack.program_graph
    impact = trial_stack.program_graph.impact_cone
    plan = build_diff_conditioned_regression_plan(state, cumulative, impact)
    transient = replace(state, graph_stack=trial_stack)
    selection = materialize_trial_challenges(
        state, trial_stack, plan, selected_frontier=selected_frontier,
    )
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
    atomic_obligations = _transition_atomic_obligations(
        state, trial_stack, selection, selected_frontier,
    )
    execution_by_challenge = {
        item.challenge_id: item for item in challenge_result.executions
    }
    alignment_by_key: dict[str, str] = {}
    for obligation in atomic_obligations:
        if not obligation.source.startswith("challenge:"):
            continue
        challenge_id = obligation.source.split(":", 1)[1]
        cell = trial_stack.challenge_graph.cells.get(challenge_id)
        paired = execution_by_challenge.get(challenge_id)
        unit = trial_stack.binding_graph.units.get(cell.binding_id) if cell else None
        entered = bool(paired and (
            paired.patched.first_project_frame
            or paired.patched.executed_symbol_ids
            or paired.patched.executed_path_ids
        ))
        if entered and unit is not None and unit.status.execution_confirmed and cell.changed_hunk_ids:
            alignment_by_key[obligation.key] = "ALIGNED"
        elif entered and cell is not None and _frontier_matches_cell(selected_frontier, cell, trial_stack):
            alignment_by_key[obligation.key] = "DISJOINT"
    trace_bundle = execute_transition_triplet(
        state.base_repository, Path(source_checkpoint.snapshot_tree), trial_tree,
        atomic_obligations,
        {
            "stability_runs": 2,
            "backend": "shared-executor",
            "binding_alignment_by_key": alignment_by_key,
        },
    )
    evidence = compute_transition_evidence(
        state, trial_stack, challenge_result, mechanical, selected_frontier,
        trace_bundle=trace_bundle, atomic_obligations=atomic_obligations,
    )
    placeholder = TrialTransition(
        source_checkpoint.checkpoint_id, str(trial_tree), incremental, cumulative,
        trial_stack, challenge_result, evidence,
        ProgressEvaluation(False, False, 0, 0, (), (), ()),
        ReachEvaluation(False, (), 0, 0, 0),
        AvoidEvaluation(AvoidKind.NOT_AVOID, (), (), ()),
        TransitionDecision(Decision.ROLLBACK, (), False, False, False, False, False, None),
        trial_patch_changed=True,
        entered_evaluation=True,
        selected_frontier=selected_frontier, trace_bundle=trace_bundle,
        atomic_obligations=atomic_obligations,
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
