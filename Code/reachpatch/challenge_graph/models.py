from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from reachpatch.challenge_graph.recipes import InputRecipe
from reachpatch.models.base import SerializableRecord, content_hash
from reachpatch.models.core import Frontier
from reachpatch.models.enums import ChallengeTerminalStatus
from reachpatch.oracle.models import ExecutableScenario


@dataclass(frozen=True, slots=True)
class ChallengePriority(SerializableRecord):
    authority: float
    failure_risk: float
    diff_relevance: float
    information_gain: float
    execution_cost: float

    @property
    def score(self) -> float:
        return (
            self.authority * self.failure_risk * self.diff_relevance
            * self.information_gain / max(self.execution_cost, 0.1)
        )


@dataclass(frozen=True, slots=True)
class ChallengeCell(SerializableRecord):
    challenge_id: str
    binding_unit_id: str
    quantified_partition: dict[str, Any]
    path_class_id: str
    trigger_recipe_id: str | None
    input_constraints: tuple[str, ...]
    observation_contract_id: str
    oracle_id: str | None
    baseline_outcome: str | None
    patched_outcome: str | None
    diff_dependency: dict[str, Any]
    stability_status: str
    terminal_status: ChallengeTerminalStatus
    evidence: tuple[str, ...]
    scenario_id: str | None
    operator_id: str
    changed_dimension: str
    origin: str
    hard: bool
    graph_hashes: dict[str, str]
    execution_bundle_id: str | None = None


@dataclass(frozen=True, slots=True)
class ScenarioProposal(SerializableRecord):
    proposal_id: str
    binding_unit_id: str
    operator_id: str
    graph_witness_ids: tuple[str, ...]
    public_trigger_id: str | None
    partition_id: str
    route_id: str
    recipe: InputRecipe
    scenario: ExecutableScenario
    locked_oracle_id: str
    changed_dimension: str
    expected_observation_class: tuple[str, ...]
    unresolved_fields: tuple[str, ...]
    origin: str
    hard: bool


@dataclass(frozen=True, slots=True)
class DiffObligation(SerializableRecord):
    obligation_id: str
    origin_update_id: str
    kind: str
    baseline_path_obligation_id: str | None
    binding_unit_id: str
    public_trigger_id: str | None
    partition_id: str
    route_id: str
    observation_id: str
    oracle_id: str | None
    evidence_cluster_id: str | None
    relation_witness_ids: tuple[str, ...]
    closure_status: str
    proof_or_frontier_id: str | None
    safety_relevant: bool
    changed_relation_id: str
    input_constraints: tuple[str, ...] = ()
    expected_exit: str = "normal"


@dataclass(frozen=True, slots=True)
class ChangedEdgeLedgerRecord(SerializableRecord):
    ledger_id: str
    changed_relation_id: str
    handler_id: str | None
    obligation_ids: tuple[str, ...]
    status: str
    frontier_id: str | None


@dataclass(frozen=True, slots=True)
class DiffChallengePlan(SerializableRecord):
    update_id: str
    baseline_path_obligation_ids: tuple[str, ...]
    overlay_obligation_ids: tuple[str, ...]
    overlay_check_ids: tuple[str, ...]
    structural_discharges: dict[str, str]
    changed_edge_ledger_ids: tuple[str, ...]
    hard_frontier_ids: tuple[str, ...]
    residual_risk_frontier_ids: tuple[str, ...]
    obligations: tuple[DiffObligation, ...]
    changed_edge_ledger: tuple[ChangedEdgeLedgerRecord, ...]
    challenge_graph_hash: str


