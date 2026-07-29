from __future__ import annotations

from reachpatch.binding_graph.models import (
    BindingStatus,
    ExecutableBindingGraph,
    ExecutableBindingUnit,
)
from reachpatch.models.base import content_hash, stable_id


def build_executable_bindings(
    executable_requirements,
    target_slice,
    causal_slices,
    impact_slice,
) -> ExecutableBindingGraph:
    """Bind real checks independently of normative path-product closure."""

    slices_by_execution = {item.execution_id: item for item in causal_slices}
    units = []
    for requirement in executable_requirements.executable_requirements:
        causal = slices_by_execution.get(requirement.baseline_execution_id)
        target = requirement.status == "BASELINE_FAILING_TARGET"
        cuts = tuple(causal.candidate_cut_node_ids[:20]) if causal else ()
        units.append(ExecutableBindingUnit(
            unit_id=stable_id(
                "executable-binding", requirement.executable_requirement_id,
                causal.slice_id if causal else None,
            ),
            kind=(
                BindingStatus.EXECUTABLE_TARGET
                if target else BindingStatus.EXECUTABLE_PRESERVATION
            ),
            executable_requirement_id=requirement.executable_requirement_id,
            normative_requirement_id=requirement.normative_requirement_id,
            check_id=requirement.check_id,
            baseline_execution_id=requirement.baseline_execution_id,
            failure_location=(
                dict(causal.failure_location)
                if causal and causal.failure_location else None
            ),
            entrypoint=causal.enclosing_callable if causal else None,
            observation_contract_id=requirement.observation_contract_id,
            causal_slice_id=causal.slice_id if causal else None,
            repair_cut_node_ids=cuts,
            candidate_repair_cut_ids=cuts,
            impact_node_ids=(tuple(impact_slice.node_ids) if impact_slice else ()),
            cut_status="CUT_RESOLVED" if cuts else (
                "CUT_UNRESOLVED" if target else "NOT_REQUIRED"
            ),
        ))
    for normative_id in executable_requirements.unresolved_normative_requirement_ids:
        units.append(ExecutableBindingUnit(
            unit_id=stable_id("deferred-normative-binding", normative_id),
            kind=BindingStatus.DEFERRED_NORMATIVE,
            executable_requirement_id=None,
            normative_requirement_id=normative_id,
            check_id=None,
            baseline_execution_id=None,
            failure_location=None,
            entrypoint=None,
            observation_contract_id=None,
            causal_slice_id=None,
            repair_cut_node_ids=(),
            candidate_repair_cut_ids=(),
            impact_node_ids=(),
            cut_status="NOT_EVALUABLE",
        ))
    body = {
        "units": [item.to_dict() for item in units],
        "overlay": executable_requirements.overlay_hash,
        "target_slice": target_slice.slice_id,
        "impact_slice": impact_slice.slice_id if impact_slice else None,
    }
    return ExecutableBindingGraph(
        units=tuple(units),
        executable_requirement_overlay_hash=executable_requirements.overlay_hash,
        target_slice_id=target_slice.slice_id,
        impact_slice_id=impact_slice.slice_id if impact_slice else None,
        graph_hash=content_hash(body),
    )
