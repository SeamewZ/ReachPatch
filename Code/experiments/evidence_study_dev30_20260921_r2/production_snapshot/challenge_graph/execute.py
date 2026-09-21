from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from reachpatch.execution.paired import execute_paired
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.evidence import (
    ConfirmedFailure, CounterexamplePacket, ExecutableOracle,
    PairClassification,
    failure_signature,
)
from reachpatch.models.graphs import ChallengeStatus, GraphStack
from reachpatch.models.reach_avoid import ChallengeRoundResult, ChallengeSelection, ReachAvoidState
from reachpatch.program_graph import (
    materialize_execution_path_class, update_program_graph_after_diff,
)
from reachpatch.program_graph.slicing import compute_causal_repair_cuts, match_trace_nodes
from reachpatch.execution.worktree import diff_between


def _divergence(baseline: str, patched: str):
    left = baseline.splitlines()
    right = patched.splitlines()
    for index, (before, after) in enumerate(zip(left, right)):
        if before != after:
            return {"line": index + 1, "baseline": before, "patched": after}
    if len(left) != len(right):
        return {"line": min(len(left), len(right)) + 1, "baseline": left[-1:] or None, "patched": right[-1:] or None}
    return None


def _executed_node_ids(graph, trace) -> tuple[str, ...]:
    ordered, _ = match_trace_nodes(graph, trace)
    return tuple(dict.fromkeys(ordered))


def _executed_source_paths(trace) -> set[str]:
    paths = set()
    for location in trace.executed_line_ids:
        path, separator, line = str(location).rpartition(":")
        if separator and line.isdigit():
            paths.add(path.replace("\\", "/"))
    return paths


def _synthesized_call_shape_error(cell, paired) -> bool:
    """Identify an error made by a generated direct caller, not the program.

    A direct recipe is only an executable probe.  When its own argument shape
    reaches Python's call binder before the intended observation, the result
    says nothing about the Requirement and must remain a recoverable frontier.
    Reporter-owned witness scripts deliberately do not take this path.
    """

    if cell.input_recipe.call_mode != "SYNTHESIZED_DIRECT":
        return False
    if (
        paired.patched.first_project_frame is not None
        or paired.baseline.first_project_frame is not None
        or set(paired.patched.executed_symbol_ids).intersection(
            cell.input_recipe.trace_symbols
        )
    ):
        # The project code was entered, so the exception is an observation of
        # that code rather than a failure to construct the probe call.
        return False
    output = "\n".join((
        paired.baseline.observation.stdout,
        paired.baseline.observation.stderr,
        paired.patched.observation.stdout,
        paired.patched.observation.stderr,
    ))
    lowered = output.casefold()
    return "typeerror:" in lowered and any(
        marker in lowered for marker in (
            "positional argument", "keyword argument", "required positional",
            "takes ", "given",
        )
    )


