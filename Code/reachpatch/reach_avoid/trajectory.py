from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from reachpatch.execution.models import (
    CheckClassification,
    CheckExecution,
    CheckRole,
    CheckStatus,
    ExecutableCheck,
)
from reachpatch.models.base import stable_id
from reachpatch.models.controller import (
    ConfirmedFailure,
    EvidenceVector,
    ExecutableOracle,
    LockedCheck,
    LockedCheckSet,
    PatchCheckpoint,
    PatchTrajectory,
    ReachAvoidState,
    RevisionRecord,
    TrialComparison,
    FailureHistory,
)
from reachpatch.oracle.models import ObservationContract


TRUSTED_AUTHORITIES = frozenset({"A", "B", "C"})
INFRASTRUCTURE_STATUSES = frozenset({
    CheckStatus.TIMEOUT,
    CheckStatus.FLAKY,
    CheckStatus.INVALID_ENVIRONMENT,
    CheckStatus.INVALID_SELECTOR,
    CheckStatus.UNSUPPORTED,
})


def authority_tier(check: ExecutableCheck, candidate: Any | None = None) -> str:
    """Map public provenance to the A-E authority scale used by transitions."""

    strategy = str(getattr(candidate, "strategy", ""))
    evidence = " ".join(map(str, check.source_evidence_ids)).lower()
    candidate_authority = str(getattr(candidate, "oracle_authority", "")).upper()
    if candidate_authority in {"A", "B", "C", "D", "E"}:
        return candidate_authority
    authority = str(check.authority).upper()
    if strategy == "llm_reproduction" or "directed-public-reproduction" in evidence:
        return "E"
    if strategy in {"issue_executable_witness", "return_or_exception_api_contract"}:
        return "B"
    if strategy in {
        "metamorphic_relation",
        "baseline_differential_relation",
        "object_state_or_protocol_relation",
        "serialization_or_rendering",
    }:
        return "C"
    if authority in {"A", "B", "C", "D", "E"}:
        return authority
    if authority in {"PUBLIC", "PUBLIC_REPOSITORY_TEST", "PUBLIC_EXPLICIT_COMMAND"}:
        return "A"
    if "issue-code-block" in evidence or "issue-behavior" in evidence:
        return "B"
    return "E"


def observations_are_comparable(baseline: Any, current: Any) -> bool:
    if not isinstance(baseline, CheckExecution) or not isinstance(current, CheckExecution):
        return False
    return bool(
        baseline.check_id == current.check_id
        and baseline.stable
        and current.stable
        and baseline.status not in INFRASTRUCTURE_STATUSES
        and current.status not in INFRASTRUCTURE_STATUSES
    )


def is_confirmed_failure(failure: ConfirmedFailure) -> bool:
    return bool(
        failure.kind in {
            "CONFIRMED_TARGET_FAILURE",
            "CONFIRMED_PRESERVATION_REGRESSION",
            "CONFIRMED_DICC_COUNTEREXAMPLE",
            "CONFIRMED_MECHANICAL_FAILURE",
        }
        and failure.oracle_authority in TRUSTED_AUTHORITIES
        and failure.stable_runs >= 2
        and failure.expected_relation.is_executable
        and observations_are_comparable(
            failure.baseline_observation,
            failure.before_patch_observation,
        )
    )


def confirmed_failure_priority(failure: ConfirmedFailure) -> tuple[int, str]:
    ranks = {
        "CONFIRMED_MECHANICAL_FAILURE": 4,
        "CONFIRMED_PRESERVATION_REGRESSION": 3,
        "CONFIRMED_TARGET_FAILURE": 2,
        "CONFIRMED_DICC_COUNTEREXAMPLE": 1,
    }
    return ranks.get(failure.kind, 0), failure.failure_id


def select_confirmed_failure(state: ReachAvoidState) -> ConfirmedFailure | None:
    failures = tuple(
        item for item in state.confirmed_failures
        if is_confirmed_failure(item)
    )
    if state.patch_trajectory is not None:
        state.patch_trajectory.confirmed_failures = list(failures)
    return max(failures, key=confirmed_failure_priority, default=None)


