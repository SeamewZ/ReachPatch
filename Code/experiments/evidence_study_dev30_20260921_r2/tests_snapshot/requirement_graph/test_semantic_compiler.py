from pathlib import Path
import json

from reachpatch.models.evidence import EvidenceRecord, PublicEvidence, SourceHint, public_evidence_from_instance
from reachpatch.requirement_graph.compiler import (
    ClaimRole, CompiledRequirementClaim, EvidenceSpan, _fallback,
    compile_goal_contracts, validate_compiled_claim,
)
from reachpatch.requirement_graph.builder import build_requirement_graph


def test_fallback_preserves_failed_tool_protocol(tmp_path):
    class BrokenTransport:
        def complete(self, messages, **kwargs):
            raise RuntimeError("provider unavailable")
    issue = "`transform_coordinates` must return 3."
    public = public_evidence_from_instance(issue, (), {}, tmp_path)
    compile_goal_contracts(issue, public, (), BrokenTransport(), tmp_path / "compile")
    protocol = json.loads((tmp_path / "compile" / "goal_compilation_tool.json").read_text())
    assert len(protocol["tool_attempts"]) == 2
    assert "provider unavailable" in str(protocol["validation_errors"])
    fallback = json.loads((tmp_path / "compile" / "requirement_compilation.json").read_text())
    assert fallback["fallback_used"]


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


def test_goal_tool_validation_feedback_retries_same_conversation(tmp_path):
    issue = "BoundWidget.id_for_label should return the widget id."
    quote = issue

    class Transport:
        def __init__(self):
            self.calls = 0
            self.messages = []

        def complete(self, messages, **kwargs):
            self.calls += 1
            self.messages.append(tuple(messages))
            assert kwargs["tool_choice"]["function"]["name"] == "submit_goal_contracts"
            if self.calls == 1:
                arguments = {"claim": "bad legacy shape", "evidence": quote}
            else:
                arguments = {"goals": [{
                    "operation": "BoundWidget.id_for_label",
                    "target_symbols": ["BoundWidget.id_for_label"],
                    "comparator": "EQUALS",
                    "expected": "the widget id",
                    "evidence_spans": [{"start": 0, "end": len(quote), "quote": quote}],
                    "authority": "B",
                    "hard": True,
                    "unresolved_reason": None,
                }]}
            import json
            return {"tool_calls": [{"function": {"name": "submit_goal_contracts", "arguments": json.dumps(arguments)}}]}

    transport = Transport()
    goals = compile_goal_contracts(issue, (), (), transport, tmp_path)
    assert transport.calls == 2
    assert any(message.get("role") == "tool" and "validation_errors" in message.get("content", "") for message in transport.messages[-1])
    assert goals[0].operation == "BoundWidget.id_for_label"
    assert goals[0].hard


def test_goal_tool_rejects_exception_class_as_operation_and_retries(tmp_path):
    issue = "The value call must raise ValueError."

    class Transport:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            import json
            operation = "ValueError" if self.calls == 1 else "value"
            args = {
                "goals": [{
                    "operation": operation, "target_symbols": [operation],
                    "comparator": "RAISES",
                    "expected": {"exception_type": "ValueError"},
                    "evidence_spans": [{"start": 0, "end": len(issue), "quote": issue}],
                    "authority": "B", "hard": True, "unresolved_reason": None,
                }]
            }
            return {"tool_calls": [{"function": {"name": "submit_goal_contracts", "arguments": json.dumps(args)}}]}

    transport = Transport()
    goals = compile_goal_contracts(issue, (), (), transport, tmp_path)
    assert transport.calls == 2
    assert goals[0].operation == "value"
    assert goals[0].expected == {"exception_type": "ValueError"}


def test_django_style_method_is_not_replaced_by_indented_return():
    issue = (
        "BoundWidget.id_for_label should return the id of its first subwidget.\n"
        "    return self.subwidgets(name, value, attrs)[0].id_for_label(id_)\n"
        "Actual: the current result points at the container."
    )
    compilation = _fallback(issue, (), ())
    hard = [claim for claim in compilation.claims if claim.role is ClaimRole.TARGET]
    assert hard and hard[0].operation == "BoundWidget.id_for_label"
    assert all(claim.operation != "subwidgets" for claim in hard)


def test_exception_name_is_oracle_not_fallback_operation():
    compilation = _fallback("The `value` call must raise `ValueError`.", (), ())
    target = next(claim for claim in compilation.claims if claim.role is ClaimRole.TARGET)
    assert target.operation == "value"
    assert target.target_symbols == ("value",)
    assert target.expected_observation == {"kind": "RAISES", "expected": {"exception_type": "ValueError"}}


def test_unresolved_goal_is_emitted_without_normative_evidence(tmp_path):
    goals = compile_goal_contracts(
        "The behavior is surprising. Here is some context only.",
        (), (), None, tmp_path,
    )
    assert len(goals) == 1
    assert goals[0].operation == "UNRESOLVED_TARGET"
    assert not goals[0].hard


def test_return_type_dotted_name_does_not_shadow_evidenced_operation(tmp_path):
    issue = "calc must return numbers.Real."
    (tmp_path / "calc.py").write_text("def calc():\n    return None\n", encoding="utf-8")
    evidence = public_evidence_from_instance(
        issue, (), {"public_checks": ({
            "check_id": "target", "command": (
                "python", "-c",
                "from calc import calc; import numbers; assert calc() is numbers.Real",
            ), "role": "TARGET", "authority": "A",
            "symbol_references": ("calc",),
        },)}, tmp_path,
    )
    from reachpatch.reach_avoid.controller import build_requirement_source_hints
    hints = build_requirement_source_hints(tmp_path, issue, evidence.checks)
    goals = compile_goal_contracts(issue, evidence, hints, None, tmp_path / "compile")
    assert len(goals) == 1
    assert goals[0].operation == "calc"
    assert goals[0].source_hint_ids
    assert goals[0].evidence_span_ids


def test_following_witness_does_not_promote_instead_as_operation(tmp_path):
    issue = (
        "Issue when passing empty lists/arrays to WCS transformations\n"
        "The following should not fail but instead should return empty lists/arrays:\n\n"
        "```\nfrom astropy.wcs import WCS\n"
        "wcs = WCS('example.fits')\nwcs.wcs_pix2world([], [], 0)\n```\n"
    )
    evidence = public_evidence_from_instance(issue, (), {}, tmp_path)
    goals = compile_goal_contracts(issue, evidence, (), None, tmp_path / "compile")

    assert all(goal.operation != "instead" for goal in goals)
    target = next(goal for goal in goals if goal.operation == "wcs_pix2world")
    assert target.hard


def test_public_maintainer_hint_can_ground_issue_operation(tmp_path):
    issue = (
        "socket.error exception is not wrapped in a requests exception.\n"
        "Here is a traceback showing requests/models.py in iter_content.\n\n"
        "Public maintainer hints:\n"
        "No, this looks like an error.\n"
        "`iter_content` doesn't expect socket errors, but it should. We need to fix this.\n"
    )
    hint = SourceHint("hint", "iter_content", "requests/models.py", 623, 663,
                      "def iter_content(self): ...", ("issue",),
                      "traceback and public maintainer operation", "B")
    evidence = public_evidence_from_instance(issue, (), {}, tmp_path)
    goals = compile_goal_contracts(issue, evidence, (hint,), None, tmp_path / "compile")
    target = next(goal for goal in goals if goal.operation == "iter_content")
    assert target.hard and target.authority == "B"
    assert target.comparator == "RELATION_HOLDS" and target.expected is True
