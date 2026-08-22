from __future__ import annotations

from dataclasses import replace

from reachpatch.models.base import stable_id
from reachpatch.models.reach_avoid import (
    ChallengeSelection, RegressionItem, RegressionPlan, ReachAvoidState,
)
from reachpatch.models.graphs import (
    BindingGraph, BindingStatus, BindingUnit, ChallengeGraph, ChallengeStatus,
    GraphStack, ImpactCone, RequirementGraph,
)
from reachpatch.models.evidence import ActualDiff, OutcomeStatus
from reachpatch.challenge_graph.models import challenge_obligation_key


def build_diff_conditioned_regression_plan(
    state: ReachAvoidState,
    actual_diff: ActualDiff,
    impact_cone: ImpactCone,
) -> RegressionPlan:
    cells = state.graph_stack.challenge_graph.cells
    selected: list[str] = []
    requirements: set[str] = set()
    items: list[RegressionItem] = []
    risk_ids = impact_cone.all_risk_ids()
    for cell in cells.values():
        if cell.patch_hash != state.graph_stack.patch_hash:
            continue
        related = (
            cell.requirement_id in state.graph_stack.requirement_graph.leaves
            and (
                cell.terminal_status.value == "PASS"
                or cell.kind == "PRESERVATION"
                or bool(set(cell.changed_hunk_ids).intersection(
                    hunk.hunk_id for hunk in actual_diff.hunks
                ))
            )
        )
        if related or cell.binding_id in state.graph_stack.binding_graph.units:
            selected.append(cell.challenge_id)
            requirements.add(cell.requirement_id)
            binding = state.graph_stack.binding_graph.units.get(cell.binding_id)
            impact_path = next((
                item for item in (binding.program_symbol_ids if binding else ())
                if item in risk_ids
            ), impact_cone.cone_id)
            for hunk_id in cell.changed_hunk_ids or tuple(
                hunk.hunk_id for hunk in actual_diff.hunks
            ):
                items.append(RegressionItem(
                    cell.requirement_id, impact_path, cell.binding_id,
                    cell.challenge_id, hunk_id,
                ))
    for unit in state.graph_stack.binding_graph.units.values():
        if set(unit.program_symbol_ids).intersection(impact_cone.all_risk_ids()):
            selected.extend(unit.challenge_ids)
            requirements.add(unit.requirement_id)
    return RegressionPlan(
        challenge_ids=tuple(dict.fromkeys(selected)),
        requirement_ids=tuple(sorted(requirements)),
        impact_path_ids=impact_cone.all_risk_ids(),
        changed_hunk_ids=tuple(hunk.hunk_id for hunk in actual_diff.hunks),
        items=tuple(dict.fromkeys(items)),
    )


def _map_program_symbols(
    source: GraphStack,
    trial: GraphStack,
    node_ids: tuple[str, ...],
) -> tuple[str, ...]:
    current_by_identity: dict[tuple[object, ...], list[str]] = {}
    for node in trial.program_graph.nodes.values():
        current_by_identity.setdefault(
            (node.path, node.symbol, node.kind), [],
        ).append(node.node_id)
    mapped: list[str] = []
    for node_id in node_ids:
        if node_id in trial.program_graph.nodes:
            mapped.append(node_id)
            continue
        old = source.program_graph.nodes.get(node_id)
        if old is None:
            continue
        mapped.extend(current_by_identity.get((old.path, old.symbol, old.kind), ()))
    return tuple(dict.fromkeys(mapped))[:128]


def _map_path_class(
    source: GraphStack,
    trial: GraphStack,
    path_class_id: str,
    mapped_symbols: tuple[str, ...],
):
    exact = trial.program_graph.path_classes.get(path_class_id)
    if exact is not None:
        return exact
    old = source.program_graph.path_classes.get(path_class_id)
    if old is None:
        return None
    mapped = set(mapped_symbols)
    candidates = tuple(
        path for path in trial.program_graph.path_classes.values()
        if path.entrypoint == old.entrypoint
        and path.dispatch_route == old.dispatch_route
        and path.exit_kind == old.exit_kind
        and path.observed_effect_kind == old.observed_effect_kind
    )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda path: (
            -len(mapped.intersection(path.node_ids)),
            path.loop_class != old.loop_class,
            path.recursion_class != old.recursion_class,
            path.path_class_id,
        ),
    )


