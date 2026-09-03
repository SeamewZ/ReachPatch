from __future__ import annotations

"""Evidence-constrained semantic requirement compiler."""

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Sequence

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.evidence import ObservationContract
from reachpatch.models.execution import EvidenceSpan, GoalContract


class ClaimRole(StrEnum):
    TARGET = "TARGET"
    PRESERVATION = "PRESERVATION"
    EXCEPTION = "EXCEPTION"
    COMPATIBILITY = "COMPATIBILITY"
    ILLUSTRATION = "ILLUSTRATION"
    CONTEXT = "CONTEXT"


# The production controller consumes a flat list of GoalContract records.
# Keep this tool schema separate from the historical claim compiler so a
# model cannot smuggle RequirementGraph fields into the execution contract.
_GOAL_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_goal_contracts",
        "description": "Submit minimal evidence-grounded executable goals.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["goals"],
            "properties": {
                "goals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "operation", "target_symbols", "comparator",
                            "expected", "evidence_spans", "authority",
                            "hard", "unresolved_reason",
                        ],
                        "properties": {
                            "operation": {"type": "string"},
                            "target_symbols": {"type": "array", "items": {"type": "string"}},
                            "comparator": {"type": "string", "enum": ["EXIT_ZERO", "EQUALS", "NOT_EQUALS", "RAISES", "NOT_RAISES", "TYPE_IS", "CONTAINS", "ORDER_EQUALS", "LENGTH_EQUALS", "HAS_ATTR", "STATE_DELTA_EQUALS", "RELATION_HOLDS"]},
                            "expected": {},
                            "evidence_spans": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["start", "end", "quote"], "properties": {"start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 0}, "quote": {"type": "string"}}}},
                            "authority": {"type": "string", "enum": ["A", "B", "C", "PROVISIONAL"]},
                            "hard": {"type": "boolean"},
                            "unresolved_reason": {"type": ["string", "null"]},
                        },
                    },
                },
            },
        },
    },
}


def _goal_from_claim(claim: CompiledRequirementClaim) -> GoalContract | None:
    # Keep an unresolved marker as an ordinary goal record. It is not
    # executable and can never satisfy Reach, but target recovery can now
    # report the exact missing evidence instead of seeing an empty list.
    if claim.role not in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY} and claim.operation != "UNRESOLVED_TARGET":
        return None
    expected = dict(claim.expected_observation or {})
    comparator = str(expected.get("kind", "RELATION_HOLDS")).upper()
    allowed = {"EXIT_ZERO", "EQUALS", "NOT_EQUALS", "RAISES", "NOT_RAISES", "TYPE_IS", "CONTAINS", "ORDER_EQUALS", "LENGTH_EQUALS", "HAS_ATTR", "STATE_DELTA_EQUALS", "RELATION_HOLDS"}
    if comparator not in allowed:
        comparator = "RELATION_HOLDS"
    authority = "B" if claim.evidence_spans else "PROVISIONAL"
    return GoalContract(
        goal_id=claim.claim_id,
        operation=claim.operation or "UNRESOLVED_TARGET",
        target_symbols=tuple(claim.target_symbols),
        comparator=comparator,
        expected=expected.get("expected", expected),
        evidence_spans=claim.evidence_spans,
        authority=authority,
        hard=(claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY}
              and authority in {"A", "B", "C"}
              and claim.operation != "UNRESOLVED_TARGET"),
        unresolved_reason=("TARGET_RECOVERY_REQUIRED" if claim.operation == "UNRESOLVED_TARGET"
                           else None),
    )


