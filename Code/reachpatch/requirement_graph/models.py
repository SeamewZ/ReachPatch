from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from reachpatch.models.base import SerializableRecord, content_hash, stable_id
from reachpatch.models.core import Frontier
from reachpatch.models.enums import Authority, LedgerStatus, RequirementAuthorityClass
from reachpatch.models.graph import GraphEdge, GraphNode, TypedMultiGraph


@dataclass(frozen=True, slots=True)
class QuantifiedVariable(SerializableRecord):
    name: str
    domain_id: str
    type_hints: tuple[str, ...] = ("Any",)
    source_expression: str = ""


@dataclass(frozen=True, slots=True)
class DomainSpec(SerializableRecord):
    domain_id: str
    variable: str
    type_names: tuple[str, ...]
    literal_values: tuple[Any, ...]
    lower_bound: float | None = None
    upper_bound: float | None = None
    container_shapes: tuple[str, ...] = ()
    open_world: bool = True


@dataclass(frozen=True, slots=True)
class DomainPartition(SerializableRecord):
    partition_id: str
    variable_names: tuple[str, ...]
    constraints: tuple[str, ...]
    candidate_bindings: tuple[dict[str, Any], ...]
    source: str
    scope: str
    satisfiable: bool
    proof: dict[str, Any]
    witness_ids: tuple[str, ...] = ()
    leaf_id: str | None = None


@dataclass(frozen=True, slots=True)
class RequirementLeaf(SerializableRecord):
    leaf_id: str
    objective_id: str
    formula: str
    quantified_variables: tuple[QuantifiedVariable, ...]
    domains: tuple[DomainSpec, ...]
    precondition: str
    trigger: str
    entrypoint_hypotheses: tuple[str, ...]
    required_trace_relation: dict[str, Any]
    observation_contract: dict[str, Any]
    exception_contract: dict[str, Any]
    state_contract: dict[str, Any]
    preservation_contract: dict[str, Any]
    witnesses: tuple[str, ...]
    authority: Authority
    authority_class: RequirementAuthorityClass
    supporting_evidence: tuple[str, ...]
    hypothesis_id: str | None
    coverage_status: str
    mandatory: bool
    weight: float


@dataclass(frozen=True, slots=True)
class RequirementHyperEdge(SerializableRecord):
    requirement_edge_id: str
    source_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    relation: str
    guard: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RequirementPathObligation(SerializableRecord):
    path_obligation_id: str
    leaf_id: str
    authority: str
    scenario_partition_id: str
    public_trigger_id: str | None
    entrypoint_id: str | None
    path_class_id: str
    path_edge_ids: tuple[str, ...]
    accumulated_guard: str
    exit_kind: str
    observation_id: str
    predicate_oracle_id: str | None
    preservation_caller_ids: tuple[str, ...]
    dependence_slice_ids: tuple[str, ...]
    base_feasible: bool
    frontier_ids: tuple[str, ...]
    requirement_graph_hash: str
    program_graph_hash: str
    partition: DomainPartition
    trigger_recipe: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PathEdgeLedgerRecord(SerializableRecord):
    ledger_id: str
    path_state_id: str
    program_edge_id: str
    relation_kind: str
    status: LedgerStatus
    proof_id: str | None
    frontier_id: str | None
    # Incremental refreshes need an explicit owner.  ``path_state_id`` is a
    # content hash, so it cannot be decoded to determine which leaf was
    # invalidated after a diff.
    leaf_id: str = ""


@dataclass(frozen=True, slots=True)
class RequirementClosure(SerializableRecord):
    closed: bool
    path_coverage: float
    missing_leaf_ids: tuple[str, ...]
    nonterminal_ledger_ids: tuple[str, ...]
    frontier_ids: tuple[str, ...]
    graph_hash: str