def _materialize_required_replays(
    state: ReachAvoidState,
    trial: GraphStack,
    regression_plan: RegressionPlan,
) -> None:
    """Retarget durable regression obligations that disappeared from impact."""

    required_ids = set(regression_plan.challenge_ids)
    required_ids.update(
        failure.challenge_id for failure in state.confirmed_failures
        if failure.open and failure.patch_hash == state.graph_stack.patch_hash
    )
    cells = dict(trial.challenge_graph.cells)
    units = dict(trial.binding_graph.units)
    leaves = dict(trial.requirement_graph.leaves)
    existing_obligations = {
        challenge_obligation_key(cell)
        for cell in trial.challenge_graph.active_cells()
    }
    current_hunks = regression_plan.changed_hunk_ids
    impact = trial.program_graph.impact_cone
    for challenge_id in sorted(required_ids):
        old_cell = state.graph_stack.challenge_graph.cells.get(challenge_id)
        if old_cell is None:
            continue
        obligation = challenge_obligation_key(old_cell)
        if obligation in existing_obligations:
            continue
        old_binding = state.graph_stack.binding_graph.units.get(old_cell.binding_id)
        old_requirement = state.graph_stack.requirement_graph.leaves.get(
            old_cell.requirement_id
        )
        if old_binding is None or old_requirement is None or not current_hunks:
            continue
        mapped_symbols = _map_program_symbols(
            state.graph_stack, trial, old_binding.program_symbol_ids,
        )
        path = _map_path_class(
            state.graph_stack, trial, old_cell.path_class_id, mapped_symbols,
        )
        if path is None:
            continue
        path_symbols = tuple(
            node_id for node_id in path.node_ids
            if node_id in trial.program_graph.nodes
        )
        symbols = tuple(dict.fromkeys(mapped_symbols + path_symbols))[:128]
        if not symbols:
            continue
        leaves[old_requirement.requirement_id] = replace(
            old_requirement, status=OutcomeStatus.UNKNOWN,
        )
        binding_id = stable_id(
            "regression-replay-binding", trial.patch_hash,
            old_binding.binding_id, old_requirement.requirement_id,
            path.path_class_id, current_hunks,
        )
        replay_id = stable_id(
            "regression-replay-challenge", trial.patch_hash,
            old_cell.challenge_id, binding_id, old_cell.input_recipe.recipe_id,
            old_cell.oracle.oracle_id,
        )
        units[binding_id] = BindingUnit(
            binding_id=binding_id,
            requirement_id=old_requirement.requirement_id,
            path_class_id=path.path_class_id,
            program_symbol_ids=symbols,
            branch_partition_ids=(),
            changed_hunk_ids=current_hunks,
            causal_cut_ids=tuple(
                cut_id for cut_id in old_binding.causal_cut_ids
                if cut_id in trial.program_graph.causal_cuts
            ),
            impact_cone_ids=((impact.cone_id,) if impact is not None else ()),
            target_check_ids=old_binding.target_check_ids,
            preservation_check_ids=old_binding.preservation_check_ids,
            challenge_ids=(replay_id,),
            trace_bundle_ids=(),
            counterexample_ids=(),
            authority=old_cell.oracle.authority,
            status=BindingStatus.STATIC_ACTIONABLE,
            evidence_ids=tuple(dict.fromkeys(
                old_binding.evidence_ids + old_cell.oracle.source_evidence_ids
            )),
        )
        cells[replay_id] = replace(
            old_cell,
            challenge_id=replay_id,
            patch_hash=trial.patch_hash,
            binding_id=binding_id,
            path_class_id=path.path_class_id,
            changed_hunk_ids=current_hunks,
            baseline_outcome=None,
            patched_outcome=None,
            trace_bundle_id=None,
            stability_runs=0,
            terminal_status=(
                ChallengeStatus.UNREACHABLE
                if old_cell.terminal_status is ChallengeStatus.UNREACHABLE
                else ChallengeStatus.PENDING
            ),
            origin="REGRESSION_REPLAY",
        )
        existing_obligations.add(obligation)
    requirement = RequirementGraph(
        leaves=leaves,
        challenge_partitions=dict(trial.requirement_graph.challenge_partitions),
        evidence_hash=trial.requirement_graph.evidence_hash,
    )
    binding = BindingGraph(
        patch_hash=trial.patch_hash,
        requirement_hash=requirement.graph_hash(),
        program_hash=trial.program_graph.graph_hash(),
        units=units,
        gaps=trial.binding_graph.gaps,
    )
    challenge = ChallengeGraph(
        patch_hash=trial.patch_hash,
        binding_hash=binding.graph_hash(),
        cells=cells,
        frontier_attempts=dict(trial.challenge_graph.frontier_attempts),
    )
    trial.requirement_graph = requirement
    trial.binding_graph = binding
    trial.challenge_graph = challenge
    trial.validate()


