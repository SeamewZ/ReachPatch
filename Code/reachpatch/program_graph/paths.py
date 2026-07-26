from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from reachpatch.models.base import stable_id
from reachpatch.program_graph.models import PathClass, ProgramGraph
from reachpatch.requirement_graph.domains import infer_domains, solve_constraints
from reachpatch.requirement_graph.models import QuantifiedVariable

PATH_RELATIONS = {
    "control_flow",
    "calls",
    "may_call",
    "parameter_flow",
    "return_flow",
    "exception_flow",
    "raises",
    "catches",
    "dispatch",
    "protocol_selected",
    "protocol_candidate",
    "state_read",
    "state_write",
    "field_flow",
    "data_flow",
    "def_use",
    "triggers",
    "observes",
}


@dataclass(frozen=True, slots=True)
class PathEnumeration:
    path_classes: tuple[PathClass, ...]
    capped: bool
    explored_states: int


@dataclass(frozen=True, slots=True)
class PathTopology:
    scc_by_node: dict[str, int]
    cyclic_sccs: frozenset[int]


@dataclass(frozen=True, slots=True)
class _PathLink:
    node_id: str
    incoming_edge_id: str | None
    parent: "_PathLink | None"


def _materialize_path(link: _PathLink) -> tuple[tuple[str, ...], tuple[str, ...]]:
    reverse_nodes: list[str] = []
    reverse_edges: list[str] = []
    cursor: _PathLink | None = link
    while cursor is not None:
        reverse_nodes.append(cursor.node_id)
        if cursor.incoming_edge_id is not None:
            reverse_edges.append(cursor.incoming_edge_id)
        cursor = cursor.parent
    reverse_nodes.reverse()
    reverse_edges.reverse()
    return tuple(reverse_nodes), tuple(reverse_edges)


def summarize_path_topology(graph: ProgramGraph) -> PathTopology:
    cached = graph._path_topology_cache
    if isinstance(cached, PathTopology):
        return cached
    sccs = graph.strongly_connected_components(
        edge_predicate=lambda edge: edge.kind in PATH_RELATIONS
    )
    scc_by_node = {
        node_id: index for index, component in enumerate(sccs) for node_id in component
    }
    cyclic_sccs = frozenset(
        index
        for index, component in enumerate(sccs)
        if len(component) > 1
        or any(
            target in component
            for source in component
            for edge in graph.outgoing(source)
            for target in edge.target_ids
            if edge.kind in PATH_RELATIONS
        )
    )
    topology = PathTopology(scc_by_node=scc_by_node, cyclic_sccs=cyclic_sccs)
    graph._path_topology_cache = topology
    return topology


def _guard_variables(guards: Iterable[str]) -> tuple[str, ...]:
    names: set[str] = set()
    for guard in guards:
        try:
            tree = ast.parse(guard, mode="eval")
        except SyntaxError:
            continue
        names.update(
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id not in {"len", "bool", "isinstance", "True", "False", "None", "iterable"}
        )
    return tuple(sorted(names))


@lru_cache(maxsize=8192)
def _guard_feasibility_cached(
    normalized: tuple[str, ...],
) -> tuple[bool, dict[str, object]]:
    positives = {re.sub(r"^not\s*\((.*)\)$", r"\1", item).strip() for item in normalized if not item.startswith("not (")}
    negatives = {re.sub(r"^not\s*\((.*)\)$", r"\1", item).strip() for item in normalized if item.startswith("not (")}
    contradiction = positives & negatives
    if contradiction:
        return False, {"reason": "syntactic_contradiction", "predicates": sorted(contradiction)}
    variables = _guard_variables(normalized)
    if not variables:
        return True, {"reason": "no_symbolic_variables"}
    formula = " ".join(normalized)
    domains = infer_domains(formula, variables)
    by_name = {domain.variable: domain for domain in domains}
    quantified = tuple(
        QuantifiedVariable(name, by_name[name].domain_id, by_name[name].type_names, formula)
        for name in variables
    )
    result = solve_constraints(quantified, domains, normalized, max_combinations=4096)
    proof = result.to_dict()
    if not result.satisfiable and (
        not result.complete
        or result.reason.startswith(("unsupported_constraint", "missing_domain"))
    ):
        # The finite witness enumerator cannot establish UNSAT for an
        # open-world domain.  Treat the edge as conservatively feasible and
        # carry the unresolved proof so the requirement compiler can retain a
        # named frontier rather than silently discharging the edge.
        proof["status"] = "UNKNOWN_OPEN_WORLD"
        proof["resolution"] = "increase symbolic coverage or provide a closed domain"
        return True, proof
    return result.satisfiable, proof


