from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable
import time

from reachpatch.binding_graph.models import BindingGraph
from reachpatch.challenge_graph.models import (
    ChallengeCell, ChallengeGraph, ChallengePriority, ScenarioProposal,
)
from reachpatch.challenge_graph.operators import ScenarioOperatorRegistry
from reachpatch.challenge_graph.recipes import RecipeCompiler
from reachpatch.execution.models import PairedTraceBundle
from reachpatch.models.base import stable_id
from reachpatch.models.base import SerializableRecord
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
    max_challenges: int | None = None,
    deadline: float | None = None,
) -> ChallengeGraph:
    total_started = time.perf_counter()
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
    active_units = sorted(
        (
            unit for unit in binding_graph.units.values()
            if unit.status in {"ACTIVE", "READY"}
        ),
        key=lambda item: item.unit_id,
    )
    per_unit_limit = (
        max(1, max_challenges // max(1, len(active_units)))
        if max_challenges is not None else None
    )
    deadline_truncated = False
    for unit in active_units:
        if deadline is not None and time.monotonic() >= deadline:
            deadline_truncated = True
            break
        if max_challenges is not None and len(challenge_graph.cells) >= max_challenges:
            break
        unit_start_count = len(challenge_graph.cells)
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
                hard=False,
                evidence_ids=(),
            )
            challenge_graph.add_frontier(frontier)
            continue
        applicable_count = 0
        proposal_count = 0
        for operator in registry.operators():
            if deadline is not None and time.monotonic() >= deadline:
                deadline_truncated = True
                break
            if not operator.applicable(unit, requirement_graph, program_graph, binding_graph):
                continue
            applicable_count += 1
            proposals = operator.propose(unit, requirement_graph, program_graph, binding_graph)
            proposal_count += len(proposals)
            for proposal in proposals:
                if deadline is not None and time.monotonic() >= deadline:
                    deadline_truncated = True
                    break
                if max_challenges is not None and len(challenge_graph.cells) >= max_challenges:
                    break
                if (
                    per_unit_limit is not None
                    and len(challenge_graph.cells) - unit_start_count >= per_unit_limit
                ):
                    break
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
    if deadline_truncated:
        challenge_graph.add_frontier(Frontier(
            frontier_id=stable_id(
                "challenge-frontier", "ANALYSIS_TRUNCATED",
                binding_graph.graph_hash(), diff_hash,
            ),
            kind="ANALYSIS_TRUNCATED",
            owner_id=binding_graph.assignment_id,
            reason="challenge materialization deadline reached",
            resolution_action="continue the bounded priority queue on demand",
            hard=False,
            evidence_ids=(),
        ))
    challenge_graph.build_timings = {
        "total_seconds": time.perf_counter() - total_started,
    }
    challenge_graph.build_stats = {
        "active_unit_count": len(active_units),
        "cell_count": len(challenge_graph.cells),
        "frontier_count": len(challenge_graph.frontiers),
        "deadline_truncated": int(deadline_truncated),
    }
    return challenge_graph


