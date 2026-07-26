from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from reachpatch.evidence.extract import (
    contract_evidence,
    deterministic_semantic_parse,
    issue_evidence,
    public_test_evidence,
)
from reachpatch.evidence.models import AuthorityAudit, SemanticClaim
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.core import Evidence
from reachpatch.models.enums import (
    Authority,
    EvidenceKind,
    HypothesisLifecycle,
    SemanticNodeKind,
)
from reachpatch.models.graph import GraphEdge, GraphNode, TypedMultiGraph


def _polarity(formula: str) -> tuple[str, bool]:
    lowered = re.sub(r"\s+", " ", formula.lower())
    negative = bool(re.search(r"\b(not|never|cannot|must not|shall not|!=)\b", lowered))
    normalized = re.sub(r"\b(not|never|cannot|must not|shall not)\b|!=", " ", lowered)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    return normalized, negative


class SemanticGraph(TypedMultiGraph):
    def __init__(self, *, version: int = 1) -> None:
        super().__init__(graph_kind="semantic_hypothesis", version=version)
        self.evidence: dict[str, Evidence] = {}
        self.claims: dict[str, SemanticClaim] = {}
        self.authority_audit: list[AuthorityAudit] = []

    def add_evidence(self, evidence: Evidence) -> GraphNode:
        self.evidence[evidence.evidence_id] = evidence
        node = GraphNode(
            node_id=evidence.evidence_id,
            kind=SemanticNodeKind.EVIDENCE.value,
            label=evidence.content,
            attributes=evidence.to_dict(),
            provenance_ids=(),
        )
        return self.add_node(node)

    def add_claim(self, claim: SemanticClaim) -> GraphNode:
        self.claims[claim.claim_id] = claim
        node = GraphNode(
            node_id=claim.claim_id,
            kind=claim.kind.value,
            label=claim.formula,
            attributes=claim.to_dict(),
            provenance_ids=claim.evidence_ids,
        )
        self.add_node(node)
        for evidence_id in claim.evidence_ids:
            if evidence_id in self.nodes:
                self.add_edge(GraphEdge.create(
                    "SUPPORTS",
                    [evidence_id],
                    [claim.claim_id],
                    provenance_ids=[evidence_id],
                ))
        return node

    def active_normative_and_preservation(self) -> list[SemanticClaim]:
        return sorted(
            (
                claim
                for claim in self.claims.values()
                if claim.kind in {
                    SemanticNodeKind.NORMATIVE_REQUIREMENT,
                    SemanticNodeKind.PRESERVATION_CONTRACT,
                }
                and claim.authority.trusted
                and claim.lifecycle not in {
                    HypothesisLifecycle.REFUTED,
                    HypothesisLifecycle.CONTESTED,
                }
            ),
            key=lambda claim: claim.claim_id,
        )

    def to_dict(self) -> dict[str, Any]:
        body = super().to_dict()
        body.update({
            "evidence": [self.evidence[key].to_dict() for key in sorted(self.evidence)],
            "claims": [self.claims[key].to_dict() for key in sorted(self.claims)],
            "authority_audit": [audit.to_dict() for audit in self.authority_audit],
        })
        body_without_hash = dict(body)
        body_without_hash.pop("graph_hash", None)
        body["graph_hash"] = content_hash(body_without_hash)
        return body


@dataclass(frozen=True, slots=True)
class SemanticBuildResult:
    graph: SemanticGraph
    evidence: tuple[Evidence, ...]
    contradiction_ids: tuple[str, ...]


def _assign_authority(evidence: Evidence, parsed_authority: Authority, rule: str) -> AuthorityAudit:
    assigned = parsed_authority
    accepted = True
    if evidence.kind in {
        EvidenceKind.ISSUE_WITNESS,
        EvidenceKind.CURRENT_BEHAVIOR,
        EvidenceKind.DYNAMIC_OBSERVATION,
        EvidenceKind.MODEL_HYPOTHESIS,
    } and parsed_authority == Authority.A:
        assigned = Authority.PROVISIONAL
        accepted = False
        rule = f"rejected_A:{rule}"
    return AuthorityAudit(
        claim_id="",
        requested=parsed_authority,
        assigned=assigned,
        rule=rule,
        evidence_ids=(evidence.evidence_id,),
        accepted=accepted,
    )


