from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from reachpatch.execution.worktree import diff_between
from reachpatch.models.base import stable_id
from reachpatch.models.evidence import (
    ConfirmedFailure, ObservationContract, PairClassification,
)
from reachpatch.models.graphs import ChallengeStatus, InputRecipe, ProgramNodeKind
from reachpatch.models.reach_avoid import (
    AtomicObligation, ReachAvoidState, RepairObjective, ValidationObligation,
    atomic_obligation_key,
)
from reachpatch.reach_avoid.frontier import RepairFrontier, RepairFrontierKind


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


def mechanical_validation_obligation(
    *,
    requirement_id: str = "__mechanical__",
    source_paths: tuple[str, ...] = (),
    binding_id: str | None = None,
    challenge_id: str | None = None,
    source: str = "repair-objective",
) -> ValidationObligation:
    """Build the explicit mechanical validation required for every edit.

    The command remains deliberately bounded to files already selected for the
    objective.  Falling back to the working directory keeps an initial patch
    mechanically validated even before a source slice has been recovered.
    """
    paths = tuple(dict.fromkeys(
        path for path in source_paths
        if path and not Path(path).is_absolute() and Path(path).suffix == ".py"
    ))
    command = ("python", "-m", "compileall", "-q", *(paths or (".",)))
    return ValidationObligation(
        validation_id=stable_id(
            "mechanical-validation", requirement_id, binding_id, challenge_id,
            command,
        ),
        role="MECHANICAL", authority="A", command=command, cwd=".",
        environment={}, timeout_seconds=120, backend="shared-executor",
        concrete_input={"source_paths": paths or (".",)},
        input_derivation=(
            f"Bounded Python parse/import validation from {source}"
        ),
        oracle_id=stable_id("mechanical-oracle", command),
        expected_relation="selected Python sources compile successfully",
        expected_observation={"exit_code": 0},
        requirement_id=requirement_id, binding_id=binding_id,
        challenge_id=challenge_id,
    )


def validation_obligation_from_challenge(cell, *, source: str) -> ValidationObligation:
    """Preserve a challenge command, input and contract as one object.

    This is intentionally the only conversion from a graph challenge into a
    generator validation.  It prevents command/input/oracle arrays from being
    independently deduplicated and accidentally re-zipped by index.
    """
    role = (
        "PRESERVATION" if cell.kind == "PRESERVATION"
        else "IMPACT" if cell.kind in {"IMPACT", "PROTOCOL"}
        else "TARGET"
    )
    return ValidationObligation(
        validation_id=stable_id(
            "challenge-validation", cell.requirement_id, role,
            cell.input_recipe.recipe_id, cell.observation_contract.contract_id,
        ),
        role=role, authority=cell.oracle.authority,
        command=tuple(cell.execution_scenario.command),
        cwd=cell.execution_scenario.cwd,
        environment=dict(cell.execution_scenario.environment),
        timeout_seconds=int(cell.execution_scenario.timeout_seconds),
        backend="shared-executor",
        concrete_input=cell.input_recipe.concrete_input,
        input_derivation="; ".join(cell.input_recipe.derivation) or source,
        oracle_id=cell.oracle.oracle_id,
        expected_relation=cell.oracle.relation,
        expected_observation=cell.oracle.expected,
        requirement_id=cell.requirement_id, binding_id=cell.binding_id,
        challenge_id=cell.challenge_id,
    )


def atomic_obligation_from_validation(
    obligation: ValidationObligation,
) -> AtomicObligation:
    """Create the semantic execution object consumed by triplet runners."""
    if obligation.role == "MECHANICAL":
        contract = ObservationContract(
            obligation.expected_relation or "Python sources compile successfully",
            obligation.expected_observation or {"exit_code": 0},
            observable="process", comparator="EXIT_ZERO",
        )
    else:
        expected = obligation.expected_observation
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
        requirement_contract_id=(obligation.oracle_id or contract.contract_id),
        role=(obligation.role if obligation.role in {
            "TARGET", "PRESERVATION", "IMPACT", "MECHANICAL",
        } else "TARGET"),
        input_recipe=recipe,
        input_partition_id=(obligation.challenge_id or stable_id(
            "validation-partition", obligation.requirement_id, obligation.role,
            obligation.command, obligation.concrete_input,
        )),
        oracle_contract=contract,
        authority=(obligation.authority if obligation.authority in {
            "A", "B", "C", "PROVISIONAL",
        } else "PROVISIONAL"),
        hard=True, source="repair-objective-validation",
    )
    return replace(raw, key=atomic_obligation_key(raw))