def materialize_active_challenges(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    binding_graph: BindingGraph,
    *,
    actual_diff,
    previous_outcomes,
    max_challenges: int,
    deadline: float | None = None,
) -> ChallengeGraph:
    if max_challenges < 1:
        raise ValueError("max_challenges must be positive")
    total_started = time.perf_counter()
    graph = materialize_challenges(
        requirement_graph, program_graph, binding_graph,
        diff_hash=getattr(actual_diff, "canonical_diff_hash", "BASELINE") if actual_diff else "BASELINE",
        max_challenges=max_challenges * 3,
        deadline=deadline,
    )
    changed_files = set(getattr(actual_diff, "changed_files", ()))
    failed_units = {
        item.unit_id for item in (previous_outcomes or {}).values()
        if getattr(getattr(item, "status", None), "value", getattr(item, "status", None)) == "FAIL"
    }
    scored: list[tuple[float, str, ChallengePriority]] = []
    deadline_truncated = False
    for cell in graph.cells.values():
        if deadline is not None and time.monotonic() >= deadline:
            deadline_truncated = True
            break
        unit = binding_graph.units[cell.binding_unit_id]
        leaf = requirement_graph.leaves[unit.leaf_id]
        touched = any(
            str(program_graph.nodes[node_id].attributes.get("file", "")) in changed_files
            for node_id in unit.interaction_path_ids
            if node_id in program_graph.nodes
        )
        priority = ChallengePriority(
            authority=max(leaf.weight, 0.5) * (
                4.0 if leaf.authority_class.value != "PRESERVATION" else 2.0
            ),
            failure_risk=3.0 if unit.unit_id in failed_units else 2.0 if cell.hard else 1.0,
            diff_relevance=3.0 if touched else 1.0,
            information_gain=2.0 if cell.changed_dimension in {"guard", "dispatch", "fallback", "representation"} else 1.0,
            execution_cost=1.0 + len(cell.input_constraints) * 0.25,
        )
        scored.append((priority.score, cell.challenge_id, priority))
    ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
    best_by_unit: dict[str, tuple[float, str, ChallengePriority]] = {}
    for item in ranked:
        unit_id = graph.cells[item[1]].binding_unit_id
        best_by_unit.setdefault(unit_id, item)
    unit_seeds = sorted(
        best_by_unit.values(), key=lambda item: (-item[0], item[1])
    )[:max_challenges]
    selected_ids = {item[1] for item in unit_seeds}
    for _, challenge_id, _ in ranked:
        if len(selected_ids) >= max_challenges:
            break
        selected_ids.add(challenge_id)
    bounded = ChallengeGraph(
        requirement_graph_hash=graph.requirement_graph_hash,
        program_graph_hash=graph.program_graph_hash,
        binding_graph_hash=graph.binding_graph_hash,
        diff_hash=graph.diff_hash,
        version=graph.version,
    )
    priorities = {challenge_id: priority for _, challenge_id, priority in scored}
    for challenge_id in sorted(selected_ids):
        cell = graph.cells[challenge_id]
        bounded.add_cell(
            cell,
            recipe=graph.recipes.get(cell.trigger_recipe_id or ""),
            scenario=graph.scenarios.get(cell.scenario_id or ""),
        )
        bounded.priorities[challenge_id] = priorities[challenge_id]
    for frontier in graph.frontiers.values():
        if frontier.owner_id in binding_graph.units:
            unit_cells = bounded.by_binding_unit.get(frontier.owner_id, ())
            if unit_cells:
                bounded.add_frontier(frontier)
    if deadline_truncated:
        bounded.add_frontier(Frontier(
            frontier_id=stable_id(
                "challenge-frontier", "ANALYSIS_TRUNCATED",
                binding_graph.graph_hash(),
            ),
            kind="ANALYSIS_TRUNCATED",
            owner_id=binding_graph.assignment_id,
            reason="active challenge materialization deadline reached",
            resolution_action="resume priority queue materialization on demand",
            hard=False,
            evidence_ids=(),
        ))
    bounded.build_timings = {
        "materialize_all_candidates_seconds": float(
            graph.build_timings.get("total_seconds", 0.0)
        ),
        "priority_selection_seconds": max(
            0.0,
            time.perf_counter() - total_started
            - float(graph.build_timings.get("total_seconds", 0.0)),
        ),
        "total_seconds": time.perf_counter() - total_started,
    }
    bounded.build_stats = {
        "candidate_cell_count": len(graph.cells),
        "active_cell_count": len(bounded.cells),
        "active_unit_count": len({item.binding_unit_id for item in bounded.cells.values()}),
        "frontier_count": len(bounded.frontiers),
        "deadline_truncated": int(deadline_truncated),
    }
    return bounded


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


@dataclass(frozen=True, slots=True)
class ChallengeExecutionResult(SerializableRecord):
    bundles: tuple[PairedTraceBundle, ...]
    executed_challenge_ids: tuple[str, ...]
    skipped_challenge_ids: tuple[str, ...]
    trace_delta: dict
    real_execution_count: int

    def __iter__(self):
        return iter(self.bundles)

    def __len__(self) -> int:
        return len(self.bundles)


