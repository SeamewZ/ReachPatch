from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from reachpatch.models.base import stable_id
from reachpatch.program_graph.models import EntrypointPath, EntrypointResult, ProgramGraph
from reachpatch.program_graph.paths import (
    PATH_RELATIONS,
    guard_feasibility,
    summarize_path_classes,
    summarize_path_topology,
)

BACKWARD_RELATIONS = {
    "calls",
    "may_call",
    "parameter_flow",
    "return_flow",
    "data_flow",
    "def_use",
    "control_flow",
    "control_dependency",
    "state_read",
    "state_write",
    "exception_flow",
    "raises",
    "catches",
    "exports",
    "alias",
    "registers",
    "dispatch",
    "configures",
    "triggers",
    "protocol_selected",
    "protocol_candidate",
}


@dataclass(frozen=True, slots=True)
class _SliceState:
    node_id: str
    path_condition: tuple[str, ...]


def _externally_controllable(node_id: str, graph: ProgramGraph) -> bool:
    node = graph.nodes[node_id]
    return node_id in graph.external_surface_ids or bool(node.attributes.get("externally_controllable"))


def _path_confidence(graph: ProgramGraph, edge_ids: Iterable[str]) -> float:
    confidence = 1.0
    for edge_id in edge_ids:
        confidence *= graph.edges[edge_id].confidence
    return confidence


def _forward_path_tree(
    graph: ProgramGraph,
    start: str,
) -> dict[str, tuple[str, str] | None]:
    queue = deque([start])
    predecessor: dict[str, tuple[str, str] | None] = {start: None}
    while queue:
        node_id = queue.popleft()
        for edge in graph.outgoing(node_id):
            if edge.kind not in PATH_RELATIONS:
                continue
            for target in edge.target_ids:
                if target in predecessor:
                    continue
                predecessor[target] = (node_id, edge.edge_id)
                queue.append(target)
    return predecessor


def _reverse_path_tree(
    graph: ProgramGraph,
    goal: str,
) -> dict[str, tuple[str, str] | None]:
    queue = deque([goal])
    successor: dict[str, tuple[str, str] | None] = {goal: None}
    while queue:
        node_id = queue.popleft()
        for edge in graph.incoming(node_id):
            if edge.kind not in PATH_RELATIONS:
                continue
            for source in edge.source_ids:
                if source in successor:
                    continue
                successor[source] = (node_id, edge.edge_id)
                queue.append(source)
    return successor


def _path_from_predecessors(
    start: str,
    goal: str,
    predecessor: dict[str, tuple[str, str] | None],
) -> tuple[list[str], list[str]] | None:
    if goal not in predecessor:
        return None
    nodes = [goal]
    edges: list[str] = []
    cursor = goal
    while cursor != start:
        previous = predecessor.get(cursor)
        if previous is None:
            return None
        cursor, edge_id = previous
        nodes.append(cursor)
        edges.append(edge_id)
    nodes.reverse()
    edges.reverse()
    return nodes, edges


def _path_to_goal(
    start: str,
    goal: str,
    successor: dict[str, tuple[str, str] | None],
) -> tuple[list[str], list[str]] | None:
    if start not in successor:
        return None
    nodes = [start]
    edges: list[str] = []
    cursor = start
    while cursor != goal:
        following = successor.get(cursor)
        if following is None:
            return None
        cursor, edge_id = following
        nodes.append(cursor)
        edges.append(edge_id)
    return nodes, edges


def _forward_observations(
    start: str,
    seed: str,
    observations: set[str],
    *,
    to_seed_successor: dict[str, tuple[str, str] | None],
    from_seed_paths: dict[str, tuple[list[str], list[str]] | None],
) -> list[tuple[str, list[str], list[str]]]:
    to_seed = _path_to_goal(start, seed, to_seed_successor)
    if to_seed is None:
        return []
    results: list[tuple[str, list[str], list[str]]] = []
    for observation in sorted(observations):
        from_seed = from_seed_paths.get(observation)
        if from_seed is None:
            continue
        nodes = to_seed[0] + from_seed[0][1:]
        edges = to_seed[1] + from_seed[1]
        results.append((observation, nodes, edges))
    return results


