from __future__ import annotations

import time
from dataclasses import replace

from reachpatch.models.evidence import PairClassification, PairedTraceBundle
from reachpatch.models.graphs import (
    BindingGraph, BindingGraphDelta, BindingStatus, ProgramGraph,
    RequirementGraph,
)
from reachpatch.models.evidence import ActualDiff, ExecutableCheck
from reachpatch.program_graph.slicing import match_trace_nodes


def _executed_program_nodes(execution: PairedTraceBundle, graph: ProgramGraph) -> set[str]:
    _, matched = match_trace_nodes(graph, execution.patched)
    return set(matched)


def confirm_bindings_from_execution(
    binding_graph: BindingGraph,
    program_graph: ProgramGraph,
    requirement_graph: RequirementGraph,
    executions: tuple[PairedTraceBundle, ...],
) -> BindingGraphDelta:
    """Promote only comparable, trusted, path-hitting paired evidence."""

    started = time.monotonic()
    units = dict(binding_graph.units)
    confirmed: list[str] = []
    changed: list[str] = []
    executed_nodes = {
        execution.paired_bundle_id: _executed_program_nodes(execution, program_graph)
        for execution in executions
        if execution.patch_hash == binding_graph.patch_hash
    }
    for binding_id, unit in tuple(units.items()):
        requirement = requirement_graph.leaves.get(unit.requirement_id)
        if requirement is None:
            continue
        for execution in executions:
            if execution.patch_hash != binding_graph.patch_hash:
                continue
            if not execution.comparable or execution.oracle_authority not in {"A", "B", "C"}:
                continue
            if execution.stable_runs < 2:
                continue
            if (
                execution.check_id not in unit.target_check_ids + unit.preservation_check_ids
                and execution.challenge_id not in unit.challenge_ids
            ):
                continue
            hit_nodes = executed_nodes[execution.paired_bundle_id]
            hit_path = bool(hit_nodes.intersection(unit.program_symbol_ids))
            path = program_graph.path_classes.get(unit.path_class_id)
            if path is not None:
                hit_path = hit_path and bool(hit_nodes.intersection(path.node_ids))
            if not hit_path or execution.classification is PairClassification.UNKNOWN:
                continue
            relation_matches = (
                execution.expected_relation == requirement.expected_observation.relation
                or (
                    requirement.preservation
                    and execution.oracle_authority == "C"
                    and execution.expected_relation.startswith(
                        "patched observation preserves stable baseline"
                    )
                )
            )
            if not relation_matches:
                continue
            if execution.classification is PairClassification.TARGET_FIXED or (
                requirement.preservation
                and execution.classification is PairClassification.PASS_PRESERVED
            ):
                status = BindingStatus.TARGET_PASSING
            elif execution.classification is PairClassification.PASS_PRESERVED:
                status = BindingStatus.EXECUTION_CONFIRMED
            elif execution.classification is PairClassification.PRESERVATION_REGRESSION:
                status = BindingStatus.PRESERVATION_RISK
            else:
                status = BindingStatus.TARGET_FAILING
            units[binding_id] = replace(
                unit,
                status=status,
                trace_bundle_ids=tuple(dict.fromkeys(
                    unit.trace_bundle_ids + (execution.paired_bundle_id,)
                )),
                evidence_ids=tuple(dict.fromkeys(
                    unit.evidence_ids + (
                        execution.baseline.trace_bundle_id,
                        execution.patched.trace_bundle_id,
                    )
                )),
            )
            confirmed.append(binding_id)
            changed.append(binding_id)
    graph = BindingGraph(
        patch_hash=binding_graph.patch_hash,
        requirement_hash=requirement_graph.graph_hash(),
        program_hash=program_graph.graph_hash(),
        units=units,
        gaps=binding_graph.gaps,
    )
    return BindingGraphDelta(
        graph=graph,
        confirmed_binding_ids=tuple(dict.fromkeys(confirmed)),
        changed_binding_ids=tuple(dict.fromkeys(changed)),
        update_seconds=time.monotonic() - started,
    )


