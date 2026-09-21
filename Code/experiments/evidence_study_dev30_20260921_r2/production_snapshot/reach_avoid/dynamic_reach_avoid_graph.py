"""The single incremental graph used by the execution-driven repair loop.

This module deliberately keeps the graph independent from the historical
requirement/program/binding/challenge graph records.  All evidence, execution
facts, repair hypotheses and checkpoints are represented as nodes in one
content-addressed graph.  Views can be projected from this object, but no
second graph is required to make a decision.
"""
from __future__ import annotations

import ast
import re
import subprocess
import time
from dataclasses import dataclass, field, replace
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Iterable, Sequence

from reachpatch.models.base import SerializableRecord, content_hash, stable_id


class GraphNodeKind(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    ORACLE = "ORACLE"
    FILE = "FILE"
    SYMBOL = "SYMBOL"
    BRANCH = "BRANCH"
    VALUE = "VALUE"
    HUNK = "HUNK"
    OBLIGATION = "OBLIGATION"
    OBSERVATION = "OBSERVATION"
    FAILURE = "FAILURE"
    CHALLENGE = "CHALLENGE"
    REPAIR_HYPOTHESIS = "REPAIR_HYPOTHESIS"
    CHECKPOINT = "CHECKPOINT"


class GraphEdgeKind(StrEnum):
    EVIDENCE_SUPPORTS = "EVIDENCE_SUPPORTS"
    DEFINES = "DEFINES"
    REFERENCES = "REFERENCES"
    STATIC_CALLS = "STATIC_CALLS"
    DYNAMIC_CALLS = "DYNAMIC_CALLS"
    BRANCH_TAKEN = "BRANCH_TAKEN"
    BRANCH_NOT_TAKEN = "BRANCH_NOT_TAKEN"
    DEF_USE = "DEF_USE"
    TRACE_REACHES = "TRACE_REACHES"
    MODIFIES = "MODIFIES"
    TESTS = "TESTS"
    FAILS_AT = "FAILS_AT"
    LOCALIZES_TO = "LOCALIZES_TO"
    DERIVED_FROM = "DERIVED_FROM"
    CHILD_OF = "CHILD_OF"
    REACHES_CHECKPOINT = "REACHES_CHECKPOINT"
    CLOSES = "CLOSES"
    REGRESSES = "REGRESSES"
    PRESERVES = "PRESERVES"
    REQUIRES_VALIDATION = "REQUIRES_VALIDATION"


@dataclass(frozen=True, slots=True)
class DynamicGraphBudget(SerializableRecord):
    max_files: int = 30
    max_symbols: int = 800
    max_edges: int = 4000
    initial_caller_depth: int = 1
    initial_callee_depth: int = 1
    max_expansion_depth: int = 3
    wall_seconds: int = 45


@dataclass(frozen=True, slots=True)
class SearchBudget(SerializableRecord):
    branch_factor: int = 1
    max_depth: int = 3
    max_evaluated_patch_nodes: int = 8
    max_children_per_causal_cut: int = 1
    max_root_recoveries: int = 2
    max_exploratory_probes_per_checkpoint: int = 2


@dataclass(frozen=True, slots=True)
class SearchScore(SerializableRecord):
    patch_applicable: int = 0
    fatal_mechanical_free: int = 0
    locked_target_pass_count: int = 0
    stable_target_pass_count: int = 0
    target_stage_progress_count: int = 0
    contract_distance_reduction_count: int = 0
    contract_distance_reduction_amount: float = 0.0
    closed_counterexample_count: int = 0
    negative_preservation_regression_count: int = 0
    executable_challenge_coverage: int = 0
    negative_diff_size: int = 0

    def key(self) -> tuple[int | float, ...]:
        return (self.patch_applicable, self.fatal_mechanical_free,
                self.locked_target_pass_count, self.stable_target_pass_count,
                self.target_stage_progress_count, self.contract_distance_reduction_count,
                self.contract_distance_reduction_amount,
                self.closed_counterexample_count, self.negative_preservation_regression_count,
                self.executable_challenge_coverage, self.negative_diff_size)


@dataclass(frozen=True, slots=True)
class GraphFrontier(SerializableRecord):
    boundary_node_ids: tuple[str, ...] = ()
    omitted_relation_kinds: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GraphNode(SerializableRecord):
    node_id: str
    kind: GraphNodeKind
    file: str | None = None
    symbol: str | None = None
    line_start: int = 0
    line_end: int = 0
    source_span: str = ""
    authority: str = ""
    status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_revision: int = 0
    last_updated_revision: int = 0


@dataclass(frozen=True, slots=True)
class GraphEdge(SerializableRecord):
    edge_id: str
    kind: GraphEdgeKind
    source_id: str
    target_id: str
    static_or_dynamic: str = "static"
    evidence_ids: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()
    confidence: float = 1.0
    created_revision: int = 0
    active: bool = True


@dataclass(frozen=True, slots=True)
class FailureLocus(SerializableRecord):
    obligation_id: str
    target_symbol: str
    first_target_frame: str | None = None
    assertion_location: str | None = None
    exception_type: str | None = None
    changed_hunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CausalCutCandidate(SerializableRecord):
    cut_id: str
    requirement_id: str
    symbol_ids: tuple[str, ...] = ()
    branch_ids: tuple[str, ...] = ()
    value_flow_ids: tuple[str, ...] = ()
    hunk_ids: tuple[str, ...] = ()
    source_spans: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()
    ranking_reason: str = ""
    alternative_to: str | None = None
    predicate: str = ""
    observed_outcomes: tuple[str, ...] = ()
    failure_locus_id: str = ""


@dataclass(frozen=True, slots=True)
class RepairHypothesis(SerializableRecord):
    hypothesis_id: str
    parent_checkpoint_id: str
    requirement_id: str
    failure_id: str
    causal_cut_ids: tuple[str, ...]
    proposed_mechanism: str
    expected_path_change: str = ""
    expected_observation_change: str = ""
    forbidden_regressions: tuple[str, ...] = ()
    graph_evidence_ids: tuple[str, ...] = ()
    distinguishing_inputs: tuple[Any, ...] = ()
    falsification_conditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckpointState(SerializableRecord):
    checkpoint_id: str
    parent_checkpoint_id: str | None
    base_to_current_diff: str
    patch_hash: str
    hypothesis_id: str | None = None
    causal_cut_ids: tuple[str, ...] = ()
    depth: int = 0
    status: str = "OPEN"
    observation_ids: tuple[str, ...] = ()
    mechanical_blockers: tuple[str, ...] = ()
    target_results: dict[str, Any] = field(default_factory=dict)
    preservation_results: dict[str, Any] = field(default_factory=dict)
    challenge_results: dict[str, Any] = field(default_factory=dict)
    locked_successes: tuple[str, ...] = ()
    search_score: tuple[Any, ...] = ()
    final_eligible: bool = False
    certified: bool = False
    best_descendant_score: tuple[Any, ...] = ()
    visit_count: int = 0
    expanded_hypothesis_ids: tuple[str, ...] = ()
    exhausted_causal_cut_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationBatch(SerializableRecord):
    mechanical_obligations: tuple[str, ...] = ()
    target_obligations: tuple[str, ...] = ()
    preservation_obligations: tuple[str, ...] = ()
    challenge_obligations: tuple[str, ...] = ()
    locked_success_obligations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChallengeCell(SerializableRecord):
    challenge_id: str
    source_branch_id: str | None
    source_value_flow_ids: tuple[str, ...]
    input_recipe: Any
    oracle: Any
    authority: str
    command: tuple[str, ...] = ()
    status: str = "EXPLORATION_ONLY"
    observation_ids: tuple[str, ...] = ()
    affected_checkpoint_ids: tuple[str, ...] = ()


class DynamicReachAvoidGraph(SerializableRecord):
    """Mutable, incrementally updated graph with deterministic identities."""

    schema = "reachpatch-dynamic-reach-avoid-graph-v1"

    def __init__(self, *, budget: DynamicGraphBudget | None = None, graph_id: str | None = None):
        self.budget = budget or DynamicGraphBudget()
        self.graph_id = graph_id or stable_id("dynamic-reach-avoid-graph", time.time_ns())
        self.revision = 0
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}
        self.frontiers: list[GraphFrontier] = []
        self.update_log: list[dict[str, Any]] = []
        self.metrics: dict[str, int] = {
            "graph_seed_node_count": 0, "graph_update_count": 0,
            "graph_expansion_count": 0, "graph_localization_decision_count": 0,
            "graph_generated_hypothesis_count": 0, "graph_generated_challenge_count": 0,
            "graph_derived_validation_count": 0, "graph_backtrack_count": 0,
            "model_calls_with_graph_source_context": 0,
            "distinct_checkpoint_count": 0,
            "distinct_causal_cut_count": 0,
            "distinct_repair_mechanism_count": 0,
            "target_recovery_attempt_count": 0,
            "target_recovery_success": 0,
            "entered_repair_loop": 0,
            "final_differs_from_p0": 0,
        }
        self.failure_loci: dict[str, FailureLocus] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema, "graph_id": self.graph_id,
            "budget": self.budget.to_dict(), "revision": self.revision,
            "nodes": {key: value.to_dict() for key, value in sorted(self.nodes.items())},
            "edges": {key: value.to_dict() for key, value in sorted(self.edges.items())},
            "frontiers": [item.to_dict() for item in self.frontiers],
            "metrics": dict(sorted(self.metrics.items())),
            "failure_loci": {key: value.to_dict() for key, value in sorted(self.failure_loci.items())},
            "update_log": list(self.update_log),
        }

    def digest(self) -> str:
        return content_hash(self.to_dict())

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DynamicReachAvoidGraph":
        budget = DynamicGraphBudget(**payload.get("budget", {}))
        graph = cls(budget=budget, graph_id=payload.get("graph_id"))
        graph.revision = int(payload.get("revision", 0))
        for key, raw in payload.get("nodes", {}).items():
            item = dict(raw)
            graph.nodes[key] = GraphNode(kind=GraphNodeKind(item.pop("kind")), **item)
        for key, raw in payload.get("edges", {}).items():
            item = dict(raw)
            graph.edges[key] = GraphEdge(kind=GraphEdgeKind(item.pop("kind")), **item)
        graph.frontiers = [GraphFrontier(**item) for item in payload.get("frontiers", ())]
        graph.metrics.update({str(k): int(v) for k, v in payload.get("metrics", {}).items()})
        graph.failure_loci = {key: FailureLocus(**value) for key, value in payload.get("failure_loci", {}).items()}
        graph.update_log = [dict(item) for item in payload.get("update_log", ()) if isinstance(item, dict)]
        return graph

    def add_node(self, kind: GraphNodeKind, *, node_id: str | None = None, revision: int | None = None, **fields: Any) -> GraphNode:
        identity = node_id or stable_id("graph-node", kind, fields.get("file"), fields.get("symbol"), fields.get("line_start", 0), fields.get("metadata", {}))
        self.revision = max(self.revision, int(revision or self.revision))
        old = self.nodes.get(identity)
        if old is not None:
            updated = GraphNode(**{**old.to_dict(), **fields, "kind": kind, "node_id": identity, "last_updated_revision": self.revision})
            self.nodes[identity] = updated
            return updated
        node = GraphNode(node_id=identity, kind=kind, created_revision=self.revision, last_updated_revision=self.revision, **fields)
        self.nodes[identity] = node
        return node

    def record_update(self, event: str, **payload: Any) -> None:
        self.update_log.append({"revision": self.revision, "event": event, **payload})

    def add_edge(self, kind: GraphEdgeKind, source_id: str, target_id: str, *, edge_id: str | None = None, revision: int | None = None, **fields: Any) -> GraphEdge:
        if source_id not in self.nodes or target_id not in self.nodes:
            raise KeyError(f"edge endpoint missing: {source_id}->{target_id}")
        identity = edge_id or stable_id("graph-edge", kind, source_id, target_id)
        if identity in self.edges:
            old = self.edges[identity]
            edge = GraphEdge(**{**old.to_dict(), **fields, "kind": kind, "edge_id": identity, "active": fields.get("active", old.active)})
            self.edges[identity] = edge
            return edge
        if len(self.edges) >= self.budget.max_edges:
            self._frontier((source_id, target_id), (kind.value,), "EDGE_BUDGET")
            return GraphEdge(identity, kind, source_id, target_id, active=False)
        edge = GraphEdge(edge_id=identity, kind=kind, source_id=source_id, target_id=target_id, created_revision=int(revision or self.revision), **fields)
        self.edges[identity] = edge
        return edge

    def _frontier(self, boundary: Iterable[str], omitted: Iterable[str], reason: str) -> None:
        boundary_ids = tuple(sorted(set(str(item) for item in boundary if item)))[:64]
        omitted_kinds = tuple(sorted(set(str(item) for item in omitted if item)))
        # Coalesce repeated budget hits for the same omitted relation.  A
        # growing execution should update one explicit frontier rather than
        # allocating thousands of equivalent records.
        for index, existing in enumerate(self.frontiers):
            if existing.reason == reason and existing.omitted_relation_kinds == omitted_kinds:
                merged = tuple(sorted(set(existing.boundary_node_ids).union(boundary_ids)))[:64]
                self.frontiers[index] = GraphFrontier(merged, omitted_kinds, reason)
                return
        self.frontiers.append(GraphFrontier(boundary_ids, omitted_kinds, reason))

    def _admit_file(self, file: str, *, revision: int = 0) -> bool:
        files = {node.file for node in self.nodes.values() if node.kind is GraphNodeKind.FILE}
        if file in files:
            return True
        if len(files) >= self.budget.max_files:
            self._frontier((), ("CALLERS", "CALLEES", "DEF_USE"), "FILE_BUDGET")
            return False
        self.add_node(GraphNodeKind.FILE, file=file, revision=revision)
        return True

    def initial_generation_context(self, limit: int = 8) -> dict[str, Any]:
        relevant = [node for node in self.nodes.values() if node.kind in {GraphNodeKind.SYMBOL, GraphNodeKind.BRANCH, GraphNodeKind.VALUE}]
        def relevance(node: GraphNode) -> tuple[int, int, int, str, int, str]:
            path = (node.file or "").casefold()
            # Source hints carry an auditable lexical rank.  Prefer those
            # spans over generic documentation/examples and keep executable
            # symbols before parameter/value nodes at the same location.
            hint_rank = int(node.metadata.get("source_hint_rank", 10_000) or 10_000)
            implementation_path = int(any(token in path for token in ("/src/", "/ext/", "/core/", "/lib/", "sphinx/ext/", "django/", "sklearn/")))
            kind_rank = {GraphNodeKind.SYMBOL: 0, GraphNodeKind.BRANCH: 1, GraphNodeKind.VALUE: 2}.get(node.kind, 9)
            return (
                0 if node.authority in {"A", "B", "C"} else 1,
                hint_rank,
                0 if implementation_path else 1,
                kind_rank,
                node.line_start,
                node.file or "",
            )
        relevant.sort(key=relevance)
        return {
            "graph_hash": self.digest(),
            "top_target_symbols": [node.symbol for node in relevant if node.kind is GraphNodeKind.SYMBOL][:limit],
            "top_causal_locations": [f"{node.file}:{node.line_start}" for node in relevant[:limit] if node.file],
            "related_source_spans": [node.to_dict() for node in relevant[:limit]],
            "possible_data_flow_paths": [
                edge.to_dict() for edge in self.edges.values()
                if edge.kind is GraphEdgeKind.DEF_USE
            ][:limit],
            "public_test_callers": [
                self.nodes[edge.source_id].metadata
                for edge in self.edges.values()
                if edge.kind is GraphEdgeKind.TESTS and edge.source_id in self.nodes
            ][:limit],
            "frontiers": [frontier.to_dict() for frontier in self.frontiers],
        }

    def context_for(self, hypothesis: RepairHypothesis | CausalCutCandidate) -> dict[str, Any]:
        ids = set(getattr(hypothesis, "causal_cut_ids", ())) | set(getattr(hypothesis, "graph_evidence_ids", ()))
        nodes = [node for node in self.nodes.values() if node.node_id in ids]
        related = {node.node_id for node in nodes}
        related.update(edge.target_id for edge in self.edges.values() if edge.source_id in related and edge.active)
        related.update(edge.source_id for edge in self.edges.values() if edge.target_id in related and edge.active)
        selected = [self.nodes[item] for item in related if item in self.nodes]
        selected.sort(key=lambda n: (n.file or "", n.line_start, n.node_id))
        return {"graph_hash": self.digest(), "nodes": [node.to_dict() for node in selected], "edges": [edge.to_dict() for edge in self.edges.values() if edge.source_id in related and edge.target_id in related and edge.active]}

    def register_checkpoint(self, checkpoint: CheckpointState) -> GraphNode:
        previous = self.nodes.get(checkpoint.checkpoint_id)
        node = self.add_node(GraphNodeKind.CHECKPOINT, node_id=checkpoint.checkpoint_id, status=checkpoint.status,
                             metadata={**(previous.metadata if previous else {}), **checkpoint.to_dict()})
        if checkpoint.parent_checkpoint_id and checkpoint.parent_checkpoint_id in self.nodes:
            self.add_edge(GraphEdgeKind.CHILD_OF, checkpoint.checkpoint_id, checkpoint.parent_checkpoint_id)
        self.metrics["distinct_checkpoint_count"] = len({
            item.node_id for item in self.nodes.values()
            if item.kind is GraphNodeKind.CHECKPOINT
        })
        return node

    def update_checkpoint(self, checkpoint_id: str, **changes: Any) -> CheckpointState:
        node = self.nodes.get(checkpoint_id)
        if node is None or node.kind is not GraphNodeKind.CHECKPOINT:
            raise KeyError(checkpoint_id)
        raw = {
            key: value for key, value in node.metadata.items()
            if key in CheckpointState.__dataclass_fields__
        }
        checkpoint = CheckpointState(**raw)
        updated = replace(checkpoint, **changes)
        self.nodes[checkpoint_id] = replace(
            node, status=updated.status, metadata={**node.metadata, **updated.to_dict()},
            last_updated_revision=self.revision,
        )
        return updated

    def register_hypothesis(self, hypothesis: RepairHypothesis) -> GraphNode:
        is_new = hypothesis.hypothesis_id not in self.nodes
        if is_new:
            self.metrics["graph_generated_hypothesis_count"] += 1
        node = self.add_node(GraphNodeKind.REPAIR_HYPOTHESIS, node_id=hypothesis.hypothesis_id, symbol=hypothesis.proposed_mechanism, metadata=hypothesis.to_dict())
        if hypothesis.parent_checkpoint_id in self.nodes:
            self.add_edge(GraphEdgeKind.DERIVED_FROM, hypothesis.hypothesis_id, hypothesis.parent_checkpoint_id)
        for cut_id in hypothesis.causal_cut_ids:
            if cut_id in self.nodes:
                self.add_edge(GraphEdgeKind.LOCALIZES_TO, hypothesis.hypothesis_id, cut_id)
        self.metrics["distinct_repair_mechanism_count"] = len({
            item.symbol for item in self.nodes.values()
            if item.kind is GraphNodeKind.REPAIR_HYPOTHESIS and item.symbol
        })
        return node

    def record_transition(self, parent_checkpoint_id: str, child_checkpoint_id: str, decision: str, *, evidence_ids: Sequence[str] = ()) -> GraphEdge:
        kind = {
            "REJECT_TRIAL": GraphEdgeKind.REGRESSES,
            "REACHED": GraphEdgeKind.CLOSES,
            "ADVANCE_SAFE": GraphEdgeKind.PRESERVES,
            "KEEP_REPAIRING": GraphEdgeKind.DERIVED_FROM,
        }.get(str(decision), GraphEdgeKind.DERIVED_FROM)
        if parent_checkpoint_id not in self.nodes:
            self.add_node(GraphNodeKind.CHECKPOINT, node_id=parent_checkpoint_id)
        if child_checkpoint_id not in self.nodes:
            self.add_node(GraphNodeKind.CHECKPOINT, node_id=child_checkpoint_id)
        child = self.nodes[child_checkpoint_id]
        if child.metadata.get("checkpoint_id"):
            self.update_checkpoint(
                child_checkpoint_id,
                status=("REJECTED" if str(decision) == "REJECT_TRIAL" else str(decision)),
                final_eligible=str(decision) in {"ADVANCE_SAFE", "REACHED"},
                certified=str(decision) == "REACHED",
            )
            child = self.nodes[child_checkpoint_id]
        else:
            self.nodes[child_checkpoint_id] = replace(
                child, status=str(decision), last_updated_revision=self.revision,
            )
        parent = self.nodes.get(parent_checkpoint_id)
        if parent is not None and parent.metadata.get("checkpoint_id"):
            parent_state = CheckpointState(**{
                key: value for key, value in parent.metadata.items()
                if key in CheckpointState.__dataclass_fields__
            })
            child_score = tuple(child.metadata.get("search_score", ())) if str(decision) != "REJECT_TRIAL" else ()
            best = max(tuple(parent_state.best_descendant_score), child_score)
            self.update_checkpoint(
                parent_checkpoint_id,
                best_descendant_score=best,
                visit_count=parent_state.visit_count + 1,
                expanded_hypothesis_ids=tuple(dict.fromkeys((
                    *parent_state.expanded_hypothesis_ids,
                    str(child.metadata.get("hypothesis_id", "")),
                ))),
            )
            ancestor_id = parent_state.parent_checkpoint_id
            visited = {parent_checkpoint_id, child_checkpoint_id}
            while ancestor_id and ancestor_id not in visited:
                visited.add(ancestor_id)
                ancestor = self.nodes.get(ancestor_id)
                if ancestor is None or not ancestor.metadata.get("checkpoint_id"):
                    break
                self.update_checkpoint(ancestor_id,
                    best_descendant_score=max(tuple(ancestor.metadata.get("best_descendant_score", ())), child_score))
                ancestor_id = ancestor.metadata.get("parent_checkpoint_id")
        if str(decision) == "REJECT_TRIAL":
            self.metrics["graph_backtrack_count"] += 1
        return self.add_edge(kind, child_checkpoint_id, parent_checkpoint_id, evidence_ids=tuple(evidence_ids), static_or_dynamic="dynamic")

    def checkpoint_tree_view(self) -> dict[str, Any]:
        return {"graph_hash": self.digest(), "checkpoints": [node.metadata for node in self.nodes.values() if node.kind is GraphNodeKind.CHECKPOINT], "edges": [edge.to_dict() for edge in self.edges.values() if edge.kind in {GraphEdgeKind.CHILD_OF, GraphEdgeKind.REGRESSES, GraphEdgeKind.CLOSES, GraphEdgeKind.PRESERVES}]}


