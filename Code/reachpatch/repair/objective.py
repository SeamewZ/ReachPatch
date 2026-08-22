from __future__ import annotations

from pathlib import Path

from reachpatch.execution.worktree import diff_between
from reachpatch.models.base import stable_id
from reachpatch.models.evidence import ConfirmedFailure, PairClassification
from reachpatch.models.graphs import ChallengeStatus, ProgramNodeKind
from reachpatch.models.reach_avoid import ReachAvoidState, RepairObjective


_REPAIR_NODE_KIND_PRIORITY = {
    ProgramNodeKind.METHOD: 0,
    ProgramNodeKind.STATE_READ: 1,
    ProgramNodeKind.STATE_WRITE: 2,
    ProgramNodeKind.CALL_SITE: 3,
    ProgramNodeKind.BRANCH: 4,
    ProgramNodeKind.RETURN: 5,
    ProgramNodeKind.FUNCTION: 6,
    ProgramNodeKind.RAISE: 7,
    ProgramNodeKind.EXTERNAL_EFFECT: 8,
}


def _failed_mechanism_records(
    state: ReachAvoidState,
    *,
    patch_hash: str,
    related_signatures: set[str],
) -> tuple[dict, ...]:
    """Return canonical failed-edit identities for the current repair parent."""

    records: list[dict] = []
    for signature, history in state.failure_history.items():
        if signature not in related_signatures:
            continue
        records.extend(
            dict(record) for record in history.mechanism_failures
            if isinstance(record, dict)
            and record.get("source_patch_hash") == patch_hash
        )
    records.extend(
        {
            "mechanism_id": stable_id(
                "failed-mechanism", event.get("source_patch_hash"),
                event.get("trial_patch_hash"), event.get("incremental_diff_hash"),
            ),
            "source_patch_hash": event.get("source_patch_hash"),
            "trial_patch_hash": event.get("trial_patch_hash"),
            "incremental_diff_hash": event.get("incremental_diff_hash"),
            "cumulative_diff_hash": event.get("cumulative_diff_hash"),
            "changed_files": event.get("changed_files", ()),
            "changed_hunk_ids": event.get("changed_hunk_ids", ()),
            "changed_symbols": event.get("changed_symbols", ()),
            "validation": event.get("validation", {}),
            "executed_classifications": event.get("executed_classifications", ()),
            "rejection_reasons": event.get("rejection_reasons", ()),
            "remaining_failure_signature": event.get(
                "remaining_failure_signature"
            ),
        }
        for event in state.generator_session.attempt_history
        if event.get("result_kind") == "REJECTED_BY_TRANSITION"
        and event.get("source_patch_hash") == patch_hash
        and event.get("remaining_failure_signature") in related_signatures
    )
    unique = {
        str(record.get("mechanism_id") or stable_id("failed-mechanism", record)): record
        for record in records
    }
    return tuple(unique[key] for key in sorted(unique))


def _prioritized_node_ids(
    graph,
    node_ids,
    *,
    limit: int = 12,
    preferred_operations: tuple[str, ...] = (),
) -> tuple[str, ...]:
    unique = {
        node_id for node_id in node_ids
        if node_id in graph.nodes
        and graph.nodes[node_id].kind in _REPAIR_NODE_KIND_PRIORITY
    }
    normalized_operations = tuple(
        operation.casefold() for operation in preferred_operations if operation
    )

    def operation_rank(node) -> int:
        symbol = node.symbol.casefold()
        if any(symbol == operation for operation in normalized_operations):
            return 0
        if any(symbol.startswith(f"{operation}.") for operation in normalized_operations):
            return 1
        return 2

    return tuple(
        node.node_id
        for node in sorted(
            (graph.nodes[node_id] for node_id in unique),
            key=lambda node: (
                not node.editable,
                operation_rank(node),
                _REPAIR_NODE_KIND_PRIORITY[node.kind],
                node.path,
                node.start_line,
                node.end_line,
                node.node_id,
            ),
        )[:limit]
    )


