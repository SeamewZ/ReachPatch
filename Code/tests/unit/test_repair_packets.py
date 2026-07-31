from pathlib import Path
from types import SimpleNamespace

from reachpatch.execution.models import CheckExecution, CheckStatus
from reachpatch.models.base import stable_id
from reachpatch.models.controller import ConfirmedFailure, ExecutableOracle
from reachpatch.repair.context import (
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
    )
    conversation = GeneratorConversation.create("case")
    conversation.inspected_files.add("pkg/api.py")
    conversation.messages.append({
        "role": "tool", "name": "read_file", "content": "{}",
    })
    conversation.messages.append({
        "role": "tool", "name": "apply_edits", "content": "{}",
    })
    revision = SimpleNamespace(
        edits=(object(),), requested_public_checks=(), summary="",
    )

    readiness = assess_first_patch_readiness(state, conversation, revision)
    assert readiness.ready
    assert readiness.target_definition_read
    assert readiness.supporting_tool_event_ids

    no_edit = assess_first_patch_readiness(
        state, GeneratorConversation.create("no-edit"),
        SimpleNamespace(edits=(), requested_public_checks=(), summary=""),
    )
    assert not no_edit.ready


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
