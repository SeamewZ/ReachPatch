from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.enums import OutcomeStatus
from reachpatch.oracle.models import PairClassification, RunObservation
from reachpatch.program_graph.tracing import DynamicTraceEvent


class CheckRole(StrEnum):
    TARGET = "TARGET"
    PRESERVATION = "PRESERVATION"
    EXPLORATION = "EXPLORATION"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    TIMEOUT = "TIMEOUT"
    FLAKY = "FLAKY"
    INVALID_ENVIRONMENT = "INVALID_ENVIRONMENT"
    INVALID_SELECTOR = "INVALID_SELECTOR"
    UNSUPPORTED = "UNSUPPORTED"


class CheckClassification(StrEnum):
    TARGET_FIXED = "TARGET_FIXED"
    TARGET_STILL_FAILING = "TARGET_STILL_FAILING"
    TARGET_REGRESSED = "TARGET_REGRESSED"
    PASS_PRESERVED = "PASS_PRESERVED"
    PRESERVATION_REGRESSION = "PRESERVATION_REGRESSION"
    SAME_INFRA_FAILURE = "SAME_INFRA_FAILURE"
    NEW_INFRA_FAILURE = "NEW_INFRA_FAILURE"
    FLAKY_RESULT = "FLAKY_RESULT"
    UNSUPPORTED_CHECK = "UNSUPPORTED_CHECK"


class EnvironmentHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    COLLECTION_BROKEN = "COLLECTION_BROKEN"
    INVALID_SELECTOR = "INVALID_SELECTOR"
    UNSUPPORTED_RUNTIME = "UNSUPPORTED_RUNTIME"
    EXTERNAL_SERVICE_REQUIRED = "EXTERNAL_SERVICE_REQUIRED"


INFRASTRUCTURE_STATUSES = frozenset({
    CheckStatus.INVALID_ENVIRONMENT,
    CheckStatus.INVALID_SELECTOR,
    CheckStatus.UNSUPPORTED,
})


@dataclass(frozen=True, slots=True)
class ExecutableCheck(SerializableRecord):
    check_id: str
    role: CheckRole
    authority: str
    command: tuple[str, ...]
    cwd: str
    environment: dict[str, str]
    timeout_seconds: float
    source_evidence_ids: tuple[str, ...]
    target_requirement_ids: tuple[str, ...]
    temporary_artifact_paths: tuple[str, ...]
    selector: str = ""

    def with_role(self, role: CheckRole) -> "ExecutableCheck":
        from dataclasses import replace

        return replace(self, role=role)


@dataclass(frozen=True, slots=True)
class CheckExecution(SerializableRecord):
    execution_id: str
    check_id: str
    tree_hash: str
    status: CheckStatus
    return_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    stable: bool
    failure_signature: str | None
    first_project_frame: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class CheckComparison(SerializableRecord):
    comparison_id: str
    check_id: str
    baseline: CheckExecution
    patched: CheckExecution
    classification: CheckClassification

    @classmethod
    def create(
        cls,
        baseline: CheckExecution,
        patched: CheckExecution,
        role: CheckRole,
    ) -> "CheckComparison":
        classification = classify_check_pair(baseline, patched, role)
        return cls(
            comparison_id=stable_id(
                "check-comparison", baseline.execution_id,
                patched.execution_id, role.value, classification.value,
            ),
            check_id=baseline.check_id,
            baseline=baseline,
            patched=patched,
            classification=classification,
        )


@dataclass(frozen=True, slots=True)
class EnvironmentPreparation(SerializableRecord):
    preparation_id: str
    status: EnvironmentHealthStatus
    run_directory: str
    environment: dict[str, str]
    environment_hash: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class NormalizedSelector(SerializableRecord):
    original: str
    normalized: str
    valid: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class EnvironmentHealth(SerializableRecord):
    health_id: str
    status: EnvironmentHealthStatus
    detail: str
    execution: CheckExecution | None = None


@dataclass(frozen=True, slots=True)
class RejectedCheck(SerializableRecord):
    check_id: str
    reason: str
    execution_id: str | None
    selector: str


