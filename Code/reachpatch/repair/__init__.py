"""Causal diagnosis, one-intent policy, structured editing, and recovery."""

from .diagnosis import diagnose_mechanism, mechanism_fingerprint
from .ablation import edit_retention_ablation
from .operators import RegisteredDiffOperator, apply_registered_operator
from .policy import next_untried_repair_intent, select_losing_core
from .session import PersistentGeneratorSession

__all__ = [
    "PersistentGeneratorSession",
    "RegisteredDiffOperator",
    "apply_registered_operator",
    "diagnose_mechanism",
    "edit_retention_ablation",
    "mechanism_fingerprint",
    "next_untried_repair_intent",
    "select_losing_core",
]
