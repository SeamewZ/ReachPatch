from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class HypothesisSet(SerializableRecord):
    common_hard_node_ids: tuple[str, ...]
    alternatives: tuple[HypothesisAssignment, ...]
    unresolved_decision_ids: tuple[str, ...]
    active_assignment_ids: tuple[str, ...]
    preferred_assignment_id: str | None


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
        if edge.kind != "REFUTES":
            continue
        claim_ids = set(edge.source_ids + edge.target_ids)
        if not claim_ids <= selected:
            continue
        claims = [graph.claims[claim_id] for claim_id in claim_ids if claim_id in graph.claims]
        kinds = {claim.kind for claim in claims}
        if kinds == {SemanticNodeKind.SEMANTIC_HYPOTHESIS}:
            return False
        if (
            SemanticNodeKind.SEMANTIC_HYPOTHESIS in kinds
            and SemanticNodeKind.NORMATIVE_REQUIREMENT in kinds
        ):
            return False
        # Preservation assertions are scoped by their input and trace. Opposite
        # assertions from different public-test partitions become challenges;
        # they are not global semantic contradictions.
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

    unknowns = {decision.unknown_claim_id for decision in decisions}
    beam: list[tuple[str, ...]] = [()]
    beam_width = max(max_assignments * 4, 16)
    fixed = set(hard) | set(preservation)
    for decision in decisions:
        alternatives = decision.alternative_claim_ids + (decision.unknown_claim_id,)
        expanded: list[tuple[str, ...]] = []
        for partial in beam:
            for choice in alternatives:
                candidate = partial + (choice,)
                selected = set(candidate) - unknowns
                if _coherent_choice(graph, selected | fixed):
                    expanded.append(candidate)
        expanded.sort(key=lambda choices: (
            -_assignment_score(graph, choices, unknowns),
            sum(choice in unknowns for choice in choices),
            choices,
        ))
        beam = expanded[:beam_width]
        if not beam:
            break
    candidates: list[HypothesisAssignment] = []
    for choices in beam:
        selected = set(choices) - unknowns
        coherent = _coherent_choice(graph, selected | fixed)
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
            authority_complete=all(
                choice not in unknowns
                or not any(
                    graph.claims[claim_id].authority.trusted
                    for claim_id in decision.alternative_claim_ids
                )
                for decision, choice in zip(decisions, choices, strict=True)
            ),
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
    if selection_mode not in {"hypothesis_set", "certified", "benchmark"}:
        raise ValueError("selection_mode must be hypothesis_set, certified, or benchmark")
    hypothesis_set = build_hypothesis_set(graph, max_active_hypotheses=max_assignments)
    if not hypothesis_set.alternatives:
        return None
    chosen = next(
        (
            item for item in hypothesis_set.alternatives
            if item.assignment_id == hypothesis_set.preferred_assignment_id
        ),
        hypothesis_set.alternatives[0],
    )
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


def _evidence_dimensions(
    graph: SemanticGraph,
    assignment: HypothesisAssignment,
) -> tuple[float, frozenset[str], int]:
    clusters: set[str] = set()
    explicit = 0
    for claim_id in assignment.assignment_node_ids:
        claim = graph.claims.get(claim_id)
        if claim is None:
            continue
        if claim.authority.trusted:
            explicit += 1
        clusters.update(
            graph.evidence[evidence_id].independence_cluster
            for evidence_id in claim.evidence_ids
            if evidence_id in graph.evidence
        )
    return assignment.score, frozenset(clusters), explicit


def build_hypothesis_set(
    graph: SemanticGraph,
    *,
    max_active_hypotheses: int = 4,
) -> HypothesisSet:
    """Retain a bounded, non-dominated set of executable interpretations."""

    if max_active_hypotheses < 1:
        raise ValueError("max_active_hypotheses must be positive")
    decisions, assignments = enumerate_assignments(
        graph, max_assignments=max(32, max_active_hypotheses * 8)
    )
    coherent = [
        item for item in assignments if item.coherent and item.authority_complete
    ]
    dimensions = {
        item.assignment_id: _evidence_dimensions(graph, item) for item in coherent
    }
    retained: list[HypothesisAssignment] = []
    for candidate in coherent:
        score, clusters, explicit = dimensions[candidate.assignment_id]
        dominated = False
        for other in coherent:
            if other.assignment_id == candidate.assignment_id:
                continue
            other_score, other_clusters, other_explicit = dimensions[other.assignment_id]
            if (
                other_score >= score
                and other_clusters.issuperset(clusters)
                and other_explicit >= explicit
                and (
                    other_score > score
                    or other_clusters != clusters
                    or other_explicit > explicit
                )
            ):
                dominated = True
                break
        if not dominated:
            retained.append(candidate)
    retained.sort(key=lambda item: (
        -dimensions[item.assignment_id][0],
        -len(dimensions[item.assignment_id][1]),
        -dimensions[item.assignment_id][2],
        item.assignment_id,
    ))
    retained = retained[:max_active_hypotheses]
    common = set(retained[0].common_hard_node_ids) if retained else set()
    for item in retained[1:]:
        common &= set(item.common_hard_node_ids)
    unresolved = tuple(sorted(
        decision.decision_id
        for decision in decisions
        if len({
            item.choice_by_decision.get(decision.decision_id) for item in retained
        }) > 1
    ))
    return HypothesisSet(
        common_hard_node_ids=tuple(sorted(common)),
        alternatives=tuple(retained),
        unresolved_decision_ids=unresolved,
        active_assignment_ids=tuple(item.assignment_id for item in retained),
        preferred_assignment_id=retained[0].assignment_id if retained else None,
    )
