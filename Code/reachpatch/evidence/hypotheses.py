from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any

from reachpatch.evidence.models import SemanticDecision
from reachpatch.evidence.semantic_graph import SemanticGraph
from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.enums import Authority, SemanticNodeKind


@dataclass(frozen=True, slots=True)
class HypothesisAssignment(SerializableRecord):
    assignment_id: str
    choice_by_decision: dict[str, str]
    common_hard_node_ids: tuple[str, ...]
    assignment_node_ids: tuple[str, ...]
    preservation_node_ids: tuple[str, ...]
    contradiction_ids: tuple[str, ...]
    coherent: bool
    authority_complete: bool
    selection_mode: str
    score: float


def factor_semantic_decisions(graph: SemanticGraph) -> list[SemanticDecision]:
    by_subject: dict[str, list[str]] = {}
    for claim in graph.claims.values():
        if claim.kind == SemanticNodeKind.SEMANTIC_HYPOTHESIS:
            by_subject.setdefault(claim.subject, []).append(claim.claim_id)
    decisions: list[SemanticDecision] = []
    for subject, claim_ids in sorted(by_subject.items()):
        unknown_id = stable_id("unknown", subject)
        decision_id = stable_id("decision", subject, sorted(claim_ids))
        contradiction_ids = tuple(sorted(
            node.node_id
            for node in graph
            if node.kind == SemanticNodeKind.CONTRADICTION.value
            and any(claim_id in node.attributes.get("claim_ids", []) for claim_id in claim_ids)
        ))
        decisions.append(SemanticDecision(
            decision_id=decision_id,
            subject=subject,
            alternative_claim_ids=tuple(sorted(claim_ids)),
            unknown_claim_id=unknown_id,
            contradiction_ids=contradiction_ids,
        ))
    return decisions


def _coherent_choice(graph: SemanticGraph, selected: set[str]) -> bool:
    for edge in graph.edges.values():
        if edge.kind == "REFUTES" and set(edge.source_ids + edge.target_ids) <= selected:
            return False
    return True


def _assignment_score(graph: SemanticGraph, choices: tuple[str, ...], unknowns: set[str]) -> float:
    score = 0.0
    clusters: set[str] = set()
    for claim_id in choices:
        if claim_id in unknowns:
            score -= 1.0
            continue
        claim = graph.claims[claim_id]
        score += {Authority.A: 5.0, Authority.B: 3.0, Authority.C: 2.0}.get(
            claim.authority, 0.5
        )
        for evidence_id in claim.evidence_ids:
            cluster = graph.evidence[evidence_id].independence_cluster
            if cluster not in clusters:
                score += 0.25
                clusters.add(cluster)
    return score


def enumerate_assignments(
    graph: SemanticGraph,
    *,
    max_assignments: int = 32,
) -> tuple[list[SemanticDecision], list[HypothesisAssignment]]:
    if max_assignments < 1:
        raise ValueError("max_assignments must be positive")
    decisions = factor_semantic_decisions(graph)
    hard = tuple(sorted(
        claim.claim_id
        for claim in graph.claims.values()
        if claim.kind == SemanticNodeKind.NORMATIVE_REQUIREMENT and claim.authority.trusted
    ))
    preservation = tuple(sorted(
        claim.claim_id
        for claim in graph.claims.values()
        if claim.kind == SemanticNodeKind.PRESERVATION_CONTRACT and claim.authority.trusted
    ))
    if not decisions:
        assignment = HypothesisAssignment(
            assignment_id=stable_id("assignment", hard, preservation),
            choice_by_decision={},
            common_hard_node_ids=hard,
            assignment_node_ids=(),
            preservation_node_ids=preservation,
            contradiction_ids=(),
            coherent=True,
            authority_complete=True,
            selection_mode="unfrozen",
            score=float(len(hard) * 5 + len(preservation) * 3),
        )
        return decisions, [assignment]

    alternatives = [decision.alternative_claim_ids + (decision.unknown_claim_id,) for decision in decisions]
    unknowns = {decision.unknown_claim_id for decision in decisions}
    candidates: list[HypothesisAssignment] = []
    for choices in product(*alternatives):
        selected = set(choices) - unknowns
        coherent = _coherent_choice(graph, selected | set(hard) | set(preservation))
        if not coherent:
            continue
        mapping = {
            decision.decision_id: choice for decision, choice in zip(decisions, choices, strict=True)
        }
        candidates.append(HypothesisAssignment(
            assignment_id=stable_id("assignment", mapping, hard, preservation),
            choice_by_decision=mapping,
            common_hard_node_ids=hard,
            assignment_node_ids=tuple(sorted(selected)),
            preservation_node_ids=preservation,
            contradiction_ids=tuple(sorted({
                contradiction_id
                for decision in decisions
                for contradiction_id in decision.contradiction_ids
            })),
            coherent=True,
            authority_complete=not any(choice in unknowns for choice in choices),
            selection_mode="unfrozen",
            score=_assignment_score(graph, choices, unknowns),
        ))
    candidates.sort(key=lambda assignment: (-assignment.score, assignment.assignment_id))
    return decisions, candidates[:max_assignments]


def freeze_assignment(
    graph: SemanticGraph,
    *,
    selection_mode: str = "certified",
    max_assignments: int = 32,
) -> HypothesisAssignment | None:
    if selection_mode not in {"certified", "benchmark"}:
        raise ValueError("selection_mode must be certified or benchmark")
    _, assignments = enumerate_assignments(graph, max_assignments=max_assignments)
    if not assignments:
        return None
    complete = [assignment for assignment in assignments if assignment.authority_complete]
    if selection_mode == "certified":
        unique_choices = {tuple(sorted(item.choice_by_decision.items())) for item in complete}
        if len(unique_choices) != 1:
            return None
        chosen = complete[0]
    else:
        chosen = assignments[0]
    data: dict[str, Any] = chosen.to_dict()
    data["selection_mode"] = selection_mode
    return HypothesisAssignment(
        assignment_id=data["assignment_id"],
        choice_by_decision=dict(data["choice_by_decision"]),
        common_hard_node_ids=tuple(data["common_hard_node_ids"]),
        assignment_node_ids=tuple(data["assignment_node_ids"]),
        preservation_node_ids=tuple(data["preservation_node_ids"]),
        contradiction_ids=tuple(data["contradiction_ids"]),
        coherent=bool(data["coherent"]),
        authority_complete=bool(data["authority_complete"]),
        selection_mode=selection_mode,
        score=float(data["score"]),
    )
