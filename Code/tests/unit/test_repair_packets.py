from pathlib import Path
from types import SimpleNamespace

from reachpatch.execution.models import CheckExecution, CheckStatus
from reachpatch.models.base import stable_id
from reachpatch.models.controller import ConfirmedFailure, ExecutableOracle
from reachpatch.program_graph.budget import Deadline
from reachpatch.program_graph.index import build_repository_index
from reachpatch.repair.context import (
    _issue_context_snippets,
    _ranked_discussion_evidence,
    assess_first_patch_readiness,
    build_initial_repair_packet,
    build_revision_packet,
)
from reachpatch.repair.deepseek_agent import GeneratorConversation


def _execution(check_id: str, status: CheckStatus, tree: str) -> CheckExecution:
    return CheckExecution(
        execution_id=stable_id("execution", check_id, status, tree),
        check_id=check_id,
        tree_hash=tree,
        status=status,
        return_code=0 if status == CheckStatus.PASS else 1,
        stdout="actual stdout",
        stderr="actual stderr",
        duration_seconds=0.01,
        stable=True,
        failure_signature="failure" if status == CheckStatus.FAIL else None,
        first_project_frame={
            "relative_path": "pkg/api.py", "line": 10, "symbol": "public",
        },
    )


def _context(issue: str):
    return SimpleNamespace(
        issue=issue,
        public_discussion=(
            "A proposed patch should be verified against callers before use."
        ),
        working_diff="diff --git a/pkg/api.py b/pkg/api.py\n",
        relevant_source_snippets=({
            "relative_path": "pkg/api.py",
            "start_line": 1,
            "end_line": 12,
            "symbol": "pkg.api.public",
            "content": "def public(value):\n    return value",
        },),
        reproduction_command=("python", "-m", "pytest", "public_test.py"),
        preservation_checks=({"check_id": "preserve"},),
        baseline_output={"stderr": "actual stderr"},
        causal_repair_cuts=({"node_id": "cut"},),
        failed_mechanisms=(),
        prohibited_mechanisms=(),
    )


def test_initial_packet_preserves_independent_behavior_sentences_and_witnesses(
    tmp_path,
):
    issue = (
        "For every value, public(value) must return a normalized list. "
        "When value is None, it should raise ValueError instead. "
        "Existing tuple behavior must remain compatible. Example: `public(None)`."
    )
    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        requirement_graph=SimpleNamespace(leaves={}),
        program_graph=SimpleNamespace(file_index={}),
        checkpoint=SimpleNamespace(snapshot_tree=str(tmp_path)),
        active_binding_graph=SimpleNamespace(units={}),
    )
    packet = build_initial_repair_packet(state, context=_context(issue))

    checklist = packet.requirement_checklist
    assert any(
        "For every value" in item
        for item in (*checklist.change_requirements, *checklist.boundary_requirements)
    )
    assert any("When value is None" in item for item in checklist.exception_requirements)
    assert any("Existing tuple behavior" in item for item in checklist.preservation_requirements)
    assert "`public(None)`" in checklist.witnesses
    assert "public" in packet.candidate_symbols
    assert packet.discussion_evidence


def test_initial_packet_bounds_source_while_preserving_exact_line_coordinates(
    tmp_path,
):
    issue = "public(value) must preserve its result while normalizing every value."
    long_source = "\n".join(f"line_{index} = {index}" for index in range(300))
    context = _context(issue)
    context.relevant_source_snippets = ({
        "relative_path": "pkg/api.py",
        "snippet_start_line": 40,
        "snippet_end_line": 339,
        "symbol": "pkg.api.public",
        "content": long_source,
    },)
    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        requirement_graph=SimpleNamespace(leaves={}),
        program_graph=SimpleNamespace(file_index={}),
        checkpoint=SimpleNamespace(snapshot_tree=str(tmp_path)),
        active_binding_graph=SimpleNamespace(units={}),
    )

    packet = build_initial_repair_packet(state, context=context)
    snippet = packet.likely_definitions[0]

    assert snippet["truncated_for_initial_packet"] is True
    assert snippet["start_line"] == 40
    assert snippet["end_line"] == 139
    assert len(snippet["content"].splitlines()) == 100
    assert len(snippet["content"]) <= 7_000