def compile_goal_contracts(
    issue_text: str,
    public_evidence: Sequence[Any],
    source_hints: Sequence[Any],
    transport: Any | None,
    run_root: Path,
) -> tuple[GoalContract, ...]:
    """Compile a flat, evidence-grounded goal list."""
    goals: list[GoalContract] = []
    direct_result = _compile_goals_with_tool(
        issue_text, transport, run_root, public_evidence, source_hints,
    )
    if direct_result is not None:
        goals.extend(direct_result)
    else:
        compilation = compile_requirement_contract(issue_text, public_evidence, source_hints, None, run_root)
        goals.extend(goal for claim in compilation.claims if (goal := _goal_from_claim(claim)) is not None)
    # Issue witnesses are executable Authority-B evidence even when the LLM
    # contract call failed semantic validation.  Recover a minimal goal from
    # the witness plus its immediately preceding normative prose; do not use
    # traceback/source lines or illustrative examples as the expected clause.
    issue_records = (
        tuple(getattr(public_evidence, "records", ()))
        if not isinstance(public_evidence, (tuple, list))
        else tuple(item for item in public_evidence if getattr(item, "source", "") == "issue")
    )
    for record in issue_records:
        if getattr(record, "source", "") != "issue":
            continue
        for witness in getattr(record, "metadata", {}).get("issue_witnesses", ()):
            if str(witness.get("authority", "PROVISIONAL")).upper() not in {"A", "B", "C"}:
                continue
            operation = str(witness.get("operation", "")).strip()
            if not operation:
                continue
            if any(item.operation == operation and item.comparator in {"EXIT_ZERO", "NOT_RAISES", "EQUALS", "RAISES"} for item in goals):
                continue
            expression = str(witness.get("target_expression", ""))
            position = issue_text.find(expression) if expression else -1
            prefix = issue_text[max(0, position - 600):position] if position >= 0 else ""
            lines = [(line_offset, value.strip()) for line_offset, value in _iter_lines_with_offsets(prefix)]
            normative = next((value for _, value in reversed(lines) if _NORMATIVE.search(value) and not _evidence_is_source_or_traceback(value) and not _EXAMPLE.search(value)), "")
            spans: tuple[EvidenceSpan, ...] = ()
            if normative:
                absolute = issue_text.rfind(normative, 0, position if position >= 0 else len(issue_text))
                if absolute >= 0:
                    spans = (EvidenceSpan(absolute, absolute + len(normative), normative),)
            expected_payload = witness.get("expected", {"exit_code": 0})
            if isinstance(expected_payload, dict) and (expected_payload.get("exception_type") or expected_payload.get("type")):
                comparator = "RAISES"
            elif isinstance(expected_payload, dict) and "exit_code" in expected_payload:
                comparator = "EXIT_ZERO"
            elif isinstance(expected_payload, dict) and "value" in expected_payload:
                comparator = "EQUALS"
                expected_payload = expected_payload.get("value")
            else:
                comparator = "RELATION_HOLDS"
            goals.append(GoalContract(
                goal_id=stable_id("goal-issue-witness", operation, expression),
                operation=operation,
                target_symbols=(operation,), comparator=comparator,
                expected=expected_payload, evidence_spans=spans,
                authority=str(witness.get("authority", "B")).upper(),
                hard=bool(spans), unresolved_reason=None if spans else "TARGET_RECOVERY_REQUIRED",
            ))
    # Public checks are stronger than an inferred prose claim and provide a
    # stable target identity for the execution loop.
    for check in public_evidence if isinstance(public_evidence, (tuple, list)) else getattr(public_evidence, "checks", ()):
        if str(getattr(check, "role", "")).upper() != "TARGET":
            continue
        check_symbols = {
            str(item).casefold().rsplit(".", 1)[-1]
            for item in tuple(getattr(check, "target_symbols", ()))
            + tuple(getattr(check, "symbol_references", ()))
        }
        # A public assertion is evidence for an issue-grounded goal when it
        # names the same operation. Do not create a second hard goal merely
        # because the assertion has its own check id; that would leave a
        # phantom hard goal with no executable target coverage.
        if check_symbols and any(
            check_symbols.intersection(
                str(symbol).casefold().rsplit(".", 1)[-1]
                for symbol in item.target_symbols
            )
            for item in goals
            if item.hard and item.operation != "UNRESOLVED_TARGET"
        ):
            continue
        goal_id = str(getattr(check, "goal_id", None) or stable_id("goal-public-check", getattr(check, "check_id", "")))
        if any(item.goal_id == goal_id for item in goals):
            continue
        expected = getattr(check, "expected", None)
        if isinstance(expected, ObservationContract):
            comparator = expected.normalized_comparator; value = expected.expected
        else:
            comparator = "EXIT_ZERO"; value = {"exit_code": 0}
        goals.append(GoalContract(goal_id, goal_id, tuple(getattr(check, "target_symbols", ()) or getattr(check, "symbol_references", ())), comparator, value, (), str(getattr(check, "authority", "A")), True, None))
    return tuple(dict((goal.goal_id, goal) for goal in goals).values())


