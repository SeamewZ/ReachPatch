from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from .base import SerializableRecord, utc_now


@dataclass(slots=True)
class BudgetVector(SerializableRecord):
    semantic_tokens: int = 0
    graph_tokens: int = 0
    initial_generator_tokens: int = 0
    repair_generator_tokens: int = 0
    challenge_materializer_tokens: int = 0
    discriminator_tokens: int = 0
    execution_seconds: float = 0.0
    tracing_seconds: float = 0.0
    wall_seconds: float = 0.0

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.to_dict().values()):
            raise ValueError("budget values cannot be negative")

    def available(self) -> bool:
        return any(value > 0 for value in self.to_dict().values())

    def subtract(self, charge: "BudgetVector") -> "BudgetVector":
        current = self.to_dict()
        debit = charge.to_dict()
        remainder = {name: current[name] - debit[name] for name in current}
        if any(value < 0 for value in remainder.values()):
            exhausted = [name for name, value in remainder.items() if value < 0]
            raise BudgetExceeded(f"budget exhausted for {exhausted}")
        return BudgetVector(**remainder)


class BudgetExceeded(RuntimeError):
    """A role attempted to spend beyond its frozen budget."""


@dataclass(frozen=True, slots=True)
class BudgetCharge(SerializableRecord):
    instance_id: str
    role: str
    episode_id: str
    transition_id: str
    request_id: str
    amount: BudgetVector
    charged_at: str = field(default_factory=utc_now)


class BudgetLedger(SerializableRecord):
    ROLES = {
        "semantic",
        "graph",
        "initial-generator",
        "repair-generator",
        "challenge-materializer",
        "discriminator",
        "executor",
        "tracer",
        "cleanup",
    }

    def __init__(self, initial: BudgetVector, *, generator_reserve_fraction: float = 0.70) -> None:
        if not 0.0 <= generator_reserve_fraction <= 1.0:
            raise ValueError("generator reserve fraction must be in [0, 1]")
        self.initial = initial
        self.remaining = BudgetVector(**initial.to_dict())
        self.generator_reserve_fraction = generator_reserve_fraction
        self.charges: list[BudgetCharge] = []
        self._lock = RLock()

    def charge(self, charge: BudgetCharge) -> BudgetVector:
        if charge.role not in self.ROLES:
            raise ValueError(f"unknown budget role {charge.role!r}")
        with self._lock:
            if any(existing.request_id == charge.request_id for existing in self.charges):
                raise ValueError(f"duplicate budget request {charge.request_id}")
            if charge.role not in {"initial-generator", "repair-generator"}:
                total_generator = (
                    self.initial.initial_generator_tokens
                    + self.initial.repair_generator_tokens
                )
                generator_remaining = (
                    self.remaining.initial_generator_tokens
                    + self.remaining.repair_generator_tokens
                )
                reserve = total_generator * self.generator_reserve_fraction
                if generator_remaining < reserve:
                    raise BudgetExceeded("generator reserve invariant already violated")
            self.remaining = self.remaining.subtract(charge.amount)
            self.charges.append(charge)
            return BudgetVector(**self.remaining.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "initial": self.initial.to_dict(),
            "remaining": self.remaining.to_dict(),
            "generator_reserve_fraction": self.generator_reserve_fraction,
            "charges": [charge.to_dict() for charge in self.charges],
        }
