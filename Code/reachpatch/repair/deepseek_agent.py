from __future__ import annotations

import json
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Callable
from urllib.request import Request, urlopen

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.repair.context import build_repair_context
from reachpatch.repair.tools import ProposedEdit, RepairToolExecutor


SYSTEM_PROMPT = """You are the Repair Player maintaining one persistent working patch.
Preserve previously validated edits. Use repository tools to inspect only relevant code.
You may make multiple coordinated edits when they implement one repair mechanism.
For apply_edits, choose exactly one registered mechanism value from the tool schema;
put a human-readable explanation in finish_revision instead of inventing a mechanism name.
Do not claim success from reasoning alone. After editing, request executable public checks.
When evidence is insufficient, request targeted context instead of returning no action.
Never access hidden tests, gold patches, test_patch, or harness outcomes."""


@dataclass(slots=True)
class GeneratorConversation(SerializableRecord):
    conversation_id: str
    messages: list[dict]
    inspected_files: set[str] = field(default_factory=set)
    inspected_symbols: set[str] = field(default_factory=set)
    attempted_mechanisms: list[str] = field(default_factory=list)
    accepted_patch_hashes: list[str] = field(default_factory=list)
    rejected_patch_hashes: list[str] = field(default_factory=list)
    delivered_counterexamples: set[str] = field(default_factory=set)
    pending_context_requests: list = field(default_factory=list)

    @classmethod
    def create(cls, instance_id: str) -> "GeneratorConversation":
        return cls(
            conversation_id=stable_id("generator-conversation", instance_id),
            messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        )


@dataclass(frozen=True, slots=True)
class GeneratorRevision(SerializableRecord):
    revision_id: str
    mechanism: str
    edits: tuple[ProposedEdit, ...]
    summary: str
    context_requests: tuple
    requested_public_checks: tuple[str, ...]
    tool_turns: int
    status: str


class ActionConversionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    NEEDS_SLICE_EXPANSION = "NEEDS_SLICE_EXPANSION"
    INVALID_OPERATOR = "INVALID_OPERATOR"
    INVALID_SOURCE = "INVALID_SOURCE"
    FORBIDDEN_PATH = "FORBIDDEN_PATH"


class GeneratorBlockedExternal(RuntimeError):
    """The persistent generator could not reach or decode its external model."""

    def __init__(self, operation: str, cause: Exception) -> None:
        self.operation = operation
        self.cause_type = type(cause).__name__
        self.detail = str(cause)
        super().__init__(f"{operation}: {self.cause_type}: {self.detail}")


@dataclass(frozen=True, slots=True)
class ActionConversionResult(SerializableRecord):
    status: ActionConversionStatus
    revision: GeneratorRevision
    reasons: tuple[str, ...]


_MECHANISMS = {
    "guard_expand", "guard_tighten", "operand_predicate", "protocol_dispatch",
    "remove_wrapper", "restore_representation", "exception_edge",
    "cross_function_propagation", "state_update_order", "preservation_restore",
    "causal_slice_rewrite", "initial_issue_repair",
}


def _normalize_mechanism(value: str, *, initial: bool) -> str | None:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _MECHANISMS:
        return normalized
    keyword_mapping = (
        (("exception", "error", "raise", "catch", "wrap"), "exception_edge"),
        (("dispatch", "notimplemented", "operand"), "protocol_dispatch"),
        (("guard", "condition", "predicate"), "guard_tighten"),
        (("wrapper",), "remove_wrapper"),
        (("representation", "shape"), "restore_representation"),
        (("state", "order"), "state_update_order"),
        (("preserv", "regression"), "preservation_restore"),
        (("import", "propagat", "caller", "callee"), "cross_function_propagation"),
        (("fix", "repair", "rewrite", "replace"), "causal_slice_rewrite"),
    )
    for keywords, mechanism in keyword_mapping:
        if any(keyword in normalized for keyword in keywords):
            return "initial_issue_repair" if initial and mechanism == "causal_slice_rewrite" else mechanism
    return None