def update_binding_graph_after_diff(
    previous: BindingGraph,
    previous_program: ProgramGraph,
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    actual_diff: ActualDiff,
    public_checks: tuple[ExecutableCheck, ...],
) -> BindingGraphDelta:
    """Rebuild only binding components touched by the local Program delta."""

    from .builder import _matching_symbols, build_binding_graph

    started = time.monotonic()
    local_files = set(previous_program.file_hashes) | set(program_graph.file_hashes)
    changed_files = {
        path for path in local_files
        if previous_program.file_hashes.get(path) != program_graph.file_hashes.get(path)
    } | set(actual_diff.changed_files)
    affected_nodes = {
        node.node_id for node in previous_program.nodes.values()
        if node.path in changed_files
    } | {
        node.node_id for node in program_graph.nodes.values()
        if node.path in changed_files
    }
    affected_paths = {
        path_id for path_id, path in previous_program.path_classes.items()
        if set(path.node_ids).intersection(affected_nodes)
        or path_id not in program_graph.path_classes
    } | {
        path_id for path_id, path in program_graph.path_classes.items()
        if set(path.node_ids).intersection(affected_nodes)
        or path_id not in previous_program.path_classes
    }
    if program_graph.impact_cone is not None:
        impact_nodes = set(program_graph.impact_cone.all_risk_ids())
        affected_paths.update(
            path_id for path_id, path in program_graph.path_classes.items()
            if set(path.node_ids).intersection(impact_nodes)
        )
    affected_requirements = {
        unit.requirement_id for unit in previous.units.values()
        if unit.path_class_id in affected_paths
        or set(unit.program_symbol_ids).intersection(affected_nodes)
    } | {
        gap.requirement_id for gap in previous.gaps
    }
    affected_requirements.update(
        requirement.requirement_id
        for requirement in requirement_graph.leaves.values()
        if set(_matching_symbols(requirement.operation, program_graph)).intersection(
            affected_nodes
        )
    )
    affected_requirements.update(
        requirement_id for requirement_id in requirement_graph.leaves
        if requirement_id not in {
            unit.requirement_id for unit in previous.units.values()
        }
    )

    retained = {
        binding_id: unit
        for binding_id, unit in previous.units.items()
        if unit.requirement_id in requirement_graph.leaves
        and unit.requirement_id not in affected_requirements
        and unit.path_class_id in program_graph.path_classes
        and all(node_id in program_graph.nodes for node_id in unit.program_symbol_ids)
    }
    affected_graph = RequirementGraph(
        leaves={
            requirement_id: requirement_graph.leaves[requirement_id]
            for requirement_id in affected_requirements
            if requirement_id in requirement_graph.leaves
        },
        challenge_partitions={
            partition_id: partition
            for partition_id, partition in requirement_graph.challenge_partitions.items()
            if partition.requirement_id in affected_requirements
        },
        evidence_hash=requirement_graph.evidence_hash,
    )
    rebuilt = build_binding_graph(
        affected_graph, program_graph, actual_diff, public_checks,
    )
    units = {**retained, **rebuilt.units}
    if actual_diff.patch_hash == previous.patch_hash:
        for binding_id, unit in tuple(units.items()):
            old = previous.units.get(binding_id)
            if old is not None and old.status is not BindingStatus.STATIC_ACTIONABLE:
                units[binding_id] = replace(
                    unit,
                    program_symbol_ids=tuple(dict.fromkeys(
                        unit.program_symbol_ids
                        + tuple(
                            node_id for node_id in old.program_symbol_ids
                            if node_id in program_graph.nodes
                        )
                    )),
                    challenge_ids=old.challenge_ids,
                    status=old.status,
                    trace_bundle_ids=old.trace_bundle_ids,
                    counterexample_ids=old.counterexample_ids,
                    causal_cut_ids=tuple(dict.fromkeys(
                        unit.causal_cut_ids + old.causal_cut_ids
                    )),
                    evidence_ids=tuple(dict.fromkeys(
                        unit.evidence_ids + old.evidence_ids
                    )),
                )
    retained_gaps = tuple(
        gap for gap in previous.gaps
        if gap.requirement_id not in affected_requirements
        and gap.requirement_id in requirement_graph.leaves
    )
    gaps = tuple(dict.fromkeys(retained_gaps + rebuilt.gaps))
    graph = BindingGraph(
        patch_hash=actual_diff.patch_hash,
        requirement_hash=requirement_graph.graph_hash(),
        program_hash=program_graph.graph_hash(),
        units=units,
        gaps=gaps,
    )
    changed_ids = tuple(sorted(
        binding_id for binding_id in set(previous.units) | set(units)
        if previous.units.get(binding_id) != units.get(binding_id)
    ))
    return BindingGraphDelta(
        graph=graph,
        confirmed_binding_ids=(),
        changed_binding_ids=changed_ids,
        update_seconds=time.monotonic() - started,
    )
