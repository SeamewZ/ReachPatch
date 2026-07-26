from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.core import Evidence, SourceSpan
from reachpatch.models.enums import Authority, Confidence, EvidenceKind, SemanticNodeKind

_NORMATIVE = re.compile(
    r"\b(must|shall|required|requires|should|expected(?:\s+(?:to|result))?|"
    r"needs?\s+to|has\s+to|cannot|may\s+not)\b",
    re.IGNORECASE,
)
_OBSERVATION = re.compile(
    r"\b(currently|actual(?:ly)?|observed|returns?\s+instead|got\b|"
    r"traceback|stack\s*trace|fails?\s+with|version\s*[:=])\b",
    re.IGNORECASE,
)
_PRESERVATION = re.compile(
    r"\b(preserve|remain|unchanged|backward compatible|still (?:works?|passes?)|"
    r"must not (?:change|break|mutate))\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(r"\?|\b(could|would|is it possible|why does|how can)\b", re.IGNORECASE)
_VERSION_ONLY = re.compile(
    r"^\s*[A-Za-z][\w.-]*\s*(?:==|=|:|\s)\s*v?\d+(?:\.\d+){1,3}\s*$"
)
_SUBJECT = re.compile(
    r"(?:`([^`]+)`|\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b|\b([A-Za-z_]\w*)\b)"
)


@dataclass(frozen=True, slots=True)
class ParsedEvidence:
    semantic_kind: SemanticNodeKind
    formula: str
    subject: str
    authority_candidate: Authority
    rule: str


def segment_text(text: str, *, source: str) -> list[tuple[str, SourceSpan, str]]:
    """Split prose by clauses while retaining fenced code as atomic evidence."""
    lines = text.splitlines()
    segments: list[tuple[str, SourceSpan, str]] = []
    buffer: list[str] = []
    start_line = 1
    in_fence = False

    def flush(end_line: int, rule: str = "clause_segment") -> None:
        nonlocal buffer, start_line
        value = "\n".join(buffer).strip()
        if value:
            segments.append((value, SourceSpan(source, start_line, end_line), rule))
        buffer = []

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if buffer and not in_fence:
                flush(index - 1)
            if not buffer:
                start_line = index
            buffer.append(line)
            in_fence = not in_fence
            if not in_fence:
                flush(index, "fenced_code")
            continue
        if in_fence:
            buffer.append(line)
            continue
        if not stripped:
            if buffer:
                flush(index - 1)
            start_line = index + 1
            continue
        clauses = [
            match.group(0).strip()
            for match in re.finditer(r".+?(?:[.!?。！？](?=\s|$)|$)", stripped)
            if match.group(0).strip()
        ]
        for clause in clauses:
            if not buffer:
                start_line = index
            buffer.append(clause)
            if (
                re.search(r"[.!?。！？]\s*$", clause)
                or stripped.startswith(("- ", "* "))
            ):
                flush(index)
                start_line = index
    if buffer:
        flush(len(lines))
    return segments


def make_evidence(
    content: str,
    span: SourceSpan,
    extraction_rule: str,
    *,
    kind: EvidenceKind,
    authority: Authority = Authority.PROVISIONAL,
    confidence: Confidence = Confidence.HIGH,
    metadata: dict[str, object] | None = None,
) -> Evidence:
    digest = content_hash(content)
    return Evidence(
        evidence_id=stable_id("evidence", kind, span, digest),
        kind=kind,
        source=span.source,
        content=content,
        source_span=span,
        independence_cluster=stable_id("cluster", kind, span.source, span.start_line),
        extraction_rule=extraction_rule,
        content_hash=digest,
        authority=authority,
        confidence=confidence,
        metadata=dict(metadata or {}),
    )


def issue_evidence(issue: str, *, source: str = "issue") -> list[Evidence]:
    records: list[Evidence] = []
    for content, span, extraction_rule in segment_text(issue, source=source):
        if _VERSION_ONLY.match(content):
            kind = EvidenceKind.CURRENT_BEHAVIOR
            authority = Authority.PROVISIONAL
        elif _QUESTION.search(content) and not _NORMATIVE.search(content):
            kind = EvidenceKind.ISSUE_WITNESS
            authority = Authority.PROVISIONAL
        elif _NORMATIVE.search(content):
            kind = EvidenceKind.ISSUE_NORMATIVE
            authority = Authority.A
        else:
            kind = EvidenceKind.ISSUE_WITNESS
            authority = Authority.PROVISIONAL
        records.append(make_evidence(
            content,
            span,
            extraction_rule,
            kind=kind,
            authority=authority,
        ))
    return records


def _assert_formula(node: ast.AST, source_text: str) -> str:
    segment = ast.get_source_segment(source_text, node)
    if segment:
        return segment.strip()
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def public_test_evidence(paths: Iterable[str | Path]) -> list[Evidence]:
    records: list[Evidence] = []
    for supplied_path in sorted(Path(path).resolve() for path in paths):
        if not supplied_path.is_file() or supplied_path.suffix != ".py":
            continue
        source_text = supplied_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source_text, filename=str(supplied_path))
        except SyntaxError as exc:
            span = SourceSpan(str(supplied_path), exc.lineno or 1, exc.end_lineno or exc.lineno or 1)
            records.append(make_evidence(
                str(exc),
                span,
                "public_test_syntax_error",
                kind=EvidenceKind.STATIC_INFERENCE,
                confidence=Confidence.CONFIRMED,
                metadata={"parse_error": True},
            ))
            continue
        for node in ast.walk(tree):
            assertion_kind: str | None = None
            if isinstance(node, ast.Assert):
                assertion_kind = "assert"
            elif (
                isinstance(node, ast.With)
                and any(
                    isinstance(item.context_expr, ast.Call)
                    and isinstance(item.context_expr.func, ast.Attribute)
                    and item.context_expr.func.attr == "raises"
                    for item in node.items
                )
            ):
                assertion_kind = "raises"
            if assertion_kind is None:
                continue
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            span = SourceSpan(str(supplied_path), start, end)
            records.append(make_evidence(
                _assert_formula(node, source_text),
                span,
                f"public_test_{assertion_kind}_ast",
                kind=EvidenceKind.PUBLIC_TEST,
                authority=Authority.A,
                confidence=Confidence.CONFIRMED,
                metadata={"assertion_kind": assertion_kind},
            ))
    return records