def _binding_for_check(state: ReachAvoidState, check: ExecutableCheck) -> Any | None:
    return next((
        unit for unit in state.active_binding_graph.units.values()
        if check.check_id in {
            *unit.target_check_ids,
            *unit.preservation_check_ids,
            *unit.challenge_check_ids,
        }
    ), None)


def _check_reaches_relevant_region(
    state: ReachAvoidState,
    check: ExecutableCheck,
) -> bool:
    if check.target_requirement_ids:
        return True
    return _binding_for_check(state, check) is not None


def refresh_confirmed_failures(state: ReachAvoidState) -> tuple[ConfirmedFailure, ...]:
    """Derive revision triggers solely from current stable executable evidence."""

    recovery = state.target_recovery
    if recovery is None:
        state.confirmed_failures = []
        return ()
    checks = {
        item.check_id: item
        for item in (*recovery.targets, *recovery.preservation_checks)
    }
    candidates = {
        item.target_id: item for item in getattr(recovery, "candidates", ())
    }
    certifications = {
        item.target_id: item
        for item in getattr(recovery, "certifications", ())
    }
    failures: list[ConfirmedFailure] = []
    for comparison in state.check_comparisons:
        check = checks.get(comparison.check_id)
        if check is None:
            continue
        tier = authority_tier(check, candidates.get(check.check_id))
        if tier not in TRUSTED_AUTHORITIES:
            continue
        if check.role == CheckRole.TARGET:
            if candidates.get(check.check_id) is None or not getattr(
                certifications.get(check.check_id), "certified", False
            ):
                continue
        if not observations_are_comparable(comparison.baseline, comparison.patched):
            continue
        if not _check_reaches_relevant_region(state, check):
            continue
        kind = None
        relation = None
        if (
            check.role == CheckRole.TARGET
            and comparison.classification == CheckClassification.TARGET_STILL_FAILING
            and comparison.baseline.status == CheckStatus.FAIL
            and comparison.patched.status == CheckStatus.FAIL
        ):
            kind = "CONFIRMED_TARGET_FAILURE"
            relation = "baseline_failure_must_become_pass"
        elif (
            check.role == CheckRole.PRESERVATION
            and comparison.classification == CheckClassification.PRESERVATION_REGRESSION
            and comparison.baseline.status == CheckStatus.PASS
            and comparison.patched.status == CheckStatus.FAIL
        ):
            kind = "CONFIRMED_PRESERVATION_REGRESSION"
            relation = "baseline_pass_must_be_preserved"
        if kind is None or relation is None:
            continue
        unit = _binding_for_check(state, check)
        requirement_id = (
            check.target_requirement_ids[0]
            if check.target_requirement_ids else getattr(unit, "requirement_id", None)
        )
        oracle = ExecutableOracle(
            oracle_id=stable_id("locked-oracle", check.check_id, relation, tier),
            authority=tier,
            relation=relation,
            requirement_id=requirement_id,
        )
        failures.append(ConfirmedFailure(
            failure_id=stable_id(
                "confirmed-failure", kind, check.check_id,
                comparison.patched.failure_signature,
                state.checkpoint.patch.canonical_diff_hash,
            ),
            kind=kind,
            check_id=check.check_id,
            oracle_authority=tier,
            requirement_id=requirement_id,
            binding_unit_id=getattr(unit, "binding_id", None),
            baseline_observation=comparison.baseline,
            before_patch_observation=comparison.patched,
            expected_relation=oracle,
            stable_runs=2,
            failure_signature=(
                comparison.patched.failure_signature
                or stable_id("failure-signature", check.check_id, comparison.patched.status)
            ),
            failure_location=(
                str(comparison.patched.first_project_frame)
                if comparison.patched.first_project_frame else None
            ),
            causal_cut_ids=tuple(getattr(unit, "causal_cut_ids", ())),
            impact_risk_ids=tuple(getattr(unit, "impact_cone_ids", ())),
        ))
    for packet in getattr(state, "counterexamples", ()):
        if not (
            packet.challenge_id
            and packet.environment_valid
            and packet.baseline_status == "PASS"
            and packet.patched_status == "FAIL"
            and packet.binding_unit_id
        ):
            continue
        challenge_graph = getattr(state, "challenge_graph", None)
        cell = getattr(challenge_graph, "cells", {}).get(packet.challenge_id)
        scenario = getattr(challenge_graph, "scenarios", {}).get(
            getattr(cell, "scenario_id", "") or ""
        )
        oracle = getattr(scenario, "oracle", None)
        tier = str(getattr(getattr(oracle, "authority", None), "value", ""))
        unit = state.active_binding_graph.units.get(packet.binding_unit_id)
        bundle = getattr(state, "trace_bundles", {}).get(
            getattr(cell, "execution_bundle_id", "") or ""
        )
        from reachpatch.repair.counterexamples import challenge_is_confirmed

        if not challenge_is_confirmed(cell, scenario, bundle, unit):
            continue
        stable_runs = min(
            len(getattr(getattr(bundle, "base_bundle", None), "runs", ())),
            len(getattr(getattr(bundle, "patch_bundle", None), "runs", ())),
        )
        if not (
            tier in TRUSTED_AUTHORITIES
            and getattr(oracle, "executable", False)
            and stable_runs >= 2
            and unit is not None
            and (unit.changed_hunk_ids or unit.program_symbol_ids)
        ):
            continue
        check_id = f"challenge:{packet.challenge_id}"
        base_run = bundle.base_bundle.runs[0]
        patch_run = bundle.patch_bundle.runs[0]
        baseline_observation = CheckExecution(
            execution_id=bundle.base_bundle.bundle_id,
            check_id=check_id,
            tree_hash=bundle.base_bundle.source_hash,
            status=CheckStatus.PASS,
            return_code=0,
            stdout=base_run.run.raw_stdout,
            stderr=base_run.run.raw_stderr,
            duration_seconds=sum(
                run.duration_seconds for run in bundle.base_bundle.runs
            ),
            stable=True,
            failure_signature=None,
            first_project_frame=None,
        )
        before_observation = CheckExecution(
            execution_id=bundle.patch_bundle.bundle_id,
            check_id=check_id,
            tree_hash=bundle.patch_bundle.source_hash,
            status=CheckStatus.FAIL,
            return_code=1,
            stdout=patch_run.run.raw_stdout,
            stderr=patch_run.run.raw_stderr,
            duration_seconds=sum(
                run.duration_seconds for run in bundle.patch_bundle.runs
            ),
            stable=True,
            failure_signature=packet.failure_signature,
            first_project_frame=packet.first_project_frame,
        )
        failures.append(ConfirmedFailure(
            failure_id=stable_id(
                "confirmed-dicc-failure", packet.counterexample_id,
                state.checkpoint.patch.canonical_diff_hash,
            ),
            kind="CONFIRMED_DICC_COUNTEREXAMPLE",
            check_id=check_id,
            oracle_authority=tier,
            requirement_id=packet.requirement_id,
            binding_unit_id=packet.binding_unit_id,
            baseline_observation=baseline_observation,
            before_patch_observation=before_observation,
            expected_relation=ExecutableOracle(
                oracle_id=str(getattr(oracle, "oracle_id", check_id)),
                authority=tier,
                relation="baseline_pass_must_be_preserved",
                requirement_id=packet.requirement_id,
                is_executable=True,
            ),
            stable_runs=stable_runs,
            failure_signature=str(packet.failure_signature),
            failure_location=packet.failure_location or None,
            causal_cut_ids=packet.causal_cut_ids,
            impact_risk_ids=packet.impact_risks,
        ))
    state.confirmed_failures = failures
    if state.patch_trajectory is not None:
        state.patch_trajectory.confirmed_failures = list(failures)
    return tuple(failures)


