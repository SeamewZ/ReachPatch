from __future__ import annotations

from dataclasses import dataclass

from reachpatch.models.controller import ReachAvoidState, UnitOutcome
from reachpatch.models.enums import OutcomeStatus
from reachpatch.execution.models import CheckClassification, CheckComparison


def target_deficit(
    state: ReachAvoidState,
    outcomes: dict[str, UnitOutcome] | None = None,
) -> float:
    selected = outcomes if outcomes is not None else state.outcomes
    target_units = {
        unit.unit_id: state.requirement_graph.leaves[unit.leaf_id].weight
        for unit in state.active_binding_graph.units.values()
        if state.requirement_graph.leaves[unit.leaf_id].authority_class.value != "PRESERVATION"
    }
    return sum(
        weight
        for unit_id, weight in target_units.items()
        if not any(item.unit_id == unit_id for item in selected.values())
        or any(
            item.status != OutcomeStatus.PASS
            for item in selected.values() if item.unit_id == unit_id and item.kind == "TARGET"
        )
    )


def repaired_losing_paths(
    old: dict[str, UnitOutcome],
    new: dict[str, UnitOutcome],
    causal_touch: dict[str, list[str]],
) -> tuple[str, ...]:
    old_by_path: dict[str, list[UnitOutcome]] = {}
    new_by_path: dict[str, list[UnitOutcome]] = {}
    for item in old.values():
        old_by_path.setdefault(item.path_obligation_id, []).append(item)
    for item in new.values():
        new_by_path.setdefault(item.path_obligation_id, []).append(item)
    repaired = []
    for path_id, previous in old_by_path.items():
        current = new_by_path.get(path_id, [])
        was_losing = not previous or any(item.status != OutcomeStatus.PASS for item in previous)
        now_passing = bool(current) and all(item.status == OutcomeStatus.PASS for item in current)
        if was_losing and now_passing and causal_touch.get(path_id):
            repaired.append(path_id)
    return tuple(sorted(repaired))


def _legacy_progress_metrics(
    state: ReachAvoidState,
    new_outcomes: dict[str, UnitOutcome],
    causal_touch: dict[str, list[str]],
    *,
    new_requirement_graph=None,
    new_program_graph=None,
    new_binding_graph=None,
    new_challenge_graph=None,
) -> dict[str, object]:
    old_deficit = target_deficit(state)
    new_deficit = target_deficit(state, new_outcomes)
    repaired = repaired_losing_paths(state.outcomes, new_outcomes, causal_touch)
    old_fail = sum(item.status == OutcomeStatus.FAIL for item in state.outcomes.values())
    new_fail = sum(item.status == OutcomeStatus.FAIL for item in new_outcomes.values())
    old_unknown = sum(item.status not in {OutcomeStatus.PASS, OutcomeStatus.FAIL} for item in state.outcomes.values())
    new_unknown = sum(item.status not in {OutcomeStatus.PASS, OutcomeStatus.FAIL} for item in new_outcomes.values())
    old_paths = set(state.requirement_graph.path_obligations)
    new_paths = set(
        getattr(new_requirement_graph, "path_obligations", state.requirement_graph.path_obligations)
    )
    old_path_pass = {
        item.path_obligation_id for item in state.outcomes.values()
        if item.status == OutcomeStatus.PASS
    }
    new_path_pass = {
        item.path_obligation_id for item in new_outcomes.values()
        if item.status == OutcomeStatus.PASS
    }
    old_path_coverage = len(old_path_pass & old_paths) / max(len(old_paths), 1)
    new_path_coverage = len(new_path_pass & new_paths) / max(len(new_paths), 1)

    def _open_frontiers(graph) -> int:
        return sum(1 for item in getattr(graph, "frontiers", {}).values() if item.status == "OPEN")

    old_frontiers = sum(_open_frontiers(graph) for graph in (
        state.requirement_graph, state.program_graph,
        state.active_binding_graph, state.challenge_graph,
    ))
    new_frontiers = sum(_open_frontiers(graph) for graph in (
        new_requirement_graph or state.requirement_graph,
        new_program_graph or state.program_graph,
        new_binding_graph or state.active_binding_graph,
        new_challenge_graph or state.challenge_graph,
    ))
    new_target_units = getattr(new_binding_graph, "units", state.active_binding_graph.units)
    worst_unit_deficit = max(
        (
            sum(
                1 for item in new_outcomes.values()
                if item.unit_id == unit_id and item.status != OutcomeStatus.PASS
            )
            for unit_id in new_target_units
        ),
        default=0,
    )
    return {
        "old_target_deficit": old_deficit,
        "new_target_deficit": new_deficit,
        "deficit_decreased": new_deficit < old_deficit,
        "repaired_losing_path_ids": repaired,
        "stable_fail_delta": new_fail - old_fail,
        "unknown_delta": new_unknown - old_unknown,
        "old_path_coverage": old_path_coverage,
        "new_path_coverage": new_path_coverage,
        "path_coverage_delta": new_path_coverage - old_path_coverage,
        "old_frontier_count": old_frontiers,
        "new_frontier_count": new_frontiers,
        "frontier_delta": new_frontiers - old_frontiers,
        "worst_unit_deficit": worst_unit_deficit,
        "strict_target_progress": (
            new_deficit < old_deficit
            and bool(repaired)
            and new_fail <= old_fail
            and new_path_coverage >= old_path_coverage
        ),
    }


