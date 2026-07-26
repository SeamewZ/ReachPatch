from __future__ import annotations

from reachpatch.evidence import build_semantic_graph, enumerate_assignments, freeze_assignment
from reachpatch.models.enums import Authority, SemanticNodeKind


def test_observation_normative_and_question_are_separate_authority_classes():
    result = build_semantic_graph(
        "Currently f(x) returns 1. It must return 2. Could it return 3?"
    )
    claims = sorted(result.graph.claims.values(), key=lambda claim: claim.formula)
    by_formula = {claim.formula: claim for claim in claims}

    assert by_formula["Currently f(x) returns 1."].kind == SemanticNodeKind.OBSERVATION
    assert by_formula["Currently f(x) returns 1."].authority == Authority.PROVISIONAL
    assert by_formula["It must return 2."].kind == SemanticNodeKind.NORMATIVE_REQUIREMENT
    assert by_formula["It must return 2."].authority == Authority.A
    assert by_formula["Could it return 3?"].authority == Authority.PROVISIONAL


def test_public_assertion_is_preservation_and_model_claim_cannot_be_authority_a(tmp_path):
    public_test = tmp_path / "test_public.py"
    public_test.write_text("def test_api():\n    assert api(1) == 2\n", encoding="utf-8")
    result = build_semantic_graph("The model thinks api returns 7.", visible_test_paths=[public_test])
    public_claims = [
        claim
        for claim in result.graph.claims.values()
        if claim.kind == SemanticNodeKind.PRESERVATION_CONTRACT
    ]
    provisional = [
        claim for claim in result.graph.claims.values() if claim.authority == Authority.PROVISIONAL
    ]
    assert len(public_claims) == 1
    assert public_claims[0].authority == Authority.A
    assert provisional


def test_multiple_unresolved_alternatives_retain_a_preferred_assignment():
    result = build_semantic_graph(
        "The result could preserve identity? The result could create a copy?"
    )
    decisions, assignments = enumerate_assignments(result.graph)
    assert decisions
    assert assignments
    assignment = freeze_assignment(result.graph, selection_mode="certified")
    assert assignment is not None
    assert assignment.coherent
    assert assignment.authority_complete
    assert freeze_assignment(result.graph, selection_mode="benchmark") is not None
