from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from reachpatch.program_graph.budget import Deadline
from reachpatch.program_graph.index import build_repository_index
from reachpatch.repair.deepseek_agent import GeneratorConversation, PersistentDeepSeekAgent
from reachpatch.repair.tools import RepairToolExecutor


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
