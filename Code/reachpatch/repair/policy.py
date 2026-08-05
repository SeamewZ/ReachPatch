from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from reachpatch.models.base import stable_id
from reachpatch.models.controller import LosingCore, ReachAvoidState, RepairIntent, UnitOutcome
from reachpatch.models.enums import OutcomeStatus


_STATUS_PRESSURE = {
    OutcomeStatus.FAIL: 4.0,
    OutcomeStatus.BLOCKED: 3.0,
    OutcomeStatus.UNKNOWN: 2.5,
    OutcomeStatus.FLAKY: 2.5,
    OutcomeStatus.UNKNOWN_EXECUTION: 2.5,
    OutcomeStatus.UNKNOWN_ORACLE: 2.0,
    OutcomeStatus.BLOCKED_EXTERNAL: 2.0,
    OutcomeStatus.UNSUPPORTED: 2.0,
}


@dataclass(frozen=True, slots=True)
class EditScopeDecision:
    allowed: bool
    touched_causal_cut: bool
    unexplained_files: tuple[str, ...]
    unexplained_symbols: tuple[str, ...]
    public_api_changes: tuple[str, ...]
    signature_changes: tuple[str, ...]
    shared_utility_changes: tuple[str, ...]
    base_class_changes: tuple[str, ...]


def accept_edit_scope(action: Any, trial_diff: Any, state: ReachAvoidState) -> EditScopeDecision:
    """Validate that one revision remains inside its execution-backed component."""

    action_data = action if isinstance(action, dict) else (
        action.to_dict() if hasattr(action, "to_dict") else {}
    )
    allowed_files = set(map(str, action_data.get("files_to_modify", ())))
    causal_cut_ids = set(map(str, (
        *action_data.get("repair_cut_ids", ()),
        *action_data.get("causal_cut_ids", ()),
    )))
    graph = getattr(state, "program_graph", None)
    cut_nodes = tuple(
        graph.nodes[node_id]
        for node_id in causal_cut_ids
        if graph is not None and node_id in getattr(graph, "nodes", {})
    )
    cut_files = {
        str(node.attributes.get("file", ""))
        for node in cut_nodes if node.attributes.get("file")
    }
    cut_symbols = {
        str(node.attributes.get("qualified_name", node.label))
        for node in cut_nodes
    }
    allowed_files.update(cut_files)
    changed_files = set(map(str, getattr(trial_diff, "changed_files", ())))
    changed_relations = tuple(getattr(trial_diff, "changed_relations", ()))
    relation_ids = {str(getattr(item, "relation_id", "")) for item in changed_relations}
    relation_scopes = {
        str(getattr(item, "qualified_scope", "")) for item in changed_relations
        if getattr(item, "qualified_scope", "")
    }
    def same_symbol(left: str, right: str) -> bool:
        return bool(left and right) and (
            left == right
            or left.startswith(right + ".")
            or right.startswith(left + ".")
            or left.endswith("." + right)
            or right.endswith("." + left)
        )

    touched = bool(
        causal_cut_ids & relation_ids
        or changed_files & cut_files
        or any(
            same_symbol(scope, symbol)
            for scope in relation_scopes for symbol in cut_symbols
        )
    )
    unexplained_files = tuple(sorted(changed_files - allowed_files)) if allowed_files else ()
    allowed_symbols = set(map(str, action_data.get("symbols_to_modify", ())))
    allowed_symbols.update(cut_symbols)
    unexplained_symbols = tuple(sorted(
        scope for scope in relation_scopes
        if allowed_symbols and not any(
            same_symbol(scope, symbol)
            for symbol in allowed_symbols
        )
    ))
    # A failure-localized action may use a changed-relation cut from an older
    # checkpoint. Its relation ID changes when the expression changes, while
    # the qualified source scope remains the same. Treat an execution-backed
    # edit to that explicitly allowed symbol as touching the causal component.
    if not touched and causal_cut_ids and action_data.get(
        "actual_failure_execution_ids", ()
    ):
        touched = any(
            same_symbol(scope, symbol)
            for scope in relation_scopes for symbol in allowed_symbols
        )
    mechanical_localization = str(
        action_data.get("selection_witness", {}).get("failure_origin", "")
    ) == "PATCH_MECHANICAL"
    if (
        not touched
        and mechanical_localization
        and allowed_files
        and changed_files
        and changed_files.issubset(allowed_files)
    ):
        # Mechanical hunk IDs necessarily change when the malformed source is
        # corrected.  The stable causal boundary is the checked file set from
        # the executable mechanical packet, not equality of old/new hunk IDs.
        touched = True
    public_api = tuple(sorted(
        str(getattr(item, "relation_id", "")) for item in changed_relations
        if "public" in str(getattr(item, "kind", "")).lower()
    ))
    signatures = tuple(sorted(
        str(getattr(item, "relation_id", "")) for item in changed_relations
        if "signature" in str(getattr(item, "kind", "")).lower()
    ))
    shared_utility = tuple(sorted(
        path for path in changed_files
        if any(part in {"utils", "util", "common", "shared"} for part in __import__("pathlib").Path(path).parts)
        and path not in allowed_files
    ))
    base_classes = tuple(sorted(
        str(getattr(item, "relation_id", "")) for item in changed_relations
        if any(token in str(getattr(item, "kind", "")).lower() for token in (
            "base_class", "inheritance", "mro",
        ))
    ))
    execution_justified = bool(action_data.get("actual_failure_execution_ids", ()))
    privileged_risk = bool(public_api or signatures or base_classes)
    risky_unjustified = bool(
        unexplained_files or unexplained_symbols or shared_utility
        or (privileged_risk and not execution_justified)
    )
    return EditScopeDecision(
        allowed=touched and not risky_unjustified,
        touched_causal_cut=touched,
        unexplained_files=unexplained_files,
        unexplained_symbols=unexplained_symbols,
        public_api_changes=public_api,
        signature_changes=signatures,
        shared_utility_changes=shared_utility,
        base_class_changes=base_classes,
    )