def _compile_goals_with_tool(
    issue_text: str,
    transport: Any | None,
    run_root: Path,
    public_evidence: Sequence[Any] | Any = (),
    source_hints: Sequence[Any] = (),
) -> tuple[GoalContract, ...] | None:
    """Call submit_goal_contracts and deterministically validate it."""
    if transport is None:
        return None
    messages = [
        {"role": "system", "content": "Compile minimal behavior goals from exact issue evidence. Code, traceback, Actual and examples are never hard expected behavior."},
        {"role": "user", "content": issue_text},
    ]
    errors: list[str] = []
    parsed_once = False
    raw_args: dict[str, Any] = {}
    # Keep the exact payload returned by the model separate from the parsed
    # object.  A malformed JSON response is still evidence that must be
    # auditable; replacing it with ``{}`` loses the model's original attempt.
    raw_argument_payload: Any = {}
    attempts: list[dict[str, Any]] = []
    invalid_json_attempts = 0
    public_checks = (
        tuple(getattr(public_evidence, "checks", ()))
        if not isinstance(public_evidence, (tuple, list))
        else tuple(public_evidence)
    )
    externally_supported = {
        str(symbol).rsplit(".", 1)[-1].casefold()
        for check in public_checks
        for symbol in (
            *tuple(getattr(check, "target_symbols", ())),
            *tuple(getattr(check, "symbol_references", ())),
        )
        if str(symbol).strip()
    }
    externally_supported.update(
        str(getattr(hint, "symbol", "")).rsplit(".", 1)[-1].casefold()
        for hint in source_hints if str(getattr(hint, "symbol", "")).strip()
    )
    for attempt in range(3):
        if attempt and errors:
            # Tool feedback must follow the assistant tool call it answers.
            # Sending a bare ``role=tool`` message makes DeepSeek reject the
            # conversation with a missing ``tool_call_id`` and destroys the
            # semantic retry path.
            previous = locals().get("previous_response")
            previous_calls = previous.get("tool_calls", ()) if isinstance(previous, dict) else ()
            if previous_calls:
                messages.append({
                    "role": "assistant",
                    "content": previous.get("content"),
                    "tool_calls": previous_calls,
                })
                call_id = str(previous_calls[0].get("id", "submit_goal_contracts"))
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"validation_errors": errors, "instruction": "repair every error and call the same tool again"}),
                })
        try:
            response = transport.complete(messages, tools=(_GOAL_TOOL_SCHEMA,), max_tokens=8000, timeout_seconds=120.0, tool_choice={"type": "function", "function": {"name": "submit_goal_contracts"}})
            previous_response = response
            calls = response.get("tool_calls", ()) if isinstance(response, dict) else ()
            if not calls:
                raise ValueError("missing submit_goal_contracts tool call")
            raw_argument_payload = calls[0].get("function", {}).get("arguments", {})
            raw_args = json.loads(raw_argument_payload) if isinstance(raw_argument_payload, str) else dict(raw_argument_payload)
            if not isinstance(raw_args, dict):
                raise ValueError("tool arguments must be an object")
            parsed_once = True
        except Exception as exc:
            errors = [f"tool JSON/schema error: {type(exc).__name__}: {exc}"]
            attempts.append({"arguments": raw_argument_payload, "validation_errors": tuple(errors)})
            invalid_json_attempts += 1
            # Malformed tool arguments get one same-session correction.  A
            # second malformed response is transport/protocol failure and may
            # use the deterministic compiler fallback; parsed semantic errors
            # below still receive the full two retries.
            if invalid_json_attempts >= 2:
                break
            continue
        candidate: list[GoalContract] = []
        errors = []
        values = raw_args.get("goals")
        if not isinstance(values, list):
            errors.append("goals must be an array")
            values = []
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                errors.append(f"goals[{index}] must be an object")
                continue
            required = ("operation", "target_symbols", "comparator", "expected", "evidence_spans", "authority", "hard", "unresolved_reason")
            missing = [key for key in required if key not in item]
            if missing:
                errors.append(f"goals[{index}] missing required fields: {', '.join(missing)}")
                continue
            spans: list[EvidenceSpan] = []
            for span_index, raw_span in enumerate(item.get("evidence_spans", ()) or ()):
                span = _span_from_raw(raw_span, issue_text)
                if span is None:
                    errors.append(f"goals[{index}].evidence_spans[{span_index}] quote/offset mismatch")
                else:
                    spans.append(span)
            operation = str(item.get("operation", "")).strip()
            symbols = tuple(str(value) for value in item.get("target_symbols", ()) if str(value).strip())
            comparator = str(item.get("comparator", "RELATION_HOLDS")).upper()
            authority = str(item.get("authority", "PROVISIONAL")).upper()
            requested_hard = bool(item.get("hard", False))
            # GoalContract intentionally has no free-form claim role: the
            # compiler receives only a minimal operation/oracle contract.
            # Therefore ``hard`` is governed by the model's explicit request
            # and deterministic evidence checks below.  Never reference the
            # old claim-role variable here (it is not part of this schema).
            hard = requested_hard
            evidence_text = " ".join(span.quote for span in spans)
            normative = bool(_NORMATIVE.search(evidence_text)) or bool(re.search(r"(?im)^\s*(?:expected|desired)\s*:", evidence_text))
            if hard and not spans:
                errors.append(f"goals[{index}] hard target has no evidence")
            if hard and (not normative or _EXAMPLE.search(evidence_text) or _TRACE.search(evidence_text) or _evidence_is_source_or_traceback(evidence_text) or re.search(r"(?i)\bactual(?: behavior)?\b", evidence_text)):
                errors.append(f"goals[{index}] evidence is not an explicit expected behavior")
            if comparator not in ObservationContract._COMPARATORS:
                errors.append(f"goals[{index}] unsupported comparator {comparator}")
            if hard and authority not in {"A", "B", "C"}:
                errors.append(f"goals[{index}] hard target requires Authority A/B/C")
            if hard and not symbols:
                errors.append(f"goals[{index}] hard target requires target_symbols")
            if hard and _EXCEPTION_SYMBOL.search(operation.rsplit(".", 1)[-1]):
                errors.append(f"goals[{index}] exception class cannot be the operation")
            unresolved = item.get("unresolved_reason")
            if unresolved is not None:
                unresolved = str(unresolved)
            if operation in {"", "return", "raise", "return ..."} and hard:
                errors.append(f"goals[{index}] operation is not a symbol-level target")
            if hard and symbols:
                terminals = {symbol.rsplit(".", 1)[-1].casefold() for symbol in symbols}
                if not all(
                    terminal in externally_supported
                    or re.search(rf"\b{re.escape(terminal)}\b", evidence_text, re.I)
                    for terminal in terminals
                ):
                    errors.append(f"goals[{index}] target symbol is not supported by its evidence span")
                operation_terminal = operation.rsplit(".", 1)[-1].casefold()
                if (
                    operation_terminal
                    and operation_terminal not in terminals
                    and operation_terminal not in externally_supported
                    and not re.search(
                        rf"\b{re.escape(operation_terminal)}\b",
                        evidence_text, re.I,
                    )
                ):
                    errors.append(f"goals[{index}] operation is not supported by its evidence span")
            if operation == "UNRESOLVED_TARGET":
                if hard:
                    errors.append(
                        f"goals[{index}] unresolved target cannot be hard"
                    )
                if symbols:
                    errors.append(
                        f"goals[{index}] unresolved target cannot name symbols"
                    )
                if not unresolved:
                    errors.append(
                        f"goals[{index}] unresolved target requires unresolved_reason"
                    )
            candidate.append(GoalContract(goal_id=stable_id("goal-contract", index, operation, symbols, comparator, item.get("expected")), operation=operation or "UNRESOLVED_TARGET", target_symbols=symbols, comparator=comparator, expected=item.get("expected"), evidence_spans=tuple(spans), authority=authority, hard=hard and not errors, unresolved_reason=unresolved))
        attempts.append({"arguments": raw_args, "validation_errors": tuple(errors)})
        if not errors and candidate:
            _write_goal_artifact(run_root, issue_text, raw_args, attempts, candidate, ())
            return tuple(dict((goal.goal_id, goal) for goal in candidate).values())
        if not errors and not candidate:
            errors.append("no goals submitted")
    if parsed_once:
        unresolved = GoalContract(goal_id=stable_id("unresolved-goal", issue_text), operation="UNRESOLVED_TARGET", target_symbols=(), comparator="RELATION_HOLDS", expected=None, evidence_spans=(), authority="PROVISIONAL", hard=False, unresolved_reason="TARGET_RECOVERY_REQUIRED")
        _write_goal_artifact(run_root, issue_text, raw_args, attempts, (unresolved,), tuple(errors))
        return (unresolved,)
    return None