def test_fallback_requirement_is_split_into_actionable_behavior_sentences(tmp_path):
    issue = (
        "Iteration currently returns a silently wrong result. "
        "It would be good to detect mutation inside `public`. "
        "This would entail recording the size and raising RuntimeError when it changes."
    )
    fallback = SimpleNamespace(
        formula=issue,
        preservation_contract={},
        witnesses=(),
        coverage_status="NEEDS_GENERATOR_INTERPRETATION",
    )
    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        requirement_graph=SimpleNamespace(
            leaves={"fallback": fallback}, build_stats={"fallback_used": 1},
        ),
        program_graph=SimpleNamespace(file_index={}),
        checkpoint=SimpleNamespace(snapshot_tree=str(tmp_path)),
        active_binding_graph=SimpleNamespace(units={}),
    )

    checklist = build_initial_repair_packet(
        state, context=_context(issue),
    ).requirement_checklist

    assert checklist.change_requirements == (
        "Iteration currently returns a silently wrong result.",
        "It would be good to detect mutation inside `public`.",
    )
    assert checklist.exception_requirements == (
        "This would entail recording the size and raising RuntimeError when it changes.",
    )
    assert "`public`" in checklist.witnesses
    assert issue in checklist.uncertainties


def test_issue_projection_prefers_explicit_named_owner_over_shared_method(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "widgets.py").write_text(
        "class Widget:\n"
        "    def id_for_label(self, value):\n"
        "        return value\n\n"
        "class MultiWidget(Widget):\n"
        "    def id_for_label(self, value):\n"
        "        return value + '_0'\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    state = SimpleNamespace(
        checkpoint=SimpleNamespace(snapshot_tree=str(repository)),
        repository_index=index,
    )

    snippets = _issue_context_snippets(
        state,
        "Remove the label target from MultiWidget.id_for_label().",
        ["pkg/widgets.py"],
    )

    assert snippets
    assert snippets[0]["symbol"].endswith("MultiWidget")
    assert "class MultiWidget" in snippets[0]["content"]


def test_initial_packet_recovers_lexical_caller_and_related_public_test(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "tests").mkdir()
    (repository / "pkg" / "api.py").write_text(
        "def public(value):\n    return value\n", encoding="utf-8",
    )
    (repository / "pkg" / "consumer.py").write_text(
        "from .api import public\n\n"
        "def render(value):\n"
        "    return public(value)\n",
        encoding="utf-8",
    )
    (repository / "tests" / "test_api.py").write_text(
        "from pkg.api import public\n\n"
        "def test_public_contract():\n"
        "    assert public(None) is None\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    issue = "public(value) must preserve None while normalizing other values."
    context = _context(issue)
    context.relevant_source_snippets = ({
        "relative_path": "pkg/api.py",
        "snippet_start_line": 1,
        "snippet_end_line": 2,
        "symbol": "pkg.api.public",
        "content": "def public(value):\n    return value",
    },)
    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        requirement_graph=SimpleNamespace(leaves={}),
        # Deliberately omit caller edges so the bounded lexical fallback is used.
        program_graph=SimpleNamespace(file_index={"pkg/api.py": ()}),
        repository_index=index,
        checkpoint=SimpleNamespace(snapshot_tree=str(repository)),
        active_binding_graph=SimpleNamespace(units={}),
    )

    packet = build_initial_repair_packet(state, context=context)

    assert any(
        item["relative_path"] == "pkg/consumer.py"
        and "return public(value)" in item["content"]
        for item in packet.direct_callers
    )
    assert any(
        item["relative_path"] == "tests/test_api.py"
        and "assert public(None) is None" in item["content"]
        for item in packet.related_public_tests
    )


def test_discussion_evidence_keeps_late_correction_over_early_speculation():
    early = (
        "Could this be fixed in both shared modules? "
        "This speculative proposal needs more investigation. "
    ) * 120
    correction = (
        "The problem is in pkg/forms/fields.py when Widget.__init__() evaluates "
        "the value. The correct fix should only change that form constructor; "
        "changing the shared model API instead would break serialization."
    )

    evidence = _ranked_discussion_evidence(early + correction)

    assert sum(map(len, evidence)) <= 6_000
    assert "pkg/forms/fields.py" in evidence[0]
    assert any("pkg/forms/fields.py" in item for item in evidence)
    assert any("should only change" in item for item in evidence)


