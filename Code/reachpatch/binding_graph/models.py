from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Iterable

from reachpatch.models.base import SerializableRecord, content_hash
from reachpatch.models.core import Frontier
from reachpatch.oracle.models import ExecutableScenario, Oracle


class BindingStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    DEFERRED = "DEFERRED"
    INFEASIBLE = "INFEASIBLE"
    EXECUTABLE_TARGET = "EXECUTABLE_TARGET"
    EXECUTABLE_PRESERVATION = "EXECUTABLE_PRESERVATION"
    CHALLENGE_CANDIDATE = "CHALLENGE_CANDIDATE"
    DEFERRED_NORMATIVE = "DEFERRED_NORMATIVE"


@dataclass(frozen=True, slots=True)
class OracleFrontier(SerializableRecord):
    frontier_id: str
    leaf_ids: tuple[str, ...]
    unit_ids: tuple[str, ...]
    observation_channels: tuple[str, ...]
    reason: str
    hard: bool


@dataclass(frozen=True, slots=True)
class ProjectionWitness(SerializableRecord):
    witness_id: str
    compatible: bool
    trigger_projection: dict[str, Any]
    domain_guard_projection: dict[str, Any]
    observation_projection: dict[str, Any]
    oracle_projection: dict[str, Any]
    reason: str


@dataclass(frozen=True, slots=True)
class BindingUnit(SerializableRecord):
    unit_id: str
    path_obligation_id: str
    assignment_scope: str
    leaf_id: str
    authority: str
    trigger_id: str | None
    entrypoint_id: str | None
    path_class_id: str
    interaction_path_ids: tuple[str, ...]
    guard: str
    exit_kind: str
    repair_cut_node_ids: tuple[str, ...]
    observation_node_ids: tuple[str, ...]
    bypass_path_ids: tuple[str, ...]
    preservation_node_ids: tuple[str, ...]
    repair_component_id: str | None
    oracle_id: str | None
    scenario_ids: tuple[str, ...]
    frontier_ids: tuple[str, ...]
    status: str
    requirement_graph_hash: str
    program_graph_hash: str
    projection_witness: ProjectionWitness
    impact_cone_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepairComponent(SerializableRecord):
    component_id: str
    unit_ids: tuple[str, ...]
    common_dominator_ids: tuple[str, ...]
    state_owner_ids: tuple[str, ...]
    dispatch_boundary_ids: tuple[str, ...]
    legal_repair_cut_ids: tuple[str, ...]
    preservation_node_ids: tuple[str, ...]
    interaction_witnesses: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class BindingClosure(SerializableRecord):
    closed: bool
    ready_ratio: float
    missing_path_obligation_ids: tuple[str, ...]
    duplicate_path_obligation_ids: tuple[str, ...]
    stale_unit_ids: tuple[str, ...]
    blocked_unit_ids: tuple[str, ...]
    frontier_ids: tuple[str, ...]
    graph_hash: str


@dataclass(frozen=True, slots=True)
class ExecutableBindingUnit(SerializableRecord):
    unit_id: str
    kind: BindingStatus
    executable_requirement_id: str | None
    normative_requirement_id: str | None
    check_id: str | None
    baseline_execution_id: str | None
    failure_location: dict[str, Any] | None
    entrypoint: str | None
    observation_contract_id: str | None
    causal_slice_id: str | None
    repair_cut_node_ids: tuple[str, ...]
    candidate_repair_cut_ids: tuple[str, ...]
    impact_node_ids: tuple[str, ...]
    cut_status: str


@dataclass(frozen=True, slots=True)
class ExecutableBindingGraph(SerializableRecord):
    units: tuple[ExecutableBindingUnit, ...]
    executable_requirement_overlay_hash: str
    target_slice_id: str
    impact_slice_id: str | None
    graph_hash: str

    @property
    def executable_targets(self) -> tuple[ExecutableBindingUnit, ...]:
        return tuple(
            item for item in self.units
            if item.kind == BindingStatus.EXECUTABLE_TARGET
        )