def _write_goal_artifact(run_root: Path, issue_text: str, raw_args: Any, attempts: list[dict[str, Any]], goals: Sequence[GoalContract], errors: Sequence[str]) -> None:
    root = Path(run_root); root.mkdir(parents=True, exist_ok=True)
    payload = {
        "issue": issue_text, "raw_tool_arguments": raw_args,
        "tool_attempts": attempts, "validation_errors": tuple(errors),
        "claims": tuple(goals), "goals": tuple(goals),
        "downgraded_claims": tuple(
            item for item in goals if not getattr(item, "hard", False)
        ),
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True, default=lambda value: value.to_dict() if hasattr(value, "to_dict") else str(value)) + "\n"
    (root / "goal_contracts.json").write_text(rendered, encoding="utf-8")
    # Keep the compiler artifact name stable across the old claim compiler and
    # the flat GoalContract production path.  Both files contain the exact raw
    # tool arguments and validation attempts for auditability.
    (root / "requirement_compilation.json").write_text(rendered, encoding="utf-8")


@dataclass(frozen=True, slots=True)
class CompiledRequirementClaim(SerializableRecord):
    claim_id: str
    role: ClaimRole
    quantifier: str
    variables: tuple[dict, ...]
    domain_constraints: tuple[str, ...]
    preconditions: tuple[str, ...]
    operation: str
    expected_observation: dict
    exception_contract: dict | None
    preservation_contract: dict | None
    evidence_spans: tuple[EvidenceSpan, ...]
    witness_ids: tuple[str, ...]
    target_symbols: tuple[str, ...]
    unresolved_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RequirementCompilation(SerializableRecord):
    claims: tuple[CompiledRequirementClaim, ...]
    witnesses: tuple[dict, ...] = ()
    ambiguities: tuple[str, ...] = ()
    raw_tool_arguments: dict[str, Any] = field(default_factory=dict)
    rejected_claims: tuple[dict[str, Any], ...] = ()
    fallback_used: bool = False
    tool_attempts: tuple[dict[str, Any], ...] = ()


