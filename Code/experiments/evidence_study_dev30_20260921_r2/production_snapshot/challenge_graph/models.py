"""Challenge records are defined once in ``reachpatch.models.graphs``."""

from collections.abc import Iterable

from reachpatch.models.base import stable_id
from reachpatch.models.graphs import (
    ChallengeCell, ChallengeGraph, ChallengeStatus, ExecutableScenario,
    InputRecipe, InputRecipeResult,
)


def challenge_obligation_key(cell: ChallengeCell) -> str:
    """Identify one executable obligation independently of graph-local IDs."""

    return stable_id(
        "challenge-obligation",
        cell.requirement_id,
        cell.kind == "PRESERVATION",
        cell.execution_scenario.command,
        cell.execution_scenario.cwd,
        cell.execution_scenario.environment,
        cell.input_recipe.concrete_input,
        cell.oracle.authority,
        cell.oracle.relation,
        cell.oracle.expected,
        cell.oracle.source_evidence_ids,
    )


def closed_challenge_obligation_keys(
    cells: Iterable[ChallengeCell],
) -> frozenset[str]:
    """Return obligations discharged by stable PASS or unreachable proof."""

    closed: set[str] = set()
    for cell in cells:
        if cell.terminal_status is ChallengeStatus.UNREACHABLE or (
            cell.terminal_status is ChallengeStatus.PASS
            and cell.stability_runs >= 2
        ):
            closed.add(challenge_obligation_key(cell))
    return frozenset(closed)


def open_high_challenge_ids(
    cells: Iterable[ChallengeCell],
) -> tuple[str, ...]:
    """Choose one deterministic representative for each open HARD obligation."""

    materialized = tuple(cells)
    closed = closed_challenge_obligation_keys(materialized)
    representatives: dict[str, str] = {}
    for cell in materialized:
        if not cell.hard or not cell.oracle.trusted or not cell.oracle.executable:
            continue
        obligation = challenge_obligation_key(cell)
        if obligation in closed:
            continue
        current = representatives.get(obligation)
        if current is None or cell.challenge_id < current:
            representatives[obligation] = cell.challenge_id
    return tuple(sorted(representatives.values()))

__all__ = [name for name in globals() if not name.startswith("_")]
