from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from reachpatch.program_graph.budget import Deadline
from reachpatch.program_graph.index import build_repository_index
from reachpatch.repair.deepseek_agent import GeneratorConversation, PersistentDeepSeekAgent
from reachpatch.repair.tools import ProposedEdit, RepairToolExecutor


def test_repair_tool_budgets_and_tree_range_read_cache(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "module.py").write_text(
        "VALUE = 1\n\ndef public(value):\n    return value\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    checks = {
        f"check-{index}": (sys.executable, "-c", "pass")
        for index in range(4)
    }
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        current_tree_hash="tree-one",
        public_checks=checks,
        max_search_calls=2,
        max_read_calls=4,
        max_public_checks=3,
    )

    executor.search_code("VALUE")
    executor.find_references("public")
    with pytest.raises(ValueError, match="search budget exhausted"):
        executor.search_code("return")

    first = executor.read_file("pkg/module.py", 1, 1)
    assert executor.read_file("pkg/module.py", 1, 1) == first
    assert executor.read_calls == 1
    executor.read_file("pkg/module.py", 1, 2)
    executor.read_file("pkg/module.py", 2, 3)
    executor.read_file("pkg/module.py", 3, 4)
    with pytest.raises(ValueError, match="read budget exhausted"):
        executor.read_file("pkg/module.py", 1, 4)

    for check_id in tuple(checks)[:3]:
        assert executor.run_public_check(check_id)["return_code"] == 0
    with pytest.raises(ValueError, match="public check budget exhausted"):
        executor.run_public_check("check-3")


def test_apply_edits_relocates_unique_whitespace_normalized_anchor(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "module.py").write_text(
        "def public(value):\n"
        "    if value:\n"
        "        if value.ready:\n"
        "            return value.old\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        public_checks={},
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="pkg/module.py",
        start_line=2,
        end_line=2,
        expected_source="    if value.ready:",
        replacement="    if value.ready and value.current:",
    ),))

    edit = executor.staged_edits[0]
    assert result["relocated"] == [{
        "path": "pkg/module.py",
        "from_start_line": 2,
        "to_start_line": 3,
        "match": "normalized_whitespace",
    }]
    assert edit.start_line == 3
    assert edit.expected_source == "        if value.ready:"
    assert edit.replacement == "        if value.ready and value.current:"


def test_program_slice_request_ends_revision_until_context_is_materialized(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def public():\n    return 1\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), relevant_source_snippets=(),
        active_program_slice={"files": (), "symbols": ()},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    calls = 0

    def transport(messages, schemas):
        nonlocal calls
        calls += 1
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": "slice",
                "type": "function",
                "function": {
                    "name": "request_program_slice",
                    "arguments": json.dumps({
                        "symbols": ["pkg.api.public"],
                        "relation_kinds": ["calls"],
                    }),
                },
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={}),
            repository_index=index,
        ),
        GeneratorConversation.create("case"),
        executor,
    )

    assert calls == 1
    assert revision.tool_turns == 1
    assert revision.status == "CONTEXT_ONLY"
    assert revision.context_requests[0].symbols == ("pkg.api.public",)


def test_compacted_correction_keeps_primary_repair_packet():
    conversation = GeneratorConversation.create("case")
    primary = '{"initial_repair_packet":{"issue_text":"normative issue"}}'
    conversation.messages.extend((
        {"role": "user", "content": primary},
        {"role": "assistant", "content": "truncated", "finish_reason": "length"},
        {"role": "user", "content": "return one compact action"},
    ))

    messages = PersistentDeepSeekAgent._request_messages(conversation)

    assert any(message.get("content") == primary for message in messages)
    assert messages[-1]["content"] == "return one compact action"
    assert any(
        "primary repair task" in message.get("content", "")
        for message in messages
    )