def _node_summaries(
    graph,
    node_ids,
    *,
    limit: int = 12,
    preferred_operations: tuple[str, ...] = (),
) -> tuple[dict, ...]:
    metadata_keys = {
        "attribute_name", "call_form", "method_binding", "owner_scope",
        "protocol", "receiver",
    }
    return tuple({
        "node_id": node.node_id,
        "kind": node.kind,
        "path": node.path,
        "symbol": node.symbol,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "editable": node.editable,
        "metadata": {
            key: value for key, value in node.metadata.items()
            if key in metadata_keys
        },
    } for node in (
        graph.nodes[node_id]
        for node_id in _prioritized_node_ids(
            graph, node_ids, limit=limit,
            preferred_operations=preferred_operations,
        )
    ))


def _changed_node_ids(graph, actual_diff) -> frozenset[str]:
    return frozenset(
        node.node_id
        for node in graph.nodes.values()
        for hunk in actual_diff.hunks
        if node.path == hunk.path
        and any(
            node.start_line <= line <= node.end_line
            for line in (hunk.changed_new_lines or (hunk.new_start,))
        )
    )


def _binding_symbol_ids(binding_ids, binding_graph) -> frozenset[str]:
    return frozenset(
        node_id
        for binding_id in binding_ids
        for unit in (binding_graph.units.get(binding_id),)
        if unit is not None
        for node_id in unit.program_symbol_ids
    )


def _executed_program_node_ids(graph, executions) -> frozenset[str]:
    direct_ids: set[str] = set()
    lines_by_path: dict[str, set[int]] = {}
    for execution in executions:
        trace = execution.patched
        for value in trace.executed_symbol_ids:
            if value in graph.nodes:
                direct_ids.add(value)
        for value in (*trace.executed_line_ids, *trace.executed_path_ids):
            path, separator, raw_line = value.rpartition(":")
            if separator and raw_line.isdigit():
                lines_by_path.setdefault(path, set()).add(int(raw_line))
    return frozenset(
        direct_ids
        | {
            node.node_id
            for node in graph.nodes.values()
            if any(
                node.start_line <= line <= node.end_line
                for line in lines_by_path.get(node.path, ())
            )
        }
    )


def _binding_objective_view(unit, program_graph, focus_node_ids) -> dict:
    focused = set(unit.program_symbol_ids).intersection(focus_node_ids)
    selected = _prioritized_node_ids(
        program_graph,
        focused or unit.program_symbol_ids,
        limit=24 if focused else 8,
    )
    value = unit.to_dict()
    value["program_symbol_ids"] = selected
    value["program_symbol_count"] = len(unit.program_symbol_ids)
    value["program_symbol_ids_truncated"] = len(selected) < len(
        unit.program_symbol_ids
    )
    value["program_symbols"] = _node_summaries(
        program_graph, selected, limit=len(selected),
    )
    return value


def _source_slice(tree: Path, path: str, start: int, end: int) -> dict:
    source = tree / path
    if not source.is_file():
        return {"path": path, "start_line": start, "end_line": end, "content": ""}
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    lower = max(1, start - 12)
    upper = min(len(lines), end + 12)
    return {
        "path": path,
        "start_line": lower,
        "end_line": upper,
        "content": "\n".join(
            f"{number}: {lines[number - 1]}"
            for number in range(lower, upper + 1)
        ),
    }


def _bounded_source_slices(
    tree: Path,
    nodes: list,
    *,
    max_slices: int = 24,
    max_characters: int = 64000,
) -> tuple[dict, ...]:
    spans: list[dict] = []
    seen_nodes: set[str] = set()
    line_counts: dict[str, int] = {}
    for priority, node in enumerate(nodes):
        if node.node_id in seen_nodes or not node.editable:
            continue
        seen_nodes.add(node.node_id)
        if node.kind in {ProgramNodeKind.MODULE, ProgramNodeKind.CLASS}:
            continue
        source = tree / node.path
        if not source.is_file():
            continue
        if node.path not in line_counts:
            line_counts[node.path] = len(
                source.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        line_count = line_counts[node.path]
        spans.append({
            "path": node.path,
            "start": max(1, node.start_line - 12),
            "end": min(line_count, node.end_line + 12),
            "priority": priority,
        })
    merged: list[dict] = []
    for span in sorted(spans, key=lambda item: (
        item["path"], item["start"], item["end"], item["priority"],
    )):
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous["path"] == span["path"]
            and span["start"] <= previous["end"] + 4
            and max(previous["end"], span["end"]) - previous["start"] <= 240
        ):
            previous["end"] = max(previous["end"], span["end"])
            previous["priority"] = min(previous["priority"], span["priority"])
        else:
            merged.append(dict(span))
    result: list[dict] = []
    used_characters = 0
    for span in sorted(merged, key=lambda item: (
        item["priority"], item["path"], item["start"], item["end"],
    )):
        rendered = _source_slice(
            tree, span["path"], span["start"] + 12, span["end"] - 12,
        )
        size = len(rendered["content"])
        if result and used_characters + size > max_characters:
            continue
        result.append(rendered)
        used_characters += size
        if len(result) >= max_slices or used_characters >= max_characters:
            break
    return tuple(result)