_NORMATIVE = re.compile(r"\b(must|should|shall|needs? to|expected|allow|support|return|raise|no longer|cannot|must not|should not|preserve|remain|continue)\b", re.I)
_EXAMPLE = re.compile(r"\b(for example|e\.g\.|such as|illustrat(?:ion|e))\b", re.I)
_TRACE = re.compile(r"^\s*(?:traceback|file \"|\^+)", re.I)
_SYMBOL = re.compile(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`")
_CALL = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")
_BARE_SYMBOL = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_]\w*)*)\b")
_NORMATIVE_SUBJECT = re.compile(
    r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s+"
    r"(?:must|should|shall|needs?\s+to|is\s+expected\s+to)\b",
    re.IGNORECASE,
)
_GENERIC_SYMBOLS = {
    "the", "this", "please", "public", "api",
    "implementation", "behavior", "function", "method",
    "operation", "system", "code", "input",
    "output", "call", "thing", "result", "following", "below", "above",
    "list", "lists", "array", "arrays",
}
_EXCEPTION_SYMBOL = re.compile(r"(?:Error|Exception|Warning|Interrupt|Exit|Failure|Fault)$", re.I)

_SOURCE_LINE = re.compile(
    r"(?im)^\s*(?:return|raise|yield|assert|from\s+.+\s+import|import\s+|def\s+|class\s+|File \"|Traceback\b)"
)


def _iter_lines_with_offsets(text: str) -> tuple[tuple[int, str], ...]:
    """Split LF and CRLF issue text while retaining local offsets."""
    result: list[tuple[int, str]] = []
    offset = 0
    for raw in text.splitlines(keepends=True):
        result.append((offset, raw.rstrip("\r\n")))
        offset += len(raw)
    if text and (not result or offset < len(text)):
        result.append((offset, text[offset:]))
    return tuple(result)


def _evidence_is_source_or_traceback(text: str) -> bool:
    """Reject code/traceback lines as standalone GoalContract evidence."""
    return bool(_SOURCE_LINE.search(text))


def _fallback_symbol(normative_line: str, witness_symbols: Sequence[str], source_hints: Sequence[Any]) -> str:
    """Find a locally evidenced API symbol, excluding exception oracles."""
    def usable(value: str | None) -> bool:
        if not value:
            return False
        terminal = value.rsplit(".", 1)[-1]
        return terminal.casefold() not in _GENERIC_SYMBOLS and not _EXCEPTION_SYMBOL.search(terminal)

    for match in _SYMBOL.finditer(normative_line):
        value = match.group(1).strip()
        if usable(value):
            return value
    for pattern in (
        r"\bcall\s+to\s+(`?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*`?)",
        r"\b(?:the\s+)?(`?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*`?)\s+call\b",
        r"\b(?:method|function|class|api)\s+(`?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*`?)",
    ):
        match = re.search(pattern, normative_line, re.I)
        if match:
            value = match.group(1).strip("`")
            if usable(value):
                return value
    # When the normative sentence refers to "the following" and the issue
    # contains exactly one executable witness, bind that witness operation
    # instead of mistaking an incidental noun (for example ``WCS``) for the
    # API under repair.  Multiple witnesses remain unresolved here and are
    # handled independently by their own evidence.
    if len(witness_symbols) == 1 and usable(str(witness_symbols[0])):
        return str(witness_symbols[0])
    for match in _NORMATIVE_SUBJECT.finditer(normative_line):
        value = match.group(1)
        if usable(value):
            return value
    for match in _CALL.finditer(normative_line):
        value = match.group(1)
        if usable(value):
            return value
    for match in _BARE_SYMBOL.finditer(normative_line):
        value = match.group(1)
        if usable(value):
            return value
    for value in witness_symbols:
        if usable(str(value)):
            return str(value)
    for item in source_hints:
        value = str(getattr(item, "symbol", "") or "")
        if usable(value):
            return value
    return "UNRESOLVED_TARGET"


def _span_from_raw(raw: Any, issue_text: str) -> EvidenceSpan | None:
    if not isinstance(raw, dict):
        return None
    try:
        start, end, quote = int(raw["start"]), int(raw["end"]), str(raw["quote"])
    except (KeyError, TypeError, ValueError):
        return None
    if start < 0 or end < start or end > len(issue_text) or issue_text[start:end] != quote:
        return None
    return EvidenceSpan(start, end, quote)


def validate_compiled_claim(claim: CompiledRequirementClaim, issue_text: str) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    if claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY} and not claim.evidence_spans:
        errors.append("normative claim has no evidence span")
    for span in claim.evidence_spans:
        if issue_text[span.start:span.end] != span.quote:
            errors.append("evidence span quote/offset mismatch")
    normative = " ".join(span.quote for span in claim.evidence_spans)
    if claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY} and normative and not _NORMATIVE.search(normative):
        errors.append("target evidence has no normative predicate")
    if claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY} and _evidence_is_source_or_traceback(normative):
        errors.append("source or traceback line cannot define expected behavior")
    if _EXAMPLE.search(normative) and claim.role is ClaimRole.TARGET:
        errors.append("illustrative evidence cannot be a hard target")
    if _TRACE.search(normative):
        errors.append("traceback text cannot define expected behavior")
    if not claim.operation.strip():
        errors.append("claim operation is empty")
    if claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY} and _EXCEPTION_SYMBOL.search(claim.operation.rsplit(".", 1)[-1]):
        errors.append("exception class cannot be the target operation")
    if not isinstance(claim.expected_observation, dict) or not str(claim.expected_observation.get("kind", "")).strip():
        errors.append("expected observation must be structured")
    if claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY} and claim.target_symbols:
        terminals = {symbol.rsplit(".", 1)[-1].casefold() for symbol in claim.target_symbols}
        if not any(re.search(rf"\b{re.escape(terminal)}\b", normative, re.I) for terminal in terminals):
            errors.append("target symbol is not supported by its evidence span")
        operation_terminal = claim.operation.rsplit(".", 1)[-1].casefold()
        if operation_terminal and operation_terminal not in terminals and not re.search(rf"\b{re.escape(operation_terminal)}\b", normative, re.I):
            errors.append("claim operation is not supported by its evidence span")
    return not errors, tuple(dict.fromkeys(errors))


def _claim_from_raw(raw: Any, issue_text: str, index: int) -> tuple[CompiledRequirementClaim | None, tuple[str, ...]]:
    if not isinstance(raw, dict):
        return None, ("claim is not an object",)
    try:
        role = ClaimRole(str(raw.get("role", "CONTEXT")).upper())
    except ValueError:
        role = ClaimRole.CONTEXT
    raw_spans = tuple(raw.get("evidence_spans", ()) or ())
    spans_list: list[EvidenceSpan] = []
    span_errors: list[str] = []
    for item in raw_spans:
        span = _span_from_raw(item, issue_text)
        if span is None:
            # Preserve a deterministic reason for the tool feedback loop; a
            # malformed span must not silently turn into an ungrounded claim.
            span_errors.append("evidence span quote/offset mismatch")
        else:
            spans_list.append(span)
    spans = tuple(spans_list)
    expected = raw.get("expected_observation")
    if not isinstance(expected, dict):
        expected = {"kind": "RELATION_HOLDS", "expected": None}
    claim = CompiledRequirementClaim(
        claim_id=str(raw.get("claim_id") or stable_id("compiled-claim", index, role, raw.get("operation", ""))),
        role=role, quantifier=str(raw.get("quantifier", "CONDITIONAL")),
        variables=tuple(item for item in raw.get("variables", ()) if isinstance(item, dict)),
        domain_constraints=tuple(map(str, raw.get("domain_constraints", ()))),
        preconditions=tuple(map(str, raw.get("preconditions", ()))),
        operation=str(raw.get("operation", "")).strip(), expected_observation=expected,
        exception_contract=raw.get("exception_contract") if isinstance(raw.get("exception_contract"), dict) else None,
        preservation_contract=raw.get("preservation_contract") if isinstance(raw.get("preservation_contract"), dict) else None,
        evidence_spans=spans, witness_ids=tuple(str(item) for item in raw.get("witness_indexes", raw.get("witness_ids", ()))),
        target_symbols=tuple(str(item) for item in raw.get("target_symbols", ())),
        unresolved_terms=tuple(map(str, raw.get("unresolved_terms", ()))),
    )
    valid, errors = validate_compiled_claim(claim, issue_text)
    errors = tuple(dict.fromkeys((*span_errors, *errors)))
    return (claim if valid else None), errors


