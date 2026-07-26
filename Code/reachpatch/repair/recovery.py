from __future__ import annotations

from reachpatch.binding_graph import build_binding_graph
from reachpatch.challenge_graph.materialize import materialize_challenges
from reachpatch.models.base import stable_id
from reachpatch.models.controller import LosingCore, ReachAvoidState, RootRecoveryRecord
from reachpatch.models.enums import OracleLifecycle, OutcomeStatus
from reachpatch.program_graph.builder import build_augmented_program_graph
from reachpatch.requirement_graph import compile_assignment_overlay, compile_requirement_paths


def root_recovery(state: ReachAvoidState, core: LosingCore) -> RootRecoveryRecord:
    old_hashes = state.graph_hashes()
    repository = state.checkpoint.snapshot_tree
    program = build_augmented_program_graph(repository)
    requirements = compile_assignment_overlay(state.semantic_graph, state.assignment)
    compile_requirement_paths(requirements, program)
    binding = build_binding_graph(requirements, program)
    challenges = materialize_challenges(requirements, program, binding)
    old_paths = set(state.binding_graph.by_path_obligation)
    new_paths = set(binding.by_path_obligation)
    old_units = set(state.binding_graph.units)
    new_units = set(binding.units)
    old_cuts = {
        node_id
        for unit_id in core.unit_ids
        if unit_id in state.binding_graph.units
        for node_id in state.binding_graph.units[unit_id].repair_cut_node_ids
    }
    new_cuts = {
        node_id
        for unit in binding.units.values()
        if unit.path_obligation_id in core.path_obligation_ids
        for node_id in unit.repair_cut_node_ids
    }
    contested = [
        oracle.oracle_id for oracle in binding.oracles.values()
        if oracle.lifecycle == OracleLifecycle.CONTESTED
    ]
    environment_blocked = bool(state.outcomes) and all(
        item.status in {
            OutcomeStatus.UNKNOWN,
            OutcomeStatus.UNKNOWN_EXECUTION,
            OutcomeStatus.BLOCKED_EXTERNAL,
        }
        for item in state.outcomes.values()
    )
    if contested:
        classification = "ORACLE_DISPUTE"
        resolution = "adjudicate contested oracle before further source edits"
    elif environment_blocked:
        classification = "ENVIRONMENT_BLOCKED"
        resolution = "repair execution environment and replay paired scenarios"
    elif old_paths != new_paths or old_units != new_units:
        classification = "WRONG_BINDING"
        resolution = "replace stale requirement paths and binding units"
    elif new_cuts - old_cuts:
        classification = "NEW_CUT"
        resolution = "continue from the newly grounded causal repair cut"
    elif state.assignment is None or not state.assignment.coherent:
        classification = "SEMANTIC_DISPUTE"
        resolution = "restart semantic episode after evidence adjudication"
    else:
        classification = "NO_LEGAL_ACTION"
        resolution = "terminate after all grounded mechanisms were exhausted"
    state.program_graph = program
    state.requirement_graph = requirements
    state.binding_graph = binding
    state.challenge_graph = challenges
    new_hashes = state.graph_hashes()
    record = RootRecoveryRecord(
        recovery_id=stable_id(
            "root-recovery", core.core_id, old_hashes, new_hashes, classification
        ),
        core_id=core.core_id,
        trigger="three_nonprogressing_or_no_legal_intent",
        old_graph_hashes=old_hashes,
        new_graph_hashes=new_hashes,
        invalidated_unit_ids=tuple(sorted(old_units - new_units)),
        new_cut_ids=tuple(sorted(new_cuts - old_cuts)),
        classification=classification,
        resolution=resolution,
    )
    state.root_recoveries.append(record)
    return record
