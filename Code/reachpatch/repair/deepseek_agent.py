from __future__ import annotations

import ast
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from reachpatch.models.base import SerializableRecord, content_hash, stable_id
from reachpatch.repair.context import build_repair_context
from reachpatch.repair.tools import ProposedEdit, RepairToolExecutor


SYSTEM_PROMPT = """You are the Repair Player maintaining one persistent working patch.
Preserve previously validated edits. Use repository tools to inspect only relevant code.
You may make multiple coordinated edits when they implement one repair mechanism.
For apply_edits, choose exactly one registered mechanism value from the tool schema;
put a human-readable explanation in finish_revision instead of inventing a mechanism name.
Use the smallest sufficient edit ranges. expected_source must be copied exactly from a
read_file result; never reconstruct it from memory or replace an entire class when a
few statements implement the repair.
Do not claim success from reasoning alone. After editing, request executable public checks.
When evidence is insufficient, request targeted context or declare a concrete blocker.
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
    current_working_diff: str = ""
    inspected_line_ranges: set[str] = field(default_factory=set)
    mechanism_failure_signatures: dict[str, list[str]] = field(default_factory=dict)
    rolled_back_diffs: list[str] = field(default_factory=list)
    eliminated_counterexamples: set[str] = field(default_factory=set)
    unresolved_counterexamples: set[str] = field(default_factory=set)
    passed_preservation_checks: set[str] = field(default_factory=set)
    last_evidence_fingerprint: str | None = None
    revision_count: int = 0

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

    def _call_with_tool_choice(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | dict,
    ) -> dict:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "max_tokens": 4000,
        }
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

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        return self._call_with_tool_choice(messages, tools, "auto")

    def call_with_tool_choice(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | dict,
    ) -> dict:
        """Expose transport-level tool choice for callers that explicitly need it."""
        return self._call_with_tool_choice(messages, tools, tool_choice)


def _tool_schema(
    allowed_names: frozenset[str] | None = None,
) -> list[dict]:
    def tool(name: str, properties: dict, required: list[str] | None = None) -> dict:
        return {"type": "function", "function": {"name": name, "description": name.replace("_", " "),
                "parameters": {"type": "object", "properties": properties, "required": required or []}}}
    string = {"type": "string"}
    strings = {"type": "array", "items": string}
    schemas = [
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
        tool("declare_blocker", {"reason": string, "missing_evidence": strings}, ["reason"]),
    ]
    if allowed_names is None:
        return schemas
    return [
        schema for schema in schemas
        if schema["function"]["name"] in allowed_names
    ]


_ALLOWED_TOOL_NAMES = frozenset(
    item["function"]["name"] for item in _tool_schema()
)
_FINAL_TURN_TOOL_NAMES = frozenset({
    "apply_edits", "request_program_slice", "finish_revision",
    "declare_blocker",
})


def _compile_reproduction_message(
    message: dict[str, Any],
    *,
    primary_issue: str = "",
    project_runner: str = "",
) -> tuple[dict[str, str] | None, str]:
    for call in message.get("tool_calls") or ():
        function = call.get("function", {})
        if function.get("name") != "submit_reproduction":
            continue
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            filename = Path(str(arguments["filename"])).name
            setup_source = str(arguments["setup_source"])
            expression = str(arguments["observation_expression"]).strip()
            expected = str(arguments["expected_observation"]).strip()
            tree = ast.parse(setup_source)
            expression_tree = ast.parse(expression, mode="eval")
        except (KeyError, TypeError, ValueError, SyntaxError, json.JSONDecodeError) as exc:
            return None, f"invalid reproduction payload or Python syntax: {exc}"
        if not filename.endswith(".py") or not expected or not expression:
            return None, "filename, observation expression, and expected observation are required"
        if filename.lower() in {"__init__.py", "conftest.py"}:
            filename = "test_public_reproduction.py"
        if expected.lower().strip(" .") in {
            "true", "false", "pass", "success", "expected behavior",
            "correct behavior", "a value",
        }:
            expected = f"public behavior satisfies: {expression}"
        forbidden_setup = any(
            isinstance(node, (ast.Assert, ast.Raise))
            for node in ast.walk(tree)
        ) or "sys.exit" in setup_source
        if forbidden_setup:
            return None, "setup_source contains an assertion, raise, or sys.exit"
        if project_runner.lower() == "django":
            for model_class in (
                node for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
                and any(
                    isinstance(base, ast.Attribute) and base.attr == "Model"
                    for base in node.bases
                )
            ):
                for nested in ast.walk(model_class):
                    if not isinstance(nested, ast.Call):
                        continue
                    function = nested.func
                    if not (
                        isinstance(function, ast.Attribute)
                        and function.attr == "check"
                        and isinstance(function.value, ast.Name)
                        and function.value.id != model_class.name
                    ):
                        continue
                    return None, (
                        "Django model class bodies must not call field.check(); "
                        "record the model class and call Model.check() after its "
                        "definition so field binding and backend checks are valid"
                    )
        direct_attribute_type_checks = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"isinstance", "callable"}
            and node.args
            and isinstance(node.args[0], ast.Attribute)
        ]
        issue_lower = primary_issue.lower()
        explicit_attribute_type_contract = any(
            marker in issue_lower for marker in (
                "attribute must be", "attribute should be", "property must be",
                "property should be", "must return a string",
                "should return a string", "must return a callable",
                "should return a callable", "isinstance(",
            )
        )
        if direct_attribute_type_checks and not explicit_attribute_type_contract:
            return None, (
                "observation inspects the runtime type of an intermediate object "
                "attribute, but the primary issue does not define that internal "
                "representation as a public contract; observe the end-to-end API "
                "result, serializer/writer output, exception, or externally visible "
                "behavior instead"
            )
        if not any(
            isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            for node in ast.walk(expression_tree)
        ):
            return None, "observation_expression does not load an observed value"
        expression_names = {
            node.id for node in ast.walk(expression_tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        observation_suffixes = (
            "_success", "_matches", "_unchanged", "_equal", "_ok",
            "_valid", "_callable", "_preserved", "_raised",
            "_warning", "_warnings",
        )
        setup_observations: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if (
                    name.startswith(("has_", "is_", "can_", "supports_"))
                    or name.endswith(observation_suffixes)
                ):
                    setup_observations.add(name)
        missing_observations = sorted(setup_observations - expression_names)
        if missing_observations:
            expression = " and ".join((
                f"({expression})",
                *(f"({name})" for name in missing_observations),
            ))
        source = (
            setup_source.rstrip()
            + "\n\n"
            + f"assert ({expression}), {expected!r}\n"
        )
        return {
            "filename": filename,
            "source": source,
            "expected_observation": expected,
        }, ""
    return None, "response did not call submit_reproduction"


class PersistentDeepSeekAgent:
    def __init__(
        self,
        transport: Transport,
        *,
        max_tool_turns: int = 6,
        max_revisions: int = 6,
    ) -> None:
        self.transport = transport
        self.max_tool_turns = max_tool_turns
        self.max_revisions = max_revisions

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
            "current_working_diff": conversation.current_working_diff,
            "inspected_line_ranges": sorted(conversation.inspected_line_ranges)[-40:],
            "mechanism_failure_signatures": conversation.mechanism_failure_signatures,
            "rolled_back_diffs": conversation.rolled_back_diffs[-10:],
            "eliminated_counterexamples": sorted(conversation.eliminated_counterexamples)[-20:],
            "unresolved_counterexamples": sorted(conversation.unresolved_counterexamples)[-20:],
            "passed_preservation_checks": sorted(conversation.passed_preservation_checks)[-20:],
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

    @staticmethod
    def _exact_source_anchor(context) -> tuple[dict[str, Any], ...]:
        """Keep exact causal source available after conversation compaction."""

        snippets = sorted(
            context.relevant_source_snippets,
            key=lambda item: (
                item.get("origin") == "TARGET_REPRODUCTION_ARTIFACT",
                max(
                    0,
                    int(item.get("snippet_end_line", item.get("end_line", 0)))
                    - int(item.get("snippet_start_line", item.get("start_line", 0))),
                ),
                str(item.get("relative_path", "")),
            ),
        )
        selected: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        total_characters = 0
        for item in snippets:
            content = str(item.get("content", ""))
            relative_path = str(item.get("relative_path", ""))
            if not relative_path or not content:
                continue
            key = (relative_path, content)
            if key in seen:
                continue
            if selected and total_characters + len(content) > 14000:
                continue
            seen.add(key)
            selected.append({
                "relative_path": relative_path,
                "start_line": int(
                    item.get("snippet_start_line", item.get("start_line", 1))
                ),
                "end_line": int(
                    item.get("snippet_end_line", item.get("end_line", 1))
                ),
                "symbol": str(item.get("symbol", "")),
                "content": content,
            })
            total_characters += len(content)
            if len(selected) >= 3:
                break
        return tuple(selected)

    @staticmethod
    def _requested_symbol_status(state, symbols: tuple[str, ...]) -> tuple[
        tuple[str, ...], tuple[str, ...], tuple[str, ...]
    ]:
        """Partition slice requests into active, expandable, and unknown symbols."""

        resolver = getattr(state.program_graph, "resolve_symbol", None)
        repository_symbols = getattr(
            getattr(state, "repository_index", None), "symbols", {}
        )
        active: list[str] = []
        expandable: list[str] = []
        unknown: list[str] = []
        for symbol in symbols:
            leaf = symbol.rsplit(".", 1)[-1]
            if callable(resolver) and (resolver(symbol) or resolver(leaf)):
                active.append(symbol)
            elif repository_symbols.get(symbol) or repository_symbols.get(leaf):
                expandable.append(symbol)
            else:
                unknown.append(symbol)
        return tuple(active), tuple(expandable), tuple(unknown)

    def _invoke(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor, *, mode: str) -> GeneratorRevision:
        context = build_repair_context(state, mode=mode)
        evidence_fingerprint = content_hash({
            "issue": context.issue,
            "working_diff": context.working_diff,
            "failed_checks": context.failed_checks,
            "counterexamples": context.counterexamples,
            "divergences": context.first_trace_divergences,
            "cuts": context.causal_repair_cuts,
            "causal_cut_candidates": context.causal_cut_candidates,
            "source_snippets": context.relevant_source_snippets,
            "active_slice_files": context.active_program_slice.get("files", ()),
            "active_slice_symbols": context.active_program_slice.get("symbols", ()),
        })
        if (
            mode != "INITIAL"
            and conversation.last_evidence_fingerprint == evidence_fingerprint
        ):
            return GeneratorRevision(
                revision_id=stable_id(
                    "generator-no-new-evidence", conversation.conversation_id,
                    evidence_fingerprint,
                ),
                mechanism="causal_slice_rewrite",
                edits=(), summary="no new repair evidence",
                context_requests=(), requested_public_checks=(),
                tool_turns=0, status="NO_NEW_REPAIR_EVIDENCE",
            )
        if conversation.revision_count >= self.max_revisions:
            return GeneratorRevision(
                revision_id=stable_id(
                    "generator-revision-budget", conversation.conversation_id,
                    conversation.revision_count,
                ),
                mechanism="causal_slice_rewrite", edits=(),
                summary="revision budget exhausted", context_requests=(),
                requested_public_checks=(), tool_turns=0,
                status="REVISION_BUDGET_EXHAUSTED",
            )
        conversation.last_evidence_fingerprint = evidence_fingerprint
        conversation.revision_count += 1
        conversation.current_working_diff = context.working_diff
        conversation.messages.append({"role": "user", "content": json.dumps(context.to_dict())})
        mechanism = "initial_issue_repair" if mode == "INITIAL" else "causal_slice_rewrite"
        requested_checks: list[str] = []
        summary = ""
        turns = 0
        invalid_synthesis_calls = 0
        invalid_feedback = ""
        final_correction_used = False
        synthesis_edit_only = False
        evidence_anchor = json.dumps({
            "failure_signature": context.failure_signature,
            "first_project_frame": context.first_project_frame,
            "causal_cut_candidates": context.causal_cut_candidates[:3],
            "exact_source_snippets": self._exact_source_anchor(context),
            "baseline_stderr": (
                str((context.baseline_output or {}).get("stderr", ""))[-2000:]
            ),
        }, sort_keys=True)
        while turns < self.max_tool_turns:
            turns += 1
            schemas = _tool_schema()
            synthesis_turn = (
                turns == self.max_tool_turns
                or (
                    self.max_tool_turns >= 6
                    and turns >= self.max_tool_turns - 1
                )
                or (
                    4 <= self.max_tool_turns < 6
                    and turns == self.max_tool_turns - 1
                )
            )
            if synthesis_turn:
                conversation.messages.append({
                    "role": "system",
                    "content": (
                        (
                            "This is the final tool turn for this revision. "
                            if turns == self.max_tool_turns
                            else "The remaining turns are reserved for synthesis and correction. "
                        )
                        + "Stop "
                        "browsing. Apply an evidence-constrained repair, finish an "
                        "already staged revision, request an exact missing slice, or "
                        "declare_blocker. If a required symbol is outside the active "
                        "slice, use request_program_slice with exact symbols. "
                        "Do not guess and do not call search/read/check tools. "
                        "Keep the proposed edit tied to this execution anchor: "
                        + evidence_anchor
                    ),
                })
                final_tools = _FINAL_TURN_TOOL_NAMES
                if synthesis_edit_only:
                    final_tools = final_tools - {"request_program_slice"}
                schemas = _tool_schema(final_tools)
            available_names = frozenset(
                schema["function"]["name"] for schema in schemas
            )
            try:
                request_messages = self._request_messages(conversation)
                constrained_call = getattr(
                    self.transport, "call_with_tool_choice", None
                )
                if synthesis_turn and callable(constrained_call):
                    # Require a decision among the advertised synthesis tools;
                    # never force a particular tool such as apply_edits.
                    message = constrained_call(
                        request_messages, schemas, "required"
                    )
                else:
                    message = self.transport(request_messages, schemas)
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
                if turns < self.max_tool_turns and (
                    synthesis_turn or self.max_tool_turns >= 6
                ):
                    invalid_synthesis_calls += 1
                    conversation.messages.append({
                        "role": "user",
                        "content": (
                            "Analysis text without a tool call does not advance "
                            "this revision. On the next response call one advertised "
                            "repository tool. Before the final two turns you may use "
                            "search_code/read_file/inspect_symbol; in the final two "
                            "turns use only apply_edits, finish_revision, "
                            "request_program_slice, or declare_blocker. Do not repeat "
                            "the analysis. Use this execution and exact-source anchor: "
                            + evidence_anchor
                        ),
                    })
                    continue
                break
            finished = False
            invalid_synthesis_turn = False
            for call in calls:
                function = call.get("function", {})
                name = str(function.get("name", ""))
                arguments: dict[str, Any] = {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if name not in available_names:
                        if synthesis_turn:
                            invalid_synthesis_calls += 1
                            invalid_synthesis_turn = True
                        output = {
                            "error": "INVALID_TOOL",
                            "requested_tool": name,
                            "allowed_tools": sorted(available_names),
                            "instruction": "use one of the registered repository tools",
                        }
                    elif name == "apply_edits":
                        mechanism = str(arguments.pop("mechanism", mechanism))
                        edits = tuple(ProposedEdit(**item) for item in arguments["edits"])
                        output = tools.apply_edits(edits)
                    elif name == "finish_revision":
                        summary = str(arguments["summary"])
                        output = tools.finish_revision(summary)
                        finished = True
                    elif name == "declare_blocker":
                        output = tools.declare_blocker(**arguments)
                        summary = str(arguments["reason"])
                        finished = True
                    elif name == "run_public_check":
                        requested_checks.append(str(arguments["check_id"]))
                        output = tools.run_public_check(**arguments)
                    elif name == "request_program_slice":
                        requested_symbols = tuple(map(str, arguments["symbols"]))
                        active, expandable, unknown = self._requested_symbol_status(
                            state, requested_symbols
                        )
                        if not expandable:
                            invalid_synthesis_calls += 1
                            invalid_synthesis_turn = True
                            error = (
                                "SYMBOL_NOT_FOUND" if unknown
                                else "CONTEXT_ALREADY_ACTIVE"
                            )
                            invalid_feedback = error + ": " + (
                                "unknown symbols cannot expand the repository slice"
                                if unknown
                                else "the requested symbols are already precise in "
                                "the active slice"
                            )
                            output = {
                                "error": error,
                                "active_symbols": list(active),
                                "unknown_symbols": list(unknown),
                                "instruction": (
                                    "Use the exact source snippets in the execution "
                                    "anchor to apply a minimal edit. Request only a "
                                    "real repository symbol outside the active slice, "
                                    "or declare a concrete blocker."
                                ),
                            }
                        else:
                            output = tools.request_program_slice(
                                symbols=expandable,
                                relation_kinds=arguments["relation_kinds"],
                            )
                            if active or unknown:
                                output["ignored_active_symbols"] = list(active)
                                output["ignored_unknown_symbols"] = list(unknown)
                            summary = "requested targeted program context"
                            finished = True
                    else:
                        output = getattr(tools, name)(**arguments)
                    if name == "read_file" and "path" in arguments:
                        conversation.inspected_files.add(str(arguments["path"]))
                        conversation.inspected_line_ranges.add(
                            f"{arguments['path']}:{arguments.get('start_line')}:{arguments.get('end_line')}"
                        )
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
                    if synthesis_turn and name == "apply_edits":
                        invalid_synthesis_calls += 1
                        invalid_synthesis_turn = True
                        invalid_feedback = output["error"]
                conversation.messages.append({
                    "role": "tool", "tool_call_id": call.get("id"),
                    "content": json.dumps(output, sort_keys=True),
                })
            if finished:
                break
            if invalid_synthesis_turn and (
                turns < self.max_tool_turns
                or not final_correction_used
            ):
                synthesis_edit_only = True
                if turns == self.max_tool_turns:
                    # A model response outside the advertised final schema is
                    # not useful work. Reuse the final budget slot once for a
                    # correction, while keeping the reported tool-turn budget
                    # bounded at max_tool_turns.
                    final_correction_used = True
                    turns -= 1
                conversation.messages.append({
                    "role": "user",
                    "content": (
                            "Your previous tool call was unavailable because browsing "
                            "is closed or returned CONTEXT_ALREADY_ACTIVE. Use the source "
                            "already returned. "
                            "On the next and final response, call exactly one advertised "
                            "tool: apply_edits, finish_revision, request_program_slice, "
                            "or declare_blocker. Do not call any search, read, inspect, "
                            "reference, caller, or public-check tool. The edit must "
                            "directly address this execution anchor. If the feedback is "
                            "CONTEXT_ALREADY_ACTIVE or the anchor contains exact source "
                            "snippets, do not request the same slice again: choose "
                            "apply_edits or declare_blocker. Tool feedback: "
                            + (invalid_feedback or "invalid synthesis tool call")
                        + ". Execution anchor: " + evidence_anchor
                    ),
                })
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
            status=(
                "PROPOSED" if tools.staged_edits
                else "DECLARED_BLOCKER" if tools.blocker
                else "GENERATOR_BROWSE_LOOP" if invalid_synthesis_calls
                else "CONTEXT_ONLY"
            ),
        )
        return revision

    def generate_initial_patch(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor) -> GeneratorRevision:
        return self._invoke(state, conversation, tools, mode="INITIAL")

    def repair_from_counterexamples(self, state, conversation: GeneratorConversation, packets, tools: RepairToolExecutor) -> GeneratorRevision:
        conversation.delivered_counterexamples.update(item.counterexample_id for item in packets)
        return self._invoke(state, conversation, tools, mode="COUNTEREXAMPLE_REPAIR")

    def root_recovery(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor) -> GeneratorRevision:
        return self._invoke(state, conversation, tools, mode="ROOT_RECOVERY")

    def generate_target_reproduction(
        self,
        *,
        issue: str,
        public_discussion: str = "",
        source_context: tuple[dict[str, Any], ...],
        project_runner: str,
    ) -> dict[str, str] | None:
        """Request at most one public-API observation, without proposing edits."""

        schema = [{
            "type": "function",
            "function": {
                "name": "submit_reproduction",
                "description": (
                    "Submit public-API observation setup and one executable "
                    "post-fix relation. ReachPatch appends the only assertion so "
                    "the relation, rather than the known broken behavior, is the oracle"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "setup_source": {"type": "string"},
                        "observation_expression": {
                            "type": "string",
                            "description": (
                                "Python expression that is true exactly when the "
                                "issue's expected public behavior is satisfied"
                            ),
                        },
                        "expected_observation": {"type": "string"},
                    },
                    "required": [
                        "filename", "setup_source", "observation_expression",
                        "expected_observation",
                    ],
                },
            },
        }]
        runner_guidance = (
            "For Django, the runner supplies DJANGO_SETTINGS_MODULE; "
            "call django.setup() before defining any models, and give every "
            "standalone models.Model subclass a Meta class with an explicit "
            "app_label such as 'reachpatch_reproduction'. To inspect "
            "model system checks, call Model.check() on the model class, "
            "never field_descriptor.check() on Model.field. Do not treat "
            "missing app_label, AppRegistryNotReady, or DeferredAttribute "
            "AttributeError as the issue behavior; those indicate an invalid "
            "standalone reproduction. "
            if project_runner.lower() == "django" else ""
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Recover one executable failing target from public evidence. "
                    "Use only the primary issue, public discussion, and supplied "
                    "public source snippets. The primary_issue is the normative "
                    "authority. public_discussion may clarify a failure or propose "
                    "implementations, but it must not add an assertion that conflicts "
                    "with the primary issue. If comments disagree, retain only their "
                    "shared end-to-end behavior and the primary issue's contract. The "
                    "script runs from the repository root and must assert every "
                    "explicit behavioral relation and qualifier in the issue, not "
                    "only its simplest example. Keep those observations in one "
                    "bounded script so a partial patch can expose the next failing "
                    "assertion. Cover ordering, metadata, negative, boundary, and "
                    "symmetry relations whenever the issue explicitly names them. "
                    "Before submitting, audit every conjunct against the supplied "
                    "public text. Do not add a negative case, rejection rule, timing "
                    "constraint, or compatibility requirement unless the issue "
                    "explicitly states it. Do not combine proposal alternatives or "
                    "superseded discussion into mutually exclusive requirements. In "
                    "particular, when evidence says a deferred representation must "
                    "be preserved for serialization but evaluated by a consumer, do "
                    "not also require the stored representation itself to be eagerly "
                    "replaced; distinguish the producer, stored state, and consumer. "
                    "When discussion contains competing implementation proposals, "
                    "assert only the end-to-end public behavior shared by the viable "
                    "proposals. Do not assert the type or timing of an intermediate "
                    "value after __init__, the raw contents returned by deconstruct, "
                    "or eager-versus-lazy internal state unless the main issue defines "
                    "that detail as a public contract. Observe serialization through "
                    "the project's public serializer or writer output, not by guessing "
                    "an intermediate representation. "
                    "Return setup_source that only performs calls and records their "
                    "outputs, exceptions, warnings, or state. It must not contain "
                    "assert, raise, sys.exit, pytest expectation helpers, or an "
                    "oracle of its own. Return observation_expression as a Python "
                    "expression over those recorded values that is TRUE for the "
                    "behavior requested by the issue and FALSE for the described "
                    "baseline bug. Never encode or confirm the known incorrect "
                    "output as the expected relation. Every named boolean "
                    "observation recorded by setup_source must occur in the final "
                    "expression, including migration, serialization, metadata, and "
                    "backward-compatibility observations. expected_observation must "
                    "state the concrete expected relation in human-readable terms; "
                    "it cannot be True, success, or another generic label. Use a "
                    "descriptive test_*.py filename, never __init__.py or conftest.py. "
                    "ReachPatch will append the assertion itself. "
                    + runner_guidance
                    + "Do not read or "
                    "name hidden tests, test_patch, gold patches, harness fields, or "
                    "official outcomes. Do not edit project files. If the evidence "
                    "cannot support a concrete observation, return no tool call."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "primary_issue": issue,
                    "public_discussion": public_discussion,
                    "project_runner": project_runner,
                    "public_source_context": source_context,
                }, sort_keys=True),
            },
        ]
        rejection = ""
        for attempt in range(2):
            try:
                message = self.transport(messages, schema)
            except Exception as exc:
                raise GeneratorBlockedExternal(
                    "deepseek_target_reproduction", exc,
                ) from exc
            proposal, rejection = _compile_reproduction_message(
                message, primary_issue=issue, project_runner=project_runner,
            )
            if proposal is not None:
                return proposal
            if attempt == 0:
                messages.extend((
                    {
                        "role": "assistant",
                        "content": str(
                            message.get("content")
                            or "The previous response did not yield a valid reproduction."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "The candidate was mechanically rejected: "
                            f"{rejection}. Correct it once using the same public "
                            "evidence. Call submit_reproduction, keep all assertions "
                            "out of setup_source, and put the expected relation only "
                            "in observation_expression."
                        ),
                    },
                ))
        return None


def generate_initial_patch(state, conversation, agent: PersistentDeepSeekAgent, tools: RepairToolExecutor) -> GeneratorRevision:
    return agent.generate_initial_patch(state, conversation, tools)


def repair_from_counterexamples(state, conversation, packets, agent: PersistentDeepSeekAgent, tools: RepairToolExecutor) -> GeneratorRevision:
    return agent.repair_from_counterexamples(state, conversation, packets, tools)