def _fallback(
    issue_text: str,
    source_hints: Sequence[Any],
    public_records: Sequence[Any] = (),
) -> RequirementCompilation:
    claims: list[CompiledRequirementClaim] = []
    offset = 0
    in_fence = False
    in_reproduction = False
    discussion_at = re.search(
        r"(?im)^\s*(?:public\s+maintainer\s+hints?|discussion|comments?)\s*:\s*$",
        issue_text,
    )
    body_end = discussion_at.start() if discussion_at else len(issue_text)
    issue_witness_records: list[dict[str, Any]] = []
    for record in public_records:
        if getattr(record, "source", None) != "issue":
            continue
        values = (getattr(record, "metadata", {}) or {}).get("issue_witnesses", ())
        if isinstance(values, (list, tuple)):
            issue_witness_records.extend(
                item for item in values
                if isinstance(item, dict) and item.get("witness_id")
            )
    if not issue_witness_records:
        for match in re.finditer(
            r"\b(?:for example|e\.g\.|such as)\s*,?\s*(?P<expr>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?\s*\([^\n]*\))\s*(?P<tail>[^\n]*)",
            issue_text,
            re.IGNORECASE,
        ):
            expr = match.group("expr").strip()
            tail = match.group("tail").strip()
            expected_match = re.search(
                r"\b(?:return|returns?|equals?|equal(?:s)?|be)\s+(.+?)(?:[.!?]|$)",
                tail,
                re.IGNORECASE,
            )
            expected = {"exit_code": 0}
            if expected_match:
                raw = expected_match.group(1).strip().strip(chr(96))
                try:
                    expected = {"value": json.loads(raw)}
                except (json.JSONDecodeError, TypeError):
                    expected = {"value": raw}
            operation = expr.split("(", 1)[0].strip().rsplit(".", 1)[-1]
            issue_witness_records.append({
                "witness_id": stable_id("inline-witness", expr, expected),
                "operation": operation,
                "target_expression": expr,
                "expected": expected,
                "expected_relation": "inline issue witness",
                "authority": "B",
            })
    witness_symbols = [
        str(item.get("operation", "")) for item in issue_witness_records
        if item.get("operation")
    ]
    witness_ids = tuple(
        str(item["witness_id"]) for item in issue_witness_records
        if item.get("witness_id")
    )
    witness_by_operation = {
        str(item.get("operation", "")).casefold(): item
        for item in issue_witness_records
        if item.get("operation")
    }
    # Do not search arbitrary calls across the whole issue.  Traceback frames,
    # examples and code blocks are witnesses/context and must never supply a
    # fallback target symbol.  Calls in a normative line are handled locally
    # below after its evidence span has been established.
    for raw_line in issue_text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        start = offset
        offset += len(raw_line)
        if start >= body_end:
            continue
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        lowered = stripped.casefold()
        expected_label = re.match(r"^(?:expected|desired)\s*:\s*(?P<body>.*)$", stripped, re.I)
        actual_label = re.match(r"^actual(?:\s+[^:]*)?\s*:", stripped, re.I)
        reproduction_label = re.match(r"^steps\s+to\s+reproduce\s*:", stripped, re.I)
        if reproduction_label:
            in_reproduction = True
            continue
        # Expected/Desired starts a normative region again; Actual and
        # traceback/reproduction material can only describe the observed
        # failure and must never become an oracle.
        if expected_label:
            in_reproduction = False
        elif actual_label or lowered.startswith("traceback"):
            in_reproduction = False if actual_label else in_reproduction
        # Indented lines are source/witness material.  In particular a
        # ``return`` statement is not an issue contract merely because the
        # surrounding issue uses normative language elsewhere.
        source_indented = bool(line[:1].isspace() and stripped and not re.match(r"^(?:Expected|Desired|Actual)\s*:", stripped, re.I))
        if (
            not stripped or in_fence or source_indented or _TRACE.search(line)
            or actual_label or lowered.startswith(("actual behavior", "steps to reproduce", "traceback"))
            or (in_reproduction and not expected_label)
            or not _NORMATIVE.search(line)
        ):
            continue
        # Parse labels from their body so ``Expected:`` itself cannot be
        # mistaken for the API operation by the generic symbol fallback.
        source_line = expected_label.group("body") if expected_label else line
        normative_line = re.split(
            r"\b(?:for example|e\.g\.|such as)\b", source_line,
            maxsplit=1, flags=re.I,
        )[0].rstrip()
        if not normative_line or not _NORMATIVE.search(normative_line):
            continue
        # Keep offsets exact even when a label was stripped for parsing. Use
        # the normative prefix before any illustrative tail so an example
        # cannot turn an otherwise hard target into a mixed evidence span.
        span_start = start + max(0, line.find(normative_line))
        span = EvidenceSpan(span_start, span_start + len(normative_line), normative_line)
        symbol = _fallback_symbol(normative_line, witness_symbols, source_hints)
        unresolved_symbol = symbol == "UNRESOLVED_TARGET"
        illustrative = False
        exception_name = None
        exception_match = re.search(
            r"\b(?:must|should|shall|expected\s+to)\s+(?:raise|throw)\s+`?([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`?",
            normative_line, re.I,
        )
        if exception_match:
            exception_name = exception_match.group(1)
        comparator = "RAISES" if exception_name or re.search(r"\b(?:raise|exception)", normative_line, re.I) else (
            "EXIT_ZERO"
            if re.search(r"\b(?:accept|allow|support|succeed|works?)\b", normative_line, re.I)
            else "RELATION_HOLDS"
        )
        match = re.search(r"\b(?:return|returns?|equal|equals?|be)\s+(`[^`]+`|None|True|False|-?\d+(?:\.\d+)?)", normative_line, re.I)
        expected: Any = (
            {"exception_type": exception_name} if exception_name else True
            if comparator == "RAISES" else ({"exit_code": 0} if comparator == "EXIT_ZERO" else None)
        )
        if match:
            raw_expected = match.group(1).strip("`")
            try:
                expected = json.loads(raw_expected)
            except (json.JSONDecodeError, TypeError):
                expected = {"None": None, "True": True, "False": False}.get(raw_expected, raw_expected)
            comparator = "EQUALS"
        witness = witness_by_operation.get(str(symbol or "").casefold())
        if witness is not None:
            witness_expected = witness.get("expected")
            if isinstance(witness_expected, dict) and "exit_code" in witness_expected and match is None:
                comparator, expected = "EXIT_ZERO", witness_expected
            elif isinstance(witness_expected, dict) and "stdout" in witness_expected and match is None:
                comparator, expected = "CONTAINS", str(witness_expected["stdout"]).rstrip("\\n")
        if unresolved_symbol:
            comparator, expected = "RELATION_HOLDS", None
        claim_witness_ids = tuple(
            dict.fromkeys((str(witness.get("witness_id")),) if witness else ())
        )
        variables = tuple(
            {"name": name, "domain": "public API domain", "source_quote": normative_line}
            for name in ("operand", "argument", "input", "value")
            if re.search(rf"\b{name}s?\b", normative_line, re.I)
        )
        claims.append(CompiledRequirementClaim(
            claim_id=stable_id("fallback-claim", normative_line), role=ClaimRole.ILLUSTRATION if illustrative else (ClaimRole.PRESERVATION if re.search(r"\b(?:preserve|remain|continue)", normative_line, re.I) else ClaimRole.TARGET),
            quantifier="FOR_ALL" if variables else "CONTRACT", variables=variables, domain_constraints=(), preconditions=(),
            operation=symbol, expected_observation={"kind": comparator, "expected": expected},
            exception_contract=None, preservation_contract=None, evidence_spans=(span,), witness_ids=claim_witness_ids,
            target_symbols=(symbol,), unresolved_terms=(),
        ))
    if not claims and issue_text.strip():
        claims.append(CompiledRequirementClaim(
            claim_id=stable_id("unresolved-target", issue_text), role=ClaimRole.CONTEXT,
            quantifier="CONDITIONAL", variables=(), domain_constraints=(), preconditions=(),
            operation="UNRESOLVED_TARGET", expected_observation={"kind": "RELATION_HOLDS", "expected": None},
            exception_contract=None, preservation_contract=None, evidence_spans=(), witness_ids=(),
            target_symbols=(), unresolved_terms=("target recovery required",),
        ))
    return RequirementCompilation(tuple(claims), tuple(issue_witness_records), fallback_used=True)