def _unit_statuses(state: ReachAvoidState) -> dict[str, list[UnitOutcome]]:
    grouped: dict[str, list[UnitOutcome]] = defaultdict(list)
    for outcome in state.outcomes.values():
        grouped[outcome.unit_id].append(outcome)
    graph = getattr(state, "active_binding_graph", None)
    for unit_id in getattr(graph, "units", {}):
        grouped.setdefault(unit_id, [])
    return grouped


def select_losing_core(state: ReachAvoidState) -> LosingCore | None:
    graph = getattr(state, "active_binding_graph", None)
    if graph is None:
        return None
    grouped = _unit_statuses(state)
    protected = tuple(sorted(
        (item.path_obligation_id, item.scenario_id or "")
        for item in state.outcomes.values()
        if item.status == OutcomeStatus.PASS
    ))
    ranked: list[tuple[float, str, tuple[str, ...]]] = []
    for component in getattr(graph, "components", {}).values():
        losing = tuple(sorted(
            unit_id
            for unit_id in component.unit_ids
            if not grouped[unit_id]
            or any(item.status != OutcomeStatus.PASS for item in grouped[unit_id])
        ))
        if not losing:
            continue
        pressure = 0.0
        for unit_id in losing:
            leaf = state.requirement_graph.leaves[graph.units[unit_id].leaf_id]
            outcomes = grouped[unit_id]
            pressure += leaf.weight
            pressure += max(
                (_STATUS_PRESSURE.get(item.status, 1.0) for item in outcomes),
                default=3.0,
            )
            pressure += len(getattr(graph.units[unit_id], "bypass_path_ids", ())) * 0.5
            pressure += len(getattr(graph.units[unit_id], "frontier_ids", ())) * 0.75
        ranked.append((pressure, component.component_id, losing))
    if not ranked:
        return None
    pressure, component_id, losing = max(ranked, key=lambda item: (item[0], item[1]))
    component = graph.components[component_id]
    cuts = [set(graph.units[unit_id].repair_cut_node_ids) for unit_id in losing]
    common = set.intersection(*cuts) if cuts else set()
    if not common:
        common = set(component.legal_repair_cut_ids)
    observations = {
        item.outcome_id: item.observation
        for unit_id in losing
        for item in grouped[unit_id]
        if item.status != OutcomeStatus.PASS
    }
    paths = tuple(sorted(
        graph.units[unit_id].path_obligation_id for unit_id in losing
    ))
    scenarios = tuple(sorted({
        item.scenario_id
        for unit_id in losing for item in grouped[unit_id]
        if item.scenario_id is not None
    }))
    return LosingCore(
        core_id=stable_id("losing-core", component_id, paths, observations, state.checkpoint.checkpoint_id),
        component_id=component_id,
        unit_ids=losing,
        path_obligation_ids=paths,
        scenario_ids=scenarios,
        common_causal_cut_ids=tuple(sorted(common)),
        protected_pass_pairs=protected,
        preservation_ids=component.preservation_node_ids,
        actual_failure_observations=observations,
        pressure=pressure,
    )