class BindingGraph(SerializableRecord):
    def __init__(
        self,
        *,
        requirement_graph_hash: str,
        program_graph_hash: str,
        assignment_id: str,
        version: int = 1,
    ) -> None:
        self.requirement_graph_hash = requirement_graph_hash
        self.program_graph_hash = program_graph_hash
        self.assignment_id = assignment_id
        self.version = version
        self.units: dict[str, BindingUnit] = {}
        self.components: dict[str, RepairComponent] = {}
        self.oracles: dict[str, Oracle] = {}
        self.scenarios: dict[str, ExecutableScenario] = {}
        self.frontiers: dict[str, Frontier] = {}
        self.oracle_frontiers: dict[str, OracleFrontier] = {}
        self.by_leaf: dict[str, set[str]] = {}
        self.by_program_node: dict[str, set[str]] = {}
        self.by_path_obligation: dict[str, set[str]] = {}
        self.by_observation: dict[str, set[str]] = {}
        # Runtime telemetry is not part of the constrained-product identity.
        self.build_timings: dict[str, float] = {}
        self.build_stats: dict[str, int] = {}

    def add_unit(self, unit: BindingUnit) -> None:
        previous = self.units.get(unit.unit_id)
        if previous is not None and previous != unit:
            raise ValueError(f"binding unit collision: {unit.unit_id}")
        self.units[unit.unit_id] = unit
        self.by_leaf.setdefault(unit.leaf_id, set()).add(unit.unit_id)
        self.by_path_obligation.setdefault(unit.path_obligation_id, set()).add(unit.unit_id)
        for node_id in unit.interaction_path_ids + unit.repair_cut_node_ids:
            self.by_program_node.setdefault(node_id, set()).add(unit.unit_id)
        for node_id in unit.observation_node_ids:
            self.by_observation.setdefault(node_id, set()).add(unit.unit_id)

    def replace_unit(self, unit_id: str, **changes: Any) -> BindingUnit:
        old = self.units[unit_id]
        updated = replace(old, **changes)
        self.units[unit_id] = updated
        return updated

    def add_frontier(self, frontier: Frontier) -> None:
        self.frontiers[frontier.frontier_id] = frontier

    def to_dict(self) -> dict[str, Any]:
        body = {
            "requirement_graph_hash": self.requirement_graph_hash,
            "program_graph_hash": self.program_graph_hash,
            "assignment_id": self.assignment_id,
            "version": self.version,
            "units": [self.units[key].to_dict() for key in sorted(self.units)],
            "components": [self.components[key].to_dict() for key in sorted(self.components)],
            "oracles": [self.oracles[key].to_dict() for key in sorted(self.oracles)],
            "scenarios": [self.scenarios[key].to_dict() for key in sorted(self.scenarios)],
            "frontiers": [self.frontiers[key].to_dict() for key in sorted(self.frontiers)],
            "oracle_frontiers": [
                self.oracle_frontiers[key].to_dict() for key in sorted(self.oracle_frontiers)
            ],
            "by_leaf": {key: sorted(value) for key, value in sorted(self.by_leaf.items())},
            "by_program_node": {key: sorted(value) for key, value in sorted(self.by_program_node.items())},
            "by_path_obligation": {
                key: sorted(value) for key, value in sorted(self.by_path_obligation.items())
            },
            "by_observation": {key: sorted(value) for key, value in sorted(self.by_observation.items())},
        }
        body["graph_hash"] = content_hash(body)
        return body

    def graph_hash(self) -> str:
        return self.to_dict()["graph_hash"]

    def unit_ids_for_nodes(self, node_ids: Iterable[str]) -> set[str]:
        return {
            unit_id
            for node_id in node_ids
            for unit_id in self.by_program_node.get(node_id, ())
        }
