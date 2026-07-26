"""Causal diagnosis, one-intent policy, structured editing, and recovery."""

from .diagnosis import diagnose_mechanism, mechanism_fingerprint
from .ablation import edit_retention_ablation
from .operators import RegisteredDiffOperator, apply_registered_operator
from .policy import next_untried_repair_intent, select_losing_core
from .session import PersistentGeneratorSession
from .context import RepairContext, build_repair_context
from .deepseek_agent import (
    ActionConversionResult,
    ActionConversionStatus,
    DeepSeekHTTPTransport,
    GeneratorBlockedExternal,
    GeneratorConversation,
    GeneratorRevision,
    PersistentDeepSeekAgent,
    convert_revision_action,
    generate_initial_patch,
    repair_from_counterexamples,
)
from .tools import ProposedEdit, RepairToolExecutor

__all__ = [
    "PersistentGeneratorSession",
    "RegisteredDiffOperator",
    "ActionConversionResult",
    "ActionConversionStatus",
    "DeepSeekHTTPTransport",
    "GeneratorBlockedExternal",
    "GeneratorConversation",
    "GeneratorRevision",
    "PersistentDeepSeekAgent",
    "ProposedEdit",
    "RepairContext",
    "RepairToolExecutor",
    "apply_registered_operator",
    "build_repair_context",
    "convert_revision_action",
    "diagnose_mechanism",
    "edit_retention_ablation",
    "mechanism_fingerprint",
    "generate_initial_patch",
    "next_untried_repair_intent",
    "repair_from_counterexamples",
    "select_losing_core",
]
