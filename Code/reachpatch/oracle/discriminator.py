from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from reachpatch.evidence.hypotheses import HypothesisAssignment
from reachpatch.evidence.models import SemanticDecision
from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.core import Frontier


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


def discriminator_probe_from_dict(raw: dict[str, Any]) -> DiscriminatorProbe:
    return DiscriminatorProbe(
        probe_id=str(raw["probe_id"]),
        decision_id=str(raw["decision_id"]),
        alternative_claim_ids=tuple(raw.get("alternative_claim_ids", ())),
        observation_channels=tuple(raw.get("observation_channels", ())),
        recipe_hint=dict(raw.get("recipe_hint", {})),
        correctness_authority=str(raw.get("correctness_authority", "NONE")),
    )


def enqueue_discriminator_probes(
    challenge_graph,
    probes: Iterable[DiscriminatorProbe],
    *,
    completed_probe_ids: Iterable[str] = (),
) -> dict[str, tuple[str, ...]]:
    """Attach ambiguity probes to executable, oracle-locked challenges."""

    completed = set(completed_probe_ids)
    mapping: dict[str, tuple[str, ...]] = {}
    ranked_cells = sorted(
        challenge_graph.cells.values(),
        key=lambda cell: (
            -float(challenge_graph.priorities[cell.challenge_id].score)
            if cell.challenge_id in challenge_graph.priorities else 0.0,
            cell.challenge_id,
        ),
    )
    for probe in probes:
        if probe.probe_id in completed:
            continue
        desired = set(probe.observation_channels)
        compatible = []
        for cell in ranked_cells:
            scenario = challenge_graph.scenarios.get(cell.scenario_id or "")
            if scenario is None or cell.trigger_recipe_id is None:
                continue
            if desired and not desired.intersection(scenario.observe.channels):
                continue
            compatible.append(cell)
        if not compatible:
            challenge_graph.add_frontier(Frontier(
                frontier_id=stable_id(
                    "challenge-frontier", "DISCRIMINATOR_DEFERRED",
                    probe.probe_id, challenge_graph.graph_hash(),
                ),
                kind="DISCRIMINATOR_DEFERRED",
                owner_id=probe.probe_id,
                reason="no oracle-locked executable challenge exposes the discriminator channels",
                resolution_action="materialize a targeted executable scenario for the unresolved semantic decision",
                hard=False,
                evidence_ids=(),
            ))
            continue
        selected = compatible[0]
        dependency = dict(selected.diff_dependency)
        probe_ids = set(map(str, dependency.get("discriminator_probe_ids", ())))
        probe_ids.add(probe.probe_id)
        decision_ids = set(map(
            str, dependency.get("discriminator_decision_ids", ())
        ))
        decision_ids.add(probe.decision_id)
        challenge_graph.cells[selected.challenge_id] = replace(
            selected,
            diff_dependency={
                **dependency,
                "discriminator_probe_ids": sorted(probe_ids),
                "discriminator_decision_ids": sorted(decision_ids),
            },
        )
        mapping[probe.probe_id] = (selected.challenge_id,)
    return mapping
