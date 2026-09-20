"""Cost-aware decision views stored exclusively in the case evidence graph.

No model is used to summarize evidence or decide whether to call another model.
Original tool replies stay in graph nodes; context elision never changes authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Sequence

from reachpatch.models.base import canonical_json, content_hash, stable_id
from .dynamic_reach_avoid_graph import GraphNodeKind as N, GraphEdgeKind as E


@dataclass(frozen=True)
class ContextPacket:
    messages: tuple[dict[str, Any], ...]
    evidence_ids: tuple[str, ...]
    original_bytes: int
    sent_bytes: int


QUESTION_STATES = frozenset({"OPEN", "SUPPORTED", "REFUTED", "BLOCKED", "SUPERSEDED"})


def _policy(graph: Any, name: str, default: bool = True) -> bool:
    node = graph.nodes.get("evidence-policy")
    return bool(node.metadata.get(name, default)) if node is not None else default


@dataclass(frozen=True)
class EvidenceQuestion:
    question_id: str
    kind: str
    claim: str
    checkpoint_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    status: str
    blocking_reason: str | None
    evidence_version: str


def ensure_evidence_question(
    graph: Any,
    checkpoint_id: str,
    kind: str,
    claim: str,
    *,
    requirement_ids: Sequence[str] = (),
    dependency_ids: Sequence[str] = (),
    evidence_ids: Sequence[str] = (),
    blocking_reason: str | None = None,
    relevant_versions: Any = None,
) -> EvidenceQuestion:
    """Create or reopen one evidence-dependent question in the unified graph."""
    requirements = tuple(dict.fromkeys(str(item) for item in requirement_ids if item))
    dependencies = tuple(dict.fromkeys(str(item) for item in dependency_ids if item))
    evidence = tuple(dict.fromkeys(str(item) for item in evidence_ids if item))
    question_id = stable_id("evidence-question", checkpoint_id, kind, claim, requirements, dependencies)
    patch_hash = graph.nodes[checkpoint_id].metadata.get("patch_hash")
    version = content_hash((patch_hash, relevant_versions, tuple(
        (item, graph.nodes[item].last_updated_revision, graph.nodes[item].status)
        for item in (*dependencies, *evidence) if item in graph.nodes
    )))
    existing = graph.nodes.get(question_id)
    status = existing.status if existing is not None else "OPEN"
    if existing is not None:
        evidence = tuple(dict.fromkeys((*existing.metadata.get("evidence_ids", ()), *evidence)))
        if blocking_reason is None and status == "BLOCKED":
            blocking_reason = existing.metadata.get("blocking_reason")
    if existing is not None and existing.metadata.get("evidence_version") != version:
        status = "OPEN"
        graph.record_update("EVIDENCE_QUESTION_REOPENED", question_id=question_id,
                            old_version=existing.metadata.get("evidence_version"), new_version=version)
    metadata = {
        "obligation_kind": "EVIDENCE_QUESTION", "question_id": question_id,
        "kind": kind, "claim": claim, "checkpoint_ids": (checkpoint_id,),
        "requirement_ids": requirements, "dependency_ids": dependencies,
        "evidence_ids": evidence, "blocking_reason": blocking_reason,
        "evidence_version": version,
    }
    graph.add_node(N.OBLIGATION, node_id=question_id, status=status, metadata=metadata)
    graph.add_edge(E.REQUIRES_VALIDATION, checkpoint_id, question_id)
    for dependency_id in (*dependencies, *evidence):
        if dependency_id in graph.nodes:
            graph.add_edge(E.DERIVED_FROM, question_id, dependency_id)
    return EvidenceQuestion(question_id, kind, claim, (checkpoint_id,), requirements,
                            dependencies, evidence, status, blocking_reason, version)


def resolve_evidence_question(graph: Any, question_id: str, status: str, *,
                              evidence_ids: Sequence[str] = (),
                              blocking_reason: str | None = None) -> EvidenceQuestion:
    if status not in QUESTION_STATES - {"OPEN"}:
        raise ValueError(f"invalid terminal evidence-question status: {status}")
    node = graph.nodes[question_id]
    evidence = tuple(dict.fromkeys((*node.metadata.get("evidence_ids", ()),
                                    *(str(item) for item in evidence_ids if item))))
    metadata = {**node.metadata, "evidence_ids": evidence,
                "blocking_reason": blocking_reason}
    graph.nodes[question_id] = replace(node, status=status, metadata=metadata,
                                       last_updated_revision=graph.revision)
    for evidence_id in evidence:
        if evidence_id in graph.nodes:
            graph.add_edge(E.EVIDENCE_SUPPORTS, evidence_id, question_id)
    graph.record_update("EVIDENCE_QUESTION_RESOLVED", question_id=question_id,
                        status=status, blocking_reason=blocking_reason,
                        evidence_ids=evidence)
    return EvidenceQuestion(question_id, str(metadata["kind"]), str(metadata["claim"]),
        tuple(metadata["checkpoint_ids"]), tuple(metadata["requirement_ids"]),
        tuple(metadata["dependency_ids"]), evidence, status, blocking_reason,
        str(metadata["evidence_version"]))


def evidence_question_summary(graph: Any, checkpoint_id: str) -> dict[str, Any]:
    questions = [node for node in graph.nodes.values()
                 if node.kind is N.OBLIGATION
                 and node.metadata.get("obligation_kind") == "EVIDENCE_QUESTION"
                 and checkpoint_id in node.metadata.get("checkpoint_ids", ())]
    counts = {status: sum(node.status == status for node in questions)
              for status in sorted(QUESTION_STATES)}
    open_ids = tuple(sorted(node.node_id for node in questions if node.status == "OPEN"))
    blocked = tuple(sorted(node.node_id for node in questions if node.status == "BLOCKED"))
    return {"checkpoint_id": checkpoint_id, "counts": counts,
            "open_question_ids": open_ids, "blocked_question_ids": blocked,
            "should_stop": not open_ids,
            "stop_reason": "QUESTIONS_CLOSED" if not open_ids and not blocked else
                           "EVIDENCE_BLOCKED" if not open_ids else None}


def resolve_checkpoint_questions(graph: Any, checkpoint_id: str,
                                 kinds: Sequence[str], status: str, *,
                                 evidence_ids: Sequence[str] = (),
                                 blocking_reason: str | None = None) -> tuple[str, ...]:
    selected = tuple(node.node_id for node in graph.nodes.values()
        if node.kind is N.OBLIGATION and node.status == "OPEN"
        and node.metadata.get("obligation_kind") == "EVIDENCE_QUESTION"
        and checkpoint_id in node.metadata.get("checkpoint_ids", ())
        and node.metadata.get("kind") in set(kinds))
    for question_id in selected:
        resolve_evidence_question(graph, question_id, status,
                                  evidence_ids=evidence_ids,
                                  blocking_reason=blocking_reason)
    return selected


def claim_evidence_action(graph: Any, checkpoint_id: str, kind: str,
                          question: str, evidence: Any) -> bool:
    """An unchanged question/evidence/mechanism can be attempted only once.

    The patch and actual evidence (not graph revision/node count) version actions.
    Independent questions remain actionable even at the same checkpoint.
    """
    question_node = ensure_evidence_question(
        graph, checkpoint_id, kind, question,
        evidence_ids=tuple(evidence.get("evidence_ids", ())) if isinstance(evidence, dict) else (),
        relevant_versions=evidence,
    )
    version = content_hash((question_node.evidence_version, evidence))
    reuse = _policy(graph, "evidence_reuse_enabled")
    attempt = 0 if reuse else sum(
        node.kind is N.OBLIGATION
        and node.metadata.get("obligation_kind") == "EVIDENCE_ACTION"
        and node.metadata.get("question_id") == question_node.question_id
        for node in graph.nodes.values()
    )
    action_id = stable_id("evidence-action", question_node.question_id, kind, version, attempt)
    if action_id in graph.nodes:
        graph.record_update("DUPLICATE_ACTION_PREVENTED", action_id=action_id, kind=kind)
        return False
    graph.add_node(N.OBLIGATION, node_id=action_id, status="ATTEMPTED", metadata={
        "obligation_kind": "EVIDENCE_ACTION", "checkpoint_id": checkpoint_id,
        "question_id": question_node.question_id, "action_kind": kind,
        "question": question, "evidence_version": version,
        "evidence": evidence})
    graph.add_edge(E.DERIVED_FROM, action_id, question_node.question_id)
    graph.record_update("EVIDENCE_ACTION", action_id=action_id, kind=kind, checkpoint_id=checkpoint_id)
    return True


def compact_evidence_context(graph: Any, messages: Sequence[dict[str, Any]],
                             *, scope: str) -> ContextPacket:
    """Elide identical tool payloads only if an earlier exact payload remains.

    Keep tool-call envelopes, system instructions, failing commands and all
    unique source verbatim. Across calls, the first occurrence is sent again:
    a model is never presumed to remember a previous request.
    """
    seen: dict[str, str] = {}
    output: list[dict[str, Any]] = []
    ids: list[str] = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "tool":
            payload = item.get("content", "")
            fingerprint = content_hash(payload)
            node_id = stable_id("tool-evidence", scope, fingerprint)
            if node_id not in graph.nodes:
                graph.add_node(N.OBSERVATION, node_id=node_id, status="TOOL_RETURNED",
                    metadata={"scope": scope, "content_hash": fingerprint, "payload": payload,
                              "certifying": False})
            ids.append(node_id)
            if fingerprint in seen and _policy(graph, "evidence_reuse_enabled"):
                reference = canonical_json({"identical_to_tool_call": seen[fingerprint],
                                            "evidence_id": node_id,
                                            "instruction": "Use the exact earlier tool result in this request."})
                if len(reference) < len(str(payload)):
                    item["content"] = reference
            else:
                seen[fingerprint] = str(item.get("tool_call_id", ""))
        output.append(item)
    original = len(canonical_json(messages).encode("utf-8"))
    sent = len(canonical_json(output).encode("utf-8"))
    packet = ContextPacket(tuple(output), tuple(dict.fromkeys(ids)), original, sent)
    graph.record_update("CONTEXT_PACKET", scope=scope, evidence_ids=packet.evidence_ids,
                        original_bytes=packet.original_bytes, sent_bytes=packet.sent_bytes,
                        elided_bytes=max(0, original - sent))
    return packet


def read_evidence(graph: Any, path: str, source_hash: str, start: int, end: int,
                  result: dict[str, Any]) -> dict[str, Any]:
    """Version source reads and explicitly invalidate stale source evidence."""
    node_id = stable_id("source-evidence", path, source_hash, start, end)
    for node in tuple(graph.nodes.values()):
        if (node.metadata.get("evidence_kind") == "SOURCE_READ"
                and node.file == path and node.metadata.get("source_hash") != source_hash
                and node.status == "VALID"):
            graph.nodes[node.node_id] = replace(node, status="STALE")
            graph.record_update("SOURCE_EVIDENCE_INVALIDATED", evidence_id=node.node_id)
    existing = graph.nodes.get(node_id)
    if existing is not None and _policy(graph, "evidence_reuse_enabled"):
        graph.record_update("SOURCE_READ_REUSED", evidence_id=node_id)
        return dict(existing.metadata["result"])
    if existing is not None:
        node_id = stable_id("source-evidence", path, source_hash, start, end,
                            sum(item.get("event") == "SOURCE_READ_REPEATED"
                                for item in graph.update_log))
        graph.record_update("SOURCE_READ_REPEATED", evidence_id=node_id)
    graph.add_node(N.OBSERVATION, node_id=node_id, file=path, line_start=start, line_end=end,
        status="VALID", metadata={"evidence_kind": "SOURCE_READ", "source_hash": source_hash,
                                  "result": result})
    return result


def efficiency_report(graph: Any, budget: Any) -> dict[str, Any]:
    packets = [item for item in graph.update_log if item.get("event") == "CONTEXT_PACKET"]
    counts = {name: sum(item.get("event") == name for item in graph.update_log)
              for name in ("DUPLICATE_ACTION_PREVENTED", "SOURCE_READ_REUSED",
                           "SOURCE_EVIDENCE_INVALIDATED", "EVIDENCE_ACTION", "VALIDATION_CACHE_HIT")}
    questions = [node for node in graph.nodes.values()
        if node.kind is N.OBLIGATION
        and node.metadata.get("obligation_kind") == "EVIDENCE_QUESTION"]
    actions = [node for node in graph.nodes.values()
        if node.kind is N.OBLIGATION
        and node.metadata.get("obligation_kind") == "EVIDENCE_ACTION"]
    return {"schema": "reachpatch-token-efficiency-v2", "events": counts,
            "context_original_bytes": sum(item["original_bytes"] for item in packets),
            "context_sent_bytes": sum(item["sent_bytes"] for item in packets),
            "context_elided_bytes": sum(item["elided_bytes"] for item in packets),
            "question_status_counts": {
                status: sum(node.status == status for node in questions)
                for status in sorted(QUESTION_STATES)
            },
            "evidence_question_count": len(questions),
            "evidence_action_count": len(actions),
            "question_reopen_count": sum(item.get("event") == "EVIDENCE_QUESTION_REOPENED"
                                         for item in graph.update_log),
            "question_resolution_count": sum(item.get("event") == "EVIDENCE_QUESTION_RESOLVED"
                                             for item in graph.update_log),
            "budget": budget.summary() if budget is not None else {"status": "NO_MODEL_BUDGET"},
            "token_savings_claim": "REQUIRES_MATCHED_BASELINE"}


def initial_source_context(graph: Any, *, max_source_chars: int = 12000) -> dict[str, Any]:
    """Ranked real source excerpts, not serialized nodes or repeated metadata.

    Source may be excerpted at line boundaries with explicit omitted counts;
    normative issue text is supplied independently and is never elided here.
    """
    context = graph.initial_generation_context()
    spans = []
    used = 0
    seen = set()
    for node in context["related_source_spans"]:
        location = (node.get("file"), node.get("symbol"))
        if location in seen or used >= max_source_chars:
            continue
        seen.add(location)
        lines = str(node.get("source_span", "")).splitlines()
        selected = []
        for line in lines:
            if sum(len(s) + 1 for s in selected) + len(line) + 1 > min(4000, max_source_chars - used):
                break
            selected.append(line)
        source = "\n".join(selected)
        used += len(source)
        spans.append({"node_id": node["node_id"], "file": node.get("file"),
                      "symbol": node.get("symbol"), "line_start": node.get("line_start"),
                      "source": source, "omitted_lines": len(lines) - len(selected)})
    graph.record_update("INITIAL_CONTEXT_SELECTED", node_ids=[s["node_id"] for s in spans],
                        source_chars=used, omitted_source_spans=len(context["related_source_spans"]) - len(spans))
    return {"source_spans": spans, "data_flow": context["possible_data_flow_paths"],
            "target_symbols": context["top_target_symbols"],
            "instruction": "Use read_file for omitted lines before editing; excerpts are not full functions."}
