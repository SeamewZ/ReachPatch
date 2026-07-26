from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from reachpatch.models.base import SerializableRecord
from reachpatch.models.enums import OutcomeStatus


@dataclass(frozen=True, slots=True)
class RepairContext(SerializableRecord):
    mode: str
    issue: str
    working_diff: str
    requirement_coverage: tuple[dict[str, Any], ...]
    failed_checks: tuple[dict[str, Any], ...]
    counterexamples: tuple[dict[str, Any], ...]
    first_trace_divergences: tuple[dict[str, Any], ...]
    active_program_slice: dict[str, Any]
    causal_repair_cuts: tuple[dict[str, Any], ...]
    impact_risks: tuple[dict[str, Any], ...]
    preserved_passes: tuple[dict[str, Any], ...]
    failed_mechanisms: tuple[str, ...]
    remaining_budget: dict[str, Any]


def _issue_text(state) -> str:
    values = [
        evidence.content
        for evidence in state.semantic_graph.evidence.values()
        if evidence.kind.value in {"ISSUE_NORMATIVE", "ISSUE_WITNESS"}
    ]
    issue = "\n".join(dict.fromkeys(values))
    hints = str(state.runtime_config.get("generation_hints", "")).strip()
    return (
        issue + (f"\n\nPublic hints (non-normative):\n{hints}" if hints else "")
    )


def build_repair_context(
    state,
    *,
    mode: Literal["INITIAL", "COUNTEREXAMPLE_REPAIR", "ROOT_RECOVERY"],
) -> RepairContext:
    coverage = []
    outcomes_by_leaf: dict[str, list] = {}
    for outcome in state.outcomes.values():
        unit = state.binding_graph.units.get(outcome.unit_id)
        if unit:
            outcomes_by_leaf.setdefault(unit.leaf_id, []).append(outcome)
    for leaf in state.requirement_graph.leaves.values():
        observed = outcomes_by_leaf.get(leaf.leaf_id, ())
        coverage.append({
            "leaf_id": leaf.leaf_id,
            "formula": leaf.formula,
            "authority": leaf.authority.value,
            "kind": leaf.authority_class.value,
            "statuses": sorted({item.status.value for item in observed}),
            "active_binding_count": sum(
                unit.leaf_id == leaf.leaf_id and unit.status == "ACTIVE"
                for unit in state.binding_graph.units.values()
            ),
        })
    failed = tuple({
        "outcome_id": item.outcome_id,
        "status": item.status.value,
        "origin": item.failure_origin,
        "observation": item.observation,
    } for item in state.outcomes.values() if item.status == OutcomeStatus.FAIL)
    packets = tuple({
        "counterexample_id": item.counterexample_id,
        "minimal_input": item.minimal_input,
        "expected": item.expected_observation,
        "actual": item.actual_observation,
        "reproduction_recipe_id": item.reproduction_recipe_id,
        "candidate_repair_cut_ids": list(item.candidate_repair_cut_ids),
        "preservation_path_ids": list(item.preservation_path_ids),
        "uncertain_information": list(item.uncertain_information),
    } for item in state.counterexamples[-20:])
    divergences = tuple(
        paired.first_divergence
        for paired in state.trace_bundles.values()
        if paired.first_divergence is not None
    )
    slice_files = sorted(state.program_graph.file_index)
    slice_summary = {
        "files": slice_files,
        "node_count": len(state.program_graph.nodes),
        "edge_count": len(state.program_graph.edges),
        "callable_count": len(state.program_graph.cfgs),
        "frontiers": [
            {"kind": item.kind, "reason": item.reason, "hard": item.hard}
            for item in state.program_graph.frontiers.values()
        ][:20],
        "symbols": sorted(state.program_graph.symbol_index)[:200],
    }
    cuts = tuple({
        "unit_id": unit.unit_id,
        "node_ids": list(unit.repair_cut_node_ids),
        "path_obligation_id": unit.path_obligation_id,
    } for unit in state.binding_graph.units.values() if unit.repair_cut_node_ids)
    impacts = tuple({
        "unit_id": unit.unit_id,
        "node_ids": list(unit.impact_cone_node_ids[:100]),
        "preservation_node_ids": list(unit.preservation_node_ids[:100]),
    } for unit in state.binding_graph.units.values() if unit.impact_cone_node_ids)
    passes = tuple({
        "outcome_id": item.outcome_id,
        "path_obligation_id": item.path_obligation_id,
        "observation": item.observation,
    } for item in state.outcomes.values() if item.status == OutcomeStatus.PASS and item.kind == "PRESERVATION")
    failed_mechanisms = tuple(sorted({
        attempt.mechanism_class
        for attempts in state.mechanism_memory.values() for attempt in attempts
        if attempt.result != "COMMIT"
    } | set(state.runtime_metrics.get("failed_generator_mechanisms", ()))))
    return RepairContext(
        mode=mode, issue=_issue_text(state),
        working_diff=state.checkpoint.patch.canonical_diff,
        requirement_coverage=tuple(coverage), failed_checks=failed,
        counterexamples=packets, first_trace_divergences=divergences,
        active_program_slice=slice_summary, causal_repair_cuts=cuts,
        impact_risks=impacts, preserved_passes=passes,
        failed_mechanisms=failed_mechanisms,
        remaining_budget=state.remaining_budget.to_dict(),
    )
