from __future__ import annotations

from dataclasses import dataclass

from reachpatch.models.base import SerializableRecord
from reachpatch.models.controller import RepairAction, RepairIntent
from reachpatch.repair.operators import registered_operator


@dataclass(frozen=True, slots=True)
class ContractValidation(SerializableRecord):
    valid: bool
    errors: tuple[str, ...]


def validate_repair_action(
    action: RepairAction,
    intent: RepairIntent,
    *,
    checkpoint_id: str,
) -> ContractValidation:
    errors = []
    if action.intent_id != intent.intent_id or action.plan.intent_id != intent.intent_id:
        errors.append("action is not tied to the selected repair intent")
    if action.plan.checkpoint_id != checkpoint_id:
        errors.append("repair plan references a stale checkpoint")
    if action.plan.component_id != intent.component_id:
        errors.append("repair plan crosses repair components")
    if not action.edit_intents:
        errors.append("repair action has no structured edits")
    if any(registered_operator(item.operator) is None for item in action.edit_intents):
        errors.append("repair action uses an unregistered diff operator")
    if not set(action.write_set) <= {item.relative_path for item in action.edit_intents}:
        errors.append("declared write set contains a file without an edit")
    if not set(intent.repair_cut_ids) <= set(action.causal_cut_ids):
        errors.append("action omits the selected causal repair cut")
    return ContractValidation(not errors, tuple(errors))


def validate_repair_plan(*args, **kwargs) -> ContractValidation:
    return validate_repair_action(*args, **kwargs)