def test_length_response_forces_compact_edit_without_losing_issue(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        public_checks={},
    )
    context = SimpleNamespace(
        issue="normative issue", working_diff="", failed_checks=(),
        counterexamples=(), first_trace_divergences=(),
        causal_repair_cuts=(), causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "start_line": 1, "end_line": 1,
            "symbol": "VALUE", "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("VALUE",)},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "normative issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )

    class TruncatedThenStructured:
        def __init__(self):
            self.calls = 0

        def __call__(self, messages, schemas):
            self.calls += 1
            assert self.calls == 1
            return {
                "role": "assistant", "content": "x" * 100,
                "finish_reason": "length",
            }

        def call_with_tool_choice(self, messages, schemas, tool_choice):
            self.calls += 1
            available = {
                schema["function"]["name"] for schema in schemas
            }
            assert "normative issue" in "\n".join(
                str(message.get("content", "")) for message in messages
            )
            assert tool_choice == "required"
            if self.calls == 2:
                assert available == {
                    "apply_edits", "finish_revision", "declare_blocker",
                }
                return {
                    "role": "assistant", "content": "", "tool_calls": [{
                        "id": "edit", "type": "function", "function": {
                            "name": "apply_edits", "arguments": json.dumps({
                                "mechanism": "initial_issue_repair",
                                "edits": [{
                                    "relative_path": "module.py",
                                    "start_line": 1, "end_line": 1,
                                    "expected_source": "VALUE = 1",
                                    "replacement": "VALUE = 2",
                                }],
                            }),
                        },
                    }],
                }
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "finish", "type": "function", "function": {
                        "name": "finish_revision", "arguments": json.dumps({
                            "summary": "reviewed the complete diff",
                        }),
                    },
                }],
            }

    transport = TruncatedThenStructured()
    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={"module.py": ()}),
            repository_index=index,
        ),
        GeneratorConversation.create("case"),
        executor,
    )

    assert transport.calls == 3
    assert revision.status == "PROPOSED"
    assert revision.edits[0].replacement == "VALUE = 2"


def test_root_recovery_for_empty_patch_gets_structural_edit_correction(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "start_line": 1, "end_line": 1,
            "symbol": "VALUE", "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("VALUE",)},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue", "working_diff": ""},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    calls: list[set[str]] = []

    def transport(messages, schemas):
        available = {schema["function"]["name"] for schema in schemas}
        calls.append(available)
        if len(calls) == 1:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "model-blocker", "type": "function",
                    "function": {
                        "name": "declare_blocker",
                        "arguments": json.dumps({
                            "reason": "more evidence would be helpful",
                        }),
                    },
                }],
            }
        assert available == {"apply_edits", "request_program_slice"}
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "root-correction-edit", "type": "function",
                "function": {
                    "name": "apply_edits",
                    "arguments": json.dumps({
                        "mechanism": "root_recovery_edit",
                        "edits": [{
                            "relative_path": "module.py", "start_line": 1,
                            "end_line": 1, "expected_source": "VALUE = 1",
                            "replacement": "VALUE = 2",
                        }],
                    }),
                },
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={"module.py": ()}),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert len(calls) == 2
    assert revision.status == "PROPOSED"
    assert revision.mechanism == "root_recovery_edit"
    assert revision.edits[0].replacement == "VALUE = 2"


def test_redundant_program_slice_request_is_corrected_into_an_edit(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), relevant_source_snippets=(),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn == 1:
            name = "request_program_slice"
            arguments = {
                "symbols": ["pkg.api.public"],
                "relation_kinds": ["calls"],
            }
        elif turn == 2:
            assert any(
                "CONTEXT_ALREADY_ACTIVE" in message.get("content", "")
                for message in messages
            )
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {"summary": "changed the active failing source"}
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    program_graph = SimpleNamespace(
        file_index={"module.py": ()},
        resolve_symbol=lambda symbol: ("node",) if symbol.endswith("public") else (),
    )
    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(program_graph=program_graph),
        GeneratorConversation.create("case"),
        executor,
    )

    assert turn == 3
    assert revision.status == "PROPOSED"
    assert revision.edits[0].replacement == "VALUE = 2"
    assert not revision.context_requests