class RequirementGraph(TypedMultiGraph):
    def __init__(self, *, assignment_id: str, version: int = 1) -> None:
        super().__init__(graph_kind="requirement", version=version)
        self.assignment_id = assignment_id
        self.leaves: dict[str, RequirementLeaf] = {}
        self.domains: dict[str, DomainSpec] = {}
        self.partitions: dict[str, DomainPartition] = {}
        self.hyperedges: dict[str, RequirementHyperEdge] = {}
        self.path_obligations: dict[str, RequirementPathObligation] = {}
        self.edge_ledger: dict[str, PathEdgeLedgerRecord] = {}
        self.frontiers: dict[str, Frontier] = {}
        self.authority_snapshot_hash = ""
        # Runtime telemetry is excluded from the Requirement Graph hash.
        self.build_timings: dict[str, float] = {}
        self.build_stats: dict[str, int] = {}

    def add_leaf(self, leaf: RequirementLeaf) -> None:
        previous = self.leaves.get(leaf.leaf_id)
        if previous is not None and previous != leaf:
            raise ValueError(f"requirement leaf collision: {leaf.leaf_id}")
        self.leaves[leaf.leaf_id] = leaf
        for domain in leaf.domains:
            self.domains[domain.domain_id] = domain
        self.add_node(GraphNode(
            node_id=leaf.leaf_id,
            kind="requirement_leaf",
            label=leaf.formula,
            attributes=leaf.to_dict(),
            provenance_ids=leaf.supporting_evidence,
        ))

    def add_hyperedge(self, edge: RequirementHyperEdge) -> None:
        self.hyperedges[edge.requirement_edge_id] = edge
        missing_sources = [node_id for node_id in edge.source_ids if node_id not in self.nodes]
        missing_targets = [node_id for node_id in edge.target_ids if node_id not in self.nodes]
        if missing_sources or missing_targets:
            raise KeyError(f"requirement hyperedge references missing nodes: {missing_sources + missing_targets}")
        self.add_edge(GraphEdge(
            edge_id=edge.requirement_edge_id,
            kind=edge.relation,
            source_ids=edge.source_ids,
            target_ids=edge.target_ids,
            condition=edge.guard,
            confidence=1.0,
            attributes={},
            provenance_ids=edge.evidence_ids,
        ))

    def add_partition(self, partition: DomainPartition) -> None:
        previous = self.partitions.get(partition.partition_id)
        if previous is not None and previous != partition:
            raise ValueError(f"domain partition collision: {partition.partition_id}")
        self.partitions[partition.partition_id] = partition

    def add_path_obligation(self, obligation: RequirementPathObligation) -> None:
        previous = self.path_obligations.get(obligation.path_obligation_id)
        if previous is not None and previous != obligation:
            raise ValueError(f"path obligation collision: {obligation.path_obligation_id}")
        self.path_obligations[obligation.path_obligation_id] = obligation

    def add_ledger(self, record: PathEdgeLedgerRecord) -> None:
        previous = self.edge_ledger.get(record.ledger_id)
        if previous is not None and previous != record:
            raise ValueError(f"edge ledger collision: {record.ledger_id}")
        duplicate = [
            item.ledger_id
            for item in self.edge_ledger.values()
            if item.path_state_id == record.path_state_id
            and item.program_edge_id == record.program_edge_id
            and item.ledger_id != record.ledger_id
        ]
        if duplicate:
            raise ValueError(
                f"program edge has multiple ledger entries for path state: {duplicate}"
            )
        self.edge_ledger[record.ledger_id] = record

    def add_frontier(self, frontier: Frontier) -> None:
        self.frontiers[frontier.frontier_id] = frontier

    def feasible_path_obligations(self) -> list[RequirementPathObligation]:
        return sorted(
            (item for item in self.path_obligations.values() if item.base_feasible),
            key=lambda item: item.path_obligation_id,
        )

    def hard_and_preservation_leaves(self) -> list[RequirementLeaf]:
        return sorted(
            (leaf for leaf in self.leaves.values() if leaf.mandatory),
            key=lambda leaf: leaf.leaf_id,
        )

    def to_dict(self) -> dict[str, Any]:
        graph = super().to_dict()
        body = {
            **graph,
            "assignment_id": self.assignment_id,
            "leaves": [self.leaves[key].to_dict() for key in sorted(self.leaves)],
            "domains": [self.domains[key].to_dict() for key in sorted(self.domains)],
            "partitions": [self.partitions[key].to_dict() for key in sorted(self.partitions)],
            "hyperedges": [self.hyperedges[key].to_dict() for key in sorted(self.hyperedges)],
            "path_obligations": [
                self.path_obligations[key].to_dict() for key in sorted(self.path_obligations)
            ],
            "edge_ledger": [self.edge_ledger[key].to_dict() for key in sorted(self.edge_ledger)],
            "frontiers": [self.frontiers[key].to_dict() for key in sorted(self.frontiers)],
            "authority_snapshot_hash": self.authority_snapshot_hash,
        }
        body.pop("graph_hash", None)
        body["graph_hash"] = content_hash(body)
        return body

    def finalize_authority_snapshot(self) -> str:
        self.authority_snapshot_hash = content_hash([
            {
                "leaf_id": leaf.leaf_id,
                "formula": leaf.formula,
                "authority": leaf.authority.value,
                "authority_class": leaf.authority_class.value,
                "evidence": leaf.supporting_evidence,
            }
            for leaf in sorted(self.leaves.values(), key=lambda item: item.leaf_id)
        ])
        return self.authority_snapshot_hash

    def semantic_layer_hash(self) -> str:
        return content_hash({
            "assignment_id": self.assignment_id,
            "nodes": [self.nodes[key].to_dict() for key in sorted(self.nodes)],
            "hyperedges": [self.hyperedges[key].to_dict() for key in sorted(self.hyperedges)],
            "leaves": [self.leaves[key].to_dict() for key in sorted(self.leaves)],
            "domains": [self.domains[key].to_dict() for key in sorted(self.domains)],
            "authority_snapshot_hash": self.authority_snapshot_hash,
        })

    def add_objective_node(self, objective_id: str, label: str, evidence_ids: Iterable[str]) -> None:
        self.add_node(GraphNode(
            objective_id,
            "objective",
            label,
            {},
            tuple(evidence_ids),
        ))


def requirement_hyperedge(
    relation: str,
    source_ids: Iterable[str],
    target_ids: Iterable[str],
    *,
    guard: str = "True",
    evidence_ids: Iterable[str] = (),
) -> RequirementHyperEdge:
    sources = tuple(source_ids)
    targets = tuple(target_ids)
    return RequirementHyperEdge(
        requirement_edge_id=stable_id("req-edge", relation, sources, targets, guard),
        source_ids=sources,
        target_ids=targets,
        relation=relation,
        guard=guard,
        evidence_ids=tuple(evidence_ids),
    )