def _mechanism_candidates(state: ReachAvoidState, core: LosingCore) -> list[str]:
    nodes = [
        state.program_graph.nodes[node_id]
        for node_id in core.common_causal_cut_ids
        if node_id in state.program_graph.nodes
    ]
    candidates: list[str] = []
    if any(node.kind in {"branch", "loop"} for node in nodes):
        candidates.append("guard_boundary")
    if any(node.kind in {"protocol_operation", "dispatch_slot"} for node in nodes):
        candidates.append("dispatch_protocol")
    if any(node.kind in {"exception", "handler"} for node in nodes):
        candidates.append("exception_contract")
    if any(node.kind in {"field", "assignment", "state_write"} for node in nodes):
        candidates.append("state_order")
    candidates.extend(("return_relation", "mechanism_rewrite"))
    return list(dict.fromkeys(candidates))


def next_untried_repair_intent(
    state: ReachAvoidState,
    core: LosingCore | None = None,
) -> RepairIntent | None:
    active_graph = getattr(state, "active_binding_graph", None)
    if active_graph is not None and hasattr(active_graph, "diff_hash"):
        from reachpatch.reach_avoid.trajectory import select_confirmed_failure

        # Older unit fixtures predate PatchTrajectory and exercise only the
        # mechanical intent formatter. Production ReachAvoidState always has
        # ``confirmed_failures`` and therefore takes the strict path below.
        legacy_fixture = not hasattr(state, "confirmed_failures")
        confirmed_failure = (
            select_confirmed_failure(state) if not legacy_fixture else None
        )
        if confirmed_failure is None and not legacy_fixture:
            return None
        priority = {
            "PRESERVATION_RISK": 0,
            "TARGET_FAILING": 1,
            "COUNTEREXAMPLE_OPEN": 2,
            "EXECUTION_CONFIRMED": 6,
            "STATIC_ACTIONABLE": 7,
            "TARGET_PASSING": 9,
        }
        units = sorted(
            (
                unit for unit in state.active_binding_graph.units.values()
                if legacy_fixture or unit.binding_id == confirmed_failure.binding_unit_id
            ),
            key=lambda unit: (priority.get(unit.status, 8), unit.binding_id),
        )
        unit = next(iter(units), None)
        if unit is None:
            # A mechanically rejected first patch can precede executable graph
            # bindings. Its concrete check output still defines a causal repair
            # intent and must not be discarded merely because the graph is empty.
            packet = next((
                item for item in reversed(state.counterexamples)
                if item.suggested_action_families
                and (
                    legacy_fixture
                    or item.public_trigger_id == confirmed_failure.check_id
                    or (
                        confirmed_failure.kind == "CONFIRMED_MECHANICAL_FAILURE"
                        and any(
                            str(failed.get("check_id", ""))
                            == confirmed_failure.check_id
                            for failed in (
                                item.actual_observation.get("failed_checks", ())
                                if isinstance(item.actual_observation, dict) else ()
                            )
                        )
                    )
                )
            ), None)
            if packet is None:
                return None
            mechanism = next((
                item for item in packet.suggested_action_families
                if item not in state.prohibited_mechanisms
            ), None)
            if mechanism is None:
                return None
            actual = (
                packet.actual_observation
                if isinstance(packet.actual_observation, dict) else {}
            )
            changed_files = tuple(map(str, actual.get("changed_files", ())))
            requirements = tuple(
                row.requirement_id
                for row in (
                    state.requirement_coverage.unresolved_rows()
                    if state.requirement_coverage is not None else ()
                )
            )
            return RepairIntent(
                intent_id=stable_id(
                    "unbound-counterexample-intent", state.instance_id,
                    packet.failure_signature, mechanism,
                ),
                source_checkpoint_id=state.checkpoint.checkpoint_id,
                losing_core_id=packet.counterexample_id,
                component_id="UNBOUND_MECHANICAL_COUNTEREXAMPLE",
                losing_path_obligation_ids=(),
                complete_component_path_ids=(),
                repair_cut_ids=packet.causal_cut_ids,
                root_mechanism_class=mechanism,
                actual_failure_execution_ids=packet.raw_execution_ids,
                protected_pass_pairs=(),
                preservation_ids=packet.preservation_path_ids,
                forbidden_fingerprints=tuple(sorted(state.prohibited_mechanisms)),
                frontier_resolution_ids=(),
                selection_witness={
                    "failure_origin": packet.failure_origin,
                    "failure_signature": packet.failure_signature,
                    "suggested_action_families": list(
                        packet.suggested_action_families
                    ),
                    "actual_observation": packet.actual_observation,
                    "working_diff_hash": active_graph.diff_hash,
                },
                mechanism_id=mechanism,
                requirements_to_satisfy=requirements,
                counterexample_ids=(packet.counterexample_id,),
                observed_failures=(
                    str(packet.failure_signature or packet.failure_origin),
                ),
                root_cause=(
                    packet.failure_location
                    or "mechanical failure in the changed hunk"
                ),
                files_to_modify=changed_files,
                causal_cut_ids=packet.causal_cut_ids,
                behavior_to_preserve=packet.protected_behavior,
                expected_effects=(
                    "make the same working repair mechanically importable/applicable",
                ),
                known_risks=packet.impact_risks,
            )
        packets = tuple(
            packet for packet in state.counterexamples
            if (
                packet.binding_unit_id in {None, unit.binding_id}
                and (
                    legacy_fixture
                    or packet.public_trigger_id == confirmed_failure.check_id
                    or packet.binding_unit_id == confirmed_failure.binding_unit_id
                )
            )
        )[-10:]
        candidates: list[str] = [
            family for packet in reversed(packets)
            for family in packet.suggested_action_families
        ]
        relation_text = " ".join(unit.causal_cut_ids).lower()
        if "guard" in relation_text:
            candidates.extend(("guard_expand", "guard_tighten"))
        if "dispatch" in relation_text or unit.protocol_edge_ids:
            candidates.append("protocol_dispatch")
        if "exception" in relation_text:
            candidates.append("exception_edge")
        if unit.status == "PRESERVATION_RISK":
            candidates.extend(("restore_representation", "remove_wrapper"))
        candidates.extend((
            "operand_predicate", "causal_slice_rewrite", "state_update_order",
        ))
        mechanism = next(
            (item for item in dict.fromkeys(candidates)
             if item not in state.prohibited_mechanisms),
            None,
        )
        if mechanism is None:
            return None
        localized_node_ids = tuple(
            node_id for node_id in unit.causal_cut_ids
            if node_id in state.program_graph.nodes
        ) or unit.program_symbol_ids
        files = tuple(dict.fromkeys(
            str(state.program_graph.nodes[symbol_id].attributes.get("file", ""))
            for symbol_id in localized_node_ids
            if symbol_id in state.program_graph.nodes
            and state.program_graph.nodes[symbol_id].attributes.get("file")
        ))
        symbols = tuple(dict.fromkeys(
            str(state.program_graph.nodes[symbol_id].attributes.get(
                "qualified_name", state.program_graph.nodes[symbol_id].label,
            ))
            for symbol_id in localized_node_ids
            if symbol_id in state.program_graph.nodes
        ))
        unresolved_requirement_ids = tuple(
            row.requirement_id
            for row in (
                state.requirement_coverage.unresolved_rows()
                if state.requirement_coverage is not None else ()
            )
        ) or (unit.requirement_id,)
        observed = tuple(dict.fromkeys(
            str(packet.failure_signature or packet.oracle_result or packet.failure_origin)
            for packet in packets
        ))
        preservation_ids = tuple(dict.fromkeys((
            *unit.preservation_check_ids,
            *(
                item.check_id for item in state.check_comparisons
                if item.classification.value == "PASS_PRESERVED"
            ),
        )))
        intent_id = stable_id(
            "active-repair-intent", state.instance_id, state.transition_index,
            unit.binding_id, mechanism, observed,
        )
        return RepairIntent(
            intent_id=intent_id,
            source_checkpoint_id=state.checkpoint.checkpoint_id,
            losing_core_id=unit.binding_id,
            component_id=unit.binding_id,
            losing_path_obligation_ids=unit.path_class_ids,
            complete_component_path_ids=unit.path_class_ids,
            repair_cut_ids=unit.causal_cut_ids,
            root_mechanism_class=mechanism,
            actual_failure_execution_ids=tuple(
                execution_id for packet in packets
                for execution_id in packet.raw_execution_ids
            ),
            protected_pass_pairs=(),
            preservation_ids=preservation_ids,
            forbidden_fingerprints=tuple(sorted(state.prohibited_mechanisms)),
            frontier_resolution_ids=tuple(
                gap.frontier_id for gap in state.active_binding_graph.unresolved_gaps
                if gap.requirement_id == unit.requirement_id
            ),
            selection_witness={
                "priority_status": unit.status,
                "working_diff_hash": state.active_binding_graph.diff_hash,
                "counterexample_ids": [item.counterexample_id for item in packets],
                "coverage_statuses": {
                    row.requirement_id: row.status
                    for row in (
                        state.requirement_coverage.unresolved_rows()
                        if state.requirement_coverage is not None else ()
                    )
                },
            },
            mechanism_id=mechanism,
            requirements_to_satisfy=unresolved_requirement_ids,
            binding_unit_ids=(unit.binding_id,),
            counterexample_ids=tuple(item.counterexample_id for item in packets),
            observed_failures=observed,
            root_cause=(
                packets[-1].failure_location if packets
                else unit.unresolved_reason or "unresolved active binding"
            ),
            files_to_modify=files,
            symbols_to_modify=symbols,
            causal_cut_ids=unit.causal_cut_ids,
            behavior_to_preserve=preservation_ids,
            expected_effects=(
                f"move binding {unit.binding_id} from {unit.status} toward PASSING",
            ),
            known_risks=unit.impact_cone_ids,
        )
    if core is None:
        return None
    attempts = state.mechanism_memory.get(core.core_id, [])
    forbidden_classes = {
        item.mechanism_class for item in attempts if item.forbidden_next
    }
    mechanism = next(
        (item for item in _mechanism_candidates(state, core) if item not in forbidden_classes),
        None,
    )
    if mechanism is None:
        return None
    graph = getattr(state, "active_binding_graph", None)
    if graph is None:
        return None
    component = graph.components[core.component_id]
    complete_paths = tuple(sorted(
        graph.units[unit_id].path_obligation_id
        for unit_id in component.unit_ids
    ))
    frontier_ids = tuple(sorted({
        frontier_id
        for unit_id in core.unit_ids
        for frontier_id in graph.units[unit_id].frontier_ids
    }))
    execution_ids = tuple(sorted(
        item.execution_bundle_id
        for item in state.outcomes.values()
        if item.unit_id in core.unit_ids and item.execution_bundle_id
    ))
    forbidden_fingerprints = tuple(sorted({
        item.fingerprint_hash for item in attempts if item.forbidden_next
    }))
    witness = {
        "component_pressure": core.pressure,
        "covered_unit_ids": list(core.unit_ids),
        "complete_component_unit_ids": list(component.unit_ids),
        "cut_coverage": {
            cut_id: sorted(
                unit_id for unit_id in component.unit_ids
                if cut_id in graph.units[unit_id].repair_cut_node_ids
            )
            for cut_id in core.common_causal_cut_ids
        },
        "mechanism_candidates": _mechanism_candidates(state, core),
    }
    return RepairIntent(
        intent_id=stable_id(
            "repair-intent", state.checkpoint.checkpoint_id, core.core_id,
            mechanism, forbidden_fingerprints,
        ),
        source_checkpoint_id=state.checkpoint.checkpoint_id,
        losing_core_id=core.core_id,
        component_id=core.component_id,
        losing_path_obligation_ids=core.path_obligation_ids,
        complete_component_path_ids=complete_paths,
        repair_cut_ids=core.common_causal_cut_ids,
        root_mechanism_class=mechanism,
        actual_failure_execution_ids=execution_ids,
        protected_pass_pairs=core.protected_pass_pairs,
        preservation_ids=core.preservation_ids,
        forbidden_fingerprints=forbidden_fingerprints,
        frontier_resolution_ids=frontier_ids,
        selection_witness=witness,
    )
