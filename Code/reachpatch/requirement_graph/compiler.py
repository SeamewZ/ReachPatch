from __future__ import annotations

import ast
import re
import time
from collections import defaultdict
from dataclasses import replace
from typing import Any, Callable, Iterable

from reachpatch.evidence.hypotheses import HypothesisAssignment, HypothesisSet
from reachpatch.evidence.semantic_graph import SemanticGraph
from reachpatch.models.base import stable_id
from reachpatch.models.core import Frontier
from reachpatch.models.enums import Authority, LedgerStatus, RequirementAuthorityClass, SemanticNodeKind
from reachpatch.models.graph import GraphNode
from reachpatch.program_graph.entrypoints import recover_entrypoints
from reachpatch.program_graph.models import EntrypointPath, EntrypointResult, PathClass, ProgramGraph
from reachpatch.program_graph.index import RepositoryIndex
from reachpatch.execution.reconcile import ActualDiff
from dataclasses import dataclass
from reachpatch.program_graph.paths import PATH_RELATIONS, guard_feasibility
from reachpatch.requirement_graph.domains import (
    infer_domains,
    promote_program_predicates,
    solve_constraints,
    symbolic_scenario_partitions,
)
from reachpatch.requirement_graph.models import (
    DomainPartition,
    PathEdgeLedgerRecord,
    QuantifiedVariable,
    RequirementGraph,
    RequirementLeaf,
    RequirementPathObligation,
    requirement_hyperedge,
)