def _candidate_contract(state: ReachAvoidState, check_id: str) -> ObservationContract:
    recovery = state.target_recovery
    candidate = next((
        item for item in getattr(recovery, "candidates", ())
        if item.target_id == check_id
    ), None)
    if candidate is not None:
        return candidate.observation_contract
    return ObservationContract(
        contract_id=stable_id("process-observation-contract", check_id),
        channels=("process_status", "stdout", "stderr", "exception"),
    )


def build_locked_check_set(
    state: ReachAvoidState,
    failure: ConfirmedFailure,
    before: PatchCheckpoint,
) -> LockedCheckSet:
    """Freeze all trusted targets and baseline-passing preservation checks."""

    recovery = state.target_recovery
    if recovery is None:
        raise ValueError("locked checks require target recovery evidence")
    baseline = {item.check_id: item for item in recovery.baseline_executions}
    candidates = {
        item.target_id: item for item in getattr(recovery, "candidates", ())
    }
    certifications = {
        item.target_id: item
        for item in getattr(recovery, "certifications", ())
    }

    def lock(check: ExecutableCheck) -> LockedCheck | None:
        tier = authority_tier(check, candidates.get(check.check_id))
        base = baseline.get(check.check_id)
        if tier not in TRUSTED_AUTHORITIES or base is None or not base.stable:
            return None
        if check.role == CheckRole.TARGET and (
            candidates.get(check.check_id) is None
            or not getattr(
                certifications.get(check.check_id), "certified", False
            )
        ):
            return None
        unit = _binding_for_check(state, check)
        if not _check_reaches_relevant_region(state, check):
            return None
        relation = (
            "baseline_failure_must_become_pass"
            if check.role == CheckRole.TARGET
            else "baseline_pass_must_be_preserved"
        )
        requirement_ids = check.target_requirement_ids or tuple(filter(None, (
            getattr(unit, "requirement_id", None),
        )))
        return LockedCheck(
            check_id=check.check_id,
            role=check.role.value,
            command=check.command,
            observation_contract=_candidate_contract(state, check.check_id),
            oracle=ExecutableOracle(
                oracle_id=stable_id("locked-oracle", check.check_id, tier, relation),
                authority=tier,
                relation=relation,
                requirement_id=requirement_ids[0] if requirement_ids else None,
            ),
            authority=tier,
            requirement_ids=requirement_ids,
            cwd=check.cwd,
            environment=check.environment,
            timeout_seconds=check.timeout_seconds,
            source_evidence_ids=check.source_evidence_ids,
            baseline_observation=base,
        )

    targets = tuple(filter(None, (lock(item) for item in recovery.targets)))
    preservation = tuple(filter(None, (
        lock(item) for item in recovery.preservation_checks
        if (
            baseline.get(item.check_id) is not None
            and baseline[item.check_id].status == CheckStatus.PASS
        )
    )))
    counterexample_checks: tuple[LockedCheck, ...] = ()
    if failure.kind == "CONFIRMED_DICC_COUNTEREXAMPLE":
        packet = next((
            item for item in state.counterexamples
            if f"challenge:{item.challenge_id}" == failure.check_id
        ), None)
        cell = getattr(state.challenge_graph, "cells", {}).get(
            packet.challenge_id if packet is not None else ""
        )
        recipe = getattr(state.challenge_graph, "recipes", {}).get(
            getattr(cell, "trigger_recipe_id", "") or ""
        )
        scenario = getattr(state.challenge_graph, "scenarios", {}).get(
            getattr(cell, "scenario_id", "") or ""
        )
        if packet is not None and recipe is not None and scenario is not None:
            counterexample_checks = (LockedCheck(
                check_id=failure.check_id,
                role=CheckRole.TARGET.value,
                command=("input-recipe", recipe.recipe_id),
                observation_contract=ObservationContract(
                    contract_id=stable_id(
                        "challenge-observation-contract", failure.check_id,
                    ),
                    channels=("process_status", "stdout", "stderr"),
                ),
                oracle=failure.expected_relation,
                authority=failure.oracle_authority,
                requirement_ids=tuple(filter(None, (failure.requirement_id,))),
                cwd=state.checkpoint.snapshot_tree,
                environment={},
                timeout_seconds=60.0,
                baseline_observation=failure.baseline_observation,
                input_recipe=recipe,
                executable_scenario=scenario,
                baseline_repository=state.base_repository,
            ),)
    if failure.check_id not in {
        item.check_id for item in (*targets, *preservation, *counterexample_checks)
    }:
        raise ValueError("confirmed failure is absent from its locked check set")
    lock_id = stable_id(
        "locked-check-set", state.instance_id, before.patch_hash,
        failure.failure_id,
        tuple(item.check_id for item in (*targets, *preservation)),
    )
    result = LockedCheckSet(
        lock_id=lock_id,
        target_checks=targets,
        preservation_checks=preservation,
        counterexample_checks=counterexample_checks,
    )
    state.current_locked_check_set = result
    if state.patch_trajectory is not None:
        state.patch_trajectory.locked_checks.update({
            item.check_id: item for item in result.all_checks()
        })
    return result


