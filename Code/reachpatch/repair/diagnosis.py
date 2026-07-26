from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import content_hash
from reachpatch.models.controller import CounterexamplePacket, UnitOutcome
from reachpatch.program_graph.models import ProgramGraph


def diagnose_mechanism(
    outcomes: Iterable[UnitOutcome],
    counterexamples: Iterable[CounterexamplePacket],
    program_graph: ProgramGraph,
) -> dict[str, Any]:
    failed = [item for item in outcomes if item.status.value != "PASS"]
    packets = list(counterexamples)
    origins = Counter(item.failure_origin for item in failed)
    divergence_kinds = Counter(
        str(item.actual_observation.get("first_divergence", {}).get("kind", "trace"))
        for item in failed
    )
    slice_ids = {
        node_id
        for packet in packets
        for node_id in packet.relevant_source_slice_ids
        if node_id in program_graph.nodes
    }
    node_kinds = Counter(program_graph.nodes[node_id].kind for node_id in slice_ids)
    protocol_nodes = sorted(
        node_id for node_id in slice_ids
        if program_graph.nodes[node_id].kind == "protocol_operation"
    )
    branch_nodes = sorted(
        node_id for node_id in slice_ids
        if program_graph.nodes[node_id].kind in {"branch", "loop"}
    )
    state_nodes = sorted(
        node_id for node_id in slice_ids
        if program_graph.nodes[node_id].kind in {"field", "state_write", "state_read"}
    )
    if origins.get("PATCH_MECHANICAL"):
        mechanism = "mechanical_repair"
    elif origins.get("PRESERVATION"):
        mechanism = "preservation_restore"
    elif protocol_nodes:
        mechanism = "dispatch_protocol"
    elif branch_nodes:
        mechanism = "guard_boundary"
    elif state_nodes:
        mechanism = "state_order"
    elif any(item.exit_kind == "exception" for item in packets):
        mechanism = "exception_contract"
    else:
        mechanism = "return_relation"
    return {
        "mechanism_class": mechanism,
        "failure_origins": dict(origins),
        "divergence_kinds": dict(divergence_kinds),
        "slice_node_kinds": dict(node_kinds),
        "protocol_node_ids": protocol_nodes,
        "branch_node_ids": branch_nodes,
        "state_node_ids": state_nodes,
        "common_slice_ids": sorted(slice_ids),
    }


def mechanism_fingerprint(actual_diff: ActualDiff) -> dict[str, Any]:
    operations = [
        {
            "kind": relation.kind,
            "file": relation.file,
            "scope": relation.qualified_scope,
            "old": relation.old_source,
            "new": relation.new_source,
            "attributes": relation.attributes,
        }
        for relation in actual_diff.changed_relations
    ]
    payload = {
        "files": list(actual_diff.changed_files),
        "operations": operations,
        "hunks": [item.to_dict() for item in actual_diff.hunks],
        "oracle_contamination": list(actual_diff.oracle_contamination_paths),
    }
    payload["fingerprint_hash"] = content_hash(payload)
    return payload