def materialize_trial_challenges(
    state: ReachAvoidState,
    trial_graph_stack: GraphStack,
    regression_plan: RegressionPlan,
    *,
    max_batch: int = 6,
) -> ChallengeSelection:
    _materialize_required_replays(state, trial_graph_stack, regression_plan)
    planned_requirements = set(regression_plan.requirement_ids)
    planned_hunks = set(regression_plan.changed_hunk_ids)
    # RegressionItem is the durable four-graph relation captured before the
    # trial: Requirement -> impact path -> Binding -> Challenge -> hunk. Map
    # it onto the new patch's cells before applying the deterministic order.
    planned_items = tuple(regression_plan.items)

    def item_matches(cell, item: RegressionItem) -> bool:
        binding = trial_graph_stack.binding_graph.units.get(cell.binding_id)
        impact = trial_graph_stack.program_graph.impact_cone
        impact_matches = (
            binding is not None
            and item.impact_path_id in binding.program_symbol_ids
        ) or (impact is not None and item.impact_path_id == impact.cone_id)
        return (
            cell.requirement_id == item.requirement_id
            and (
                item.changed_hunk_id in cell.changed_hunk_ids
                or item.changed_hunk_id in planned_hunks
            )
            and impact_matches
        )

    def item_rank(cell) -> int:
        return min((
            index for index, item in enumerate(planned_items)
            if item_matches(cell, item)
        ), default=len(planned_items))

    cells = [
        cell for cell in trial_graph_stack.challenge_graph.active_cells()
        if cell.terminal_status not in {
            ChallengeStatus.UNREACHABLE, ChallengeStatus.STALE,
        }
        if cell.requirement_id in planned_requirements
        or bool(planned_hunks.intersection(cell.changed_hunk_ids))
        or cell.hard
    ]
    locked_ids = state.locked_checks.target_ids | state.locked_checks.preservation_ids

    def priority(cell):
        binding = trial_graph_stack.binding_graph.units.get(cell.binding_id)
        locked_replay = bool(
            binding
            and locked_ids.intersection(
                binding.target_check_ids + binding.preservation_check_ids
            )
        )
        known_failure = any(
            failure.open
            and failure.patch_hash == state.graph_stack.patch_hash
            and failure.requirement_id == cell.requirement_id
            for failure in state.confirmed_failures
        )
        return (
            not cell.hard,
            not (cell.oracle.trusted and cell.oracle.executable),
            not known_failure,
            not locked_replay,
            not bool(cell.changed_hunk_ids),
            cell.origin != "PUBLIC_CHECK",
            item_rank(cell),
            cell.execution_scenario.timeout_seconds,
            cell.challenge_id,
        )

    cells.sort(key=priority)
    selected_cells = []
    covered: set[str] = set()
    for cell in cells:
        group = challenge_obligation_key(cell)
        if group in covered:
            continue
        selected_cells.append(cell)
        covered.add(group)
        if len(selected_cells) >= max_batch:
            break
    for cell in cells:
        if len(selected_cells) >= max_batch:
            break
        if cell not in selected_cells:
            selected_cells.append(cell)
    selected = tuple(cell.challenge_id for cell in selected_cells)
    return ChallengeSelection(selected, (), exhausted=not selected)