def test_issue_title_precedes_incidental_modal_background(tmp_path):
    issue = (
        "NCA fails in GridSearch due to too strict parameter checks\n"
        "The issue is that np.int64 is not accepted as int.\n"
        "For background, l1_ratio must be between 0 and 1."
    )
    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        requirement_graph=SimpleNamespace(leaves={}),
        program_graph=SimpleNamespace(file_index={}),
        checkpoint=SimpleNamespace(snapshot_tree=str(tmp_path)),
        active_binding_graph=SimpleNamespace(units={}),
    )

    checklist = build_initial_repair_packet(
        state, context=_context(issue),
    ).requirement_checklist

    assert checklist.change_requirements[0] == (
        "NCA fails in GridSearch due to too strict parameter checks"
    )
    assert any(
        "np.int64 is not accepted" in item
        for item in checklist.change_requirements
    )


def test_discussion_source_anchor_ranks_correct_same_named_definition(tmp_path):
    issue = "Allow FilePathField path to accept a callable."
    context = _context(issue)
    context.public_discussion = (
        "The problem is in pkg/forms/fields.py when FilePathField.__init__() "
        "consumes the value. The correct fix should only change that form "
        "constructor; changing the model definition would break serialization."
    )
    context.relevant_source_snippets = (
        {
            "relative_path": "pkg/models/fields.py",
            "start_line": 1,
            "end_line": 2,
            "symbol": "pkg.models.fields.FilePathField",
            "content": "class FilePathField:\n    pass",
        },
        {
            "relative_path": "pkg/forms/fields.py",
            "start_line": 1,
            "end_line": 2,
            "symbol": "pkg.forms.fields.FilePathField",
            "content": "class FilePathField:\n    pass",
        },
    )
    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        requirement_graph=SimpleNamespace(leaves={}),
        program_graph=SimpleNamespace(file_index={}),
        checkpoint=SimpleNamespace(snapshot_tree=str(tmp_path)),
        active_binding_graph=SimpleNamespace(units={}),
    )

    packet = build_initial_repair_packet(state, context=context)

    assert packet.likely_definitions[0]["relative_path"] == "pkg/forms/fields.py"


def test_first_patch_readiness_is_derived_from_tool_events():
    state = SimpleNamespace(
        requirement_graph=SimpleNamespace(leaves={
            "req": SimpleNamespace(
                formula="public(value) must return a list",
                preservation_contract={"existing": "preserve"},
                witnesses=(), coverage_status="UNKNOWN",
            ),
        }),
        target_recovery=SimpleNamespace(targets=(), preservation_checks=()),
        runtime_metrics={
            "initial_packet_evidence": {
                "definition_paths": ["pkg/api.py"],
                "caller_paths": [],
                "test_paths": [],
                "caller_search_completed": True,
                "test_search_completed": True,
                "evidence_ids": ["packet-definition"],
            },
        },
    )
    conversation = GeneratorConversation.create("case")
    conversation.messages.append({
        "role": "tool", "name": "apply_edits",
        "content": '{"accepted": true, "staged_edit_version": 1}',
    })
    conversation.messages.append({
        "role": "system",
        "content": '{"event": "SYSTEM_STAGED_DIFF_PREVIEW"}',
    })
    conversation.messages.append({
        "role": "tool", "name": "finish_revision",
        "content": '{"finished": true, "reviewed_staged_version": 1}',
    })
    revision = SimpleNamespace(
        edits=(object(),), requested_public_checks=(),
        summary="localized and reviewed root-cause repair",
    )

    readiness = assess_first_patch_readiness(state, conversation, revision)
    assert readiness.ready
    assert readiness.target_definition_read
    assert readiness.caller_inspection_status == "NOT_FOUND_AFTER_BOUNDED_SEARCH"
    assert readiness.test_or_contract_inspection_status == "NOT_FOUND_AFTER_BOUNDED_SEARCH"
    assert readiness.supporting_tool_event_ids

    no_edit = assess_first_patch_readiness(
        state, GeneratorConversation.create("no-edit"),
        SimpleNamespace(edits=(), requested_public_checks=(), summary=""),
    )
    assert not no_edit.ready


