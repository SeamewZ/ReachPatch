"""Execution-driven repair objective public API.

The former graph-backed objective lived at this import path. Production code
now exposes the single-failure objective from :mod:`execution_objective`; the
explicit module keeps this compatibility path free of graph imports.
"""

from .execution_objective import (
    InitialPatchObjective, MechanicalBlocker, RepairAttempt, RepairMode,
    RepairObjective, SourceSlice, compile_execution_repair_objective,
)


def validation_obligation_from_challenge(cell, *, source: str):
    """Read-only compatibility adapter for historical graph artifacts.

    The execution-driven controller never calls this function.  Imports of
    graph models are intentionally deferred until a legacy test or artifact
    reader explicitly requests the conversion.
    """
    from reachpatch.models.evidence import ObservationContract
    from reachpatch.models.reach_avoid import ValidationObligation
    from reachpatch.reach_avoid.semantics import normalize_execution_contract

    role = (
        "PRESERVATION" if cell.kind == "PRESERVATION"
        else "IMPACT" if cell.kind in {"IMPACT", "PROTOCOL"}
        else "TARGET"
    )
    contract = normalize_execution_contract(
        cell.observation_contract,
        role=role,
        force_process_success=(
            role == "TARGET"
            and (
                str(getattr(cell, "origin", "")).upper() == "PUBLIC_CHECK"
                or str(getattr(getattr(cell, "input_recipe", None), "kind", "")).upper()
                == "PUBLIC_REPLAY"
            )
        ),
    )
    return ValidationObligation(
        validation_id=f"legacy-challenge:{cell.requirement_id}:{cell.challenge_id}",
        role=role,
        authority=cell.oracle.authority,
        command=tuple(cell.execution_scenario.command),
        cwd=cell.execution_scenario.cwd,
        environment=dict(cell.execution_scenario.environment),
        timeout_seconds=int(cell.execution_scenario.timeout_seconds),
        backend="shared-executor",
        concrete_input=cell.input_recipe.concrete_input,
        input_derivation="; ".join(cell.input_recipe.derivation) or source,
        oracle_id=cell.oracle.oracle_id,
        expected_relation=contract.relation,
        expected_observation=contract.expected,
        requirement_id=cell.requirement_id,
        binding_id=cell.binding_id,
        challenge_id=cell.challenge_id,
    )


def atomic_obligation_from_validation(obligation):
    """Convert a historical validation record for legacy readers/tests.

    This adapter is deliberately isolated from the production objective and
    transition code.  It preserves the old serialized shape without making
    GraphStack part of the execution-driven controller dependency graph.
    """
    from dataclasses import replace
    from reachpatch.models.base import stable_id
    from reachpatch.models.evidence import ObservationContract
    from reachpatch.models.graphs import InputRecipe
    from reachpatch.models.reach_avoid import AtomicObligation, atomic_obligation_key
    from reachpatch.reach_avoid.semantics import input_partition_semantic_key

    if obligation.role == "MECHANICAL":
        contract = ObservationContract(
            obligation.expected_relation or "Python sources compile successfully",
            obligation.expected_observation or {"exit_code": 0},
            observable="process", comparator="EXIT_ZERO",
        )
    else:
        expected = obligation.expected_observation
        if isinstance(expected, dict) and expected.get("exit_code") == 0:
            contract = ObservationContract(
                obligation.expected_relation or "validation command succeeds",
                {"exit_code": 0}, observable="process", comparator="EXIT_ZERO",
            )
        else:
            contract = ObservationContract(
                obligation.expected_relation or "validation contract", expected,
                observable="process" if isinstance(expected, dict) else "return",
                comparator="EQUALS",
            )
    recipe = InputRecipe(
        recipe_id=stable_id(
            "validation-input-recipe", obligation.requirement_id,
            obligation.role, obligation.command, obligation.cwd,
            obligation.environment, obligation.concrete_input,
        ),
        kind=f"VALIDATION_{obligation.role}",
        concrete_input=obligation.concrete_input,
        derivation=(obligation.input_derivation,), command=tuple(obligation.command),
        source_check_id=obligation.challenge_id,
        environment=tuple(sorted(obligation.environment.items())),
    )
    raw = AtomicObligation(
        key="", requirement_id=obligation.requirement_id,
        requirement_contract_id=contract.contract_id,
        role=(obligation.role if obligation.role in {
            "TARGET", "PRESERVATION", "IMPACT", "MECHANICAL",
        } else "TARGET"),
        input_recipe=recipe,
        input_partition_id=input_partition_semantic_key(recipe),
        oracle_contract=contract,
        authority=(obligation.authority if obligation.authority in {
            "A", "B", "C", "PROVISIONAL",
        } else "PROVISIONAL"),
        hard=True, source="repair-objective-validation", binding_id=obligation.binding_id,
    )
    return replace(raw, key=atomic_obligation_key(raw))


def compile_repair_objective(state, active_failure, **kwargs):
    """Compatibility alias for the execution objective compiler."""
    return compile_execution_repair_objective(state, active_failure, **kwargs)


__all__ = [
    "InitialPatchObjective", "MechanicalBlocker", "RepairAttempt", "RepairMode",
    "RepairObjective", "SourceSlice", "compile_execution_repair_objective",
    "compile_repair_objective",
]
