from __future__ import annotations

from typing import Iterable

from reachpatch.models.controller import MechanicalCheck, ReachAvoidState, UnitOutcome
from reachpatch.models.enums import OutcomeStatus
from reachpatch.challenge_graph.models import DICCStatus
from reachpatch.execution.models import CheckClassification
from reachpatch.reach_avoid.trajectory import TRUSTED_AUTHORITIES, authority_tier


def _hard_frontiers(state: ReachAvoidState) -> tuple[str, ...]:
    sources = (
        state.requirement_graph.frontiers.values(),
        state.program_graph.frontiers.values(),
        state.challenge_graph.frontiers.values(),
    )
    return tuple(sorted({
        item.frontier_id
        for source in sources for item in source
        if item.hard and item.status == "OPEN"
    }))


def raw_avoid_reasons(
    state: ReachAvoidState,
    new_outcomes: dict[str, UnitOutcome],
    mechanical_checks: Iterable[MechanicalCheck],
    *,
    forbidden_edit: bool,
    oracle_contamination: bool,
    diff_safety_pass: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if any(item.status != OutcomeStatus.PASS for item in mechanical_checks):
        reasons.append("MECHANICAL_FAILURE")
    if forbidden_edit:
        reasons.append("FORBIDDEN_EDIT")
    if oracle_contamination:
        reasons.append("ORACLE_CONTAMINATION")
    old_pass_challenges = {
        item.challenge_id for item in state.outcomes.values()
        if item.status == OutcomeStatus.PASS and item.challenge_id
    }
    new_by_challenge = {
        item.challenge_id: item for item in new_outcomes.values() if item.challenge_id
    }
    lost = [
        challenge_id for challenge_id in old_pass_challenges
        if challenge_id not in new_by_challenge
        or new_by_challenge[challenge_id].status != OutcomeStatus.PASS
    ]
    if lost:
        reasons.append("ESTABLISHED_SUCCESS_LOST")
    if any(
        item.kind == "PRESERVATION" and item.status == OutcomeStatus.FAIL and item.stable
        for item in new_outcomes.values()
    ):
        reasons.append("PRESERVATION_FAILURE")
    return tuple(sorted(set(reasons)))


def in_safe_set(state: ReachAvoidState) -> bool:
    return state.checkpoint.safe


def in_target_set(state: ReachAvoidState) -> bool:
    if not state.checkpoint.patch.canonical_diff:
        return False
    target_recovery = getattr(state, "target_recovery", None)
    if target_recovery is not None:
        candidates = {
            item.target_id: item for item in getattr(target_recovery, "candidates", ())
        }
        certifications = {
            item.target_id: item
            for item in getattr(target_recovery, "certifications", ())
        }
        targets = tuple(
            item for item in target_recovery.targets
            if authority_tier(item, candidates.get(item.check_id)) in TRUSTED_AUTHORITIES
            and candidates.get(item.check_id) is not None
            and bool(getattr(certifications.get(item.check_id), "certified", False))
        )
        target_ids = {item.check_id for item in targets}
        comparisons = tuple(state.check_comparisons)
        target_comparisons = tuple(
            item for item in comparisons if item.check_id in target_ids
        )
        preservation_regression = any(
            item.classification == CheckClassification.PRESERVATION_REGRESSION
            for item in comparisons
        )
        environment_invalid = any(
            item.classification in {
                CheckClassification.SAME_INFRA_FAILURE,
                CheckClassification.NEW_INFRA_FAILURE,
                CheckClassification.FLAKY_RESULT,
                CheckClassification.UNSUPPORTED_CHECK,
            }
            for item in target_comparisons
        )
        current_failure_signatures = {
            item.patched.failure_signature for item in comparisons
            if item.patched.failure_signature
        }
        stable_counterexamples_pass = not any(
            item.environment_valid
            and item.failure_signature
            and item.failure_signature in current_failure_signatures
            for item in state.counterexamples
        )
        return all((
            bool(targets),
            bool(target_comparisons),
            len(target_comparisons) == len(targets),
            all(
                item.classification == CheckClassification.TARGET_FIXED
                for item in target_comparisons
            ),
            not preservation_regression,
            stable_counterexamples_pass,
            not environment_invalid,
            state.checkpoint.safe,
            not getattr(state, "confirmed_failures", ()),
            not bool(
                getattr(
                    getattr(state, "requirement_graph", None),
                    "build_stats",
                    {},
                ).get(
                    "fallback_used", 0,
                )
            ),
            all(
                row.status == "PASSING"
                for row in getattr(getattr(state, "requirement_coverage", None), "rows", {}).values()
                if row.binding_unit_ids and row.executable_check_ids
            ) if getattr(state, "requirement_coverage", None) is not None else True,
        ))
    active_graph = getattr(state, "active_binding_graph", None)
    if active_graph is None:
        return False
    active_target_ids = {
        unit.unit_id for unit in active_graph.units.values()
        if unit.target_check_ids
    }
    active_preservation_ids = {
        unit.unit_id for unit in active_graph.units.values()
        if unit.preservation_check_ids
    }
    by_unit = {
        unit_id: [item for item in state.outcomes.values() if item.unit_id == unit_id]
        for unit_id in active_target_ids | active_preservation_ids
    }
    public_target_evidence = set(
        map(str, state.runtime_metrics.get("public_target_evidence_unit_ids", ()))
    )
    active_targets_pass = all(
        (
            by_unit[unit_id]
            and all(item.status == OutcomeStatus.PASS for item in by_unit[unit_id])
        )
        or unit_id in public_target_evidence
        for unit_id in active_target_ids
    )
    preservation_pass = all(
        by_unit[unit_id]
        and all(item.status == OutcomeStatus.PASS for item in by_unit[unit_id])
        for unit_id in active_preservation_ids
    )
    stable_counterexamples_pass = not any(
        item.status == OutcomeStatus.FAIL and item.stable
        for item in state.outcomes.values()
    )
    diff_closed = bool(state.runtime_metrics.get("diff_adequacy_closed", False))
    hashes_current = (
        active_graph.requirement_graph_hash == state.requirement_graph.semantic_layer_hash()
        and active_graph.program_graph_hash == state.program_graph.program_hash()
    )
    return all((
        state.checkpoint.safe,
        bool(active_target_ids),
        active_targets_pass,
        preservation_pass,
        stable_counterexamples_pass,
        not state.runtime_metrics.get("public_stable_fail_commands", ()),
        diff_closed,
        hashes_current,
        not state.runtime_metrics.get("high_value_pending_challenge_ids", ()),
    ))


def evidence_limited_complete(state: ReachAvoidState) -> bool:
    if not state.checkpoint.patch.canonical_diff or state.target_recovery is None:
        return False
    candidates = {
        item.target_id: item for item in getattr(state.target_recovery, "candidates", ())
    }
    trusted_targets = tuple(
        item for item in state.target_recovery.targets
        if authority_tier(item, candidates.get(item.check_id)) in TRUSTED_AUTHORITIES
        and candidates.get(item.check_id) is not None
        and bool(next((
            certification.certified
            for certification in getattr(
                state.target_recovery, "certifications", ()
            )
            if certification.target_id == item.check_id
        ), False))
    )
    return bool(
        not trusted_targets
        and not getattr(state, "confirmed_failures", ())
        and state.checkpoint.safe
        and state.checkpoint.patch.status not in {"ROLLED_BACK", "EMPTY"}
        and not any(
            item.classification == CheckClassification.PRESERVATION_REGRESSION
            for item in getattr(state, "check_comparisons", ())
        )
    )


def terminal_avoid_reason(state: ReachAvoidState) -> str | None:
    if not state.remaining_budget.available() and not in_target_set(state):
        if (
            state.checkpoint.patch.canonical_diff
            and state.checkpoint.safe
            and not getattr(state, "confirmed_failures", ())
        ):
            return None
        if state.checkpoint.patch.canonical_diff:
            return "REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH"
        return "REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE"
    if state.termination_status in {
        "NO_LEGAL_ACTION", "ENVIRONMENT_BLOCKED", "SEMANTIC_BLOCKED",
        "ORACLE_BLOCKED", "LOCALIZATION_BLOCKED", "GENERATOR_BLOCKED_EXTERNAL",
        "GENERATOR_NONPROGRESS", "MECHANICAL_FAILURE",
    }:
        return state.termination_status
    return None