@dataclass(frozen=True, slots=True)
class ProgressMetrics:
    new_target_passes: int
    eliminated_stable_failures: int
    eliminated_counterexamples: int
    new_diff_adequacy_coverage: int
    new_preservation_failures: int
    new_high_risk_unknowns: int
    impact_risk_delta: float

    @property
    def has_confirmed_regression(self) -> bool:
        return self.new_preservation_failures > 0 or self.impact_risk_delta > 0

    @property
    def meaningful_progress(self) -> bool:
        return (
            self.new_target_passes > 0
            or self.eliminated_stable_failures > 0
            or self.eliminated_counterexamples > 0
            or self.new_diff_adequacy_coverage > 0
        ) and not self.has_confirmed_regression


@dataclass(frozen=True, slots=True)
class ProgressVector:
    target_pass_delta: int
    target_failure_distance_delta: float
    requirement_coverage_delta: int
    counterexample_delta: int
    preservation_regression_delta: int
    unresolved_frontier_delta: int
    syntax_health_delta: int

    @property
    def target_fixed_gain(self) -> int:
        return self.target_pass_delta

    @property
    def target_deficit_reduction(self) -> float:
        return self.target_failure_distance_delta

    @property
    def counterexamples_eliminated(self) -> int:
        return self.counterexample_delta

    @property
    def preservation_pass_retained(self) -> int:
        return max(0, -self.preservation_regression_delta)

    @property
    def high_risk_unknown_reduction(self) -> int:
        return max(0, -self.unresolved_frontier_delta)

    @property
    def new_regressions(self) -> int:
        return max(0, self.preservation_regression_delta)

    @property
    def meaningful(self) -> bool:
        return self.new_regressions == 0 and any((
            self.target_pass_delta > 0,
            self.target_failure_distance_delta > 0,
            self.requirement_coverage_delta > 0,
            self.counterexample_delta > 0,
            self.syntax_health_delta > 0,
        ))


@dataclass(frozen=True, slots=True)
class RevisionEvidence:
    progress: ProgressVector
    safe: bool
    real_execution_count: int
    environment_blocked: bool = False

    @property
    def new_regressions(self) -> int:
        return self.progress.new_regressions

    @property
    def target_fixed_gain(self) -> int:
        return self.progress.target_fixed_gain

    @property
    def target_deficit_reduction(self) -> float:
        return self.progress.target_deficit_reduction

    @property
    def counterexamples_eliminated(self) -> int:
        return self.progress.counterexamples_eliminated

    @property
    def high_risk_unknown_reduction(self) -> int:
        return self.progress.high_risk_unknown_reduction


def progress_vector_from_comparisons(
    previous: tuple[CheckComparison, ...] | list[CheckComparison],
    current: tuple[CheckComparison, ...] | list[CheckComparison],
    *,
    target_weights: dict[str, float] | None = None,
) -> ProgressVector:
    """Measure progress only from actual paired check executions."""

    if not current:
        return ProgressVector(0, 0.0, 0, 0, 0, 0, 0)
    weights = target_weights or {}
    old = {item.check_id: item for item in previous}
    new = {item.check_id: item for item in current}
    fixed = {
        check_id for check_id, item in new.items()
        if item.classification == CheckClassification.TARGET_FIXED
    }
    old_fixed = {
        check_id for check_id, item in old.items()
        if item.classification == CheckClassification.TARGET_FIXED
    }
    old_unresolved = {
        check_id for check_id, item in old.items()
        if item.classification in {
            CheckClassification.TARGET_STILL_FAILING,
            CheckClassification.TARGET_REGRESSED,
        }
    }
    if not old:
        old_unresolved = {
            check_id for check_id, item in new.items()
            if item.baseline.status.value == "FAIL"
        }
    deficit_reduction = sum(
        weights.get(check_id, 1.0) for check_id in fixed & old_unresolved
    )
    old_failure_signatures = {
        item.patched.failure_signature for item in old.values()
        if item.patched.failure_signature
        and item.classification == CheckClassification.TARGET_STILL_FAILING
    }
    if not old:
        old_failure_signatures = {
            item.baseline.failure_signature for item in new.values()
            if item.baseline.failure_signature and item.baseline.status.value == "FAIL"
        }
    new_failure_signatures = {
        item.patched.failure_signature for item in new.values()
        if item.patched.failure_signature
        and item.classification == CheckClassification.TARGET_STILL_FAILING
    }
    unknown_classes = {
        CheckClassification.SAME_INFRA_FAILURE,
        CheckClassification.NEW_INFRA_FAILURE,
        CheckClassification.FLAKY_RESULT,
        CheckClassification.UNSUPPORTED_CHECK,
    }
    old_unknown = sum(item.classification in unknown_classes for item in old.values())
    new_unknown = sum(item.classification in unknown_classes for item in new.values())
    regression_classes = {
        CheckClassification.TARGET_REGRESSED,
        CheckClassification.PRESERVATION_REGRESSION,
    }
    return ProgressVector(
        target_pass_delta=len(fixed - old_fixed),
        target_failure_distance_delta=deficit_reduction,
        requirement_coverage_delta=len(fixed - old_fixed),
        counterexample_delta=len(old_failure_signatures - new_failure_signatures),
        preservation_regression_delta=sum(
            item.classification in regression_classes for item in new.values()
        ),
        unresolved_frontier_delta=new_unknown - old_unknown,
        syntax_health_delta=sum(
            item.patched.status.value == "PASS"
            and item.baseline.status.value != "PASS"
            for item in new.values()
        ),
    )