_SPAN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["start", "end", "quote"],
    "properties": {"start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 0}, "quote": {"type": "string"}},
}
_CLAIM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["role", "quantifier", "variables", "domain_constraints", "preconditions", "operation", "expected_observation", "exception_contract", "preservation_contract", "target_symbols", "evidence_spans", "witness_indexes"],
    "properties": {
        "role": {"type": "string", "enum": [item.value for item in ClaimRole]},
        "quantifier": {"type": "string", "enum": ["FOR_ALL", "EXISTS", "CONDITIONAL"]},
        "variables": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name", "domain", "source_quote"], "properties": {"name": {"type": "string"}, "domain": {"type": "string"}, "source_quote": {"type": "string"}}}},
        "domain_constraints": {"type": "array", "items": {"type": "string"}},
        "preconditions": {"type": "array", "items": {"type": "string"}},
        "operation": {"type": "string"},
        "expected_observation": {"type": "object", "additionalProperties": False, "required": ["kind", "expected"], "properties": {"kind": {"type": "string", "enum": ["EXIT_ZERO", "EQUALS", "NOT_EQUALS", "RAISES", "NOT_RAISES", "TYPE_IS", "CONTAINS", "ORDER_EQUALS", "LENGTH_EQUALS", "HAS_ATTR", "STATE_DELTA_EQUALS", "RELATION_HOLDS"]}, "expected": {}, "relation": {"type": "string"}}},
        "exception_contract": {"type": ["object", "null"]},
        "preservation_contract": {"type": ["object", "null"]},
        "target_symbols": {"type": "array", "items": {"type": "string"}},
        "evidence_spans": {"type": "array", "items": _SPAN_SCHEMA},
        "witness_indexes": {"type": "array", "items": {"type": "integer", "minimum": 0}},
    },
}
_TOOL_SCHEMA = {
    "type": "function", "function": {"name": "submit_requirement_contracts", "description": "Submit evidence-grounded requirement claims.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["claims", "witnesses", "ambiguities"],
    "properties": {"claims": {"type": "array", "items": _CLAIM_SCHEMA}, "witnesses": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["source", "code_or_expression", "expected_relation", "evidence_span"], "properties": {"source": {"type": "string", "enum": ["issue", "test", "source"]}, "code_or_expression": {"type": "string"}, "expected_relation": {"type": "string"}, "evidence_span": _SPAN_SCHEMA}}}, "ambiguities": {"type": "array", "items": {"type": "string"}}}}}}


