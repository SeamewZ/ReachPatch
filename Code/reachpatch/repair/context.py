from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from reachpatch.models.base import SerializableRecord
from reachpatch.models.enums import OutcomeStatus


@dataclass(frozen=True, slots=True)
class RepairContext(SerializableRecord):
    mode: str
    issue: str
    working_diff: str
    active_target_check: dict[str, Any] | None
    baseline_output: dict[str, Any] | None
    patched_output: dict[str, Any] | None
    failure_signature: str | None
    first_project_frame: dict[str, Any] | None
    reproduction_command: tuple[str, ...]
    relevant_source_snippets: tuple[dict[str, Any], ...]
    causal_cut_candidates: tuple[dict[str, Any], ...]
    previous_revision: dict[str, Any] | None
    previous_failure_reason: str | None
    preservation_checks: tuple[dict[str, Any], ...]
    semantic_ambiguities: tuple[str, ...]
    requirement_coverage: tuple[dict[str, Any], ...]
    failed_checks: tuple[dict[str, Any], ...]
    counterexamples: tuple[dict[str, Any], ...]
    first_trace_divergences: tuple[dict[str, Any], ...]
    active_program_slice: dict[str, Any]
    causal_repair_cuts: tuple[dict[str, Any], ...]
    impact_risks: tuple[dict[str, Any], ...]
    preserved_passes: tuple[dict[str, Any], ...]
    failed_mechanisms: tuple[str, ...]
    remaining_budget: dict[str, Any]


def _issue_text(state) -> str:
    primary_issue = str(state.runtime_config.get("primary_issue", "")).strip()
    values = [
        evidence.content
        for evidence in state.semantic_graph.evidence.values()
        if evidence.kind.value in {"ISSUE_NORMATIVE", "ISSUE_WITNESS"}
    ]
    issue = primary_issue or "\n".join(dict.fromkeys(values))
    hints = str(state.runtime_config.get("generation_hints", "")).strip()
    if hints and hints not in issue:
        issue += f"\n\nPublic hints:\n{hints}"
    return issue