def _as_executable_check(check: LockedCheck) -> ExecutableCheck:
    return ExecutableCheck(
        check_id=check.check_id,
        role=CheckRole(check.role),
        authority=check.authority,
        command=check.command,
        cwd=check.cwd,
        environment=check.environment,
        timeout_seconds=check.timeout_seconds,
        source_evidence_ids=check.source_evidence_ids,
        target_requirement_ids=check.requirement_ids,
        temporary_artifact_paths=(),
        executed_symbol_ids=(),
    )


def execute_locked_checks(
    repository: str | Path,
    locked_checks: LockedCheckSet,
    project_runner: Any,
    tree_hash: str,
) -> tuple[CheckExecution, ...]:
    results: list[CheckExecution] = []
    for check in locked_checks.all_checks():
        if check.input_recipe is not None and check.executable_scenario is not None:
            from reachpatch.execution.executor import TraceExecutor
            from reachpatch.models.enums import OutcomeStatus

            executor = TraceExecutor(
                temporary_root=Path(repository).parent / ".reachpatch-locked",
            )
            paired = executor.execute_paired(
                check.input_recipe,
                check.baseline_repository,
                repository,
                check.executable_scenario,
            )
            if paired.status == OutcomeStatus.PASS:
                status = CheckStatus.PASS
            elif paired.status == OutcomeStatus.FAIL:
                status = CheckStatus.FAIL
            else:
                status = CheckStatus.FLAKY
            results.append(CheckExecution(
                execution_id=paired.paired_bundle_id,
                check_id=check.check_id,
                tree_hash=tree_hash,
                status=status,
                return_code=0 if status == CheckStatus.PASS else 1,
                stdout="",
                stderr="",
                duration_seconds=sum(
                    run.duration_seconds
                    for trace_bundle in (paired.base_bundle, paired.patch_bundle)
                    for run in trace_bundle.runs
                ),
                stable=paired.stability_status == "STABLE",
                failure_signature=(
                    None if status == CheckStatus.PASS
                    else stable_id("locked-recipe-failure", paired.to_dict())
                ),
                first_project_frame=paired.first_divergence,
            ))
        else:
            results.append(project_runner.run_check(
                _as_executable_check(check),
                repository=Path(repository),
                tree_hash=tree_hash,
            ))
    return tuple(results)


