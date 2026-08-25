from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from reachpatch.binding_graph import (
    build_binding_graph, confirm_bindings_from_execution,
    update_binding_graph_after_diff,
)
from reachpatch.challenge_graph import (
    materialize_challenge_graph, update_challenge_graph_after_diff,
)
from reachpatch.challenge_graph.models import challenge_obligation_key
from reachpatch.models.base import stable_id
from reachpatch.models.evidence import (
    ActualDiff, CounterexamplePacket, OutcomeStatus, PairClassification,
    PairedTraceBundle, PublicEvidence,
)
from reachpatch.models.graphs import (
    BindingStatus, ChallengeStatus, GraphStack, ProgramEdge, ProgramEdgeKind,
    RequirementGraph,
)
from reachpatch.models.reach_avoid import ReachAvoidState
from reachpatch.program_graph import (
    GraphBudget, build_initial_program_graph, match_trace_nodes,
    update_program_graph_after_diff,
)
from reachpatch.requirement_graph import promote_diff_partitions


_LAST_UPDATE_METRICS = {
    "program_update_seconds": 0.0,
    "requirement_update_seconds": 0.0,
    "binding_update_seconds": 0.0,
    "challenge_materialization_seconds": 0.0,
}


def latest_graph_metrics() -> dict[str, float]:
    return dict(_LAST_UPDATE_METRICS)


def set_graph_metrics(values: dict[str, float]) -> None:
    for key in _LAST_UPDATE_METRICS:
        _LAST_UPDATE_METRICS[key] = float(values.get(key, 0.0))


def build_graph_stack(
    repository: Path,
    base_commit: str,
    issue: str,
    requirement_graph: RequirementGraph,
    actual_diff: ActualDiff,
    public_evidence: PublicEvidence,
    budget: GraphBudget,
    revision: int = 0,
) -> GraphStack:
    program_started = time.monotonic()
    program = build_initial_program_graph(
        repository, issue, actual_diff, public_evidence.checks, budget,
        base_commit=base_commit,
        relevant_symbols=tuple(
            leaf.operation for leaf in requirement_graph.leaves.values()
        ),
    )
    _LAST_UPDATE_METRICS["program_update_seconds"] = time.monotonic() - program_started
    requirement_delta = promote_diff_partitions(requirement_graph, program, actual_diff)
    _LAST_UPDATE_METRICS["requirement_update_seconds"] = requirement_delta.update_seconds
    binding_started = time.monotonic()
    binding = build_binding_graph(requirement_graph, program, actual_diff, public_evidence.checks)
    _LAST_UPDATE_METRICS["binding_update_seconds"] = time.monotonic() - binding_started
    binding, challenge, materialization_seconds = materialize_challenge_graph(
        requirement_graph, program, binding, public_evidence,
    )
    _LAST_UPDATE_METRICS["challenge_materialization_seconds"] = materialization_seconds
    stack = GraphStack(actual_diff.patch_hash, revision, requirement_graph, program, binding, challenge)
    stack.validate()
    return stack


def update_graph_stack_after_diff(
    previous: GraphStack,
    cumulative_diff: ActualDiff,
    trial_tree: Path,
    repository: Path,
    issue: str,
    public_evidence: PublicEvidence,
    budget: GraphBudget,
    *,
    traces: tuple = (),
    context_requests: tuple = (),
) -> GraphStack:
    program_delta = update_program_graph_after_diff(
        previous.program_graph, trial_tree, cumulative_diff,
        tuple(traces), tuple(context_requests), budget,
        public_evidence.checks,
    )
    requirement_copy = RequirementGraph(
        leaves={
            requirement_id: leaf
            for requirement_id, leaf in previous.requirement_graph.leaves.items()
            if not any(
                evidence_id.startswith("impact-preservation:")
                for evidence_id in leaf.evidence_ids
            )
        },
        challenge_partitions=dict(previous.requirement_graph.challenge_partitions),
        evidence_hash=previous.requirement_graph.evidence_hash,
    )
    requirement_delta = promote_diff_partitions(
        requirement_copy, program_delta.graph, cumulative_diff,
    )
    binding_delta = update_binding_graph_after_diff(
        previous.binding_graph, previous.program_graph,
        requirement_delta.graph, program_delta.graph, cumulative_diff,
        public_evidence.checks,
    )
    binding = binding_delta.graph
    _LAST_UPDATE_METRICS["program_update_seconds"] = program_delta.update_seconds
    _LAST_UPDATE_METRICS["requirement_update_seconds"] = requirement_delta.update_seconds
    _LAST_UPDATE_METRICS["binding_update_seconds"] = binding_delta.update_seconds
    binding, challenge, materialization_seconds = update_challenge_graph_after_diff(
        previous.challenge_graph, previous.binding_graph,
        requirement_delta.graph, program_delta.graph, binding,
        public_evidence, binding_delta.changed_binding_ids,
    )
    _LAST_UPDATE_METRICS["challenge_materialization_seconds"] = materialization_seconds
    stack = GraphStack(
        patch_hash=cumulative_diff.patch_hash,
        revision=previous.revision + 1,
        requirement_graph=requirement_delta.graph,
        program_graph=program_delta.graph,
        binding_graph=binding,
        challenge_graph=challenge,
    )
    stack.validate()
    return stack


