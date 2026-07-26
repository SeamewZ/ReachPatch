from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reachpatch.models.base import SerializableRecord
from reachpatch.models.enums import Authority, HypothesisLifecycle, SemanticNodeKind


@dataclass(frozen=True, slots=True)
class SemanticClaim(SerializableRecord):
    claim_id: str
    kind: SemanticNodeKind
    formula: str
    subject: str
    authority: Authority
    evidence_ids: tuple[str, ...]
    decision_id: str | None = None
    lifecycle: HypothesisLifecycle = HypothesisLifecycle.VIABLE
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SemanticDecision(SerializableRecord):
    decision_id: str
    subject: str
    alternative_claim_ids: tuple[str, ...]
    unknown_claim_id: str
    contradiction_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorityAudit(SerializableRecord):
    claim_id: str
    requested: Authority
    assigned: Authority
    rule: str
    evidence_ids: tuple[str, ...]
    accepted: bool
