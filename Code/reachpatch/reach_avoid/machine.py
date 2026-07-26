from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from reachpatch.models.base import SerializableRecord, stable_id, utc_now
from reachpatch.models.enums import ControllerPhase


_ALLOWED = {
    ControllerPhase.SEMANTIC: {ControllerPhase.GRAPH_BUILD, ControllerPhase.SEALED},
    ControllerPhase.GRAPH_BUILD: {ControllerPhase.INCUMBENT_CLOSE, ControllerPhase.SEALED},
    ControllerPhase.INCUMBENT_CLOSE: {
        ControllerPhase.CORE_SELECT,
        ControllerPhase.ABLATE,
        ControllerPhase.SEALED,
    },
    ControllerPhase.CORE_SELECT: {
        ControllerPhase.INTENT_SELECT,
        ControllerPhase.ROOT_RECOVERY,
        ControllerPhase.SEALED,
    },
    ControllerPhase.INTENT_SELECT: {
        ControllerPhase.GENERATOR_REVISE,
        ControllerPhase.ROOT_RECOVERY,
        ControllerPhase.SEALED,
    },
    ControllerPhase.GENERATOR_REVISE: {
        ControllerPhase.DIFF_RECONCILE,
        ControllerPhase.INCUMBENT_CLOSE,
        ControllerPhase.ROOT_RECOVERY,
        ControllerPhase.SEALED,
    },
    ControllerPhase.DIFF_RECONCILE: {
        ControllerPhase.DICC_VALIDATE,
        ControllerPhase.COUNTEREXAMPLE_FEEDBACK,
        ControllerPhase.SEALED,
    },
    ControllerPhase.DICC_VALIDATE: {
        ControllerPhase.TRANSITION_GATE,
        ControllerPhase.COUNTEREXAMPLE_FEEDBACK,
        ControllerPhase.SEALED,
    },
    ControllerPhase.TRANSITION_GATE: {
        ControllerPhase.COUNTEREXAMPLE_FEEDBACK,
        ControllerPhase.SEALED,
    },
    ControllerPhase.COUNTEREXAMPLE_FEEDBACK: {
        ControllerPhase.INCUMBENT_CLOSE,
        ControllerPhase.ROOT_RECOVERY,
        ControllerPhase.SEALED,
    },
    ControllerPhase.ROOT_RECOVERY: {
        ControllerPhase.INCUMBENT_CLOSE,
        ControllerPhase.CORE_SELECT,
        ControllerPhase.SEALED,
    },
    ControllerPhase.ABLATE: {
        ControllerPhase.INCUMBENT_CLOSE,
        ControllerPhase.SEALED,
    },
    ControllerPhase.SEALED: set(),
}


@dataclass(frozen=True, slots=True)
class PhaseTransition(SerializableRecord):
    transition_id: str
    from_phase: ControllerPhase
    to_phase: ControllerPhase
    event: str
    artifact_ids: tuple[str, ...]
    occurred_at: str = field(default_factory=utc_now)


def phase_transition(
    current: ControllerPhase,
    target: ControllerPhase,
    *,
    event: str,
    artifact_ids: Iterable[str] = (),
) -> PhaseTransition:
    if target != current and target not in _ALLOWED[current]:
        raise ValueError(f"illegal controller transition {current.value} -> {target.value}")
    identifiers = tuple(sorted(set(artifact_ids)))
    return PhaseTransition(
        transition_id=stable_id(
            "phase-transition", current, target, event, identifiers, utc_now()
        ),
        from_phase=current,
        to_phase=target,
        event=event,
        artifact_ids=identifiers,
    )


class StateMachine:
    def __init__(self, phase: ControllerPhase = ControllerPhase.SEMANTIC) -> None:
        self.phase = phase
        self.history: list[PhaseTransition] = []

    def transition(
        self,
        target: ControllerPhase,
        *,
        event: str,
        artifact_ids: Iterable[str] = (),
        expected_phase: ControllerPhase | None = None,
    ) -> PhaseTransition:
        if expected_phase is not None and expected_phase != self.phase:
            raise ValueError(
                f"stale controller phase: expected {expected_phase.value}, actual {self.phase.value}"
            )
        record = phase_transition(
            self.phase, target, event=event, artifact_ids=artifact_ids
        )
        self.phase = target
        self.history.append(record)
        return record
