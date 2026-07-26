from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from reachpatch.binding_graph.models import BindingGraph
from reachpatch.challenge_graph.models import ChallengeCell, ChallengeGraph, ScenarioProposal
from reachpatch.challenge_graph.operators import ScenarioOperatorRegistry
from reachpatch.challenge_graph.recipes import RecipeCompiler
from reachpatch.execution.models import PairedTraceBundle
from reachpatch.models.base import stable_id
from reachpatch.models.core import Frontier
from reachpatch.models.enums import ChallengeTerminalStatus, OutcomeStatus
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.requirement_graph.models import RequirementGraph


def _admit(proposal: ScenarioProposal, binding_graph: BindingGraph) -> tuple[bool, str]:
    unit = binding_graph.units[proposal.binding_unit_id]
    if unit.oracle_id != proposal.locked_oracle_id:
        return False, "oracle_not_locked_to_binding"
    if proposal.scenario.oracle.oracle_id != proposal.locked_oracle_id:
        return False, "scenario_changed_locked_oracle"
    if not proposal.scenario.oracle.active_and_trusted:
        return False, "oracle_not_active_ABC"
    if proposal.unresolved_fields:
        return False, "proposal_has_unresolved_fields"
    try:
        RecipeCompiler().validate(proposal.recipe)
    except ValueError as exc:
        return False, f"recipe_rejected:{exc}"
    if not proposal.expected_observation_class:
        return False, "observation_channel_missing"
    return True, "admitted"


def admit_scenario(
    proposal: ScenarioProposal,
    binding_graph: BindingGraph,
) -> tuple[bool, str]:
    """Public challenge-admission contract; correctness remains oracle-locked."""

    return _admit(proposal, binding_graph)


def materialize_challenges(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    binding_graph: BindingGraph,
    *,
    registry: ScenarioOperatorRegistry | None = None,
    diff_hash: str = "BASELINE",
) -> ChallengeGraph:
    if binding_graph.requirement_graph_hash != requirement_graph.semantic_layer_hash():
        raise ValueError("Binding Graph references a stale Requirement Graph")
    if binding_graph.program_graph_hash != program_graph.program_hash():
        raise ValueError("Binding Graph references a stale Program Graph")
    challenge_graph = ChallengeGraph(
        requirement_graph_hash=binding_graph.requirement_graph_hash,
        program_graph_hash=binding_graph.program_graph_hash,
        binding_graph_hash=binding_graph.graph_hash(),
        diff_hash=diff_hash,
    )
    registry = registry or ScenarioOperatorRegistry.default()
    exact_keys: set[tuple[object, ...]] = set()
    for unit in sorted(binding_graph.units.values(), key=lambda item: item.unit_id):
        obligation = requirement_graph.path_obligations.get(unit.path_obligation_id)
        leaf = requirement_graph.leaves[unit.leaf_id]
        if (
            leaf.mandatory
            and obligation is not None
            and not obligation.partition.proof.get("coverage_complete", False)
        ):
            challenge_graph.add_frontier(Frontier(
                frontier_id=stable_id(
                    "challenge-frontier", unit.unit_id, "universal-domain-coverage"
                ),
                kind="UNIVERSAL_DOMAIN_COVERAGE",
                owner_id=unit.unit_id,
                reason="finite witness exists but open-world quantified coverage is not proved",
                resolution_action="produce an exhaustive symbolic partition or a closed domain proof",
                hard=True,
                evidence_ids=leaf.supporting_evidence,
            ))
        if not unit.scenario_ids or unit.oracle_id is None:
            frontier = Frontier(
                frontier_id=stable_id("challenge-frontier", unit.unit_id, "materialization"),
                kind="CHALLENGE_MATERIALIZATION",
                owner_id=unit.unit_id,
                reason="binding lacks an executable scenario or oracle",
                resolution_action="resolve binding oracle/scenario frontier",
                hard=True,
                evidence_ids=(),
            )
            challenge_graph.add_frontier(frontier)
            cell = ChallengeCell(
                challenge_id=stable_id("challenge", unit.unit_id, frontier.frontier_id),
                binding_unit_id=unit.unit_id,
                quantified_partition={},
                path_class_id=unit.path_class_id,
                trigger_recipe_id=None,
                input_constraints=(),
                observation_contract_id="unresolved",
                oracle_id=unit.oracle_id,
                baseline_outcome=None,
                patched_outcome=None,
                diff_dependency={},
                stability_status="NOT_EXECUTED",
                terminal_status=ChallengeTerminalStatus.UNKNOWN_ORACLE,
                evidence=(frontier.frontier_id,),
                scenario_id=None,
                operator_id="unmaterialized",
                changed_dimension="oracle_or_scenario",
                origin="BASELINE_GRAPH",
                hard=True,
                graph_hashes={
                    "requirement": challenge_graph.requirement_graph_hash,
                    "program": challenge_graph.program_graph_hash,
                    "binding": challenge_graph.binding_graph_hash,
                },
            )
            challenge_graph.add_cell(cell, recipe=None, scenario=None)
            continue
        applicable_count = 0
        proposal_count = 0
        for operator in registry.operators():
            if not operator.applicable(unit, requirement_graph, program_graph, binding_graph):
                continue
            applicable_count += 1
            proposals = operator.propose(unit, requirement_graph, program_graph, binding_graph)
            proposal_count += len(proposals)
            for proposal in proposals:
                admitted, reason = _admit(proposal, binding_graph)
                if not admitted:
                    frontier = Frontier(
                        frontier_id=stable_id("challenge-frontier", proposal.proposal_id, reason),
                        kind="SCENARIO_ADMISSION",
                        owner_id=unit.unit_id,
                        reason=reason,
                        resolution_action="repair the scenario while preserving the locked oracle",
                        hard=proposal.hard,
                        evidence_ids=proposal.graph_witness_ids,
                    )
                    challenge_graph.add_frontier(frontier)
                    continue
                obligation = requirement_graph.path_obligations[unit.path_obligation_id]
                exact_key = (
                    unit.unit_id,
                    proposal.partition_id,
                    proposal.route_id,
                    proposal.scenario.observe.contract_id,
                    proposal.locked_oracle_id,
                    proposal.scenario.evidence_cluster_id,
                    proposal.changed_dimension,
                )
                if exact_key in exact_keys:
                    continue
                exact_keys.add(exact_key)
                challenge_id = stable_id("challenge", exact_key, proposal.recipe.recipe_id, diff_hash)
                cell = ChallengeCell(
                    challenge_id=challenge_id,
                    binding_unit_id=unit.unit_id,
                    quantified_partition=obligation.partition.to_dict(),
                    path_class_id=unit.path_class_id,
                    trigger_recipe_id=proposal.recipe.recipe_id,
                    input_constraints=obligation.partition.constraints,
                    observation_contract_id=proposal.scenario.observe.contract_id,
                    oracle_id=proposal.locked_oracle_id,
                    baseline_outcome=None,
                    patched_outcome=None,
                    diff_dependency={"graph_witness_ids": list(proposal.graph_witness_ids)},
                    stability_status="NOT_EXECUTED",
                    terminal_status=ChallengeTerminalStatus.PENDING,
                    evidence=proposal.scenario.evidence_ids,
                    scenario_id=proposal.scenario.scenario_id,
                    operator_id=proposal.operator_id,
                    changed_dimension=proposal.changed_dimension,
                    origin=proposal.origin,
                    hard=proposal.hard,
                    graph_hashes={
                        "requirement": challenge_graph.requirement_graph_hash,
                        "program": challenge_graph.program_graph_hash,
                        "binding": challenge_graph.binding_graph_hash,
                    },
                )
                challenge_graph.add_cell(
                    cell, recipe=proposal.recipe, scenario=proposal.scenario
                )
        if applicable_count and not proposal_count:
            frontier = Frontier(
                frontier_id=stable_id("challenge-frontier", unit.unit_id, "empty-proposal"),
                kind="OPERATOR_EMPTY_PROPOSAL",
                owner_id=unit.unit_id,
                reason="applicable scenario operators produced no executable partition",
                resolution_action="resolve constraint or recipe construction frontier",
                hard=True,
                evidence_ids=(),
            )
            challenge_graph.add_frontier(frontier)
    return challenge_graph


