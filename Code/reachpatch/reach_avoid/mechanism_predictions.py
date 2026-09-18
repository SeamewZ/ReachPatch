"""Typed, falsifiable mechanism predictions; expected paths are not oracles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from reachpatch.models.base import SerializableRecord, stable_id, content_hash
from .dynamic_reach_avoid_graph import GraphNodeKind as N, GraphEdgeKind as E


@dataclass(frozen=True)
class MechanismPrediction(SerializableRecord):
    prediction_id: str
    kind: str
    subject_node_ids: tuple[str, ...]
    input_partition_id: str
    expected_change: Any
    oracle_id: str | None = None
    check_ids: tuple[str, ...] = ()


def compile_hypothesis_predictions(graph: Any, hypothesis: Any) -> tuple[MechanismPrediction, ...]:
    predictions: list[MechanismPrediction] = []
    failure = graph.nodes.get(hypothesis.failure_id)
    failed_obligation = graph.nodes.get(failure.metadata.get("obligation_id")) if failure else None
    bindings = [node for node in graph.nodes.values() if node.kind is N.OBLIGATION
                and node.metadata.get("check_id") and node.metadata.get("command")
                and (node.node_id == failed_obligation.node_id if failed_obligation else
                     node.metadata.get("goal_id") == hypothesis.requirement_id and node.metadata.get("role") == "TARGET")]
    for binding in bindings:
        check_id = binding.metadata["check_id"]
        partition = content_hash((binding.metadata.get("command"), binding.metadata.get("cwd"), binding.metadata.get("input_recipe")))
        for cut_id in hypothesis.causal_cut_ids:
            cut = graph.nodes.get(cut_id)
            if cut is None:
                continue
            kind = "BRANCH_OUTCOME_CHANGE" if cut.metadata.get("branch_ids") else "RETURN_SUMMARY_CHANGE"
            outcomes = {str(value).lower().removeprefix("branch_")
                        for value in cut.metadata.get("observed_outcomes", ())} & {"taken", "not_taken"}
            path_prediction = ({"from": next(iter(outcomes)),
                                "to": "not_taken" if outcomes == {"taken"} else "taken"}
                               if kind == "BRANCH_OUTCOME_CHANGE" and len(outcomes) == 1 else
                               {"status": "UNSPECIFIED_PATH", "reason": "NO_UNAMBIGUOUS_BRANCH_DIRECTION"})
            predictions.append(MechanismPrediction(stable_id("prediction", hypothesis.hypothesis_id, cut_id, kind, partition),
                kind, (cut_id,), partition, path_prediction, check_ids=(check_id,)))
        predictions.append(MechanismPrediction(stable_id("prediction", hypothesis.hypothesis_id, "contract", partition),
            "CONTRACT_PASS", (), partition, "STABLE_PASS", stable_id("oracle", check_id), (check_id,)))
    parent = graph.nodes.get(hypothesis.parent_checkpoint_id)
    for check_id in parent.metadata.get("locked_successes", ()) if parent else ():
        predictions.append(MechanismPrediction(stable_id("prediction", hypothesis.hypothesis_id, "lock", check_id),
            "LOCK_PRESERVED", (), stable_id("locked-input", check_id), "STABLE_PASS", stable_id("oracle", check_id), (check_id,)))
    if not bindings:
        graph.record_update("PREDICTION_UNBOUND", hypothesis_id=hypothesis.hypothesis_id, reason="NO_EXECUTABLE_INPUT_BINDING")
    for prediction in predictions:
        graph.add_node(N.OBLIGATION, node_id=prediction.prediction_id, status="PREDICTION",
                       metadata={**prediction.to_dict(), "obligation_kind": "MECHANISM_PREDICTION"})
        graph.add_edge(E.DERIVED_FROM, prediction.prediction_id, hypothesis.hypothesis_id)
    return tuple(predictions)


def align_execution_paths(parent: Any, trial: Any, *, file: str, symbol: str, kind: str,
                          parent_line_start: int = 0, parent_line_end: int = 0,
                          expected_change: dict[str, Any] | None = None,
                          parent_source_version_id: str | None = None,
                          trial_source_version_id: str | None = None) -> dict[str, Any]:
    def features(result: Any, restrict_parent: bool) -> dict[tuple[Any, ...], list[Any]]:
        values: dict[tuple[Any, ...], list[Any]] = {}
        for event in getattr(getattr(result, "trace", None), "events", ()) or ():
            if hasattr(event, "to_dict"):
                event = event.to_dict()
            if not isinstance(event, dict):
                continue
            expected_version = parent_source_version_id if restrict_parent else trial_source_version_id
            if expected_version is not None and event.get("source_version_id") != expected_version:
                continue
            path = str(event.get("file", event.get("path", "")))
            function = str(event.get("function", ""))
            anchor = event.get("source_anchor")
            if path != file or (symbol and function != symbol) or not anchor:
                continue
            subject_line = int(str(event.get("branch_id", "")).rsplit(":", 1)[-1]) if (
                kind == "BRANCH_OUTCOME_CHANGE" and str(event.get("branch_id", "")).rsplit(":", 1)[-1].isdigit()
            ) else int(event.get("line", 0))
            if restrict_parent and parent_line_start and not parent_line_start <= subject_line <= parent_line_end:
                continue
            if kind == "BRANCH_OUTCOME_CHANGE":
                value = event.get("branch_outcome")
                if value not in {"taken", "not_taken"}:
                    continue
            else:
                value = event.get("return_summary")
                if not value:
                    continue
            values.setdefault((path, function, tuple(anchor)), []).append(value)
        return values
    before, after = features(parent, True), features(trial, False)
    shared = sorted(before.keys() & after.keys())
    if not shared:
        return {"status": "NOT_OBSERVABLE", "reason": "NO_ALIGNED_AST_ANCHOR"}
    if expected_change is not None:
        if not {"from", "to"}.issubset(expected_change):
            return {"status": "NOT_OBSERVABLE", "reason": "NO_SPECIFIC_PATH_PREDICTION"}
        matched = [key for key in shared if before[key] and all(value == expected_change["from"] for value in before[key])]
        if not matched:
            return {"status": "NOT_OBSERVABLE", "reason": "PARENT_DOES_NOT_MATCH_PREDICTED_INPUT_PATH"}
        changed = all(after[key] and all(value == expected_change["to"] for value in after[key]) for key in matched)
        return {"status": "SUPPORTED" if changed else "CONTRADICTED",
                "prediction": expected_change, "anchors": matched}
    changed = any(before[key] != after[key] for key in shared)
    return {"status": "SUPPORTED" if changed else "CONTRADICTED",
            "anchors": shared, "before": [before[key] for key in shared], "after": [after[key] for key in shared]}


def evaluate_hypothesis_predictions(graph: Any, hypothesis: Any, checkpoint_id: str,
                                     parent_results: Sequence[Any], trial_results: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    before = {result.check_id: result for result in parent_results}
    reports: list[dict[str, Any]] = []
    for prediction in compile_hypothesis_predictions(graph, hypothesis):
        checks = [result for result in trial_results if result.check_id in before and result.check_id in prediction.check_ids]
        outcomes = []
        for result in checks:
            parent = before[result.check_id]
            if not (parent.stable and result.stable):
                outcome = {"status": "NOT_OBSERVABLE", "reason": "UNSTABLE_EXECUTION"}
            elif prediction.kind in {"CONTRACT_PASS", "LOCK_PRESERVED"}:
                outcome = {"status": "SUPPORTED" if str(result.status) == "PASS"
                           and result.authority in {"A", "B", "C"} and result.entered_target_code is True
                           else "CONTRADICTED"}
            else:
                cut = graph.nodes[prediction.subject_node_ids[0]]
                parent_version = graph.nodes[hypothesis.parent_checkpoint_id].metadata.get("source_version_id")
                trial_version = graph.nodes[checkpoint_id].metadata.get("source_version_id")
                outcome = align_execution_paths(parent, result, file=cut.file or "", symbol=cut.symbol or "", kind=prediction.kind,
                    parent_line_start=cut.line_start, parent_line_end=cut.line_end,
                    expected_change=prediction.expected_change,
                    parent_source_version_id=parent_version, trial_source_version_id=trial_version)
                if not parent_version or not trial_version:
                    outcome = {"status": "NOT_OBSERVABLE", "reason": "MISSING_CHECKPOINT_SOURCE_VERSION"}
                if outcome["status"] == "SUPPORTED" and not (
                    result.status == "PASS" and result.authority in {"A", "B", "C"}
                    and result.entered_target_code is True):
                    outcome = {**outcome, "status": "PATH_ONLY", "reason": "CONTRACT_NOT_SATISFIED"}
            outcomes.append({"check_id": result.check_id, **outcome})
        report = {"prediction_id": prediction.prediction_id, "checkpoint_id": checkpoint_id,
                  "kind": prediction.kind, "outcomes": outcomes,
                  "status": "NOT_EXECUTED" if not outcomes else
                      "CONTRADICTED" if any(item["status"] == "CONTRADICTED" for item in outcomes) else
                      "SUPPORTED" if all(item["status"] == "SUPPORTED" for item in outcomes) else "NOT_OBSERVABLE"}
        node_id = stable_id("prediction-result", checkpoint_id, prediction.prediction_id)
        graph.add_node(N.OBSERVATION, node_id=node_id, status=report["status"], metadata=report)
        graph.add_edge(E.DERIVED_FROM, node_id, prediction.prediction_id, static_or_dynamic="dynamic")
        graph.record_update("MECHANISM_PREDICTION_RESULT", **report)
        reports.append(report)
    return tuple(reports)
