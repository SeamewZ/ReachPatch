from pathlib import Path

from reachpatch.models.evidence import EvidenceRecord, PublicEvidence, public_evidence_from_instance
from reachpatch.requirement_graph.compiler import (
    ClaimRole, CompiledRequirementClaim, EvidenceSpan, _fallback,
    validate_compiled_claim,
)
from reachpatch.requirement_graph.builder import build_requirement_graph


def test_real_target_and_illustrative_example_keep_quantifier(tmp_path):
    issue = "\x60combine\x60 must accept every public operand. For example, \x60other\x60 returns 1."
    evidence = public_evidence_from_instance(issue, (), {}, tmp_path)
    graph = build_requirement_graph(issue, evidence)
    targets = [leaf for leaf in graph.leaves.values() if not leaf.preservation]
    assert len(targets) == 1
    assert targets[0].operation == "combine"
    assert targets[0].quantifier == "FOR_ALL"


def test_traceback_line_is_not_compiled_as_expected_requirement():
    issue = "The API must return 3.\nTraceback (most recent call last):\n  File \"api.py\", line 2"
    compilation = _fallback(issue, (), ())
    assert all("Traceback" not in claim.operation for claim in compilation.claims)
    assert all(not any("Traceback" in span.quote for span in claim.evidence_spans) for claim in compilation.claims)


def test_code_block_witness_does_not_change_for_all_quantifier():
    issue = "\x60combine\x60 must accept all values.\n\x60\x60\x60python\ncombine([], 1)\n\x60\x60\x60"
    compilation = _fallback(issue, (), ())
    target = next(claim for claim in compilation.claims if claim.role is ClaimRole.TARGET)
    assert target.quantifier == "FOR_ALL"


def test_invalid_span_offsets_reject_claim():
    issue = "The API must return 3."
    claim = CompiledRequirementClaim(
        "claim", ClaimRole.TARGET, "CONTRACT", (), (), (), "api",
        {"kind": "EQUALS", "expected": 3}, None, None,
        (EvidenceSpan(1, 4, "The"),), (), ("api",), (),
    )
    valid, errors = validate_compiled_claim(claim, issue)
    assert not valid
    assert "evidence span quote/offset mismatch" in errors


def test_multiple_normative_lines_are_retained():
    issue = "\x60first\x60 must return 1.\n\x60second\x60 must raise ValueError."
    compilation = _fallback(issue, (), ())
    operations = {claim.operation for claim in compilation.claims}
    assert {"first", "second"}.issubset(operations)


def test_fallback_does_not_use_first_dotted_token_from_example():
    issue = "The implementation must support the API. For example, tests.helper() returns 1."
    compilation = _fallback(issue, (), ())
    assert all(claim.operation != "tests.helper" for claim in compilation.claims)


def test_unrelated_positive_sentence_does_not_authorize_all_witnesses():
    issue = "Actual behavior is broken. The API must return 2."
    record = EvidenceRecord(
        "issue", "issue", "B", issue, metadata={
            "issue_witnesses": ({"witness_id": "w", "operation": "api", "expected": {"exit_code": 1}},),
        },
    )
    evidence = PublicEvidence(records=(record,))
    compilation = _fallback(issue, (), evidence.records)
    assert all("w" not in claim.witness_ids for claim in compilation.claims)
