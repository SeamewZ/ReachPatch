from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.graphs import BindingGap, BindingGraph, BindingUnit, RequirementGraph
from reachpatch.binding_graph.builder import build_binding_graph


@dataclass(frozen=True, slots=True)
class ActiveBindingGraph(SerializableRecord):
    graph: BindingGraph
    preservation_checks: tuple[Any, ...] = ()

    @property
    def units(self):
        return self.graph.units

    @property
    def gaps(self):
        return self.graph.gaps

    def graph_hash(self) -> str:
        return self.graph.graph_hash()


def _scenario_values(target_recovery: Any) -> tuple[Any, ...]:
    return tuple(getattr(target_recovery, "scenarios", ())) + tuple(getattr(target_recovery, "challenge_cells", ()))


def build_active_binding_graph(
    requirement_graph: RequirementGraph, program_slice: Any, diff: Any,
    target_recovery: Any, public_checks: Sequence[Any],
    previous_graph: ActiveBindingGraph | BindingGraph | None,
) -> ActiveBindingGraph:
    """Construct only trace-, diff-, or public-check-grounded bindings."""
    program = getattr(program_slice, "graph", program_slice)
    built = build_binding_graph(requirement_graph, program, diff, tuple(public_checks))
    scenarios = _scenario_values(target_recovery)
    traced_symbols = {str(symbol) for item in scenarios for symbol in getattr(item, "trace_symbols", ())}
    traced_symbols.update(str(symbol) for item in scenarios for symbol in getattr(item, "executed_symbol_ids", ()))
    changed_files = set(getattr(diff, "changed_files", ()))
    public_symbols = {str(symbol) for check in public_checks for symbol in getattr(check, "symbol_references", ())}
    changed_hunks = {hunk.hunk_id for hunk in getattr(diff, "hunks", ())}
    units: dict[str, BindingUnit] = {}
    for binding_id, unit in built.units.items():
        symbols = set(unit.program_symbol_ids)
        path = program.path_classes.get(unit.path_class_id)
        path_symbols = set(path.node_ids) if path is not None else set()
        touched = bool(
            symbols.intersection(traced_symbols) or path_symbols.intersection(traced_symbols)
            or symbols.intersection(public_symbols)
            or set(unit.changed_hunk_ids).intersection(changed_hunks)
            or any(getattr(program.nodes.get(node_id), "path", "") in changed_files for node_id in symbols if node_id in program.nodes)
        )
        if touched:
            units[binding_id] = unit
    # Preserve unaffected executable bindings across a local diff refresh. The
    # unit is still tied to the new program hash in the enclosing graph, while
    # its evidence/path identity remains stable until a changed hunk or trace
    # requires recomputation.
    previous_units = getattr(getattr(previous_graph, "graph", previous_graph), "units", {}) if previous_graph is not None else {}
    for binding_id, old_unit in previous_units.items():
        if binding_id in units:
            continue
        if any(hunk_id in changed_hunks for hunk_id in getattr(old_unit, "changed_hunk_ids", ())):
            continue
        if old_unit.requirement_id not in requirement_graph.leaves:
            continue
        units[binding_id] = old_unit
    gaps = list(built.gaps)
    present_requirements = {unit.requirement_id for unit in units.values()}
    for requirement in requirement_graph.leaves.values():
        if requirement.requirement_id in present_requirements:
            continue
        if not any(requirement.operation and requirement.operation in str(getattr(node, "symbol", "")) for node in program.nodes.values()):
            gap_type = "NO_ENTRYPOINT"
        elif not scenarios:
            gap_type = "NO_EXECUTABLE_TARGET"
        else:
            gap_type = "SLICE_FRONTIER"
        gaps.append(BindingGap(
            requirement_id=requirement.requirement_id, gap_type=gap_type, hard=requirement.hard,
            attempted_symbols=(requirement.operation,), next_recovery_actions=(),
            gap_id=stable_id("binding-gap", requirement.requirement_id, gap_type),
        ))
    graph = BindingGraph(
        patch_hash=getattr(diff, "patch_hash", built.patch_hash),
        requirement_hash=requirement_graph.graph_hash(), program_hash=program.graph_hash(),
        units=units, gaps=tuple(dict.fromkeys(gaps)),
    )
    preservation = tuple(check for check in public_checks if getattr(check, "role", "") == "PRESERVATION")
    return ActiveBindingGraph(graph, preservation)


def update_active_binding_graph(
    requirement_graph: RequirementGraph, program_slice: Any, diff: Any, target_recovery: Any,
    public_checks: Sequence[Any], previous_graph: ActiveBindingGraph | None,
) -> ActiveBindingGraph:
    return build_active_binding_graph(requirement_graph, program_slice, diff, target_recovery, public_checks, previous_graph)
