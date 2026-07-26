from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from reachpatch.models.base import SerializableRecord, content_hash, stable_id
from reachpatch.models.core import Frontier
from reachpatch.models.graph import GraphEdge, GraphNode, TypedMultiGraph


PROGRAM_RELATIONS = {
    "containment",
    "defines",
    "imports",
    "exports",
    "calls",
    "may_call",
    "control_flow",
    "control_dependency",
    "def_use",
    "data_flow",
    "alias",
    "field_flow",
    "parameter_flow",
    "return_flow",
    "exception_flow",
    "raises",
    "catches",
    "inheritance",
    "override",
    "dispatch",
    "descriptor",
    "property",
    "protocol_candidate",
    "protocol_selected",
    "protocol_fallback",
    "protocol_infeasible",
    "reflection",
    "dynamic_lookup",
    "state_read",
    "state_write",
    "external_effect",
    "test_coverage",
    "observed_execution",
    "registers",
    "configures",
    "serializes",
    "wraps",
    "decorates",
    "triggers",
    "observes",
}


@dataclass(frozen=True, slots=True)
class ProtocolOperation(SerializableRecord):
    operation_id: str
    kind: str
    source_node_id: str
    left_expression: str | None
    right_expression: str | None
    candidate_method_names: tuple[str, ...]
    candidate_target_ids: tuple[str, ...]
    selected_target_id: str | None
    fallback_order: tuple[str, ...]
    status: str
    conditions: tuple[str, ...]
    not_implemented_fallback: bool


@dataclass(frozen=True, slots=True)
class CFGRecord(SerializableRecord):
    callable_id: str
    entry_node_id: str
    exit_node_ids: tuple[str, ...]
    statement_node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PathClass(SerializableRecord):
    path_class_id: str
    entrypoint_id: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    critical_predicates: tuple[str, ...]
    protocol_selections: tuple[str, ...]
    exit_kind: str
    observation_ids: tuple[str, ...]
    state_effect_ids: tuple[str, ...]
    loop_summaries: tuple[str, ...]
    accumulated_guard: str
    feasible: bool
    proof: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EntrypointPath(SerializableRecord):
    entrypoint_id: str
    trigger_id: str
    seed_id: str
    observation_id: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    path_condition: str
    bypass_path_ids: tuple[str, ...]
    confidence: float
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntrypointResult(SerializableRecord):
    paths: tuple[EntrypointPath, ...]
    path_classes: tuple[PathClass, ...]
    frontier_ids: tuple[str, ...]
    excluded_infeasible_seeds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CausalRepairCut(SerializableRecord):
    cut_id: str
    node_ids: tuple[str, ...]
    ranked_nodes: tuple[dict[str, Any], ...]
    insertion_boundary_ids: tuple[str, ...]
    covered_unit_ids: tuple[str, ...]
    excluded_node_ids: tuple[str, ...]
    impact_cone_node_ids: tuple[str, ...]
    proof: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ImpactCone(SerializableRecord):
    cone_id: str
    source_node_ids: tuple[str, ...]
    downstream_node_ids: tuple[str, ...]
    caller_ids: tuple[str, ...]
    consumer_ids: tuple[str, ...]
    exception_handler_ids: tuple[str, ...]
    serialization_ids: tuple[str, ...]
    preservation_test_ids: tuple[str, ...]
    affected_edge_ids: tuple[str, ...]
    frontier_ids: tuple[str, ...]