def guard_feasibility(guards: Iterable[str]) -> tuple[bool, dict[str, object]]:
    normalized = tuple(
        guard
        for guard in guards
        if guard and guard not in {"True", "entry", "normal", "loop_back"}
        and not guard.startswith("iterable(")
        and not guard.startswith("raises(")
    )
    return _guard_feasibility_cached(normalized)


def summarize_path_classes(
    graph: ProgramGraph,
    entrypoint_id: str,
    observation_ids: Iterable[str],
    *,
    max_paths: int = 256,
    max_states: int | None = None,
    topology: PathTopology | None = None,
) -> PathEnumeration:
    observations = set(observation_ids)
    if entrypoint_id not in graph.nodes:
        raise KeyError(entrypoint_id)
    if not observations:
        raise ValueError("path classification requires at least one observation")
    topology = topology or summarize_path_topology(graph)
    scc_by_node = topology.scc_by_node
    cyclic_sccs = topology.cyclic_sccs
    stack: list[tuple[_PathLink, tuple[str, ...], dict[int, int]]] = [
        (_PathLink(entrypoint_id, None, None), (), {})
    ]
    emitted: dict[tuple[object, ...], PathClass] = {}
    explored = 0
    capped = False
    # Cover at least one state per graph node by default. This preserves a
    # finite cap for condition/branch explosion while allowing an arbitrarily
    # long acyclic corridor to reach its observation.
    state_budget = max_states if max_states is not None else max(20000, len(graph.nodes))
    while stack:
        path_link, guards, scc_counts = stack.pop()
        node_id = path_link.node_id
        explored += 1
        if explored > state_budget or len(emitted) >= max_paths:
            capped = True
            break
        if node_id in observations:
            node_path, edge_path = _materialize_path(path_link)
            feasible, proof = guard_feasibility(guards)
            protocol_selections = tuple(sorted(
                edge_id
                for edge_id in edge_path
                if graph.edges[edge_id].kind in {"protocol_selected", "protocol_candidate", "dispatch"}
            ))
            state_effects = tuple(sorted(
                edge_id
                for edge_id in edge_path
                if graph.edges[edge_id].kind in {"state_read", "state_write", "external_effect"}
            ))
            exit_kind = (
                "exception"
                if graph.nodes[node_id].kind == "exception"
                or any(graph.edges[edge_id].kind in {"exception_flow", "raises"} for edge_id in edge_path)
                else "normal"
            )
            loop_summaries = tuple(sorted(
                f"scc:{index}:{'many' if count > 1 else 'one'}"
                for index, count in scc_counts.items()
                if index in cyclic_sccs and count > 0
            ))
            key = (
                entrypoint_id,
                tuple(sorted(set(guards))),
                protocol_selections,
                exit_kind,
                node_id,
                state_effects,
                loop_summaries,
            )
            path_id = stable_id("path-class", key)
            emitted[key] = PathClass(
                path_class_id=path_id,
                entrypoint_id=entrypoint_id,
                node_ids=node_path,
                edge_ids=edge_path,
                critical_predicates=tuple(guards),
                protocol_selections=protocol_selections,
                exit_kind=exit_kind,
                observation_ids=(node_id,),
                state_effect_ids=state_effects,
                loop_summaries=loop_summaries,
                accumulated_guard=" and ".join(f"({guard})" for guard in guards) if guards else "True",
                feasible=feasible,
                proof=proof,
            )
            continue
        for edge in reversed(graph.outgoing(node_id)):
            if edge.kind not in PATH_RELATIONS:
                continue
            next_guards = guards
            if edge.condition not in {"", "True", "entry", "normal", "loop_back"}:
                next_guards = guards + (edge.condition,)
            feasible, _ = guard_feasibility(next_guards)
            if not feasible:
                continue
            for target in reversed(edge.target_ids):
                scc_index = scc_by_node.get(target, -1)
                next_counts = scc_counts
                if scc_index in cyclic_sccs:
                    next_count = scc_counts.get(scc_index, 0) + 1
                    if next_count > 2:
                        continue
                    next_counts = dict(scc_counts)
                    next_counts[scc_index] = next_count
                stack.append((
                    _PathLink(target, edge.edge_id, path_link),
                    next_guards,
                    next_counts,
                ))
    return PathEnumeration(
        path_classes=tuple(sorted(emitted.values(), key=lambda path: path.path_class_id)),
        capped=capped,
        explored_states=explored,
    )
