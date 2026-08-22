from __future__ import annotations

import re
import ast
from typing import Any

from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.evidence import (
    ExceptionContract, ObservationContract, OutcomeStatus, PublicEvidence,
    extract_issue_witnesses, issue_witnesses, primary_issue_content,
)
from reachpatch.models.graphs import (
    RequirementGraph, RequirementLeaf, RequirementVariable,
)


_NORMATIVE = re.compile(
    r"\b(must|must not|should|should not|shall|shall not|needs? to|"
    r"returns?|raises?|preserves?|forbids?|cannot|can not)\b",
    re.IGNORECASE,
)
_EXAMPLE = re.compile(r"\b(for example|e\.g\.|example|such as)\b", re.IGNORECASE)
_DESCRIBED_FAILURE = re.compile(
    r"\b(?:wrongly|incorrectly|unexpectedly|erroneously)\b", re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"`([^`]+)`|\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b")
_FENCED = re.compile(r"```.*?```", re.DOTALL)


def _sentences(issue: str) -> tuple[str, ...]:
    visible = _FENCED.sub(" ", primary_issue_content(issue))
    values: list[str] = []
    for line in visible.splitlines():
        normalized = line.strip(" \t\n-*#")
        if not normalized:
            continue
        values.extend(
            item.strip()
            for item in re.split(r"(?<=[.!?])\s+(?=(?:[A-Z]|`))", normalized)
            if item.strip()
        )
    return tuple(values)


def _implicit_contract(issue: str) -> str:
    visible = _FENCED.sub(" ", primary_issue_content(issue))
    for raw in visible.splitlines():
        value = raw.strip(" \t\r\n-*#")
        if not value or value.lower() in {"description", "additional context"}:
            continue
        if value.lower().startswith(("traceback", "file ")):
            continue
        return value
    return "The requested public behavior must be supported"


def _operation_from_issue(
    issue: str,
    public_evidence: PublicEvidence,
) -> str:
    primary = _FENCED.sub(" ", primary_issue_content(issue))
    title = next((line.strip() for line in primary.splitlines() if line.strip()), primary)
    witness_operations: list[str] = []
    for record in public_evidence.records:
        if record.source == "issue":
            witness_operations.extend(
                str(item["operation"]) for item in issue_witnesses(record)
            )
    if not witness_operations:
        virtual_id = stable_id("evidence", "issue", issue)
        witness_operations.extend(
            str(item["operation"])
            for item in extract_issue_witnesses(issue, virtual_id)
        )
    candidates: set[str] = set(witness_operations)
    candidates.update(
        match.group(1) for match in re.finditer(
            r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(", primary,
        )
    )
    candidates.update(
        (match.group(1) or match.group(2)).strip("`")
        for match in _IDENTIFIER.finditer(primary)
    )
    candidates.update(
        str(record.metadata["symbol"])
        for record in public_evidence.api_contracts
        if isinstance(record.metadata.get("symbol"), str)
    )
    qualified = re.findall(
        r"\b([A-Z][A-Za-z0-9_]*\.[a-z_]\w*)\b", issue,
    )
    candidates.update(qualified)

    def related(text: str, symbol: str) -> bool:
        word = symbol.rsplit(".", 1)[-1].casefold()
        variants = {word}
        if word.endswith("e"):
            variants.add(word[:-1])
        return any(re.search(rf"\b{re.escape(item)}(?:s|es|ed|ing)?\b", text.casefold()) for item in variants)

    def score(value: str) -> tuple[int, int, int, str]:
        terminal = value.rsplit(".", 1)[-1]
        points = 0
        if re.search(rf"`{re.escape(value)}`", primary):
            points += 100
        if related(title, terminal):
            points += 50
        elif related(primary, terminal):
            points += 35
        if value in qualified:
            points += 30
        if terminal in witness_operations:
            points += 20
        if terminal[:1].islower():
            points += 8
        if terminal in {"public", "operation", "result", "value"}:
            points -= 100
        return points, value.count("."), -len(value), value

    viable = [value for value in candidates if re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", value,
    )]
    return max(viable, key=score) if viable else "public operation"


def _variables(sentence: str) -> tuple[RequirementVariable, ...]:
    names: list[str] = []
    for match in _IDENTIFIER.finditer(sentence):
        token = (match.group(1) or match.group(2)).strip()
        for name in re.findall(r"[A-Za-z_]\w*", token):
            if name.lower() not in {
                "none", "true", "false", "must", "should", "return", "returns",
            } and name not in names:
                names.append(name)
    lowered = sentence.lower()
    for word in ("operand", "argument", "input", "value"):
        if re.search(rf"\b{word}s?\b", lowered) and word not in names:
            names.append(word)
    return tuple(RequirementVariable(name) for name in names[:8])