def _resolve_execution_anchor_ids(
    previous_graph,
    current_graph,
    node_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Carry static binding anchors across a changed-file reparse."""

    resolved: list[str] = []
    by_identity: dict[tuple[str, str, object], list[str]] = {}
    for node in current_graph.nodes.values():
        by_identity.setdefault((node.path, node.symbol, node.kind), []).append(
            node.node_id
        )
    for node_id in node_ids:
        if node_id in current_graph.nodes:
            resolved.append(node_id)
            continue
        previous = previous_graph.nodes.get(node_id)
        if previous is None:
            continue
        resolved.extend(by_identity.get(
            (previous.path, previous.symbol, previous.kind), (),
        ))
    return tuple(dict.fromkeys(resolved))


def execute_challenge_round(
    state: ReachAvoidState,
    selection: ChallengeSelection,
    baseline_tree: Path,
    working_tree: Path,
    *,
    previous_tree: Path | None = None,
) -> ChallengeRoundResult:
    started = time.monotonic()
    baseline_tree = Path(baseline_tree)
    working_tree = Path(working_tree)
    previous_tree = Path(previous_tree) if previous_tree is not None else None
    executions = []
    counterexamples = []
    failures = []
    scenario_frontiers = []
    causal_cuts = dict(state.graph_stack.program_graph.causal_cuts)
    cells = dict(state.graph_stack.challenge_graph.cells)
    cache_hits = 0
    for challenge_id in selection.challenge_ids:
        cell = cells.get(challenge_id)
        if cell is None or cell.patch_hash != state.graph_stack.patch_hash:
            continue
        if cell.terminal_status in {
            ChallengeStatus.UNREACHABLE, ChallengeStatus.STALE,
        }:
            continue
        retry_key = "|".join((
            challenge_id, state.graph_stack.patch_hash,
            content_hash(cell.input_recipe.to_dict()),
            cell.oracle.oracle_id, str(state.graph_stack.revision),
        ))
        state.challenge_attempts[retry_key] = state.challenge_attempts.get(retry_key, 0) + 1
        if not cell.execution_scenario.command:
            cells[challenge_id] = replace(
                cell, terminal_status=ChallengeStatus.UNSUPPORTED,
            )
            continue
        role = "PRESERVATION" if cell.kind == "PRESERVATION" else "TARGET"
        paired, cache_hit = execute_paired(
            baseline_tree=baseline_tree,
            patched_tree=working_tree,
            previous_tree=previous_tree,
            recipe=cell.input_recipe,
            scenario=cell.execution_scenario,
            oracle=cell.oracle,
            check_id=(cell.input_recipe.source_check_id or ((
                state.graph_stack.binding_graph.units[cell.binding_id].preservation_check_ids
                if cell.kind == "PRESERVATION" else
                state.graph_stack.binding_graph.units[cell.binding_id].target_check_ids
            ) or (cell.challenge_id,))[0]),
            challenge_id=challenge_id,
            patch_hash=state.graph_stack.patch_hash,
            role=role,
            observation_contract=cell.observation_contract,
            stability_runs=2,
            cache_dir=state.run_root / "execution_cache",
        )
        cache_hits += int(cache_hit)
        executions.append(paired)
        if _synthesized_call_shape_error(cell, paired):
            terminal = ChallengeStatus.UNKNOWN
            scenario_frontiers.append(
                "SYNTHESIZED_CALL_SHAPE:"
                f"{cell.challenge_id}:caller argument binding failed"
            )
        elif paired.oracle_authority not in {"A", "B", "C"}:
            terminal = ChallengeStatus.EXPLORATION_ONLY
        elif (
            role == "PRESERVATION"
            and paired.classification is PairClassification.TARGET_STILL_FAILING
        ):
            terminal = ChallengeStatus.UNKNOWN
        elif paired.classification in {
            PairClassification.TARGET_FIXED,
            PairClassification.PASS_PRESERVED,
        } and paired.stable_runs >= 2:
            terminal = ChallengeStatus.PASS
        elif paired.classification is PairClassification.UNKNOWN:
            terminal = ChallengeStatus.UNKNOWN
        else:
            terminal = ChallengeStatus.FAIL
        resolved_oracle = cell.oracle
        if paired.oracle_id != cell.oracle.oracle_id:
            resolved_oracle = ExecutableOracle(
                paired.oracle_id,
                paired.oracle_authority,
                paired.expected_relation,
                paired.baseline.observation,
                True,
                (paired.baseline.trace_bundle_id,),
            )
        cells[challenge_id] = replace(
            cell,
            oracle=resolved_oracle,
            authority=resolved_oracle.authority,
            baseline_outcome=paired.baseline.observation.status,
            patched_outcome=paired.patched.observation.status,
            trace_bundle_id=paired.paired_bundle_id,
            stability_runs=paired.stable_runs,
            terminal_status=terminal,
        )
    cumulative_diff = diff_between(baseline_tree, working_tree)
    program_delta = update_program_graph_after_diff(
        state.graph_stack.program_graph,
        working_tree,
        cumulative_diff,
        tuple(execution.patched for execution in executions),
        (),
        getattr(state, "graph_budget", None),
    )
    graph = program_delta.graph
    public_check_paths = {
        argument.replace("\\", "/").removeprefix("./")
        for candidate in cells.values()
        if candidate.origin == "PUBLIC_CHECK"
        for argument in candidate.execution_scenario.command
        if argument.endswith(".py")
    }
    graph = replace(
        graph,
        nodes={
            node_id: replace(
                node,
                editable=False,
                metadata={**node.metadata, "public_check": True},
            ) if node.path in public_check_paths else node
            for node_id, node in graph.nodes.items()
        },
    )
    units = dict(state.graph_stack.binding_graph.units)
    for paired in executions:
        cell = cells.get(paired.challenge_id)
        if cell is None or cell.binding_id not in units:
            continue
        unit = units[cell.binding_id]
        executed_node_ids = _executed_node_ids(graph, paired.patched)
        static_path = graph.path_classes.get(unit.path_class_id)
        static_path_hit = bool(
            static_path is not None
            and set(static_path.node_ids).intersection(executed_node_ids)
        )
        if static_path_hit:
            continue
        anchor_ids = _resolve_execution_anchor_ids(
            state.graph_stack.program_graph,
            graph,
            tuple(dict.fromkeys(
                unit.program_symbol_ids + cell.input_recipe.trace_symbols
            )),
        )
        graph, execution_path = materialize_execution_path_class(
            graph, paired.patched, anchor_ids,
        )
        if execution_path is None:
            continue
        retained_static_symbols = tuple(
            node_id for node_id in unit.program_symbol_ids
            if node_id in graph.nodes
        )
        units[cell.binding_id] = replace(
            unit,
            path_class_id=execution_path.path_class_id,
            program_symbol_ids=tuple(dict.fromkeys(
                retained_static_symbols + execution_path.node_ids
            ))[:128],
        )
        for related_id, related in tuple(cells.items()):
            if related.binding_id == cell.binding_id:
                cells[related_id] = replace(
                    related, path_class_id=execution_path.path_class_id,
                )

    for paired in executions:
        cell = cells.get(paired.challenge_id)
        if (
            cell is None
            or cell.terminal_status is not ChallengeStatus.FAIL
            or paired.stable_runs < 2
            or (
                cell.kind == "PRESERVATION"
                and paired.classification
                is not PairClassification.PRESERVATION_REGRESSION
            )
        ):
            continue
        binding = units[cell.binding_id]
        executed_node_ids = _executed_node_ids(graph, paired.patched)
        path = graph.path_classes.get(binding.path_class_id)
        binding_path_hit = bool(
            set(executed_node_ids).intersection(binding.program_symbol_ids)
        ) and (
            path is None
            or bool(set(executed_node_ids).intersection(path.node_ids))
        )
        changed_hunk_paths = {
            hunk.path for hunk in cumulative_diff.hunks
            if hunk.hunk_id in cell.changed_hunk_ids
        }
        changed_hunk_hit = bool(
            changed_hunk_paths.intersection(_executed_source_paths(paired.patched))
        )
        localization_failure = not binding_path_hit and not changed_hunk_hit
        causal_trace = replace(
            paired.patched,
            executed_path_ids=tuple(dict.fromkeys(executed_node_ids)),
        )
        executed_path_classes = (binding.path_class_id,)
        cuts = compute_causal_repair_cuts(
            graph,
            causal_trace,
            tuple(
                hunk for hunk in cumulative_diff.hunks
                if hunk.hunk_id in cell.changed_hunk_ids
            ),
        )
        causal_cuts.update((cut.cut_id, cut) for cut in cuts)
        signature = failure_signature(paired.patched.observation)
        counterexample_id = stable_id(
            "counterexample", paired.challenge_id,
            paired.paired_bundle_id, signature,
        )
        packet = CounterexamplePacket(
            counterexample_id=counterexample_id,
            requirement_id=cell.requirement_id,
            binding_id=cell.binding_id,
            challenge_id=paired.challenge_id,
            patch_hash=cell.patch_hash,
            reproduction_command=cell.execution_scenario.command,
            concrete_input=cell.input_recipe.concrete_input,
            input_derivation=cell.input_recipe.derivation,
            oracle_id=paired.oracle_id,
            oracle_authority=paired.oracle_authority,
            expected_relation=paired.expected_relation,
            baseline_observation=paired.baseline.observation.to_dict(),
            patched_observation=paired.patched.observation.to_dict(),
            failure_signature=signature,
            first_divergence=_divergence(
                paired.baseline.observation.stdout + paired.baseline.observation.stderr,
                paired.patched.observation.stdout + paired.patched.observation.stderr,
            ),
            executed_path_ids=executed_path_classes,
            changed_hunk_ids=cell.changed_hunk_ids,
            causal_cut_ids=tuple(cut.cut_id for cut in cuts),
            impact_risk_ids=(
                graph.impact_cone.all_risk_ids()
                if graph.impact_cone else ()
            ),
            protected_target_ids=tuple(sorted(state.locked_checks.target_ids)),
            protected_preservation_ids=tuple(sorted(state.locked_checks.preservation_ids)),
            suggested_action_families=("EDIT_CAUSAL_CUT", "REPLAY_IMPACT_CONSUMERS"),
            frontier_kind=("LOCALIZATION_FAILURE" if localization_failure else
                           "PRESERVATION_REGRESSION" if cell.kind == "PRESERVATION" else
                           "BEHAVIOR_FAILURE"),
            observation_projection=paired.patched.observation.to_dict(),
            command_cwd=cell.execution_scenario.cwd,
            environment=cell.execution_scenario.environment,
            backend="shared-executor",
            stdout=paired.patched.observation.stdout,
            stderr=paired.patched.observation.stderr,
            first_project_frame=paired.patched.first_project_frame,
            binding_path_hit=binding_path_hit,
            changed_hunk_hit=changed_hunk_hit,
            changed_hunks=tuple(hunk.to_dict() for hunk in cumulative_diff.hunks
                                if hunk.hunk_id in cell.changed_hunk_ids),
            protected_behavior=tuple(sorted(state.locked_checks.target_ids | state.locked_checks.preservation_ids)),
            failure_kind=("LOCALIZATION_FAILURE" if localization_failure else "BEHAVIOR_FAILURE"),
            stability_evidence={"stable_runs": paired.stable_runs, "oracle": paired.oracle_authority},
            expected_observation=cell.oracle.expected,
            incumbent_observation=(
                paired.previous.observation.to_dict()
                if paired.previous is not None
                else paired.baseline.observation.to_dict()
            ),
            trial_observation=paired.patched.observation.to_dict(),
            comparator=cell.observation_contract.normalized_comparator,
        )
        counterexamples.append(packet)
        failures.append(ConfirmedFailure(
            failure_id=stable_id("failure", counterexample_id, signature),
            requirement_id=cell.requirement_id,
            binding_id=cell.binding_id,
            challenge_id=paired.challenge_id,
            counterexample_id=counterexample_id,
            patch_hash=cell.patch_hash,
            failure_signature=signature,
            causal_component_id=(cuts[0].cut_id if cuts else binding.path_class_id),
            first_divergence=packet.first_divergence,
            hard=cell.hard,
            priority=0 if cell.hard else 1,
        ))
    from reachpatch.reach_avoid.graph_stack import apply_execution_to_graph_stack
    program = replace(graph, causal_cuts=causal_cuts)
    binding = replace(
        state.graph_stack.binding_graph,
        program_hash=program.graph_hash(),
        units=units,
    )
    challenge = replace(
        state.graph_stack.challenge_graph,
        binding_hash=binding.graph_hash(),
        cells=cells,
    )
    provisional_stack = GraphStack(
        patch_hash=state.graph_stack.patch_hash,
        revision=state.graph_stack.revision,
        requirement_graph=state.graph_stack.requirement_graph,
        program_graph=program,
        binding_graph=binding,
        challenge_graph=challenge,
    )
    updated = apply_execution_to_graph_stack(
        provisional_stack, tuple(executions), tuple(counterexamples),
        program_update_seconds=program_delta.update_seconds,
    )
    executed_ids = {item.challenge_id for item in executions}
    executable_frontiers = tuple(sorted(
        cell.challenge_id for cell in updated.challenge_graph.active_cells()
        if cell.oracle.trusted and cell.oracle.executable
        and cell.terminal_status in {
            ChallengeStatus.PENDING, ChallengeStatus.UNKNOWN,
        }
        and (
            cell.challenge_id not in selection.challenge_ids
            or cell.challenge_id in executed_ids
        )
    ))
    return ChallengeRoundResult(
        selected_challenge_ids=selection.challenge_ids,
        executed_challenge_ids=tuple(item.challenge_id for item in executions),
        executions=tuple(executions),
        counterexamples=tuple(counterexamples),
        confirmed_failures=tuple(failures),
        updated_graph_stack=updated,
        frontiers=tuple(dict.fromkeys(
            tuple(frontier for _, frontier in selection.recovery_actions)
            + tuple(scenario_frontiers)
            + executable_frontiers
        )),
        execution_seconds=time.monotonic() - started,
        cache_hits=cache_hits,
    )
