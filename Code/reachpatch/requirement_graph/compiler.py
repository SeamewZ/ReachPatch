from __future__ import annotations

"""Evidence-constrained semantic requirement compiler."""

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Sequence

from reachpatch.models.base import SerializableRecord, stable_id


class ClaimRole(StrEnum):
    TARGET = "TARGET"
    PRESERVATION = "PRESERVATION"
    EXCEPTION = "EXCEPTION"
    COMPATIBILITY = "COMPATIBILITY"
    ILLUSTRATION = "ILLUSTRATION"
    CONTEXT = "CONTEXT"


@dataclass(frozen=True, slots=True)
class EvidenceSpan(SerializableRecord):
    start: int
    end: int
    quote: str


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


_NORMATIVE = re.compile(r"\b(must|should|shall|needs? to|expected|allow|support|return|raise|no longer|cannot|must not|should not|preserve|remain|continue)\b", re.I)
_EXAMPLE = re.compile(r"\b(for example|e\.g\.|such as|illustrat(?:ion|e))\b", re.I)
_TRACE = re.compile(r"^\s*(?:traceback|file \"|\^+)", re.I)
_SYMBOL = re.compile(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`")
_CALL = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")
_BARE_SYMBOL = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_]\w*)*)\b")


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
    if claim.role is ClaimRole.TARGET and normative and not _NORMATIVE.search(normative):
        errors.append("target evidence has no normative predicate")
    if _EXAMPLE.search(normative) and claim.role is ClaimRole.TARGET:
        errors.append("illustrative evidence cannot be a hard target")
    if _TRACE.search(normative):
        errors.append("traceback text cannot define expected behavior")
    if not claim.operation.strip():
        errors.append("claim operation is empty")
    if not isinstance(claim.expected_observation, dict) or not str(claim.expected_observation.get("kind", "")).strip():
        errors.append("expected observation must be structured")
    return not errors, tuple(dict.fromkeys(errors))


def _claim_from_raw(raw: Any, issue_text: str, index: int) -> tuple[CompiledRequirementClaim | None, tuple[str, ...]]:
    if not isinstance(raw, dict):
        return None, ("claim is not an object",)
    try:
        role = ClaimRole(str(raw.get("role", "CONTEXT")).upper())
    except ValueError:
        role = ClaimRole.CONTEXT
    spans = tuple(span for span in (_span_from_raw(item, issue_text) for item in raw.get("evidence_spans", ())) if span is not None)
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
    return (claim if valid else None), errors


def _fallback(
    issue_text: str,
    source_hints: Sequence[Any],
    public_records: Sequence[Any] = (),
) -> RequirementCompilation:
    claims: list[CompiledRequirementClaim] = []
    offset = 0
    in_fence = False
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
    witness_symbols.extend([
        match.group(1) for match in re.finditer(
            r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(", issue_text,
        ) if match.group(1).split(".")[0].casefold() not in {"tests", "example"}
    ])
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
        if (
            not stripped or in_fence or _TRACE.search(line)
            or lowered.startswith(("actual behavior", "actual:", "steps to reproduce", "traceback"))
            or not _NORMATIVE.search(line)
        ):
            continue
        normative_line = re.split(
            r"\b(?:for example|e\.g\.|such as)\b", line,
            maxsplit=1, flags=re.I,
        )[0].rstrip()
        if not normative_line or not _NORMATIVE.search(normative_line):
            continue
        span = EvidenceSpan(start, start + len(normative_line), normative_line)
        symbol = next((m.group(1) for m in _SYMBOL.finditer(normative_line)), None)
        if not symbol:
            symbol = next((m.group(1) for m in _CALL.finditer(normative_line) if m.group(1).lower() not in {"tests", "example"}), None)
        if not symbol:
            symbol = next(
                (
                    match.group(1)
                    for match in _BARE_SYMBOL.finditer(normative_line)
                    if match.group(1) not in {"The", "This", "Please", "Public"}
                ),
                None,
            )
        if not symbol and witness_symbols:
            symbol = witness_symbols[0]
        if not symbol:
            symbol = next((str(getattr(item, "symbol", "")) for item in source_hints if getattr(item, "symbol", None)), "UNRESOLVED_TARGET")
        illustrative = False
        comparator = "RAISES" if re.search(r"\b(?:raise|exception)", normative_line, re.I) else (
            "EXIT_ZERO"
            if re.search(r"\b(?:accept|allow|support|succeed|works?)\b", normative_line, re.I)
            else "RELATION_HOLDS"
        )
        match = re.search(r"\b(?:return|returns?|equal|equals?|be)\s+(`[^`]+`|None|True|False|-?\d+(?:\.\d+)?)", normative_line, re.I)
        expected: Any = True if comparator == "RAISES" else ({"exit_code": 0} if comparator == "EXIT_ZERO" else None)
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


_TOOL_SCHEMA = {"type": "function", "function": {"name": "submit_requirement_contracts", "description": "Submit evidence-grounded requirement claims.", "parameters": {"type": "object", "required": ["claims", "witnesses", "ambiguities"], "properties": {"claims": {"type": "array", "items": {"type": "object"}}, "witnesses": {"type": "array"}, "ambiguities": {"type": "array"}}}}}


def compile_requirement_contract(issue_text: str, public_evidence: Sequence[Any], source_hints: Sequence[Any], transport: Any, run_root: Path) -> RequirementCompilation:
    messages = [{"role": "system", "content": "Compile only evidence-grounded behavioral contracts. Examples and traceback are not targets."}, {"role": "user", "content": json.dumps({"issue": issue_text, "source_hints": [getattr(item, "to_dict", lambda: item)() for item in source_hints]}, default=str)}]
    raw_args: dict[str, Any] = {}
    rejected: list[dict[str, Any]] = []
    for attempt in range(2):
        try:
            if attempt:
                messages.append({"role": "user", "content": "Schema error. Return one valid submit_requirement_contracts tool call."})
            message = transport.complete(messages, tools=(_TOOL_SCHEMA,), max_tokens=12000, timeout_seconds=120.0, tool_choice={"type": "function", "function": {"name": "submit_requirement_contracts"}})
            calls = message.get("tool_calls", ()) if isinstance(message, dict) else ()
            arguments = calls[0].get("function", {}).get("arguments", "{}") if calls else "{}"
            raw_args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
            break
        except Exception:
            if attempt:
                return _fallback(issue_text, source_hints, public_evidence)
    accepted: list[CompiledRequirementClaim] = []
    for index, raw in enumerate(raw_args.get("claims", ()) if isinstance(raw_args, dict) else ()):
        claim, errors = _claim_from_raw(raw, issue_text, index)
        if claim is None:
            rejected.append({"claim": raw, "errors": errors})
        else:
            accepted.append(claim)
    unique: dict[tuple[str, str, str], CompiledRequirementClaim] = {}
    for claim in accepted:
        unique.setdefault((claim.operation, claim.quantifier, json.dumps(claim.expected_observation, sort_keys=True, default=str)), claim)
    if not unique:
        fallback = _fallback(issue_text, source_hints, public_evidence)
        result = RequirementCompilation(fallback.claims, fallback.witnesses, fallback.ambiguities, raw_args, tuple(rejected), True)
    else:
        result = RequirementCompilation(tuple(unique.values()), tuple(raw_args.get("witnesses", ())), tuple(map(str, raw_args.get("ambiguities", ()))), raw_args, tuple(rejected), False)
    artifact_root = Path(run_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "requirement_compilation.json").write_text(
        json.dumps({
            "issue": issue_text,
            "raw_tool_arguments": result.raw_tool_arguments,
            "validation_rejections": result.rejected_claims,
            "claims": result.claims,
            "witnesses": result.witnesses,
            "ambiguities": result.ambiguities,
            "fallback_used": result.fallback_used,
        }, sort_keys=True, default=lambda value: value.to_dict() if hasattr(value, "to_dict") else str(value), indent=2) + "\n",
        encoding="utf-8",
    )
    return result
