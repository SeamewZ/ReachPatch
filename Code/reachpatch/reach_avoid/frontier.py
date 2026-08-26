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
from pathlib import Path
from typing import Any, Mapping

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.evidence import (
    OutcomeStatus, PairClassification, PairedTraceBundle,
)
from reachpatch.models.graphs import ChallengeStatus
from .semantics import (
    input_partition_semantic_key,
    normalize_execution_contract,
    repair_frontier_semantic_key,
)


def _missing_requirement_slice(
    state: Any, program_graph: Any, leaf: Any,
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """Find a bounded edit slice for a target leaf with no BindingUnit.

    Requirement compilation can legitimately produce a hard target before a
    dynamic route exists.  That is a repair mismatch, not permission to seal
    the current patch.  Use only files touched by the current working diff
    (and the already-built local ProgramGraph) so this fallback does not
    become a repository scan.
    """
    try:
        from reachpatch.execution.worktree import diff_between
        diff = diff_between(
            state.base_repository,
            Path(state.working_checkpoint.snapshot_tree),
        )
    except (OSError, RuntimeError, ValueError):
        return (), (), str(getattr(leaf, "operation", ""))
    changed_paths = set(diff.changed_files)
    nodes = [
        node for node in getattr(program_graph, "nodes", {}).values()
        if getattr(node, "editable", False)
        and getattr(node, "path", "") in changed_paths
        and getattr(getattr(node, "kind", None), "value", getattr(node, "kind", None))
        not in {"MODULE", "CLASS"}
    ]
    # A generated diff may touch a file before the local graph has an exact
    # changed-line node.  Prefer nodes whose symbol mentions the requirement
    # operation, then fall back to the smallest editable changed-file slice.
    operation = str(getattr(leaf, "operation", "")).casefold()
    tokens = tuple(part for part in operation.replace("::", ".").split(".") if part)
    changed_symbols = tuple(
        symbol.casefold() for symbol in diff.changed_symbols if symbol
    )
    preferred = [
        node for node in nodes
        if any(token in str(getattr(node, "symbol", "")).casefold() for token in tokens)
        or any(symbol in str(getattr(node, "symbol", "")).casefold() for symbol in changed_symbols)
    ]

    def relevance(node: Any) -> tuple[int, str, int, str]:
        symbol = str(getattr(node, "symbol", "")).casefold()
        leaf_name = tokens[-1] if tokens else ""
        if leaf_name and leaf_name in symbol:
            rank = 0
        elif any(value in symbol for value in changed_symbols):
            rank = 1
        elif any(token in symbol for token in tokens):
            rank = 2
        else:
            rank = 3
        return (
            rank, str(getattr(node, "path", "")),
            int(getattr(node, "start_line", 0)),
            str(getattr(node, "node_id", "")),
        )

    selected = sorted(
        preferred or nodes,
        key=relevance,
    )[:12]
    if not selected:
        return (), (), operation
    paths = {str(getattr(node, "path", "")) for node in selected}
    hunk_ids = tuple(
        hunk.hunk_id for hunk in diff.hunks
        if hunk.path in paths
    )
    source_symbol = min(
        (str(getattr(node, "symbol", "")) for node in selected),
        key=lambda value: (value.count("."), value),
    )
    return (
        tuple(str(getattr(node, "node_id", "")) for node in selected),
        hunk_ids,
        source_symbol or operation,
    )


class RepairFrontierKind(StrEnum):
    MECHANICAL_FAILURE = "MECHANICAL_FAILURE"
    BEHAVIOR_FAILURE = "BEHAVIOR_FAILURE"
    PRESERVATION_REGRESSION = "PRESERVATION_REGRESSION"
    LOCALIZATION_FAILURE = "LOCALIZATION_FAILURE"
    REQUIREMENT_COVERAGE_GAP = "REQUIREMENT_COVERAGE_GAP"
    ISSUE_DIFF_MISMATCH = "ISSUE_DIFF_MISMATCH"


class FrontierStatus(StrEnum):
    PENDING = "PENDING"
    ACTIONABLE = "ACTIONABLE"
    IN_EVIDENCE_RECOVERY = "IN_EVIDENCE_RECOVERY"
    CLOSED = "CLOSED"
    EXHAUSTED = "EXHAUSTED"
    SUPERSEDED = "SUPERSEDED"


def repair_frontier_kind_rank(item: "RepairFrontier") -> int:
    """Return the architecture-defined primary repair ordering."""
    if item.kind is RepairFrontierKind.MECHANICAL_FAILURE:
        return 0
    if (
        item.kind is RepairFrontierKind.BEHAVIOR_FAILURE
        and item.authority in {"A", "B", "C"}
    ):
        return 1
    return {
        RepairFrontierKind.LOCALIZATION_FAILURE: 2,
        RepairFrontierKind.REQUIREMENT_COVERAGE_GAP: 3,
        RepairFrontierKind.ISSUE_DIFF_MISMATCH: 4,
        RepairFrontierKind.PRESERVATION_REGRESSION: 5,
        RepairFrontierKind.BEHAVIOR_FAILURE: 6,
    }.get(item.kind, 99)


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
    requirement_contract_id: str = ""
    input_partition_id: str | None = None
    source_symbol: str = ""
    failure_signature: str = ""

    @property
    def semantic_key(self) -> str:
        """Patch-independent identity used to compare frontier evidence."""
        return repair_frontier_semantic_key(
            kind=self.kind,
            requirement_contract_id=self.requirement_contract_id
            or stable_id("requirement-contract", tuple(sorted(self.requirement_ids))),
            input_partition_key=self.input_partition_id or "",
            source_symbol=self.source_symbol,
            failure_signature=self.failure_signature or _normalize(self.failure_location),
        )

    @property
    def instance_id(self) -> str:
        return self.frontier_id

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
        """Whether this frontier has enough evidence for an edit request.

        ``ACTIONABLE`` is deliberately not just a lifecycle label.  The
        controller may only ask the repair agent to edit when the selected
        frontier carries a concrete, verifiable repair basis.  Gaps that need
        recipe/oracle/localization recovery stay in evidence recovery instead
        of inviting an unsupported guess.
        """
        if self.status not in {FrontierStatus.PENDING, FrontierStatus.ACTIONABLE}:
            return False
        if self.kind is RepairFrontierKind.MECHANICAL_FAILURE:
            return bool(self.failure_location or self.recovery_recipes)
        if self.kind in {
            RepairFrontierKind.BEHAVIOR_FAILURE,
            RepairFrontierKind.PRESERVATION_REGRESSION,
        }:
            return bool(self.repair_slice_ids and (
                self.challenge_ids or self.expected_contract is not None
            ))
        if self.kind is RepairFrontierKind.REQUIREMENT_COVERAGE_GAP:
            return bool(
                self.repair_slice_ids
                and self.execution_route
                and self.challenge_ids
            )
        if self.kind is RepairFrontierKind.ISSUE_DIFF_MISMATCH:
            return bool(self.repair_slice_ids and self.requirement_ids)
        if self.kind is RepairFrontierKind.LOCALIZATION_FAILURE:
            # A localization frontier becomes editable only after a dynamic
            # recovery supplied the real route/frame and a bounded source
            # slice.  It remains open until a later trial aligns the diff.
            return bool(
                self.repair_slice_ids
                and self.challenge_ids
                and (self.execution_route or self.first_project_frame)
            )
        return False

    @property
    def terminal(self) -> bool:
        return self.status in {FrontierStatus.CLOSED, FrontierStatus.EXHAUSTED, FrontierStatus.SUPERSEDED}


class NextActionKind(StrEnum):
    SEAL = "SEAL"
    RUN_CHALLENGE = "RUN_CHALLENGE"
    RECOVER_EVIDENCE = "RECOVER_EVIDENCE"
    REPAIR = "REPAIR"
    REPAIR_EVIDENCE_LIMITED = "REPAIR_EVIDENCE_LIMITED"


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


def _stable_source_symbol(program_graph: Any, unit: Any, fallback: str) -> str:
    """Return a patch-independent source identity for frontier semantics."""
    symbols = []
    for node_id in getattr(unit, "program_symbol_ids", ()):
        node = getattr(program_graph, "nodes", {}).get(node_id)
        if node is not None and getattr(node, "symbol", None):
            symbols.append(str(node.symbol))
    if symbols:
        return min(symbols, key=lambda value: (value.count("."), value))
    return str(fallback or "")


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


def _is_confirmed_trial_preservation_regression(cell: Any, execution: Any) -> bool:
    """Return whether a trial, rather than p0, broke stable behavior.

    A baseline-to-p0 difference is a validation risk: there is no incumbent
    working mechanism to preserve yet.  A preservation repair frontier is
    justified only when the triplet executor observed the *same* scenario
    passing on the incumbent and failing on the trial.
    """
    previous = getattr(execution, "previous", None)
    patched = getattr(execution, "patched", None)
    return bool(
        previous is not None
        and patched is not None
        and getattr(execution, "oracle_authority", "PROVISIONAL")
        in {"A", "B", "C"}
        and getattr(execution, "stable_runs", 0) >= 2
        and getattr(previous, "stable_runs", 0) >= 2
        and getattr(patched, "stable_runs", 0) >= 2
        and getattr(getattr(previous, "observation", None), "status", None)
        is OutcomeStatus.PASS
        and getattr(getattr(patched, "observation", None), "status", None)
        is OutcomeStatus.FAIL
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

    cells = tuple(getattr(challenge_graph, "active_cells", lambda: ())())
    units = getattr(binding_graph, "units", {})
    for cell in cells:
        execution = getattr(observations, "by_challenge", {}).get(cell.challenge_id)
        if execution is None:
            # Unexecuted cells are validation backlog, never repair frontiers.
            continue
        classification = execution.classification
        is_preservation_regression = _is_confirmed_trial_preservation_regression(
            cell, execution,
        )
        leaf = getattr(requirement_graph, "leaves", {}).get(
            cell.requirement_id,
        )
        is_preservation_scenario = bool(
            cell.kind == "PRESERVATION"
            or getattr(leaf, "preservation", False)
        )
        if is_preservation_scenario and not is_preservation_regression:
            # Baseline differences and provisional preservation probes are
            # bounded validation backlog.  They cannot become behavior or
            # localization repair targets without an A/B/C incumbent PASS ->
            # trial FAIL transition for this exact scenario.
            continue
        if (
            classification is PairClassification.TARGET_STILL_FAILING
            or is_preservation_regression
        ):
            kind = (
                RepairFrontierKind.PRESERVATION_REGRESSION
                if is_preservation_regression
                else RepairFrontierKind.BEHAVIOR_FAILURE
            )
            trace = execution.patched
            path_hit = bool(trace.executed_path_ids or trace.executed_symbol_ids or trace.first_project_frame)
            if not path_hit and not is_preservation_regression:
                kind = RepairFrontierKind.LOCALIZATION_FAILURE
            unit = units.get(cell.binding_id)
            repair_slices = tuple(unit.program_symbol_ids) if unit is not None else ()
            role = "PRESERVATION" if is_preservation_regression else "TARGET"
            target_contract = normalize_execution_contract(
                cell.observation_contract, role=role,
            )
            frontier = RepairFrontier.create(
                kind=kind, patch_hash=cell.patch_hash, graph_revision=getattr(state.graph_stack, "revision", 0),
                authority=execution.oracle_authority, hard=cell.hard, priority=0 if cell.hard else 10,
                requirement_ids=(cell.requirement_id,), binding_ids=(cell.binding_id,),
                challenge_ids=(cell.challenge_id,), path_class_ids=((unit.path_class_id if unit else cell.path_class_id),),
                changed_hunk_ids=cell.changed_hunk_ids,
                expected_contract=target_contract, expected_observation={
                    "observable": target_contract.observable,
                    "expected": target_contract.expected,
                    "comparator": target_contract.normalized_comparator,
                    "authority": execution.oracle_authority,
                },
                baseline_observation=execution.baseline.observation.to_dict(),
                patched_observation=execution.patched.observation.to_dict(),
                execution_route=execution.patched.executed_path_ids,
                first_project_frame=execution.patched.first_project_frame,
                repair_slice_ids=repair_slices,
                causal_cut_ids=(
                    tuple(unit.causal_cut_ids) if unit is not None else ()
                ),
                preservation_lock_ids=tuple(sorted(getattr(state.locked_checks, "preservation_ids", set()))),
                failure_location=execution.patched.first_project_frame or execution.patched.observation.exception,
                status=(
                    FrontierStatus.IN_EVIDENCE_RECOVERY
                    if kind is RepairFrontierKind.LOCALIZATION_FAILURE
                    or not repair_slices
                    else FrontierStatus.ACTIONABLE
                ),
                requirement_contract_id=target_contract.contract_id,
                input_partition_id=input_partition_semantic_key(cell.input_recipe),
                source_symbol=_stable_source_symbol(
                    program_graph, unit, cell.requirement_id,
                ),
                failure_signature=(
                    cell.observation_contract.contract_id
                ),
            )
            result[frontier.frontier_id] = frontier

    leaves = getattr(requirement_graph, "leaves", {})
    for requirement_id, leaf in leaves.items():
        if not getattr(leaf, "hard", True) or getattr(leaf, "preservation", False):
            continue
        related = [cell for cell in cells if cell.requirement_id == requirement_id]
        # Binding is deliberately created before diff alignment.  When the
        # requirement has a real candidate path but the working diff touches
        # somewhere else, preserve each disjoint path as an independent
        # coverage frontier.  It carries the actual source slice and can move
        # through evidence recovery into an executable repair objective.
        disjoint_units = [
            unit for unit in units.values()
            if unit.requirement_id == requirement_id
            and getattr(unit, "alignment_status", "UNKNOWN") == "DISJOINT"
        ]
        for unit in disjoint_units:
            unit_challenges = tuple(
                challenge_id for challenge_id in unit.challenge_ids
                if challenge_id in getattr(challenge_graph, "cells", {})
            )
            # A frontier partition is a scenario identity, not a graph-local
            # branch or path identifier.  Use the same input semantic key as
            # AtomicObligation whenever an executable cell exists.  A route
            # without an input remains unpartitioned until recovery creates a
            # concrete scenario.
            unit_cell = next((
                challenge_graph.cells[challenge_id]
                for challenge_id in unit_challenges
                if challenge_id in challenge_graph.cells
            ), None)
            frontier = RepairFrontier.create(
                kind=RepairFrontierKind.REQUIREMENT_COVERAGE_GAP,
                patch_hash=state.graph_stack.patch_hash,
                graph_revision=state.graph_stack.revision,
                authority=leaf.authority, hard=True, priority=4,
                requirement_ids=(requirement_id,), binding_ids=(unit.binding_id,),
                challenge_ids=unit_challenges, path_class_ids=(unit.path_class_id,),
                changed_hunk_ids=unit.changed_hunk_ids,
                expected_contract=normalize_execution_contract(
                    leaf.expected_observation, role="TARGET",
                ),
                expected_observation=normalize_execution_contract(
                    leaf.expected_observation, role="TARGET",
                ).to_dict(),
                execution_route=(unit.path_class_id,),
                repair_slice_ids=tuple(unit.program_symbol_ids),
                causal_cut_ids=tuple(unit.causal_cut_ids),
                recovery_recipes=({
                    "kind": "REQUIREMENT_PATH_DISJOINT",
                    "binding_id": unit.binding_id,
                    "path_class_id": unit.path_class_id,
                    "alignment": "DISJOINT",
                },),
                failure_location={
                    "binding_id": unit.binding_id,
                    "path_class_id": unit.path_class_id,
                    "alignment": "DISJOINT",
                },
                status=(
                    FrontierStatus.ACTIONABLE if unit_challenges
                    else FrontierStatus.IN_EVIDENCE_RECOVERY
                ),
                requirement_contract_id=normalize_execution_contract(
                    leaf.expected_observation, role="TARGET",
                ).contract_id,
                input_partition_id=(
                    input_partition_semantic_key(unit_cell.input_recipe)
                    if unit_cell is not None else None
                ),
                source_symbol=_stable_source_symbol(
                    program_graph, unit, leaf.operation,
                ),
                failure_signature="requirement-path-diff-disjoint",
            )
            result[frontier.frontier_id] = frontier
        # A hard requirement with only unexecuted, statically unaligned
        # candidates is a review/repair mismatch, rather than a reproduction
        # frontier.  It is the one bounded repair item allowed before a
        # challenge has run: the current diff has no causal alignment to the
        # requirement's real source slice.  Pending cells themselves remain
        # validation backlog and are not promoted to a failure frontier.
        trusted_pass = any(
            cell.terminal_status is ChallengeStatus.PASS
            and cell.stability_runs >= 2
            and cell.authority in {"A", "B", "C"}
            for cell in related
        )
        if not trusted_pass:
            mismatch_unit = next((
                unit for unit in units.values()
                if unit.requirement_id == requirement_id
                and getattr(unit, "program_symbol_ids", ())
                and getattr(unit, "alignment_status", "UNKNOWN") != "ALIGNED"
            ), None)
            if mismatch_unit is not None:
                mismatch_cell = next((
                    challenge_graph.cells[challenge_id]
                    for challenge_id in mismatch_unit.challenge_ids
                    if challenge_id in challenge_graph.cells
                ), None)
                frontier = RepairFrontier.create(
                    kind=RepairFrontierKind.ISSUE_DIFF_MISMATCH,
                    patch_hash=state.graph_stack.patch_hash,
                    graph_revision=state.graph_stack.revision,
                    authority="PROVISIONAL", hard=True, priority=5,
                    requirement_ids=(requirement_id,),
                    binding_ids=(mismatch_unit.binding_id,),
                    challenge_ids=tuple(mismatch_unit.challenge_ids),
                    path_class_ids=(mismatch_unit.path_class_id,),
                    changed_hunk_ids=tuple(mismatch_unit.changed_hunk_ids),
                    expected_contract=normalize_execution_contract(
                        leaf.expected_observation, role="TARGET",
                    ),
                    expected_observation=normalize_execution_contract(
                        leaf.expected_observation, role="TARGET",
                    ).to_dict(),
                    repair_slice_ids=tuple(mismatch_unit.program_symbol_ids),
                    causal_cut_ids=tuple(mismatch_unit.causal_cut_ids),
                    failure_location={
                        "requirement_id": requirement_id,
                        "binding_id": mismatch_unit.binding_id,
                        "reason": "no current diff causal alignment",
                    },
                    requirement_contract_id=normalize_execution_contract(
                        leaf.expected_observation, role="TARGET",
                    ).contract_id,
                    input_partition_id=(
                        input_partition_semantic_key(mismatch_cell.input_recipe)
                        if mismatch_cell is not None else None
                    ),
                    source_symbol=_stable_source_symbol(
                        program_graph, mismatch_unit, leaf.operation,
                    ),
                    failure_signature="issue-diff-mismatch",
                    status=FrontierStatus.ACTIONABLE,
                )
                result[frontier.frontier_id] = frontier
            elif not related:
                # A hard target may have no ChallengeCell and no BindingUnit
                # yet (the common state immediately after p0 graph sync).
                # Keep it actionable when the current diff supplies a bounded
                # editable slice.  Otherwise Reach--Avoid would seal a failing
                # p0 solely because evidence compilation was incomplete.
                slice_ids, hunk_ids, source_symbol = _missing_requirement_slice(
                    state, program_graph, leaf,
                )
                if slice_ids:
                    contract = normalize_execution_contract(
                        leaf.expected_observation, role="TARGET",
                    )
                    gap_ids = tuple(
                        str(getattr(gap, "gap_id", None) or gap.get("gap_id"))
                        for gap in getattr(binding_graph, "gaps", ())
                        if (getattr(gap, "requirement_id", None) or gap.get("requirement_id")) == requirement_id
                        and (getattr(gap, "gap_id", None) or gap.get("gap_id"))
                    )
                    frontier = RepairFrontier.create(
                        kind=RepairFrontierKind.ISSUE_DIFF_MISMATCH,
                        patch_hash=state.graph_stack.patch_hash,
                        graph_revision=state.graph_stack.revision,
                        authority="PROVISIONAL", hard=True, priority=5,
                        requirement_ids=(requirement_id,),
                        expected_contract=contract,
                        expected_observation=contract.to_dict(),
                        changed_hunk_ids=hunk_ids,
                        repair_slice_ids=slice_ids,
                        recovery_recipes=({
                            "kind": "TARGET_BINDING_GAP",
                            "gap_ids": gap_ids,
                            "operation": str(getattr(leaf, "operation", "")),
                        },),
                        failure_location={
                            "requirement_id": requirement_id,
                            "reason": "hard target has no executable binding",
                            "gap_ids": gap_ids,
                        },
                        requirement_contract_id=contract.contract_id,
                        input_partition_id=None,
                        source_symbol=source_symbol,
                        failure_signature="target-binding-gap",
                        status=FrontierStatus.ACTIONABLE,
                    )
                    result[frontier.frontier_id] = frontier
        if not related:
            # Missing routes are validation work until a concrete command has
            # executed and entered project code. ISSUE_DIFF_MISMATCH above is
            # the sole source-backed review frontier permitted in this state.
            continue
        # A FAIL on an existing executable cell is a behavior frontier, not a
        # coverage gap.  Treating it as both created duplicate repair requests
        # and obscured the selected target frontier.  Coverage is introduced
        # only when recovery finds a distinct, unbound partition.

    # Aggregate equivalent repair semantics and keep at most two active items.
    aggregated: dict[str, RepairFrontier] = {}
    for frontier in result.values():
        current = aggregated.get(frontier.semantic_key)
        if current is None or (frontier.actionable, -frontier.priority, frontier.frontier_id) > (current.actionable, -current.priority, current.frontier_id):
            aggregated[frontier.semantic_key] = frontier
    allowed = {
        RepairFrontierKind.MECHANICAL_FAILURE, RepairFrontierKind.BEHAVIOR_FAILURE,
        RepairFrontierKind.LOCALIZATION_FAILURE, RepairFrontierKind.REQUIREMENT_COVERAGE_GAP,
        RepairFrontierKind.ISSUE_DIFF_MISMATCH, RepairFrontierKind.PRESERVATION_REGRESSION,
    }
    candidates = [item for item in aggregated.values() if item.kind in allowed]
    authority_rank = {"A": 4, "B": 3, "C": 2, "PROVISIONAL": 1}
    candidates.sort(key=lambda item: (
        repair_frontier_kind_rank(item), not item.hard, not item.actionable,
        -authority_rank.get(item.authority, 0), item.semantic_key,
    ))
    return {item.frontier_id: item for item in candidates[:2]}


def select_next_action(state: Any, *, max_challenge_rounds: int | None = None) -> NextAction:
    """Adapt the single production scheduler to the controller action enum."""
    from .scheduler import select_next_scheduled_action
    scheduled = select_next_scheduled_action(state)
    mapping = {
        "RUN_PRIMARY_CHALLENGE": NextActionKind.RUN_CHALLENGE,
        "RECOVER_PRIMARY_EVIDENCE": NextActionKind.RECOVER_EVIDENCE,
        "REPAIR_PRIMARY": NextActionKind.REPAIR,
        "REPAIR_EVIDENCE_LIMITED": NextActionKind.REPAIR_EVIDENCE_LIMITED,
        "SEAL": NextActionKind.SEAL,
    }
    return NextAction(
        mapping[scheduled.action], scheduled.reason,
        challenge_id=scheduled.challenge_id,
        frontier_id=scheduled.primary_frontier_id or None,
    )
