"""Shared immutable and serializable ReachPatch records."""

from .base import SCHEMA_VERSION, canonical_json, content_hash, stable_id
from .controller import (
    CounterexamplePacket,
    IncumbentCheckpoint,
    ReachAvoidState,
    RepairAction,
    RepairIntent,
    TerminalCertificate,
    TransitionCertificate,
    WorkingPatch,
)
from .enums import Authority, OutcomeStatus
from .isolation import (
    GenerationInstance, HarnessEvaluationInstance, assert_generation_payload,
)

__all__ = [
    "Authority",
    "CounterexamplePacket",
    "IncumbentCheckpoint",
    "GenerationInstance",
    "HarnessEvaluationInstance",
    "OutcomeStatus",
    "ReachAvoidState",
    "RepairAction",
    "RepairIntent",
    "SCHEMA_VERSION",
    "TerminalCertificate",
    "TransitionCertificate",
    "WorkingPatch",
    "canonical_json",
    "assert_generation_payload",
    "content_hash",
    "stable_id",
]
