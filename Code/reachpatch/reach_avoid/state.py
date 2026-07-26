from __future__ import annotations

from reachpatch.challenge_graph.models import ChallengeGraph
from reachpatch.execution.models import PairedTraceBundle
from reachpatch.models.base import stable_id
from reachpatch.models.controller import ReachAvoidState, UnitOutcome
from reachpatch.models.enums import ChallengeTerminalStatus, OutcomeStatus
from reachpatch.oracle.authority import evaluate_oracle


def _already_satisfied_target(bundle: PairedTraceBundle, scenario) -> OutcomeStatus | None:
    classifications = bundle.classifications
    if not classifications or not all(
        item.reason == "base_precondition_false" for item in classifications
    ):
        return None
    statuses = []
    for base_run, patch_run in zip(
        bundle.base_bundle.runs, bundle.patch_bundle.runs, strict=True
    ):
        base_eval = evaluate_oracle(scenario.oracle, base_run.run.channels)
        patch_eval = evaluate_oracle(scenario.oracle, patch_run.run.channels)
        if base_eval.status != OutcomeStatus.PASS:
            return None
        statuses.append(patch_eval.status)
    return statuses[0] if len(set(statuses)) == 1 else OutcomeStatus.FLAKY


def outcomes_from_challenges(
    state: ReachAvoidState,
    challenge_graph: ChallengeGraph,
    bundles: tuple[PairedTraceBundle, ...] | list[PairedTraceBundle],
) -> dict[str, UnitOutcome]:
    by_bundle = {item.paired_bundle_id: item for item in bundles}
    outcomes: dict[str, UnitOutcome] = {}
    terminal_mapping = {
        ChallengeTerminalStatus.PASS: OutcomeStatus.PASS,
        ChallengeTerminalStatus.FAIL: OutcomeStatus.FAIL,
        ChallengeTerminalStatus.FLAKY: OutcomeStatus.FLAKY,
        ChallengeTerminalStatus.UNKNOWN_ORACLE: OutcomeStatus.UNKNOWN_ORACLE,
        ChallengeTerminalStatus.UNKNOWN_EXECUTION: OutcomeStatus.UNKNOWN_EXECUTION,
        ChallengeTerminalStatus.BLOCKED_EXTERNAL: OutcomeStatus.BLOCKED_EXTERNAL,
        ChallengeTerminalStatus.UNSUPPORTED: OutcomeStatus.UNSUPPORTED,
        ChallengeTerminalStatus.INFEASIBLE_PROVED: OutcomeStatus.INFEASIBLE_PROVED,
        ChallengeTerminalStatus.PENDING: OutcomeStatus.BLOCKED,
    }
    graph_hashes = {
        "requirement": challenge_graph.requirement_graph_hash,
        "program": challenge_graph.program_graph_hash,
        "binding": challenge_graph.binding_graph_hash,
        "challenge": challenge_graph.graph_hash(),
        "diff": challenge_graph.diff_hash,
    }
    for cell in challenge_graph.cells.values():
        unit = state.binding_graph.units.get(cell.binding_unit_id)
        if unit is None:
            continue
        leaf = state.requirement_graph.leaves[unit.leaf_id]
        scenario = challenge_graph.scenarios.get(cell.scenario_id or "")
        bundle = by_bundle.get(cell.execution_bundle_id or "")
        status = terminal_mapping[cell.terminal_status]
        origin = "NOT_EXECUTED"
        stable = cell.stability_status == "STABLE"
        comparable = False
        observation = {}
        if bundle is not None:
            converted = (
                _already_satisfied_target(bundle, scenario)
                if scenario is not None and scenario.kind == "TARGET"
                else None
            )
            if converted is not None:
                status = converted
                origin = "ALREADY_SATISFIED_TARGET"
                if converted == OutcomeStatus.PASS:
                    challenge_graph.update_cell(
                        cell.challenge_id,
                        terminal_status=ChallengeTerminalStatus.PASS,
                    )
            elif bundle.classifications:
                origins = {item.failure_origin for item in bundle.classifications}
                origin = next(iter(origins)) if len(origins) == 1 else "MIXED"
            stable = bundle.stability_status == "STABLE"
            comparable = all(item.comparable for item in bundle.classifications)
            if bundle.patch_bundle.runs:
                observation = {
                    **bundle.patch_bundle.runs[0].run.channels,
                    "first_divergence": bundle.first_divergence,
                }
        kind = scenario.kind if scenario is not None else (
            "PRESERVATION" if leaf.authority_class.value == "PRESERVATION" else "TARGET"
        )
        outcome = UnitOutcome(
            outcome_id=stable_id(
                "unit-outcome", cell.challenge_id, status, cell.execution_bundle_id,
                graph_hashes,
            ),
            unit_id=unit.unit_id,
            path_obligation_id=unit.path_obligation_id,
            scenario_id=cell.scenario_id,
            challenge_id=cell.challenge_id,
            kind=kind,
            status=status,
            weight=leaf.weight,
            execution_bundle_id=cell.execution_bundle_id,
            failure_origin=origin,
            stable=stable,
            comparable=comparable,
            observation=observation,
            graph_hashes=graph_hashes,
        )
        outcomes[outcome.outcome_id] = outcome
    return outcomes


def classify_state_information(state: ReachAvoidState) -> dict[str, tuple[str, ...]]:
    groups = {
        "passed": [],
        "failed": [],
        "unknown": [],
        "blocked": [],
    }
    for item in state.outcomes.values():
        if item.status == OutcomeStatus.PASS:
            groups["passed"].append(item.outcome_id)
        elif item.status == OutcomeStatus.FAIL:
            groups["failed"].append(item.outcome_id)
        elif item.status in {OutcomeStatus.BLOCKED, OutcomeStatus.BLOCKED_EXTERNAL}:
            groups["blocked"].append(item.outcome_id)
        else:
            groups["unknown"].append(item.outcome_id)
    return {key: tuple(sorted(value)) for key, value in groups.items()}
