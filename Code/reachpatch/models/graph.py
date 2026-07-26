from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from .base import SerializableRecord, content_hash, stable_id


@dataclass(frozen=True, slots=True)
class GraphNode(SerializableRecord):
    node_id: str
    kind: str
    label: str
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance_ids: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        kind: str,
        label: str,
        *,
        attributes: dict[str, Any] | None = None,
        provenance_ids: Iterable[str] = (),
        identity: Any | None = None,
    ) -> "GraphNode":
        attributes = dict(attributes or {})
        node_id = stable_id("node", kind, identity if identity is not None else label, attributes)
        return cls(node_id, kind, label, attributes, tuple(provenance_ids))


@dataclass(frozen=True, slots=True)
class GraphEdge(SerializableRecord):
    edge_id: str
    kind: str
    source_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    condition: str = "True"
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance_ids: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        kind: str,
        source_ids: Iterable[str],
        target_ids: Iterable[str],
        *,
        condition: str = "True",
        confidence: float = 1.0,
        attributes: dict[str, Any] | None = None,
        provenance_ids: Iterable[str] = (),
    ) -> "GraphEdge":
        sources = tuple(source_ids)
        targets = tuple(target_ids)
        if not sources or not targets:
            raise ValueError("graph edges require at least one source and target")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("edge confidence must be in [0, 1]")
        attributes = dict(attributes or {})
        edge_id = stable_id("edge", kind, sources, targets, condition, attributes)
        return cls(
            edge_id,
            kind,
            sources,
            targets,
            condition,
            confidence,
            attributes,
            tuple(provenance_ids),
        )


@dataclass(slots=True)
class _SCCFrame:
    """Compact mutable DFS frame used by the iterative SCC traversal."""

    node_id: str
    edge_ids: tuple[str, ...]
    edge_index: int = 0
    endpoint_index: int = 0