def contract_evidence(
    contracts: Iterable[tuple[str, str, EvidenceKind]],
) -> list[Evidence]:
    records: list[Evidence] = []
    for source, content, kind in contracts:
        authority = Authority.B if kind in {EvidenceKind.DOCUMENTATION, EvidenceKind.TYPE_CONTRACT} else Authority.C
        records.append(make_evidence(
            content,
            SourceSpan(source, 1, max(1, content.count("\n") + 1)),
            "public_contract",
            kind=kind,
            authority=authority,
            confidence=Confidence.HIGH,
        ))
    return records


def extract_subject(text: str) -> str:
    ignored = {
        "the", "a", "an", "this", "that", "must", "shall", "should",
        "expected", "currently", "return", "returns", "raise", "raises",
    }
    for match in _SUBJECT.finditer(text):
        candidate = next(group for group in match.groups() if group is not None)
        if candidate.lower() not in ignored:
            return candidate
    return "behavior"


def normalize_formula(text: str) -> str:
    value = re.sub(r"```\w*|```", " ", text)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def deterministic_semantic_parse(evidence: Evidence) -> ParsedEvidence:
    text = evidence.content
    formula = normalize_formula(text)
    subject = extract_subject(text)
    if evidence.kind == EvidenceKind.PUBLIC_TEST:
        return ParsedEvidence(
            SemanticNodeKind.PRESERVATION_CONTRACT,
            formula,
            subject,
            Authority.A,
            "visible_public_assertion",
        )
    if evidence.kind in {EvidenceKind.DOCUMENTATION, EvidenceKind.TYPE_CONTRACT}:
        return ParsedEvidence(
            SemanticNodeKind.NORMATIVE_REQUIREMENT,
            formula,
            subject,
            Authority.B,
            "resolved_public_contract",
        )
    if evidence.kind == EvidenceKind.MODEL_HYPOTHESIS:
        return ParsedEvidence(
            SemanticNodeKind.SEMANTIC_HYPOTHESIS,
            formula,
            subject,
            Authority.PROVISIONAL,
            "model_cannot_assign_authority",
        )
    if evidence.kind == EvidenceKind.ISSUE_NORMATIVE and _NORMATIVE.search(text):
        kind = (
            SemanticNodeKind.PRESERVATION_CONTRACT
            if _PRESERVATION.search(text)
            else SemanticNodeKind.NORMATIVE_REQUIREMENT
        )
        return ParsedEvidence(kind, formula, subject, Authority.A, "explicit_modal_obligation")
    if evidence.kind in {EvidenceKind.PRESERVATION_BEHAVIOR, EvidenceKind.CURRENT_BEHAVIOR}:
        kind = (
            SemanticNodeKind.PRESERVATION_CONTRACT
            if evidence.kind == EvidenceKind.PRESERVATION_BEHAVIOR
            else SemanticNodeKind.OBSERVATION
        )
        authority = Authority.C if kind == SemanticNodeKind.PRESERVATION_CONTRACT else Authority.PROVISIONAL
        return ParsedEvidence(kind, formula, subject, authority, "checked_behavior_classification")
    if _QUESTION.search(text):
        return ParsedEvidence(
            SemanticNodeKind.SEMANTIC_HYPOTHESIS,
            formula,
            subject,
            Authority.PROVISIONAL,
            "question_proposes_hypothesis_without_authority",
        )
    if _OBSERVATION.search(text) or _VERSION_ONLY.match(text):
        return ParsedEvidence(
            SemanticNodeKind.OBSERVATION,
            formula,
            subject,
            Authority.PROVISIONAL,
            "observation_or_question_not_normative",
        )
    return ParsedEvidence(
        SemanticNodeKind.SEMANTIC_HYPOTHESIS,
        formula,
        subject,
        Authority.PROVISIONAL,
        "unqualified_issue_claim",
    )