def compare_observations(
    *,
    before_results: Iterable[CheckExecution],
    after_results: Iterable[CheckExecution],
    locked_checks: LockedCheckSet,
    before_patch_hash: str,
    after_patch_hash: str,
) -> TrialComparison:
    before = {item.check_id: item for item in before_results}
    after = {item.check_id: item for item in after_results}
    expected_ids = tuple(item.check_id for item in locked_checks.all_checks())
    comparable = bool(expected_ids) and set(before) == set(after) == set(expected_ids)
    comparable = comparable and all(
        observations_are_comparable(before[check_id], after[check_id])
        for check_id in expected_ids
    )
    target_ids = {
        item.check_id
        for item in (
            *locked_checks.target_checks,
            *locked_checks.counterexample_checks,
        )
    }
    preservation = {
        item.check_id: item for item in locked_checks.preservation_checks
    }
    target_pass_before = tuple(sorted(
        check_id for check_id in target_ids
        if check_id in before and before[check_id].status == CheckStatus.PASS
    ))
    target_pass_after = tuple(sorted(
        check_id for check_id in target_ids
        if check_id in after and after[check_id].status == CheckStatus.PASS
    ))
    target_fail_before = tuple(sorted(
        check_id for check_id in target_ids
        if check_id in before and before[check_id].status == CheckStatus.FAIL
    ))
    target_fail_after = tuple(sorted(
        check_id for check_id in target_ids
        if check_id in after and after[check_id].status == CheckStatus.FAIL
    ))
    regressions = tuple(sorted(
        check_id for check_id, check in preservation.items()
        if (
            isinstance(check.baseline_observation, CheckExecution)
            and check.baseline_observation.status == CheckStatus.PASS
            and check_id in after and after[check_id].status == CheckStatus.FAIL
        )
    ))
    before_regressions = tuple(sorted(
        check_id for check_id, check in preservation.items()
        if (
            isinstance(check.baseline_observation, CheckExecution)
            and check.baseline_observation.status == CheckStatus.PASS
            and check_id in before and before[check_id].status == CheckStatus.FAIL
        )
    ))
    unknown = tuple(sorted(
        check_id for check_id in expected_ids
        if (
            check_id not in before or check_id not in after
            or before[check_id].status in INFRASTRUCTURE_STATUSES
            or after[check_id].status in INFRASTRUCTURE_STATUSES
            or not before[check_id].stable or not after[check_id].stable
        )
    ))
    return TrialComparison(
        comparison_id=stable_id(
            "trial-comparison", locked_checks.lock_id,
            before_patch_hash, after_patch_hash,
            tuple(item.execution_id for item in before.values()),
            tuple(item.execution_id for item in after.values()),
        ),
        lock_id=locked_checks.lock_id,
        before_patch_hash=before_patch_hash,
        after_patch_hash=after_patch_hash,
        before_results=tuple(before[check_id] for check_id in expected_ids if check_id in before),
        after_results=tuple(after[check_id] for check_id in expected_ids if check_id in after),
        executed_check_ids=expected_ids,
        comparable=comparable and not unknown,
        confirmed_target_pass_before=target_pass_before,
        confirmed_target_pass_after=target_pass_after,
        confirmed_target_failure_before=target_fail_before,
        confirmed_target_failure_after=target_fail_after,
        preservation_regressions_before=before_regressions,
        preservation_regressions_after=regressions,
        unknown_check_ids=unknown,
    )


