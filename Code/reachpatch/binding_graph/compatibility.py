from __future__ import annotations

from typing import Any

from reachpatch.models.base import stable_id
from reachpatch.oracle.models import ObservationContract, Oracle
from reachpatch.program_graph.models import PathClass, ProgramGraph
from reachpatch.requirement_graph.domains import solve_constraints
from reachpatch.requirement_graph.models import RequirementLeaf, RequirementPathObligation

from .models import ProjectionWitness

_CHANNEL_KINDS = {
    "return": {"return", "assertion", "observation_point"},
    "exception": {"exception", "assertion", "observation_point"},
    "state": {"field", "assertion", "observation_point"},
    "output": {"external_effect", "assertion", "observation_point"},
    "calls": {"call_site", "protocol_operation", "assertion"},
    "effects": {"external_interface", "external_effect", "assertion"},
}


def project_domain_to_guard(
    leaf: RequirementLeaf,
    obligation: RequirementPathObligation,
    path_class: PathClass,
) -> dict[str, Any]:
    constraints = tuple(dict.fromkeys(
        obligation.partition.constraints + path_class.critical_predicates
    ))
    result = solve_constraints(
        leaf.quantified_variables,
        leaf.domains,
        constraints,
        max_combinations=4096,
    )
    return {
        "compatible": result.satisfiable,
        "constraints": list(constraints),
        "solver": result.to_dict(),
        "quantified_variables": [variable.name for variable in leaf.quantified_variables],
    }


def project_observation(
    contract: ObservationContract,
    obligation: RequirementPathObligation,
    graph: ProgramGraph,
) -> dict[str, Any]:
    if obligation.observation_id not in graph.nodes:
        return {
            "compatible": False,
            "reason": "observation_node_missing",
            "observation_id": obligation.observation_id,
        }
    kind = graph.nodes[obligation.observation_id].kind
    matching_channels = [
        channel
        for channel in contract.channels
        if kind in _CHANNEL_KINDS.get(channel, {"observation_point"})
    ]
    return {
        "compatible": bool(matching_channels),
        "observation_id": obligation.observation_id,
        "program_kind": kind,
        "matching_channels": matching_channels,
    }


def oracle_applicable(
    oracle: Oracle,
    observation_projection: dict[str, Any],
) -> dict[str, Any]:
    channels = set(observation_projection.get("matching_channels", []))
    expected = set(oracle.observation_channels)
    return {
        "compatible": oracle.active_and_trusted and bool(channels & expected),
        "active_and_trusted": oracle.active_and_trusted,
        "channel_intersection": sorted(channels & expected),
        "lifecycle": oracle.lifecycle.value,
        "executable": oracle.executable,
    }


def bind_compatibility(
    leaf: RequirementLeaf,
    obligation: RequirementPathObligation,
    path_class: PathClass,
    graph: ProgramGraph,
    observation_contract: ObservationContract,
    oracle: Oracle,
    *,
    trigger_path_cache: dict[tuple[str, str], bool] | None = None,
) -> ProjectionWitness:
    trigger_path_key = (
        obligation.public_trigger_id or "",
        obligation.entrypoint_id or "",
    )
    trigger_reachable = trigger_path_cache.get(trigger_path_key) if trigger_path_cache is not None else None
    if trigger_reachable is None:
        trigger_reachable = bool(
            obligation.public_trigger_id is not None
            and obligation.entrypoint_id is not None
            and obligation.public_trigger_id in graph.nodes
            and obligation.entrypoint_id in graph.nodes
            and (
                obligation.public_trigger_id == obligation.entrypoint_id
                or graph.shortest_path(
                    obligation.public_trigger_id, obligation.entrypoint_id
                ) is not None
            )
        )
        if trigger_path_cache is not None:
            trigger_path_cache[trigger_path_key] = trigger_reachable
    trigger_projection = {
        "compatible": trigger_reachable,
        "trigger_id": obligation.public_trigger_id,
        "entrypoint_id": obligation.entrypoint_id,
    }
    domain_projection = project_domain_to_guard(leaf, obligation, path_class)
    observation_projection = project_observation(observation_contract, obligation, graph)
    oracle_projection = oracle_applicable(oracle, observation_projection)
    components = (
        trigger_projection["compatible"],
        domain_projection["compatible"],
        observation_projection["compatible"],
        oracle_projection["compatible"],
    )
    reason = "compatible" if all(components) else ";".join(
        name
        for name, value in zip(
            ("trigger", "domain_guard", "observation", "oracle"),
            components,
            strict=True,
        )
        if not value
    )
    return ProjectionWitness(
        witness_id=stable_id(
            "binding-projection",
            leaf.leaf_id,
            obligation.path_obligation_id,
            path_class.path_class_id,
            trigger_projection,
            domain_projection,
            observation_projection,
            oracle_projection,
        ),
        compatible=all(components),
        trigger_projection=trigger_projection,
        domain_guard_projection=domain_projection,
        observation_projection=observation_projection,
        oracle_projection=oracle_projection,
        reason=reason,
    )