def should_commit(previous, trial) -> bool:
    del previous
    if getattr(trial, "real_execution_count", 0) <= 0:
        return False
    if trial.new_regressions > 0 or not trial.safe:
        return False
    return any((
        trial.target_fixed_gain > 0,
        trial.target_deficit_reduction > 0,
        trial.counterexamples_eliminated > 0,
        trial.high_risk_unknown_reduction > 0,
    ))


def progress_metrics(old_state, trial_state):
    """Compare patch-first states using the unified execution/coverage view."""

    if not hasattr(trial_state, "outcomes"):
        raise TypeError("progress_metrics requires a patch-first ReachAvoidState")
    old_pass = {
        item.unit_id for item in old_state.outcomes.values()
        if item.kind == "TARGET" and item.status == OutcomeStatus.PASS
    }
    new_pass = {
        item.unit_id for item in trial_state.outcomes.values()
        if item.kind == "TARGET" and item.status == OutcomeStatus.PASS
    }
    old_fail = {
        item.unit_id for item in old_state.outcomes.values()
        if item.status == OutcomeStatus.FAIL and item.stable
    }
    new_fail = {
        item.unit_id for item in trial_state.outcomes.values()
        if item.status == OutcomeStatus.FAIL and item.stable
    }
    old_counterexamples = {item.counterexample_id for item in old_state.counterexamples}
    passing_units = {
        item.unit_id for item in trial_state.outcomes.values()
        if item.status == OutcomeStatus.PASS
    }
    eliminated_counterexample_ids = {
        item.counterexample_id for item in old_state.counterexamples
        if item.binding_unit_id is not None and item.binding_unit_id in passing_units
    }
    old_preservation_fail = {
        item.unit_id for item in old_state.outcomes.values()
        if item.kind == "PRESERVATION" and item.status == OutcomeStatus.FAIL and item.stable
    }
    new_preservation_fail = {
        item.unit_id for item in trial_state.outcomes.values()
        if item.kind == "PRESERVATION" and item.status == OutcomeStatus.FAIL and item.stable
    }
    old_unknown = int(old_state.runtime_metrics.get("high_risk_unknowns", 0))
    new_unknown = int(trial_state.runtime_metrics.get("high_risk_unknowns", 0))
    old_coverage = set(old_state.runtime_metrics.get("diff_adequacy_keys", ()))
    new_coverage = set(trial_state.runtime_metrics.get("diff_adequacy_keys", ()))
    old_public_targets = set(
        old_state.runtime_metrics.get("public_target_fixed_commands", ())
    )
    new_public_targets = set(
        trial_state.runtime_metrics.get("public_target_fixed_commands", ())
    )
    old_public_failures = set(
        old_state.runtime_metrics.get("public_stable_fail_commands", ())
    )
    new_public_failures = set(
        trial_state.runtime_metrics.get("public_stable_fail_commands", ())
    )
    return ProgressMetrics(
        new_target_passes=(
            len(new_pass - old_pass) + len(new_public_targets - old_public_targets)
        ),
        eliminated_stable_failures=(
            len(old_fail - new_fail) + len(old_public_failures - new_public_failures)
        ),
        eliminated_counterexamples=len(
            eliminated_counterexample_ids & old_counterexamples
        ),
        new_diff_adequacy_coverage=len(new_coverage - old_coverage),
        new_preservation_failures=len(new_preservation_fail - old_preservation_fail),
        new_high_risk_unknowns=max(0, new_unknown - old_unknown),
        impact_risk_delta=float(trial_state.runtime_metrics.get("impact_risk", 0.0))
        - float(old_state.runtime_metrics.get("impact_risk", 0.0)),
    )


def component_shadow_pass(intent, outcomes: dict[str, UnitOutcome]) -> bool:
    """Require every path in the selected component to have a PASS witness."""

    required = set(intent.complete_component_path_ids)
    if not required:
        return False
    observed = {
        item.path_obligation_id
        for item in outcomes.values()
        if item.path_obligation_id in required
    }
    return observed == required and all(
        item.status == OutcomeStatus.PASS
        for item in outcomes.values()
        if item.path_obligation_id in required
    )