def build_repair_context(
    state,
    *,
    mode: Literal["INITIAL", "COUNTEREXAMPLE_REPAIR", "ROOT_RECOVERY"],
) -> RepairContext:
    target_checks = tuple(
        getattr(getattr(state, "target_recovery", None), "targets", ())
    )
    target_ids = {item.check_id for item in target_checks}
    comparisons = tuple(getattr(state, "check_comparisons", ()))
    priority = {
        "TARGET_STILL_FAILING": 0,
        "TARGET_REGRESSED": 1,
        "TARGET_FIXED": 2,
    }
    active_comparison = min(
        (item for item in comparisons if item.check_id in target_ids),
        key=lambda item: priority.get(item.classification.value, 9),
        default=None,
    )
    active_check = next(
        (
            item for item in target_checks
            if active_comparison is not None and item.check_id == active_comparison.check_id
        ),
        target_checks[0] if target_checks else None,
    )
    baseline_execution = (
        active_comparison.baseline if active_comparison is not None
        else getattr(getattr(state, "target_recovery", None), "execution_for", lambda _: None)(
            active_check.check_id
        ) if active_check is not None else None
    )
    patched_execution = active_comparison.patched if active_comparison is not None else None
    coverage = []
    outcomes_by_leaf: dict[str, list] = {}
    for outcome in state.outcomes.values():
        unit = state.binding_graph.units.get(outcome.unit_id)
        if unit:
            outcomes_by_leaf.setdefault(unit.leaf_id, []).append(outcome)
    for leaf in state.requirement_graph.leaves.values():
        observed = outcomes_by_leaf.get(leaf.leaf_id, ())
        coverage.append({
            "leaf_id": leaf.leaf_id,
            "formula": leaf.formula,
            "authority": leaf.authority.value,
            "kind": leaf.authority_class.value,
            "statuses": sorted({item.status.value for item in observed}),
            "active_binding_count": sum(
                unit.leaf_id == leaf.leaf_id and unit.status == "ACTIVE"
                for unit in state.binding_graph.units.values()
            ),
        })
    failed_rows = [{
        "outcome_id": item.outcome_id,
        "status": item.status.value,
        "origin": item.failure_origin,
        "observation": item.observation,
    } for item in state.outcomes.values() if item.status == OutcomeStatus.FAIL]
    failed_rows.extend({
        "outcome_id": item.get("check_id"),
        "status": item.get("classification"),
        "origin": "PUBLIC_CHECK",
        "observation": {
            "command": item.get("command", ()),
            "baseline_return_code": item.get("baseline_return_code"),
            "patched_return_code": item.get("patched_return_code"),
            "patched_stdout": item.get("patched_stdout", ""),
            "patched_stderr": item.get("patched_stderr", ""),
        },
    } for item in state.runtime_metrics.get("last_public_check_comparisons", ())
    if item.get("classification") in {"STABLE_FAIL", "PRESERVATION_REGRESSION"})
    failed = tuple(failed_rows)
    packets = tuple({
        "counterexample_id": item.counterexample_id,
        "minimal_input": item.minimal_input,
        "expected": item.expected_observation,
        "actual": item.actual_observation,
        "reproduction_recipe_id": item.reproduction_recipe_id,
        "candidate_repair_cut_ids": list(item.candidate_repair_cut_ids),
        "preservation_path_ids": list(item.preservation_path_ids),
        "uncertain_information": list(item.uncertain_information),
    } for item in state.counterexamples[-20:])
    divergences = tuple(
        paired.first_divergence
        for paired in state.trace_bundles.values()
        if paired.first_divergence is not None
    )
    slice_files = sorted(state.program_graph.file_index)
    slice_summary = {
        "files": slice_files,
        "node_count": len(state.program_graph.nodes),
        "edge_count": len(state.program_graph.edges),
        "callable_count": len(state.program_graph.cfgs),
        "frontiers": [
            {"kind": item.kind, "reason": item.reason, "hard": item.hard}
            for item in state.program_graph.frontiers.values()
        ][:20],
        "symbols": sorted(state.program_graph.symbol_index)[:40],
    }
    cuts = tuple({
        "unit_id": unit.unit_id,
        "node_ids": list(unit.repair_cut_node_ids),
        "path_obligation_id": unit.path_obligation_id,
    } for unit in state.binding_graph.units.values() if unit.repair_cut_node_ids)
    impacts = tuple({
        "unit_id": unit.unit_id,
        "node_ids": list(unit.impact_cone_node_ids[:100]),
        "preservation_node_ids": list(unit.preservation_node_ids[:100]),
    } for unit in state.binding_graph.units.values() if unit.impact_cone_node_ids)
    passes = tuple({
        "outcome_id": item.outcome_id,
        "path_obligation_id": item.path_obligation_id,
        "observation": item.observation,
    } for item in state.outcomes.values() if item.status == OutcomeStatus.PASS and item.kind == "PRESERVATION")
    failed_mechanisms = tuple(sorted({
        attempt.mechanism_class
        for attempts in state.mechanism_memory.values() for attempt in attempts
        if attempt.result != "COMMIT"
    } | set(state.runtime_metrics.get("failed_generator_mechanisms", ()))))
    source_snippets = []
    source_root = __import__("pathlib").Path(state.checkpoint.snapshot_tree)
    candidate_nodes = tuple(dict.fromkeys(
        node_id for causal in getattr(state, "causal_slices", ())
        for node_id in causal.candidate_cut_node_ids
    ))[:5]
    cut_candidates = []
    for node_id in candidate_nodes:
        node = state.program_graph.nodes.get(node_id)
        if node is None:
            continue
        relative = str(node.attributes.get("file", ""))
        line = int(node.attributes.get("line", 1))
        end = int(node.attributes.get("end_line", line))
        cut = {
            "node_id": node_id,
            "relative_path": relative,
            "start_line": line,
            "end_line": end,
            "symbol": str(node.attributes.get("qualified_name", node.label)),
            "reason": "backward causal slice from stable target failure",
        }
        cut_candidates.append(cut)
        path = source_root / relative
        if path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(1, line - 3)
            stop = min(len(lines), end + 3)
            source_snippets.append({
                **cut,
                "snippet_start_line": start,
                "snippet_end_line": stop,
                "content": "\n".join(lines[start - 1:stop]),
            })
    if active_check is not None:
        run_root = __import__("pathlib").Path(state.run_root).resolve()
        for raw_path in active_check.temporary_artifact_paths[:1]:
            path = __import__("pathlib").Path(raw_path).resolve()
            try:
                path.relative_to(run_root)
            except ValueError:
                continue
            if not path.is_file():
                continue
            content = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[:160]
            source_snippets.append({
                "relative_path": str(path.relative_to(run_root)),
                "start_line": 1,
                "end_line": len(content),
                "symbol": "<public-target-reproduction>",
                "reason": "stable public target reproduction",
                "origin": "TARGET_REPRODUCTION_ARTIFACT",
                "content": "\n".join(content),
            })
    preservation_checks = tuple({
        "check_id": item.check_id,
        "command": list(item.command),
        "selector": item.selector,
    } for item in getattr(
        getattr(state, "target_recovery", None), "preservation_checks", ()
    ))
    previous = state.repair_history[-1] if state.repair_history else None
    return RepairContext(
        mode=mode, issue=_issue_text(state),
        working_diff=state.checkpoint.patch.canonical_diff,
        active_target_check=(active_check.to_dict() if active_check else None),
        baseline_output=(baseline_execution.to_dict() if baseline_execution else None),
        patched_output=(patched_execution.to_dict() if patched_execution else None),
        failure_signature=(
            patched_execution.failure_signature if patched_execution
            else baseline_execution.failure_signature if baseline_execution else None
        ),
        first_project_frame=(
            patched_execution.first_project_frame if patched_execution
            else baseline_execution.first_project_frame if baseline_execution else None
        ),
        reproduction_command=(active_check.command if active_check else ()),
        relevant_source_snippets=tuple(source_snippets),
        causal_cut_candidates=tuple(cut_candidates),
        previous_revision=(previous.graph_delta if previous else None),
        previous_failure_reason=(
            str(previous.graph_delta.get("avoid_reasons", "")) if previous else None
        ),
        preservation_checks=preservation_checks,
        semantic_ambiguities=tuple(
            getattr(getattr(state, "hypothesis_set", None), "unresolved_decision_ids", ())
        ),
        requirement_coverage=tuple(coverage), failed_checks=failed,
        counterexamples=packets, first_trace_divergences=divergences,
        active_program_slice=slice_summary, causal_repair_cuts=cuts,
        impact_risks=impacts, preserved_passes=passes,
        failed_mechanisms=failed_mechanisms,
        remaining_budget=state.remaining_budget.to_dict(),
    )