class ProgramGraph(TypedMultiGraph):
    _SUFFIX_INDEX_KINDS = frozenset({
        "module",
        "class",
        "function",
        "method",
        "property",
        "parameter",
        "local",
        "field",
        "abstract_object",
        "external_interface",
    })

    def __init__(self, *, repository_root: str, source_hash: str, version: int = 1) -> None:
        super().__init__(graph_kind="program_interaction", version=version)
        self.repository_root = repository_root
        self.source_hash = source_hash
        self.symbol_index: dict[str, list[str]] = {}
        # Index only the final qualified-name component.  Full suffix
        # materialization stores the same node once for every dotted prefix
        # and becomes dominant on repositories with many locals.  Filtering
        # this much smaller candidate bucket preserves exact suffix lookup
        # semantics while keeping index growth linear in indexed nodes.
        self._symbol_tail_index: dict[str, set[str]] = {}
        self._symbol_resolution_cache: dict[str, tuple[str, ...]] = {}
        self._symbol_resolution_keys_by_tail: dict[str, set[str]] = {}
        self._interned_target_tuples: dict[tuple[str, ...], tuple[str, ...]] = {}
        self.file_index: dict[str, list[str]] = {}
        self.cfgs: dict[str, CFGRecord] = {}
        self.protocol_operations: dict[str, ProtocolOperation] = {}
        self.path_classes: dict[str, PathClass] = {}
        self.frontiers: dict[str, Frontier] = {}
        self.external_surface_ids: set[str] = set()
        self.test_node_ids: set[str] = set()
        self.observation_node_ids: set[str] = set()
        self._program_hash_cache: str | None = None
        self._path_topology_cache: Any | None = None
        self._node_hashes_cache: tuple[str, ...] | None = None
        self._edge_hashes_cache: tuple[str, ...] | None = None
        self._cfg_hashes_cache: tuple[str, ...] | None = None
        self._protocol_hashes_cache: tuple[str, ...] | None = None
        self._path_class_hashes_cache: tuple[str, ...] | None = None
        self._frontier_hashes_cache: tuple[str, ...] | None = None
        # Performance telemetry is deliberately excluded from serialization
        # and semantic hashes.
        self.build_timings: dict[str, float] = {}
        self.build_stats: dict[str, int] = {}

    def invalidate_hash(self) -> None:
        self._program_hash_cache = None

    def add_node(self, node: GraphNode) -> GraphNode:
        self.invalidate_hash()
        self._path_topology_cache = None
        self._node_hashes_cache = None
        return super().add_node(node)

    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        self.invalidate_hash()
        self._path_topology_cache = None
        self._edge_hashes_cache = None
        return super().add_edge(edge)

    def remove_edge(self, edge_id: str) -> GraphEdge:
        self.invalidate_hash()
        self._path_topology_cache = None
        self._edge_hashes_cache = None
        return super().remove_edge(edge_id)

    def remove_node(self, node_id: str) -> GraphNode:
        self.invalidate_hash()
        self._path_topology_cache = None
        self._node_hashes_cache = None
        self._edge_hashes_cache = None
        return super().remove_node(node_id)

    def update_node_attributes(self, node_id: str, attributes: dict[str, Any]) -> GraphNode:
        previous = self.nodes[node_id]
        updated = GraphNode(
            node_id=previous.node_id,
            kind=previous.kind,
            label=previous.label,
            attributes=dict(attributes),
            provenance_ids=previous.provenance_ids,
        )
        self.nodes[node_id] = updated
        self.invalidate_hash()
        self._node_hashes_cache = None
        return updated

    def add_cfg(self, cfg: CFGRecord) -> None:
        previous = self.cfgs.get(cfg.callable_id)
        self.cfgs[cfg.callable_id] = cfg
        if previous != cfg:
            self.invalidate_hash()
            self._cfg_hashes_cache = None

    def add_protocol_operation(self, operation: ProtocolOperation) -> None:
        previous = self.protocol_operations.get(operation.operation_id)
        self.protocol_operations[operation.operation_id] = operation
        if previous != operation:
            self.invalidate_hash()
            self._protocol_hashes_cache = None

    def index_node(self, node: GraphNode) -> GraphNode:
        is_new = node.node_id not in self.nodes
        self.add_node(node)
        if not is_new:
            return node
        qualified_name = node.attributes.get("qualified_name")
        if qualified_name:
            qualified = str(qualified_name)
            self.symbol_index.setdefault(qualified, []).append(node.node_id)
            if node.kind in self._SUFFIX_INDEX_KINDS:
                tail = qualified.rsplit(".", 1)[-1]
                self._symbol_tail_index.setdefault(tail, set()).add(node.node_id)
                for cached_name in self._symbol_resolution_keys_by_tail.pop(tail, ()):
                    self._symbol_resolution_cache.pop(cached_name, None)
        file_name = node.attributes.get("file")
        if file_name:
            self.file_index.setdefault(str(file_name), []).append(node.node_id)
        if node.kind in {"test", "assertion", "fixture"}:
            self.test_node_ids.add(node.node_id)
        if node.kind in {"observation_point", "return", "exception", "external_effect"}:
            self.observation_node_ids.add(node.node_id)
        if node.attributes.get("externally_controllable"):
            self.external_surface_ids.add(node.node_id)
        return node

    def add_relation(
        self,
        kind: str,
        sources: Iterable[str],
        targets: Iterable[str],
        *,
        condition: str = "True",
        confidence: float = 1.0,
        attributes: dict[str, Any] | None = None,
        provenance_ids: Iterable[str] = (),
    ) -> GraphEdge:
        if kind not in PROGRAM_RELATIONS and not kind.startswith("custom:"):
            raise ValueError(f"unregistered program relation: {kind}")
        return self.add_edge(GraphEdge.create(
            kind,
            sources,
            targets,
            condition=condition,
            confidence=confidence,
            attributes=attributes,
            provenance_ids=provenance_ids,
        ))

    def add_frontier(self, frontier: Frontier) -> None:
        if self.frontiers.get(frontier.frontier_id) != frontier:
            self.invalidate_hash()
            self._frontier_hashes_cache = None
        self.frontiers[frontier.frontier_id] = frontier

    def add_path_class(self, path_class: PathClass) -> None:
        if self.path_classes.get(path_class.path_class_id) != path_class:
            self.invalidate_hash()
            self._path_class_hashes_cache = None
        self.path_classes[path_class.path_class_id] = path_class

    def resolve_symbol(self, name: str) -> list[str]:
        cached = self._symbol_resolution_cache.get(name)
        if cached is not None:
            return list(cached)
        exact = list(self.symbol_index.get(name, []))
        if exact:
            resolved = tuple(sorted(set(exact)))
        else:
            tail = name.rsplit(".", 1)[-1]
            suffix = f".{name}"
            resolved = tuple(sorted(
                node_id
                for node_id in self._symbol_tail_index.get(tail, ())
                if (
                    (qualified := str(self.nodes[node_id].attributes.get("qualified_name", "")))
                    == name
                    or qualified.endswith(suffix)
                )
            ))
        self._symbol_resolution_cache[name] = resolved
        tail = name.rsplit(".", 1)[-1]
        self._symbol_resolution_keys_by_tail.setdefault(tail, set()).add(name)
        return list(resolved)

    def intern_target_ids(self, target_ids: Iterable[str]) -> tuple[str, ...]:
        """Return one immutable target tuple for every exact candidate set."""

        candidate = tuple(sorted(set(target_ids)))
        return self._interned_target_tuples.setdefault(candidate, candidate)

    def source_segment(self, node_id: str) -> str:
        node = self.nodes[node_id]
        attributes = node.attributes
        relative_path = str(attributes.get("file", ""))
        if not relative_path or relative_path.startswith("<"):
            return ""
        root = Path(self.repository_root).resolve()
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return ""
        try:
            lines = path.read_bytes().splitlines(keepends=True)
            start_line = int(attributes["line"]) - 1
            end_line = int(attributes.get("end_line", start_line + 1)) - 1
            start_column = int(attributes.get("column", 0))
            end_column = int(attributes.get("end_column", start_column))
            if start_line < 0 or end_line < start_line or end_line >= len(lines):
                return ""
            if start_line == end_line:
                payload = lines[start_line][start_column:end_column]
            else:
                payload = b"".join((
                    lines[start_line][start_column:],
                    *lines[start_line + 1:end_line],
                    lines[end_line][:end_column],
                ))
            return payload.decode("utf-8")
        except (KeyError, OSError, UnicodeDecodeError, ValueError):
            return ""

    def to_dict(self) -> dict[str, Any]:
        body = {
            "graph_kind": self.graph_kind,
            "version": self.version,
            "nodes": [self.nodes[node_id].to_dict() for node_id in sorted(self.nodes)],
            "edges": [self.edges[edge_id].to_dict() for edge_id in sorted(self.edges)],
            "repository_root": self.repository_root,
            "source_hash": self.source_hash,
            "symbol_index": {key: sorted(value) for key, value in sorted(self.symbol_index.items())},
            "file_index": {key: sorted(value) for key, value in sorted(self.file_index.items())},
            "cfgs": [self.cfgs[key].to_dict() for key in sorted(self.cfgs)],
            "protocol_operations": [
                self.protocol_operations[key].to_dict()
                for key in sorted(self.protocol_operations)
            ],
            "path_classes": [
                self.path_classes[key].to_dict() for key in sorted(self.path_classes)
            ],
            "frontiers": [self.frontiers[key].to_dict() for key in sorted(self.frontiers)],
            "external_surface_ids": sorted(self.external_surface_ids),
            "test_node_ids": sorted(self.test_node_ids),
            "observation_node_ids": sorted(self.observation_node_ids),
        }
        body["graph_hash"] = self.program_hash()
        return body

    def program_hash(self) -> str:
        if self._program_hash_cache is None:
            if self._node_hashes_cache is None:
                self._node_hashes_cache = tuple(
                    content_hash(self.nodes[node_id].to_dict())
                    for node_id in sorted(self.nodes)
                )
            if self._edge_hashes_cache is None:
                self._edge_hashes_cache = tuple(
                    content_hash(self.edges[edge_id].to_dict())
                    for edge_id in sorted(self.edges)
                )
            if self._cfg_hashes_cache is None:
                self._cfg_hashes_cache = tuple(
                    content_hash(self.cfgs[key].to_dict()) for key in sorted(self.cfgs)
                )
            if self._protocol_hashes_cache is None:
                self._protocol_hashes_cache = tuple(
                    content_hash(self.protocol_operations[key].to_dict())
                    for key in sorted(self.protocol_operations)
                )
            if self._path_class_hashes_cache is None:
                self._path_class_hashes_cache = tuple(
                    content_hash(self.path_classes[key].to_dict())
                    for key in sorted(self.path_classes)
                )
            if self._frontier_hashes_cache is None:
                self._frontier_hashes_cache = tuple(
                    content_hash(self.frontiers[key].to_dict())
                    for key in sorted(self.frontiers)
                )
            self._program_hash_cache = content_hash({
                "graph_kind": self.graph_kind,
                "version": self.version,
                "repository_root": self.repository_root,
                "source_hash": self.source_hash,
                "node_hashes": self._node_hashes_cache,
                "edge_hashes": self._edge_hashes_cache,
                "cfg_hashes": self._cfg_hashes_cache,
                "protocol_hashes": self._protocol_hashes_cache,
                "path_class_hashes": self._path_class_hashes_cache,
                "frontier_hashes": self._frontier_hashes_cache,
                "external_surface_ids": sorted(self.external_surface_ids),
                "test_node_ids": sorted(self.test_node_ids),
                "observation_node_ids": sorted(self.observation_node_ids),
            })
        return self._program_hash_cache

    def create_frontier(
        self,
        kind: str,
        owner_id: str,
        reason: str,
        action: str,
        *,
        hard: bool = True,
        evidence_ids: Iterable[str] = (),
    ) -> Frontier:
        frontier = Frontier(
            frontier_id=stable_id("frontier", kind, owner_id, reason, self.source_hash),
            kind=kind,
            owner_id=owner_id,
            reason=reason,
            resolution_action=action,
            hard=hard,
            evidence_ids=tuple(evidence_ids),
        )
        self.add_frontier(frontier)
        return frontier
