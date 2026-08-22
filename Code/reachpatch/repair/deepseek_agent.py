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
from reachpatch.models.reach_avoid import RepairObjective

from .tools import RepairToolExecutor, TOOL_SCHEMAS


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    initial_generator_max_turns: int = 20
    revision_generator_max_turns: int = 12
    root_recovery_max_turns: int = 16
    initial_generator_wall_time_s: float = 600.0
    revision_generator_wall_time_s: float = 480.0
    initial_generator_token_budget: int = 32768
    revision_generator_token_budget: int = 32768

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
        return dict(raw["choices"][0]["message"])


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
        objective: RepairObjective,
        attempt_history: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        primary = objective.primary_requirement
        public_context = []
        public_context_total = 0
        for item in objective.public_context:
            compacted = cls._compact(item, string_limit=8000)
            encoded_size = len(canonical_json(compacted))
            if public_context_total + encoded_size > 20000:
                break
            public_context.append(compacted)
            public_context_total += encoded_size
        related = tuple(
            {
                key: item.get(key)
                for key in (
                    "requirement_id", "kind", "preservation", "operation",
                    "expected_observation", "preconditions", "domain_constraints",
                )
                if key in item
            }
            for item in objective.related_requirements[:16]
        )
        executions = tuple({
            "command": command,
            "input": cls._compact(
                objective.concrete_inputs[index]
                if index < len(objective.concrete_inputs) else None,
                string_limit=900,
            ),
            "derivation": cls._compact(
                objective.input_derivations[index]
                if index < len(objective.input_derivations) else (),
                string_limit=1400,
            ),
            "oracle": cls._compact(
                objective.oracle_relations[index]
                if index < len(objective.oracle_relations) else {},
                string_limit=1400,
            ),
        } for index, command in enumerate(objective.reproduction_commands[:16]))
        slices = []
        total = 0
        for item in objective.editable_source_slices:
            compacted = cls._compact(item, string_limit=2600)
            encoded_size = len(canonical_json(compacted))
            if total + encoded_size > 18000:
                break
            slices.append(compacted)
            total += encoded_size
        compact_attempts = []
        attempt_total = 0
        for item in reversed(attempt_history[-8:]):
            compacted = cls._compact({
                key: item.get(key)
                for key in (
                    "attempt_id", "objective_id", "source_patch_hash",
                    "incremental_patch_hash", "incremental_diff_hash",
                    "incremental_diff", "changed_files", "changed_hunk_ids",
                    "changed_symbols", "tool_calls", "validation", "result_kind",
                    "error_kind", "transition_decision", "transition_reasons",
                    "rejection_reasons", "remaining_failure_signature",
                ) if key in item
            }, string_limit=900)
            size = len(canonical_json(compacted))
            if attempt_total + size > 12000:
                break
            compact_attempts.append(compacted)
            attempt_total += size
        return {
            "objective_id": objective.objective_id,
            "objective_kind": objective.objective_kind,
            "primary_requirement": cls._compact({
                key: primary.get(key) for key in (
                    "requirement_id", "kind", "quantifier", "operation",
                    "expected_observation", "exception_contract", "preconditions",
                    "domain_constraints", "preservation", "authority",
                ) if key in primary
            }),
            "public_issue_context": tuple(public_context),
            "related_requirements": related,
            "execution_contract": executions,
            "failure_observations": cls._compact(objective.observations, string_limit=1400),
            "failure_signatures": objective.failure_signatures,
            "first_divergences": cls._compact(objective.first_divergences),
            "bindings": cls._compact(objective.bindings, string_limit=1400),
            "actual_hunks": cls._compact(objective.actual_hunks, string_limit=1800),
            "causal_cuts": cls._compact(objective.causal_cuts, string_limit=1600),
            "impact_cone": cls._compact(objective.impact_cone, string_limit=1200),
            "causal_guidance": cls._compact(objective.causal_guidance, string_limit=1600),
            "locked_check_ids": objective.locked_check_ids,
            "protected_target_ids": objective.protected_target_ids,
            "protected_preservation_ids": objective.protected_preservation_ids,
            "allowed_source_slices": slices,
            "current_cumulative_diff": objective.cumulative_diff,
            "expected_next_effects": objective.expected_next_effects,
            "failed_mechanism_records": cls._compact(
                objective.forbidden_mechanisms, string_limit=1200,
            ),
            "attempt_history": tuple(reversed(compact_attempts)),
        }

    @classmethod
    def _prompt(
        cls,
        objective: RepairObjective,
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
            "You are the Repair Player in a graph-grounded Reach-Avoid loop. "
            "Work on the current working tree with the supplied tools and return exactly "
            "one tool call per turn. The graph-derived causal brief follows first; this "
            "single repair contract is the source of truth. "
            + (
                "The cumulative diff is already applied to the working tree; submit only "
                "incremental edits. " if revision else
                "Inspect the relevant causal slice and execution contract before making "
                "the initial behavioral edit. "
            )
            + "Use the allowed source slices, reproduce every grounded observation, and "
            "preserve locked target and preservation behavior. A patch must change executable "
            "behavior, not only comments, whitespace, or an unchanged excerpt. Never use "
            "model wording as a mechanism identity: failed mechanism records are keyed by "
            "actual diff and execution facts. finish_revision is valid only after the current "
            "diff is inspected and all required validations are SATISFIED; UNKNOWN is not PASS. "
            "If a protected target passes while preservation fails, make one cumulative edit "
            "that repairs the preservation consumer and retains the target. "
            "read_file, search_symbol, inspect_callers, inspect_trace, and inspect_diff are "
            "available whenever needed to understand a failed observation.\n"
            + retry_guidance
            + canonical_json(cls._repair_context(objective, attempt_history))
        )

    @classmethod
    def _convergence_prompt(
        cls,
        objective: RepairObjective,
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
            str(item.get("path", "")) for item in objective.editable_source_slices
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
            "canonical_diff", objective.cumulative_diff,
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
        objective: RepairObjective,
        tools: RepairToolExecutor,
        *,
        initial: bool = False,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": "You are the Repair Player in a graph-grounded Reach-Avoid loop."},
            {"role": "user", "content": self._prompt(
                objective,
                tuple(tools.state.generator_session.attempt_history[-8:]),
            )},
        ]
        max_turns = (
            self.config.initial_generator_max_turns if initial
            else self.config.revision_generator_max_turns
        )
        timeout = (
            self.config.initial_generator_wall_time_s if initial
            else self.config.revision_generator_wall_time_s
        )
        tokens = (
            self.config.initial_generator_token_budget if initial
            else self.config.revision_generator_token_budget
        )
        recovery_used = False
        mechanism = "causal_edit"
        refresh_after_apply_failure = False
        force_convergence = False
        convergence_prompted = False
        rejected_patches: set[str] = {
            objective.cumulative_diff
        } if objective.cumulative_diff.strip() else set()
        duplicate_rejection_count = 0
        no_op_rejection_count = 0
        rejected_finished_revision = False
        source_contexts: dict[str, dict[str, Any]] = {}
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
                        "Reach-Avoid deterministically executed the next graph-grounded "
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
                        "content": "You are the Repair Player in a graph-grounded Reach-Avoid loop.",
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
                message = self.transport.complete(
                    messages, tools=available_tools, max_tokens=tokens,
                    timeout_seconds=max(1.0, deadline - time.monotonic()),
                    tool_choice=tool_choice,
                )
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
                if recovery_used:
                    return {"error_kind": type(exc).__name__, "summary": str(exc), "recovery_used": True}
                recovery_used = True
                turn_limit = max(turn_limit, self.config.root_recovery_max_turns)
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 400:
                    messages = [
                        {
                            "role": "system",
                            "content": "You are the Repair Player in a graph-grounded Reach-Avoid loop.",
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
            messages.append(message)
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
                        objective_effect = canonical_json({
                            "operation": objective.primary_requirement.get("operation"),
                            "expected_observation": objective.primary_requirement.get(
                                "expected_observation"
                            ),
                            "expected_next_effects": objective.expected_next_effects,
                        })
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
                                        "patch after graph-grounded rejection feedback."
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
        retained_edit = bool(
            final_incremental.get("canonical_diff", "").strip()
            and final_cumulative.get("canonical_diff", "").strip()
            and not tools.cumulative_patch_rejected(
                str(final_cumulative.get("patch_hash", ""))
            )
            and tools.validation_status()["ready"]
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