def evaluate_trial_against_checkpoint(
    *,
    before: PatchCheckpoint,
    after_patch_hash: str,
    before_repository: str | Path,
    after_repository: str | Path,
    locked_checks: LockedCheckSet,
    project_runner: Any,
    before_tree_hash: str,
    after_tree_hash: str,
) -> TrialComparison:
    before_results = execute_locked_checks(
        before_repository, locked_checks, project_runner, before_tree_hash,
    )
    after_results = execute_locked_checks(
        after_repository, locked_checks, project_runner, after_tree_hash,
    )
    return compare_observations(
        before_results=before_results,
        after_results=after_results,
        locked_checks=locked_checks,
        before_patch_hash=before.patch_hash,
        after_patch_hash=after_patch_hash,
    )


def decide_transition(comparison: TrialComparison) -> tuple[str, str]:
    if not comparison.comparable or comparison.unknown_check_ids:
        return "ROLLBACK", "LOCKED_CHECK_RESULTS_NOT_COMPARABLE"
    before_target = len(comparison.confirmed_target_pass_before)
    after_target = len(comparison.confirmed_target_pass_after)
    before_regressions = len(comparison.preservation_regressions_before)
    after_regressions = len(comparison.preservation_regressions_after)
    target_improvement = after_target > before_target
    regression_improvement = (
        after_target == before_target
        and before_regressions > after_regressions
    )
    if target_improvement and after_regressions:
        return (
            "KEEP_TRIAL_FOR_REGRESSION_REPAIR",
            "TARGET_FIXED_WITH_CONFIRMED_PRESERVATION_REGRESSION",
        )
    if (
        (target_improvement or regression_improvement)
        and after_regressions == 0
        and not comparison.mechanical_failures_after
    ):
        return "PROMOTE", "CONFIRMED_EXECUTION_IMPROVEMENT"
    return "ROLLBACK", "NO_CONFIRMED_IMPROVEMENT"


def evidence_vector_from_comparison(comparison: TrialComparison) -> EvidenceVector:
    return EvidenceVector(
        confirmed_target_pass_count=len(comparison.confirmed_target_pass_after),
        confirmed_target_failure_count=len(comparison.confirmed_target_failure_after),
        confirmed_preservation_regression_count=len(
            comparison.preservation_regressions_after
        ),
        mechanical_failure_count=len(comparison.mechanical_failures_after),
    )