def deduplicate_validation_obligations(
    obligations: list[ValidationObligation] | tuple[ValidationObligation, ...],
) -> tuple[ValidationObligation, ...]:
    """Deduplicate whole validation records, never individual fields."""
    return tuple(dict((item.validation_id, item) for item in obligations).values())


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
    failure: ConfirmedFailure | RepairFrontier,
) -> RepairObjective:
    graph = state.graph_stack
    frontier = failure if isinstance(failure, RepairFrontier) else None
    if frontier is not None:
        requirement_id = next(iter(frontier.requirement_ids), None)
        if frontier.kind is RepairFrontierKind.PRESERVATION_REGRESSION:
            preservation_id = next((
                item.requirement_id for item in graph.requirement_graph.leaves.values()
                if item.preservation
            ), None)
            if preservation_id is not None:
                requirement_id = preservation_id
        if requirement_id not in graph.requirement_graph.leaves:
            requirement_id = next(iter(graph.requirement_graph.leaves), None)
        req_id = requirement_id
        # Keep the rest of the objective compiler shared with legacy failure
        # packets while making the frontier the source of truth for action.
        class _FailureView:
            failure_id = frontier.frontier_id
            requirement_id = req_id
            binding_id = next(iter(frontier.binding_ids), "")
            challenge_id = next(iter(frontier.challenge_ids), "")
            counterexample_id = ""
            patch_hash = frontier.patch_hash
            failure_signature = frontier.frontier_id
            causal_component_id = next(iter(frontier.causal_cut_ids or frontier.path_class_ids), "")
            first_divergence = frontier.failure_location
            hard = frontier.hard
            priority = frontier.priority
            open = True
        failure = _FailureView()  # type: ignore[assignment]
    else:
        requirement_id = failure.requirement_id
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
            or (frontier is not None and packet.challenge_id in frontier.challenge_ids)
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
    if frontier is not None:
        # A RepairFrontier may have no legacy ConfirmedFailure. Rejected
        # concrete diffs for its semantic key are still mandatory input to
        # the next objective.
        frontier_records = tuple(
            {
                "mechanism_id": stable_id(
                    "frontier-mechanism", event.get("incremental_diff_hash"),
                ),
                "incremental_diff_hash": event.get("incremental_diff_hash"),
                "changed_files": event.get("changed_files", ()),
                "changed_hunk_ids": event.get("changed_hunk_ids", ()),
                "rejection_reasons": event.get("rejection_reasons", ()),
            }
            for event in state.generator_session.attempt_history
            if event.get("result_kind") == "REJECTED_BY_TRANSITION"
            and event.get("source_patch_hash") == graph.patch_hash
            and event.get("selected_frontier_key") == frontier.semantic_key
        )
        merged = {
            str(item.get("mechanism_id")): item
            for item in (*all_failed_mechanisms, *frontier_records)
        }
        all_failed_mechanisms = tuple(merged[key] for key in sorted(merged))
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
        "expected_observation": packet.expected_observation,
        "baseline": packet.baseline_observation,
        "incumbent": packet.incumbent_observation,
        "actual": packet.patched_observation,
        "trial": packet.trial_observation,
        "comparator": packet.comparator,
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
    ), (
        "CONFIRMED_FAILURE" if frontier is not None and frontier.kind is RepairFrontierKind.BEHAVIOR_FAILURE
        else frontier.kind.value if frontier is not None else "CONFIRMED_FAILURE"
    ))
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
    obligations: list[ValidationObligation] = []
    for index, packet in enumerate(packets):
        cell = graph.challenge_graph.cells.get(packet.challenge_id)
        packet_requirement = graph.requirement_graph.leaves.get(
            packet.requirement_id, requirement,
        )
        is_preservation = packet_requirement.preservation
        # A target counterexample must retain a contract, even when the
        # matching ChallengeCell was superseded during an incremental graph
        # refresh.  Its incumbent/baseline output is evidence of the original
        # failure and must never become the target's expected output.
        if cell is not None and cell.oracle.executable:
            expected_observation = cell.oracle.expected
        elif not is_preservation:
            expected_observation = packet_requirement.expected_observation.expected
        else:
            # Preservation without a more specific executable oracle is the
            # one place where a stable baseline is a valid contract source.
            expected_observation = packet.baseline_observation
        obligations.append(ValidationObligation(
            validation_id=stable_id("validation", graph.patch_hash, packet.counterexample_id),
            role=("PRESERVATION" if is_preservation else "TARGET"),
            authority=packet.oracle_authority, command=tuple(packet.reproduction_command),
            cwd=".", environment={}, timeout_seconds=120, backend="shared-executor",
            concrete_input=packet.concrete_input, input_derivation="; ".join(packet.input_derivation),
            oracle_id=packet.oracle_id, expected_relation=packet.expected_relation,
            expected_observation=expected_observation, requirement_id=packet.requirement_id,
            binding_id=packet.binding_id, challenge_id=packet.challenge_id,
        ))
    for cell, unit, execution in target_executions:
        obligations.append(ValidationObligation(
            validation_id=stable_id("validation", graph.patch_hash, cell.challenge_id, execution.oracle_id),
            role="TARGET", authority=execution.oracle_authority,
            command=tuple(cell.execution_scenario.command), cwd=cell.execution_scenario.cwd,
            environment=dict(cell.execution_scenario.environment), timeout_seconds=int(cell.execution_scenario.timeout_seconds),
            backend="shared-executor", concrete_input=cell.input_recipe.concrete_input,
            input_derivation="; ".join(cell.input_recipe.derivation), oracle_id=execution.oracle_id,
            # This is a protected target validation.  The incumbent execution is
            # evidence that the target was previously satisfied, not its oracle.
            # Requiring its raw observation here turns a fixed target into a
            # validation failure whenever the old output differs from the actual
            # contract (for example an assertion check versus a direct probe).
            expected_relation=cell.oracle.relation, expected_observation=cell.oracle.expected,
            requirement_id=cell.requirement_id, binding_id=unit.binding_id, challenge_id=cell.challenge_id,
        ))
    if frontier is not None and frontier.kind is not RepairFrontierKind.MECHANICAL_FAILURE:
        selected_ids = set(frontier.challenge_ids)
        selected_validation = any(
            item.challenge_id in selected_ids
            for item in obligations
        )
        if not selected_validation:
            candidate_cells = sorted(
                (
                    cell for cell in graph.challenge_graph.active_cells()
                    if cell.execution_scenario.command
                    and (
                        cell.challenge_id in selected_ids
                        or cell.requirement_id in frontier.requirement_ids
                    )
                ),
                key=lambda cell: (
                    cell.challenge_id not in selected_ids, cell.challenge_id,
                ),
            )
            if candidate_cells:
                obligations.append(validation_obligation_from_challenge(
                    candidate_cells[0], source=(
                        f"selected {frontier.kind.value} frontier"
                    ),
                ))
                selected_validation = True
        if not selected_validation:
            # The controller only permits this fallback for a frontier with a
            # real repair slice.  It gives the selected source region a
            # measurable structural contract while evidence recovery builds a
            # behavior recipe; it cannot masquerade as target success because
            # transition execution still evaluates graph-backed obligations.
            selected_role = (
                "PRESERVATION"
                if frontier.kind is RepairFrontierKind.PRESERVATION_REGRESSION
                else "IMPACT"
                if frontier.kind is RepairFrontierKind.IMPACT_RISK
                else "TARGET"
            )
            structural = mechanical_validation_obligation(
                requirement_id=requirement.requirement_id,
                source_paths=tuple(item["path"] for item in slices),
                binding_id=next(iter(frontier.binding_ids), None),
                challenge_id=next(iter(frontier.challenge_ids), None),
                source=f"selected {frontier.kind.value} source slice",
            )
            obligations.append(replace(
                structural,
                validation_id=stable_id(
                    "frontier-structural-validation", frontier.semantic_key,
                    structural.command, selected_role,
                ),
                role=selected_role,
                input_derivation=(
                    f"Structural validation for selected {frontier.kind.value} "
                    "repair slice"
                ),
            ))
    # A compile/import check is a first-class validation obligation rather
    # than an implicit post-hoc gate.  The trial triplet still records the
    # canonical applyability result, while the agent receives an executable
    # command that it can run before finishing its revision.
    obligations.append(mechanical_validation_obligation(
        source_paths=tuple(dict.fromkeys(
            tuple(item["path"] for item in slices) + tuple(actual.changed_files)
        )),
        source="current repair objective",
    ))
    obligations = list(deduplicate_validation_obligations(obligations))
    atomic_obligations = [
        atomic_obligation_from_validation(obligation)
        for obligation in obligations
    ]
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
        validation_obligations=tuple(obligations),
        selected_frontier=frontier,
        working_patch_hash=graph.patch_hash,
        graph_revision=graph.revision,
        atomic_obligations=tuple(dict((item.key, item) for item in atomic_obligations).values()),
    )