def test_read_source_counts_as_definition_even_if_index_mislabels_it_as_test():
    state = SimpleNamespace(
        requirement_graph=SimpleNamespace(leaves={
            "req": SimpleNamespace(
                formula="ExceptionInfo must preserve exception text",
                preservation_contract={}, witnesses=(), coverage_status="UNKNOWN",
            ),
        }),
        runtime_metrics={
            "initial_packet_evidence": {
                "definition_paths": [],
                "caller_paths": ["src/pkg/caller.py"],
                # Lightweight indices can expose a source module through their
                # test-reference map. Its path, not that incidental membership,
                # determines whether a real definition was inspected.
                "test_paths": ["src/pkg/code.py", "testing/test_code.py"],
                "caller_search_completed": True,
                "test_search_completed": True,
                "evidence_ids": [],
            },
        },
    )
    conversation = GeneratorConversation.create("mislabelled-source")
    conversation.messages.extend((
        {
            "role": "tool", "name": "read_file",
            "content": '{"path":"src/pkg/code.py","content":"class ExceptionInfo: pass"}',
        },
        {
            "role": "tool", "name": "apply_edits",
            "content": '{"accepted":true,"staged_edit_version":1}',
        },
        {
            "role": "system", "content": "SYSTEM_STAGED_DIFF_PREVIEW",
        },
        {
            "role": "tool", "name": "finish_revision",
            "content": '{"finished":true,"reviewed_staged_version":1}',
        },
    ))
    revision = SimpleNamespace(
        edits=(object(),), requested_public_checks=(),
        summary="removed the conflicting formatter after inspecting the definition",
    )

    readiness = assess_first_patch_readiness(state, conversation, revision)

    assert readiness.target_definition_read
    assert readiness.root_cause_identified


def test_revision_packet_contains_only_the_selected_confirmed_failure():
    first = ConfirmedFailure(
        failure_id="first", kind="CONFIRMED_TARGET_FAILURE", check_id="target-1",
        oracle_authority="A", requirement_id="req-1", binding_unit_id="unit-1",
        baseline_observation=_execution("target-1", CheckStatus.FAIL, "base"),
        before_patch_observation=_execution("target-1", CheckStatus.FAIL, "work"),
        expected_relation=ExecutableOracle(
            oracle_id="oracle-1", authority="A",
            relation="baseline_failure_must_become_pass",
        ),
        stable_runs=2, failure_signature="first-signature", failure_location=None,
        causal_cut_ids=("cut-1",), impact_risk_ids=(),
    )
    selected = ConfirmedFailure(
        failure_id="selected", kind="CONFIRMED_PRESERVATION_REGRESSION",
        check_id="preserve", oracle_authority="A", requirement_id="req-2",
        binding_unit_id="unit-2",
        baseline_observation=_execution("preserve", CheckStatus.PASS, "base"),
        before_patch_observation=_execution("preserve", CheckStatus.FAIL, "work"),
        expected_relation=ExecutableOracle(
            oracle_id="oracle-2", authority="A",
            relation="baseline_pass_must_be_preserved",
        ),
        stable_runs=2, failure_signature="selected-signature",
        failure_location="pkg/api.py:10", causal_cut_ids=("cut-2",),
        impact_risk_ids=("consumer",),
    )
    unit = SimpleNamespace(
        requirement_text="tuple behavior remains compatible",
        changed_hunk_ids=("hunk-2",), preservation_check_ids=("preserve",),
    )
    state = SimpleNamespace(
        runtime_metrics={"selected_confirmed_failure_id": "selected"},
        confirmed_failures=(first, selected),
        counterexamples=(),
        active_binding_graph=SimpleNamespace(units={"unit-2": unit}),
        requirement_graph=SimpleNamespace(leaves={}),
        failure_histories={},
        patch_trajectory=SimpleNamespace(working_patch=SimpleNamespace(
            patch=SimpleNamespace(canonical_diff="working diff"),
        )),
        prohibited_mechanisms={"remove_wrapper"},
    )

    packet = build_revision_packet(state, context=_context("issue"))
    assert packet.current_patch == "working diff"
    assert packet.failure_kind == "CONFIRMED_PRESERVATION_REGRESSION"
    assert packet.causal_cut_ids == ("cut-2",)
    assert packet.changed_hunks == ("hunk-2",)
    assert "first-signature" not in str(packet.to_dict())