def apply_execution_to_graph_stack(
    stack: GraphStack,
    executions: tuple[PairedTraceBundle, ...],
    counterexamples: tuple[CounterexamplePacket, ...],
    *,
    program_update_seconds: float = 0.0,
) -> GraphStack:
    """Apply dynamic observations and rebalance all four graph hashes together."""

    program_started = time.monotonic()
    ordered_nodes_by_trace: dict[str, tuple[str, ...]] = {}
    for execution in executions:
        if execution.patch_hash != stack.patch_hash:
            continue
        ordered, _ = match_trace_nodes(
            stack.program_graph, execution.patched,
        )
        ordered_nodes_by_trace[execution.patched.trace_bundle_id] = ordered
    edges = dict(stack.program_graph.edges)
    for trace_id, ordered in ordered_nodes_by_trace.items():
        transitions = (
            ((ordered[0], ordered[0]),)
            if len(ordered) == 1 else
            tuple(dict.fromkeys(zip(ordered, ordered[1:])))
        )
        for ordinal, (source_id, target_id) in enumerate(transitions):
            edge_id = stable_id(
                "executed-edge", stack.patch_hash, trace_id, ordinal,
                source_id, target_id,
            )
            edges[edge_id] = ProgramEdge(
                edge_id, source_id, target_id, ProgramEdgeKind.EXECUTED_CALL,
                True, (trace_id,),
            )
    program = replace(stack.program_graph, edges=edges)
    _LAST_UPDATE_METRICS["program_update_seconds"] = (
        program_update_seconds + time.monotonic() - program_started
    )
    binding_started = time.monotonic()
    binding_input = replace(stack.binding_graph, program_hash=program.graph_hash())
    binding_delta = confirm_bindings_from_execution(
        binding_input, program, stack.requirement_graph, executions,
    )
    binding = binding_delta.graph
    cells = dict(stack.challenge_graph.cells)
    for execution in executions:
        if execution.patch_hash != stack.patch_hash:
            continue
        cell = cells.get(execution.challenge_id)
        if cell is None:
            continue
        obligation = challenge_obligation_key(cell)
        for related_id, related in tuple(cells.items()):
            if challenge_obligation_key(related) != obligation:
                continue
            cells[related_id] = replace(
                related,
                oracle=cell.oracle,
                authority=cell.authority,
                baseline_outcome=execution.baseline.observation.status,
                patched_outcome=execution.patched.observation.status,
                trace_bundle_id=execution.paired_bundle_id,
                stability_runs=execution.stable_runs,
                terminal_status=cell.terminal_status,
            )
        unit = binding.units.get(cell.binding_id)
        if unit is None or execution.classification is not PairClassification.UNKNOWN:
            continue
        environment_blocked = any(
            trace.observation.exception in {"TIMEOUT", "FileNotFoundError", "PermissionError"}
            or trace.observation.status in {OutcomeStatus.BLOCKED, OutcomeStatus.UNSUPPORTED}
            for trace in (execution.baseline, execution.patched)
        )
        status = (
            BindingStatus.ENVIRONMENT_BLOCKED
            if environment_blocked else BindingStatus.ORACLE_UNAVAILABLE
        )
        binding.units[cell.binding_id] = replace(
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
    packets_by_binding: dict[str, list[CounterexamplePacket]] = {}
    for packet in counterexamples:
        if packet.patch_hash != stack.patch_hash:
            continue
        packets_by_binding.setdefault(packet.binding_id, []).append(packet)
    for binding_id, packets in packets_by_binding.items():
        unit = binding.units.get(binding_id)
        if unit is None:
            continue
        binding.units[binding_id] = replace(
            unit,
            status=BindingStatus.COUNTEREXAMPLE_OPEN,
            counterexample_ids=tuple(dict.fromkeys(
                unit.counterexample_ids
                + tuple(packet.counterexample_id for packet in packets)
            )),
            causal_cut_ids=tuple(dict.fromkeys(
                unit.causal_cut_ids
                + tuple(
                    cut_id for packet in packets for cut_id in packet.causal_cut_ids
                )
            )),
        )
    _LAST_UPDATE_METRICS["binding_update_seconds"] = (
        time.monotonic() - binding_started
    )
    requirement_started = time.monotonic()
    leaves = dict(stack.requirement_graph.leaves)
    for execution in executions:
        if execution.patch_hash != stack.patch_hash:
            continue
        cell = cells.get(execution.challenge_id)
        if cell is None:
            continue
        confirmed_binding = binding.units.get(cell.binding_id)
        if confirmed_binding is None or not confirmed_binding.status.execution_confirmed:
            continue
        status = (
            "PASS" if (
                execution.classification in {
                    PairClassification.TARGET_FIXED,
                    PairClassification.PASS_PRESERVED,
                }
            ) and execution.stable_runs >= 2 else
            "FAIL" if execution.classification in {
                PairClassification.PRESERVATION_REGRESSION,
                PairClassification.TARGET_REGRESSED,
                PairClassification.TARGET_STILL_FAILING,
            } else "EXPLORATION_ONLY"
            if execution.oracle_authority not in {"A", "B", "C"}
            else "UNKNOWN"
        )
        if cell.challenge_id in cells:
            cells[cell.challenge_id] = replace(
                cell,
                baseline_outcome=execution.baseline.observation.status,
                patched_outcome=execution.patched.observation.status,
                trace_bundle_id=execution.paired_bundle_id,
                stability_runs=execution.stable_runs,
                terminal_status=(
                    ChallengeStatus.PASS if status == "PASS" else
                    ChallengeStatus.FAIL if status == "FAIL" else
                    ChallengeStatus.EXPLORATION_ONLY
                    if status == "EXPLORATION_ONLY" else ChallengeStatus.UNKNOWN
                ),
            )
        leaf = leaves.get(cell.requirement_id)
        if (
            leaf is not None
            and leaf.preservation
            and execution.oracle_authority == "C"
            and execution.expected_relation.startswith(
                "patched observation preserves stable baseline"
            )
        ):
            leaves[cell.requirement_id] = replace(
                leaf,
                authority="C",
                evidence_ids=tuple(dict.fromkeys(
                    leaf.evidence_ids
                    + (execution.baseline.trace_bundle_id,)
                )),
            )
            unit = binding.units.get(cell.binding_id)
            if unit is not None:
                binding.units[cell.binding_id] = replace(
                    unit,
                    authority="C",
                    evidence_ids=tuple(dict.fromkeys(
                        unit.evidence_ids
                        + (execution.baseline.trace_bundle_id,)
                    )),
                )
    for requirement_id, leaf in tuple(leaves.items()):
        related = tuple(
            cell for cell in cells.values()
            if cell.patch_hash == stack.patch_hash
            and cell.requirement_id == requirement_id
            and cell.oracle.trusted and cell.oracle.executable
        )
        obligations: dict[str, list] = {}
        for cell in related:
            obligations.setdefault(challenge_obligation_key(cell), []).append(cell)
        if any(
            any(
                cell.terminal_status is ChallengeStatus.FAIL
                and cell.stability_runs >= 2
                for cell in equivalent_cells
            )
            and not any(
                cell.terminal_status is ChallengeStatus.PASS
                and cell.stability_runs >= 2
                for cell in equivalent_cells
            )
            for equivalent_cells in obligations.values()
        ):
            outcome = OutcomeStatus.FAIL
        elif obligations and all(
            any(
                cell.terminal_status is ChallengeStatus.UNREACHABLE
                or (
                    cell.terminal_status is ChallengeStatus.PASS
                    and cell.stability_runs >= 2
                )
                for cell in equivalent_cells
            )
            for equivalent_cells in obligations.values()
        ):
            outcome = OutcomeStatus.PASS
        elif leaf.authority not in {"A", "B", "C"}:
            outcome = OutcomeStatus.PROVISIONAL
        else:
            outcome = OutcomeStatus.UNKNOWN
        leaves[requirement_id] = replace(leaf, status=outcome)
    partitions = dict(stack.requirement_graph.challenge_partitions)
    for cell in cells.values():
        if cell.patch_hash != stack.patch_hash or cell.trace_bundle_id is None:
            continue
        unit = binding.units.get(cell.binding_id)
        if unit is None:
            continue
        partition_status = (
            OutcomeStatus.PASS
            if cell.terminal_status is ChallengeStatus.PASS else
            OutcomeStatus.UNREACHABLE
            if cell.terminal_status is ChallengeStatus.UNREACHABLE else
            OutcomeStatus.FAIL
            if cell.terminal_status is ChallengeStatus.FAIL else
            OutcomeStatus.UNKNOWN
        )
        for partition_id in unit.branch_partition_ids:
            partition = partitions.get(partition_id)
            if partition is not None and partition.kind == cell.input_recipe.kind:
                partitions[partition_id] = replace(
                    partition, status=partition_status,
                )
    requirement = RequirementGraph(
        leaves=leaves,
        challenge_partitions=partitions,
        evidence_hash=stack.requirement_graph.evidence_hash,
    )
    _LAST_UPDATE_METRICS["requirement_update_seconds"] = (
        time.monotonic() - requirement_started
    )
    challenge_started = time.monotonic()
    # Binding status was computed against the old requirement hash. Repointing
    # it after the leaf outcome update keeps the sparse relation atomic.
    binding = replace(binding, requirement_hash=requirement.graph_hash())
    challenge = replace(
        stack.challenge_graph,
        binding_hash=binding.graph_hash(),
        cells=cells,
    )
    result = GraphStack(
        patch_hash=stack.patch_hash,
        revision=stack.revision,
        requirement_graph=requirement,
        program_graph=program,
        binding_graph=binding,
        challenge_graph=challenge,
    )
    result.validate()
    _LAST_UPDATE_METRICS["challenge_materialization_seconds"] = (
        time.monotonic() - challenge_started
    )
    return result