def test_unknown_symbol_cannot_bypass_active_slice_during_synthesis(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "VALUE = 1\n\ndef public():\n    return VALUE\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE",
            "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn <= 2:
            name, arguments = "show_current_diff", {}
        elif turn == 3:
            name = "request_program_slice"
            arguments = {
                "symbols": ["pkg.api.public", "pkg.api._missing"],
                "relation_kinds": ["calls"],
            }
        elif turn == 4:
            assert any(
                "SYMBOL_NOT_FOUND" in message.get("content", "")
                and "VALUE = 1" in message.get("content", "")
                for message in messages
            )
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {"summary": "used the grounded active source"}
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    program_graph = SimpleNamespace(
        file_index={"module.py": ()},
        resolve_symbol=lambda symbol: ("node",) if symbol.endswith("public") else (),
    )
    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(program_graph=program_graph, repository_index=index),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 5
    assert revision.status == "PROPOSED"
    assert revision.edits[0].replacement == "VALUE = 2"
    assert not revision.context_requests


def test_synthesis_analysis_without_tool_gets_a_correction_turn(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE",
            "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ()},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn <= 2:
            name, arguments = "show_current_diff", {}
        elif turn == 3:
            return {
                "role": "assistant",
                "content": "A long analysis that forgot to call a tool.",
            }
        elif turn == 4:
            assert any(
                "Analysis text without a tool call" in message.get("content", "")
                and "VALUE = 1" in message.get("content", "")
                for message in messages
            )
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {"summary": "completed after synthesis correction"}
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={}),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 5
    assert revision.status == "PROPOSED"
    assert revision.edits[0].replacement == "VALUE = 2"


def test_synthesis_edit_mismatch_returns_actual_source_before_budget_ends(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), relevant_source_snippets=(),
        active_program_slice={"files": ("module.py",), "symbols": ()},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn <= 2:
            name, arguments = "show_current_diff", {}
        elif turn == 3:
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = stale",
                    "replacement": "VALUE = 2",
                }],
            }
        elif turn == 4:
            assert any(
                "actual source at requested range: 'VALUE = 1'"
                in message.get("content", "")
                for message in messages
            )
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {"summary": "corrected exact source mismatch"}
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(program_graph=SimpleNamespace(file_index={})),
        GeneratorConversation.create("case"),
        executor,
    )

    assert turn == 5
    assert revision.status == "PROPOSED"
    assert revision.edits[0].expected_source == "VALUE = 1"


def test_structural_correction_returns_accepted_slice_to_controller(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "VALUE = 1\n\ndef needed():\n    return VALUE\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="needed() must return the normalized public value",
        working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE",
            "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ()},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": "needed() must return the normalized public value"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn == 1:
            name = "declare_blocker"
            arguments = {"reason": "need the exact needed implementation"}
        elif turn == 2:
            assert "needed() must return" in messages[-1]["content"]
            name = "request_program_slice"
            arguments = {"symbols": ["needed"], "relation_kinds": ["calls"]}
        else:
            raise AssertionError("accepted context expansion must end the revision")
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(
                file_index={"module.py": ()}, resolve_symbol=lambda symbol: (),
            ),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 2
    assert revision.status == "CONTEXT_ONLY"
    assert revision.context_requests[0].symbols == ("needed",)
    assert not revision.edits


def test_structural_recovery_retries_rejected_edit_with_issue_and_actual_source(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "VALUE = 1\n\ndef public():\n    return VALUE\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = "public() must return 2 while preserving its callable API"
    context = SimpleNamespace(
        issue=issue, working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE",
            "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn == 1:
            name = "declare_blocker"
            arguments = {"reason": "need one more source decision"}
        elif turn == 2:
            name = "request_program_slice"
            arguments = {"symbols": ["public"], "relation_kinds": ["calls"]}
        elif turn == 3:
            assert issue in messages[-1]["content"]
            name = "apply_edits"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = stale",
                    "replacement": "VALUE = 2",
                }],
            }
        elif turn == 4:
            assert issue in messages[-1]["content"]
            assert "actual source at requested range: 'VALUE = 1'" in messages[-1]["content"]
            name = "apply_edits"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            raise AssertionError("structural retry must remain bounded")
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(
                file_index={"module.py": ()},
                resolve_symbol=lambda symbol: ("node",) if symbol == "public" else (),
            ),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 4
    assert revision.status == "PROPOSED"
    assert revision.edits[0].expected_source == "VALUE = 1"
    assert revision.edits[0].replacement == "VALUE = 2"
