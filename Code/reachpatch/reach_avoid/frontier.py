"""Evidence-driven RepairFrontier and Reach--Avoid action selection.

This module is intentionally independent of the legacy ``ConfirmedFailure``
record.  A frontier is created whenever the current working patch leaves an
actionable obligation, including mechanical, reproduction, oracle and
localization gaps.  Its identity is content addressed so a restart cannot
silently create a different repair task.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.evidence import (
    OutcomeStatus, PairClassification, PairedTraceBundle,
)


class RepairFrontierKind(StrEnum):
    MECHANICAL_FAILURE = "MECHANICAL_FAILURE"
    BEHAVIOR_FAILURE = "BEHAVIOR_FAILURE"
    PRESERVATION_REGRESSION = "PRESERVATION_REGRESSION"
    LOCALIZATION_FAILURE = "LOCALIZATION_FAILURE"
    REQUIREMENT_COVERAGE_GAP = "REQUIREMENT_COVERAGE_GAP"
    REPRODUCTION_GAP = "REPRODUCTION_GAP"
    OBSERVATION_GAP = "OBSERVATION_GAP"
    IMPACT_RISK = "IMPACT_RISK"


class FrontierStatus(StrEnum):
    PENDING = "PENDING"
    ACTIONABLE = "ACTIONABLE"
    IN_EVIDENCE_RECOVERY = "IN_EVIDENCE_RECOVERY"
    CLOSED = "CLOSED"
    EXHAUSTED = "EXHAUSTED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True, slots=True)
class RepairFrontier(SerializableRecord):
    frontier_id: str
    kind: RepairFrontierKind
    status: FrontierStatus
    patch_hash: str
    graph_revision: int
    authority: str = "B"
    hard: bool = True
    priority: int = 0
    requirement_ids: tuple[str, ...] = ()
    binding_ids: tuple[str, ...] = ()
    challenge_ids: tuple[str, ...] = ()
    path_class_ids: tuple[str, ...] = ()
    changed_hunk_ids: tuple[str, ...] = ()
    causal_cut_ids: tuple[str, ...] = ()
    expected_contract: Any = None
    expected_observation: Any = None
    baseline_observation: Any = None
    patched_observation: Any = None
    execution_route: tuple[str, ...] = ()
    first_project_frame: str | None = None
    repair_slice_ids: tuple[str, ...] = ()
    preservation_lock_ids: tuple[str, ...] = ()
    attempted_mechanism_hashes: tuple[str, ...] = ()
    recovery_attempts: int = 0
    recovery_recipes: tuple[dict[str, Any], ...] = ()
    closure_evidence: tuple[dict[str, Any], ...] = ()
    failure_location: Any = None
    lineage: tuple[str, ...] = ()

    @staticmethod
    def derive_id(
        kind: RepairFrontierKind | str,
        requirement_ids: tuple[str, ...] = (),
        binding_ids: tuple[str, ...] = (),
        expected_contract: Any = None,
        failure_location: Any = None,
        patch_lineage: tuple[str, ...] = (),
    ) -> str:
        return stable_id(
            "repair-frontier", str(kind), tuple(sorted(requirement_ids)),
            tuple(sorted(binding_ids)), _normalize(expected_contract),
            _normalize(failure_location), tuple(patch_lineage),
        )

    @classmethod
    def create(cls, *, kind: RepairFrontierKind, patch_hash: str, graph_revision: int,
               requirement_ids: tuple[str, ...] = (), binding_ids: tuple[str, ...] = (),
               expected_contract: Any = None, failure_location: Any = None,
               **kwargs: Any) -> "RepairFrontier":
        lineage = tuple(kwargs.pop("lineage", (patch_hash,)))
        frontier_id = cls.derive_id(
            kind, requirement_ids, binding_ids, expected_contract, failure_location, lineage,
        )
        return cls(
            frontier_id=frontier_id, kind=kind, status=kwargs.pop("status", FrontierStatus.ACTIONABLE),
            patch_hash=patch_hash, graph_revision=graph_revision,
            requirement_ids=tuple(requirement_ids), binding_ids=tuple(binding_ids),
            expected_contract=expected_contract, failure_location=failure_location,
            lineage=lineage, **kwargs,
        )

    @property
    def actionable(self) -> bool:
        return self.status in {FrontierStatus.PENDING, FrontierStatus.ACTIONABLE}

    @property
    def terminal(self) -> bool:
        return self.status in {FrontierStatus.CLOSED, FrontierStatus.EXHAUSTED, FrontierStatus.SUPERSEDED}


class NextActionKind(StrEnum):
    SEAL = "SEAL"
    RUN_CHALLENGE = "RUN_CHALLENGE"
    RECOVER_EVIDENCE = "RECOVER_EVIDENCE"
    REPAIR = "REPAIR"


@dataclass(frozen=True, slots=True)
class NextAction(SerializableRecord):
    kind: NextActionKind
    reason: str
    challenge_id: str | None = None
    frontier_id: str | None = None


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        values = [_normalize(item) for item in value]
        return sorted(values, key=repr) if isinstance(value, (set, frozenset)) else values
    if hasattr(value, "to_dict"):
        return _normalize(value.to_dict())
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return value


def _frontier_from_mechanical(state: Any, mechanical: Any) -> RepairFrontier | None:
    if mechanical is None or getattr(mechanical, "passed", True):
        return None
    reasons = tuple(getattr(mechanical, "failure_reasons", ()))
    return RepairFrontier.create(
        kind=RepairFrontierKind.MECHANICAL_FAILURE,
        patch_hash=state.graph_stack.patch_hash, graph_revision=state.graph_stack.revision,
        authority="A", hard=True, priority=-100,
        expected_contract={"relation": "PATCH_APPLIES_AND_IMPORTS", "expected": True},
        expected_observation={"mechanical_pass": True},
        patched_observation={"failure_reasons": reasons},
        recovery_recipes=tuple({"error": reason} for reason in reasons),
        failure_location=reasons,
    )


def derive_repair_frontiers(
    state: Any, requirement_graph: Any, program_graph: Any, binding_graph: Any,
    challenge_graph: Any, observations: Any, mechanical_result: Any = None,
) -> dict[str, RepairFrontier]:
    """Derive all currently evidenced repair frontiers.

    The function is deliberately monotonic with respect to evidence: an
    unavailable oracle or an unlocalised trace still produces a frontier; it
    is never silently converted to an empty failure set.
    """
    result: dict[str, RepairFrontier] = {}
    mechanical = _frontier_from_mechanical(state, mechanical_result)
    if mechanical:
        result[mechanical.frontier_id] = mechanical

    cells = getattr(challenge_graph, "active_cells", lambda: ())()
    units = getattr(binding_graph, "units", {})
    for cell in cells:
        execution = getattr(observations, "by_challenge", {}).get(cell.challenge_id)
        if execution is None:
            if cell.terminal_status.value in {"UNKNOWN", "PENDING"} and cell.hard:
                frontier = RepairFrontier.create(
                    kind=RepairFrontierKind.OBSERVATION_GAP if not cell.oracle.trusted else RepairFrontierKind.REPRODUCTION_GAP,
                    patch_hash=cell.patch_hash, graph_revision=getattr(state.graph_stack, "revision", 0),
                    authority=cell.authority, hard=cell.hard, priority=20,
                    requirement_ids=(cell.requirement_id,), binding_ids=(cell.binding_id,),
                    challenge_ids=(cell.challenge_id,), path_class_ids=(cell.path_class_id,),
                    changed_hunk_ids=cell.changed_hunk_ids, expected_contract=cell.observation_contract,
                    expected_observation=cell.oracle.expected,
                    recovery_recipes=({"command": cell.execution_scenario.command, "cwd": cell.execution_scenario.cwd},),
                    failure_location=cell.challenge_id,
                )
                result[frontier.frontier_id] = frontier
            continue
        classification = execution.classification
        if classification in {PairClassification.TARGET_STILL_FAILING, PairClassification.TARGET_REGRESSED, PairClassification.PRESERVATION_REGRESSION}:
            kind = RepairFrontierKind.PRESERVATION_REGRESSION if cell.kind == "PRESERVATION" or classification in {PairClassification.TARGET_REGRESSED, PairClassification.PRESERVATION_REGRESSION} else RepairFrontierKind.BEHAVIOR_FAILURE
            trace = execution.patched
            path_hit = bool(trace.executed_path_ids or trace.executed_symbol_ids or trace.first_project_frame)
            if not path_hit:
                kind = RepairFrontierKind.LOCALIZATION_FAILURE
            unit = units.get(cell.binding_id)
            frontier = RepairFrontier.create(
                kind=kind, patch_hash=cell.patch_hash, graph_revision=getattr(state.graph_stack, "revision", 0),
                authority=execution.oracle_authority, hard=cell.hard, priority=0 if cell.hard else 10,
                requirement_ids=(cell.requirement_id,), binding_ids=(cell.binding_id,),
                challenge_ids=(cell.challenge_id,), path_class_ids=((unit.path_class_id if unit else cell.path_class_id),),
                changed_hunk_ids=cell.changed_hunk_ids, causal_cut_ids=(),
                expected_contract=cell.observation_contract, expected_observation=execution.expected_relation,
                baseline_observation=execution.baseline.observation.to_dict(),
                patched_observation=execution.patched.observation.to_dict(),
                execution_route=execution.patched.executed_path_ids,
                first_project_frame=execution.patched.first_project_frame,
                preservation_lock_ids=tuple(sorted(getattr(state.locked_checks, "preservation_ids", set()))),
                failure_location=execution.patched.first_project_frame or execution.patched.observation.exception,
            )
            result[frontier.frontier_id] = frontier

    leaves = getattr(requirement_graph, "leaves", {})
    for requirement_id, leaf in leaves.items():
        if not getattr(leaf, "hard", True) or getattr(leaf, "preservation", False):
            continue
        related = [cell for cell in cells if cell.requirement_id == requirement_id]
        if not related:
            frontier = RepairFrontier.create(
                kind=RepairFrontierKind.REQUIREMENT_COVERAGE_GAP,
                patch_hash=state.graph_stack.patch_hash, graph_revision=state.graph_stack.revision,
                authority=leaf.authority, hard=True, priority=5, requirement_ids=(requirement_id,),
                expected_contract=leaf.expected_observation, expected_observation=leaf.expected_observation.to_dict(),
                failure_location=requirement_id,
            )
            result[frontier.frontier_id] = frontier
            continue
        if not any(cell.terminal_status.value in {"PASS", "UNREACHABLE"} for cell in related):
            if all(cell.terminal_status.value in {"UNKNOWN", "PENDING", "FAIL"} for cell in related):
                frontier = RepairFrontier.create(
                    kind=RepairFrontierKind.REQUIREMENT_COVERAGE_GAP,
                    patch_hash=state.graph_stack.patch_hash, graph_revision=state.graph_stack.revision,
                    authority=leaf.authority, hard=True, priority=5, requirement_ids=(requirement_id,),
                    binding_ids=tuple(dict.fromkeys(cell.binding_id for cell in related)),
                    challenge_ids=tuple(cell.challenge_id for cell in related),
                    path_class_ids=tuple(dict.fromkeys(cell.path_class_id for cell in related)),
                    changed_hunk_ids=tuple(dict.fromkeys(h for cell in related for h in cell.changed_hunk_ids)),
                    expected_contract=leaf.expected_observation, expected_observation=leaf.expected_observation.to_dict(),
                    failure_location=requirement_id,
                )
                result[frontier.frontier_id] = frontier

    impact = getattr(program_graph, "impact_cone", None)
    if impact is not None:
        unresolved_cells = any(
            cell.hard and cell.terminal_status.value not in {"PASS", "UNREACHABLE"}
            for cell in cells
        )
        if not unresolved_cells:
            return result
        executed = {
            node_id for execution in getattr(observations, "by_challenge", {}).values()
            if execution.patch_hash == state.graph_stack.patch_hash
            for node_id in execution.patched.executed_symbol_ids
        }
        risks = tuple(item for item in impact.all_risk_ids() if item not in executed)
        if risks:
            frontier = RepairFrontier.create(
                kind=RepairFrontierKind.IMPACT_RISK, patch_hash=state.graph_stack.patch_hash,
                graph_revision=state.graph_stack.revision, authority="C", hard=False, priority=50,
                challenge_ids=tuple(), expected_contract={"impact": "replay"},
                expected_observation={"executed": True}, repair_slice_ids=risks,
                failure_location=risks,
            )
            result[frontier.frontier_id] = frontier
    return result


def select_next_action(state: Any, *, max_challenge_rounds: int | None = None) -> NextAction:
    """Select the next action using strict Reach--Avoid priority."""
    reach = getattr(state, "reach_evaluation", None)
    if reach is not None and getattr(reach, "reached", False):
        return NextAction(NextActionKind.SEAL, "strict Reach Set satisfied")
    # The gate is imported lazily to avoid a model/controller import cycle.
    try:
        from .gates import evaluate_reach
        if evaluate_reach(state).reached:
            return NextAction(NextActionKind.SEAL, "strict Reach Set satisfied")
    except Exception:
        pass
    # Challenge execution is evidence gathering, not an unbounded inner loop.
    # The controller budget is passed explicitly; once it is exhausted we
    # retain all observations and hand control to recovery/repair frontiers.
    # This prevents graph expansion from continually creating fresh probes.
    challenge_budget_available = (
        max_challenge_rounds is None
        or int(getattr(state, "challenge_round_count", 0)) < int(max_challenge_rounds)
    )
    cells = (
        tuple(cell for cell in state.graph_stack.challenge_graph.active_cells())
        if challenge_budget_available else ()
    )
    attempts = getattr(state, "challenge_attempts", {})
    recovery_attempts = getattr(state, "frontier_attempts", {})
    # A graph revision is intentionally part of the durable retry key so that a
    # changed patch/recipe/graph can be re-executed.  It must not, however,
    # reset the per-challenge evidence budget: executing a challenge can itself
    # create a new graph revision, and counting only the full key would make an
    # UNKNOWN probe run forever without ever yielding to recovery or repair.
    # The controller config fixes this budget at three attempts.
    def _attempt_count(cell: Any) -> int:
        prefix = "|".join((
            cell.challenge_id, state.graph_stack.patch_hash,
            cell.input_recipe.recipe_id, cell.oracle.oracle_id,
        ))
        return sum(
            int(value) for key, value in attempts.items()
            if str(key) == prefix or str(key).startswith(prefix + "|")
        )
    for cell in sorted(cells, key=lambda item: (not item.hard, item.challenge_id)):
        if (
            cell.terminal_status.value in {"PENDING", "UNKNOWN"}
            and _attempt_count(cell) < 3
        ):
            return NextAction(NextActionKind.RUN_CHALLENGE, "unexecuted evidence-bearing challenge", challenge_id=cell.challenge_id)
    frontiers = getattr(state, "repair_frontiers", {}) or {}
    actionable = [item for item in frontiers.values() if item.actionable and item.patch_hash == state.graph_stack.patch_hash]
    def _recovery_budget_available(item: RepairFrontier) -> bool:
        # Recovery expands evidence, but it must be bounded independently of
        # graph growth.  Otherwise each rebuild can recreate the same gap and
        # keep Reach--Avoid in RECOVER_EVIDENCE forever.
        keys = item.requirement_ids or (item.frontier_id,)
        return any(recovery_attempts.get(str(key), 0) < 3 for key in keys)

    recovery_budget_available = (
        max_challenge_rounds is None
        or int(getattr(state, "challenge_round_count", 0))
        < int(max_challenge_rounds) * 2
    )
    recovery = [
        item for item in actionable
        if item.kind in {
            RepairFrontierKind.REPRODUCTION_GAP,
            RepairFrontierKind.OBSERVATION_GAP,
            RepairFrontierKind.LOCALIZATION_FAILURE,
        }
        and recovery_budget_available
        and _recovery_budget_available(item)
    ]
    if recovery:
        item = min(recovery, key=lambda value: (value.priority, value.frontier_id))
        return NextAction(NextActionKind.RECOVER_EVIDENCE, "evidence recovery required", frontier_id=item.frontier_id)
    if actionable:
        item = min(actionable, key=lambda value: (value.priority, value.frontier_id))
        return NextAction(NextActionKind.REPAIR, "actionable RepairFrontier", frontier_id=item.frontier_id)
    return NextAction(NextActionKind.SEAL, "all challenges, recoveries and frontiers are terminal")