def checkpoint_from_state(
    state: ReachAvoidState,
    *,
    evidence_vector: EvidenceVector | None = None,
    executed_check_ids: Iterable[str] = (),
    status: str | None = None,
) -> PatchCheckpoint:
    comparisons = tuple(state.check_comparisons)
    target_ids = {
        item.check_id for item in getattr(state.target_recovery, "targets", ())
    }
    target_passes = tuple(sorted(
        item.check_id for item in comparisons
        if item.check_id in target_ids
        and item.classification == CheckClassification.TARGET_FIXED
    ))
    target_failures = tuple(sorted(
        item.check_id for item in comparisons
        if item.check_id in target_ids
        and item.classification == CheckClassification.TARGET_STILL_FAILING
    ))
    regressions = tuple(sorted(
        item.check_id for item in comparisons
        if item.classification == CheckClassification.PRESERVATION_REGRESSION
    ))
    vector = evidence_vector or EvidenceVector(
        confirmed_target_pass_count=len(target_passes),
        confirmed_target_failure_count=len(target_failures),
        confirmed_preservation_regression_count=len(regressions),
    )
    return PatchCheckpoint(
        checkpoint_id=state.checkpoint.checkpoint_id,
        revision=state.checkpoint.patch.version,
        patch=state.checkpoint.patch,
        patch_hash=state.checkpoint.patch.canonical_diff_hash,
        evidence_vector=vector,
        executed_check_ids=tuple(executed_check_ids),
        confirmed_target_pass_ids=target_passes,
        confirmed_target_failure_ids=target_failures,
        preservation_regression_ids=regressions,
        mechanical_failure_ids=(),
        parent_checkpoint_id=state.checkpoint.parent_checkpoint_id,
        status=status or state.checkpoint.patch.status,
        snapshot_tree=state.checkpoint.snapshot_tree,
    )


def checkpoint_from_trial_diff(
    state: ReachAvoidState,
    cumulative_diff: Any,
    *,
    status: str,
    evidence_vector: EvidenceVector | None = None,
    executed_check_ids: Iterable[str] = (),
    snapshot_tree: str = "",
) -> PatchCheckpoint:
    parent = (
        state.patch_trajectory.working_patch
        if state.patch_trajectory is not None else None
    )
    checkpoint_id = stable_id(
        "trajectory-trial-checkpoint", state.instance_id,
        cumulative_diff.canonical_diff_hash, state.transition_index + 1,
    )
    from reachpatch.models.controller import WorkingPatch
    patch = WorkingPatch(
        version=state.checkpoint.patch.version + 1,
        base_commit=state.base_commit,
        canonical_diff=cumulative_diff.canonical_diff,
        canonical_diff_hash=cumulative_diff.canonical_diff_hash,
        base_tree_hash=cumulative_diff.base_tree_hash,
        working_tree_hash=cumulative_diff.trial_tree_hash,
        parent_patch_hash=state.checkpoint.patch.canonical_diff_hash,
        checkpoint_id=checkpoint_id,
        status=status,
    )
    return PatchCheckpoint(
        checkpoint_id=checkpoint_id,
        revision=patch.version,
        patch=patch,
        patch_hash=patch.canonical_diff_hash,
        evidence_vector=evidence_vector or EvidenceVector(),
        executed_check_ids=tuple(executed_check_ids),
        confirmed_target_pass_ids=(),
        confirmed_target_failure_ids=(),
        preservation_regression_ids=(),
        mechanical_failure_ids=(),
        parent_checkpoint_id=(parent.checkpoint_id if parent is not None else None),
        status=status,
        snapshot_tree=snapshot_tree,
    )


def initialize_patch_trajectory(state: ReachAvoidState) -> PatchTrajectory:
    if state.patch_trajectory is not None:
        return state.patch_trajectory
    first = checkpoint_from_state(
        state,
        executed_check_ids=(item.check_id for item in state.check_comparisons),
        status="FIRST_PATCH",
    )
    trajectory = PatchTrajectory(
        first_patch=first,
        best_evidence_patch=first,
        working_patch=first,
        trial_patch=None,
        locked_checks={},
        confirmed_failures=[],
        revision_history=[],
        checkpoint_archive={first.checkpoint_id: first},
    )
    state.patch_trajectory = trajectory
    state.checkpoint_history[first.checkpoint_id] = state.checkpoint
    refresh_confirmed_failures(state)
    return trajectory


