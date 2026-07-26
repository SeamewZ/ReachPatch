from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from reachpatch.challenge_graph.models import ChallengeGraph
from reachpatch.challenge_graph.recipes import InputRecipe
from reachpatch.execution.models import PairedTraceBundle
from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import stable_id
from reachpatch.models.controller import CounterexamplePacket, ReachAvoidState
from reachpatch.models.enums import ChallengeTerminalStatus, OutcomeStatus


def _shrink_values(value: Any) -> list[Any]:
    candidates: list[Any] = []
    if isinstance(value, bool):
        candidates.append(False)
    elif isinstance(value, int):
        candidates.extend((0, 1, -1, value // 2))
    elif isinstance(value, float):
        candidates.extend((0.0, 1.0, -1.0, value / 2.0))
    elif isinstance(value, str):
        candidates.extend(("", value[:1], value[: max(1, len(value) // 2)]))
    elif isinstance(value, list):
        candidates.extend(([], value[:1], value[: max(1, len(value) // 2)]))
    elif isinstance(value, tuple):
        candidates.extend(((), value[:1], value[: max(1, len(value) // 2)]))
    elif isinstance(value, dict):
        candidates.append({})
        if value:
            first = next(iter(value))
            candidates.append({first: value[first]})
    return [item for item in candidates if item != value]


def _candidate_recipes(recipe: InputRecipe, limit: int) -> Iterable[InputRecipe]:
    emitted = 0
    for step_index, step in enumerate(recipe.stimulus):
        for field in ("args", "kwargs", "left", "right"):
            if field not in step:
                continue
            value = step[field]
            locations: list[tuple[Any, Any]] = []
            if isinstance(value, list):
                locations.extend((index, item) for index, item in enumerate(value))
            elif isinstance(value, dict):
                locations.extend(value.items())
            else:
                locations.append((None, value))
            for location, original in locations:
                if isinstance(original, dict) and set(original) == {"ref"}:
                    continue
                for shrunk in _shrink_values(original):
                    updated_step = dict(step)
                    if isinstance(value, list):
                        updated = list(value)
                        updated[int(location)] = shrunk
                        updated_step[field] = updated
                    elif isinstance(value, dict):
                        updated = dict(value)
                        updated[location] = shrunk
                        updated_step[field] = updated
                    else:
                        updated_step[field] = shrunk
                    stimulus = list(recipe.stimulus)
                    stimulus[step_index] = updated_step
                    try:
                        yield InputRecipe.create(
                            imports=recipe.imports,
                            setup=recipe.setup,
                            stimulus=stimulus,
                            observations=recipe.observations,
                            teardown=recipe.teardown,
                            traces=recipe.traces,
                            environment=recipe.environment,
                            resource_limits=recipe.resource_limits,
                            allow_network=recipe.allow_network,
                            allow_subprocess=recipe.allow_subprocess,
                            max_iteration_items=recipe.max_iteration_items,
                            provenance_ids=recipe.provenance_ids,
                        )
                    except ValueError:
                        continue
                    emitted += 1
                    if emitted >= limit:
                        return


def minimize_counterexample(
    recipe: InputRecipe,
    scenario,
    executor,
    base_repository: str | Path,
    patch_repository: str | Path,
    *,
    max_candidates: int = 24,
) -> tuple[InputRecipe, PairedTraceBundle]:
    best_recipe = recipe
    best_bundle = executor.execute_paired(recipe, base_repository, patch_repository, scenario)
    if best_bundle.status != OutcomeStatus.FAIL or best_bundle.stability_status != "STABLE":
        return best_recipe, best_bundle
    for candidate in _candidate_recipes(recipe, max_candidates):
        bundle = executor.execute_paired(
            candidate, base_repository, patch_repository, scenario
        )
        if bundle.status == OutcomeStatus.FAIL and bundle.stability_status == "STABLE":
            best_recipe = candidate
            best_bundle = bundle
    return best_recipe, best_bundle


def counterexample_from_challenge(
    state: ReachAvoidState,
    challenge_graph: ChallengeGraph,
    challenge_id: str,
    actual_diff: ActualDiff,
    *,
    transition_id: str,
    minimized_recipe: InputRecipe | None = None,
    bundle: PairedTraceBundle | None = None,
) -> CounterexamplePacket:
    cell = challenge_graph.cells[challenge_id]
    unit = state.binding_graph.units.get(cell.binding_unit_id)
    path = (
        state.requirement_graph.path_obligations.get(unit.path_obligation_id)
        if unit is not None else None
    )
    scenario = challenge_graph.scenarios.get(cell.scenario_id or "")
    recipe = minimized_recipe or challenge_graph.recipes.get(cell.trigger_recipe_id or "")
    paired = bundle or state.trace_bundles.get(cell.execution_bundle_id or "")
    base_observation: dict[str, Any] = {}
    patch_observation: dict[str, Any] = {}
    execution_ids: tuple[str, ...] = ()
    failure_origin = "CHALLENGE_FRONTIER"
    first_divergence: dict[str, Any] | None = None
    if paired is not None:
        if paired.base_bundle.runs:
            base_observation = paired.base_bundle.runs[0].run.channels
        if paired.patch_bundle.runs:
            patch_observation = paired.patch_bundle.runs[0].run.channels
        execution_ids = tuple(
            run.run.execution_id
            for trace_bundle in (paired.base_bundle, paired.patch_bundle)
            for run in trace_bundle.runs
        )
        origins = [item.failure_origin for item in paired.classifications]
        failure_origin = origins[0] if origins and len(set(origins)) == 1 else "UNSTABLE_ORIGIN"
        first_divergence = paired.first_divergence
    expected = scenario.oracle.relation if scenario is not None else None
    actual = {
        "baseline": base_observation,
        "patched": patch_observation,
        "first_divergence": first_divergence,
        "terminal_status": cell.terminal_status.value,
    }
    uncertain = []
    if cell.terminal_status not in {ChallengeTerminalStatus.FAIL, ChallengeTerminalStatus.PASS}:
        uncertain.append(f"challenge_status:{cell.terminal_status.value}")
    if scenario is None:
        uncertain.append("scenario_unmaterialized")
    minimal_input = {
        "recipe_id": recipe.recipe_id if recipe else None,
        "stimulus": list(recipe.stimulus) if recipe else [],
        "setup": list(recipe.setup) if recipe else [],
    }
    return CounterexamplePacket(
        counterexample_id=stable_id(
            "counterexample", transition_id, challenge_id, actual_diff.canonical_diff_hash,
            minimal_input, actual,
        ),
        transition_id=transition_id,
        path_obligation_id=unit.path_obligation_id if unit else None,
        binding_unit_id=unit.unit_id if unit else None,
        challenge_id=challenge_id,
        public_trigger_id=unit.trigger_id if unit else None,
        entrypoint_id=unit.entrypoint_id if unit else None,
        guarded_path_edge_ids=path.path_edge_ids if path else (),
        exit_kind=unit.exit_kind if unit else None,
        trusted_oracle_id=unit.oracle_id if unit else None,
        expected_observation=expected,
        actual_observation=actual,
        minimal_input=minimal_input,
        reproduction_recipe_id=recipe.recipe_id if recipe else None,
        raw_execution_ids=execution_ids,
        relevant_source_slice_ids=unit.interaction_path_ids if unit else (),
        causal_touch_witness_ids=tuple(
            cell.diff_dependency.get("relation_witness_ids", ())
        ),
        candidate_repair_cut_ids=unit.repair_cut_node_ids if unit else (),
        protected_sibling_path_ids=tuple(sorted(
            item.path_obligation_id
            for item in state.outcomes.values()
            if item.status == OutcomeStatus.PASS and item.path_obligation_id != (unit.path_obligation_id if unit else None)
        )),
        preservation_path_ids=unit.preservation_node_ids if unit else (),
        forbidden_behavior_ids=tuple(sorted(
            item.path_obligation_id
            for item in state.outcomes.values()
            if item.status == OutcomeStatus.PASS
        )),
        source_hash=state.checkpoint.patch.working_tree_hash,
        diff_hash=actual_diff.canonical_diff_hash,
        failure_origin=failure_origin,
        frontier_kind=(
            cell.terminal_status.value
            if cell.terminal_status not in {ChallengeTerminalStatus.FAIL, ChallengeTerminalStatus.PASS}
            else None
        ),
        uncertain_information=tuple(uncertain),
        mechanism_fingerprint_hash=str(actual_diff.fingerprint.get("hash")),
    )


def packets_for_nonpass_challenges(
    state: ReachAvoidState,
    challenge_graph: ChallengeGraph,
    actual_diff: ActualDiff,
    *,
    transition_id: str,
    challenge_ids: Iterable[str] | None = None,
    executor=None,
    base_repository: str | Path | None = None,
    patch_repository: str | Path | None = None,
    max_minimize_candidates: int = 24,
) -> tuple[CounterexamplePacket, ...]:
    selected = challenge_ids or challenge_graph.cells
    packets = []
    for challenge_id in sorted(selected):
        cell = challenge_graph.cells[challenge_id]
        if cell.terminal_status == ChallengeTerminalStatus.PASS:
            continue
        minimized_recipe = None
        minimized_bundle = None
        if (
            executor is not None
            and base_repository is not None
            and patch_repository is not None
            and cell.scenario_id
            and cell.trigger_recipe_id
            and cell.terminal_status == ChallengeTerminalStatus.FAIL
        ):
            scenario = challenge_graph.scenarios.get(cell.scenario_id)
            recipe = challenge_graph.recipes.get(cell.trigger_recipe_id)
            if scenario is not None and recipe is not None:
                try:
                    minimized_recipe, minimized_bundle = minimize_counterexample(
                        recipe,
                        scenario,
                        executor,
                        base_repository,
                        patch_repository,
                        max_candidates=max_minimize_candidates,
                    )
                except (OSError, RuntimeError, ValueError):
                    # Preserve the original executed packet and expose the
                    # failed minimization through its raw evidence instead of
                    # converting a valid failure into an UNKNOWN.
                    minimized_recipe = None
                    minimized_bundle = None
        packets.append(counterexample_from_challenge(
            state,
            challenge_graph,
            challenge_id,
            actual_diff,
            transition_id=transition_id,
            minimized_recipe=minimized_recipe,
            bundle=minimized_bundle,
        ))
    return tuple(packets)