def compile_repair_objective(
    state: ReachAvoidState,
    failure: ConfirmedFailure,
) -> RepairObjective:
    graph = state.graph_stack
    requirement = graph.requirement_graph.leaves[failure.requirement_id]
    related_failures = tuple(
        item for item in state.confirmed_failures
        if item.open and item.patch_hash == graph.patch_hash
        and (
            item.requirement_id == failure.requirement_id
            or item.causal_component_id == failure.causal_component_id
        )
    )
    related_counterexample_ids = {item.counterexample_id for item in related_failures}
    packets = tuple(
        packet for packet in state.counterexamples
        if packet.patch_hash == graph.patch_hash
        and (
            packet.counterexample_id in related_counterexample_ids
            or packet.requirement_id == failure.requirement_id
            or failure.causal_component_id in packet.causal_cut_ids
        )
    )
    target_requirement_ids = {
        leaf.requirement_id
        for leaf in graph.requirement_graph.leaves.values()
        if not leaf.preservation
    }
    packet_hunk_ids = {
        hunk_id for packet in packets for hunk_id in packet.changed_hunk_ids
    }
    target_execution_candidates = []
    for challenge_id, execution in sorted(state.observations.by_challenge.items()):
        cell = graph.challenge_graph.cells.get(challenge_id)
        if (
            cell is None
            or cell.patch_hash != graph.patch_hash
            or execution.patch_hash != graph.patch_hash
            or execution.oracle_authority not in {"A", "B", "C"}
            or execution.stable_runs < 2
            or cell.terminal_status is not ChallengeStatus.PASS
        ):
            continue
        leaf = graph.requirement_graph.leaves.get(cell.requirement_id)
        unit = graph.binding_graph.units.get(cell.binding_id)
        if leaf is None or leaf.preservation or unit is None:
            continue
        if packet_hunk_ids and not packet_hunk_ids.intersection(unit.changed_hunk_ids):
            continue
        target_execution_candidates.append((cell, unit, execution))
    locked_target_ids = state.locked_checks.target_ids
    target_executions = [
        item for item in target_execution_candidates
        if item[2].classification is PairClassification.TARGET_FIXED
        or item[0].input_recipe.source_check_id in locked_target_ids
        or bool(set(item[1].target_check_ids).intersection(locked_target_ids))
    ]
    protected_requirement_ids = {
        cell.requirement_id for cell, _, _ in target_executions
    }
    for item in target_execution_candidates:
        cell, _, _ = item
        if cell.requirement_id in protected_requirement_ids:
            continue
        target_executions.append(item)
        protected_requirement_ids.add(cell.requirement_id)
    locked_check_ids = state.locked_checks.target_ids | state.locked_checks.preservation_ids
    failure_binding_ids = tuple(dict.fromkeys(
        tuple(item.binding_id for item in related_failures)
        + tuple(packet.binding_id for packet in packets)
    ) or (failure.binding_id,))
    protected_target_binding_ids = tuple(dict.fromkeys(
        unit.binding_id for _, unit, _ in target_executions
    ))
    repair_binding_ids = tuple(dict.fromkeys(
        failure_binding_ids
        + tuple(
            unit.binding_id for _, unit, _ in target_executions
        )
    ) or (failure.binding_id,))
    locked_binding_ids = tuple(
        unit.binding_id
        for unit in graph.binding_graph.units.values()
        if locked_check_ids.intersection(
            unit.target_check_ids + unit.preservation_check_ids
        )
    )
    binding_ids = tuple(dict.fromkeys(
        repair_binding_ids
        + tuple(
            locked_binding_ids
        )
    ) or (failure.binding_id,))
    cut_ids = tuple(dict.fromkeys(
        cut_id for packet in packets for cut_id in packet.causal_cut_ids
    ))
    cuts = tuple(
        graph.program_graph.causal_cuts[item].to_dict()
        for item in cut_ids if item in graph.program_graph.causal_cuts
    )
    tree = Path(state.working_checkpoint.snapshot_tree)
    actual = diff_between(state.base_repository, tree)
    causal_failure_node_ids = frozenset(
        node_id
        for cut in cuts
        for node_id in (
            cut["earliest_editable_node_id"], *cut["responsible_node_ids"],
        )
        if node_id in graph.program_graph.nodes
    )
    failing_symbol_ids = (
        _binding_symbol_ids(failure_binding_ids, graph.binding_graph)
        | causal_failure_node_ids
    )
    protected_target_binding_symbol_ids = _binding_symbol_ids(
        protected_target_binding_ids, graph.binding_graph,
    )
    protected_target_trace_symbol_ids = _executed_program_node_ids(
        graph.program_graph,
        tuple(execution for _, _, execution in target_executions),
    )
    # Dynamic BindingUnits stay intentionally sparse around the observed
    # target consumer. A changed upstream causal node can still be shared when
    # the protected target trace actually executed it.
    protected_target_symbol_ids = (
        protected_target_binding_symbol_ids
        | (protected_target_trace_symbol_ids & failing_symbol_ids)
    )
    target_only_symbol_ids = protected_target_symbol_ids - failing_symbol_ids
    failure_only_symbol_ids = failing_symbol_ids - protected_target_symbol_ids
    shared_symbol_ids = failing_symbol_ids & protected_target_symbol_ids
    shared_changed_symbol_ids = shared_symbol_ids & _changed_node_ids(
        graph.program_graph, actual,
    )
    target_only_summaries = _node_summaries(
        graph.program_graph, target_only_symbol_ids,
        preferred_operations=tuple(
            graph.requirement_graph.leaves[cell.requirement_id].operation
            for cell, _, _ in target_executions
            if cell.requirement_id in graph.requirement_graph.leaves
        ),
    )
    failure_only_summaries = _node_summaries(
        graph.program_graph, failure_only_symbol_ids,
        preferred_operations=(requirement.operation,),
    )
    shared_changed_summaries = _node_summaries(
        graph.program_graph, shared_changed_symbol_ids,
    )
    cut_node_ids = tuple(dict.fromkeys(
        node_id
        for cut in cuts
        for node_id in (
            cut["earliest_editable_node_id"],
            *cut["responsible_node_ids"],
            *cut["preservation_consumer_ids"],
        )
    ))
    cut_summaries = _node_summaries(
        graph.program_graph, cut_node_ids,
        preferred_operations=(requirement.operation,),
    )
    guided_node_ids = tuple(dict.fromkeys(
        tuple(item["node_id"] for item in target_only_summaries)
        + tuple(item["node_id"] for item in shared_changed_summaries)
        + tuple(item["node_id"] for item in failure_only_summaries)
        + tuple(item["node_id"] for item in cut_summaries)
    ))
    objective_focus_node_ids = frozenset(
        guided_node_ids
    ) | _changed_node_ids(graph.program_graph, actual)
    bindings = tuple(
        _binding_objective_view(
            graph.binding_graph.units[item], graph.program_graph,
            objective_focus_node_ids,
        )
        for item in binding_ids if item in graph.binding_graph.units
    )
    editable_nodes = [
        graph.program_graph.nodes[node_id]
        for node_id in guided_node_ids
        if graph.program_graph.nodes[node_id].editable
    ]
    if not editable_nodes:
        fallback_ids = _prioritized_node_ids(
            graph.program_graph,
            _binding_symbol_ids(repair_binding_ids, graph.binding_graph),
            limit=24,
        )
        editable_nodes = [
            graph.program_graph.nodes[node_id] for node_id in fallback_ids
            if graph.program_graph.nodes[node_id].editable
        ]
    slices = _bounded_source_slices(tree, editable_nodes)
    relevant_hunk_ids = set(
        hunk_id for packet in packets for hunk_id in packet.changed_hunk_ids
    )
    hunks = tuple(
        hunk.to_dict() for hunk in actual.hunks
        if not relevant_hunk_ids or hunk.hunk_id in relevant_hunk_ids
    )
    locked_requirement_ids = {
        unit.requirement_id
        for unit in graph.binding_graph.units.values()
        if locked_check_ids.intersection(
            unit.target_check_ids + unit.preservation_check_ids
        )
    }
    related_requirement_ids = {
        failure.requirement_id,
        *(packet.requirement_id for packet in packets),
        *locked_requirement_ids,
        *target_requirement_ids,
    }
    preservation = tuple(
        leaf.to_dict() for leaf in graph.requirement_graph.leaves.values()
        if leaf.requirement_id in related_requirement_ids
        and (
            leaf.preservation
            or leaf.requirement_id in locked_requirement_ids
        )
    )
    all_failed_mechanisms = _failed_mechanism_records(
        state,
        patch_hash=graph.patch_hash,
        related_signatures={item.failure_signature for item in related_failures},
    )
    guarded = tuple(dict.fromkeys(
        node_id
        for packet in packets
        for path_id in packet.executed_path_ids
        if path_id in graph.program_graph.path_classes
        for node_id in graph.program_graph.path_classes[path_id].node_ids
        if node_id in graph.program_graph.nodes
        and graph.program_graph.nodes[node_id].kind is ProgramNodeKind.BRANCH
    ))
    counterexample_observations = tuple({
        "evidence_kind": "COUNTEREXAMPLE",
        "counterexample_id": packet.counterexample_id,
        "expected": packet.expected_relation,
        "baseline": packet.baseline_observation,
        "actual": packet.patched_observation,
        "failure_signature": packet.failure_signature,
        "first_divergence": packet.first_divergence,
        "failure_path_symbols": _node_summaries(
            graph.program_graph,
            graph.binding_graph.units[packet.binding_id].program_symbol_ids
            if packet.binding_id in graph.binding_graph.units else (),
            preferred_operations=(
                graph.requirement_graph.leaves[packet.requirement_id].operation,
            ) if packet.requirement_id in graph.requirement_graph.leaves else (),
        ),
        "shared_changed_symbols": shared_changed_summaries,
    } for packet in packets)
    target_observations = tuple({
        "evidence_kind": "PROTECTED_TARGET_EXECUTION",
        "challenge_id": cell.challenge_id,
        "requirement_id": cell.requirement_id,
        "binding_id": cell.binding_id,
        "paired_trace_bundle_id": execution.paired_bundle_id,
        "oracle_id": execution.oracle_id,
        "oracle_authority": execution.oracle_authority,
        "expected": execution.expected_relation,
        "baseline": execution.baseline.observation.to_dict(),
        "actual": execution.patched.observation.to_dict(),
        "classification": execution.classification,
        "first_divergence": None,
        "target_only_path_symbols": _node_summaries(
            graph.program_graph,
            set(unit.program_symbol_ids) - failing_symbol_ids,
        ),
        "shared_changed_symbols": shared_changed_summaries,
    } for cell, _, execution in target_executions)
    observations = counterexample_observations + target_observations
    pending_kind = next((
        str(event["pending_objective_kind"])
        for event in reversed(state.generator_session.conversation)
        if event.get("patch_hash") == graph.patch_hash
        and event.get("pending_objective_kind")
    ), "CONFIRMED_FAILURE")
    if requirement.preservation:
        pending_kind = "PRESERVATION_REGRESSION"
    causal_direction = []
    if requirement.preservation and target_only_summaries:
        causal_direction.append(
            "Keep the failing preservation consumer baseline-equivalent and "
            "relocate or narrow the target transformation to target-only consumer "
            "nodes before changing shared state."
        )
        causal_direction.append(
            "The next cumulative patch must do both in one revision: restore the "
            "failing preservation observation and retain the already passing target "
            "observation at the target-only path; do not alternate between two "
            "single-observation patches."
        )
    elif shared_changed_summaries:
        causal_direction.append(
            "Repair the smallest shared changed causal node that explains the first "
            "divergence while retaining every protected observation."
        )
    else:
        causal_direction.append(
            "Edit the earliest executable causal node for the failing Requirement "
            "and retain every protected observation."
        )
    causal_direction.append(
        "Run every graph-grounded reproduction command after the edit; a skipped, "
        "unknown, or unsupported observation is not evidence of success."
    )
    causal_guidance = {
        "failing_requirement_id": failure.requirement_id,
        "failing_binding_ids": failure_binding_ids,
        "protected_target_binding_ids": protected_target_binding_ids,
        "failing_path_symbols": _node_summaries(
            graph.program_graph, failing_symbol_ids,
            preferred_operations=(requirement.operation,),
        ),
        "protected_target_path_symbols": _node_summaries(
            graph.program_graph, protected_target_symbol_ids,
        ),
        "target_only_path_symbols": target_only_summaries,
        "failure_only_path_symbols": failure_only_summaries,
        "shared_changed_symbols": shared_changed_summaries,
        "causal_cut_symbols": cut_summaries,
        "direction": tuple(causal_direction),
    }
    return RepairObjective(
        objective_id=stable_id(
            "repair-objective", graph.patch_hash, failure.failure_id,
            tuple(packet.counterexample_id for packet in packets),
        ),
        objective_kind=pending_kind,
        primary_requirement=requirement.to_dict(),
        related_requirements=tuple(
            leaf.to_dict() for leaf in graph.requirement_graph.leaves.values()
            if leaf.requirement_id in related_requirement_ids
        ),
        public_context=(
            state.current_repair_objective.public_context
            if state.current_repair_objective is not None else ()
        ),
        related_failures=tuple(item.to_dict() for item in related_failures),
        counterexamples=packets,
        preservation_requirements=preservation,
        reproduction_commands=tuple(dict.fromkeys(
            tuple(packet.reproduction_command for packet in packets)
            + tuple(cell.execution_scenario.command for cell, _, _ in target_executions)
        )),
        concrete_inputs=(
            tuple(packet.concrete_input for packet in packets)
            + tuple(cell.input_recipe.concrete_input for cell, _, _ in target_executions)
        ),
        input_derivations=tuple(dict.fromkeys(
            tuple(packet.input_derivation for packet in packets)
            + tuple(cell.input_recipe.derivation for cell, _, _ in target_executions)
        )),
        oracle_relations=(
            tuple({
                "oracle_id": packet.oracle_id,
                "authority": packet.oracle_authority,
                "expected_relation": packet.expected_relation,
            } for packet in packets)
            + tuple({
                "oracle_id": execution.oracle_id,
                "authority": execution.oracle_authority,
                "expected_relation": execution.expected_relation,
                "challenge_id": cell.challenge_id,
            } for cell, _, execution in target_executions)
        ),
        observations=observations,
        failure_signatures=tuple(packet.failure_signature for packet in packets),
        first_divergences=tuple(packet.first_divergence for packet in packets),
        executed_path_ids=tuple(dict.fromkeys(
            item for packet in packets for item in packet.executed_path_ids
        ) | dict.fromkeys(
            unit.path_class_id for _, unit, _ in target_executions
        )),
        guarded_branch_ids=guarded,
        causal_guidance=causal_guidance,
        bindings=bindings,
        actual_hunks=hunks,
        causal_cuts=cuts,
        impact_cone=(
            graph.program_graph.impact_cone.to_dict()
            if graph.program_graph.impact_cone is not None else None
        ),
        impact_risks=tuple(dict.fromkeys(
            item for packet in packets for item in packet.impact_risk_ids
        )),
        protected_target_ids=tuple(dict.fromkeys(
            tuple(sorted(state.locked_checks.target_ids))
            + tuple(
                item for packet in packets for item in packet.protected_target_ids
            )
            + tuple(cell.challenge_id for cell, _, _ in target_executions)
        )),
        protected_preservation_ids=tuple(dict.fromkeys(
            tuple(sorted(state.locked_checks.preservation_ids))
            + tuple(
                item
                for packet in packets
                for item in packet.protected_preservation_ids
            )
        )),
        suggested_action_families=tuple(dict.fromkeys(
            item
            for packet in packets
            for item in packet.suggested_action_families
        )),
        locked_check_ids=state.locked_checks.all_ids(),
        cumulative_diff=actual.canonical_diff,
        failed_mechanisms=all_failed_mechanisms,
        forbidden_mechanisms=all_failed_mechanisms,
        editable_source_slices=slices,
        expected_next_effects=tuple(dict.fromkeys(
            tuple(
                f"Close {packet.failure_signature} after causal cut {cut_id}"
                for packet in packets
                for cut_id in (packet.causal_cut_ids or ("unlocalized",))
            )
            + tuple(
                f"Satisfy target Requirement {leaf.requirement_id}: "
                f"{leaf.expected_observation.relation}"
                for leaf in graph.requirement_graph.leaves.values()
                if not leaf.preservation
            )
            + tuple(causal_direction)
        )),
    )
