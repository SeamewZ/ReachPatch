from __future__ import annotations

import re
import ast
import json
from pathlib import Path
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
    contract = _contract(sentence, kind)
    executable = contract.normalized_comparator != "RELATION_HOLDS" or authority in {"A", "B"}
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
        expected_observation=contract,
        exception_contract=exception,
        preservation=preservation,
        authority=authority,
        evidence_ids=evidence_ids,
        witness_ids=witness_ids,
        status=OutcomeStatus.UNKNOWN if authority in {"A", "B", "C"} else OutcomeStatus.PROVISIONAL,
        hard=hard and authority in {"A", "B"},
        domain_partitions=tuple(
            f"{name}:public-domain" for name in variables
        ) or ("default",),
        executable=executable,
        issue_evidence_spans=tuple({"evidence_id": item} for item in evidence_ids),
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
    *,
    transport: Any | None = None,
    source_hints: tuple[Any, ...] = (),
    run_root: Any | None = None,
) -> RequirementGraph:
    """Compile evidence-grounded behavioral contracts, never raw sentences."""
    from .compiler import ClaimRole, _fallback, compile_requirement_contract
    from reachpatch.models.graphs import RequirementVariable
    # The compiler owns both the forced-tool path and its deterministic fallback.
    # Passing no transport deliberately invokes that fallback; the retired
    # sentence/token planner is not part of the production path anymore.
    compilation = (
        compile_requirement_contract(
            issue, public_evidence.records, source_hints, transport, run_root or Path(".")
        ) if transport is not None else _fallback(
            issue, source_hints, public_evidence.records,
        )
    )
    if run_root is not None and transport is None:
        artifact_root = Path(run_root)
        artifact_root.mkdir(parents=True, exist_ok=True)
        (artifact_root / "requirement_compilation.json").write_text(
            json.dumps({
                "issue": issue,
                "raw_tool_arguments": compilation.raw_tool_arguments,
                "validation_rejections": compilation.rejected_claims,
                "claims": compilation.claims,
                "witnesses": compilation.witnesses,
                "ambiguities": compilation.ambiguities,
                "fallback_used": compilation.fallback_used,
            }, sort_keys=True, default=lambda value: value.to_dict() if hasattr(value, "to_dict") else str(value), indent=2) + "\n",
            encoding="utf-8",
        )
    leaves: dict[str, RequirementLeaf] = {}
    issue_evidence_ids = tuple(
        str(record.evidence_id)
        for record in public_evidence.records
        if getattr(record, "source", None) == "issue"
    )
    for claim in compilation.claims:
        if claim.role in {ClaimRole.ILLUSTRATION, ClaimRole.CONTEXT}:
            continue
        expected = claim.expected_observation if isinstance(claim.expected_observation, dict) else {}
        kind = str(expected.get("kind", "RELATION_HOLDS"))
        observable = str(expected.get("observable", "exception" if kind == "RAISES" else "return"))
        contract = ObservationContract(
            relation=f"compiled:{claim.claim_id}", expected=expected.get("expected"),
            observable=observable, comparator=kind,
        )
        variables = tuple(RequirementVariable(
            str(item.get("name", "input")), item.get("domain"), "input"
        ) for item in claim.variables)
        authority = "B" if claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY} else "C"
        leaves[claim.claim_id] = RequirementLeaf(
            requirement_id=claim.claim_id, kind=claim.role.value, quantifier=claim.quantifier,
            variables=variables, domain_constraints=claim.domain_constraints,
            preconditions=claim.preconditions, operation=claim.operation,
            expected_observation=contract, exception_contract=None,
            preservation=claim.role is ClaimRole.PRESERVATION, authority=authority,
            evidence_ids=(
                issue_evidence_ids
                if claim.evidence_spans and issue_evidence_ids
                else tuple(f"span:{span.start}:{span.end}" for span in claim.evidence_spans)
            ),
            witness_ids=claim.witness_ids, status=OutcomeStatus.UNKNOWN,
            hard=claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY},
            executable=bool(claim.operation and kind and kind != "RELATION_HOLDS"),
            issue_evidence_spans=tuple(span.to_dict() for span in claim.evidence_spans),
        )
    # Public preservation checks are executable contracts even when the issue
    # compiler has no preservation sentence. They remain separate from targets.
    for check in public_evidence.checks:
        if check.role != "PRESERVATION":
            continue
        contract = check.expected or ObservationContract(
            f"public check {check.check_id} remains successful", {"exit_code": 0},
            observable="process", comparator="EXIT_ZERO",
        )
        requirement_id = stable_id("preservation-check", check.check_id, contract.normalized())
        leaves.setdefault(requirement_id, RequirementLeaf(
            requirement_id=requirement_id, kind=ClaimRole.PRESERVATION.value,
            quantifier="CONTRACT", variables=(), domain_constraints=(), preconditions=(),
            operation=check.symbol_references[0] if check.symbol_references else check.check_id,
            expected_observation=contract, exception_contract=None, preservation=True,
            authority=check.authority, evidence_ids=check.source_evidence_ids, witness_ids=(),
            status=OutcomeStatus.UNKNOWN, hard=False, executable=True,
        ))
    # Public API/docstring contracts are preservation evidence only.  They are
    # admitted when their declared symbol is related to a compiled target
    # operation; unrelated APIs must not become hard targets merely because
    # their documentation contains normative wording.
    target_operations = tuple(
        str(claim.operation).casefold()
        for claim in compilation.claims
        if claim.role in {ClaimRole.TARGET, ClaimRole.EXCEPTION, ClaimRole.COMPATIBILITY}
    )
    for record in (*public_evidence.api_contracts, *public_evidence.baseline_contracts):
        symbol = str((getattr(record, "metadata", {}) or {}).get("symbol", ""))
        if not symbol:
            continue
        symbol_lower = symbol.casefold()
        if not any(
            symbol_lower in operation or operation in symbol_lower
            or symbol_lower.rsplit(".", 1)[-1] in operation
            for operation in target_operations
        ):
            continue
        contract = ObservationContract(
            relation=str(getattr(record, "content", "") or f"{symbol} public contract"),
            expected=True,
            observable="return",
            comparator="RELATION_HOLDS",
        )
        requirement_id = stable_id("preservation-contract", record.evidence_id, symbol, contract.normalized())
        leaves.setdefault(requirement_id, RequirementLeaf(
            requirement_id=requirement_id,
            kind=ClaimRole.PRESERVATION.value,
            quantifier="CONTRACT",
            variables=(), domain_constraints=(), preconditions=(),
            operation=symbol,
            expected_observation=contract,
            exception_contract=None, preservation=True,
            authority=str(getattr(record, "authority", "PROVISIONAL")),
            evidence_ids=(str(record.evidence_id),), witness_ids=(),
            status=OutcomeStatus.UNKNOWN,
            hard=False, executable=False,
        ))
    return RequirementGraph(leaves=leaves, evidence_hash=content_hash(compilation))
