"""Initial patch entry point for the single-working-patch controller."""
from __future__ import annotations

from reachpatch.models.reach_avoid import GeneratorResult, ReachAvoidState, RepairObjective


class InitialPatchAgent:
    """Run initial DeepSeek coding without graph-gated repair state."""

    def __init__(self, repair_player) -> None:
        self._repair_player = repair_player

    def generate(
        self, state: ReachAvoidState, objective: RepairObjective,
    ) -> GeneratorResult:
        if objective.objective_kind != "INITIAL_PATCH":
            raise ValueError("InitialPatchAgent requires an INITIAL_PATCH objective")
        if objective.selected_frontier is not None:
            raise ValueError("initial generation must not depend on a RepairFrontier")
        return self._repair_player.revise_working_patch(state, objective, initial=True)