def _domain_constraints(sentence: str) -> tuple[str, ...]:
    lowered = sentence.lower()
    constraints: list[str] = []
    variable = next((
        name for name in ("input", "value", "argument", "operand")
        if re.search(rf"\b{name}s?\b", lowered)
    ), "input")
    if re.search(rf"\bpositive\s+{variable}s?\b", lowered):
        constraints.append(f"{variable} > 0")
    if re.search(rf"\bnon[- ]negative\s+{variable}s?\b", lowered):
        constraints.append(f"{variable} >= 0")
    if re.search(rf"\bnegative\s+{variable}s?\b", lowered):
        constraints.append(f"{variable} < 0")
    if re.search(rf"\bnonempty\s+{variable}s?\b", lowered):
        constraints.append(f"len({variable}) > 0")
    if re.search(rf"\bempty\s+{variable}s?\b", lowered) and "nonempty" not in lowered:
        constraints.append(f"len({variable}) == 0")
    if re.search(rf"\bnon[- ]none\s+{variable}s?\b", lowered):
        constraints.append(f"{variable} is not None")
    return tuple(constraints)


def _kind(sentence: str) -> tuple[str, bool, ExceptionContract | None]:
    lowered = sentence.lower()
    if "raise" in lowered or "exception" in lowered:
        match = re.search(r"\b([A-Za-z_]\w*(?:Error|Exception))\b", sentence)
        contract = ExceptionContract(match.group(1) if match else "Exception")
        return "EXCEPTION_BEHAVIOR", False, contract
    if any(word in lowered for word in ("state", "before", "after", "mutate", "update")):
        return "STATE_TRANSITION", False, None
    if any(word in lowered for word in ("reverse", "reflected", "dispatch", "protocol")):
        return "PROTOCOL_RELATION", False, None
    if any(word in lowered for word in ("return", "result")):
        return "RETURN_CONTRACT", False, None
    if any(word in lowered for word in ("side effect", "write", "emit", "send")):
        return "SIDE_EFFECT_CONTRACT", False, None
    preservation = any(word in lowered for word in ("preserve", "remain", "continue to"))
    return ("PRESERVATION" if preservation else "TARGET_BEHAVIOR"), preservation, None


def _contract(sentence: str, kind: str) -> ObservationContract:
    comparator = "satisfies"
    expected: Any = sentence
    lowered = sentence.lower()
    if "must not" in lowered or "should not" in lowered or "shall not" in lowered:
        comparator = "forbidden"
    if kind == "EXCEPTION_BEHAVIOR":
        comparator = "raises"
    if (
        kind != "EXCEPTION_BEHAVIOR"
        and comparator != "forbidden"
        and re.search(r"\b(?:accept|allow)\b", lowered)
    ):
        comparator = "succeeds"
        expected = {"exit_code": 0}
    match = re.search(
        r"\b(?:return|returns|equal|equals|be)\s+(`[^`]+`|None|True|False|-?\d+(?:\.\d+)?)",
        sentence,
        re.IGNORECASE,
    )
    if match:
        raw = match.group(1).strip("`")
        try:
            expected = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            expected = raw
        comparator = "equals"
    observable = (
        "exception" if kind == "EXCEPTION_BEHAVIOR" else
        "process" if comparator == "succeeds" else "return"
    )
    return ObservationContract(sentence, expected, observable, comparator)


def _leaf(
    sentence: str,
    *,
    authority: str,
    evidence_ids: tuple[str, ...],
    witness_ids: tuple[str, ...],
    preservation_override: bool | None = None,
    operation: str | None = None,
    hard: bool = True,
) -> RequirementLeaf:
    kind, preservation, exception = _kind(sentence)
    if preservation_override is not None:
        preservation = preservation_override
        if preservation:
            kind = "PRESERVATION"
    variables = _variables(sentence)
    requirement_id = stable_id(
        "requirement", kind, sentence, authority, evidence_ids, preservation,
    )
    return RequirementLeaf(
        requirement_id=requirement_id,
        kind=kind,
        quantifier="FOR_ALL" if variables else "CONTRACT",
        variables=variables,
        domain_constraints=(
            _domain_constraints(sentence)
            or tuple(f"{item.name} is in its public API domain" for item in variables)
        ),
        preconditions=_domain_constraints(sentence),
        operation=operation or next((
            match.group(1) or match.group(2)
            for match in _IDENTIFIER.finditer(sentence)
        ), "public operation"),
        expected_observation=_contract(sentence, kind),
        exception_contract=exception,
        preservation=preservation,
        authority=authority,
        evidence_ids=evidence_ids,
        witness_ids=witness_ids,
        status=OutcomeStatus.UNKNOWN if authority in {"A", "B", "C"} else OutcomeStatus.PROVISIONAL,
        hard=hard and authority in {"A", "B"},
    )