@dataclass(frozen=True, slots=True)
class DiffClosureCertificate(SerializableRecord):
    closure_id: str
    update_id: str
    checkpoint_id: str
    transition_index: int
    baseline_path_obligation_ids: tuple[str, ...]
    overlay_obligation_ids: tuple[str, ...]
    obligation_result_ids: tuple[str, ...]
    causal_touch_witnesses: dict[str, list[str]]
    invalidated_node_ids: tuple[str, ...]
    changed_guard_obligation_ids: tuple[str, ...]
    call_exit_obligation_ids: tuple[str, ...]
    fallback_obligation_ids: tuple[str, ...]
    state_dispatch_obligation_ids: tuple[str, ...]
    bypass_obligation_ids: tuple[str, ...]
    preservation_caller_obligation_ids: tuple[str, ...]
    hard_frontier_ids: tuple[str, ...]
    residual_risk_frontier_ids: tuple[str, ...]
    oracle_change_ids: tuple[str, ...]
    stale_record_ids: tuple[str, ...]
    changed_edge_ledger_ids: tuple[str, ...]
    commit_safety_closed: bool
    diff_challenge_closed: bool
    source_graph_oracle_hashes: dict[str, str]
    recomputation_hash: str
    plan_payload: dict[str, Any] = field(default_factory=dict)
    updated_obligations: tuple[dict[str, Any], ...] = ()


class ChallengeGraph(SerializableRecord):
    def __init__(
        self,
        *,
        requirement_graph_hash: str,
        program_graph_hash: str,
        binding_graph_hash: str,
        diff_hash: str = "BASELINE",
        version: int = 1,
    ) -> None:
        self.requirement_graph_hash = requirement_graph_hash
        self.program_graph_hash = program_graph_hash
        self.binding_graph_hash = binding_graph_hash
        self.diff_hash = diff_hash
        self.version = version
        self.cells: dict[str, ChallengeCell] = {}
        self.recipes: dict[str, InputRecipe] = {}
        self.scenarios: dict[str, ExecutableScenario] = {}
        self.frontiers: dict[str, Frontier] = {}
        self.by_binding_unit: dict[str, set[str]] = {}
        self.by_partition: dict[str, set[str]] = {}
        self.by_path_class: dict[str, set[str]] = {}
        self.priorities: dict[str, ChallengePriority] = {}

    def add_cell(
        self,
        cell: ChallengeCell,
        *,
        recipe: InputRecipe | None,
        scenario: ExecutableScenario | None,
    ) -> None:
        previous = self.cells.get(cell.challenge_id)
        if previous is not None and previous != cell:
            raise ValueError(f"challenge collision: {cell.challenge_id}")
        self.cells[cell.challenge_id] = cell
        if recipe:
            self.recipes[recipe.recipe_id] = recipe
        if scenario:
            self.scenarios[scenario.scenario_id] = scenario
        self.by_binding_unit.setdefault(cell.binding_unit_id, set()).add(cell.challenge_id)
        partition_id = str(cell.quantified_partition.get("partition_id", ""))
        self.by_partition.setdefault(partition_id, set()).add(cell.challenge_id)
        self.by_path_class.setdefault(cell.path_class_id, set()).add(cell.challenge_id)

    def add_frontier(self, frontier: Frontier) -> None:
        self.frontiers[frontier.frontier_id] = frontier

    def update_cell(self, challenge_id: str, **changes: Any) -> ChallengeCell:
        updated = replace(self.cells[challenge_id], **changes)
        self.cells[challenge_id] = updated
        return updated

    def to_dict(self) -> dict[str, Any]:
        body = {
            "requirement_graph_hash": self.requirement_graph_hash,
            "program_graph_hash": self.program_graph_hash,
            "binding_graph_hash": self.binding_graph_hash,
            "diff_hash": self.diff_hash,
            "version": self.version,
            "cells": [self.cells[key].to_dict() for key in sorted(self.cells)],
            "recipes": [self.recipes[key].to_dict() for key in sorted(self.recipes)],
            "scenarios": [self.scenarios[key].to_dict() for key in sorted(self.scenarios)],
            "frontiers": [self.frontiers[key].to_dict() for key in sorted(self.frontiers)],
            "by_binding_unit": {key: sorted(value) for key, value in sorted(self.by_binding_unit.items())},
            "by_partition": {key: sorted(value) for key, value in sorted(self.by_partition.items())},
            "by_path_class": {key: sorted(value) for key, value in sorted(self.by_path_class.items())},
            "priorities": {
                key: value.to_dict() for key, value in sorted(self.priorities.items())
            },
        }
        body["graph_hash"] = content_hash(body)
        return body

    def graph_hash(self) -> str:
        return self.to_dict()["graph_hash"]

    def terminal_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cell in self.cells.values():
            counts[cell.terminal_status.value] = counts.get(cell.terminal_status.value, 0) + 1
        return counts
