"""Bounded validation work which must not compete with repair frontiers."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Any
from reachpatch.models.base import SerializableRecord, stable_id
from .semantics import scenario_semantic_key

@dataclass(frozen=True, slots=True)
class ValidationBacklogItem(SerializableRecord):
    semantic_key: str
    kind: Literal["IMPACT_REPLAY", "LOCKED_TARGET_REPLAY", "LOCKED_PRESERVATION_REPLAY", "ADJACENT_PARTITION", "EXPLORATION_PROBE"]
    requirement_id: str | None = None
    scenario_key: str | None = None
    source_symbol: str | None = None
    risk_rank: int = 0
    authority: str = "PROVISIONAL"

    @classmethod
    def create(cls, *, kind: str, requirement_id: str | None = None, scenario_key: str | None = None, source_symbol: str | None = None, risk_rank: int = 0, authority: str = "PROVISIONAL") -> "ValidationBacklogItem":
        key = stable_id(
            "validation-backlog", kind, requirement_id, scenario_key,
            source_symbol,
        )
        return cls(key, kind, requirement_id, scenario_key, source_symbol, risk_rank, authority)


def _source_symbol(stack: Any, binding: Any) -> str | None:
    symbols = []
    for node_id in getattr(binding, "program_symbol_ids", ()):
        node = stack.program_graph.nodes.get(node_id)
        if node is not None and getattr(node, "symbol", None):
            symbols.append(str(node.symbol))
    if not symbols:
        return None
    return min(symbols, key=lambda value: (value.count("."), value))

def _scenario_key(cell: Any, stack: Any) -> str:
    leaf = stack.requirement_graph.leaves.get(cell.requirement_id)
    contract = getattr(leaf, "expected_observation", None) or cell.observation_contract
    contract_id = getattr(contract, "contract_id", cell.observation_contract.contract_id)
    role = (
        "PRESERVATION" if cell.kind == "PRESERVATION"
        or bool(getattr(leaf, "preservation", False))
        else "IMPACT" if cell.kind == "IMPACT" else "TARGET"
    )
    return scenario_semantic_key(
        requirement_contract_id=contract_id, role=role,
        input_recipe=cell.input_recipe,
        observation_contract=cell.observation_contract,
    )


def derive_impact_validation_plan(
    state: Any,
    selected_frontier: Any = None,
    cumulative_diff: Any = None,
) -> tuple[ValidationBacklogItem, ...]:
    """Return the bounded replay plan consumed by a trial transition.

    Static callers are represented as backlog only. They cannot become repair
    frontiers and cannot preempt the selected target.
    """
    stack = getattr(state, "graph_stack", None)
    if stack is None:
        return ()
    cells = tuple(stack.challenge_graph.active_cells())
    items: dict[str, ValidationBacklogItem] = {}
    changed_hunk_ids = {
        hunk.hunk_id for hunk in getattr(cumulative_diff, "hunks", ())
    }
    locked_ids = (
        set(getattr(getattr(state, "locked_checks", None), "target_ids", ()))
        | set(getattr(getattr(state, "locked_checks", None), "preservation_ids", ()))
    )
    locked_cells = sorted(
        (cell for cell in cells
         if cell.execution_scenario.command
         and cell.input_recipe.source_check_id in locked_ids),
        key=lambda cell: (
            cell.kind != "PRESERVATION", _scenario_key(cell, stack),
        ),
    )[:4]
    preservation_ids = set(
        getattr(getattr(state, "locked_checks", None), "preservation_ids", ())
    )
    for rank, cell in enumerate(locked_cells):
        kind = (
            "LOCKED_PRESERVATION_REPLAY"
            if cell.input_recipe.source_check_id in preservation_ids
            else "LOCKED_TARGET_REPLAY"
        )
        binding = stack.binding_graph.units.get(cell.binding_id)
        item = ValidationBacklogItem.create(
            kind=kind, requirement_id=cell.requirement_id,
            scenario_key=_scenario_key(cell, stack),
            source_symbol=_source_symbol(stack, binding),
            risk_rank=rank, authority=cell.oracle.authority,
        )
        items[item.semantic_key] = item

    impact = getattr(stack.program_graph, "impact_cone", None)
    if impact is not None:
        risk_ids = set(impact.all_risk_ids())
        impacted_public_checks = set(getattr(impact, "public_check_ids", ()))
        selected_symbols = set(getattr(selected_frontier, "repair_slice_ids", ()))
        source_symbol = getattr(selected_frontier, "source_symbol", None)
        if source_symbol:
            selected_symbols.add(source_symbol)

        def is_impact_candidate(cell: Any) -> bool:
            if not cell.execution_scenario.command:
                return False
            binding = stack.binding_graph.units.get(cell.binding_id)
            symbols = set(getattr(binding, "program_symbol_ids", ()))
            # An existing public preservation replay directly tied to the
            # selected source slice is a bounded impact check.  It is not a
            # p0 repair frontier: it merely prevents a target-only trial from
            # being accepted without replaying the behavior it can break.
            return bool(
                cell.kind == "IMPACT"
                or symbols.intersection(risk_ids)
                or (
                    cell.kind == "PRESERVATION"
                    and cell.input_recipe.source_check_id in impacted_public_checks
                )
                or (
                    cell.kind == "PRESERVATION"
                    and cell.origin == "PUBLIC_CHECK"
                    and symbols.intersection(selected_symbols)
                )
            )

        impact_cells = sorted(
            (cell for cell in cells if is_impact_candidate(cell)),
            key=lambda cell: (
                cell.kind != "PRESERVATION",
                cell.oracle.authority not in {"A", "B", "C"},
                not bool(changed_hunk_ids.intersection(cell.changed_hunk_ids)),
                not bool(
                    set(getattr(
                        stack.binding_graph.units.get(cell.binding_id),
                        "program_symbol_ids", (),
                    )).intersection(selected_symbols)
                ),
                _scenario_key(cell, stack),
            ),
        )[:2]
        for rank, cell in enumerate(impact_cells):
            binding = stack.binding_graph.units.get(cell.binding_id)
            item = ValidationBacklogItem.create(
                kind="IMPACT_REPLAY", requirement_id=cell.requirement_id,
                scenario_key=_scenario_key(cell, stack),
                source_symbol=_source_symbol(stack, binding),
                risk_rank=rank, authority=cell.oracle.authority,
            )
            items[item.semantic_key] = item
    return tuple(sorted(items.values(), key=lambda item: (item.risk_rank, item.semantic_key)))


def derive_validation_backlog(state: Any, selected_frontier: Any = None) -> dict[str, ValidationBacklogItem]:
    items: dict[str, ValidationBacklogItem] = {}
    stack = getattr(state, "graph_stack", None)
    if stack is None:
        return items
    for item in derive_impact_validation_plan(state, selected_frontier):
        items[item.semantic_key] = item
    planned_scenarios = {
        item.scenario_key for item in items.values() if item.scenario_key
    }
    for cell in stack.challenge_graph.active_cells():
        if (
            cell.terminal_status.value not in {"PENDING", "UNKNOWN"}
            or not cell.execution_scenario.command
        ):
            continue
        scenario_key = _scenario_key(cell, stack)
        if scenario_key in planned_scenarios:
            continue
        binding = stack.binding_graph.units.get(cell.binding_id)
        item = ValidationBacklogItem.create(
            kind=("ADJACENT_PARTITION" if cell.hard
                  else "EXPLORATION_PROBE"),
            requirement_id=cell.requirement_id, scenario_key=scenario_key,
            source_symbol=_source_symbol(stack, binding),
            risk_rank=20 if cell.hard else 40, authority=cell.oracle.authority,
        )
        items[item.semantic_key] = item
        planned_scenarios.add(scenario_key)
    return items
