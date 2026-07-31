from __future__ import annotations

from pathlib import Path
from typing import Any

from reachpatch.artifacts import ArtifactStore
from reachpatch.models.controller import ReachAvoidState
from reachpatch.models.enums import Authority, Confidence


class RunArtifacts:
    def __init__(self, run_root: str | Path, instance_id: str) -> None:
        self.run_root = Path(run_root).resolve()
        self.instance_id = instance_id
        self.store = ArtifactStore(self.run_root / "artifacts")

    def put(
        self,
        artifact_type: str,
        payload: Any,
        *,
        state: ReachAvoidState | None = None,
        producer: str,
        parent_ids: tuple[str, ...] = (),
        authority: Authority = Authority.PROVISIONAL,
        confidence: Confidence = Confidence.UNKNOWN,
        status: str = "ACTIVE",
    ) -> str:
        envelope = self.store.put(
            artifact_type,
            payload,
            instance_id=self.instance_id,
            producer=producer,
            parent_ids=parent_ids,
            authority=authority,
            confidence=confidence,
            status=status,
        )
        if state is not None:
            identifiers = state.artifact_ids.setdefault(artifact_type, [])
            if envelope.artifact_id not in identifiers:
                identifiers.append(envelope.artifact_id)
        return envelope.artifact_id

    def persist_graph_stack(self, state: ReachAvoidState) -> tuple[str, ...]:
        parent_ids: list[str] = []
        for artifact_type, payload in (
            ("semantic_hypothesis_graph", state.semantic_graph),
            ("episode_assignment", state.assignment),
            ("requirement_graph", state.requirement_graph),
            ("program_graph", state.program_graph),
            ("active_binding_graph", state.active_binding_graph),
            ("challenge_graph", state.challenge_graph),
        ):
            identifier = self.put(
                artifact_type,
                payload,
                state=state,
                producer="reachpatch.graph-pipeline",
                parent_ids=tuple(parent_ids[-2:]),
                authority=Authority.C,
                confidence=Confidence.HIGH,
            )
            parent_ids.append(identifier)
        for path_class in state.program_graph.path_classes.values():
            self.put(
                "path_class", path_class, state=state,
                producer="reachpatch.program-graph",
                authority=Authority.C,
                confidence=Confidence.HIGH,
            )
        for unit in state.active_binding_graph.units.values():
            self.put(
                "active_binding_unit", unit, state=state,
                producer="reachpatch.active-binding-graph",
                authority=Authority.C,
                confidence=Confidence.HIGH,
            )
        for recipe in state.challenge_graph.recipes.values():
            self.put(
                "input_recipe", recipe, state=state,
                producer="reachpatch.challenge-materializer",
                authority=Authority.C,
                confidence=Confidence.HIGH,
            )
        for cell in state.challenge_graph.cells.values():
            self.put(
                "challenge_cell", cell, state=state,
                producer="reachpatch.challenge-materializer",
                authority=Authority.C,
                confidence=Confidence.HIGH,
            )
        return tuple(parent_ids)

    def persist_state(self, state: ReachAvoidState) -> str:
        parents = tuple(
            identifiers[-1]
            for key, identifiers in sorted(state.artifact_ids.items())
            if identifiers and key != "reach_avoid_state"
        )
        return self.put(
            "reach_avoid_state",
            state.to_dict(),
            state=state,
            producer="reachpatch.controller",
            parent_ids=parents,
            authority=Authority.C,
            confidence=Confidence.CONFIRMED,
            status=state.termination_status or "ACTIVE",
        )