def build_semantic_graph(
    issue: str,
    *,
    visible_test_paths: Iterable[str | Path] = (),
    public_contracts: Iterable[tuple[str, str, EvidenceKind]] = (),
    extra_evidence: Iterable[Evidence] = (),
) -> SemanticBuildResult:
    evidence_records = issue_evidence(issue)
    evidence_records.extend(public_test_evidence(visible_test_paths))
    evidence_records.extend(contract_evidence(public_contracts))
    evidence_records.extend(extra_evidence)
    unique = {record.evidence_id: record for record in evidence_records}

    graph = SemanticGraph()
    for evidence in sorted(unique.values(), key=lambda item: item.evidence_id):
        graph.add_evidence(evidence)
        parsed = deterministic_semantic_parse(evidence)
        audit = _assign_authority(evidence, parsed.authority_candidate, parsed.rule)
        claim_id = stable_id(
            "claim",
            parsed.semantic_kind,
            parsed.formula,
            parsed.subject,
            evidence.evidence_id,
        )
        audit = AuthorityAudit(
            claim_id=claim_id,
            requested=audit.requested,
            assigned=audit.assigned,
            rule=audit.rule,
            evidence_ids=audit.evidence_ids,
            accepted=audit.accepted,
        )
        graph.authority_audit.append(audit)
        graph.add_claim(SemanticClaim(
            claim_id=claim_id,
            kind=parsed.semantic_kind,
            formula=parsed.formula,
            subject=parsed.subject,
            authority=audit.assigned,
            evidence_ids=(evidence.evidence_id,),
            lifecycle=(
                HypothesisLifecycle.SUPPORTED
                if audit.assigned.trusted
                else HypothesisLifecycle.VIABLE
            ),
            payload={"authority_rule": audit.rule},
        ))

    contradiction_ids: list[str] = []
    claims = sorted(graph.claims.values(), key=lambda claim: claim.claim_id)
    for index, left in enumerate(claims):
        left_formula, left_negative = _polarity(left.formula)
        for right in claims[index + 1:]:
            right_formula, right_negative = _polarity(right.formula)
            if left.subject != right.subject:
                continue
            relation = "REFINES"
            if left_formula == right_formula and left_negative != right_negative:
                relation = "REFUTES"
                contradiction_id = stable_id("contradiction", left.claim_id, right.claim_id)
                contradiction_node = GraphNode(
                    contradiction_id,
                    SemanticNodeKind.CONTRADICTION.value,
                    f"{left.formula} <> {right.formula}",
                    {"claim_ids": [left.claim_id, right.claim_id]},
                    left.evidence_ids + right.evidence_ids,
                )
                graph.add_node(contradiction_node)
                graph.add_edge(GraphEdge.create(
                    "REFUTES", [left.claim_id], [right.claim_id],
                    provenance_ids=contradiction_node.provenance_ids,
                ))
                graph.add_edge(GraphEdge.create(
                    "REFUTES", [right.claim_id], [left.claim_id],
                    provenance_ids=contradiction_node.provenance_ids,
                ))
                contradiction_ids.append(contradiction_id)
            elif (
                left.kind == SemanticNodeKind.SEMANTIC_HYPOTHESIS
                and right.kind == SemanticNodeKind.SEMANTIC_HYPOTHESIS
                and left.formula != right.formula
            ):
                relation = "ALTERNATIVE_TO"
                graph.add_edge(GraphEdge.create(
                    relation, [left.claim_id], [right.claim_id], confidence=0.7,
                    provenance_ids=left.evidence_ids + right.evidence_ids,
                ))
                graph.add_edge(GraphEdge.create(
                    relation, [right.claim_id], [left.claim_id], confidence=0.7,
                    provenance_ids=left.evidence_ids + right.evidence_ids,
                ))
            elif left_formula and (left_formula in right_formula or right_formula in left_formula):
                source, target = (
                    (left, right) if len(left_formula) >= len(right_formula) else (right, left)
                )
                graph.add_edge(GraphEdge.create(
                    relation, [source.claim_id], [target.claim_id], confidence=0.8,
                    provenance_ids=source.evidence_ids + target.evidence_ids,
                ))
    return SemanticBuildResult(
        graph=graph,
        evidence=tuple(sorted(unique.values(), key=lambda item: item.evidence_id)),
        contradiction_ids=tuple(sorted(contradiction_ids)),
    )
