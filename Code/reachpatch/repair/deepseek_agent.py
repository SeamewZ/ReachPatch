from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from reachpatch.models.base import canonical_json, content_hash
from reachpatch.reach_avoid.evidence_context import compact_evidence_context
from reachpatch.repair.execution_objective import InitialPatchObjective, RepairMode, RepairObjective

from .execution_tools import RepairToolExecutor, TOOL_SCHEMAS


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    initial_generator_max_turns: int = 10
    revision_generator_max_turns: int = 6
    initial_generator_wall_time_s: float = 1200.0
    revision_generator_wall_time_s: float = 1200.0
    initial_generator_token_budget: int = 4096
    revision_generator_token_budget: int = 4096
    # Keep the phase wall-clock budgets large enough for recovery while
    # bounding one stalled provider request so a sibling checkpoint can run.
    provider_request_timeout_seconds: float = 180.0

    @classmethod
    def from_environment(cls) -> "DeepSeekConfig":
        defaults = cls()
        return cls(**{
            field: type(getattr(defaults, field))(
                os.environ.get(f"REACHPATCH_{field.upper()}", getattr(defaults, field))
            )
            for field in defaults.__dataclass_fields__
        })


class DeepSeekHTTPTransport:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: tuple[dict[str, Any], ...],
        max_tokens: int,
        timeout_seconds: float,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> dict[str, Any]:
        payload = json.dumps({
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "max_tokens": max_tokens,
            "temperature": 0,
            # Flash defaults to thinking, which rejects forced tool_choice.
            # This agent uses forced structured submissions for contracts.
            **({"thinking": {"type": "disabled"}} if self.model == "deepseek-flash" else {}),
        }).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:4000]
            reason = f"{exc.reason}: {body}" if body else str(exc.reason)
            raise urllib.error.HTTPError(
                exc.url, exc.code, reason, exc.headers, None,
            ) from exc
        choice = dict(raw["choices"][0])
        message = dict(choice.get("message") or {})
        # Preserve the provider request identity for transition certificates.
        # The message itself is still the only model content consumed by the
        # tool loop; this identifier is audit metadata, never progress input.
        message["_request_id"] = raw.get("id")
        # Preserve provider termination metadata.  In particular, a length
        # truncated response must not be interpreted as an empty repair and
        # must leave any already-applied working edit intact.
        message["_finish_reason"] = choice.get("finish_reason")
        message["_usage"] = dict(raw.get("usage") or {})
        return message