def _symbol_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        parent = _symbol_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _qualified_definitions(tree: ast.AST) -> dict[int, str]:
    names: dict[int, str] = {}
    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + child.name
                names[id(child)] = name
                visit(child, name + ".")
            else:
                visit(child, prefix)
    visit(tree, "")
    return names


def _walk_scope(node: ast.AST) -> Iterable[ast.AST]:
    yield node
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from _walk_scope(child)


def refresh_checkpoint_source(graph: DynamicReachAvoidGraph, repo_root: Path, current_diff: str) -> None:
    """Refresh admitted source in-place when selecting a parent or trial.

    Source snapshots can move backwards during backtracking. Historical dynamic
    edges remain evidence for their own observations; stale static dependencies
    are deactivated rather than reused against a different source revision.
    """
    paths = set(re.findall(r"^\+\+\+ b/(.+)$", current_diff, re.M))
    paths.update(node.file for node in graph.nodes.values() if node.kind is GraphNodeKind.FILE
                 and node.file and node.metadata.get("source_snapshot_hash"))
    started = time.monotonic()
    for path in sorted(paths):
        if time.monotonic() - started >= graph.budget.wall_seconds:
            graph._frontier((), ("DEFINES", "DEF_USE"), "TIME_BUDGET")
            break
        source_path = (repo_root / path).resolve()
        if not source_path.is_relative_to(repo_root.resolve()) or not source_path.is_file():
            continue
        if not graph._admit_file(path):
            continue
        file_node = next(node for node in graph.nodes.values() if node.kind is GraphNodeKind.FILE and node.file == path)
        source = source_path.read_text(encoding="utf-8", errors="replace")
        source_hash = content_hash(source)
        if file_node.metadata.get("source_snapshot_hash") == source_hash:
            continue
        try:
            parsed = ast.parse(source, filename=path)
        except SyntaxError:
            graph.record_update("SOURCE_PARSE_BLOCKED", file=path)
            continue
        graph.revision += 1
        affected = {node.node_id for node in graph.nodes.values()
                    if node.file == path and node.status not in {"HISTORICAL_SOURCE", "RETIRED_SOURCE"}
                    and node.kind in {GraphNodeKind.SYMBOL, GraphNodeKind.BRANCH, GraphNodeKind.VALUE}}
        for node_id in affected:
            historical = graph.nodes[node_id]
            version_id = stable_id("source-version", node_id, historical.source_span)
            if version_id not in graph.nodes:
                graph.add_node(historical.kind, node_id=version_id, file=historical.file,
                    symbol=historical.symbol, line_start=historical.line_start, line_end=historical.line_end,
                    source_span=historical.source_span, status="HISTORICAL_SOURCE",
                    metadata={**historical.metadata, "source_entity_id": node_id,
                              "source_version_id": content_hash(historical.source_span)})
                graph.add_edge(GraphEdgeKind.DERIVED_FROM, version_id, node_id)
            for edge_id, edge in tuple(graph.edges.items()):
                if (edge.static_or_dynamic == "dynamic" and edge.target_id == node_id
                    and edge.kind in {GraphEdgeKind.TRACE_REACHES, GraphEdgeKind.DYNAMIC_CALLS,
                                      GraphEdgeKind.BRANCH_TAKEN, GraphEdgeKind.BRANCH_NOT_TAKEN, GraphEdgeKind.DEF_USE}):
                    graph.edges[edge_id] = replace(edge, target_id=version_id)
            graph.nodes[node_id] = replace(graph.nodes[node_id], status="RETIRED_SOURCE")
        for edge_id, edge in tuple(graph.edges.items()):
            if edge.static_or_dynamic == "static" and edge.kind in {GraphEdgeKind.DEFINES, GraphEdgeKind.DEF_USE, GraphEdgeKind.STATIC_CALLS} and (edge.source_id in affected or edge.target_id in affected):
                graph.edges[edge_id] = replace(edge, active=False)
        graph.nodes[file_node.node_id] = replace(file_node, metadata={**file_node.metadata, "source_snapshot_hash": source_hash})
        names = _qualified_definitions(parsed)
        calls: list[tuple[str, str]] = []
        for definition in (node for node in ast.walk(parsed) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
            name = names[id(definition)]
            symbol_id = stable_id("symbol", path, name)
            previous = graph.nodes.get(symbol_id)
            if previous is None:
                if sum(node.kind is GraphNodeKind.SYMBOL for node in graph.nodes.values()) >= graph.budget.max_symbols:
                    graph._frontier((file_node.node_id,), ("DEFINES",), "SYMBOL_BUDGET")
                    break
            symbol = graph.add_node(GraphNodeKind.SYMBOL, node_id=symbol_id, file=path, symbol=name,
                                    line_start=definition.lineno, line_end=definition.end_lineno,
                                    source_span=ast.get_source_segment(source, definition) or "", status="CURRENT_SOURCE",
                                    metadata={**(previous.metadata if previous else {}), "source_snapshot_hash": source_hash})
            graph.add_edge(GraphEdgeKind.DEFINES, file_node.node_id, symbol.node_id, active=True)
            definitions: dict[str, str] = {}
            for argument in (*definition.args.posonlyargs, *definition.args.args, *definition.args.kwonlyargs):
                value = graph.add_node(GraphNodeKind.VALUE, node_id=stable_id("parameter", symbol_id, argument.arg),
                    file=path, symbol=argument.arg, line_start=argument.lineno, line_end=argument.lineno,
                    source_span=argument.arg, status="CURRENT_SOURCE", metadata={"parameter": argument.arg})
                definitions[argument.arg] = value.node_id
                graph.add_edge(GraphEdgeKind.DEFINES, symbol_id, value.node_id, active=True)
            for expression in _walk_scope(definition):
                if isinstance(expression, ast.Call):
                    called = _symbol_name(expression.func)
                    if called:
                        if called.startswith(("self.", "cls.")) and "." in name:
                            called = name.rsplit(".", 1)[0] + "." + called.split(".", 1)[1]
                        calls.append((symbol_id, called))
                kind = (GraphNodeKind.BRANCH if isinstance(expression, (ast.If, ast.While, ast.IfExp))
                        else GraphNodeKind.VALUE if isinstance(expression, (ast.Return, ast.Raise, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Call)) else None)
                if kind is None:
                    continue
                if len(graph.edges) >= graph.budget.max_edges:
                    graph._frontier((symbol_id,), ("DEF_USE",), "EDGE_BUDGET")
                    break
                span = ast.get_source_segment(source, expression) or ""
                predicate = ast.unparse(expression.test) if kind is GraphNodeKind.BRANCH else ""
                value = graph.add_node(kind, node_id=stable_id("source-expression", symbol_id, expression.lineno, span),
                    file=path, symbol=name, line_start=expression.lineno, line_end=expression.end_lineno,
                    source_span=span, status="CURRENT_SOURCE", metadata={"predicate": predicate})
                graph.add_edge(GraphEdgeKind.DEFINES, symbol_id, value.node_id, active=True)
                used_expression = expression.test if kind is GraphNodeKind.BRANCH else expression
                for used in ast.walk(used_expression):
                    if isinstance(used, ast.Name) and isinstance(used.ctx, ast.Load) and used.id in definitions:
                        graph.add_edge(GraphEdgeKind.DEF_USE, definitions[used.id], value.node_id, active=True)
                if isinstance(expression, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    assigned = expression.targets if isinstance(expression, ast.Assign) else (expression.target,)
                    for target in assigned:
                        if isinstance(target, ast.Name):
                            definitions[target.id] = value.node_id
        for caller_id, called in calls:
            targets = [node for node in graph.nodes.values() if node.kind is GraphNodeKind.SYMBOL
                       and node.status != "RETIRED_SOURCE" and node.symbol == called]
            local_targets = [node for node in targets if node.file == path]
            targets = local_targets or targets
            if len(targets) == 1:
                graph.add_edge(GraphEdgeKind.STATIC_CALLS, caller_id, targets[0].node_id, active=True)
        graph.record_update("SOURCE_REFRESH", file=path, source_snapshot_hash=source_hash)


def _issue_symbols(text: str) -> tuple[str, ...]:
    title = text.splitlines()[0] if text.splitlines() else text
    candidates = (
        re.findall(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`", text)
        + re.findall(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(", text)
        + re.findall(r"(?m)\bin\s+([A-Za-z_]\w*)\s*$", text)
        + re.findall(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b", text)
        + re.findall(r"\b(?:function|method|class|API)\s+([A-Za-z_]\w*)\b", title, re.I)
        + re.findall(r"\b(?:[A-Za-z_]\w*_[A-Za-z_]\w*|[A-Z][A-Za-z0-9]+)\b", title)
    )
    excluded = {"the", "this", "that", "should", "must", "expected", "actual", "return", "error", "exception", "tests", "test", "implementation", "needs", "improvement", "support", "behavior", "behaviour", "value", "positive", "negative", "input", "output", "from", "with", "when", "then", "where", "into", "does", "not", "and", "or", "for", "all", "any", "none"}
    return tuple(dict.fromkeys(item for item in candidates if item.casefold() not in excluded and not item.isupper()))


def seed_dynamic_graph(repo_root: Path, issue_text: str, goal_contracts: Sequence[Any], source_hints: Sequence[Any], public_checks: Sequence[Any], budget: DynamicGraphBudget | None = None) -> DynamicReachAvoidGraph:
    effective_budget = budget or DynamicGraphBudget()
    graph = DynamicReachAvoidGraph(budget=effective_budget)
    started = time.monotonic()
    revision = 0
    symbols: list[str] = []
    symbols.extend(_issue_symbols(issue_text))
    for goal in goal_contracts:
        symbols.extend(str(item) for item in getattr(goal, "target_symbols", ()) if item)
        req = graph.add_node(GraphNodeKind.REQUIREMENT, node_id=str(goal.goal_id), symbol=getattr(goal, "operation", ""), authority=str(getattr(goal, "authority", "")), status="HARD" if getattr(goal, "hard", False) else "SOFT", metadata=goal.to_dict() if hasattr(goal, "to_dict") else {})
        for span in getattr(goal, "evidence_spans", ()):
            observation = graph.add_node(GraphNodeKind.OBSERVATION, source_span=str(getattr(span, "quote", span)), authority=str(getattr(goal, "authority", "")))
            graph.add_edge(GraphEdgeKind.EVIDENCE_SUPPORTS, observation.node_id, req.node_id, evidence_ids=(str(goal.goal_id),))
    for goal in goal_contracts:
        parent_id = getattr(goal, "parent_goal_id", None)
        if parent_id in graph.nodes:
            graph.add_edge(GraphEdgeKind.DERIVED_FROM, str(goal.goal_id), parent_id,
                           evidence_ids=tuple(getattr(goal, "evidence_span_ids", ())))
    hint_ranks: dict[str, int] = {}
    hint_symbol_ranks: dict[tuple[str, str], int] = {}
    for hint_index, hint in enumerate(source_hints):
        symbol = str(getattr(hint, "symbol", "") or "")
        if symbol: symbols.append(symbol)
        file = str(getattr(hint, "file", getattr(hint, "path", "")) or "")
        if file:
            normalized_file = file.replace("\\", "/").lstrip("./")
            hint_ranks[normalized_file] = min(
                hint_ranks.get(normalized_file, 10_000), hint_index,
            )
            if symbol:
                hint_symbol_ranks[(normalized_file, symbol.rsplit(".", 1)[-1])] = min(
                    hint_symbol_ranks.get((normalized_file, symbol.rsplit(".", 1)[-1]), 10_000),
                    hint_index,
                )
            graph._admit_file(file, revision=revision)
    for check in public_checks:
        check_id = str(getattr(check, "check_id", stable_id("check", repr(check))))
        oracle = graph.add_node(GraphNodeKind.ORACLE, node_id=stable_id("oracle", check_id), authority=str(getattr(check, "authority", "")), metadata={"check_id": check_id, "command": getattr(check, "command", ()), "target_symbols": tuple(getattr(check, "target_symbols", ())), "symbol_references": tuple(getattr(check, "symbol_references", ()))})
        for symbol in tuple(getattr(check, "target_symbols", ())) + tuple(getattr(check, "symbol_references", ())): symbols.append(str(symbol)); graph.add_edge(GraphEdgeKind.TESTS, oracle.node_id, stable_id("symbol", str(symbol))) if stable_id("symbol", str(symbol)) in graph.nodes else None
    wanted = tuple(dict.fromkeys(item for item in symbols if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", item)))
    hinted_files = {
        str(getattr(hint, "file", getattr(hint, "path", "")) or "")
        for hint in source_hints
    }
    check_files = {
        token.replace("::", ":").split(":", 1)[0].lstrip("./")
        for check in public_checks
        for token in tuple(getattr(check, "command", ()))
        if str(token).endswith(".py") or "::" in str(token)
    }
    all_paths = tuple(
        path for path in Path(repo_root).rglob("*.py")
        if not any(part in {".git", "artifacts", "official_harness", "harness", "node_modules", "build", "dist", "__pycache__"} for part in path.parts)
    )
    definition_paths: set[str] = set()
    reference_paths: set[str] = set()
    terminals = tuple(dict.fromkeys(
        item.rsplit(".", 1)[-1] for item in wanted
        if re.fullmatch(r"[A-Za-z_]\w*", item.rsplit(".", 1)[-1])
    ))[:80]
    if terminals:
        pattern = r"^(?:\s*)(?:async\s+def|def|class)\s+(?:" + "|".join(map(re.escape, terminals)) + r")\b"
        try:
            completed = subprocess.run(
                ["rg", "-l", "-g", "*.py", "-g", "!**/.git/**", pattern, str(repo_root)],
                capture_output=True, text=True, check=False,
                timeout=max(1.0, min(10.0, effective_budget.wall_seconds / 2)),
            )
            if completed.returncode in {0, 1}:
                for raw in completed.stdout.splitlines():
                    try:
                        definition_paths.add(Path(raw).resolve().relative_to(Path(repo_root).resolve()).as_posix())
                    except ValueError:
                        continue
        except (OSError, subprocess.TimeoutExpired):
            graph._frontier((), ("DEFINES",), "TIME_BUDGET")
        call_pattern = r"\b(?:" + "|".join(map(re.escape, terminals)) + r")\s*\("
        try:
            completed = subprocess.run(
                ["rg", "-l", "-g", "*.py", "-g", "!**/.git/**", call_pattern, str(repo_root)],
                capture_output=True, text=True, check=False,
                timeout=max(1.0, min(10.0, effective_budget.wall_seconds / 2)),
            )
            if completed.returncode in {0, 1}:
                for raw in completed.stdout.splitlines()[: max(20, effective_budget.max_files * 2)]:
                    try:
                        reference_paths.add(Path(raw).resolve().relative_to(Path(repo_root).resolve()).as_posix())
                    except ValueError:
                        continue
        except (OSError, subprocess.TimeoutExpired):
            graph._frontier((), ("REFERENCES", "STATIC_CALLS"), "TIME_BUDGET")
    candidate_paths = sorted(
        all_paths,
        key=lambda path: (
            0 if path.relative_to(repo_root).as_posix() in hinted_files else
            1 if path.relative_to(repo_root).as_posix() in check_files else
            2 if path.relative_to(repo_root).as_posix() in definition_paths else 3,
            path.relative_to(repo_root).as_posix(),
        ),
    )
    for path in candidate_paths:
        if time.monotonic() - started >= effective_budget.wall_seconds:
            graph._frontier((), ("STATIC_CALLS", "DEFINES", "DEF_USE"), "TIME_BUDGET")
            break
        rel = path.relative_to(repo_root).as_posix()
        prioritized = rel in hinted_files or rel in check_files or rel in definition_paths or rel in reference_paths
        if not prioritized:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=rel)
        except (OSError, SyntaxError): continue
        qualified_names = _qualified_definitions(tree)
        target_definitions = tuple(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and (not wanted or any(item.rsplit(".", 1)[-1].casefold() == node.name.casefold() for item in wanted))
        )
        direct_callee_names = {
            str(name).rsplit(".", 1)[-1]
            for target_node in target_definitions
            for call in ast.walk(target_node)
            if isinstance(call, ast.Call)
            for name in (_symbol_name(call.func),)
            if name
        }
        wanted_terminals = {item.rsplit(".", 1)[-1].casefold() for item in wanted}
        related_definitions = tuple(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and (
                node in target_definitions
                or node.name in direct_callee_names
                or any(
                    isinstance(call, ast.Call)
                    and (_symbol_name(call.func) or "").rsplit(".", 1)[-1].casefold() in wanted_terminals
                    for call in ast.walk(node)
                )
            )
        )
        matched_definitions = related_definitions
        if not matched_definitions and rel not in hinted_files and rel not in check_files:
            continue
        if not graph._admit_file(rel, revision=revision):
            continue
        file_node = next(
            item for item in graph.nodes.values()
            if item.kind is GraphNodeKind.FILE and item.file == rel
        )
        if rel in hint_ranks:
            graph.nodes[file_node.node_id] = replace(
                file_node,
                metadata={**file_node.metadata, "source_hint_rank": hint_ranks[rel]},
            )
        for imported in (
            node for node in ast.iter_child_nodes(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ):
            module_names = (
                tuple(alias.name for alias in imported.names)
                if isinstance(imported, ast.Import) else
                tuple(f"{imported.module or ''}.{alias.name}".strip(".") for alias in imported.names)
            )
            for module_name in module_names:
                value = graph.add_node(
                    GraphNodeKind.VALUE, file=rel, symbol=module_name,
                    line_start=imported.lineno,
                    line_end=getattr(imported, "end_lineno", imported.lineno),
                    source_span=ast.get_source_segment(source, imported) or "",
                    metadata={"relation": "IMPORT"},
                )
                graph.add_edge(GraphEdgeKind.REFERENCES, file_node.node_id, value.node_id)
        for node in matched_definitions:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = qualified_names[id(node)]
                if len([n for n in graph.nodes.values() if n.kind is GraphNodeKind.SYMBOL]) >= effective_budget.max_symbols:
                    graph._frontier((), ("SYMBOL",), "SYMBOL_BUDGET"); break
                matched_goals = tuple(
                    goal for goal in goal_contracts
                    if any(str(item).casefold() == qualified.casefold() or
                           ("." not in str(item) and str(item).casefold() == node.name.casefold())
                           for item in getattr(goal, "target_symbols", ()))
                )
                symbol_authority = next((
                    str(getattr(goal, "authority", "")) for goal in matched_goals
                    if str(getattr(goal, "authority", "")) in {"A", "B", "C"}
                ), "")
                symbol_rank = hint_symbol_ranks.get(
                    (rel, qualified.rsplit(".", 1)[-1]),
                    hint_ranks.get(rel, 10_000) + 1000,
                )
                sym = graph.add_node(GraphNodeKind.SYMBOL, node_id=stable_id("symbol", rel, qualified), file=rel, symbol=qualified, line_start=node.lineno, line_end=getattr(node, "end_lineno", node.lineno), source_span=ast.get_source_segment(source, node) or "", authority=symbol_authority, metadata={"requirement_ids": tuple(str(goal.goal_id) for goal in matched_goals), **({"source_hint_rank": symbol_rank} if symbol_rank < 10_000 else {})})
                related_expressions = list(getattr(node, "decorator_list", ()))
                if isinstance(node, ast.ClassDef):
                    related_expressions.extend(node.bases)
                for expression in related_expressions:
                    relation_symbol = _symbol_name(expression)
                    if relation_symbol is None and isinstance(expression, ast.Call):
                        relation_symbol = _symbol_name(expression.func)
                    if relation_symbol:
                        relation = graph.add_node(
                            GraphNodeKind.VALUE, file=rel, symbol=relation_symbol,
                            line_start=getattr(expression, "lineno", node.lineno),
                            line_end=getattr(expression, "end_lineno", getattr(expression, "lineno", node.lineno)),
                            source_span=ast.get_source_segment(source, expression) or "",
                            metadata={"relation": "BASE_CLASS" if isinstance(node, ast.ClassDef) and expression in node.bases else "DECORATOR"},
                        )
                        graph.add_edge(GraphEdgeKind.REFERENCES, sym.node_id, relation.node_id)
                for goal in goal_contracts:
                    if goal in matched_goals: graph.add_edge(GraphEdgeKind.REFERENCES, str(goal.goal_id), sym.node_id)
                defined_values: dict[str, str] = {}
                for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else ():
                    value = graph.add_node(
                        GraphNodeKind.VALUE, file=rel, symbol=argument.arg,
                        line_start=argument.lineno, line_end=getattr(argument, "end_lineno", argument.lineno),
                        source_span=argument.arg, metadata={"parameter": argument.arg},
                    )
                    defined_values[argument.arg] = value.node_id
                    graph.add_edge(GraphEdgeKind.DEFINES, sym.node_id, value.node_id)
                for child in _walk_scope(node):
                    if isinstance(child, (ast.If, ast.While, ast.IfExp, ast.Match)): 
                        branch = graph.add_node(GraphNodeKind.BRANCH, file=rel, symbol=qualified, line_start=child.lineno, line_end=getattr(child, "end_lineno", child.lineno), source_span=ast.get_source_segment(source, child) or "", authority=symbol_authority, metadata={"predicate": ast.get_source_segment(source, getattr(child, "test", child)) or "", "requirement_ids": tuple(str(goal.goal_id) for goal in matched_goals)})
                        graph.add_edge(GraphEdgeKind.DEFINES, sym.node_id, branch.node_id)
                        predicate = getattr(child, "test", None) or getattr(child, "subject", None)
                        for used in (item for item in ast.walk(predicate) if isinstance(item, ast.Name)) if predicate is not None else ():
                            if used.id in defined_values:
                                graph.add_edge(GraphEdgeKind.DEF_USE, defined_values[used.id], branch.node_id)
                    if isinstance(child, ast.Call):
                        called = _symbol_name(child.func)
                        if called:
                            value = graph.add_node(GraphNodeKind.VALUE, file=rel, symbol=called, line_start=child.lineno, line_end=getattr(child, "end_lineno", child.lineno), source_span=ast.get_source_segment(source, child) or "", metadata={"call": called})
                            graph.add_edge(GraphEdgeKind.DEF_USE, sym.node_id, value.node_id)
                            target = next((item for item in graph.nodes.values() if item.kind is GraphNodeKind.SYMBOL and item.symbol == called.rsplit(".", 1)[-1]), None)
                            if target is not None:
                                graph.add_edge(GraphEdgeKind.STATIC_CALLS, sym.node_id, target.node_id)
                    if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                        assigned_targets = child.targets if isinstance(child, ast.Assign) else (child.target,)
                        used_ids = {defined_values[used.id] for used in ast.walk(child.value)
                                    if isinstance(used, ast.Name) and used.id in defined_values} if child.value is not None else set()
                        if isinstance(child, ast.AugAssign) and isinstance(child.target, ast.Name) and child.target.id in defined_values:
                            used_ids.add(defined_values[child.target.id])
                        for target in assigned_targets:
                            if isinstance(target, ast.Name):
                                value = graph.add_node(GraphNodeKind.VALUE, file=rel, symbol=target.id, line_start=child.lineno, line_end=getattr(child, "end_lineno", child.lineno), source_span=ast.get_source_segment(source, child) or "", metadata={"definition": target.id})
                                graph.add_edge(GraphEdgeKind.DEFINES, sym.node_id, value.node_id)
                                for used_id in used_ids:
                                    graph.add_edge(GraphEdgeKind.DEF_USE, used_id, value.node_id)
                                defined_values[target.id] = value.node_id
                    if isinstance(child, (ast.Return, ast.Raise)):
                        expression = getattr(child, "value", None) or getattr(child, "exc", None)
                        value = graph.add_node(
                            GraphNodeKind.VALUE, file=rel,
                            symbol="return" if isinstance(child, ast.Return) else "raise",
                            line_start=child.lineno, line_end=getattr(child, "end_lineno", child.lineno),
                            source_span=ast.get_source_segment(source, child) or "",
                            metadata={"terminal": type(child).__name__.upper()},
                        )
                        graph.add_edge(GraphEdgeKind.DEFINES, sym.node_id, value.node_id)
                        for used in (item for item in ast.walk(expression) if isinstance(item, ast.Name)) if expression is not None else ():
                            if used.id in defined_values:
                                graph.add_edge(GraphEdgeKind.DEF_USE, defined_values[used.id], value.node_id)
        # Limit seed scanning to the bounded file budget and relevant files.
        if len({n.file for n in graph.nodes.values() if n.kind is GraphNodeKind.FILE}) >= effective_budget.max_files:
            graph._frontier(
                tuple(node.node_id for node in graph.nodes.values() if node.kind is GraphNodeKind.FILE)[-4:],
                ("STATIC_CALLS", "DEFINES", "DEF_USE"),
                "FILE_BUDGET",
            )
            break
    for node in tuple(graph.nodes.values()):
        if node.kind is not GraphNodeKind.ORACLE:
            continue
        for symbol in tuple(node.metadata.get("target_symbols", ())) + tuple(node.metadata.get("symbol_references", ())):
            target = next((item for item in graph.nodes.values() if item.kind is GraphNodeKind.SYMBOL and item.symbol == str(symbol).rsplit(".", 1)[-1]), None)
            if target is not None:
                graph.add_edge(GraphEdgeKind.TESTS, node.node_id, target.node_id, evidence_ids=(str(node.metadata.get("check_id", "")),))
    for value in tuple(graph.nodes.values()):
        if value.kind is not GraphNodeKind.VALUE or not value.metadata.get("call"):
            continue
        called = str(value.metadata["call"]).rsplit(".", 1)[-1]
        caller = next((
            item for item in graph.nodes.values()
            if item.kind is GraphNodeKind.SYMBOL and item.file == value.file
            and item.line_start <= value.line_start <= item.line_end
        ), None)
        callee = next((
            item for item in graph.nodes.values()
            if item.kind is GraphNodeKind.SYMBOL and item.symbol == called
        ), None)
        if caller is not None and callee is not None:
            graph.add_edge(GraphEdgeKind.STATIC_CALLS, caller.node_id, callee.node_id)
    graph.metrics["graph_seed_node_count"] = len(graph.nodes)
    # The initial generator receives a ranked graph context.  Count that
    # deterministic localization decision even when P0 immediately satisfies
    # the currently known executable target and no repair child is needed.
    if graph.nodes:
        graph.metrics["graph_localization_decision_count"] = 1
    return graph


def update_graph_from_execution(graph: DynamicReachAvoidGraph, obligation: Any, observation: Any, trace: Any, current_diff: Any = "") -> DynamicReachAvoidGraph:
    graph.revision += 1; graph.metrics["graph_update_count"] += 1
    check_id = str(getattr(obligation, "check_id", "") or "")
    raw_obligation_id = str(getattr(obligation, "obligation_id", "") or "")
    # Recovery stores obligations under stable check-derived IDs. ActiveFailure
    # records only carry check_id, so resolve that existing node instead of
    # creating a disconnected duplicate on the first execution failure.
    obligation_id = raw_obligation_id or (
        stable_id("obligation", check_id)
        if stable_id("obligation", check_id) in graph.nodes else check_id
    ) or "obligation"
    graph.record_update("EXECUTION", obligation_id=obligation_id, status=str(getattr(observation, "status", "")))
    obs_id = stable_id("observation", obligation_id, getattr(observation, "semantic_signature", repr(observation)), graph.revision)
    if obligation_id not in graph.nodes:
        graph.add_node(GraphNodeKind.OBLIGATION, node_id=obligation_id,
                       metadata=obligation.to_dict() if hasattr(obligation, "to_dict") else {"check_id": check_id})
    graph.add_node(GraphNodeKind.OBSERVATION, node_id=obs_id, status=str(getattr(observation, "status", "")), metadata=observation.to_dict() if hasattr(observation, "to_dict") else {"value": repr(observation)})
    graph.add_edge(GraphEdgeKind.TRACE_REACHES, obligation_id, obs_id, static_or_dynamic="dynamic")
    goal_id = str(getattr(obligation, "goal_id", "") or "")
    if goal_id in graph.nodes:
        graph.add_edge(GraphEdgeKind.EVIDENCE_SUPPORTS, obs_id, goal_id, static_or_dynamic="dynamic")
    changed_hunk_ids: list[str] = []
    for hunk in getattr(current_diff, "hunks", ()) or ():
        path = str(getattr(hunk, "path", "") or "")
        hunk_id = str(getattr(hunk, "hunk_id", "") or stable_id(
            "hunk", path, getattr(hunk, "old_start", 0), getattr(hunk, "new_start", 0),
        ))
        changed_hunk_ids.append(hunk_id)
        graph.add_node(
            GraphNodeKind.HUNK,
            node_id=hunk_id,
            file=path,
            line_start=int(getattr(hunk, "new_start", 0) or 0),
            line_end=int(getattr(hunk, "new_start", 0) or 0) + max(0, int(getattr(hunk, "new_count", 0) or 0)) - 1,
            source_span=str(getattr(hunk, "content", "") or ""),
            metadata={"path": path, "patch_hash": getattr(current_diff, "patch_hash", "")},
            revision=graph.revision,
        )
        graph.add_edge(GraphEdgeKind.MODIFIES, obs_id, hunk_id, static_or_dynamic="dynamic", trace_ids=(obs_id,))
    events = getattr(trace, "events", ()) or ()
    executed_symbols: list[str] = []
    def event_value(raw: Any, key: str, default: Any = None) -> Any:
        if isinstance(raw, dict):
            return raw.get(key, default)
        return getattr(raw, key, default)
    for raw in events:
        file = event_value(raw, "file", event_value(raw, "path", ""))
        line = event_value(raw, "line", 0)
        function = event_value(raw, "function", event_value(raw, "symbol", ""))
        event = event_value(raw, "event", "line")
        if isinstance(raw, (tuple, list)) and len(raw) >= 4:
            file, line, function, event = raw[-4], raw[-3], raw[-2], raw[-1]
        file, function = str(file), str(function); line = int(line or 0)
        symbol_id = next((key for key, node in graph.nodes.items() if node.kind is GraphNodeKind.SYMBOL and node.symbol == function and (not file or node.file and (node.file in file or file in node.file))), None)
        if symbol_id is None:
            symbol_id = graph.add_node(GraphNodeKind.SYMBOL, file=file, symbol=function, line_start=line, line_end=line).node_id
        executed_symbols.append(symbol_id)
        graph.add_edge(GraphEdgeKind.TRACE_REACHES, obs_id, symbol_id, static_or_dynamic="dynamic", trace_ids=(obs_id,))
        raw_branch_id = event_value(raw, "branch_id")
        if raw_branch_id:
            branch_id = str(raw_branch_id)
            predicate_line = line
            branch_line_match = re.search(r":(\d+)$", branch_id)
            if branch_line_match:
                predicate_line = int(branch_line_match.group(1))
            static_branch = next((node for node in graph.nodes.values()
                                  if node.kind is GraphNodeKind.BRANCH and node.file == file
                                  and node.line_start == predicate_line and node.source_span), None)
            predicate = str(event_value(raw, "predicate", "") or
                            (static_branch.metadata.get("predicate", static_branch.source_span) if static_branch else ""))
            branch_node = graph.add_node(GraphNodeKind.BRANCH, node_id=static_branch.node_id if static_branch else stable_id("branch", branch_id), file=file, symbol=function, line_start=predicate_line, line_end=predicate_line, source_span=static_branch.source_span if static_branch else predicate, metadata={"branch_id": branch_id, "predicate": predicate})
            raw_outcome = event_value(raw, "branch_outcome")
            if raw_outcome is not None and str(raw_outcome).casefold() in {"taken", "not_taken", "true", "false", "1", "0"}:
                branch_kind = GraphEdgeKind.BRANCH_TAKEN if str(raw_outcome).casefold() in {"taken", "true", "1"} else GraphEdgeKind.BRANCH_NOT_TAKEN
                graph.add_edge(branch_kind, obs_id, branch_node.node_id, static_or_dynamic="dynamic", trace_ids=(obs_id,))
                predicate_names = set(re.findall(r"\b[A-Za-z_]\w*\b", predicate))
                for key, summary in (event_value(raw, "safe_local_summary", {}) or {}).items():
                    if key not in predicate_names:
                        continue
                    value = graph.add_node(GraphNodeKind.VALUE, file=file, symbol=str(key), line_start=predicate_line, line_end=predicate_line, metadata={"summary": summary})
                    graph.add_edge(GraphEdgeKind.DEF_USE, value.node_id, branch_node.node_id, static_or_dynamic="dynamic", trace_ids=(obs_id,))
        caller_name = str(event_value(raw, "caller", "") or "")
        caller_file = str(event_value(raw, "caller_file", "") or "")
        if event == "call" and caller_name:
            caller_id = next((
                key for key, node in graph.nodes.items()
                if node.kind is GraphNodeKind.SYMBOL
                and node.symbol == caller_name
                and (not caller_file or node.file and (caller_file == node.file or caller_file.endswith("/" + node.file)))
            ), None)
            if caller_id and caller_id != symbol_id:
                graph.add_edge(GraphEdgeKind.DYNAMIC_CALLS, caller_id, symbol_id, static_or_dynamic="dynamic", trace_ids=(obs_id,))
    underlying = getattr(observation, "observation", observation)
    exception_text = str(getattr(underlying, "exception", "") or "")
    stderr = str(getattr(underlying, "stderr", "") or "")
    # Temporary execution roots are not part of a causal identity. Resolve
    # only project frames, excluding instrumentation and interpreter frames.
    project_paths = {node.file for node in graph.nodes.values() if node.file}
    assertion_frames = [f"{path}:{match.group(2)}"
        for match in re.finditer(r'File ["\']([^"\']+)["\'], line (\d+)', stderr)
        for path in project_paths
        if match.group(1) == path or match.group(1).endswith("/" + path)]
    assertion = assertion_frames[-1] if assertion_frames else None
    declared_symbols = tuple(getattr(obligation, "target_symbols", ()) or ()) or tuple(graph.nodes[obligation_id].metadata.get("target_symbols", ()))
    target_terms = {
        str(item).rsplit(".", 1)[-1].casefold()
        for item in declared_symbols
        if str(item).strip()
    }
    target_symbol = ""
    if target_terms:
        for symbol_id in executed_symbols:
            candidate = str(graph.nodes[symbol_id].symbol or "")
            if candidate.rsplit(".", 1)[-1].casefold() in target_terms:
                target_symbol = candidate
                break
    if not target_symbol and executed_symbols:
        target_symbol = str(graph.nodes[executed_symbols[-1]].symbol or "")
    target_frames = [f"{event_value(event, 'file', '')}:{event_value(event, 'line', 0)}"
                     for event in events if event_value(event, "function", "") == target_symbol]
    first_frame = target_frames[0] if target_frames else getattr(trace, "first_project_frame", None)
    locus_id = stable_id(
        "failure-locus", obligation_id, target_symbol, first_frame,
        assertion, exception_text.rsplit(":", 1)[0],
    )
    if locus_id not in graph.failure_loci:
        graph.failure_loci[locus_id] = FailureLocus(
            obligation_id, target_symbol, first_frame, assertion,
            exception_text.rsplit(":", 1)[0] or None,
            tuple(changed_hunk_ids),
        )
        graph.add_node(GraphNodeKind.FAILURE, node_id=locus_id, symbol=graph.failure_loci[locus_id].target_symbol, metadata=graph.failure_loci[locus_id].to_dict())
        graph.add_edge(GraphEdgeKind.FAILS_AT, obligation_id, locus_id, static_or_dynamic="dynamic")
    else:
        failure_node = graph.nodes[locus_id]
        graph.add_node(
            GraphNodeKind.FAILURE, node_id=locus_id,
            metadata={
                **failure_node.metadata,
                "occurrence_count": int(failure_node.metadata.get("occurrence_count", 1)) + 1,
            },
        )
    graph.add_edge(GraphEdgeKind.DERIVED_FROM, locus_id, obs_id,
                   static_or_dynamic="dynamic", trace_ids=(obs_id,))
    return graph


def update_graph_from_recovery(
    graph: DynamicReachAvoidGraph,
    recovery: Any,
    *,
    revision: int | None = None,
) -> DynamicReachAvoidGraph:
    """Attach recovered executable evidence to the existing graph instance."""
    graph.revision = max(graph.revision + 1, int(revision or 0))
    graph.record_update(
        "TARGET_RECOVERY",
        target_count=len(getattr(recovery, "target_checks", ()) or ()),
        preservation_count=len(getattr(recovery, "preservation_checks", ()) or ()),
    )
    graph.metrics["target_recovery_attempt_count"] += 1
    targets = tuple(getattr(recovery, "target_checks", ()) or ())
    preservations = tuple(getattr(recovery, "preservation_checks", ()) or ())
    if targets:
        graph.metrics["target_recovery_success"] += 1
    for check in (*targets, *preservations):
        check_id = str(getattr(check, "check_id", stable_id("check", repr(check))))
        role = str(getattr(getattr(check, "role", None), "value", getattr(check, "role", "TARGET"))).upper()
        obligation_id = stable_id("obligation", check_id)
        obligation = graph.add_node(
            GraphNodeKind.OBLIGATION,
            node_id=obligation_id,
            authority=str(getattr(check, "authority", "PROVISIONAL")),
            status=role,
            metadata={
                "check_id": check_id,
                "goal_id": getattr(check, "goal_id", None),
                "role": role,
                "command": tuple(getattr(check, "command", ())),
                "cwd": str(getattr(check, "cwd", ".")),
                "environment": tuple(getattr(check, "environment", ())),
                "timeout_seconds": float(getattr(check, "timeout_seconds", 120.0)),
                "comparator": getattr(check, "comparator", ""),
                "expected": getattr(check, "expected", None),
                "target_symbols": tuple(getattr(check, "target_symbols", ())),
                "input_recipe": getattr(check, "input_recipe", None),
            },
            revision=graph.revision,
        )
        oracle = graph.add_node(
            GraphNodeKind.ORACLE,
            node_id=stable_id("oracle", check_id),
            authority=str(getattr(check, "authority", "PROVISIONAL")),
            status="TRUSTED" if str(getattr(check, "authority", "")).upper() in {"A", "B", "C"} else "EXPLORATORY",
            metadata={"check_id": check_id, "role": role},
            revision=graph.revision,
        )
        graph.add_edge(GraphEdgeKind.REQUIRES_VALIDATION, obligation.node_id, oracle.node_id, static_or_dynamic="dynamic")
        for symbol in tuple(getattr(check, "target_symbols", ())) + tuple(getattr(check, "symbol_references", ())):
            terminal = str(symbol).rsplit(".", 1)[-1]
            target = next((node for node in graph.nodes.values() if node.kind is GraphNodeKind.SYMBOL and node.symbol == terminal), None)
            if target is not None:
                graph.add_edge(GraphEdgeKind.TESTS, oracle.node_id, target.node_id, static_or_dynamic="dynamic", evidence_ids=tuple(getattr(check, "evidence_ids", ())))
    return graph


def update_graph_from_challenges(
    graph: DynamicReachAvoidGraph,
    results: Sequence[Any],
    *,
    checkpoint_id: str | None = None,
) -> DynamicReachAvoidGraph:
    """Attach executable challenge observations to their graph cells."""
    changed = False
    for result in results:
        check_id = str(getattr(result, "check_id", "") or "")
        if not check_id:
            continue
        challenge = graph.nodes.get(check_id)
        if challenge is None or challenge.kind is not GraphNodeKind.CHALLENGE:
            challenge = next(
                (
                    node for node in graph.nodes.values()
                    if node.kind is GraphNodeKind.CHALLENGE
                    and str(node.metadata.get("challenge_id", "")) == check_id
                ),
                None,
            )
        if challenge is None:
            continue
        graph.revision += 1
        graph.metrics["graph_update_count"] += 1
        observation_id = stable_id(
            "challenge-observation", check_id,
            getattr(result, "semantic_signature", repr(result)), graph.revision,
        )
        graph.add_node(
            GraphNodeKind.OBSERVATION,
            node_id=observation_id,
            file=challenge.file,
            symbol=challenge.symbol,
            line_start=challenge.line_start,
            line_end=challenge.line_end,
            status=str(getattr(result, "status", "")),
            metadata=result.to_dict() if hasattr(result, "to_dict") else {"check_id": check_id},
            revision=graph.revision,
        )
        metadata = {
            **challenge.metadata,
            "status": str(getattr(result, "status", challenge.status)),
            "observation_ids": tuple(dict.fromkeys((*challenge.metadata.get("observation_ids", ()), observation_id))),
            "affected_checkpoint_ids": tuple(dict.fromkeys((*challenge.metadata.get("affected_checkpoint_ids", ()), *( (checkpoint_id,) if checkpoint_id else () )))),
        }
        graph.nodes[challenge.node_id] = replace(
            challenge,
            status=str(getattr(result, "status", challenge.status)),
            metadata=metadata,
            last_updated_revision=graph.revision,
        )
        graph.add_edge(
            GraphEdgeKind.TRACE_REACHES,
            observation_id,
            challenge.node_id,
            static_or_dynamic="dynamic",
            trace_ids=tuple(
                item for item in (getattr(getattr(result, "trace", None), "trace_bundle_id", None),)
                if item
            ),
        )
        if str(getattr(result, "status", "")).upper() == "PASS":
            graph.add_edge(GraphEdgeKind.CLOSES, observation_id, challenge.node_id, static_or_dynamic="dynamic")
        changed = True
    if changed:
        graph.record_update(
            "CHALLENGE_EXECUTION",
            checkpoint_id=checkpoint_id,
            result_count=sum(1 for item in results if getattr(item, "check_id", None)),
        )
    return graph


def rank_causal_cuts(graph: DynamicReachAvoidGraph, requirement_id: str, failure_id: str, current_diff: str, limit: int = 3) -> tuple[CausalCutCandidate, ...]:
    graph.metrics["graph_localization_decision_count"] += 1
    candidates: list[CausalCutCandidate] = []
    hunks = re.findall(r"\+\+\+ b/(.+?)\n@@[^\n]*\n", str(current_diff))
    hunk_ids = tuple(stable_id("hunk", path) for path in hunks)
    failure = graph.nodes.get(failure_id)
    if failure is None:
        # Controller failure IDs are execution signatures, whereas graph
        # failure nodes are stable causal loci. Resolve through the linked
        # obligation when callers provide the former.
        failure = next((
            node for node in graph.nodes.values()
            if node.kind is GraphNodeKind.FAILURE
            and str(node.metadata.get("obligation_id", "")) in {str(requirement_id), str(failure_id)}
        ), None)
    obligations = {node.node_id for node in graph.nodes.values()
                   if node.kind is GraphNodeKind.OBLIGATION
                   and (node.metadata.get("goal_id") == requirement_id
                        or node.metadata.get("check_id") in {requirement_id, failure_id})}
    if failure is None:
        failure = max((node for node in graph.nodes.values()
                       if node.kind is GraphNodeKind.FAILURE
                       and node.metadata.get("obligation_id") in obligations),
                      key=lambda node: (node.last_updated_revision, node.node_id), default=None)
    first_frame = str(failure.metadata.get("first_target_frame", "")) if failure else ""
    frame_match = re.search(r"^(.*?):(\d+)(?::(.*))?$", first_frame)
    frame_file = frame_match.group(1) if frame_match else ""
    frame_line = int(frame_match.group(2)) if frame_match else 0
    trace_ids = {trace_id for edge in graph.edges.values()
                 if edge.active and failure and edge.source_id == failure.node_id
                 for trace_id in edge.trace_ids}
    target_names = {str(symbol) for node_id in obligations
                    for symbol in graph.nodes[node_id].metadata.get("target_symbols", ())}
    if failure and failure.symbol:
        target_names.add(failure.symbol)

    def requirement_symbol_matches(node_symbol: str | None) -> bool:
        """Bind a class requirement to its current method symbols.

        Source refresh versions whole classes and their methods separately.
        If the class span is retired after P0, an exact-only comparison leaves
        a requirement such as ``RenameIndex`` with no current root even though
        ``RenameIndex.database_backwards`` is the executed failure locus.
        This is a qualified member relation, never a substring match.
        """
        candidate = str(node_symbol or "")
        for declared in target_names:
            declared = str(declared)
            leaf = declared.rsplit(".", 1)[-1]
            if candidate in {declared, leaf}:
                return True
            if candidate.startswith(declared + ".") or candidate.startswith(leaf + "."):
                return True
        return False

    roots = {node.node_id for node in graph.nodes.values()
             if node.kind is GraphNodeKind.SYMBOL
             and node.status not in {"RETIRED_SOURCE", "HISTORICAL_SOURCE"}
             and (requirement_id in node.metadata.get("requirement_ids", ())
                  or requirement_symbol_matches(node.symbol))}
    scope = set(roots)
    # A bounded view of the same graph, not a new graph or repository scan.
    for _ in range(graph.budget.initial_caller_depth + 1):
        previous_scope = set(scope)
        scope.update(edge.target_id if edge.source_id in previous_scope else edge.source_id
                     for edge in tuple(graph.edges.values()) if edge.active
                     and edge.kind in {GraphEdgeKind.STATIC_CALLS, GraphEdgeKind.DYNAMIC_CALLS, GraphEdgeKind.DEF_USE}
                     and (edge.source_id in previous_scope or edge.target_id in previous_scope))
    containers = [graph.nodes[key] for key in scope if graph.nodes[key].kind is GraphNodeKind.SYMBOL
                  and graph.nodes[key].status not in {"RETIRED_SOURCE", "HISTORICAL_SOURCE"}]
    scope.update(node.node_id for node in graph.nodes.values()
                 if any(node.file == symbol.file and symbol.line_start <= node.line_start <= symbol.line_end
                        for symbol in containers))
    if not scope:
        # Missing binding is an evidence frontier, not permission to rank every
        # unrelated symbol in the repository.
        graph.record_update("LOCALIZATION_UNRESOLVED", requirement_id=requirement_id, failure_id=failure_id)
        return ()
    def score(node: GraphNode) -> tuple[int, int, int, str, int]:
        is_first_frame = bool(frame_file and node.file and
                              (frame_file == node.file or frame_file.endswith("/" + node.file))
                              and node.line_start <= frame_line <= node.line_end)
        has_trace = any(
            edge.active and edge.target_id == node.node_id and set(edge.trace_ids).intersection(trace_ids)
            for edge in graph.edges.values()
        )
        dynamic = any(
            edge.kind in {GraphEdgeKind.DYNAMIC_CALLS, GraphEdgeKind.BRANCH_TAKEN, GraphEdgeKind.BRANCH_NOT_TAKEN, GraphEdgeKind.DEF_USE}
            and edge.static_or_dynamic == "dynamic"
            and edge.active and set(edge.trace_ids).intersection(trace_ids)
            and (edge.source_id == node.node_id or edge.target_id == node.node_id)
            for edge in graph.edges.values()
        )
        changed = bool(node.file and any(node.file == path for path in hunks))
        kind_priority = {
            GraphNodeKind.BRANCH: 1,
            GraphNodeKind.VALUE: 2,
            GraphNodeKind.SYMBOL: 3,
        }.get(node.kind, 9)
        discriminated = bool(node.metadata.get("sibling_disagreement_probe_ids"))
        return (0 if is_first_frame else 1, 0 if has_trace else 1 if dynamic else 2 if discriminated else 3 if changed else 4, kind_priority, node.file or "", node.line_start)
    relevant = sorted(
        (node for node in graph.nodes.values() if node.node_id in scope and node.source_span and node.status != "RETIRED_SOURCE"
         and node.status != "HISTORICAL_SOURCE" and node.kind in {GraphNodeKind.SYMBOL, GraphNodeKind.BRANCH, GraphNodeKind.VALUE}),
        key=score,
    )
    # Materialize only the bounded decision set.  Creating an OBLIGATION node
    # for every AST value defeats the graph budget and makes localization
    # metrics count candidates the search never considered.
    selected_nodes: list[GraphNode] = []
    seen_functions: set[tuple[str | None, str | None]] = set()
    seen_kinds: set[GraphNodeKind] = set()
    for node in relevant:
        function_key = (node.file, node.symbol)
        diversity_key = (function_key, node.kind)
        if len(selected_nodes) < max(1, limit):
            if node.kind is GraphNodeKind.SYMBOL and function_key in seen_functions and len(selected_nodes) < limit - 1:
                continue
            if node.kind in seen_kinds and len(selected_nodes) < limit - 1 and any(item.kind is not node.kind for item in selected_nodes):
                continue
            selected_nodes.append(node)
            seen_functions.add(function_key)
            seen_kinds.add(node.kind)
        if len(selected_nodes) >= max(1, limit):
            break
    if len(selected_nodes) < max(1, limit):
        for node in relevant:
            if node not in selected_nodes:
                selected_nodes.append(node)
            if len(selected_nodes) >= max(1, limit):
                break
    for priority, node in enumerate(selected_nodes):
        incident = tuple(edge.edge_id for edge in graph.edges.values() if edge.source_id == node.node_id or edge.target_id == node.node_id)
        cut_id = stable_id("causal-cut", requirement_id, failure.node_id if failure else failure_id, node.node_id)
        source_context = (
            f"{node.file}:{node.line_start}-{node.line_end}\n{node.source_span}"
            if node.file else node.source_span
        )
        candidates.append(CausalCutCandidate(cut_id, requirement_id, (node.node_id,) if node.kind is GraphNodeKind.SYMBOL else (), (node.node_id,) if node.kind is GraphNodeKind.BRANCH else (), incident if node.kind is GraphNodeKind.VALUE else (), hunk_ids if node.file and any(node.file == path for path in hunks) else (), (source_context,) if source_context else (), tuple(edge.trace_ids[0] for edge in graph.edges.values() if edge.target_id == node.node_id and edge.trace_ids), f"evidence-priority-{priority}", candidates[0].cut_id if candidates else None))
        candidates[-1] = replace(candidates[-1],
            failure_locus_id=failure.node_id if failure else failure_id,
            predicate=str(node.metadata.get("predicate", "")),
            observed_outcomes=tuple(sorted({str(edge.kind) for edge in graph.edges.values()
                if edge.target_id == node.node_id and edge.active
                and edge.kind in {GraphEdgeKind.BRANCH_TAKEN, GraphEdgeKind.BRANCH_NOT_TAKEN}
                and set(edge.trace_ids).intersection(trace_ids)})),
            ranking_reason=f"exact_frame={score(node)[0] == 0}; current_execution_rank={score(node)[1]}; requirement_bound=True")
        graph.add_node(GraphNodeKind.OBLIGATION, node_id=cut_id, file=node.file, symbol=node.symbol, line_start=node.line_start, line_end=node.line_end, source_span=node.source_span, metadata={**candidates[-1].to_dict(), "obligation_kind": "CAUSAL_CUT"})
    selected = tuple(candidates)
    graph.metrics["distinct_causal_cut_count"] = sum(node.metadata.get("obligation_kind") == "CAUSAL_CUT" for node in graph.nodes.values())
    return selected


def expand_dynamic_graph_frontier(
    graph: DynamicReachAvoidGraph,
    repo_root: Path,
    boundary_node_ids: Sequence[str] = (),
) -> DynamicReachAvoidGraph:
    """Expand direct caller/callee definitions without rebuilding the graph."""
    if graph.metrics["graph_expansion_count"] >= graph.budget.max_expansion_depth:
        graph._frontier(tuple(boundary_node_ids), ("STATIC_CALLS", "DEF_USE"), "TIME_BUDGET")
        return graph
    started = time.monotonic()
    graph.revision += 1
    boundaries = [
        graph.nodes[node_id] for node_id in boundary_node_ids
        if node_id in graph.nodes and graph.nodes[node_id].kind is GraphNodeKind.SYMBOL
    ]
    if not boundaries:
        boundaries = [
            node for node in graph.nodes.values()
            if node.kind is GraphNodeKind.SYMBOL
        ][:32]
    relation_names: set[str] = set()
    for boundary in boundaries:
        if time.monotonic() - started >= graph.budget.wall_seconds:
            graph._frontier((boundary.node_id,), ("STATIC_CALLS", "DEF_USE"), "TIME_BUDGET")
            break
        if not boundary.file:
            continue
        path = Path(repo_root) / boundary.file
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=boundary.file)
        except (OSError, SyntaxError):
            graph._frontier((boundary.node_id,), ("STATIC_CALLS", "DEF_USE"), "TIME_BUDGET")
            continue
        owner = next((
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == boundary.symbol
            and node.lineno == boundary.line_start
        ), None)
        if owner is not None:
            relation_names.update(
                name.rsplit(".", 1)[-1]
                for call in ast.walk(owner) if isinstance(call, ast.Call)
                for name in (_symbol_name(call.func),) if name
            )
        if boundary.symbol:
            relation_names.add(boundary.symbol)
    if not relation_names:
        graph._frontier(tuple(node.node_id for node in boundaries), ("STATIC_CALLS",), "SYMBOL_BUDGET")
        return graph
    pattern = r"\b(?:" + "|".join(map(re.escape, sorted(relation_names))) + r")\s*\("
    try:
        completed = subprocess.run(
            ["rg", "-l", "-g", "*.py", "-g", "!**/.git/**", pattern, str(repo_root)],
            capture_output=True, text=True, check=False,
            timeout=max(1.0, graph.budget.wall_seconds - (time.monotonic() - started)),
        )
        raw_paths = completed.stdout.splitlines() if completed.returncode in {0, 1} else ()
    except (OSError, subprocess.TimeoutExpired):
        graph._frontier(tuple(node.node_id for node in boundaries), ("REFERENCES", "STATIC_CALLS"), "TIME_BUDGET")
        return graph
    for raw in raw_paths:
        if time.monotonic() - started >= graph.budget.wall_seconds:
            graph._frontier(tuple(node.node_id for node in boundaries), ("REFERENCES", "STATIC_CALLS"), "TIME_BUDGET")
            break
        try:
            path = Path(raw).resolve()
            relative = path.relative_to(Path(repo_root).resolve()).as_posix()
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=relative)
        except (OSError, SyntaxError, ValueError):
            continue
        if not graph._admit_file(relative, revision=graph.revision):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = {
                (_symbol_name(call.func) or "").rsplit(".", 1)[-1]
                for call in ast.walk(node) if isinstance(call, ast.Call)
            }
            if node.name not in relation_names and not calls.intersection(relation_names):
                continue
            if sum(item.kind is GraphNodeKind.SYMBOL for item in graph.nodes.values()) >= graph.budget.max_symbols:
                graph._frontier(tuple(item.node_id for item in boundaries), ("SYMBOL",), "SYMBOL_BUDGET")
                break
            symbol = graph.add_node(
                GraphNodeKind.SYMBOL,
                node_id=stable_id("symbol", relative, node.name),
                file=relative, symbol=node.name,
                line_start=node.lineno,
                line_end=getattr(node, "end_lineno", node.lineno),
                source_span=ast.get_source_segment(source, node) or "",
                revision=graph.revision,
            )
            for callee_name in calls:
                callee = next((
                    item for item in graph.nodes.values()
                    if item.kind is GraphNodeKind.SYMBOL and item.symbol == callee_name
                ), None)
                if callee is not None and callee.node_id != symbol.node_id:
                    graph.add_edge(GraphEdgeKind.STATIC_CALLS, symbol.node_id, callee.node_id)
    graph.metrics["graph_expansion_count"] += 1
    graph.record_update("EXPAND_GRAPH", boundary_node_ids=tuple(node.node_id for node in boundaries))
    return graph


def derive_validation_obligations(graph: DynamicReachAvoidGraph, checkpoint_id: str) -> ValidationBatch:
    """Select grounded checks; never promote a cut/exploratory cell to a test."""
    if checkpoint_id not in graph.nodes:
        raise KeyError(f"Unknown validation checkpoint: {checkpoint_id}")
    nodes = [node for node in graph.nodes.values()
             if node.kind is GraphNodeKind.OBLIGATION
             and node.metadata.get("executable_handle")
             and node.metadata.get("check_id") and node.metadata.get("command")
             and node.metadata.get("obligation_kind") != "CAUSAL_CUT"
             and node.status not in {"EXPLORATION_ONLY", "RETIRED"}]
    # Mandatory evidence precedes discretionary checks. All applicable trusted
    # preservation checks remain in the batch: static slicing cannot prove an
    # unvisited consumer unaffected.
    nodes.sort(key=lambda node: (
        not bool(node.metadata.get("locked")),
        {"TARGET": 0, "PRESERVATION": 1, "CHALLENGE": 2}.get(node.metadata.get("role"), 3),
        -int(node.metadata.get("sibling_disagreement_count", 0)), node.node_id,
    ))
    mechanical: list[str] = []
    target: list[str] = []
    preservation: list[str] = []
    challenge: list[str] = []
    locked: list[str] = []
    for node in nodes:
        node_id = node.node_id
        role = str(node.metadata.get("role", node.metadata.get("kind", ""))).upper()
        affected = node.metadata.get("affected_checkpoint_ids", ())
        if role == "CHALLENGE" and affected and checkpoint_id not in affected and not node.metadata.get("locked"):
            continue
        if role == "MECHANICAL": mechanical.append(node_id)
        elif "PRESERV" in role: preservation.append(node_id)
        elif "CHALLENGE" in role: challenge.append(node_id)
        elif role == "TARGET": target.append(node_id)
        else:
            raise ValueError(f"Unknown executable obligation role: {role}")
        if node.metadata.get("locked"):
            locked.append(node_id)
        graph.add_edge(GraphEdgeKind.REQUIRES_VALIDATION, checkpoint_id, node_id)
    batch = ValidationBatch(tuple(mechanical), tuple(target), tuple(preservation), tuple(challenge), tuple(locked))
    graph.record_update("VALIDATION_BATCH", checkpoint_id=checkpoint_id, batch=batch.to_dict())
    graph.metrics["graph_derived_validation_count"] += len(set(mechanical + target + preservation + challenge + locked))
    return batch


def register_validation_checks(graph: DynamicReachAvoidGraph, checks: Sequence[Any], *, locked_ids: Sequence[str] = ()) -> None:
    """Bind executable handles without manufacturing a recovery attempt."""
    for check in checks:
        node_id = stable_id("obligation", check.check_id)
        prior = graph.nodes.get(node_id)
        metadata = {**(prior.metadata if prior else {}), **check.to_dict(),
                    "role": str(check.role), "locked": check.check_id in locked_ids,
                    "executable_handle": True}
        recipe = check.input_recipe if isinstance(check.input_recipe, dict) else {}
        metadata["affected_checkpoint_ids"] = tuple(recipe.get("affected_checkpoint_ids", ()))
        metadata["sibling_disagreement_count"] = int(recipe.get("sibling_disagreement_count", 0))
        graph.add_node(GraphNodeKind.OBLIGATION, node_id=node_id,
                       authority=check.authority, status="EXECUTABLE", metadata=metadata)
        oracle_id = stable_id("oracle", check.check_id)
        graph.add_node(GraphNodeKind.ORACLE, node_id=oracle_id, authority=check.authority,
                       metadata={"comparator": check.comparator, "expected": check.expected,
                                 "evidence_ids": check.evidence_ids})
        graph.add_edge(GraphEdgeKind.REQUIRES_VALIDATION, node_id, oracle_id,
                       evidence_ids=tuple(check.evidence_ids))
        if check.goal_id in graph.nodes:
            graph.add_edge(GraphEdgeKind.DERIVED_FROM, node_id, check.goal_id,
                           evidence_ids=tuple(check.evidence_ids))


def predicate_input_recipes(predicate: str) -> tuple[dict[str, Any], ...]:
    """Literal AST boundaries. Recipes are hypotheses about inputs, not oracles."""
    expression = predicate.strip().removesuffix(":")
    for prefix in ("if ", "elif ", "while "):
        if expression.startswith(prefix):
            expression = expression[len(prefix):]
            break
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return ({"kind": "UNRESOLVED_PREDICATE", "predicate": predicate},)
    recipes: list[dict[str, Any]] = []
    for comparison in (node for node in ast.walk(tree) if isinstance(node, ast.Compare)):
        operands = [comparison.left, *comparison.comparators]
        for left, operator, right in zip(operands, comparison.ops, operands[1:]):
            variable, literal = left, right
            if isinstance(left, (ast.Constant, ast.UnaryOp)):
                variable, literal = right, left
            try:
                value = ast.literal_eval(literal)
            except (ValueError, TypeError, SyntaxError):
                continue
            values: tuple[Any, ...]
            if isinstance(operator, (ast.In, ast.NotIn)) and isinstance(value, (list, tuple, set, str)):
                members = list(value)[:2]
                outsider = "__reachpatch_outside__"
                while outsider in value:
                    outsider += "_"
                values = (*members, outsider)
            elif type(value) is int:
                values = (value - 1, value, value + 1)
            elif type(value) is float:
                import math
                values = (math.nextafter(value, -math.inf), value, math.nextafter(value, math.inf))
            elif value is None:
                values = (None, [], [1])
            else:
                values = (value,)
            recipes.extend({"kind": "BOUNDARY", "parameter": ast.unparse(variable), "value": item}
                           for item in values)
    if not recipes:
        recipes.extend({"kind": "TRUTHINESS", "parameter": ast.unparse(tree.body), "value": item}
                       for item in (None, [], [1], 0, 1, -1))
    return tuple({content_hash(item): item for item in recipes}.values())


def materialize_graph_guided_challenges(repo_root: Path, graph: DynamicReachAvoidGraph, checkpoint: CheckpointState, observations: Sequence[Any]) -> tuple[ChallengeCell, ...]:
    """Generate bounded boundary probes from changed predicates and value flow."""
    repo_root = Path(repo_root).resolve()
    commands: list[tuple[str, ...]] = []
    observed_check_ids: set[str] = set()
    for item in observations:
        command = getattr(item, "command", None)
        if command: commands.append(tuple(str(part) for part in command))
        check_id = getattr(item, "check_id", None)
        if check_id:
            observed_check_ids.add(str(check_id))
        check = getattr(item, "check", None)
        if check is not None and getattr(check, "command", None): commands.append(tuple(str(part) for part in check.command))
    # Recovery obligations retain the exact command and typed oracle even
    # when the current observation object is a compact CheckExecution.  Use
    # those records as the validation source instead of manufacturing a
    # command from a branch node.
    obligations = [
        node for node in graph.nodes.values()
        if node.kind is GraphNodeKind.OBLIGATION
        and node.metadata.get("obligation_kind") != "CAUSAL_CUT"
        and node.metadata.get("role") in {"TARGET", "PRESERVATION"}
        and (not observed_check_ids or str(node.metadata.get("check_id", "")) in observed_check_ids)
    ]
    if not obligations:
        obligations = [
            node for node in graph.nodes.values()
            if node.kind is GraphNodeKind.OBLIGATION
            and node.metadata.get("obligation_kind") != "CAUSAL_CUT"
            and node.metadata.get("role") in {"TARGET", "PRESERVATION"}
        ]
    if not commands:
        commands.extend(
            tuple(str(part) for part in node.metadata.get("command", ()))
            for node in obligations if node.metadata.get("command")
        )
    command = commands[0] if commands else ()
    changed = set(checkpoint.causal_cut_ids)
    cut_branch_ids = {
        branch_id
        for cut_id in changed
        for branch_id in tuple((graph.nodes.get(cut_id).metadata if graph.nodes.get(cut_id) else {}).get("branch_ids", ()))
    }
    cut_locations = {(graph.nodes[cut_id].file, graph.nodes[cut_id].symbol)
                     for cut_id in changed if cut_id in graph.nodes}
    diff_files = set(re.findall(r"^\+\+\+ b/(.+)$", checkpoint.base_to_current_diff, re.M))
    branches = [
        node for node in graph.nodes.values()
        if node.kind is GraphNodeKind.BRANCH and node.status not in {"RETIRED_SOURCE", "HISTORICAL_SOURCE"}
        and (not diff_files or node.file in diff_files or (node.file, node.symbol) in cut_locations)
        and (not changed or node.node_id in changed or node.node_id in cut_branch_ids
             or (node.file, node.symbol) in cut_locations)
    ]
    result: list[ChallengeCell] = []
    for branch in branches[:24]:
        predicate = str(branch.metadata.get("predicate", branch.source_span))
        recipes = predicate_input_recipes(predicate)
        # Prefer a typed obligation bound to this symbol.  Its authority and
        # oracle are evidence-backed; branch metadata alone is never enough
        # to certify a generated challenge.
        bound = next((item for item in obligations if (
            str(branch.symbol or "").casefold() in {
                str(symbol).rsplit(".", 1)[-1].casefold()
                for symbol in item.metadata.get("target_symbols", ())
            }
        )), None)
        oracle_payload = bound.metadata.get("expected") if bound is not None else branch.metadata.get("oracle")
        comparator = bound.metadata.get("comparator", "") if bound is not None else ""
        authority = str(bound.authority if bound is not None else (branch.authority or branch.metadata.get("authority", "PROVISIONAL"))).upper()
        base_command = tuple(str(part) for part in (bound.metadata.get("command", ()) if bound is not None else command))
        # A check may provide executable adjacent recipes.  Preserve their
        # command exactly; otherwise the generated boundary cell remains an
        # exploratory input and cannot enter certification.
        variants: list[dict[str, Any]] = []
        raw_variants = (bound.metadata.get("input_recipe") if bound is not None else None)
        if isinstance(raw_variants, dict):
            for raw in raw_variants.get("variants", ()) or raw_variants.get("challenge_commands", ()) or ():
                if isinstance(raw, dict) and isinstance(raw.get("command"), (tuple, list)):
                    variants.append(raw)
        for index, variant in enumerate((*({"input": recipe} for recipe in recipes), *variants)):
            variant_command = tuple(str(part) for part in variant.get("command", ()))
            variant_input = variant.get("input", variant)
            if not variant_command and isinstance(variant_input, dict) and bound is not None:
                from reachpatch.execution.discriminating_probes import instantiate_probe_command
                symbol = next((node for node in graph.nodes.values()
                               if node.kind is GraphNodeKind.SYMBOL and node.file == branch.file
                               and node.symbol == branch.symbol), None)
                if symbol:
                    variant_command = instantiate_probe_command(base_command, symbol.source_span, str(symbol.symbol), variant_input)
            challenge_id = stable_id("graph-challenge", checkpoint.checkpoint_id, branch.node_id, variant_input, variant_command)
            if not variant_command:
                gap_id = stable_id("missing-probe-adapter", checkpoint.checkpoint_id, branch.node_id)
                if gap_id not in graph.nodes:
                    graph.add_node(GraphNodeKind.OBLIGATION, node_id=gap_id,
                        file=branch.file, status="MISSING_ADAPTER", metadata={
                            "obligation_kind": "PROBE_ADAPTER_GAP", "source_branch_id": branch.node_id,
                            "checkpoint_id": checkpoint.checkpoint_id,
                            "source_exists": bool(branch.file and (repo_root / branch.file).is_file()),
                            "reason": "NO_EXECUTABLE_PROBE_ADAPTER"})
                    graph.add_edge(GraphEdgeKind.REQUIRES_VALIDATION, checkpoint.checkpoint_id, gap_id)
                continue
            oracle = variant.get("oracle")
            authority = str(variant.get("authority", "PROVISIONAL")).upper()
            evidence_ids = tuple(variant.get("evidence_ids", ()))
            # Source authority alone does not grant an adjacent input an
            # Oracle.  A challenge is certifying only when the graph carries
            # an explicit typed contract for this exact input partition.
            status = "PENDING" if (authority in {"A", "B", "C"}
                and isinstance(oracle, dict) and oracle.get("comparator")
                and "expected" in oracle and evidence_ids and variant_command
                and variant_command != base_command) else "EXPLORATION_ONLY"
            cell = ChallengeCell(
                challenge_id=challenge_id, source_branch_id=branch.node_id,
                source_value_flow_ids=tuple(edge.edge_id for edge in graph.edges.values() if edge.active and edge.kind is GraphEdgeKind.DEF_USE and (edge.source_id == branch.node_id or edge.target_id == branch.node_id)),
                input_recipe={"kind": "BOUNDARY", "predicate": predicate, "value": variant_input,
                              "evidence_ids": evidence_ids, "source_check_id": bound.metadata.get("check_id") if bound else None,
                              "status_reason": "EXACT_INPUT_CONTRACT" if status == "PENDING" else
                                  "NO_EXACT_INPUT_ORACLE" if variant_command else "NO_EXECUTABLE_PROBE_ADAPTER"},
                oracle=oracle, authority=authority,
                command=variant_command, status=status,
                affected_checkpoint_ids=(checkpoint.checkpoint_id,),
            )
            if bound is not None:
                cell = replace(cell, source_branch_id=branch.node_id)
            is_new = challenge_id not in graph.nodes
            if not is_new:
                existing = graph.nodes[challenge_id]
                cell = replace(cell, status=existing.status,
                               observation_ids=tuple(existing.metadata.get("observation_ids", ())))
                result.append(cell)
                continue
            graph.add_node(GraphNodeKind.CHALLENGE, node_id=challenge_id, file=branch.file, symbol=branch.symbol, line_start=branch.line_start, line_end=branch.line_end, authority=authority, status=status, metadata=cell.to_dict())
            graph.add_node(
                GraphNodeKind.OBLIGATION,
                node_id=stable_id("challenge-obligation", challenge_id),
                file=branch.file,
                symbol=branch.symbol,
                line_start=branch.line_start,
                line_end=branch.line_end,
                authority=authority,
                status=status,
                metadata={
                    "obligation_kind": "CHALLENGE",
                    "challenge_id": challenge_id,
                    "role": "CHALLENGE",
                    "check_id": challenge_id if status == "PENDING" else None,
                    "command": variant_command,
                    "comparator": oracle.get("comparator") if isinstance(oracle, dict) else None,
                    "expected": oracle.get("expected") if isinstance(oracle, dict) else None,
                    "affected_checkpoint_ids": (checkpoint.checkpoint_id,),
                    "oracle": oracle,
                    "authority": authority,
                },
            )
            graph.add_edge(GraphEdgeKind.DERIVED_FROM, challenge_id, branch.node_id)
            graph.add_edge(
                GraphEdgeKind.REQUIRES_VALIDATION,
                stable_id("challenge-obligation", challenge_id),
                challenge_id,
                static_or_dynamic="dynamic",
            )
            if is_new:
                graph.metrics["graph_generated_challenge_count"] += 1
            result.append(cell)
    return prioritize_sibling_challenges(graph, checkpoint, tuple(result))


def prioritize_sibling_challenges(graph: DynamicReachAvoidGraph, checkpoint: CheckpointState, cells: Sequence[ChallengeCell]) -> tuple[ChallengeCell, ...]:
    """Use observed/patched branch disagreements, never majority-vote oracles."""
    siblings = [node for node in graph.nodes.values() if node.kind is GraphNodeKind.CHECKPOINT
                and node.metadata.get("parent_checkpoint_id") == checkpoint.parent_checkpoint_id
                and checkpoint.parent_checkpoint_id is not None and node.status != "REJECTED"]
    ranked: list[tuple[int, ChallengeCell]] = []
    for cell in cells:
        branch = graph.nodes.get(cell.source_branch_id or "")
        if branch is None:
            ranked.append((0, cell))
            continue
        signatures = {content_hash({
            "target": {key: value.get("semantic_signature") for key, value in node.metadata.get("target_results", {}).items()},
            "branch_edits": [line for line in node.metadata.get("base_to_current_diff", "").splitlines()
                             if line.startswith(("+", "-")) and str(branch.metadata.get("predicate", branch.source_span)) in line],
        }) for node in siblings}
        disagreement = max(0, len(signatures) - 1) + len(branch.metadata.get("sibling_disagreement_probe_ids", ()))
        recipe = {**cell.input_recipe, "sibling_disagreement_count": disagreement,
                  "affected_checkpoint_ids": cell.affected_checkpoint_ids}
        updated = replace(cell, input_recipe=recipe)
        graph.add_node(GraphNodeKind.CHALLENGE, node_id=cell.challenge_id,
                       file=branch.file, symbol=branch.symbol, authority=cell.authority,
                       status=cell.status, metadata={**graph.nodes[cell.challenge_id].metadata, **updated.to_dict()})
        ranked.append((disagreement, updated))
    return tuple(cell for _, cell in sorted(ranked, key=lambda pair: (-pair[0], pair[1].challenge_id)))


def build_distinct_repair_hypotheses(parent: CheckpointState, requirement_id: str, failure_id: str, causal_cuts: Sequence[CausalCutCandidate], *, limit: int = 3, forbidden_mechanisms: Sequence[str] = ()) -> tuple[RepairHypothesis, ...]:
    def mechanism_for(cut: CausalCutCandidate, index: int) -> str:
        text = " ".join(cut.source_spans).casefold()
        if cut.branch_ids:
            if re.search(r"none|empty|len\(|\bnot\b", cut.predicate or text):
                return "handle_empty_input_at_guard"
            return "repair_predicate_boundary"
        if cut.value_flow_ids:
            if re.search(r"raise|exception|error", text):
                return "preserve_expected_exception_protocol"
            return "repair_return_value_data_flow" if "return" in text else "normalize_value_before_consumer"
        if re.search(r"notimplemented|reverse|operand", text):
            return "preserve_NotImplemented_for_reverse_operand"
        if re.search(r"none|truth|empty|len\(", text):
            return "normalize_input_representation_before_consumers"
        if re.search(r"raise|exception|error", text):
            return "preserve_expected_exception_protocol"
        if re.search(r"return|yield", text):
            return "repair_return_value_data_flow"
        return "repair_bound_symbol_contract"
    result: list[RepairHypothesis] = []
    used: set[str] = set()
    for index, cut in enumerate(causal_cuts):
        mechanism = mechanism_for(cut, index)
        if mechanism in forbidden_mechanisms or mechanism in used:
            continue
        used.add(mechanism)
        result.append(RepairHypothesis(
            hypothesis_id=stable_id("repair-hypothesis", parent.checkpoint_id, cut.failure_locus_id or failure_id, cut.cut_id, mechanism),
            parent_checkpoint_id=parent.checkpoint_id, requirement_id=requirement_id,
            failure_id=cut.failure_locus_id or failure_id, causal_cut_ids=(cut.cut_id,),
            proposed_mechanism=mechanism,
            expected_path_change=(
                f"Change the outcome of predicate {cut.predicate!r} on the failing partition; "
                f"previous observed outcomes={cut.observed_outcomes}. Preserve adjacent partitions."
                if cut.branch_ids else
                f"Change values along {cut.value_flow_ids or cut.symbol_ids} before their return/consumer; "
                "keep the target entry and locked successful paths executable."
            ),
            expected_observation_change=f"Close the executable observation obligation for {requirement_id}, without bypassing its return/exception contract.",
            forbidden_regressions=("locked target", "trusted preservation"),
            graph_evidence_ids=tuple(cut.symbol_ids + cut.branch_ids + cut.value_flow_ids),
            distinguishing_inputs=predicate_input_recipes(cut.predicate) if cut.predicate else (),
            falsification_conditions=(
                "The bound target is no longer entered: apparent PASS is a bypass, not support.",
                "The predicted branch/value-flow change is absent and the trusted observation is unchanged.",
                "A locked target or authority-backed preservation obligation stably regresses.",
            ),
        ))
        if len(result) >= max(1, limit): break
    return tuple(result)


def record_hypothesis_feedback(graph: DynamicReachAvoidGraph, hypothesis: RepairHypothesis,
                               checkpoint_id: str, parent_results: Sequence[Any],
                               trial_results: Sequence[Any]) -> dict[str, Any]:
    """Test mechanism predictions using paired executions; never use LLM scores."""
    from .mechanism_predictions import evaluate_hypothesis_predictions
    predictions = evaluate_hypothesis_predictions(graph, hypothesis, checkpoint_id, parent_results, trial_results)
    before = {item.check_id: item for item in parent_results}
    evidence: list[dict[str, Any]] = []
    for trial in trial_results:
        parent = before.get(trial.check_id)
        if parent is None:
            continue
        comparable = bool(parent.stable and trial.stable and str(parent.status) in {"PASS", "FAIL"}
                          and str(trial.status) in {"PASS", "FAIL"})
        trusted = str(getattr(trial, "authority", "")) in {"A", "B", "C"}
        evidence.append({"check_id": trial.check_id, "comparable": comparable,
            "trusted": trusted, "path_changed": any(item["kind"] in {"BRANCH_OUTCOME_CHANGE", "RETURN_SUMMARY_CHANGE"} and any(
                outcome["check_id"] == trial.check_id and outcome["status"] == "SUPPORTED"
                for outcome in item["outcomes"]) for item in predictions),
            "observation_changed": parent.semantic_signature != trial.semantic_signature,
            "closed": comparable and trusted and str(parent.status) == "FAIL" and str(trial.status) == "PASS"
                      and getattr(trial, "entered_target_code", False) is True,
            "regressed": comparable and trusted and str(parent.status) == "PASS" and str(trial.status) == "FAIL",
            "bypassed_target": getattr(parent, "entered_target_code", False) is True
                               and getattr(trial, "entered_target_code", None) is False})
    status = ("REFUTED" if any(item["regressed"] or item["bypassed_target"] for item in evidence)
              else "SUPPORTED" if any(item["closed"] and item["path_changed"] for item in evidence)
              else "OBSERVATION_PROGRESS_WITHOUT_PATH_ATTRIBUTION" if any(item["closed"] for item in evidence)
              else "NO_OBSERVED_EFFECT" if evidence and all(item["comparable"] and not item["observation_changed"] and not item["path_changed"] for item in evidence)
              else "INCONCLUSIVE")
    feedback = {"hypothesis_id": hypothesis.hypothesis_id, "checkpoint_id": checkpoint_id,
                "status": status, "evidence": evidence,
                "prediction": hypothesis.expected_path_change}
    feedback["prediction_results"] = predictions
    node_id = stable_id("hypothesis-feedback", hypothesis.hypothesis_id, checkpoint_id)
    graph.add_node(GraphNodeKind.OBSERVATION, node_id=node_id, status=status, metadata=feedback)
    graph.add_edge(GraphEdgeKind.DERIVED_FROM, node_id, hypothesis.hypothesis_id, static_or_dynamic="dynamic")
    graph.record_update("HYPOTHESIS_FEEDBACK", **feedback)
    return feedback


def select_open_checkpoint_from_graph(graph: DynamicReachAvoidGraph, *, budget: SearchBudget | None = None) -> CheckpointState | None:
    candidates: list[CheckpointState] = []
    for node in graph.nodes.values():
        if node.kind is not GraphNodeKind.CHECKPOINT and not node.metadata.get("checkpoint_id"):
            continue
        raw = node.metadata
        if str(raw.get("status", node.status)).upper() in {"REJECTED", "EXHAUSTED", "EXPANDED", "CERTIFIED"}:
            continue
        try:
            candidates.append(CheckpointState(**{key: raw[key] for key in CheckpointState.__dataclass_fields__ if key in raw}))
        except (TypeError, KeyError):
            continue
    if not candidates: return None
    if budget is not None:
        candidates = [item for item in candidates if item.depth < budget.max_depth]
    def expansion_key(item: CheckpointState) -> tuple[Any, ...]:
        untried = sum(node.kind is GraphNodeKind.REPAIR_HYPOTHESIS
                      and node.metadata.get("parent_checkpoint_id") == item.checkpoint_id
                      and node.node_id not in item.expanded_hypothesis_ids
                      for node in graph.nodes.values())
        # Descendant quality changes expansion potential, never the node's own
        # final quality. A modest ancestor with untried alternatives can win.
        potential = max(tuple(item.search_score), tuple(item.best_descendant_score))
        repair_locked_progress = bool(item.locked_successes) and any(
            result.get("stable") and str(result.get("status")) == "FAIL"
            for result in item.preservation_results.values())
        return (repair_locked_progress, potential, bool(untried), tuple(item.search_score), -item.visit_count, -item.depth, item.checkpoint_id)
    candidates.sort(key=expansion_key, reverse=True)
    return candidates[0] if candidates else None


def select_frontier_action_from_graph(graph: DynamicReachAvoidGraph, checkpoint_id: str,
                                       *, oracle_gaps: Sequence[Any] = ()) -> dict[str, Any]:
    """Deterministic expansion/evaluation choice from the evaluated checkpoint."""
    checkpoint = graph.nodes[checkpoint_id].metadata
    targets = checkpoint.get("target_results", {})
    preservation = checkpoint.get("preservation_results", {})
    challenges = checkpoint.get("challenge_results", {})
    def failed(results: dict[str, Any]) -> list[str]:
        return sorted(key for key, value in results.items() if value.get("stable") and str(value.get("status")) == "FAIL")
    target_failed, preservation_failed, challenge_failed = failed(targets), failed(preservation), failed(challenges)
    locks = set(checkpoint.get("locked_successes", ()))
    progress = any(value.get("stable") and str(value.get("status")) == "PASS" for value in targets.values())
    check_id = ""
    if checkpoint.get("mechanical_blockers"):
        kind, reason = "FIX_MECHANICAL", "Named mechanical blockers precede semantic search."
    elif locks.intersection(target_failed):
        kind, reason, check_id = "FIX_REGRESSION_PRESERVE_TARGET", "A locked target regressed.", sorted(locks.intersection(target_failed))[0]
    elif preservation_failed and (progress or locks.intersection(preservation_failed)):
        kind, reason, check_id = "FIX_REGRESSION_PRESERVE_TARGET", "Preserve measured successes while repairing the regression.", preservation_failed[0]
    elif target_failed:
        kind, reason, check_id = "FIX_TARGET", "Stable target failure selects a causal hypothesis.", target_failed[0]
    elif oracle_gaps or not targets:
        kind, reason = "RECOVER_EVIDENCE", "Executable target or return-oracle evidence is insufficient."
    elif challenge_failed:
        kind, reason, check_id = "FIX_CHALLENGE", "An executable counterexample remains open.", challenge_failed[0]
    elif any(not value.get("stable") or str(value.get("status")) != "PASS" for value in (*targets.values(), *preservation.values(), *challenges.values())):
        kind, reason = "RECOVER_EVIDENCE", "Unknown/blocked evidence cannot justify rollback or certification."
    else:
        kind, reason = "VERIFY_REACH", "Complete the independent mechanical, oracle and lock certification gates."
    action = {"kind": kind, "reason": reason, "check_id": check_id, "checkpoint_id": checkpoint_id}
    graph.record_update("FRONTIER_ACTION", **action)
    return action


def create_trial_checkpoint(parent: CheckpointState, *, full_base_diff: str, patch_hash: str, hypothesis: RepairHypothesis, status: str = "OPEN", search_score: Sequence[Any] = ()) -> CheckpointState:
    return CheckpointState(
        checkpoint_id=stable_id("checkpoint", parent.checkpoint_id, hypothesis.hypothesis_id, patch_hash),
        parent_checkpoint_id=parent.checkpoint_id,
        base_to_current_diff=full_base_diff, patch_hash=patch_hash,
        hypothesis_id=hypothesis.hypothesis_id,
        causal_cut_ids=hypothesis.causal_cut_ids,
        depth=parent.depth + 1, status=status,
        search_score=tuple(search_score),
    )


def select_best_evaluated_checkpoint(graph: DynamicReachAvoidGraph) -> CheckpointState | None:
    values: list[CheckpointState] = []
    for node in graph.nodes.values():
        if node.kind is not GraphNodeKind.CHECKPOINT:
            continue
        raw = node.metadata
        try:
            values.append(CheckpointState(**{key: raw[key] for key in CheckpointState.__dataclass_fields__ if key in raw}))
        except (KeyError, TypeError):
            continue
    eligible = [item for item in values if item.status not in {"REJECTED", "REJECT_TRIAL"}
                and item.observation_ids and not item.mechanical_blockers
                and not any(str(result.get("status")) == "FAIL" and result.get("stable")
                            and result.get("authority") in {"A", "B", "C"}
                            for result in item.preservation_results.values())]
    return max(eligible, key=lambda item: (item.certified, tuple(item.search_score), -item.depth, item.checkpoint_id), default=None)


def backtrack_checkpoint(graph: DynamicReachAvoidGraph, child: CheckpointState) -> CheckpointState | None:
    parent_id = child.parent_checkpoint_id
    if not parent_id:
        return None
    parent_node = graph.nodes.get(parent_id)
    if parent_node is None:
        return None
    graph.record_transition(parent_id, child.checkpoint_id, "REJECT_TRIAL")
    raw = parent_node.metadata
    try:
        return CheckpointState(**{key: raw[key] for key in CheckpointState.__dataclass_fields__ if key in raw})
    except (KeyError, TypeError):
        return None
