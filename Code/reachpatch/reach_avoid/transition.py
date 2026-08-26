from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from reachpatch.challenge_graph.models import (
    challenge_obligation_key, open_high_challenge_ids,
)
from reachpatch.execution import (
    apply_generator_result, create_trial_tree, diff_between, execute_transition_triplet,
    run_mechanical_checks,
)
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.evidence import (
    ExecutableCheck, ExecutableOracle, ObservationContract, OutcomeStatus,
    PairClassification, PairedTraceBundle, PublicEvidence, RunObservation,
    TraceBundle,
)
from reachpatch.models.graphs import ChallengeCell, ChallengeGraph, ChallengeStatus, ExecutableScenario
from reachpatch.models.reach_avoid import (
    AtomicEvidence, AtomicObligation, AvoidEvaluation, AvoidKind, ChallengeRoundResult, ChallengeSelection, CheckpointEvidence, Decision,
    FrontierDelta, build_frontier_measure,
    GeneratorResult, ProgressEvaluation, ReachAvoidState, ReachEvaluation,
    StateCheckpoint, TransitionDecision, TransitionEvidence, TrialTransition,
    TransitionTraceBundle, atomic_obligation_key, normalize_input_recipe_semantics,
)
from .validation_backlog import derive_impact_validation_plan
from reachpatch.reach_avoid.gates import compare_progress, evaluate_avoid, evaluate_reach
from reachpatch.reach_avoid.graph_stack import (
    latest_graph_metrics, set_graph_metrics, update_graph_stack_after_diff,
)
from reachpatch.reach_avoid.regression import (
    build_diff_conditioned_regression_plan, materialize_trial_challenges,
)
from reachpatch.reach_avoid.semantics import (
    input_partition_semantic_key, scenario_semantic_key,
    normalize_execution_contract,
    normalize_target_cell,
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


def _frontier_scenario_rank(frontier, cell, stack) -> tuple[int, int, int, str]:
    """Rank a trial cell against the selected frontier's stable semantics.

    Requirement membership is intentionally the weakest match.  A single
    Requirement can materialize several adjacent partitions after a diff; if
    those cells are truncated first, the exact failing partition can disappear
    from the two-scenario primary batch.
    """
    if frontier is None:
        return (1, 1, 1, cell.challenge_id)
    partition_id = input_partition_semantic_key(cell.input_recipe)
    exact_partition = bool(
        getattr(frontier, "input_partition_id", None)
        and partition_id == frontier.input_partition_id
    )
    binding_ids = set(getattr(frontier, "binding_ids", ()))
    path_ids = set(getattr(frontier, "path_class_ids", ()))
    unit = stack.binding_graph.units.get(cell.binding_id)
    bound_route = bool(
        cell.binding_id in binding_ids
        or cell.path_class_id in path_ids
        or (unit is not None and unit.path_class_id in path_ids)
    )
    requirement_match = cell.requirement_id in set(
        getattr(frontier, "requirement_ids", ()),
    )
    return (
        not exact_partition,
        not bound_route,
        not requirement_match,
        cell.challenge_id,
    )


def _atomic_obligation_from_cell(cell, stack) -> AtomicObligation:
    leaf = stack.requirement_graph.leaves.get(cell.requirement_id)
    role = (
        "PRESERVATION" if cell.kind == "PRESERVATION"
        or bool(getattr(leaf, "preservation", False))
        else "IMPACT" if cell.kind == "IMPACT"
        else "TARGET"
    )
    normalized_contract = normalize_execution_contract(
        cell.observation_contract,
        role=role,
        force_process_success=(
            role == "TARGET"
            and (
                str(getattr(cell, "origin", "")).upper() == "PUBLIC_CHECK"
                or str(getattr(getattr(cell, "input_recipe", None), "kind", "")).upper()
                == "PUBLIC_REPLAY"
            )
        ),
    )
    contract_id = getattr(normalized_contract, "contract_id", None) or cell.observation_contract.contract_id
    # Input partition identity must be identical in ChallengeCell,
    # RepairFrontier, and AtomicObligation. Execution details travel in the
    # scenario, not in a second competing partition key.
    partition_id = input_partition_semantic_key(cell.input_recipe)
    # A cell's resolved oracle is the executable authority for a transition.
    # Requirement prose can describe a return value while a public check
    # establishes the same requirement through exit status.  Carrying the
    # prose contract here would make a passing public assertion look like an
    # atomic failure.
    oracle_expected = normalized_contract.expected if role == "TARGET" else cell.oracle.expected
    if hasattr(oracle_expected, "to_dict"):
        oracle_expected = oracle_expected.to_dict()
    if role in {"PRESERVATION", "IMPACT"} and cell.oracle.authority == "C" and isinstance(oracle_expected, dict):
        # Authority-C preservation is a relation to the stable baseline
        # observation.  Keep only typed observable fields so the shared
        # ObservationContract comparator can evaluate it; duration, trace IDs
        # and status metadata are not behavior.
        oracle_expected = {
            key: oracle_expected[key]
            for key in ("exit_code", "stdout", "stderr", "value", "exception")
            if key in oracle_expected and oracle_expected[key] is not None
        } or oracle_expected
    if isinstance(oracle_expected, dict) and set(oracle_expected) == {"exit_code"} and oracle_expected["exit_code"] == 0:
        atomic_contract = ObservationContract(
            relation=normalized_contract.relation, expected={"exit_code": 0}, observable="process",
            comparator="EXIT_ZERO",
        )
    elif isinstance(oracle_expected, dict) and any(
        key in oracle_expected for key in ("exit_code", "stdout", "stderr", "value", "exception")
    ):
        atomic_contract = ObservationContract(
            relation=normalized_contract.relation, expected=oracle_expected, observable="process",
            comparator="EQUALS",
        )
    else:
        comparator = normalized_contract.normalized_comparator
        atomic_contract = ObservationContract(
            relation=normalized_contract.relation, expected=oracle_expected,
            observable=normalized_contract.observable,
            comparator=(comparator if comparator != "RELATION_HOLDS" else "EQUALS"),
        )
    raw = AtomicObligation(
        key="", requirement_id=cell.requirement_id,
        requirement_contract_id=contract_id, role=role, input_recipe=cell.input_recipe,
        input_partition_id=partition_id, oracle_contract=atomic_contract,
        authority=cell.oracle.authority if cell.oracle.authority in {"A", "B", "C", "PROVISIONAL"} else "PROVISIONAL",
        hard=cell.hard, source=f"challenge:{cell.challenge_id}",
    )
    return replace(raw, key=atomic_obligation_key(raw))


def build_transition_validation_batch(
    state: ReachAvoidState,
    selected_frontier,
    trial_diff,
    *,
    trial_stack=None,
    selection: ChallengeSelection | None = None,
) -> tuple[AtomicObligation, ...]:
    """Return the bounded validation set in the required execution order.

    The selected frontier must be evaluated before locks and impact replays.
    Duplicates are removed by the patch-independent atomic semantic key.
    """
    trial_stack = trial_stack or state.graph_stack
    selection = selection or ChallengeSelection(
        tuple(getattr(selected_frontier, "challenge_ids", ()))[:2],
    )
    selected_ids = tuple(getattr(selection, "challenge_ids", ()))
    cells_by_id = {
        cell.challenge_id: cell
        for cell in trial_stack.challenge_graph.active_cells()
    }
    # Preserve the materializer's selected-frontier order.  Iterating the
    # graph dictionary here used to put older target cells ahead of a newly
    # materialized preservation replay, so the selected frontier silently
    # disappeared from transition evidence.
    primary_candidates = [
        cells_by_id[challenge_id] for challenge_id in selected_ids
        if challenge_id in cells_by_id
        and cells_by_id[challenge_id].execution_scenario.command
    ]
    primary_candidates.sort(key=lambda cell: (
        _frontier_scenario_rank(selected_frontier, cell, trial_stack),
        cell.kind == "PRESERVATION",
    ))
    candidates = primary_candidates[:2]
    planning_state = replace(state, graph_stack=trial_stack)
    validation_items = derive_impact_validation_plan(
        planning_state, selected_frontier, trial_diff,
    )
    cells_by_scenario = {}
    for cell in trial_stack.challenge_graph.active_cells():
        if not cell.execution_scenario.command:
            continue
        obligation = _atomic_obligation_from_cell(cell, trial_stack)
        scenario_key = scenario_semantic_key(
            requirement_contract_id=obligation.requirement_contract_id,
            role=obligation.role,
            input_recipe=cell.input_recipe,
            observation_contract=cell.observation_contract,
        )
        cells_by_scenario.setdefault(scenario_key, cell)
    locked_added = 0
    impact_added = 0
    for item in validation_items:
        candidate = cells_by_scenario.get(item.scenario_key or "")
        if candidate is None or candidate in candidates:
            continue
        if item.kind.startswith("LOCKED_") and locked_added < 4:
            candidates.append(candidate)
            locked_added += 1
        elif item.kind == "IMPACT_REPLAY" and impact_added < 2:
            candidates.append(candidate)
            impact_added += 1
    obligations: list[AtomicObligation] = []
    seen: set[str] = set()
    for cell in candidates:
        obligation = _atomic_obligation_from_cell(cell, trial_stack)
        if obligation.key in seen:
            continue
        seen.add(obligation.key)
        obligations.append(obligation)
    # A source-backed ISSUE_DIFF_MISMATCH can be actionable before a
    # ChallengeCell exists.  Its objective supplies the selected executable
    # structural obligation.  Carry that obligation as one atomic unit into
    # the triplet; otherwise transition would validate only mechanics and
    # could never retain an evidence-limited repair.
    objective = state.current_repair_objective
    if (
        objective is not None
        and getattr(objective, "selected_frontier", None) is not None
        and objective.selected_frontier.semantic_key
        == getattr(selected_frontier, "semantic_key", "")
    ):
        requirement_ids = set(getattr(selected_frontier, "requirement_ids", ()))
        for obligation in objective.atomic_obligations:
            if len([item for item in obligations if item.role != "MECHANICAL"]) >= 2:
                break
            if (
                obligation.role == "MECHANICAL"
                or obligation.requirement_id not in requirement_ids
                or obligation.key in seen
            ):
                continue
            seen.add(obligation.key)
            obligations.append(obligation)
    # Registered probes are durable state, not tool-call transcripts.  When a
    # probe is linked to this frontier it must be re-run in the controller's
    # triplet and compared alongside the selected challenge.
    selected_key = getattr(selected_frontier, "semantic_key", "")
    for registration in state.probe_registrations.values():
        if len(obligations) >= 2:
            break
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


def _atomic_trace(evidence: AtomicEvidence, *, tree_hash: str, suffix: str) -> TraceBundle:
    """Reify one triplet observation as a graph/audit trace.

    The triplet executor is the sole runner for a transition.  This adapter
    does not execute anything; it only translates its structured observation
    into the existing ChallengeGraph evidence shape so the rest of the
    Reach--Avoid state can consume the same run without a second paired
    execution.
    """
    payload = evidence.observed_payload if isinstance(evidence.observed_payload, dict) else {}
    raw_status = payload.get("status", evidence.status)
    try:
        status = OutcomeStatus(str(raw_status))
    except ValueError:
        status = OutcomeStatus.UNKNOWN
    observation = RunObservation(
        status=status,
        return_code=payload.get("return_code"),
        stdout=str(payload.get("stdout", "")),
        stderr=str(payload.get("stderr", "")),
        duration_seconds=float(payload.get("duration_seconds", 0.0) or 0.0),
        value=payload.get("value"),
        exception=payload.get("exception"),
    )
    return TraceBundle(
        trace_bundle_id=(evidence.trace_ids[-1] if evidence.trace_ids
                         else stable_id("triplet-trace", evidence.obligation_key, suffix)),
        tree_hash=tree_hash, command=tuple(evidence.command), observation=observation,
        executed_symbol_ids=(), executed_path_ids=(),
        first_project_frame=evidence.first_project_frame,
        stable_runs=max(1, evidence.stability_runs), comparable=True,
        cwd=evidence.cwd, backend=evidence.backend,
    )


def _triplet_classification(incumbent: AtomicEvidence, trial: AtomicEvidence) -> PairClassification:
    if incumbent.status == "PASS" and trial.status == "PASS":
        return PairClassification.PASS_PRESERVED
    if incumbent.status != "PASS" and trial.status == "PASS":
        return (PairClassification.PASS_PRESERVED
                if trial.role in {"PRESERVATION", "IMPACT"}
                else PairClassification.TARGET_FIXED)
    if incumbent.status == "PASS" and trial.status != "PASS":
        return (PairClassification.PRESERVATION_REGRESSION
                if trial.role in {"PRESERVATION", "IMPACT"}
                else PairClassification.TARGET_REGRESSED)
    if trial.status in {"UNKNOWN", "UNEXECUTABLE", "BLOCKED"}:
        return PairClassification.UNKNOWN
    return PairClassification.TARGET_STILL_FAILING


def apply_triplet_evidence_to_trial_graph(
    trial_stack, obligations: tuple[AtomicObligation, ...],
    trace_bundle: TransitionTraceBundle,
) -> GraphStack:
    """Apply already-collected triplet evidence without running a challenge."""
    cells = dict(trial_stack.challenge_graph.cells)
    for obligation in obligations:
        if not obligation.source.startswith("challenge:"):
            continue
        challenge_id = obligation.source.split(":", 1)[1]
        cell = cells.get(challenge_id)
        evidence = trace_bundle.trial.get(obligation.key)
        if cell is None or evidence is None:
            continue
        status_map = {
            "PASS": ChallengeStatus.PASS,
            "FAIL": ChallengeStatus.FAIL,
            "UNKNOWN": ChallengeStatus.UNKNOWN,
            "UNEXECUTABLE": ChallengeStatus.UNSUPPORTED,
            "BLOCKED": ChallengeStatus.UNKNOWN,
        }
        terminal = status_map.get(evidence.status, ChallengeStatus.UNKNOWN)
        payload = evidence.observed_payload if isinstance(evidence.observed_payload, dict) else {}
        try:
            outcome = OutcomeStatus(str(payload.get("status", evidence.status)))
        except ValueError:
            outcome = OutcomeStatus.UNKNOWN
        cells[challenge_id] = replace(
            cell, patched_outcome=outcome, trace_bundle_id=(
                evidence.trace_ids[-1] if evidence.trace_ids else cell.trace_bundle_id
            ), stability_runs=evidence.stability_runs, terminal_status=terminal,
        )
    challenge_graph = replace(trial_stack.challenge_graph, cells=cells)
    return replace(trial_stack, challenge_graph=challenge_graph)


def _mechanical_status(evidence: dict[str, AtomicEvidence]) -> bool | None:
    item = next((value for value in evidence.values()
                 if value.role == "MECHANICAL"), None)
    if item is None:
        return None
    return item.status == "PASS"


def compute_selected_frontier_delta(
    selected_frontier,
    incumbent_evidence: dict[str, AtomicEvidence],
    trial_evidence: dict[str, AtomicEvidence],
) -> FrontierDelta:
    """Compare only evidence semantically owned by one repair frontier."""
    before = build_frontier_measure(
        selected_frontier, incumbent_evidence,
        mechanical_ok=_mechanical_status(incumbent_evidence),
    )
    after = build_frontier_measure(
        selected_frontier, trial_evidence,
        mechanical_ok=_mechanical_status(trial_evidence),
    )
    trusted_fail_to_pass = {
        key for key in before.failed_atomic_keys & after.passed_atomic_keys
        if incumbent_evidence[key].authority in {"A", "B", "C"}
        and trial_evidence[key].authority in {"A", "B", "C"}
        and incumbent_evidence[key].stability_runs >= 2
        and trial_evidence[key].stability_runs >= 2
    }
    trusted_pass_to_fail = {
        key for key in before.passed_atomic_keys & after.failed_atomic_keys
        if incumbent_evidence[key].authority in {"A", "B", "C"}
        and trial_evidence[key].authority in {"A", "B", "C"}
        and incumbent_evidence[key].stability_runs >= 2
        and trial_evidence[key].stability_runs >= 2
    }
    kind = _frontier_kind_name(selected_frontier)
    reasons: list[str] = []
    verified_closed = False
    if (
        kind == "MECHANICAL_FAILURE"
        and before.mechanical_ok is False
        and after.mechanical_ok is True
    ):
        reasons.append("mechanical fail -> pass")
        verified_closed = True
    elif kind == "BEHAVIOR_FAILURE" and trusted_fail_to_pass:
        reasons.append("selected trusted atomic FAIL -> PASS")
        verified_closed = True
    elif (
        kind == "PRESERVATION_REGRESSION"
        and trusted_fail_to_pass
        and not any(
            incumbent_evidence[key].role == "TARGET"
            for key in trusted_pass_to_fail
        )
    ):
        reasons.append(
            "preservation atomic FAIL -> PASS with locked targets retained",
        )
        verified_closed = True
    elif (
        kind == "LOCALIZATION_FAILURE"
        and before.binding_alignment != "ALIGNED"
        and after.binding_alignment == "ALIGNED"
    ):
        reasons.append("dynamic trace aligned binding to current source")
        verified_closed = True
    elif (
        kind == "REQUIREMENT_COVERAGE_GAP"
        and after.covered_partition_ids - before.covered_partition_ids
    ):
        reasons.append("missing requirement partition executed")
        verified_closed = True
    elif kind == "ISSUE_DIFF_MISMATCH":
        if trusted_fail_to_pass:
            reasons.append("selected mismatch scenario FAIL -> PASS")
        elif (
            before.entered_project_code is False
            and after.entered_project_code is True
        ):
            reasons.append("selected mismatch scenario entered project code")
        elif after.stable_observation_count > before.stable_observation_count:
            reasons.append("selected mismatch gained a stable observation")
        elif (
            before.binding_alignment != "ALIGNED"
            and after.binding_alignment == "ALIGNED"
            and after.stable_observation_count > 0
        ):
            reasons.append(
                "incremental edit aligned to selected source and passed stable validation",
            )
        # Graph/source alignment alone never closes a mismatch frontier.
        verified_closed = False
    return FrontierDelta(
        selected_frontier_key=selected_frontier.semantic_key,
        selected_frontier_kind=kind, before=before, after=after,
        verified_closed=verified_closed, material_progress=bool(reasons),
        progress_reasons=tuple(reasons),
        regression_reasons=(
            ("selected trusted atomic PASS -> FAIL",)
            if trusted_pass_to_fail else ()
        ),
    )


def _challenge_result_from_triplet(
    state: ReachAvoidState, trial_stack: GraphStack,
    selection: ChallengeSelection, obligations: tuple[AtomicObligation, ...],
    trace_bundle: TransitionTraceBundle,
) -> Any:
    """Build a compatibility result from the single triplet execution."""
    executions: list[PairedTraceBundle] = []
    for obligation in obligations:
        if not obligation.source.startswith("challenge:"):
            continue
        challenge_id = obligation.source.split(":", 1)[1]
        incumbent = trace_bundle.incumbent.get(obligation.key)
        trial = trace_bundle.trial.get(obligation.key)
        baseline = trace_bundle.baseline.get(obligation.key)
        if incumbent is None or trial is None or baseline is None:
            continue
        cell = trial_stack.challenge_graph.cells.get(challenge_id)
        if cell is None:
            continue
        role = "PRESERVATION" if obligation.role in {"PRESERVATION", "IMPACT"} else "TARGET"
        classification = _triplet_classification(incumbent, trial)
        oracle_id = stable_id("triplet-oracle", obligation.key, obligation.requirement_contract_id)
        executions.append(PairedTraceBundle(
            paired_bundle_id=stable_id("triplet-paired", obligation.key, tuple(trial.trace_ids)),
            check_id=cell.input_recipe.source_check_id or challenge_id,
            challenge_id=challenge_id, patch_hash=trial_stack.patch_hash,
            baseline=_atomic_trace(baseline, tree_hash="baseline", suffix="baseline"),
            patched=_atomic_trace(trial, tree_hash=trial_stack.patch_hash, suffix="trial"),
            classification=classification, oracle_id=oracle_id,
            oracle_authority=trial.authority, expected_relation=(
                getattr(obligation.oracle_contract, "relation", "")
            ), stable_runs=min(baseline.stability_runs, trial.stability_runs),
            previous=_atomic_trace(incumbent, tree_hash="incumbent", suffix="incumbent"),
            oracle_contract_id=getattr(obligation.oracle_contract, "contract_id", ""),
        ))
    return ChallengeRoundResult(
        selected_challenge_ids=tuple(selection.challenge_ids),
        executed_challenge_ids=tuple(item.challenge_id for item in executions),
        executions=tuple(executions), counterexamples=(), confirmed_failures=(),
        updated_graph_stack=trial_stack, frontiers=(), execution_seconds=0.0, cache_hits=0,
    )


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
    locked_check_ids = (
        set(state.locked_checks.target_ids)
        | set(state.locked_checks.preservation_ids)
    )
    locked_lost = tuple(sorted(
        check_id for check_id in locked_check_ids
        if any(
            execution.check_id == check_id
            and execution.classification in {
                PairClassification.TARGET_REGRESSED,
                PairClassification.PRESERVATION_REGRESSION,
            }
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
        frontier_delta = compute_selected_frontier_delta(
            selected_frontier, atomic_before, atomic_after,
        )
    confirmed_regression_keys = tuple(sorted(
        key for key in atomic_pass_to_fail
        if atomic_after[key].role in {"PRESERVATION", "IMPACT"}
    ))
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
        trusted_regressions=tuple(sorted(
            set(confirmed_regression_keys)
            | set(preservation_regressions)
            | set(locked_lost)
        )),
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
    # A transition is about the selected repair frontier.  A new impact or
    # locked replay can add useful evidence, but it cannot independently
    # promote an unrelated trial.  The no-frontier fallback is retained for
    # direct API callers that construct TransitionEvidence outside the
    # controller; production always supplies a selected frontier.
    delta = evidence.frontier_delta
    if delta is None:
        selected_fail_to_pass = set(evidence.atomic_fail_to_pass)
    else:
        selected_fail_to_pass = (
            set(evidence.atomic_fail_to_pass)
            .intersection(delta.before.failed_atomic_keys)
            .intersection(delta.after.passed_atomic_keys)
        )
    target_progress_keys = tuple(
        key for key in selected_fail_to_pass
        if evidence.atomic_after.get(key, AtomicEvidence(key, "UNKNOWN")).role == "TARGET"
    )
    trusted_target_progress = bool(target_progress_keys) or bool(
        delta is None and evidence.target_failures_closed
    )
    trusted_atomic_progress = bool(selected_fail_to_pass)
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
        delta is not None
        and delta.verified_closed
    ) or trusted_atomic_progress:
        # An ISSUE_DIFF_MISMATCH may identify the right source mechanism, but
        # it begins with provisional alignment evidence.  It must be retained
        # for the next targeted validation, never treated as a direct Reach
        # proof or an unconditional commit.
        if evidence.selected_frontier_kind == "ISSUE_DIFF_MISMATCH":
            decision = Decision.KEEP_PROVISIONAL
            reasons = ("issue/diff mismatch gained selected scenario evidence",)
            next_kind = evidence.selected_frontier_kind
        else:
            decision = Decision.COMMIT_WORKING
            reasons = ("selected frontier closed by trusted atomic evidence",)
            next_kind = None
    elif (
        delta is not None
        and delta.material_progress
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
        delta and delta.verified_closed
    )
    causal_progress = bool(
        delta and delta.material_progress
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
    trial_stack = replace(
        trial_stack,
        challenge_graph=replace(
            trial_stack.challenge_graph,
            cells={
                challenge_id: normalize_target_cell(cell)
                for challenge_id, cell in trial_stack.challenge_graph.cells.items()
            },
        ),
    )
    trial_stack = _materialize_registered_probes(
        state, trial_stack, selected_frontier,
    )
    trial_stack = replace(
        trial_stack,
        challenge_graph=replace(
            trial_stack.challenge_graph,
            cells={
                challenge_id: normalize_target_cell(cell)
                for challenge_id, cell in trial_stack.challenge_graph.cells.items()
            },
        ),
    )
    diff_graph_metrics = latest_graph_metrics()
    diff_program = trial_stack.program_graph
    impact = trial_stack.program_graph.impact_cone
    plan = build_diff_conditioned_regression_plan(state, cumulative, impact)
    selection = materialize_trial_challenges(
        state, trial_stack, plan, selected_frontier=selected_frontier,
    )
    # Build the bounded atomic batch first, then execute exactly one triplet.
    # The former implementation ran execute_challenge_round here and ran the
    # same scenarios again in execute_transition_triplet, making transition
    # evidence depend on two different runners.
    atomic_obligations = build_transition_validation_batch(
        state, selected_frontier, cumulative,
        trial_stack=trial_stack, selection=selection,
    )
    trace_bundle = execute_transition_triplet(
        state.base_repository, Path(source_checkpoint.snapshot_tree), trial_tree,
        atomic_obligations,
        {
            "stability_runs": 2,
            "backend": "shared-executor",
        },
    )
    # Dynamic alignment is derived from the triplet trace, never from a
    # static MAY_EXECUTE edge.  The adapter annotates evidence for the
    # selected frontier before measure/delta comparison.
    trial_evidence = dict(trace_bundle.trial)
    incremental_paths = {hunk.path for hunk in incremental.hunks}
    selected_slice_paths = {
        trial_stack.program_graph.nodes[node_id].path
        for node_id in getattr(selected_frontier, "repair_slice_ids", ())
        if node_id in trial_stack.program_graph.nodes
    }
    selected_incremental_alignment = bool(
        incremental_paths & selected_slice_paths
    )
    for obligation in atomic_obligations:
        evidence_item = trial_evidence.get(obligation.key)
        if evidence_item is None:
            continue
        if (
            _frontier_kind_name(selected_frontier) == "ISSUE_DIFF_MISMATCH"
            and obligation.role == "TARGET"
            and obligation.requirement_id
            in set(getattr(selected_frontier, "requirement_ids", ()))
            and selected_incremental_alignment
            and evidence_item.stability_runs >= 2
        ):
            trial_evidence[obligation.key] = replace(
                evidence_item, binding_alignment="ALIGNED",
            )
            continue
        if not obligation.source.startswith("challenge:"):
            continue
        challenge_id = obligation.source.split(":", 1)[1]
        cell = trial_stack.challenge_graph.cells.get(challenge_id)
        entered = bool(evidence_item.entered_project_code)
        alignment = "UNKNOWN"
        if entered and cell is not None and _frontier_matches_cell(selected_frontier, cell, trial_stack):
            alignment = "ALIGNED" if cell.changed_hunk_ids else "UNKNOWN"
        trial_evidence[obligation.key] = replace(evidence_item, binding_alignment=alignment)
    trace_bundle = replace(trace_bundle, trial=trial_evidence)
    trial_stack = apply_triplet_evidence_to_trial_graph(
        trial_stack, atomic_obligations, trace_bundle,
    )
    challenge_result = _challenge_result_from_triplet(
        state, trial_stack, selection, atomic_obligations, trace_bundle,
    )
    execution_graph_metrics = latest_graph_metrics()
    set_graph_metrics({
        key: diff_graph_metrics.get(key, 0.0)
        + execution_graph_metrics.get(key, 0.0)
        for key in diff_graph_metrics
    })
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