def convert_revision_action(state, revision: GeneratorRevision) -> ActionConversionResult:
    normalized_mechanism = _normalize_mechanism(
        revision.mechanism,
        initial=not bool(getattr(
            getattr(state.checkpoint, "patch", None), "canonical_diff", ""
        )),
    )
    if normalized_mechanism is None:
        return ActionConversionResult(
            ActionConversionStatus.INVALID_OPERATOR, revision,
            (f"unregistered mechanism: {revision.mechanism}",),
        )
    if normalized_mechanism != revision.mechanism:
        revision = replace(revision, mechanism=normalized_mechanism)
    root = __import__("pathlib").Path(state.checkpoint.snapshot_tree).resolve()
    active_files = set(state.program_graph.file_index)
    requested_files = {
        path for request in revision.context_requests
        for path in getattr(request, "file_paths", ())
    }
    needs_expansion = False
    reasons: list[str] = []
    for edit in revision.edits:
        relative = edit.relative_path.replace("\\", "/")
        lowered = relative.lower()
        if (
            relative.startswith("/") or ".." in __import__("pathlib").Path(relative).parts
            or any(token in lowered for token in ("test_patch", "gold", "hidden", ".git/"))
            or __import__("pathlib").Path(relative).name.startswith("test_")
            or "tests" in __import__("pathlib").Path(relative).parts
        ):
            return ActionConversionResult(
                ActionConversionStatus.FORBIDDEN_PATH, revision,
                (f"forbidden edit path: {relative}",),
            )
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return ActionConversionResult(
                ActionConversionStatus.INVALID_SOURCE, revision,
                (f"source file missing: {relative}",),
            )
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        actual = "\n".join(lines[edit.start_line - 1:edit.end_line])
        if actual != edit.expected_source.rstrip("\n"):
            return ActionConversionResult(
                ActionConversionStatus.INVALID_SOURCE, revision,
                (f"source changed at {relative}:{edit.start_line}",),
            )
        if relative not in active_files:
            if relative in requested_files:
                needs_expansion = True
                reasons.append(f"expand active slice for {relative}")
            else:
                return ActionConversionResult(
                    ActionConversionStatus.INVALID_SOURCE, revision,
                    (f"source outside active slice without context request: {relative}",),
                )
    return ActionConversionResult(
        ActionConversionStatus.NEEDS_SLICE_EXPANSION if needs_expansion else ActionConversionStatus.ACCEPTED,
        revision, tuple(reasons),
    )


Transport = Callable[[list[dict], list[dict]], dict]


class DeepSeekHTTPTransport:
    def __init__(self, api_key: str, *, model: str = "deepseek-chat", base_url: str = "https://api.deepseek.com", max_concurrency: int = 10) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = {"model": self.model, "temperature": 0, "messages": messages,
                   "tools": tools, "tool_choice": "auto", "max_tokens": 4000}
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        started = time.monotonic()
        record: dict[str, Any] = {
            "message_count": len(messages), "status": "REQUESTED",
        }
        try:
            with self._semaphore, urlopen(request, timeout=180) as response:
                raw = json.loads(response.read().decode("utf-8"))
            message = dict(raw["choices"][0]["message"])
            record.update({
                "status": "RESPONSE",
                "tool_names": [
                    str(item.get("function", {}).get("name", ""))
                    for item in message.get("tool_calls", ())
                ],
                "usage": dict(raw.get("usage", {})),
            })
            return message
        except Exception as exc:
            record.update({
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            })
            raise
        finally:
            record["duration_seconds"] = time.monotonic() - started
            with self._lock:
                self.calls.append(record)