class DeepSeekAgent:
    def __init__(
        self,
        transport: DeepSeekHTTPTransport,
        config: DeepSeekConfig | None = None,
    ) -> None:
        self.transport = transport
        self.config = config or DeepSeekConfig.from_environment()

    @staticmethod
    def _compact(value: Any, *, string_limit: int = 1800, depth: int = 0) -> Any:
        if depth > 4:
            return "<depth-limited>"
        if isinstance(value, str):
            return value if len(value) <= string_limit else value[:string_limit] + "...[truncated]"
        if isinstance(value, dict):
            return {
                str(key): DeepSeekAgent._compact(item, string_limit=string_limit, depth=depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                DeepSeekAgent._compact(item, string_limit=string_limit, depth=depth + 1)
                for item in value[:24]
            ]
        return value

    @staticmethod
    def _prompt_validation_status(tools: RepairToolExecutor) -> dict[str, Any]:
        """Return validation state without duplicating inline probe programs.

        The exact active failing command already appears first in the repair
        objective.  ``pending_commands`` can contain the same multi-kilobyte
        ``python -c`` probe plus every preservation selector; serializing it
        again made context compaction larger than the original request.  The
        model only needs queue identity/status here.  The executor retains the
        complete commands and runs them after an edit.
        """
        status = tools.validation_status()
        command_refs = tuple({
            "argv0": str(command[0]) if command else "",
            "selector": next((
                str(part) for part in reversed(command)
                if len(str(part)) <= 240 and "\n" not in str(part)
            ), "<inline-program>"),
            "command_hash": content_hash(command),
            "argument_count": len(command),
        } for command in status.get("pending_commands", ())[:24])
        return {
            key: value for key, value in status.items()
            if key not in {"pending_commands", "outcomes"}
        } | {
            "pending_command_refs": command_refs,
            "outcomes": tuple({
                "check_id": item.get("check_id"),
                "status": item.get("status"),
                "stable": item.get("stable"),
                "failure_stage": item.get("failure_stage"),
            } for item in status.get("outcomes", ())[:24]),
        }

    @classmethod
    def _bound_messages(
        cls,
        messages: list[dict[str, Any]],
        objective: RepairObjective | InitialPatchObjective,
        tools: RepairToolExecutor,
        source_contexts: dict[str, dict[str, Any]],
        *,
        max_chars: int = 48000,
    ) -> list[dict[str, Any]]:
        """Rebuild a short request from current state, never truncate a contract.

        All raw tool replies remain in generator artifacts. Old exploratory
        conversation is replaceable; the issue, full working diff, exact failure
        and relevant current source are not.
        """
        if len(messages) < 8 and len(canonical_json(messages)) <= max_chars:
            return messages
        current_sources = {}
        source_items = list(source_contexts.items())
        # Early reads are normally the graph-ranked target and its imports;
        # late reads are exploratory. Keep both instead of allowing recency to
        # evict the target exactly when editing becomes mandatory.
        selected_source_items = source_items[:1]
        if len(source_items) > 1:
            selected_source_items.extend(source_items[-1:])
        selected_source_items = list(dict(selected_source_items).items())
        for key, span in selected_source_items:
            path = str(span["path"])
            try:
                selected = tools.read_file(path, span.get("start_line", 1),
                                           span.get("end_line", 240))
                content = str(selected.get("content", ""))
                if len(content) > 2500:
                    selected = {**selected, "content": content[:2500],
                                "omitted_chars": len(content) - 2500}
                current_sources[key] = selected
            except (OSError, ValueError) as error:
                current_sources[key] = {"source_unavailable": str(error)}
        if isinstance(objective, InitialPatchObjective) and current_sources:
            from dataclasses import replace
            objective = replace(objective, graph_context={"source": "current source below"})
        compact = [
            messages[0],
            {"role": "user", "content": cls._prompt(
                objective, ()) +
                "\\nCURRENT SOURCE READS:\\n" + canonical_json(current_sources) +
                "\\nVALIDATION:\\n" + canonical_json(cls._prompt_validation_status(tools)) +
                "\\nLATEST TOOL EVIDENCE:\\n" + canonical_json(cls._compact([
                    item for item in messages[-4:] if item.get("role") == "tool"
                ][-1:], string_limit=1000))},
        ]
        if len(canonical_json(compact)) > max_chars:
            from reachpatch.execution.case_budget import CaseBudgetExhausted
            raise CaseBudgetExhausted("MANDATORY_CONTEXT_EXCEEDS_LIMIT")
        tools.state.dynamic_failure_graph.record_update("CONTEXT_HISTORY_ELIDED",
            original_chars=len(canonical_json(messages)), sent_chars=len(canonical_json(compact)),
            objective_id=objective.objective_id)
        return compact

    @classmethod
    def _repair_context(
        cls,
        objective: RepairObjective | InitialPatchObjective,
        attempt_history: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        if isinstance(objective, InitialPatchObjective):
            return {
                "objective_id": objective.objective_id,
                "repair_mode": objective.mode,
                "goal_contracts": tuple(item.to_dict() for item in objective.goal_contracts),
                "public_issue_context": cls._compact(objective.public_context, string_limit=10000),
                "current_full_diff": objective.current_full_diff,
                "current_patch_hash": objective.current_patch_hash,
                "dynamic_graph_context": cls._compact(objective.graph_context, string_limit=12000),
            }
        failure = objective.active_failure
        graph = objective.dynamic_failure_graph
        dynamic_context = None
        if graph is not None:
            nodes = graph.nodes
            selected_ids = set(getattr(objective, "causal_cut_ids", ()))
            for cut_id in tuple(selected_ids):
                cut = nodes.get(cut_id)
                if cut is None:
                    continue
                metadata = getattr(cut, "metadata", {})
                for field in ("symbol_ids", "branch_ids", "value_flow_ids", "hunk_ids"):
                    selected_ids.update(map(str, metadata.get(field, ())))
            for edge in graph.edges.values():
                if edge.edge_id in selected_ids:
                    selected_ids.update((edge.source_id, edge.target_id))
            selected_nodes = (
                [node for node in nodes.values() if node.node_id in selected_ids]
                if selected_ids else [node for node in nodes.values()
                    if str(node.kind) in {"SYMBOL", "BRANCH", "VALUE"}
                    and node.file in {span.path for span in objective.relevant_source_slices}]
            )
            selected_node_ids = {node.node_id for node in selected_nodes}
            dynamic_context = {
                # Only local execution context is exposed. Internal graph IDs,
                # patch hashes and edge counts are deliberately omitted so the
                # model cannot treat the locator as certification evidence.
                "nodes": tuple({
                    "kind": str(node.kind), "path": getattr(node, "path", getattr(node, "file", None)),
                    "symbol": getattr(node, "symbol", None), "start_line": getattr(node, "start_line", getattr(node, "line_start", 0)),
                    "end_line": getattr(node, "end_line", getattr(node, "line_end", 0)),
                    "authority": getattr(node, "authority", ""), "status": getattr(node, "status", ""),
                    "source_span": str(getattr(node, "source_span", ""))[:2500],
                    "metadata": {key: value for key, value in node.metadata.items()
                                 if key in {"predicate", "defines", "uses", "branch_outcome", "safe_local_summary"}},
                } for node in sorted(
                    selected_nodes, key=lambda item: (getattr(item, "distance", 0), getattr(item, "path", getattr(item, "file", "")) or "", getattr(item, "start_line", getattr(item, "line_start", 0))),
                )[:8]),
                "edges": tuple({
                    "kind": str(edge.kind),
                    "source": nodes.get(edge.source_id).symbol if nodes.get(edge.source_id) else None,
                    "target": nodes.get(edge.target_id).symbol if nodes.get(edge.target_id) else None,
                    "distance": getattr(edge, "distance", 0),
                    "static_or_dynamic": edge.static_or_dynamic,
                    "trace_ids": edge.trace_ids,
                    "evidence_ids": edge.evidence_ids,
                } for edge in graph.edges.values()
                if edge.active and edge.source_id in selected_node_ids and edge.target_id in selected_node_ids)[:24],
                "frontier": tuple({
                    "reason": getattr(item, "reason", ""), "path": getattr(item, "path", None),
                    "symbol": getattr(item, "symbol", None), "depth": getattr(item, "depth", 0),
                    "boundary_node_ids": getattr(item, "boundary_node_ids", ()),
                    "omitted_relation_kinds": getattr(item, "omitted_relation_kinds", ()),
                } for item in getattr(graph, "frontier", getattr(graph, "frontiers", ()))[:8]),
                "expanded_depth": getattr(graph, "expanded_depth", getattr(graph, "revision", 0)),
            }
        return {
            "exact_failure_command": objective.exact_failure_command,
            "comparator": objective.comparator,
            "expected_observation": objective.expected_observation,
            "actual_observation": objective.actual_observation,
            "traceback_frames": objective.traceback_frames,
            "current_full_diff": objective.current_full_diff,
            "parent_patch_hash": objective.parent_patch_hash,
            "causal_cut_ids": getattr(objective, "causal_cut_ids", ()),
            "repair_hypothesis": (
                objective.repair_hypothesis.to_dict()
                if getattr(objective, "repair_hypothesis", None) is not None
                and hasattr(objective.repair_hypothesis, "to_dict")
                else getattr(objective, "repair_hypothesis", None)
            ),
            "graph_source_spans": cls._compact(
                tuple(getattr(objective, "graph_source_spans", ()))[:4],
                string_limit=2500,
            ),
            "hypothesis_feedback": getattr(objective, "hypothesis_feedback", ()),
            "exploratory_observations": getattr(objective, "exploratory_observations", ()),
            "dynamic_failure_context": cls._compact(dynamic_context, string_limit=2500),
            "objective_id": objective.objective_id,
            "repair_mode": objective.mode,
            "active_failure": {"failure_id": failure.failure_id, "kind": str(failure.kind)},
            "stdout": cls._compact(objective.stdout, string_limit=4000),
            "stderr": cls._compact(objective.stderr, string_limit=4000),
            "current_patch_hash": objective.current_patch_hash,
            "relevant_source_slices": cls._compact(
                tuple(item.to_dict() for item in objective.relevant_source_slices[:2]),
                string_limit=2500,
            ),
            "locked_checks": tuple({
                "check_id": item.check_id,
                "role": str(item.role),
                "authority": item.authority,
                "selector": next((str(part) for part in reversed(item.command)
                                  if len(str(part)) <= 240 and "\n" not in str(part)),
                                 "<inline-program>"),
            } for item in objective.locked_checks[:12]),
            "preservation_checks": tuple({
                "check_id": item.check_id,
                "authority": item.authority,
                "selector": next((str(part) for part in reversed(item.command)
                                  if len(str(part)) <= 240 and "\n" not in str(part)),
                                 "<inline-program>"),
            } for item in objective.preservation_checks[:12]),
            "mechanical_blockers": tuple(item.to_dict() for item in objective.mechanical_blockers),
            "forbidden_repeated_mechanisms": objective.forbidden_repeated_mechanisms,
            "hypothesis_id": getattr(objective, "hypothesis_id", None),
            "attempt_history": tuple({key: value for key, value in item.items()
                if key in {"result_kind", "error_kind", "active_failure_id", "changed_files"}}
                for item in attempt_history[-3:]),
        }

    @classmethod
    def _prompt(cls, objective: RepairObjective | InitialPatchObjective,
                attempt_history: tuple[dict[str, Any], ...] = ()) -> str:
        retry = os.environ.get("REACHPATCH_RA51_ATTEMPT", "1")
        retry_guidance = (f"Independent generation retry {retry}: do not repeat a rejected algorithm or exact diff. "
                          if retry != "1" else "")
        return (
            "Make one minimal behavior-changing edit for this issue or the specified evidence-backed hypothesis. "
            "The working tree already contains the full base-to-parent diff. Never reset it or inherit sibling edits. "
            "Use actual source and public contracts; a missing oracle does not justify inventing expected behavior. "
            "Read omitted source lines before editing. Return one tool call per turn. "
            "apply_patch accepts complete git unified diff or *** Begin Patch actions. "
            "Do not modify tests, swallow exceptions, weaken inputs, or remove locked successful behavior. "
            "After one edit the controller validates and decides whether another repair is warranted; "
            "do not spend calls submitting or self-scoring. Reuse evidence rather than repeat diagnostics.\\n"
            + retry_guidance + canonical_json(cls._repair_context(objective, attempt_history))
        )
    def revise(self, objective: RepairObjective | InitialPatchObjective,
               tools: RepairToolExecutor, *, initial: bool = False) -> dict[str, Any]:
        """One evidence-bound edit. Validation and submission need no LLM turn."""
        messages = [
            {"role": "system", "content": "Repair the supplied issue using repository evidence. Do not modify tests or invent expected behavior."},
            {"role": "user", "content": self._prompt(objective)},
        ]
        turns = self.config.initial_generator_max_turns if initial else self.config.revision_generator_max_turns
        tokens = self.config.initial_generator_token_budget if initial else self.config.revision_generator_token_budget
        wall = self.config.initial_generator_wall_time_s if initial else self.config.revision_generator_wall_time_s
        deadline = time.monotonic() + wall
        source_contexts: dict[str, dict[str, Any]] = {}
        attempted: dict[str, Any] = {}
        graph = tools.state.dynamic_failure_graph
        mechanism = getattr(getattr(objective, "repair_hypothesis", None), "proposed_mechanism", "initial_issue_repair")
        diagnostic_tools = tuple(schema for schema in TOOL_SCHEMAS
                                 if schema["function"]["name"] not in {"finish_revision", "run_allowed_public_check"})
        patch_tools = tuple(schema for schema in diagnostic_tools
                            if schema["function"]["name"] == "apply_patch")
        # A larger case budget permits retries and later hypotheses; it must
        # not turn one evidence question into dozens of source-reading calls.
        # Initial localization gets at most twelve diagnostic turns and a
        # hypothesis-bound revision at most six before an edit is required.
        diagnostic_turn_limit = max(2, min(turns - 1, 12 if initial else 6))
        patch_required_announced = False
        patch_apply_failures = 0
        for turn in range(turns):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"error_kind": "ACTION_WALL_BUDGET", "summary": "No edit within action budget", "mechanism": mechanism}
            messages = self._bound_messages(messages, objective, tools, source_contexts)
            available = diagnostic_tools
            if turn >= diagnostic_turn_limit:
                available = patch_tools
                if not patch_required_announced:
                    messages.append({"role": "user", "content": (
                        "The bounded evidence-collection phase is complete. Use the source evidence already "
                        "present to apply one minimal production-code patch now. Do not request more source, "
                        "do not edit tests, and preserve behavior outside the stated contract."
                    )})
                    patch_required_announced = True
            packet = compact_evidence_context(graph, messages, scope=objective.objective_id)
            started = time.monotonic()
            message = self.transport.complete(list(packet.messages), tools=available, max_tokens=tokens,
                timeout_seconds=min(self.config.provider_request_timeout_seconds, remaining),
                tool_choice="required")
            graph.record_update("MODEL_CALL", objective_id=objective.objective_id,
                checkpoint_id=tools.state.working_checkpoint.checkpoint_id,
                hypothesis_id=getattr(objective, "hypothesis_id", None),
                graph_hash=graph.digest(), graph_evidence_ids=packet.evidence_ids,
                source_paths=tuple(source_contexts), request_id=message.get("_request_id"),
                finish_reason=message.get("_finish_reason"), usage=message.get("_usage", {}),
                wall_seconds=time.monotonic() - started, turn=turn)
            calls = message.get("tool_calls") or ()
            if not calls:
                return {"error_kind": "MISSING_TOOL_CALL", "summary": str(message.get("content", ""))[:1000],
                        "mechanism": mechanism}
            messages.append({key: value for key, value in message.items()
                             if key in {"role", "content", "tool_calls", "reasoning_content"}})
            edited = False
            for index, call in enumerate(calls):
                function = call.get("function", {})
                name = str(function.get("name", ""))
                if index:
                    result = {"status": "DEFERRED", "reason": "Only one state-dependent action executes per turn."}
                else:
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                        signature = canonical_json((name, arguments, tools.inspect_diff().get("patch_hash")))
                        if signature in attempted:
                            graph.record_update("DUPLICATE_TOOL_PREVENTED", objective_id=objective.objective_id, tool=name)
                            result = {"status": "REUSED_EVIDENCE", "previous_result": attempted[signature],
                                      "instruction": "This action adds no evidence. Use this result to edit, or investigate a different unresolved question."}
                        else:
                            if name not in {schema["function"]["name"] for schema in available}:
                                raise ValueError("Tool not available for this evidence action")
                            result = tools.invoke(name, arguments)
                            attempted[signature] = result
                            if name == "read_file":
                                source_contexts[f"{result['path']}:{result['start_line']}:{result['end_line']}"] = result
                            edited = name == "apply_patch"
                    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
                        from reachpatch.execution.case_budget import CaseBudgetExhausted
                        if isinstance(error, CaseBudgetExhausted):
                            raise
                        result = {"error": type(error).__name__, "detail": str(error)}
                        if name == "apply_patch":
                            patch_apply_failures += 1
                            patch_text = str(locals().get("arguments", {}).get("patch", ""))
                            matching_source = []
                            error_text = str(error)
                            match = re.search(
                                r"(?:current source|target does not exist):\s*([^\s]+)",
                                error_text,
                            )
                            failed_path = match.group(1) if match else None
                            for span in source_contexts.values():
                                if failed_path and str(span.get("path")) != failed_path:
                                    continue
                                try:
                                    refreshed = tools.read_file(
                                        str(span["path"]), span.get("start_line", 1),
                                        span.get("end_line", 240),
                                    )
                                except (OSError, ValueError):
                                    continue
                                matching_source.append(cls._compact(
                                    refreshed, string_limit=4000,
                                ))
                                if len(matching_source) >= 2:
                                    break
                            if matching_source:
                                result["exact_current_source"] = matching_source
                            result["instruction"] = (
                                "Regenerate one smaller patch using exact_current_source; "
                                "do not reuse the failed hunk context."
                            )
                            graph.record_update(
                                "PATCH_APPLY_FAILED",
                                objective_id=objective.objective_id,
                                error=error_text,
                                patch_hash=content_hash(patch_text),
                                patch_preview=patch_text[:2000],
                                failure_count=patch_apply_failures,
                            )
                messages.append({"role": "tool", "tool_call_id": str(call.get("id", name)),
                                 "content": canonical_json(result)})
            if patch_apply_failures >= 6:
                return {
                    "error_kind": "PATCH_APPLICATION_RETRY_EXHAUSTED",
                    "summary": "Repeated patch context mismatch after exact-source refresh",
                    "mechanism": mechanism,
                }
            if edited:
                validation = tools.validation_status()
                while validation["pending_commands"]:
                    tools.run_allowed_public_check(validation["pending_commands"][0])
                    validation = tools.validation_status()
                # A stable failure is submitted as evidence, not hidden by
                # internal greedy edits. The controller decides what to do.
                tools.finish_revision("Single edit submitted for independent execution review", mechanism)
                return {"summary": tools.finish_summary, "mechanism": mechanism}
        return {"error_kind": "ACTION_CALL_BUDGET", "summary": "No edit within bounded evidence action",
                "mechanism": mechanism}