@dataclass(frozen=True, slots=True)
class EnvironmentFrontier(SerializableRecord):
    frontier_id: str
    check_id: str
    health_status: EnvironmentHealthStatus
    reason: str
    execution_id: str | None


def classify_check_pair(
    baseline: CheckExecution,
    patched: CheckExecution,
    role: CheckRole,
) -> CheckClassification:
    """Classify one check using only stable paired process observations."""

    if baseline.check_id != patched.check_id:
        raise ValueError("paired executions must have the same check_id")
    if (
        baseline.status == CheckStatus.UNSUPPORTED
        or patched.status == CheckStatus.UNSUPPORTED
        or baseline.status == CheckStatus.INVALID_SELECTOR
    ):
        return CheckClassification.UNSUPPORTED_CHECK
    if baseline.status == CheckStatus.INVALID_ENVIRONMENT:
        return CheckClassification.SAME_INFRA_FAILURE
    if patched.status in {
        CheckStatus.INVALID_ENVIRONMENT, CheckStatus.INVALID_SELECTOR,
    }:
        return CheckClassification.NEW_INFRA_FAILURE
    if (
        not baseline.stable or not patched.stable
        or baseline.status == CheckStatus.FLAKY
        or patched.status == CheckStatus.FLAKY
    ):
        return CheckClassification.FLAKY_RESULT
    if role == CheckRole.TARGET:
        if baseline.status == CheckStatus.FAIL and patched.status == CheckStatus.PASS:
            return CheckClassification.TARGET_FIXED
        if baseline.status == CheckStatus.FAIL and patched.status == CheckStatus.FAIL:
            return CheckClassification.TARGET_STILL_FAILING
        if baseline.status == CheckStatus.PASS and patched.status == CheckStatus.FAIL:
            return CheckClassification.TARGET_REGRESSED
    if role == CheckRole.PRESERVATION:
        if baseline.status == CheckStatus.PASS and patched.status == CheckStatus.PASS:
            return CheckClassification.PASS_PRESERVED
        if baseline.status == CheckStatus.PASS and patched.status == CheckStatus.FAIL:
            return CheckClassification.PRESERVATION_REGRESSION
    # Exploration checks do not establish target payoff. Stable passes are
    # preservation evidence; stable failures remain unresolved targets.
    if baseline.status == patched.status == CheckStatus.PASS:
        return CheckClassification.PASS_PRESERVED
    if baseline.status == patched.status == CheckStatus.FAIL:
        return CheckClassification.TARGET_STILL_FAILING
    if baseline.status == CheckStatus.FAIL and patched.status == CheckStatus.PASS:
        return CheckClassification.TARGET_FIXED
    if baseline.status == CheckStatus.PASS and patched.status == CheckStatus.FAIL:
        return CheckClassification.TARGET_REGRESSED
    return CheckClassification.FLAKY_RESULT


@dataclass(frozen=True, slots=True)
class ExecutionRun(SerializableRecord):
    run: RunObservation
    trace_events: tuple[DynamicTraceEvent, ...]
    state_snapshots: tuple[dict[str, Any], ...]
    side_effects: tuple[dict[str, Any], ...]
    object_shapes: tuple[dict[str, Any], ...]
    duration_seconds: float
    worker_status: str
    raw_result_hash: str


@dataclass(frozen=True, slots=True)
class TraceBundle(SerializableRecord):
    bundle_id: str
    recipe_id: str
    repository_role: str
    runs: tuple[ExecutionRun, ...]
    stability_status: str
    stable_status: OutcomeStatus
    environment_signature: str
    source_hash: str
    unresolved_reason: str | None


@dataclass(frozen=True, slots=True)
class PairedTraceBundle(SerializableRecord):
    paired_bundle_id: str
    recipe_id: str
    scenario_id: str
    base_bundle: TraceBundle
    patch_bundle: TraceBundle
    classifications: tuple[PairClassification, ...]
    status: OutcomeStatus
    stability_status: str
    first_divergence: dict[str, Any] | None