def compile_requirement_contract(issue_text: str, public_evidence: Sequence[Any], source_hints: Sequence[Any], transport: Any, run_root: Path) -> RequirementCompilation:
    messages = [{"role": "system", "content": "Compile only evidence-grounded behavioral contracts. Examples and traceback are not targets."}, {"role": "user", "content": json.dumps({"issue": issue_text, "source_hints": [getattr(item, "to_dict", lambda: item)() for item in source_hints]}, default=str)}]
    raw_args: dict[str, Any] = {}
    attempts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    parsed = False
    previous_response: dict[str, Any] | None = None
    # Keep an artifact-safe representation even when no transport is supplied
    # or the first response fails before a tool payload can be decoded.
    raw_argument_payload: Any = {}
    for attempt in range(3):
        if transport is None:
            break
        try:
            if attempt:
                if previous_response and previous_response.get("tool_calls"):
                    messages.append({
                        "role": "assistant",
                        "content": previous_response.get("content"),
                        "tool_calls": previous_response["tool_calls"],
                    })
                    call_id = str(previous_response["tool_calls"][0].get("id", "submit_requirement_contracts"))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": canonical_validation_feedback(rejected),
                    })
            message = transport.complete(messages, tools=(_TOOL_SCHEMA,), max_tokens=12000, timeout_seconds=120.0, tool_choice={"type": "function", "function": {"name": "submit_requirement_contracts"}})
            previous_response = message
            calls = message.get("tool_calls", ()) if isinstance(message, dict) else ()
            raw_argument_payload = calls[0].get("function", {}).get("arguments", "{}") if calls else "{}"
            raw_args = json.loads(raw_argument_payload) if isinstance(raw_argument_payload, str) else dict(raw_argument_payload)
            if not isinstance(raw_args, dict):
                raise ValueError("tool arguments must be a JSON object")
            parsed = True
        except Exception as error:
            # Tool/JSON failures receive the same structured feedback as
            # semantic validation failures.  Do not fall back to sentence
            # heuristics after a model has already entered the tool protocol.
            rejected = [{"index": None, "claim": None, "errors": (
                f"tool JSON/schema error: {type(error).__name__}: {error}",
            )}]
            attempts.append({"arguments": raw_argument_payload, "validation_errors": rejected})
            continue
        current_errors: list[dict[str, Any]] = []
        accepted: list[CompiledRequirementClaim] = []
        for index, raw in enumerate(raw_args.get("claims", ()) if isinstance(raw_args, dict) else ()):
            claim, errors = _claim_from_raw(raw, issue_text, index)
            if claim is None:
                current_errors.append({"index": index, "claim": raw, "errors": errors})
            else:
                accepted.append(claim)
        attempts.append({"arguments": raw_args, "validation_errors": current_errors})
        rejected = current_errors
        # A semantically invalid response is never partially accepted.  Send
        # every validation error back through the same tool conversation so
        # the model can repair the complete contract payload.
        if accepted and not current_errors:
            break
    else:
        accepted = []
    public_records = (
        tuple(public_evidence.records)
        if hasattr(public_evidence, "records")
        else tuple(public_evidence or ())
    )
    if not parsed:
        fallback = _fallback(issue_text, source_hints, public_records)
        # A fallback is only allowed when no structured response was parsed;
        # retain the raw protocol payload in the artifact even in that case.
        fallback_raw = raw_argument_payload if raw_argument_payload not in ({}, None, "") else {}
        result = RequirementCompilation(fallback.claims, fallback.witnesses, fallback.ambiguities, {"raw": fallback_raw} if not isinstance(fallback_raw, dict) else fallback_raw, (), True, tuple(attempts))
        _write_compilation_artifact(run_root, issue_text, result)
        return result
    unique: dict[tuple[str, str, str], CompiledRequirementClaim] = {}
    for claim in accepted:
        unique.setdefault((claim.operation, claim.quantifier, json.dumps(claim.expected_observation, sort_keys=True, default=str)), claim)
    if not unique:
        # Valid JSON with invalid semantic claims is evidence-limited; do not
        # silently replace it with a dotted-token fallback.
        result = RequirementCompilation(tuple(), tuple(raw_args.get("witnesses", ())), tuple(map(str, raw_args.get("ambiguities", ()))), raw_args, tuple(rejected), False, tuple(attempts))
    else:
        result = RequirementCompilation(tuple(unique.values()), tuple(raw_args.get("witnesses", ())), tuple(map(str, raw_args.get("ambiguities", ()))), raw_args, tuple(rejected), False, tuple(attempts))
    _write_compilation_artifact(run_root, issue_text, result)
    return result


def canonical_validation_feedback(errors: Sequence[dict[str, Any]]) -> str:
    return json.dumps({"tool": "submit_requirement_contracts", "validation_errors": list(errors), "instruction": "repair every listed claim and call the same tool again"}, sort_keys=True, default=str)


def _write_compilation_artifact(run_root: Path, issue_text: str, result: RequirementCompilation) -> None:
    artifact_root = Path(run_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "requirement_compilation.json").write_text(json.dumps({"issue": issue_text, "raw_tool_arguments": result.raw_tool_arguments, "tool_attempts": result.tool_attempts, "validation_rejections": result.rejected_claims, "claims": result.claims, "witnesses": result.witnesses, "ambiguities": result.ambiguities, "fallback_used": result.fallback_used}, sort_keys=True, default=lambda value: value.to_dict() if hasattr(value, "to_dict") else str(value), indent=2) + "\n", encoding="utf-8")