class TypedMultiGraph(SerializableRecord):
    """Deterministic attributed directed hypergraph with traversal algorithms."""

    _ADJACENCY_CACHE_LIMIT = 8192

    def __init__(self, *, graph_kind: str, version: int = 1) -> None:
        if version < 1:
            raise ValueError("graph version must be positive")
        self.graph_kind = graph_kind
        self.version = version
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}
        self._out: dict[str, set[str]] = defaultdict(set)
        self._in: dict[str, set[str]] = defaultdict(set)
        self._out_order_cache: dict[str, tuple[str, ...]] = {}
        self._in_order_cache: dict[str, tuple[str, ...]] = {}

    @classmethod
    def _cache_ordered_edge_ids(
        cls,
        node_id: str,
        adjacency: dict[str, set[str]],
        cache: dict[str, tuple[str, ...]],
    ) -> tuple[str, ...]:
        ordered = cache.get(node_id)
        if ordered is not None:
            return ordered
        ordered = tuple(sorted(adjacency.get(node_id, ())))
        if len(cache) >= cls._ADJACENCY_CACHE_LIMIT:
            cache.pop(next(iter(cache)))
        cache[node_id] = ordered
        return ordered

    def add_node(self, node: GraphNode) -> GraphNode:
        previous = self.nodes.get(node.node_id)
        if previous is not None and previous != node:
            raise ValueError(f"node id collision: {node.node_id}")
        self.nodes[node.node_id] = node
        return node

    def add_edge(self, edge: GraphEdge) -> GraphEdge:
        missing = [
            node_id
            for node_id in edge.source_ids + edge.target_ids
            if node_id not in self.nodes
        ]
        if missing:
            raise KeyError(f"edge {edge.edge_id} references missing nodes: {missing}")
        previous = self.edges.get(edge.edge_id)
        if previous is not None and previous != edge:
            raise ValueError(f"edge id collision: {edge.edge_id}")
        self.edges[edge.edge_id] = edge
        for node_id in edge.source_ids:
            self._out[node_id].add(edge.edge_id)
            self._out_order_cache.pop(node_id, None)
        for node_id in edge.target_ids:
            self._in[node_id].add(edge.edge_id)
            self._in_order_cache.pop(node_id, None)
        return edge

    def remove_edge(self, edge_id: str) -> GraphEdge:
        edge = self.edges.pop(edge_id)
        for node_id in edge.source_ids:
            self._out[node_id].discard(edge_id)
            self._out_order_cache.pop(node_id, None)
        for node_id in edge.target_ids:
            self._in[node_id].discard(edge_id)
            self._in_order_cache.pop(node_id, None)
        return edge

    def remove_node(self, node_id: str) -> GraphNode:
        incident = set(self._out.get(node_id, ())) | set(self._in.get(node_id, ()))
        for edge_id in sorted(incident):
            self.remove_edge(edge_id)
        self._out.pop(node_id, None)
        self._in.pop(node_id, None)
        return self.nodes.pop(node_id)

    def outgoing(self, node_id: str, kinds: set[str] | None = None) -> list[GraphEdge]:
        result = [
            self.edges[edge_id]
            for edge_id in self._cache_ordered_edge_ids(
                node_id, self._out, self._out_order_cache
            )
        ]
        if kinds is not None:
            result = [edge for edge in result if edge.kind in kinds]
        return result

    def incoming(self, node_id: str, kinds: set[str] | None = None) -> list[GraphEdge]:
        result = [
            self.edges[edge_id]
            for edge_id in self._cache_ordered_edge_ids(
                node_id, self._in, self._in_order_cache
            )
        ]
        if kinds is not None:
            result = [edge for edge in result if edge.kind in kinds]
        return result

    def successors(self, node_id: str, kinds: set[str] | None = None) -> list[str]:
        return sorted({
            target
            for edge in self.outgoing(node_id, kinds)
            for target in edge.target_ids
        })

    def predecessors(self, node_id: str, kinds: set[str] | None = None) -> list[str]:
        return sorted({
            source
            for edge in self.incoming(node_id, kinds)
            for source in edge.source_ids
        })

    def reachable(
        self,
        starts: Iterable[str],
        *,
        direction: str = "forward",
        edge_predicate: Callable[[GraphEdge], bool] | None = None,
        stop: set[str] | None = None,
        max_nodes: int | None = None,
    ) -> set[str]:
        if direction not in {"forward", "backward"}:
            raise ValueError("direction must be forward or backward")
        queue = deque(sorted(set(starts)))
        visited: set[str] = set()
        while queue:
            node_id = queue.popleft()
            if node_id in visited:
                continue
            if node_id not in self.nodes:
                raise KeyError(node_id)
            visited.add(node_id)
            if max_nodes is not None and len(visited) >= max_nodes:
                break
            if stop and node_id in stop:
                continue
            edges = self.outgoing(node_id) if direction == "forward" else self.incoming(node_id)
            for edge in edges:
                if edge_predicate is not None and not edge_predicate(edge):
                    continue
                neighbors = edge.target_ids if direction == "forward" else edge.source_ids
                queue.extend(sorted(set(neighbors) - visited))
        return visited

    def shortest_path(
        self,
        start: str,
        goal: str,
        *,
        edge_predicate: Callable[[GraphEdge], bool] | None = None,
        forbidden_nodes: set[str] | None = None,
    ) -> tuple[list[str], list[str]] | None:
        forbidden = forbidden_nodes or set()
        if start in forbidden or goal in forbidden:
            return None
        queue = deque([start])
        predecessor: dict[str, tuple[str, str] | None] = {start: None}
        while queue:
            node_id = queue.popleft()
            if node_id == goal:
                break
            for edge in self.outgoing(node_id):
                if edge_predicate is not None and not edge_predicate(edge):
                    continue
                for target in edge.target_ids:
                    if target in forbidden or target in predecessor:
                        continue
                    predecessor[target] = (node_id, edge.edge_id)
                    queue.append(target)
        if goal not in predecessor:
            return None
        nodes = [goal]
        edges: list[str] = []
        cursor = goal
        while predecessor[cursor] is not None:
            previous, edge_id = predecessor[cursor]
            nodes.append(previous)
            edges.append(edge_id)
            cursor = previous
        nodes.reverse()
        edges.reverse()
        return nodes, edges

    def strongly_connected_components(
        self,
        *,
        edge_predicate: Callable[[GraphEdge], bool] | None = None,
    ) -> list[tuple[str, ...]]:
        def frame(node_id: str, *, reverse: bool) -> _SCCFrame:
            adjacency = self._in if reverse else self._out
            cache = self._in_order_cache if reverse else self._out_order_cache
            return _SCCFrame(
                node_id,
                self._cache_ordered_edge_ids(node_id, adjacency, cache),
            )

        def next_neighbor(current: _SCCFrame, *, reverse: bool) -> str | None:
            while current.edge_index < len(current.edge_ids):
                edge = self.edges[current.edge_ids[current.edge_index]]
                if edge_predicate is not None and not edge_predicate(edge):
                    current.edge_index += 1
                    current.endpoint_index = 0
                    continue
                endpoints = edge.source_ids if reverse else edge.target_ids
                if current.endpoint_index < len(endpoints):
                    neighbor = endpoints[current.endpoint_index]
                    current.endpoint_index += 1
                    return neighbor
                current.edge_index += 1
                current.endpoint_index = 0
            return None

        # Iterative Kosaraju avoids Python recursion limits on repository-scale
        # control/data-flow chains without materializing a second adjacency map.
        # Cursor frames also avoid retaining one generator and one edge list for
        # every node on a deep dependency chain.
        visited: set[str] = set()
        finish_order: list[str] = []
        for start in sorted(self.nodes):
            if start in visited:
                continue
            visited.add(start)
            stack = [frame(start, reverse=False)]
            while stack:
                current = stack[-1]
                successor = next_neighbor(current, reverse=False)
                if successor is None:
                    finish_order.append(current.node_id)
                    stack.pop()
                    continue
                if successor in visited:
                    continue
                visited.add(successor)
                stack.append(frame(successor, reverse=False))

        visited.clear()
        assigned = visited
        components: list[tuple[str, ...]] = []
        for start in reversed(finish_order):
            if start in assigned:
                continue
            assigned.add(start)
            component: list[str] = []
            stack = [frame(start, reverse=True)]
            while stack:
                current = stack[-1]
                predecessor = next_neighbor(current, reverse=True)
                if predecessor is None:
                    component.append(current.node_id)
                    stack.pop()
                    continue
                if predecessor in assigned:
                    continue
                assigned.add(predecessor)
                stack.append(frame(predecessor, reverse=True))
            components.append(tuple(sorted(component)))
        return sorted(components, key=lambda component: component[0])

    def copy(self, *, version: int | None = None) -> "TypedMultiGraph":
        duplicate = TypedMultiGraph(
            graph_kind=self.graph_kind,
            version=self.version if version is None else version,
        )
        for node in self.nodes.values():
            duplicate.add_node(node)
        for edge in self.edges.values():
            duplicate.add_edge(edge)
        return duplicate

    def to_dict(self) -> dict[str, Any]:
        body = {
            "graph_kind": self.graph_kind,
            "version": self.version,
            "nodes": [self.nodes[node_id].to_dict() for node_id in sorted(self.nodes)],
            "edges": [self.edges[edge_id].to_dict() for edge_id in sorted(self.edges)],
        }
        body["graph_hash"] = content_hash(body)
        return body

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TypedMultiGraph":
        graph = cls(graph_kind=raw["graph_kind"], version=int(raw["version"]))
        for item in raw.get("nodes", []):
            graph.add_node(GraphNode(
                node_id=item["node_id"],
                kind=item["kind"],
                label=item["label"],
                attributes=dict(item.get("attributes", {})),
                provenance_ids=tuple(item.get("provenance_ids", [])),
            ))
        for item in raw.get("edges", []):
            graph.add_edge(GraphEdge(
                edge_id=item["edge_id"],
                kind=item["kind"],
                source_ids=tuple(item["source_ids"]),
                target_ids=tuple(item["target_ids"]),
                condition=item.get("condition", "True"),
                confidence=float(item.get("confidence", 1.0)),
                attributes=dict(item.get("attributes", {})),
                provenance_ids=tuple(item.get("provenance_ids", [])),
            ))
        expected = raw.get("graph_hash")
        actual = graph.to_dict()["graph_hash"]
        if expected is not None and expected != actual:
            raise ValueError(f"graph hash mismatch: expected {expected}, got {actual}")
        return graph

    def __iter__(self) -> Iterator[GraphNode]:
        for node_id in sorted(self.nodes):
            yield self.nodes[node_id]