def _validated_proposals(public_evidence: PublicEvidence) -> tuple[RequirementLeaf, ...]:
    result: list[RequirementLeaf] = []
    known = public_evidence.by_id()
    for record in public_evidence.records:
        proposals = record.metadata.get("requirement_proposals", ())
        for raw in proposals:
            if not isinstance(raw, dict) or not isinstance(raw.get("operation"), str):
                continue
            grounded = tuple(
                str(item) for item in raw.get("evidence_ids", ()) if str(item) in known
            )
            authority = str(raw.get("authority", "PROVISIONAL"))
            if not grounded:
                authority = "PROVISIONAL"
            sentence = str(raw.get("contract", "")).strip()
            if not sentence:
                continue
            result.append(_leaf(
                sentence,
                authority=authority,
                evidence_ids=grounded,
                witness_ids=tuple(map(str, raw.get("witness_ids", ()))),
                operation=str(raw["operation"]),
                hard=bool(raw.get("hard", False)),
            ))
    return tuple(result)


def _grounded_contracts(content: str, source: str) -> tuple[str, ...]:
    """Select bounded normative contracts from public source/documentation."""

    values: list[str] = []
    for sentence in _sentences(content):
        normalized = sentence.strip()
        if not normalized:
            continue
        if _NORMATIVE.search(normalized):
            values.append(normalized)
    if not values and source in {"public_api", "baseline"} and content.strip():
        # Explicit structured metadata is already scoped as a contract by the
        # producer. Free-form README/documentation records are not.
        values.append(content.strip())
    return tuple(dict.fromkeys(values[:8]))


def _issue_operation_terms(
    issue: str,
    public_evidence: PublicEvidence,
) -> set[str]:
    values = set(re.findall(
        r"`([A-Za-z_]\w*)`|\b([A-Za-z_]\w*)\s*\(",
        _FENCED.sub(" ", primary_issue_content(issue)),
    ))
    terms = {
        item for pair in values for item in pair if item
    }
    for record in public_evidence.records:
        if record.source == "issue":
            terms.update(str(item["operation"]) for item in issue_witnesses(record))
    return terms


def build_requirement_graph(
    issue: str,
    public_evidence: PublicEvidence,
) -> RequirementGraph:
    """Compile evidence-grounded behavioral contracts, not issue sentences."""

    sentences = _sentences(issue)
    sentence_witnesses = tuple(
        stable_id("witness", sentence)
        for sentence in sentences if _EXAMPLE.search(sentence)
    )
    structured_witnesses = tuple(dict.fromkeys(
        str(item["witness_id"])
        for record in public_evidence.records if record.source == "issue"
        for item in issue_witnesses(record)
    ))
    witnesses = tuple(dict.fromkeys(sentence_witnesses + structured_witnesses))
    leaves: dict[str, RequirementLeaf] = {}
    issue_evidence = tuple(
        record.evidence_id for record in public_evidence.records
        if record.source == "issue"
    )
    normative = tuple(dict.fromkeys(
        sentence for sentence in sentences
        if _NORMATIVE.search(sentence)
        and not _EXAMPLE.search(sentence)
        and not _DESCRIBED_FAILURE.search(sentence)
    ))[:8]
    if not normative and issue.strip():
        # The issue itself is authoritative, but an implicit behavior remains
        # one broad contract rather than one leaf per descriptive sentence.
        normative = (_implicit_contract(issue),)
    issue_operation = _operation_from_issue(issue, public_evidence)
    issue_terms = _issue_operation_terms(issue, public_evidence)
    for sentence in normative:
        leaf = _leaf(
            sentence,
            authority="B",
            evidence_ids=issue_evidence,
            witness_ids=witnesses,
            preservation_override=False,
            operation=issue_operation,
        )
        leaves[leaf.requirement_id] = leaf

    for check in public_evidence.checks:
        if check.role != "PRESERVATION":
            continue
        sentence = (
            check.expected.relation if check.expected is not None
            else f"Public check {check.check_id} must remain successful"
        )
        leaf = _leaf(
            sentence,
            authority=check.authority,
            evidence_ids=check.source_evidence_ids,
            witness_ids=(),
            preservation_override=True,
            operation=check.symbol_references[0] if check.symbol_references else check.check_id,
        )
        leaves[leaf.requirement_id] = leaf

    for record in (*public_evidence.api_contracts, *public_evidence.baseline_contracts):
        preservation = record.source != "issue"
        symbol = record.metadata.get("symbol")
        if record.source.startswith("documentation:") and not isinstance(symbol, str):
            continue
        if (
            record.metadata.get("kind") == "docstring_and_type_signature"
            and (not isinstance(symbol, str) or symbol not in issue_terms)
        ):
            continue
        for contract in _grounded_contracts(record.content, record.source):
            leaf = _leaf(
                contract,
                authority=record.authority,
                evidence_ids=(record.evidence_id,),
                witness_ids=(),
                preservation_override=preservation,
                hard=record.authority in {"A", "B"},
                operation=symbol if isinstance(symbol, str) else None,
            )
            leaves[leaf.requirement_id] = leaf

    for leaf in _validated_proposals(public_evidence):
        leaves.setdefault(leaf.requirement_id, leaf)
    return RequirementGraph(
        leaves=leaves,
        evidence_hash=content_hash(public_evidence),
    )
