from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from reachpatch.models.base import canonical_json
from reachpatch.repair.execution_objective import InitialPatchObjective, RepairMode, RepairObjective

from .execution_tools import RepairToolExecutor, TOOL_SCHEMAS


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    initial_generator_max_turns: int = 24
    revision_generator_max_turns: int = 20
    revision_generator_turn_limit: int = 20
    root_recovery_max_turns: int = 28
    initial_generator_wall_time_s: float = 1200.0
    revision_generator_wall_time_s: float = 1200.0
    root_recovery_wall_time_s: float = 1800.0
    initial_generator_token_budget: int = 32768
    revision_generator_token_budget: int = 32768
    root_recovery_token_budget: int = 32768

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
            }
        failure = objective.active_failure
        graph = objective.dynamic_failure_graph
        dynamic_context = None
        if graph is not None:
            nodes = graph.nodes
            dynamic_context = {
                # Only local execution context is exposed. Internal graph IDs,
                # patch hashes and edge counts are deliberately omitted so the
                # model cannot treat the locator as certification evidence.
                "nodes": tuple({
                    "kind": str(node.kind), "path": node.path,
                    "symbol": node.symbol, "start_line": node.start_line,
                    "end_line": node.end_line, "distance": node.distance,
                } for node in sorted(
                    nodes.values(), key=lambda item: (item.distance, item.path, item.start_line),
                )[:32]),
                "edges": tuple({
                    "kind": str(edge.kind),
                    "source": nodes.get(edge.source_id).symbol if nodes.get(edge.source_id) else None,
                    "target": nodes.get(edge.target_id).symbol if nodes.get(edge.target_id) else None,
                    "distance": edge.distance,
                } for edge in graph.edges.values()
                if nodes.get(edge.source_id) is not None and nodes.get(edge.target_id) is not None)[:64],
                "frontier": tuple({
                    "reason": item.reason, "path": item.path,
                    "symbol": item.symbol, "depth": item.depth,
                } for item in graph.frontier[:24]),
                "expanded_depth": graph.expanded_depth,
            }
        return {
            "objective_id": objective.objective_id,
            "repair_mode": objective.mode,
            "active_failure": cls._compact(
                failure.to_dict() if hasattr(failure, "to_dict") else failure,
                string_limit=12000,
            ),
            "exact_failure_command": objective.exact_failure_command,
            "comparator": objective.comparator,
            "expected_observation": objective.expected_observation,
            "actual_observation": objective.actual_observation,
            "stdout": cls._compact(objective.stdout, string_limit=8000),
            "stderr": cls._compact(objective.stderr, string_limit=8000),
            "traceback_frames": objective.traceback_frames,
            "current_full_diff": objective.current_full_diff,
            "parent_patch_hash": objective.parent_patch_hash,
            "current_patch_hash": objective.current_patch_hash,
            "relevant_source_slices": cls._compact(
                tuple(item.to_dict() for item in objective.relevant_source_slices),
                string_limit=5000,
            ),
            "changed_hunks": cls._compact(
                tuple(item.to_dict() for item in objective.changed_hunks),
                string_limit=3000,
            ),
            "dynamic_failure_context": cls._compact(dynamic_context, string_limit=5000),
            "locked_checks": tuple(item.to_dict() for item in objective.locked_checks),
            "preservation_checks": tuple(item.to_dict() for item in objective.preservation_checks),
            "mechanical_blockers": tuple(item.to_dict() for item in objective.mechanical_blockers),
            "previous_attempts": tuple(item.to_dict() for item in objective.previous_attempts),
            "forbidden_repeated_mechanisms": objective.forbidden_repeated_mechanisms,
            "attempt_history": cls._compact(attempt_history[-8:], string_limit=1200),
        }

    @classmethod
    def _prompt(
        cls,
        objective: RepairObjective | InitialPatchObjective,
        attempt_history: tuple[dict[str, Any], ...] = (),
    ) -> str:
        revision = objective.objective_kind != "INITIAL_PATCH"
        retry_marker = os.environ.get("REACHPATCH_RA51_ATTEMPT", "1")
        retry_guidance = (
            f"This is independent generation retry {retry_marker}. The previous "
            "case-level attempt did not produce an acceptable patch. Do not repeat "
            "its rejected algorithm or exact diff; derive a materially different, "
            "executable repair from the issue and current source.\n"
            if retry_marker != "1" else ""
        )
        return (
            "You are the Repair Player in an execution-driven Reach-Avoid loop. "
            "Work on the current working tree with the supplied tools and return exactly "
            "one tool call per turn. The exact executable failure and typed Oracle are the source of truth; any dynamic graph is context only and cannot certify progress. "
            + (
                "The cumulative diff is already applied to the working tree; submit only "
                "incremental edits. " if revision else
                "Inspect the relevant causal slice and execution contract before making "
                "the initial behavioral edit. "
            )
            + "Edit the existing working tree and preserve its complete cumulative diff; do not reset to a clean repository or generate an independent patch. Read the exact failure command, Oracle, actual observation, stdout/stderr, traceback and current source before changing code. Close only this one ActiveFailure in the current revision. "
            + "For apply_patch, send either a complete git unified diff starting with 'diff --git' and containing ---/+++/@@ hunks, or a complete structured action starting with '*** Begin Patch' and ending with '*** End Patch'. Never send a prose explanation, markdown without a patch body, or a partial hunk. "
            + "Use the allowed source slices, reproduce every grounded observation, and "
            "preserve locked target and preservation behavior. A patch must change executable "
            "behavior, not only comments, whitespace, or an unchanged excerpt. Never use "
            "model wording as a mechanism identity: failed mechanism records are keyed by "
            "actual diff and execution facts. finish_revision is valid only after every grounded "
            "validation has executed to a terminal observation; a stable FAIL may be submitted "
            "for stage/distance comparison, while UNKNOWN or BLOCKED is not progress. "
            "If a protected target passes while preservation fails, make one cumulative edit "
            "that repairs the preservation consumer and retains the target. "
            "Do not delete target behavior, weaken inputs, modify tests, or swallow exceptions to obtain a surface pass. When target progress and a regression coexist, retain the target mechanism and repair the regression in the same cumulative edit. "
            "read_file, search_symbol, inspect_diff, and inspect_incremental_diff are "
            "available whenever needed to understand a failed observation.\n"
            + retry_guidance
            + canonical_json(cls._repair_context(objective, attempt_history))
        )

    @classmethod
    def _convergence_prompt(
        cls,
        objective: RepairObjective | InitialPatchObjective,
        current_incremental_diff: dict[str, Any],
        current_cumulative_diff: dict[str, Any],
        source_contexts: dict[str, dict[str, Any]],
        validation_status: dict[str, Any],
        remaining_turns: int,
        attempt_history: tuple[dict[str, Any], ...] = (),
    ) -> str:
        retry_marker = os.environ.get("REACHPATCH_RA51_ATTEMPT", "1")
        retry_guidance = (
            f"Independent case-level retry {retry_marker}: use a materially different "
            "repair strategy from any prior failed attempt.\n"
            if retry_marker != "1" else ""
        )
        preferred_paths = {
            item.path for item in getattr(objective, "relevant_source_slices", ())
        }
        observed = [
            value for path, value in source_contexts.items()
            if path in preferred_paths
            or not any(part in {"test", "tests"} for part in path.split("/"))
        ][-4:]
        turn_label = "Eight" if remaining_turns == 8 else str(remaining_turns)
        repair_contract = cls._repair_context(objective, attempt_history)
        # The live tree may have changed since the objective was compiled;
        # replace the objective snapshot instead of serializing two diffs.
        repair_contract["current_cumulative_diff"] = current_cumulative_diff.get(
            "canonical_diff", objective.current_full_diff,
        )
        diff_instruction = (
            "The cumulative diff is already present; submit only an incremental hunk "
            "with exact current-source context. "
            if str(current_cumulative_diff.get("canonical_diff", "")).strip() else
            "No edit exists yet. Submit the smallest executable unified diff now, using "
            "exact current-source context. "
        )
        return (
            f"{turn_label} tool turns remain. Converge using the same full repair contract. "
            "Make the smallest evidence-grounded causal edit, but you may read a required "
            "source slice, inspect a trace/diff, or run a grounded validation before editing. "
            "After a successful edit inspect_diff and finish_revision when validations are "
            "satisfied. "
            "Any apply_patch call must contain a complete git unified diff or a complete "
            "*** Begin Patch ... *** End Patch structured action, with exact current-source "
            "context; never submit prose or a partial hunk. "
            + diff_instruction
            + "\n"
            + retry_guidance
            + canonical_json({
                "repair_contract": repair_contract,
                "current_incremental_diff": current_incremental_diff,
                "grounded_validation_status": validation_status,
                "observed_source_contexts": observed,
            })
        )

    @staticmethod
    def _diff_from_content(content: str) -> str | None:
        match = re.search(r"```(?:diff|patch)\s*\n(.*?)```", content, re.DOTALL)
        if match:
            return match.group(1).strip() + "\n"
        if content.lstrip().startswith("diff --git "):
            return content.strip() + "\n"
        return None

    def revise(
        self,
        objective: RepairObjective | InitialPatchObjective,
        tools: RepairToolExecutor,
        *,
        initial: bool = False,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "You are the Repair Player in an execution-driven Reach-Avoid loop. Work only from the exact executable failure and typed Oracle."},
            {"role": "user", "content": self._prompt(
                objective,
                tuple(tools.state.generator_session.attempt_history[-8:]),
            )},
        ]
        max_turns = (
            self.config.initial_generator_max_turns if initial
            else (
                self.config.root_recovery_max_turns
                if getattr(objective, "mode", None) is RepairMode.RECOVER_ROOT_CAUSE
                else (
                    self.config.revision_generator_turn_limit
                    if self.config.revision_generator_max_turns == 12
                    else self.config.revision_generator_max_turns
                )
            )
        )
        timeout = (
            self.config.initial_generator_wall_time_s if initial
            else (
                self.config.root_recovery_wall_time_s
                if getattr(objective, "mode", None) is RepairMode.RECOVER_ROOT_CAUSE
                else self.config.revision_generator_wall_time_s
            )
        )
        tokens = (
            self.config.initial_generator_token_budget if initial
            else (
                self.config.root_recovery_token_budget
                if getattr(objective, "mode", None) is RepairMode.RECOVER_ROOT_CAUSE
                else self.config.revision_generator_token_budget
            )
        )
        recovery_used = False
        mechanism = "causal_edit"
        refresh_after_apply_failure = False
        force_convergence = False
        convergence_prompted = False
        rejected_patches: set[str] = {
            objective.current_full_diff
        } if objective.current_full_diff.strip() else set()
        duplicate_rejection_count = 0
        no_op_rejection_count = 0
        rejected_finished_revision = False
        source_contexts: dict[str, dict[str, Any]] = {}
        last_tool_signature: str | None = None
        repeated_tool_streak = 0
        force_patch_next = False
        turn = 0
        turn_limit = max_turns
        convergence_turns = 12 if initial and max_turns >= 20 else 8
        deadline = time.monotonic() + timeout
        while turn < turn_limit and time.monotonic() < deadline:
            current_incremental = tools.inspect_incremental_diff()
            current_cumulative = tools.inspect_diff()
            has_incremental = bool(
                current_incremental.get("canonical_diff", "").strip()
            )
            has_cumulative = bool(
                current_cumulative.get("canonical_diff", "").strip()
            )
            committable_revision = has_incremental and has_cumulative
            validation = tools.validation_status()
            if (
                committable_revision
                and validation["required_count"]
                and validation["pending_commands"]
            ):
                result = tools.run_allowed_public_check(
                    validation["pending_commands"][0]
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "Reach-Avoid deterministically executed the next executable "
                        "validation for the current cumulative patch. Preserve every "
                        "SATISFIED observation and repair every FAILED observation before "
                        "finishing:\n" + canonical_json(result)
                    ),
                })
                continue
            turn += 1
            remaining_turns = turn_limit - turn + 1
            # Tool phases describe what may finish the revision, not what the
            # model may inspect.  Diagnosis remains available after a rejected
            # edit and during convergence so the next patch can be causal.
            finish_allowed = bool(
                committable_revision
                and (not validation["required_count"] or validation["ready"])
                and not tools.cumulative_patch_rejected(
                    str(current_cumulative.get("patch_hash", ""))
                )
            )
            available_tools = tuple(
                schema for schema in TOOL_SCHEMAS
                if finish_allowed or schema["function"]["name"] != "finish_revision"
            )
            tool_choice: str | dict[str, Any] = "required"
            force_patch = bool(
                not committable_revision
                and (
                    force_patch_next
                    or (remaining_turns <= 4 and bool(source_contexts))
                    or remaining_turns == 1
                )
            )
            if force_patch:
                available_tools = tuple(
                    schema for schema in available_tools
                    if schema["function"]["name"] == "apply_patch"
                )
                tool_choice = {
                    "type": "function",
                    "function": {"name": "apply_patch"},
                }
            elif remaining_turns == 1 and finish_allowed:
                tool_choice = {
                    "type": "function",
                    "function": {"name": "finish_revision"},
                }
            if not convergence_prompted and (
                force_convergence or remaining_turns <= convergence_turns
            ):
                messages = [
                    {
                        "role": "system",
                        "content": "You are the Repair Player in an execution-driven Reach-Avoid loop. The dynamic failure graph is context only.",
                    },
                    {
                        "role": "user",
                        "content": self._convergence_prompt(
                            objective, current_incremental, current_cumulative,
                            source_contexts,
                            tools.validation_summary(),
                            remaining_turns,
                            tuple(tools.state.generator_session.attempt_history[-8:]),
                        ),
                    },
                ]
                convergence_prompted = True
            elif remaining_turns == 1:
                current = tools.inspect_diff()
                if current.get("canonical_diff", "").strip():
                    messages.append({
                        "role": "user",
                        "content": (
                            "Final turn: a non-empty cumulative diff exists. Call "
                            "finish_revision now; do not read or search again."
                        ),
                    })
            try:
                request_tokens = (
                    self.config.root_recovery_token_budget
                    if recovery_used else tokens
                )
                message = self.transport.complete(
                    messages, tools=available_tools, max_tokens=request_tokens,
                    timeout_seconds=max(1.0, deadline - time.monotonic()),
                    tool_choice=tool_choice,
                )
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
                if recovery_used:
                    return {"error_kind": type(exc).__name__, "summary": str(exc), "recovery_used": True}
                recovery_used = True
                turn_limit = max(turn_limit, self.config.root_recovery_max_turns)
                deadline = max(deadline, time.monotonic() + self.config.root_recovery_wall_time_s)
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 400:
                    messages = [
                        {
                            "role": "system",
                            "content": "You are the Repair Player in an execution-driven Reach-Avoid loop. The dynamic failure graph is context only.",
                        },
                        {
                            "role": "user",
                            "content": self._convergence_prompt(
                                objective, current_incremental, current_cumulative,
                                source_contexts, tools.validation_summary(),
                                remaining_turns,
                                tuple(tools.state.generator_session.attempt_history[-8:]),
                            ),
                        },
                    ]
                    convergence_prompted = True
                else:
                    messages.append({
                        "role": "user",
                        "content": "Return one valid tool call. The working patch is preserved; inspect a local source slice if needed.",
                    })
                continue
            force_patch_next = False
            finish_reason = message.pop("_finish_reason", None)
            request_id = message.pop("_request_id", None)
            if request_id:
                phase_name = (
                    "initial" if initial else
                    "root_recovery" if recovery_used or getattr(objective, "mode", None) is RepairMode.RECOVER_ROOT_CAUSE
                    else f"revision:{tools.state.revision_count}"
                )
                tools.state.generator_session.conversation.append({
                    "role": "model_request",
                    "request_id": str(request_id),
                    "objective_id": objective.objective_id,
                    "phase": phase_name,
                    "phase_key": (
                        "case:initial" if phase_name == "initial" else
                        f"case:root_recovery:{tools.state.revision_count + 1}"
                        if phase_name == "root_recovery" else
                        f"case:revision:{tools.state.revision_count + 1}"
                    ),
                })
            messages.append(message)
            if finish_reason == "length":
                # The tree is authoritative.  Keep a partial cumulative edit
                # and give the same frontier one bounded context-compression
                # recovery turn instead of clearing or sealing the patch.
                recovery_used = True
                turn_limit = max(turn_limit, self.config.root_recovery_max_turns)
                deadline = max(deadline, time.monotonic() + self.config.root_recovery_wall_time_s)
                messages.append({
                    "role": "user",
                    "content": (
                        "The provider response was truncated. Preserve the current working "
                        "patch, compress context to the selected frontier and continue "
                        "with exactly one executable tool call."
                    ),
                })
                continue
            tool_calls = message.get("tool_calls") or ()
            allowed_tool_names = {
                schema["function"]["name"] for schema in available_tools
            }
            if not tool_calls:
                content = str(message.get("content") or "")
                patch = self._diff_from_content(content)
                if patch:
                    try:
                        tools.invoke("apply_patch", {"patch": patch})
                        tools.invoke("finish_revision", {
                            "summary": "Applied model-provided unified diff",
                            "mechanism": mechanism,
                        })
                    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
                        rejected_patches.add(patch)
                        force_convergence = True
                        messages.append({
                            "role": "user",
                            "content": (
                                "The prose diff was rejected and the pre-call working tree "
                                "was restored. Return one valid, materially different "
                                "apply_patch tool call grounded in the current observations: "
                                f"{exc}"
                            ),
                        })
                        continue
                    break
                if recovery_used:
                    force_convergence = True
                    continue
                recovery_used = True
                turn_limit = max(turn_limit, self.config.root_recovery_max_turns)
                messages.append({
                    "role": "user",
                    "content": "Use a valid tool call; do not replace the response with prose.",
                })
                continue
            executed_call = False
            deferred_calls = False
            for call in tool_calls:
                function = call.get("function", {})
                name = str(function.get("name", ""))
                followup = ""
                arguments: dict[str, Any] = {}
                duplicate_patch = False
                if executed_call:
                    deferred_calls = True
                    messages.append({
                        "role": "tool",
                        "tool_call_id": str(call.get("id", name)),
                        "content": canonical_json({
                            "error": "DeferredToolCall",
                            "detail": (
                                "Only the first tool call in a turn is executed because "
                                "each edit can change the valid Reach-Avoid tool phase."
                            ),
                        }),
                    })
                    continue
                executed_call = True
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    signature = canonical_json({"name": name, "arguments": arguments})
                    if signature == last_tool_signature:
                        repeated_tool_streak += 1
                    else:
                        last_tool_signature = signature
                        repeated_tool_streak = 1
                    if repeated_tool_streak >= 3 and name not in {"apply_patch", "finish_revision"}:
                        force_patch_next = True
                        followup = (
                            "The same diagnostic tool call has been repeated three times. "
                            "Stop searching and apply the smallest causal patch now; "
                            "the next turn must use apply_patch on the current source."
                        )
                    if name not in allowed_tool_names:
                        raise ValueError(
                            f"{name or '<empty>'} is not allowed in this tool phase; "
                            f"choose one of {sorted(allowed_tool_names)}"
                        )
                    if name == "apply_patch":
                        patch = str(arguments.get("patch", ""))
                        duplicate_patch = patch in rejected_patches
                        if duplicate_patch:
                            raise ValueError(
                                "this exact unified diff was already rejected; "
                                "change the repair algorithm and build a different hunk"
                            )
                    result = tools.invoke(name, arguments)
                    if name == "apply_patch":
                        rejected_finished_revision = False
                    if name == "read_file" and isinstance(result, dict):
                        source_contexts[str(result.get("path", ""))] = result
                        if result.get("redundant"):
                            force_patch_next = True
                            followup = (
                                "That source interval was already available. Stop repeated "
                                "reads and move to the smallest causal apply_patch."
                            )
                    if name == "finish_revision":
                        mechanism = str(arguments.get("mechanism", mechanism))
                    if name == "read_file" and refresh_after_apply_failure:
                        refresh_after_apply_failure = False
                except (ValueError, KeyError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
                    result = {"error": type(exc).__name__, "detail": str(exc)}
                    if name == "apply_patch" and name in allowed_tool_names:
                        patch = str(arguments.get("patch", ""))
                        force_convergence = True
                        convergence_prompted = True
                        objective_effect = canonical_json(
                            self._repair_context(objective).get(
                                "active_failure",
                                self._repair_context(objective).get("goal_contracts", ()),
                            )
                        )
                        if duplicate_patch:
                            duplicate_rejection_count += 1
                            refresh_after_apply_failure = False
                            followup = (
                                "The exact rejected patch was repeated. The current source is "
                                "already in context, so do not read it again and do not submit "
                                "the same algorithm. Apply a materially different executable "
                                "change grounded in this required observable effect: "
                                f"{objective_effect}"
                            )
                            if duplicate_rejection_count >= 2:
                                return {
                                    "error_kind": "REPEATED_REJECTED_PATCH",
                                    "summary": (
                                        "The generator repeated an unchanged or unappliable "
                                        "patch after execution-backed rejection feedback."
                                    ),
                                    "mechanism": "repeated_rejected_patch",
                                    "recovery_used": recovery_used,
                                }
                        else:
                            if patch:
                                rejected_patches.add(patch)
                            rejected_no_op = "no-op" in str(exc).casefold()
                            if rejected_no_op:
                                no_op_rejection_count += 1
                            refresh_after_apply_failure = True
                            followup = (
                                "The patch was rejected and the tree is unchanged. On the next "
                                "turn, read the current target file. Then construct a different "
                                "hunk from that exact source; do not repeat the rejected diff. "
                                "A valid patch must change executable control flow, data flow, "
                                "a return value, an exception, or a state effect required by the "
                                "objective. Reprinting the method, changing only comments or "
                                "whitespace, and replacing a line with itself are no-ops. "
                                f"Required observable effect: {objective_effect}"
                            )
                            if no_op_rejection_count >= 3:
                                return {
                                    "error_kind": "REPEATED_NOOP_PATCH",
                                    "summary": (
                                        "The generator submitted three distinct patches with "
                                        "no executable source change."
                                    ),
                                    "mechanism": "repeated_noop_patch",
                                    "recovery_used": recovery_used,
                                }
                    elif name == "finish_revision" and "previously rolled back" in str(exc):
                        rejected_finished_revision = True
                        force_convergence = True
                        convergence_prompted = True
                        followup = (
                            "Reach-Avoid already evaluated and rolled back this exact "
                            "cumulative patch. Do not rename its mechanism or finish it "
                            "again. Apply one materially different causal edit that preserves "
                            "the protected target executions and closes the counterexample."
                        )
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id", name)),
                    "content": canonical_json(result),
                })
                if followup:
                    messages.append({"role": "user", "content": followup})
            if deferred_calls:
                messages.append({
                    "role": "user",
                    "content": (
                        "Additional tool calls were deferred after the first call changed "
                        "or inspected state. Re-evaluate the current diff and return exactly "
                        "one next tool call."
                    ),
                })
            if tools.finished:
                break
        final_incremental = tools.inspect_incremental_diff()
        final_cumulative = tools.inspect_diff()
        # Validation is deterministic evidence collection, not an extra model
        # turn.  A model can consume its final available turn by applying the
        # edit, so drain the bounded executable validation queue before
        # deciding whether that edit may be retained for transition evaluation.
        # This never treats an empty validation set as ready.
        if (
            final_incremental.get("canonical_diff", "").strip()
            and final_cumulative.get("canonical_diff", "").strip()
        ):
            while tools.validation_status()["pending_commands"]:
                tools.run_allowed_public_check(
                    tools.validation_status()["pending_commands"][0]
                )
        final_validation = tools.validation_status()
        # A stable FAIL is still a valid trial.  Reach-Avoid, rather than the
        # edit agent, owns the decision to advance, keep repairing, or reject.
        # Only pending/UNKNOWN execution prevents a trial from being evaluated.
        validation_observed = bool(
            not final_validation["pending_commands"]
            and not final_validation["unknown_validation_ids"]
        )
        retained_edit = bool(
            final_incremental.get("canonical_diff", "").strip()
            and final_cumulative.get("canonical_diff", "").strip()
            and not tools.cumulative_patch_rejected(
                str(final_cumulative.get("patch_hash", ""))
            )
            and validation_observed
        )
        if not retained_edit and not tools.finished and mechanism == "causal_edit":
            mechanism = "context_expansion_without_edit"
        return {
            "error_kind": None if tools.finished or retained_edit else "TURN_LIMIT",
            "summary": (
                tools.finish_summary
                or "Retained a non-empty tool-applied edit for Reach-Avoid evaluation"
                if retained_edit else tools.finish_summary
            ),
            "mechanism": mechanism,
            "recovery_used": recovery_used,
        }