def _tool_schema() -> list[dict]:
    def tool(name: str, properties: dict, required: list[str] | None = None) -> dict:
        return {"type": "function", "function": {"name": name, "description": name.replace("_", " "),
                "parameters": {"type": "object", "properties": properties, "required": required or []}}}
    string = {"type": "string"}
    strings = {"type": "array", "items": string}
    return [
        tool("search_code", {"query": string, "paths": strings}, ["query"]),
        tool("read_file", {"path": string, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
        tool("inspect_symbol", {"symbol": string}, ["symbol"]),
        tool("find_callers", {"symbol": string}, ["symbol"]),
        tool("find_references", {"symbol": string}, ["symbol"]),
        tool("show_current_diff", {}),
        tool("run_public_check", {"check_id": string}, ["check_id"]),
        tool("request_program_slice", {"symbols": strings, "relation_kinds": strings}, ["symbols", "relation_kinds"]),
        tool("apply_edits", {"mechanism": {"type": "string", "enum": sorted(_MECHANISMS)}, "edits": {"type": "array", "items": {"type": "object", "properties": {
            "relative_path": string, "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
            "expected_source": string, "replacement": string},
            "required": ["relative_path", "start_line", "end_line", "expected_source", "replacement"]}}}, ["mechanism", "edits"]),
        tool("finish_revision", {"summary": string}, ["summary"]),
    ]


class PersistentDeepSeekAgent:
    def __init__(self, transport: Transport, *, max_tool_turns: int = 12) -> None:
        self.transport = transport
        self.max_tool_turns = max_tool_turns

    @staticmethod
    def _request_messages(conversation: GeneratorConversation) -> list[dict]:
        last_user = max(
            index for index, message in enumerate(conversation.messages)
            if message.get("role") == "user"
        )
        memory = {
            "conversation_id": conversation.conversation_id,
            "inspected_files": sorted(conversation.inspected_files)[-40:],
            "inspected_symbols": sorted(conversation.inspected_symbols)[-40:],
            "attempted_mechanisms": conversation.attempted_mechanisms[-20:],
            "accepted_patch_hashes": conversation.accepted_patch_hashes[-10:],
            "rejected_patch_hashes": conversation.rejected_patch_hashes[-10:],
            "delivered_counterexamples": sorted(conversation.delivered_counterexamples)[-20:],
        }
        return [
            conversation.messages[0],
            {
                "role": "system",
                "content": "Persistent conversation memory: "
                + json.dumps(memory, sort_keys=True),
            },
            *conversation.messages[last_user:],
        ]

    def _invoke(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor, *, mode: str) -> GeneratorRevision:
        context = build_repair_context(state, mode=mode)
        conversation.messages.append({"role": "user", "content": json.dumps(context.to_dict(), sort_keys=True)})
        mechanism = "initial_issue_repair" if mode == "INITIAL" else "causal_slice_rewrite"
        requested_checks: list[str] = []
        summary = ""
        turns = 0
        for turns in range(1, self.max_tool_turns + 1):
            try:
                message = self.transport(
                    self._request_messages(conversation), _tool_schema()
                )
            except GeneratorBlockedExternal:
                raise
            except Exception as exc:
                raise GeneratorBlockedExternal(
                    f"deepseek_{mode.lower()}", exc
                ) from exc
            if not isinstance(message, dict):
                raise GeneratorBlockedExternal(
                    f"deepseek_{mode.lower()}_response",
                    ValueError("model response is not an object"),
                )
            conversation.messages.append(message)
            calls = message.get("tool_calls") or ()
            if not calls:
                summary = str(message.get("content") or "revision response without finish tool")
                break
            finished = False
            for call in calls:
                function = call.get("function", {})
                name = str(function.get("name", ""))
                arguments: dict[str, Any] = {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if name == "apply_edits":
                        mechanism = str(arguments.pop("mechanism", mechanism))
                        edits = tuple(ProposedEdit(**item) for item in arguments["edits"])
                        output = tools.apply_edits(edits)
                    elif name == "finish_revision":
                        summary = str(arguments["summary"])
                        output = tools.finish_revision(summary)
                        finished = True
                    elif name == "run_public_check":
                        requested_checks.append(str(arguments["check_id"]))
                        output = tools.run_public_check(**arguments)
                    else:
                        output = getattr(tools, name)(**arguments)
                    if name == "read_file" and "path" in arguments:
                        conversation.inspected_files.add(str(arguments["path"]))
                    if (
                        name in {"inspect_symbol", "find_callers", "find_references"}
                        and "symbol" in arguments
                    ):
                        conversation.inspected_symbols.add(str(arguments["symbol"]))
                except (
                    OSError, ValueError, KeyError, TypeError,
                    subprocess.SubprocessError,
                ) as exc:
                    output = {"error": f"{type(exc).__name__}: {exc}"}
                conversation.messages.append({
                    "role": "tool", "tool_call_id": call.get("id"),
                    "content": json.dumps(output, sort_keys=True),
                })
            if finished:
                break
        conversation.attempted_mechanisms.append(mechanism)
        active_files = set(state.program_graph.file_index)
        for relative in sorted({item.relative_path for item in tools.staged_edits} - active_files):
            tools.context_requests.append(__import__(
                "reachpatch.program_graph.slice", fromlist=["ContextRequest"]
            ).ContextRequest(file_paths=(relative,), reason="generator_inspected_edit_target"))
        conversation.pending_context_requests.extend(tools.context_requests)
        revision = GeneratorRevision(
            revision_id=stable_id(
                "generator-revision", conversation.conversation_id,
                len(conversation.attempted_mechanisms), tools.staged_edits,
            ),
            mechanism=mechanism, edits=tuple(tools.staged_edits),
            summary=summary, context_requests=tuple(tools.context_requests),
            requested_public_checks=tuple(dict.fromkeys(requested_checks)),
            tool_turns=turns,
            status="PROPOSED" if tools.staged_edits else "CONTEXT_ONLY",
        )
        return revision

    def generate_initial_patch(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor) -> GeneratorRevision:
        return self._invoke(state, conversation, tools, mode="INITIAL")

    def repair_from_counterexamples(self, state, conversation: GeneratorConversation, packets, tools: RepairToolExecutor) -> GeneratorRevision:
        conversation.delivered_counterexamples.update(item.counterexample_id for item in packets)
        return self._invoke(state, conversation, tools, mode="COUNTEREXAMPLE_REPAIR")

    def root_recovery(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor) -> GeneratorRevision:
        return self._invoke(state, conversation, tools, mode="ROOT_RECOVERY")


def generate_initial_patch(state, conversation, agent: PersistentDeepSeekAgent, tools: RepairToolExecutor) -> GeneratorRevision:
    return agent.generate_initial_patch(state, conversation, tools)


def repair_from_counterexamples(state, conversation, packets, agent: PersistentDeepSeekAgent, tools: RepairToolExecutor) -> GeneratorRevision:
    return agent.repair_from_counterexamples(state, conversation, packets, tools)