def record_execution(
    challenge_graph: ChallengeGraph,
    challenge_id: str,
    bundle: PairedTraceBundle,
) -> ChallengeCell:
    mapping = {
        OutcomeStatus.PASS: ChallengeTerminalStatus.PASS,
        OutcomeStatus.FAIL: ChallengeTerminalStatus.FAIL,
        OutcomeStatus.UNKNOWN: ChallengeTerminalStatus.UNKNOWN_EXECUTION,
        OutcomeStatus.BLOCKED: ChallengeTerminalStatus.BLOCKED_EXTERNAL,
        OutcomeStatus.FLAKY: ChallengeTerminalStatus.FLAKY,
        OutcomeStatus.BLOCKED_EXTERNAL: ChallengeTerminalStatus.BLOCKED_EXTERNAL,
        OutcomeStatus.UNSUPPORTED: ChallengeTerminalStatus.UNSUPPORTED,
    }
    terminal = mapping.get(bundle.status, ChallengeTerminalStatus.UNKNOWN_EXECUTION)
    return challenge_graph.update_cell(
        challenge_id,
        baseline_outcome=bundle.base_bundle.stable_status.value,
        patched_outcome=bundle.patch_bundle.stable_status.value,
        stability_status=bundle.stability_status,
        terminal_status=terminal,
        execution_bundle_id=bundle.paired_bundle_id,
    )


def execute_challenges(
    challenge_graph: ChallengeGraph,
    executor,
    base_repository: str | Path,
    patch_repository: str | Path,
    *,
    challenge_ids: Iterable[str] | None = None,
) -> list[PairedTraceBundle]:
    selected = sorted(challenge_ids or challenge_graph.cells)
    bundles: list[PairedTraceBundle] = []
    execution_cache: dict[tuple[str, str], PairedTraceBundle] = {}
    appended_bundle_ids: set[str] = set()
    for challenge_id in selected:
        cell = challenge_graph.cells[challenge_id]
        if cell.terminal_status != ChallengeTerminalStatus.PENDING:
            continue
        if cell.trigger_recipe_id is None or cell.scenario_id is None:
            challenge_graph.update_cell(
                challenge_id,
                terminal_status=ChallengeTerminalStatus.UNKNOWN_EXECUTION,
                stability_status="NOT_MATERIALIZED",
            )
            continue
        recipe = challenge_graph.recipes[cell.trigger_recipe_id]
        scenario = challenge_graph.scenarios[cell.scenario_id]
        cache_key = (recipe.recipe_id, scenario.scenario_id)
        bundle = execution_cache.get(cache_key)
        if bundle is None:
            bundle = executor.execute_paired(
                recipe, base_repository, patch_repository, scenario
            )
            execution_cache[cache_key] = bundle
        record_execution(challenge_graph, challenge_id, bundle)
        if bundle.paired_bundle_id not in appended_bundle_ids:
            bundles.append(bundle)
            appended_bundle_ids.add(bundle.paired_bundle_id)
    return bundles
