from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from reachpatch.evidence.hypotheses import HypothesisAssignment
from reachpatch.evidence.models import SemanticDecision
from reachpatch.models.base import SerializableRecord, stable_id


@dataclass(frozen=True, slots=True)
class DiscriminatorProbe(SerializableRecord):
    probe_id: str
    decision_id: str
    alternative_claim_ids: tuple[str, ...]
    observation_channels: tuple[str, ...]
    recipe_hint: dict[str, Any]
    correctness_authority: str = "NONE"


@dataclass(frozen=True, slots=True)
class DiscriminatorResult(SerializableRecord):
    probe_id: str
    raw_observations: tuple[dict[str, Any], ...]
    evidence_ids: tuple[str, ...]
    selected_claim_id: str | None
    correctness_status: str = "DISCRIMINATOR_ONLY"


class HypothesisDiscriminator:
    """Plan distinguishing observations without assigning correctness authority."""

    def plan(
        self,
        decisions: Iterable[SemanticDecision],
        assignments: Iterable[HypothesisAssignment],
    ) -> tuple[DiscriminatorProbe, ...]:
        viable = tuple(assignments)
        probes = []
        for decision in decisions:
            choices = {
                item.choice_by_decision.get(decision.decision_id)
                for item in viable
            }
            if len(choices) <= 1:
                continue
            alternatives = tuple(sorted(item for item in choices if item is not None))
            probes.append(DiscriminatorProbe(
                probe_id=stable_id("discriminator-probe", decision.decision_id, alternatives),
                decision_id=decision.decision_id,
                alternative_claim_ids=alternatives,
                observation_channels=("return", "exception", "state", "calls"),
                recipe_hint={
                    "subject": decision.subject,
                    "vary": ["input_partition", "route", "state"],
                },
            ))
        return tuple(probes)

    def record(
        self,
        probe: DiscriminatorProbe,
        observations: Iterable[dict[str, Any]],
        *,
        evidence_ids: Iterable[str],
        selected_claim_id: str | None = None,
    ) -> DiscriminatorResult:
        if selected_claim_id is not None and selected_claim_id not in probe.alternative_claim_ids:
            raise ValueError("discriminator selected a claim outside its alternatives")
        return DiscriminatorResult(
            probe_id=probe.probe_id,
            raw_observations=tuple(dict(item) for item in observations),
            evidence_ids=tuple(sorted(set(evidence_ids))),
            selected_claim_id=selected_claim_id,
        )
