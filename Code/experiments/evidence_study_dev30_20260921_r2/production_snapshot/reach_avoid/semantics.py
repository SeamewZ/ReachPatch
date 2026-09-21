"""Stable semantic identities shared by Reach--Avoid."""
from __future__ import annotations
import re
from dataclasses import replace
from typing import Any
from reachpatch.models.base import stable_id


_TARGET_SUCCESS_RE = re.compile(
    r"\b(?:should|must|does|do)\s+(?:not\s+)?(?:fail|raise|error)\b"
    r"|\b(?:should|must)\s+succeed\b"
    r"|\bwithout\s+(?:an?\s+)?(?:error|exception)\b",
    re.IGNORECASE,
)


def _contract_value(contract: Any, name: str, default: Any = None) -> Any:
    if isinstance(contract, dict):
        return contract.get(name, default)
    return getattr(contract, name, default)


def target_success_semantics(relation: Any) -> bool:
    """Whether target prose specifies successful process completion."""
    return bool(_TARGET_SUCCESS_RE.search(" ".join(str(relation or "").split())))


def normalize_execution_contract(
    contract: Any,
    *,
    role: str = "TARGET",
    force_process_success: bool = False,
) -> Any:
    """Return one typed contract for the Reach--Avoid execution path.

    Target success relations are represented by an exit-zero observation. Raw
    output, including a baseline traceback, remains an observed payload and is
    never copied into the expected target payload. Preservation and impact
    relations are intentionally untouched because they may require an exact
    baseline relation.
    """
    if contract is None or str(role).upper() not in {"TARGET", "MECHANICAL"}:
        return contract
    relation = _contract_value(contract, "relation", "")
    expected = _contract_value(contract, "expected")
    exit_zero = isinstance(expected, dict) and expected.get("exit_code") == 0
    if not (force_process_success or target_success_semantics(relation) or exit_zero):
        return contract
    from reachpatch.models.evidence import ObservationContract
    return ObservationContract(
        relation=str(relation or "target command succeeds"),
        expected={"exit_code": 0}, observable="process", comparator="EXIT_ZERO",
    )


def normalize_target_oracle(oracle: Any, *, role: str = "TARGET") -> Any:
    """Normalize an executable target oracle without changing authority."""
    if oracle is None or str(role).upper() != "TARGET":
        return oracle
    contract = normalize_execution_contract(
        {"relation": getattr(oracle, "relation", ""),
         "expected": getattr(oracle, "expected", None)}, role=role,
    )
    if (
        contract is None
        or _contract_value(contract, "expected") == getattr(oracle, "expected", None)
    ):
        return oracle
    from reachpatch.models.evidence import ExecutableOracle
    return ExecutableOracle(
        oracle_id=stable_id(
            "normalized-target-oracle", getattr(oracle, "oracle_id", ""),
            contract.normalized(),
        ),
        authority=oracle.authority, relation=contract.relation,
        expected=contract.expected, executable=oracle.executable,
        source_evidence_ids=tuple(getattr(oracle, "source_evidence_ids", ())),
    )


def normalize_target_cell(cell: Any) -> Any:
    """Adapt one graph cell while preserving preservation/impact contracts."""
    kind = str(getattr(cell, "kind", "TARGET")).upper()
    role = (
        "PRESERVATION" if kind == "PRESERVATION"
        else "IMPACT" if kind in {"IMPACT", "PROTOCOL"} else "TARGET"
    )
    if role != "TARGET":
        return cell
    force_process_success = (
        str(getattr(cell, "origin", "")).upper() == "PUBLIC_CHECK"
        or str(getattr(getattr(cell, "input_recipe", None), "kind", "")).upper()
        == "PUBLIC_REPLAY"
    )
    contract = normalize_execution_contract(
        getattr(cell, "observation_contract", None), role=role,
        force_process_success=force_process_success,
    )
    oracle = normalize_target_oracle(getattr(cell, "oracle", None), role=role)
    if (
        contract is getattr(cell, "observation_contract", None)
        and oracle is getattr(cell, "oracle", None)
    ):
        return cell
    return replace(cell, observation_contract=contract, oracle=oracle,
                   authority=oracle.authority)

def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return tuple(_plain(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_plain(item) for item in value), key=repr))
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return value

def normalize_input_recipe_semantics(recipe: Any) -> Any:
    value = _plain(recipe)
    if isinstance(value, dict):
        return {
            key: item for key, item in value.items()
            if key not in {
                "recipe_id", "source_check_id", "challenge_id",
                "binding_id", "trace_symbols", "derivation", "kind",
            }
        }
    return value

def observation_contract_semantic_id(contract: Any) -> str:
    value = contract.normalized() if hasattr(contract, "normalized") else _plain(contract)
    return stable_id("reachavoid-observation-contract", value)

def input_partition_semantic_key(input_recipe: Any) -> str:
    return stable_id("reachavoid-input-partition", normalize_input_recipe_semantics(input_recipe))

def scenario_semantic_key(*, requirement_contract_id: str, role: str, input_recipe: Any, observation_contract: Any) -> str:
    return stable_id("reachavoid-scenario", requirement_contract_id, str(role).upper(), normalize_input_recipe_semantics(input_recipe), observation_contract_semantic_id(observation_contract))

def normalize_symbol(symbol: Any) -> str:
    return " ".join(str(symbol or "").replace("\\", "/").split()).casefold()

def normalize_failure_signature(signature: Any) -> str:
    return " ".join(str(signature or "").split()).casefold()

def repair_frontier_semantic_key(*, kind: Any, requirement_contract_id: str = "", input_partition_key: Any = "", source_symbol: Any = "", failure_signature: Any = "") -> str:
    return stable_id("repair-frontier-semantic", getattr(kind, "value", kind), requirement_contract_id, input_partition_key, normalize_symbol(source_symbol), normalize_failure_signature(failure_signature))