def recover_entrypoints(
    seeds: Iterable[str],
    graph: ProgramGraph,
    *,
    observation_ids: Iterable[str] = (),
    max_slice_states: int | None = None,
    max_paths_per_entry: int = 256,
) -> EntrypointResult:
    seeds = tuple(sorted(set(seeds)))
    missing = [seed for seed in seeds if seed not in graph.nodes]
    if missing:
        raise KeyError(f"entrypoint seeds missing from graph: {missing}")
    observations = set(observation_ids) or set(graph.observation_node_ids)
    paths: dict[str, EntrypointPath] = {}
    frontier_ids: set[str] = set()
    infeasible: set[str] = set()
    bypass_cache: dict[tuple[str, str, str], tuple[list[str], list[str]] | None] = {}

    for seed in seeds:
        to_seed_successor = _reverse_path_tree(graph, seed)
        from_seed_predecessor = _forward_path_tree(graph, seed)
        from_seed_paths = {
            observation: _path_from_predecessors(
                seed, observation, from_seed_predecessor
            )
            for observation in observations
        }
        queue = deque([_SliceState(seed, ())])
        visited: set[tuple[str, tuple[str, ...]]] = set()
        processed = 0
        # The default permits one state per Program Graph node so a long
        # acyclic dependency corridor is not mistaken for path explosion.
        # Additional path-condition variants remain bounded.
        slice_state_budget = (
            max_slice_states
            if max_slice_states is not None
            else max(20000, len(graph.nodes))
        )
        found_for_seed = False
        while queue:
            state = queue.pop()
            processed += 1
            if processed > slice_state_budget:
                frontier = graph.create_frontier(
                    "ENTRYPOINT_SLICE_CAP",
                    seed,
                    f"backward slice exceeded {slice_state_budget} states",
                    "raise slice cap or run targeted dynamic entrypoint tracing",
                    hard=True,
                )
                frontier_ids.add(frontier.frontier_id)
                break
            canonical = (state.node_id, tuple(sorted(set(state.path_condition))))
            if canonical in visited:
                continue
            visited.add(canonical)
            feasible, _ = guard_feasibility(state.path_condition)
            if not feasible:
                infeasible.add(state.node_id)
                continue
            if _externally_controllable(state.node_id, graph):
                confirmed = _forward_observations(
                    state.node_id,
                    seed,
                    observations,
                    to_seed_successor=to_seed_successor,
                    from_seed_paths=from_seed_paths,
                )
                for observation, nodes, edges in confirmed:
                    bypasses: list[str] = []
                    bypass_key = (state.node_id, seed, observation)
                    if bypass_key not in bypass_cache:
                        bypass_cache[bypass_key] = graph.shortest_path(
                            state.node_id,
                            observation,
                            edge_predicate=lambda edge: edge.kind in PATH_RELATIONS,
                            forbidden_nodes={seed},
                        )
                    direct_bypass = bypass_cache[bypass_key]
                    if direct_bypass is not None:
                        bypasses.append(stable_id(
                            "bypass", state.node_id, observation, direct_bypass[1]
                        ))
                    path_condition = " and ".join(
                        f"({condition})" for condition in state.path_condition
                    ) or "True"
                    path_id = stable_id(
                        "entry-path", state.node_id, seed, observation, edges, path_condition
                    )
                    evidence_ids = tuple(sorted({
                        provenance
                        for edge_id in edges
                        for provenance in graph.edges[edge_id].provenance_ids
                    }))
                    paths[path_id] = EntrypointPath(
                        entrypoint_id=state.node_id,
                        trigger_id=state.node_id,
                        seed_id=seed,
                        observation_id=observation,
                        node_ids=tuple(nodes),
                        edge_ids=tuple(edges),
                        path_condition=path_condition,
                        bypass_path_ids=tuple(bypasses),
                        confidence=_path_confidence(graph, edges),
                        evidence_ids=evidence_ids,
                    )
                    found_for_seed = True
                if confirmed:
                    continue
            predecessors = []
            for edge in graph.incoming(state.node_id):
                if edge.kind not in BACKWARD_RELATIONS:
                    continue
                condition = edge.condition
                next_conditions = state.path_condition
                if condition not in {"", "True", "entry", "normal", "loop_back"}:
                    next_conditions += (condition,)
                for source in edge.source_ids:
                    predecessors.append(_SliceState(
                        source,
                        next_conditions,
                    ))
            if predecessors:
                queue.extend(predecessors)
        if not found_for_seed:
            frontier = graph.create_frontier(
                "ENTRYPOINT_NOT_CONFIRMED",
                seed,
                "no controllable trigger-to-seed-to-observation path was confirmed",
                "expand aliases/dispatch/state slice or execute a targeted trace",
                hard=True,
            )
            frontier_ids.add(frontier.frontier_id)

    path_classes = {}
    by_entry: dict[str, set[str]] = {}
    for path in paths.values():
        by_entry.setdefault(path.entrypoint_id, set()).add(path.observation_id)
    topology = summarize_path_topology(graph) if by_entry else None
    for entrypoint, entry_observations in sorted(by_entry.items()):
        enumeration = summarize_path_classes(
            graph,
            entrypoint,
            entry_observations,
            max_paths=max_paths_per_entry,
            topology=topology,
        )
        for path_class in enumeration.path_classes:
            path_classes[path_class.path_class_id] = path_class
        if enumeration.capped:
            frontier = graph.create_frontier(
                "PATH_CLASS_CAP",
                entrypoint,
                f"path enumeration capped after {enumeration.explored_states} states",
                "increase path cap or add a dynamic feasibility trace",
                hard=True,
            )
            frontier_ids.add(frontier.frontier_id)
    return EntrypointResult(
        paths=tuple(sorted(paths.values(), key=lambda path: path.entrypoint_id + path.observation_id)),
        path_classes=tuple(sorted(path_classes.values(), key=lambda path: path.path_class_id)),
        frontier_ids=tuple(sorted(frontier_ids)),
        excluded_infeasible_seeds=tuple(sorted(infeasible)),
    )