def execute_challenges(
    challenge_graph: ChallengeGraph,
    executor,
    base_repository: str | Path,
    patch_repository: str | Path,
    *,
    challenge_ids: Iterable[str] | None = None,
    max_workers: int = 1,
) -> ChallengeExecutionResult:
    if max_workers < 1:
        raise ValueError("max_workers must be positive")
    selected = sorted(challenge_ids or challenge_graph.cells)
    bundles: list[PairedTraceBundle] = []
    appended_bundle_ids: set[str] = set()
    executed: list[str] = []
    skipped: list[str] = []
    locations: dict[tuple[str, int, str], dict] = {}
    challenge_keys: dict[str, tuple[str, str]] = {}
    jobs: dict[tuple[str, str], tuple[object, object]] = {}
    for challenge_id in selected:
        cell = challenge_graph.cells[challenge_id]
        if cell.terminal_status != ChallengeTerminalStatus.PENDING:
            skipped.append(challenge_id)
            continue
        if cell.trigger_recipe_id is None or cell.scenario_id is None:
            challenge_graph.update_cell(
                challenge_id,
                terminal_status=ChallengeTerminalStatus.UNKNOWN_EXECUTION,
                stability_status="NOT_MATERIALIZED",
            )
            skipped.append(challenge_id)
            continue
        recipe = challenge_graph.recipes[cell.trigger_recipe_id]
        scenario = challenge_graph.scenarios[cell.scenario_id]
        cache_key = (recipe.recipe_id, scenario.scenario_id)
        challenge_keys[challenge_id] = cache_key
        jobs.setdefault(cache_key, (recipe, scenario))

    execution_cache: dict[tuple[str, str], PairedTraceBundle] = {}
    if max_workers == 1 or len(jobs) <= 1:
        for cache_key in sorted(jobs):
            recipe, scenario = jobs[cache_key]
            execution_cache[cache_key] = executor.execute_paired(
                recipe, base_repository, patch_repository, scenario
            )
    else:
        # Each recipe executes in isolated worker subprocesses and a distinct
        # temporary directory.  Keep graph mutation on this thread so result
        # ordering and terminal-state updates remain deterministic.
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(jobs)),
            thread_name_prefix="reachpatch-challenge",
        ) as pool:
            futures = {
                pool.submit(
                    executor.execute_paired,
                    recipe,
                    base_repository,
                    patch_repository,
                    scenario,
                ): cache_key
                for cache_key, (recipe, scenario) in jobs.items()
            }
            try:
                for future in as_completed(futures):
                    execution_cache[futures[future]] = future.result()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise

    for challenge_id in selected:
        cache_key = challenge_keys.get(challenge_id)
        if cache_key is None:
            continue
        bundle = execution_cache[cache_key]
        record_execution(challenge_graph, challenge_id, bundle)
        executed.append(challenge_id)
        for trace_bundle in (bundle.base_bundle, bundle.patch_bundle):
            if trace_bundle.stability_status != "STABLE":
                continue
            for run in trace_bundle.runs:
                for event in run.trace_events:
                    key = (event.file, event.line, event.function)
                    locations[key] = {
                        "file": event.file, "line": event.line,
                        "end_line": event.line, "symbol": event.function,
                    }
        if bundle.paired_bundle_id not in appended_bundle_ids:
            bundles.append(bundle)
            appended_bundle_ids.add(bundle.paired_bundle_id)
    return ChallengeExecutionResult(
        bundles=tuple(bundles), executed_challenge_ids=tuple(executed),
        skipped_challenge_ids=tuple(sorted(set(skipped))),
        trace_delta={
            "locations": tuple(locations[key] for key in sorted(locations)),
            "stable_bundle_ids": tuple(sorted(
                item.paired_bundle_id for item in bundles if item.stability_status == "STABLE"
            )),
            "nonempty": bool(locations),
        },
        real_execution_count=len(executed),
    )