def record_transition(
    state: ReachAvoidState,
    *,
    failure: ConfirmedFailure,
    comparison: TrialComparison,
    trial: PatchCheckpoint,
    action_id: str,
    mechanism_id: str,
    decision: str,
    reason: str,
) -> None:
    trajectory = state.patch_trajectory
    if trajectory is None:
        raise RuntimeError("patch trajectory is not initialized")
    source = trajectory.working_patch
    trajectory.trial_patch = trial
    trajectory.checkpoint_archive[trial.checkpoint_id] = trial
    promoted = decision == "PROMOTE"
    rolled_back = decision == "ROLLBACK"
    if promoted:
        trajectory.best_evidence_patch = trial
        trajectory.working_patch = trial
        trajectory.regression_repair_attempts = 0
        trajectory.trial_patch = None
    elif decision == "KEEP_TRIAL_FOR_REGRESSION_REPAIR":
        trajectory.working_patch = trial
        trajectory.regression_repair_attempts = 0
        trajectory.trial_patch = None
    else:
        if failure.kind != "CONFIRMED_PRESERVATION_REGRESSION":
            trajectory.working_patch = trajectory.best_evidence_patch
        trajectory.trial_patch = None
    revision_id = stable_id(
        "trajectory-revision", comparison.comparison_id, decision,
    )
    trajectory.revision_history.append(RevisionRecord(
        revision_id=revision_id,
        failure_id=failure.failure_id,
        action_id=action_id,
        mechanism_id=mechanism_id,
        source_checkpoint_id=source.checkpoint_id,
        trial_checkpoint_id=trial.checkpoint_id,
        locked_check_set_id=comparison.lock_id,
        executed_check_ids=comparison.executed_check_ids,
        decision=decision,
        reason=reason,
        promoted=promoted,
        rolled_back=rolled_back,
    ))
    previous = state.failure_histories.get(failure.failure_signature)
    attempted = tuple(dict.fromkeys((
        *(previous.attempted_mechanism_ids if previous else ()),
        mechanism_id,
    )))
    outcomes = tuple((
        *(previous.confirmed_outcomes if previous else ()),
        f"{decision}:{reason}",
    ))
    affected = tuple(dict.fromkeys((
        *(previous.affected_symbol_ids if previous else ()),
        *(
            state.active_binding_graph.units[failure.binding_unit_id].program_symbol_ids
            if failure.binding_unit_id in state.active_binding_graph.units else ()
        ),
    )))
    state.failure_histories[failure.failure_signature] = FailureHistory(
        failure_signature=failure.failure_signature,
        attempted_mechanism_ids=attempted,
        causal_cut_ids=tuple(dict.fromkeys((
            *(previous.causal_cut_ids if previous else ()),
            *failure.causal_cut_ids,
        ))),
        revision_ids=tuple((
            *(previous.revision_ids if previous else ()), revision_id,
        )),
        confirmed_outcomes=outcomes,
        affected_symbol_ids=affected,
    )
    failed_outcomes = sum(
        not item.startswith("PROMOTE:") for item in outcomes
    )
    if failed_outcomes >= 2:
        state.prohibited_mechanisms.add(mechanism_id)
    if failed_outcomes >= 3:
        state.runtime_metrics["root_recovery_required"] = True
        state.runtime_metrics["root_recovery_failure_signature"] = (
            failure.failure_signature
        )


def finalize_best_patch(state: ReachAvoidState) -> Any:
    trajectory = state.patch_trajectory
    if trajectory is None:
        return state.checkpoint.patch
    selected = trajectory.best_evidence_patch or trajectory.first_patch
    incumbent = state.checkpoint_history.get(selected.checkpoint_id)
    if incumbent is not None:
        state.checkpoint = incumbent
    state.current_patch_hash = selected.patch_hash
    if state.generator_conversation is not None:
        state.generator_conversation.current_working_diff = selected.patch.canonical_diff
    state.runtime_metrics["final_selected_checkpoint_id"] = selected.checkpoint_id
    state.runtime_metrics["final_selected_patch_hash"] = selected.patch_hash
    return selected.patch