_ENTRY_TOKEN = re.compile(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`|\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\b")
_CALL = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(([^)]*)\)")
_PRECONDITION = re.compile(r"\b(?:if|when|unless|provided that)\s+(.+?)(?:,|\bthen\b)", re.IGNORECASE)
_CONJUNCTION = re.compile(r"\s+(?:and|but also|as well as)\s+|\s*;\s*", re.IGNORECASE)


def _decompose_formula(formula: str) -> list[str]:
    pieces = [piece.strip() for piece in _CONJUNCTION.split(formula) if piece.strip()]
    return pieces or [formula.strip()]


def _variable_names(formula: str, *, concrete: bool = False) -> tuple[str, ...]:
    if concrete:
        return ()
    names: set[str] = set()
    for match in _CALL.finditer(formula):
        arguments = match.group(2)
        for token in re.findall(r"\b[A-Za-z_]\w*\b", arguments):
            if token.lower() not in {"none", "true", "false", "int", "str", "list", "dict"}:
                names.add(token)
    for match in re.finditer(r"\b(?:for all|every|any)\s+([A-Za-z_]\w*)", formula, re.IGNORECASE):
        names.add(match.group(1))
    return tuple(sorted(names))


def _entrypoint_hypotheses(formula: str) -> tuple[str, ...]:
    names = {
        first or second
        for first, second in _ENTRY_TOKEN.findall(formula)
        if first or second
    }
    names.update(match.group(1) for match in _CALL.finditer(formula))
    most_specific = {
        name for name in names
        if not any(other.startswith(name + ".") for other in names)
    }
    return tuple(sorted(most_specific))


def _trigger(formula: str) -> str:
    call = _CALL.search(formula)
    if call:
        return call.group(0)
    lowered = formula.lower()
    for operation in ("iterate", "compare", "add", "index", "contain", "enter", "exit", "call"):
        if operation in lowered:
            return operation
    return "public_invocation"


def _precondition(formula: str, variables: tuple[str, ...]) -> str:
    match = _PRECONDITION.search(formula)
    if match:
        candidate = match.group(1).strip()
        try:
            ast.parse(candidate, mode="eval")
            return candidate
        except SyntaxError:
            return "True"
    lowered = formula.lower()
    for name in variables:
        if f"{name} is none" in lowered:
            return f"{name} is None"
        if f"{name} is not none" in lowered:
            return f"{name} is not None"
    return "True"


def _relation(formula: str) -> dict[str, Any]:
    lowered = formula.lower()
    if "raise" in lowered or "exception" in lowered or "error" in lowered:
        kind = "exception"
    elif any(word in lowered for word in ("unchanged", "preserve", "same as")):
        kind = "preservation"
    elif any(word in lowered for word in ("before", "after", "order", "eventually")):
        kind = "temporal"
    elif any(word in lowered for word in ("idempotent", "twice", "repeat")):
        kind = "metamorphic"
    elif any(token in formula for token in ("==", "equal", "return", "yield")):
        kind = "equality"
    else:
        kind = "trace_predicate"
    return {"kind": kind, "formula": formula, "arity": 2 if kind in {"preservation", "metamorphic"} else 1}


def _observation_contract(formula: str, relation: dict[str, Any]) -> dict[str, Any]:
    lowered = formula.lower()
    channels: list[str] = []
    if any(word in lowered for word in ("return", "yield", "result", "equal")):
        channels.append("return")
    if any(word in lowered for word in ("raise", "exception", "error")):
        channels.append("exception")
    if any(word in lowered for word in ("state", "mutate", "unchanged", "field")):
        channels.append("state")
    if any(word in lowered for word in ("stdout", "stderr", "print", "output")):
        channels.append("output")
    if any(word in lowered for word in ("call", "dispatch", "protocol")):
        channels.append("calls")
    if any(word in lowered for word in ("file", "network", "database", "side effect")):
        channels.append("effects")
    if not channels:
        channels.append("return")
    return {
        "contract_id": stable_id("observation", formula, channels),
        "channels": sorted(set(channels)),
        "normalization": "structural",
        "relation_kind": relation["kind"],
    }


def _contracts(formula: str, relation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    lowered = formula.lower()
    exception = {
        "required": relation["kind"] == "exception",
        "type": next(iter(re.findall(r"\b([A-Z][A-Za-z]+(?:Error|Exception))\b", formula)), None),
        "message_category": "declared" if "message" in lowered else None,
        "phase": "stimulus",
    }
    state = {
        "must_change": "state" in lowered and "unchanged" not in lowered,
        "must_preserve": any(word in lowered for word in ("unchanged", "must not mutate", "preserve state")),
        "fields": sorted(set(re.findall(r"\bself\.([A-Za-z_]\w*)", formula))),
    }
    preservation = {
        "required": relation["kind"] == "preservation" or any(
            word in lowered for word in ("preserve", "unchanged", "still pass", "must not break")
        ),
        "formula": formula if relation["kind"] == "preservation" else "baseline public behavior",
    }
    return exception, state, preservation


def _leaf_from_claim(
    graph: SemanticGraph,
    claim_id: str,
    formula: str,
    *,
    objective_id: str,
    hypothesis: bool,
) -> RequirementLeaf:
    claim = graph.claims[claim_id]
    variable_names = _variable_names(
        formula,
        concrete=claim.payload.get("authority_rule") == "visible_public_assertion",
    )
    domains = infer_domains(formula, variable_names)
    domain_by_variable = {domain.variable: domain for domain in domains}
    variables = tuple(
        QuantifiedVariable(
            name=name,
            domain_id=domain_by_variable[name].domain_id,
            type_hints=domain_by_variable[name].type_names,
            source_expression=formula,
        )
        for name in variable_names
    )
    relation = _relation(formula)
    observation = _observation_contract(formula, relation)
    exception, state, preservation = _contracts(formula, relation)
    if claim.kind == SemanticNodeKind.PRESERVATION_CONTRACT:
        authority_class = RequirementAuthorityClass.PRESERVATION
    elif hypothesis:
        authority_class = RequirementAuthorityClass.HYPOTHESIS
    else:
        authority_class = RequirementAuthorityClass.HARD
    leaf_id = stable_id("req-leaf", claim_id, formula, objective_id)
    weight = {
        Authority.A: 4.0,
        Authority.B: 3.0,
        Authority.C: 2.0,
        Authority.PROVISIONAL: 1.0,
    }[claim.authority]
    return RequirementLeaf(
        leaf_id=leaf_id,
        objective_id=objective_id,
        formula=formula,
        quantified_variables=variables,
        domains=domains,
        precondition=_precondition(formula, variable_names),
        trigger=_trigger(formula),
        entrypoint_hypotheses=_entrypoint_hypotheses(formula),
        required_trace_relation=relation,
        observation_contract=observation,
        exception_contract=exception,
        state_contract=state,
        preservation_contract=preservation,
        witnesses=claim.evidence_ids,
        authority=claim.authority,
        authority_class=authority_class,
        supporting_evidence=claim.evidence_ids,
        hypothesis_id=claim_id if hypothesis else None,
        coverage_status="PARTITIONED",
        mandatory=(claim.authority.trusted or hypothesis),
        weight=weight,
    )


def compile_assignment_overlay(
    semantic_graph: SemanticGraph,
    assignment: HypothesisAssignment,
) -> RequirementGraph:
    if not assignment.coherent:
        raise ValueError("cannot compile an incoherent semantic assignment")
    selected = (
        set(assignment.common_hard_node_ids)
        | set(assignment.assignment_node_ids)
        | set(assignment.preservation_node_ids)
    )
    missing = selected - semantic_graph.claims.keys()
    if missing:
        raise KeyError(f"assignment references missing semantic claims: {sorted(missing)}")
    requirement_graph = RequirementGraph(assignment_id=assignment.assignment_id)
    root_id = stable_id("objective", assignment.assignment_id, "root")
    requirement_graph.add_objective_node(root_id, "complete repair contract", selected)

    for claim_id in sorted(selected):
        claim = semantic_graph.claims[claim_id]
        objective_id = stable_id("objective", claim_id)
        requirement_graph.add_objective_node(objective_id, claim.formula, claim.evidence_ids)
        requirement_graph.add_hyperedge(requirement_hyperedge(
            "DECOMPOSES_TO",
            [root_id],
            [objective_id],
            evidence_ids=claim.evidence_ids,
        ))
        formulas = _decompose_formula(claim.formula)
        for formula in formulas:
            leaf = _leaf_from_claim(
                semantic_graph,
                claim_id,
                formula,
                objective_id=objective_id,
                hypothesis=claim_id in assignment.assignment_node_ids,
            )
            requirement_graph.add_leaf(leaf)
            requirement_graph.add_hyperedge(requirement_hyperedge(
                "DECOMPOSES_TO",
                [objective_id],
                [leaf.leaf_id],
                guard=leaf.precondition,
                evidence_ids=leaf.supporting_evidence,
            ))
            for variable in leaf.quantified_variables:
                variable_id = stable_id("quantifier", leaf.leaf_id, variable.name)
                requirement_graph.add_node(GraphNode(
                    variable_id,
                    "quantifier",
                    f"forall {variable.name} in {variable.domain_id}",
                    variable.to_dict(),
                    leaf.supporting_evidence,
                ))
                requirement_graph.add_hyperedge(requirement_hyperedge(
                    "QUANTIFIES",
                    [variable_id],
                    [leaf.leaf_id],
                    evidence_ids=leaf.supporting_evidence,
                ))
            partitions = symbolic_scenario_partitions(leaf)
            for partition in partitions:
                requirement_graph.add_partition(partition)

    requirement_graph.finalize_authority_snapshot()
    return requirement_graph


def compile_requirement_core(
    semantic_graph: SemanticGraph,
    hypothesis_set: HypothesisSet,
    repository_index: RepositoryIndex,
) -> RequirementGraph:
    """Compile only authoritative issue/test obligations needed before generation."""

    if not hypothesis_set.alternatives or hypothesis_set.preferred_assignment_id is None:
        raise ValueError("no coherent authority-complete semantic hypothesis")
    preferred = next(
        item for item in hypothesis_set.alternatives
        if item.assignment_id == hypothesis_set.preferred_assignment_id
    )
    high_confidence = tuple(sorted(
        claim_id for claim_id in preferred.assignment_node_ids
        if claim_id in semantic_graph.claims
        and semantic_graph.claims[claim_id].authority.trusted
    ))
    core_assignment = HypothesisAssignment(
        assignment_id=stable_id(
            "requirement-core-assignment", hypothesis_set.active_assignment_ids,
            hypothesis_set.common_hard_node_ids, high_confidence,
        ),
        choice_by_decision=dict(preferred.choice_by_decision),
        common_hard_node_ids=hypothesis_set.common_hard_node_ids,
        assignment_node_ids=high_confidence,
        preservation_node_ids=preferred.preservation_node_ids,
        contradiction_ids=preferred.contradiction_ids,
        coherent=True, authority_complete=True,
        selection_mode="hypothesis_set", score=preferred.score,
    )
    graph = compile_assignment_overlay(semantic_graph, core_assignment)
    graph.build_stats.update({
        "core_leaf_count": len(graph.leaves),
        "repository_index_symbol_count": len(repository_index.symbols),
        "unresolved_semantic_decision_count": len(hypothesis_set.unresolved_decision_ids),
    })
    return graph


def _predicate_names(predicates: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for predicate in predicates:
        try:
            names.update(
                item.id for item in ast.walk(ast.parse(predicate, mode="eval"))
                if isinstance(item, ast.Name)
            )
        except SyntaxError:
            continue
    return names


def join_requirement_to_paths(
    leaf: RequirementLeaf,
    candidate_paths: Iterable[PathClass],
    partitions: Iterable[DomainPartition],
    *,
    max_results: int,
    deadline: float | None = None,
) -> tuple[RequirementPathObligation, ...]:
    if max_results < 1:
        raise ValueError("max_results must be positive")
    satisfiable = [item for item in partitions if item.satisfiable]
    candidates: dict[tuple[str, str, tuple[str, ...]], RequirementPathObligation] = {}
    for path in candidate_paths:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if not path.feasible or not path.observation_ids:
            continue
        channels = set(map(str, leaf.observation_contract.get("channels", ("return",))))
        if "exception" in channels and path.exit_kind not in {"exception", "raise"}:
            continue
        if channels == {"return"} and path.exit_kind in {"exception", "raise"}:
            continue
        path_names = _predicate_names(path.critical_predicates)
        linked = [
            partition for partition in satisfiable
            if path_names & set(partition.variable_names)
        ]
        if not linked and satisfiable:
            # No quantified variable is connected to this path predicate. One
            # representative partition is sufficient; copying every partition
            # would be the old Cartesian-product bug.
            linked = [min(
                satisfiable,
                key=lambda item: (item.constraints != ("True",), len(item.constraints), item.partition_id),
            )]
        for partition in linked:
            if deadline is not None and time.monotonic() >= deadline:
                break
            combined = _combined_partition(leaf, partition, path)
            if not combined.satisfiable:
                continue
            key = (path.accumulated_guard, path.exit_kind, path.observation_ids)
            obligation = RequirementPathObligation(
                path_obligation_id=stable_id(
                    "active-path-obligation", leaf.leaf_id,
                    combined.partition_id, path.path_class_id,
                ),
                leaf_id=leaf.leaf_id, authority=leaf.authority.value,
                scenario_partition_id=combined.partition_id,
                public_trigger_id=path.entrypoint_id,
                entrypoint_id=path.entrypoint_id,
                path_class_id=path.path_class_id, path_edge_ids=path.edge_ids,
                accumulated_guard=path.accumulated_guard,
                exit_kind=path.exit_kind, observation_id=path.observation_ids[0],
                predicate_oracle_id=None, preservation_caller_ids=(),
                dependence_slice_ids=path.node_ids, base_feasible=True,
                frontier_ids=(), requirement_graph_hash="", program_graph_hash="",
                partition=combined,
                trigger_recipe={"entrypoint_id": path.entrypoint_id,
                                "bindings": combined.candidate_bindings[0] if combined.candidate_bindings else {}},
            )
            previous = candidates.get(key)
            if previous is None or len(obligation.path_edge_ids) < len(previous.path_edge_ids):
                candidates[key] = obligation
    return tuple(sorted(
        candidates.values(),
        key=lambda item: (len(item.path_edge_ids), item.path_obligation_id),
    )[:max_results])


@dataclass(frozen=True, slots=True)
class RequirementDelta:
    affected_leaf_ids: tuple[str, ...]
    added_partition_ids: tuple[str, ...]
    reused: bool
    reasons: tuple[str, ...]


def promote_domains_from_diff(
    graph: RequirementGraph,
    program_graph: ProgramGraph,
    actual_diff: ActualDiff,
    trace_delta: dict[str, Any] | None,
    *,
    deadline: float | None = None,
) -> RequirementDelta:
    if actual_diff.empty and not trace_delta:
        return RequirementDelta((), (), True, ("NO_NEW_INFORMATION",))
    predicates: list[str] = []
    reasons: set[str] = set()
    for relation in actual_diff.changed_relations:
        if deadline is not None and time.monotonic() >= deadline:
            reasons.add("ANALYSIS_TRUNCATED")
            break
        base_kind = relation.kind.split("_", 1)[0]
        if base_kind == "guard":
            source = relation.new_source or relation.old_source
            if source:
                predicates.append(source)
                reasons.add("DIFF_GUARD")
        elif base_kind in {"dispatch", "return", "exception", "state", "resource"}:
            source = relation.new_source or relation.old_source
            if source:
                predicates.append(source)
            reasons.add(f"DIFF_{base_kind.upper()}")
    affected: set[str] = set()
    added: set[str] = set()
    for leaf in graph.leaves.values():
        if deadline is not None and time.monotonic() >= deadline:
            reasons.add("ANALYSIS_TRUNCATED")
            break
        variable_names = {item.name for item in leaf.quantified_variables}
        relevant = [
            predicate for predicate in predicates
            if not variable_names or _predicate_names((predicate,)) & variable_names
        ]
        if not relevant:
            continue
        affected.add(leaf.leaf_id)
        for partition in promote_program_predicates(leaf, relevant):
            if deadline is not None and time.monotonic() >= deadline:
                reasons.add("ANALYSIS_TRUNCATED")
                break
            if partition.partition_id not in graph.partitions:
                graph.add_partition(partition)
                added.add(partition.partition_id)
    if "ANALYSIS_TRUNCATED" in reasons:
        graph.add_frontier(Frontier(
            frontier_id=stable_id(
                "req-frontier", graph.assignment_id,
                "DIFF_PROMOTION_ANALYSIS_TRUNCATED",
            ),
            kind="ANALYSIS_TRUNCATED",
            owner_id=graph.assignment_id,
            reason="diff-driven domain promotion deadline reached",
            resolution_action="continue affected leaf promotion on demand",
            hard=False,
            evidence_ids=(),
        ))
    return RequirementDelta(
        tuple(sorted(affected)), tuple(sorted(added)), False,
        tuple(sorted(reasons or {"TRACE_DELTA"})),
    )


def apply_domain_promotions(
    graph: RequirementGraph,
    promotions_by_leaf: dict[str, Iterable[Any]],
) -> RequirementGraph:
    updated = graph
    for leaf_id, promotions in promotions_by_leaf.items():
        if leaf_id not in updated.leaves:
            raise KeyError(leaf_id)
        for promotion in promotions:
            updated.add_partition(promotion)
        leaf = updated.leaves[leaf_id]
        updated.leaves[leaf_id] = replace(leaf, coverage_status="PROGRAM_PROMOTED")
    return updated


def _observation_candidates(
    leaf: RequirementLeaf,
    graph: ProgramGraph,
    seeds: set[str],
    *,
    max_nodes: int | None = None,
) -> set[str]:
    channel_to_kinds = {
        "return": {"return", "assertion", "observation_point"},
        "exception": {"exception", "assertion", "observation_point"},
        "state": {"field", "observation_point", "assertion"},
        "output": {"external_effect", "assertion", "observation_point"},
        "calls": {"call_site", "protocol_operation", "assertion"},
        "effects": {"external_interface", "external_effect", "assertion"},
    }
    desired_kinds = {
        kind
        for channel in leaf.observation_contract.get("channels", ["return"])
        for kind in channel_to_kinds.get(str(channel), {"observation_point"})
    }
    # One multi-source traversal is both complete for long dependency chains
    # and cheaper than scanning the same reachable subgraph once per seed.
    reachable = graph.reachable(
        seeds,
        edge_predicate=lambda edge: edge.kind in PATH_RELATIONS,
        max_nodes=max_nodes,
    )
    candidates = {
        node_id
        for node_id in reachable
        if graph.nodes[node_id].kind in desired_kinds
        or node_id in graph.observation_node_ids
    }
    return candidates


def _recover_leaf_seeds(
    leaf: RequirementLeaf,
    graph: ProgramGraph,
    *,
    max_open_world_seeds: int,
) -> tuple[set[str], bool]:
    seeds: set[str] = set()
    for hypothesis in leaf.entrypoint_hypotheses:
        resolved = graph.resolve_symbol(hypothesis)
        if not resolved and "." in hypothesis:
            resolved = graph.resolve_symbol(hypothesis.rsplit(".", 1)[-1])
        seeds.update(resolved)
    trigger_name = leaf.trigger.split("(", 1)[0].strip(" `")
    if not seeds and trigger_name and trigger_name != "public_invocation":
        seeds.update(graph.resolve_symbol(trigger_name))
    if seeds:
        return seeds, False
    formula_tokens = {
        token
        for token in re.findall(r"\b[A-Za-z_]\w*\b", leaf.formula)
        if len(token) > 2
    }
    referenced = {
        node_id
        for node_id in graph.external_surface_ids
        if graph.nodes[node_id].label in formula_tokens
        or str(graph.nodes[node_id].attributes.get("qualified_name", "")).rsplit(".", 1)[-1] in formula_tokens
    }
    if referenced:
        return referenced, False
    ordered = sorted(graph.external_surface_ids)
    return set(ordered[:max_open_world_seeds]), len(ordered) > max_open_world_seeds


def _path_matches_recovered(path_class: PathClass, recovered_node_ids: set[str]) -> bool:
    return bool(set(path_class.node_ids) & recovered_node_ids)


def _combined_partition(
    leaf: RequirementLeaf,
    base_partition: DomainPartition,
    path_class: PathClass,
) -> DomainPartition:
    path_constraints = tuple(
        predicate
        for predicate in path_class.critical_predicates
        if predicate and not predicate.startswith(("iterable(", "raises("))
    )
    constraints = tuple(dict.fromkeys(base_partition.constraints + path_constraints))
    result = solve_constraints(
        leaf.quantified_variables,
        leaf.domains,
        constraints,
        max_combinations=4096,
    )
    proof = result.to_dict()
    proof["coverage_complete"] = bool(
        result.complete and all(not domain.open_world for domain in leaf.domains)
    )
    return DomainPartition(
        partition_id=stable_id("path-partition", leaf.leaf_id, base_partition.partition_id, path_class.path_class_id, constraints),
        variable_names=base_partition.variable_names,
        constraints=constraints,
        candidate_bindings=(result.witness,) if result.witness is not None else (),
        source="requirement_program_product",
        scope=base_partition.scope,
        satisfiable=result.satisfiable,
        proof=proof,
        witness_ids=base_partition.witness_ids,
        leaf_id=leaf.leaf_id,
    )


def _edge_frontier(
    graph: RequirementGraph,
    leaf: RequirementLeaf,
    kind: str,
    reason: str,
    action: str,
    *,
    hard: bool | None = None,
) -> Frontier:
    frontier = Frontier(
        frontier_id=stable_id("req-frontier", leaf.leaf_id, kind, reason),
        kind=kind,
        owner_id=leaf.leaf_id,
        reason=reason,
        resolution_action=action,
        hard=leaf.mandatory if hard is None else hard,
        evidence_ids=leaf.supporting_evidence,
    )
    graph.add_frontier(frontier)
    return frontier


def _partition_is_proven_unsat(partition: DomainPartition) -> bool:
    """Return true only when the constraint result is a closed-world proof."""

    proof = partition.proof or {}
    return (
        not partition.satisfiable
        and bool(proof.get("complete", False))
        and proof.get("reason") in {
            "finite_domain_exhausted",
            "unsupported_constraint",
            "missing_domain",
        }
    )


def _record_partition_frontier(
    requirement_graph: RequirementGraph,
    leaf: RequirementLeaf,
    partition: DomainPartition,
) -> None:
    if partition.satisfiable or _partition_is_proven_unsat(partition):
        return
    _edge_frontier(
        requirement_graph,
        leaf,
        "OPEN_WORLD_DOMAIN_CAP",
        f"partition {partition.partition_id} is not decided by the finite witness solver",
        "retain the partition and extend symbolic/domain analysis before closure",
    )


def _account_path_edges(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    leaf: RequirementLeaf,
    partition: DomainPartition,
    path_class: PathClass,
    observation_reachability_cache: dict[tuple[str, ...], set[str]],
) -> None:
    path_edges = set(path_class.edge_ids)
    observations = set(path_class.observation_ids)
    observation_key = tuple(sorted(observations))
    reaches_observation = observation_reachability_cache.get(observation_key)
    if reaches_observation is None:
        reaches_observation = program_graph.reachable(
            observation_key,
            direction="backward",
            edge_predicate=lambda edge: edge.kind in PATH_RELATIONS,
        )
        observation_reachability_cache[observation_key] = reaches_observation
    for node_id in path_class.node_ids:
        path_state_id = stable_id(
            "path-state", leaf.leaf_id, partition.partition_id, path_class.path_class_id, node_id
        )
        for edge in program_graph.outgoing(node_id):
            targets_reach_observation = any(
                target in reaches_observation for target in edge.target_ids
            )
            if edge.edge_id not in path_edges and not targets_reach_observation:
                continue
            proof_id: str | None = None
            frontier_id: str | None = None
            if edge.edge_id in path_edges:
                status = LedgerStatus.ENUMERATED
            elif node_id in observations:
                status = LedgerStatus.PRESERVATION_ONLY
                proof_id = stable_id(
                    "post-observation-discharge", leaf.leaf_id, edge.edge_id
                )
            elif edge.kind not in PATH_RELATIONS:
                status = LedgerStatus.PRESERVATION_ONLY
                proof_id = stable_id(
                    "structural-edge-discharge", leaf.leaf_id, edge.edge_id, edge.kind
                )
            else:
                feasible, proof = guard_feasibility(
                    partition.constraints + (edge.condition,)
                )
                if not feasible:
                    status = LedgerStatus.PROVED_INFEASIBLE
                    proof_id = stable_id("infeasibility-proof", proof)
                elif any(program_graph.nodes[target].kind == "unknown_dynamic_target" for target in edge.target_ids):
                    frontier = _edge_frontier(
                        requirement_graph,
                        leaf,
                        "DYNAMIC_TARGET",
                        f"unresolved relevant target on edge {edge.edge_id}",
                        "run targeted tracing and recompile this path state",
                    )
                    status = LedgerStatus.FRONTIER
                    frontier_id = frontier.frontier_id
                else:
                    frontier = _edge_frontier(
                        requirement_graph,
                        leaf,
                        "UNENUMERATED_FEASIBLE_EDGE",
                        f"feasible relevant edge {edge.edge_id} is absent from path class",
                        "expand path enumeration without dropping the alternative",
                    )
                    status = LedgerStatus.FRONTIER
                    frontier_id = frontier.frontier_id
            record = PathEdgeLedgerRecord(
                ledger_id=stable_id("edge-ledger", path_state_id, edge.edge_id),
                path_state_id=path_state_id,
                program_edge_id=edge.edge_id,
                relation_kind=edge.kind,
                status=status,
                proof_id=proof_id,
                frontier_id=frontier_id,
                leaf_id=leaf.leaf_id,
            )
            requirement_graph.add_ledger(record)


def compile_requirement_paths(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    *,
    max_open_world_seeds: int = 64,
    max_observation_nodes: int = 256,
    max_paths_per_entry: int = 256,
    max_path_classes_per_leaf: int = 24,
    promote_all_program_predicates: bool = False,
    deadline: float | None = None,
    leaf_ids: set[str] | None = None,
    progress_callback: Callable[[str, str, float | None], None] | None = None,
) -> RequirementGraph:
    total_started = time.perf_counter()
    timings: dict[str, float] = defaultdict(float)
    if requirement_graph.path_obligations and leaf_ids is None:
        raise ValueError("path compilation requires an unmaterialized requirement graph version")
    if not requirement_graph.path_obligations and leaf_ids is None:
        # A ledger without an owning materialized obligation is stale.  This
        # can occur when a bounded incremental refresh invalidates every path
        # before it reaches path materialization.  Keeping those records both
        # misstates coverage and prevents a later context-driven retry.
        requirement_graph.edge_ledger.clear()
    semantic_hash = requirement_graph.semantic_layer_hash()
    program_hash = program_graph.program_hash()
    observation_reachability_cache: dict[tuple[str, ...], set[str]] = {}
    observation_candidate_cache: dict[
        tuple[tuple[str, ...], tuple[str, ...]], frozenset[str]
    ] = {}
    recovery_cache: dict[
        tuple[tuple[str, ...], tuple[str, ...], int], EntrypointResult
    ] = {}
    recovery_shape_cache: dict[
        tuple[tuple[str, ...], tuple[str, ...], int],
        tuple[frozenset[str], tuple[PathClass, ...]],
    ] = {}
    entry_path_cache: dict[
        tuple[tuple[tuple[str, ...], tuple[str, ...], int], str],
        EntrypointPath | None,
    ] = {}
    preservation_callers_cache: dict[str, tuple[str, ...]] = {}
    leaves = [
        leaf for leaf in requirement_graph.hard_and_preservation_leaves()
        if leaf_ids is None or leaf.leaf_id in leaf_ids
    ]
    for leaf in leaves:
        if deadline is not None and time.monotonic() >= deadline:
            _edge_frontier(
                requirement_graph, leaf, "ANALYSIS_TRUNCATED",
                "requirement path deadline reached", "continue with active obligations",
                hard=False,
            )
            break
        recovery_started = time.perf_counter()
        if progress_callback is not None:
            progress_callback("seed_observation_recovery", "in_progress", None)
        seeds, seeds_capped = _recover_leaf_seeds(
            leaf, program_graph, max_open_world_seeds=max_open_world_seeds
        )
        if seeds_capped:
            _edge_frontier(
                requirement_graph,
                leaf,
                "OPEN_WORLD_SEED_CAP",
                f"more than {max_open_world_seeds} public seeds require analysis",
                "raise seed cap or provide evidence-backed symbol references",
            )
        if not seeds:
            _edge_frontier(
                requirement_graph,
                leaf,
                "MISSING_SEED",
                "no issue/test/public-API seed resolved",
                "resolve a public symbol reference or failing observation",
            )
            elapsed = time.perf_counter() - recovery_started
            timings["seed_observation_recovery_seconds"] += elapsed
            if progress_callback is not None:
                progress_callback("seed_observation_recovery", "complete", elapsed)
            continue
        observation_key = (
            tuple(sorted(seeds)),
            tuple(sorted(map(str, leaf.observation_contract.get("channels", ["return"])))),
        )
        cached_observations = observation_candidate_cache.get(observation_key)
        if cached_observations is None:
            cached_observations = frozenset(
                _observation_candidates(leaf, program_graph, seeds)
            )
            observation_candidate_cache[observation_key] = cached_observations
        observations = set(cached_observations)
        if len(observations) > max_observation_nodes:
            observations = set(sorted(observations)[:max_observation_nodes])
            _edge_frontier(
                requirement_graph,
                leaf,
                "OBSERVATION_CAP",
                f"observation candidates exceeded {max_observation_nodes}",
                "narrow by evidence-backed observation channel or increase cap",
            )
        if not observations:
            _edge_frontier(
                requirement_graph,
                leaf,
                "MISSING_OBSERVATION",
                "no required observation channel is forward reachable from seeds",
                "expand data/exception/state flow or run a targeted trace",
            )
            elapsed = time.perf_counter() - recovery_started
            timings["seed_observation_recovery_seconds"] += elapsed
            if progress_callback is not None:
                progress_callback("seed_observation_recovery", "complete", elapsed)
            continue
        recovery_key = (
            tuple(sorted(seeds)),
            tuple(sorted(observations)),
            max_paths_per_entry,
        )
        recovery = recovery_cache.get(recovery_key)
        if recovery is None:
            recovery = recover_entrypoints(
                seeds,
                program_graph,
                observation_ids=observations,
                max_paths_per_entry=max_paths_per_entry,
                deadline=deadline,
            )
            recovery_cache[recovery_key] = recovery
        for frontier_id in recovery.frontier_ids:
            source = program_graph.frontiers[frontier_id]
            requirement_graph.add_frontier(Frontier(
                frontier_id=source.frontier_id,
                kind=source.kind,
                owner_id=leaf.leaf_id,
                reason=source.reason,
                resolution_action=source.resolution_action,
                hard=(leaf.mandatory and source.kind != "ANALYSIS_TRUNCATED"),
                evidence_ids=leaf.supporting_evidence,
                status=source.status,
            ))
        recovery_shape = recovery_shape_cache.get(recovery_key)
        if recovery_shape is None:
            recovered_nodes = frozenset(
                node_id for path in recovery.paths for node_id in path.node_ids
            )
            relevant_path_classes = tuple(
                path_class
                for path_class in recovery.path_classes
                if path_class.feasible
                and _path_matches_recovered(path_class, set(recovered_nodes))
            )
            recovery_shape = (recovered_nodes, relevant_path_classes)
            recovery_shape_cache[recovery_key] = recovery_shape
        recovered_nodes, relevant_path_classes = recovery_shape
        for path_class in relevant_path_classes:
            program_graph.add_path_class(path_class)
        elapsed = time.perf_counter() - recovery_started
        timings["seed_observation_recovery_seconds"] += elapsed
        if progress_callback is not None:
            progress_callback("seed_observation_recovery", "complete", elapsed)

        promotion_started = time.perf_counter()
        if progress_callback is not None:
            progress_callback("domain_promotion", "in_progress", None)
        branch_predicates = {
            str(program_graph.nodes[node_id].attributes["predicate"])
            for node_id in recovered_nodes
            if program_graph.nodes[node_id].kind in {"branch", "loop"}
            and program_graph.nodes[node_id].attributes.get("predicate")
            and not str(program_graph.nodes[node_id].attributes["predicate"]).startswith(("for ", "async for "))
        }
        promoted = promote_program_predicates(leaf, branch_predicates) if promote_all_program_predicates else ()
        for partition in promoted:
            requirement_graph.add_partition(partition)
            _record_partition_frontier(requirement_graph, leaf, partition)
        base_partitions = [
            partition
            for partition in requirement_graph.partitions.values()
            if partition.leaf_id == leaf.leaf_id
            and set(partition.variable_names) == {item.name for item in leaf.quantified_variables}
            and (partition.witness_ids == leaf.witnesses or partition.source.startswith("reverse_domain"))
        ]
        elapsed = time.perf_counter() - promotion_started
        timings["domain_promotion_seconds"] += elapsed
        if progress_callback is not None:
            progress_callback("domain_promotion", "complete", elapsed)

        product_started = time.perf_counter()
        if progress_callback is not None:
            progress_callback("path_partition_product", "in_progress", None)
        templates = join_requirement_to_paths(
            leaf, relevant_path_classes, base_partitions,
            max_results=max_path_classes_per_leaf,
            deadline=deadline,
        )
        for template in templates:
                if deadline is not None and time.monotonic() >= deadline:
                    _edge_frontier(
                        requirement_graph, leaf, "ANALYSIS_TRUNCATED",
                        "requirement path product deadline reached",
                        "continue active path obligations on demand",
                        hard=False,
                    )
                    break
                path_class = program_graph.path_classes[template.path_class_id]
                partition = template.partition
                requirement_graph.add_partition(partition)
                if not partition.satisfiable:
                    _record_partition_frontier(requirement_graph, leaf, partition)
                    continue
                entry_path_key = (recovery_key, path_class.path_class_id)
                if entry_path_key not in entry_path_cache:
                    observation_ids = set(path_class.observation_ids)
                    entry_path_cache[entry_path_key] = next((
                        path
                        for path in recovery.paths
                        if path.entrypoint_id == path_class.entrypoint_id
                        and path.observation_id in observation_ids
                    ), None)
                entry_path = entry_path_cache[entry_path_key]
                if entry_path is None:
                    continue
                preservation_callers = preservation_callers_cache.get(
                    path_class.path_class_id
                )
                if preservation_callers is None:
                    path_node_ids = set(path_class.node_ids)
                    preservation_callers = tuple(sorted(
                        source
                        for node_id in path_class.node_ids
                        for edge in program_graph.incoming(
                            node_id, {"calls", "may_call", "test_coverage"}
                        )
                        for source in edge.source_ids
                        if source not in path_node_ids
                    ))
                    preservation_callers_cache[
                        path_class.path_class_id
                    ] = preservation_callers
                obligation = replace(
                    template,
                    path_obligation_id=stable_id(
                        "path-obligation", leaf.leaf_id, partition.partition_id,
                        entry_path.trigger_id, path_class.path_class_id,
                        path_class.exit_kind, path_class.observation_ids,
                    ),
                    public_trigger_id=entry_path.trigger_id,
                    entrypoint_id=entry_path.entrypoint_id,
                    preservation_caller_ids=preservation_callers,
                    requirement_graph_hash=semantic_hash,
                    program_graph_hash=program_hash,
                    trigger_recipe={
                        "entrypoint": program_graph.nodes[entry_path.entrypoint_id].attributes.get("qualified_name"),
                        "bindings": partition.candidate_bindings[0] if partition.candidate_bindings else {},
                    },
                )
                requirement_graph.add_path_obligation(obligation)
                _account_path_edges(
                    requirement_graph,
                    program_graph,
                    leaf,
                    partition,
                    path_class,
                    observation_reachability_cache,
                )
        elapsed = time.perf_counter() - product_started
        timings["path_partition_product_seconds"] += elapsed
        if progress_callback is not None:
            progress_callback("path_partition_product", "complete", elapsed)
        if not any(
            obligation.leaf_id == leaf.leaf_id and obligation.base_feasible
            for obligation in requirement_graph.path_obligations.values()
        ):
            _edge_frontier(
                requirement_graph,
                leaf,
                "MISSING_FEASIBLE_PATH",
                "no satisfiable trigger-to-observation path obligation was materialized",
                "resolve entrypoint, path, partition, or observation frontier",
            )
    finalization_started = time.perf_counter()
    if progress_callback is not None:
        progress_callback("hash_finalization", "in_progress", None)
    final_requirement_hash = requirement_graph.semantic_layer_hash()
    final_program_hash = program_graph.program_hash()
    requirement_graph.path_obligations = {
        obligation_id: replace(
            obligation,
            requirement_graph_hash=final_requirement_hash,
            program_graph_hash=final_program_hash,
        )
        for obligation_id, obligation in requirement_graph.path_obligations.items()
    }
    finalization_seconds = time.perf_counter() - finalization_started
    timings["hash_finalization_seconds"] += finalization_seconds
    timings["total_seconds"] = time.perf_counter() - total_started
    requirement_graph.build_timings = dict(timings)
    requirement_graph.build_stats = {
        "leaf_count": len(leaves),
        "partition_count": len(requirement_graph.partitions),
        "path_obligation_count": len(requirement_graph.path_obligations),
        "edge_ledger_count": len(requirement_graph.edge_ledger),
        "observation_cache_count": len(observation_candidate_cache),
        "entrypoint_recovery_cache_count": len(recovery_cache),
    }
    if progress_callback is not None:
        progress_callback("hash_finalization", "complete", finalization_seconds)
    return requirement_graph


def refresh_requirement_paths(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    *,
    affected_leaf_ids: set[str],
    max_path_classes_per_leaf: int = 24,
    deadline: float | None = None,
) -> tuple[RequirementGraph, tuple[str, ...], tuple[str, ...]]:
    """Invalidate and recompute only obligations owned by affected leaves."""

    selected = set(affected_leaf_ids) & set(requirement_graph.leaves)
    if not selected:
        return requirement_graph, (), ()
    removed = {
        path_id for path_id, obligation in requirement_graph.path_obligations.items()
        if obligation.leaf_id in selected
    }
    for path_id in removed:
        requirement_graph.path_obligations.pop(path_id, None)
    requirement_graph.edge_ledger = {
        ledger_id: record
        for ledger_id, record in requirement_graph.edge_ledger.items()
        if record.leaf_id and record.leaf_id not in selected
    }
    requirement_graph.frontiers = {
        frontier_id: frontier
        for frontier_id, frontier in requirement_graph.frontiers.items()
        if frontier.owner_id not in selected
    }
    compile_requirement_paths(
        requirement_graph,
        program_graph,
        max_open_world_seeds=64,
        max_observation_nodes=128,
        max_paths_per_entry=max_path_classes_per_leaf,
        max_path_classes_per_leaf=max_path_classes_per_leaf,
        promote_all_program_predicates=False,
        deadline=deadline,
        leaf_ids=selected,
    )
    added = {
        path_id for path_id, obligation in requirement_graph.path_obligations.items()
        if obligation.leaf_id in selected
    } - removed
    return requirement_graph, tuple(sorted(removed)), tuple(sorted(added))
