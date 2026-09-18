"""Decision views over DynamicReachAvoidGraph, never a second search structure."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence
from statistics import median

from reachpatch.models.base import SerializableRecord, stable_id, content_hash
from .dynamic_reach_avoid_graph import GraphNodeKind as N, GraphEdgeKind as E


@dataclass(frozen=True)
class FrontierAction(SerializableRecord):
    action_id: str
    kind: str
    checkpoint_id: str
    requirement_id: str | None
    obligation_ids: tuple[str, ...]
    hypothesis_id: str | None
    evidence_ids: tuple[str, ...]
    estimated_cost: float | None
    priority: int
    priority_reason: str


def estimate_action_cost(graph: Any, checkpoint_id: str, kind: str) -> float | None:
    """Seconds from observed actions/runs; unknown is not a fabricated unit cost."""
    samples = [float(item["duration_seconds"]) for item in graph.update_log
               if item.get("event") == "ACTION_COMPLETED" and item.get("kind") == kind
               and isinstance(item.get("duration_seconds"), (int, float))]
    if samples:
        return median(samples)
    if kind == "VALIDATE_CHECKPOINT":
        node = graph.nodes[checkpoint_id]
        durations = [run.get("duration_seconds") for role in ("target", "preservation", "challenge")
                     for result in node.metadata.get(role + "_results", {}).values()
                     for run in result.get("run_observations", ())]
        known = [value for value in durations if isinstance(value, (int, float)) and value >= 0]
        if known:
            return sum(known)
    return None  # NO_OBSERVED_COST


def contract_obligation_id(goal: Any, check: Any) -> str:
    # A command is part of the partition identity until an independently
    # grounded equivalence mapping exists. Different inputs never cancel gaps.
    return stable_id("contract-obligation", goal.goal_id, goal.comparator,
                     goal.expected, check.command, check.cwd, check.input_recipe)


def compile_contract_obligations(graph: Any, goals: Sequence[Any], checks: Sequence[Any]) -> tuple[str, ...]:
    ids: list[str] = []
    for goal in goals:
        if not goal.hard:
            continue
        bound = [check for check in checks if not goal.unresolved_reason and check.goal_id == goal.goal_id and check.trusted
                 and str(check.role) == "TARGET"]
        if not bound:
            node_id = stable_id("missing-contract", goal.goal_id)
            graph.add_node(N.OBLIGATION, node_id=node_id, status="UNRESOLVED_FACET" if goal.unresolved_reason else "MISSING_ORACLE", authority=goal.authority,
                           metadata={"obligation_kind": "CONTRACT_FACET", "goal_id": goal.goal_id,
                                     "facet": goal.facet_kind, "required": True, "check_ids": (),
                                     "unresolved_reason": goal.unresolved_reason,
                                     "parent_goal_id": goal.parent_goal_id,
                                     "evidence_span_ids": goal.evidence_span_ids})
            if goal.goal_id in graph.nodes:
                graph.add_edge(E.DERIVED_FROM, node_id, goal.goal_id, evidence_ids=goal.evidence_span_ids)
            ids.append(node_id)
        for check in bound:
            node_id = contract_obligation_id(goal, check)
            old = graph.nodes.get(node_id)
            graph.add_node(N.OBLIGATION, node_id=node_id, authority=goal.authority,
                status=old.status if old else "REQUIRES_VALIDATION", metadata={
                    "obligation_kind": "CONTRACT_FACET", "goal_id": goal.goal_id,
                    "facet": goal.comparator, "expected": goal.expected, "required": True,
                    "input_partition": content_hash((check.command, check.input_recipe)),
                    "check_ids": tuple(sorted(set((*(old.metadata.get("check_ids", ()) if old else ()), check.check_id)))),
                    "evidence_span_ids": goal.evidence_span_ids, "oracle_id": stable_id("oracle", check.check_id)})
            if goal.goal_id in graph.nodes:
                graph.add_edge(E.DERIVED_FROM, node_id, goal.goal_id, evidence_ids=goal.evidence_span_ids)
            if stable_id("oracle", check.check_id) in graph.nodes:
                graph.add_edge(E.REQUIRES_VALIDATION, node_id, stable_id("oracle", check.check_id))
            ids.append(node_id)
    return tuple(ids)


def evaluate_validation_closure(graph: Any, checkpoint_id: str, obligation_ids: Sequence[str],
                                results: Sequence[Any], audit_reports: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_id = {result.check_id: result for result in results}
    gaps: list[dict[str, Any]] = []
    for node_id in obligation_ids:
        node = graph.nodes[node_id]
        executions = [by_id[key] for key in node.metadata.get("check_ids", ()) if key in by_id]
        passing = any(item.stable and str(item.status) == "PASS" and item.authority in {"A", "B", "C"}
                      and item.entered_target_code is True for item in executions)
        audit = next((item for item in audit_reports if item.get("obligation_id") == node_id
                      and item.get("blocks_certification")), None)
        if not passing:
            reason = node.metadata.get("unresolved_reason") or ("MISSING_ORACLE" if not executions else "UNVERIFIED_CONTRACT")
        elif audit and audit.get("blocks_certification"):
            reason = audit["status"]
        else:
            reason = "COVERED_BY_EXECUTION"
        if reason != "COVERED_BY_EXECUTION":
            gaps.append({"obligation_id": node_id, "reason": reason})
        graph.add_edge(E.REQUIRES_VALIDATION, checkpoint_id, node_id)
    # Exact-input executable challenges must reach a stable terminal verdict.
    for edge in graph.edges.values():
        if edge.source_id != checkpoint_id or edge.kind is not E.REQUIRES_VALIDATION or not edge.active:
            continue
        node = graph.nodes[edge.target_id]
        if node.metadata.get("role") == "CHALLENGE" and node.metadata.get("executable_handle"):
            result = by_id.get(node.metadata.get("check_id"))
            if result is None or not result.stable or str(result.status) != "PASS":
                gaps.append({"obligation_id": node.node_id, "reason": "OPEN_EXECUTABLE_CHALLENGE"})
    report = {"checkpoint_id": checkpoint_id, "closed": not gaps, "gaps": gaps,
            "obligation_ids": tuple(obligation_ids), "execution_ids": tuple(sorted(by_id))}
    graph.record_update("VALIDATION_CLOSURE", **report)
    node = graph.nodes[checkpoint_id]
    graph.nodes[checkpoint_id] = replace(node, metadata={**node.metadata, "validation_closure": report})
    return report


def derive_frontier_actions(graph: Any, *, max_depth: int, generation_allowed: bool = True,
                            max_exploratory_probes: int = 2) -> tuple[FrontierAction, ...]:
    actions: list[FrontierAction] = []
    for node in graph.nodes.values():
        if node.kind is not N.CHECKPOINT or not node.metadata.get("checkpoint_id"):
            continue
        state = node.metadata
        if node.status in {"REJECTED", "CERTIFIED", "EVALUATING"}:
            continue
        results = {**state.get("target_results", {}), **state.get("preservation_results", {}), **state.get("challenge_results", {})}
        semantic_results = {key: {field: value.get(field) for field in ("status", "stable", "semantic_signature", "distance")}
                            for key, value in results.items()}
        version = content_hash((state.get("patch_hash"), semantic_results,
                               tuple((item.node_id, item.metadata.get("check_ids")) for item in graph.nodes.values()
                                     if item.metadata.get("obligation_kind") == "CONTRACT_FACET")))
        exhausted = set(state.get("exhausted_action_ids", ()))
        closure = state.get("validation_closure", {})
        if state.get("observation_ids") and state.get("exploratory_probe_attempts", 0) < max_exploratory_probes:
            for cell in graph.nodes.values():
                if (cell.kind is not N.CHALLENGE or cell.status != "EXPLORATION_ONLY"
                    or not cell.metadata.get("command") or cell.metadata.get("observation_ids")
                    or node.node_id not in cell.metadata.get("affected_checkpoint_ids", ())):
                    continue
                probe_action = stable_id("frontier-probe", node.node_id, cell.node_id)
                if probe_action not in exhausted:
                    actions.append(FrontierAction(probe_action, "RUN_PROBE", node.node_id, None,
                        (cell.node_id,), None, (cell.metadata.get("source_branch_id"),),
                        estimate_action_cost(graph, node.node_id, "RUN_PROBE"), 2,
                        "Observe an unmeasured boundary; exploratory evidence cannot certify."))
        pending_checks = tuple(edge.target_id for edge in graph.edges.values()
            if edge.active and edge.source_id == node.node_id and edge.kind is E.REQUIRES_VALIDATION
            and graph.nodes[edge.target_id].metadata.get("executable_handle")
            and graph.nodes[edge.target_id].metadata.get("check_id") not in results)
        if not state.get("observation_ids") or pending_checks:
            kind, priority, reason = "VALIDATE_CHECKPOINT", 1, "Checkpoint lacks executable evidence."
        elif state.get("mechanical_blockers"):
            kind, priority, reason = "FIX_MECHANICAL", 0, "Mechanical blocker precedes semantic search."
        elif any(key in state.get("locked_successes", ()) and value.get("stable") and value.get("status") == "FAIL"
                 for key, value in results.items()):
            kind, priority, reason = "GENERATE_PATCH", 1, "Repair a locked-success regression."
        elif any(value.get("stable") and value.get("status") == "FAIL" for value in results.values()):
            kind, priority, reason = "GENERATE_PATCH", 4, "Stable failure has unexplored repair mechanisms."
        elif not state.get("target_results") or closure.get("gaps"):
            kind, priority, reason = "RECOVER_EVIDENCE", 3, "Required executable evidence is missing."
        else:
            kind, priority, reason = "FINAL_REVIEW", 2, "Complete verification before selecting a final patch."
        if kind in {"GENERATE_PATCH", "FIX_MECHANICAL"}:
            if not generation_allowed or state.get("depth", 0) >= max_depth:
                continue
            hypotheses = [item for item in graph.nodes.values() if item.kind is N.REPAIR_HYPOTHESIS
                          and item.metadata.get("parent_checkpoint_id") == node.node_id
                          and item.node_id not in state.get("expanded_hypothesis_ids", ())]
            if node.status == "EXPANDED":
                if state.get("expansion_rounds", 0) >= graph.budget.max_expansion_depth:
                    continue
                kind, priority, reason = "EXPAND_GRAPH", 5, "Current mechanisms exhausted; admit the next bounded source frontier."
                version = content_hash((version, state.get("expansion_rounds", 0)))
            elif hypotheses:
                for hypothesis in hypotheses:
                    action_id = stable_id("frontier-action", node.node_id, hypothesis.node_id)
                    if action_id not in exhausted:
                        actions.append(FrontierAction(action_id, kind, node.node_id,
                            hypothesis.metadata.get("requirement_id"), tuple(hypothesis.metadata.get("causal_cut_ids", ())),
                            hypothesis.node_id, tuple(hypothesis.metadata.get("graph_evidence_ids", ())),
                            estimate_action_cost(graph, node.node_id, kind), priority, reason))
                continue
        if kind == "RECOVER_EVIDENCE" and closure.get("gaps"):
            for gap in closure["gaps"]:
                obligation = graph.nodes[gap["obligation_id"]]
                action_id = stable_id("frontier-action", node.node_id, kind, version, obligation.node_id, gap["reason"])
                if action_id not in exhausted:
                    actions.append(FrontierAction(action_id, kind, node.node_id,
                        obligation.metadata.get("goal_id"), (obligation.node_id,), None,
                        tuple(obligation.metadata.get("evidence_span_ids", ())),
                        estimate_action_cost(graph, node.node_id, kind), priority, gap["reason"]))
            continue
        action_id = stable_id("frontier-action", node.node_id, kind, version)
        if action_id in exhausted:
            continue
        missing = tuple(dict.fromkeys((*pending_checks, *(item["obligation_id"] for item in closure.get("gaps", ())))))
        actions.append(FrontierAction(action_id, kind, node.node_id, None, missing, None,
                                      tuple(state.get("observation_ids", ())), estimate_action_cost(graph, node.node_id, kind), priority, reason))
    return tuple(actions)


def select_next_action(graph: Any, actions: Sequence[FrontierAction]) -> FrontierAction | None:
    if not actions:
        return None  # GLOBAL_FRONTIER_EXHAUSTED
    def key(action: FrontierAction) -> tuple[Any, ...]:
        state = graph.nodes[action.checkpoint_id].metadata
        score = tuple(state.get("search_score", ()))
        # Descendant credit is expansion potential only. It never changes
        # this checkpoint's own score or certification evidence.
        potential = max(score, tuple(state.get("best_descendant_score", ()))) if action.kind in {
            "GENERATE_PATCH", "FIX_MECHANICAL", "EXPAND_GRAPH"} else score
        return (action.priority, tuple(-float(value) for value in potential),
                -len(action.obligation_ids), state.get("visit_count", 0),
                action.estimated_cost if action.estimated_cost is not None else float("inf"), action.action_id)
    selected = min(actions, key=key)
    graph.record_update("SELECT_FRONTIER_ACTION", **selected.to_dict())
    return selected


def exhaust_action(graph: Any, action: FrontierAction, reason: str) -> None:
    node = graph.nodes[action.checkpoint_id]
    graph.nodes[node.node_id] = replace(node, metadata={**node.metadata,
        "exhausted_action_ids": tuple(dict.fromkeys((*node.metadata.get("exhausted_action_ids", ()), action.action_id)))})
    graph.record_update("ACTION_EXHAUSTED", action_id=action.action_id, checkpoint_id=action.checkpoint_id, reason=reason)


def select_comparable_checkpoints(graph: Any, checkpoint_id: str, *, limit: int = 3) -> tuple[str, ...]:
    current = graph.nodes[checkpoint_id].metadata
    checks = set(current.get("target_results", {}))
    candidates = [node for node in graph.nodes.values() if node.kind is N.CHECKPOINT
                  and node.status != "REJECTED" and not node.metadata.get("mechanical_blockers")
                  and checks.intersection(node.metadata.get("target_results", {}))]
    def discriminating_priority(node):
        shared = checks.intersection(node.metadata.get("target_results", {}))
        disagreements = sum(
            content_hash(tuple(current["target_results"][key].get(field) for field in ("status", "semantic_signature", "distance"))) !=
            content_hash(tuple(node.metadata["target_results"][key].get(field) for field in ("status", "semantic_signature", "distance")))
            for key in shared)
        cut_diversity = len(set(node.metadata.get("causal_cut_ids", ())) - set(current.get("causal_cut_ids", ())))
        return (node.node_id != checkpoint_id, -disagreements, -cut_diversity, -len(shared), node.node_id)
    candidates.sort(key=discriminating_priority)
    hashes: set[str] = set()
    selected: list[str] = []
    for node in candidates:
        patch_hash = node.metadata.get("patch_hash", "")
        if patch_hash not in hashes:
            hashes.add(patch_hash)
            selected.append(node.node_id)
        if len(selected) >= limit:
            break
    return tuple(selected)


def record_patch_transposition(graph: Any, parent_id: str, hypothesis_id: str,
                               patch_hash: str, full_diff: str) -> str:
    matches = [node for node in graph.nodes.values() if node.kind is N.CHECKPOINT
               and node.metadata.get("patch_hash") == patch_hash
               and node.metadata.get("base_to_current_diff") == full_diff]
    if not matches:
        raise ValueError("DUPLICATE_HASH_WITHOUT_MATCHING_CHECKPOINT")
    existing = min(matches, key=lambda node: node.node_id)
    graph.add_edge(E.REACHES_CHECKPOINT, hypothesis_id, existing.node_id)
    graph.record_update("PATCH_TRANSPOSITION", parent_checkpoint_id=parent_id,
                        hypothesis_id=hypothesis_id, checkpoint_id=existing.node_id,
                        reason="EXACT_PATCH_STATE; provenance retained; execution reuse requires environment equivalence")
    parent = graph.nodes[parent_id]
    graph.nodes[parent_id] = replace(parent, metadata={**parent.metadata,
        "expanded_hypothesis_ids": tuple(dict.fromkeys((*parent.metadata.get("expanded_hypothesis_ids", ()), hypothesis_id)))})
    return existing.node_id


def challenge_lifecycle_metrics(graph: Any) -> dict[str, int]:
    cells = [node for node in graph.nodes.values() if node.kind is N.CHALLENGE]
    return {
        "challenge_generated": len(cells),
        "challenge_executable": sum(bool(node.metadata.get("command")) for node in cells),
        "challenge_trusted_oracle": sum(node.authority in {"A", "B", "C"} and bool(node.metadata.get("oracle")) for node in cells),
        "challenge_executed": sum(bool(node.metadata.get("observation_ids")) for node in cells),
        "challenge_discriminating": sum(bool(node.metadata.get("discriminates_candidates")) for node in cells),
        "challenge_missing_oracle": sum(node.authority not in {"A", "B", "C"} or not node.metadata.get("oracle") for node in cells),
        "challenge_missing_adapter": sum(not node.metadata.get("command") for node in cells),
    }
