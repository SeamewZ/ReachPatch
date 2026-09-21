from types import SimpleNamespace
import threading
import time

import pytest

from reachpatch.execution.case_budget import CaseBudget, BudgetedTransport, CaseBudgetExhausted
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    DynamicReachAvoidGraph, CheckpointState, GraphNodeKind,
)
from reachpatch.reach_avoid.evidence_context import (
    claim_evidence_action, compact_evidence_context, read_evidence, efficiency_report,
    ensure_evidence_question, resolve_evidence_question, evidence_question_summary,
)


def graph():
    g = DynamicReachAvoidGraph()
    g.register_checkpoint(CheckpointState("cp", None, "diff", "hash"))
    return g


def test_repeated_question_requires_new_evidence_not_more_nodes():
    g = graph()
    assert claim_evidence_action(g, "cp", "REPAIR", "guard", {"actual": 1})
    g.record_update("UNRELATED_UPDATE")
    assert not claim_evidence_action(g, "cp", "REPAIR", "guard", {"actual": 1})
    assert claim_evidence_action(g, "cp", "REPAIR", "guard", {"actual": 2})
    assert claim_evidence_action(g, "cp", "REPAIR", "consumer", {"actual": 1})


def test_no_reuse_ablation_repeats_action_and_context_payload():
    g = graph()
    g.add_node(GraphNodeKind.OBSERVATION, node_id="evidence-policy", status="ACTIVE",
               metadata={"evidence_reuse_enabled": False})
    assert claim_evidence_action(g, "cp", "REPAIR", "guard", {"actual": 1})
    assert claim_evidence_action(g, "cp", "REPAIR", "guard", {"actual": 1})
    source = "same-source" * 50
    packet = compact_evidence_context(g, (
        {"role": "tool", "tool_call_id": "one", "content": source},
        {"role": "tool", "tool_call_id": "two", "content": source},
    ), scope="ablation")
    assert packet.messages[0]["content"] == packet.messages[1]["content"] == source


def test_question_reopens_only_when_relevant_evidence_version_changes():
    g = graph()
    evidence = g.add_node(
        GraphNodeKind.OBSERVATION, node_id="observation", status="VALID",
        metadata={"semantic_signature": "one"},
    )
    question = ensure_evidence_question(
        g, "cp", "FALSIFY", "Does the boundary refute the patch?",
        dependency_ids=(evidence.node_id,), relevant_versions={"source": "v1"},
    )
    resolve_evidence_question(g, question.question_id, "SUPPORTED",
                              evidence_ids=(evidence.node_id,))
    unchanged = ensure_evidence_question(
        g, "cp", "FALSIFY", "Does the boundary refute the patch?",
        dependency_ids=(evidence.node_id,), relevant_versions={"source": "v1"},
    )
    assert unchanged.status == "SUPPORTED"
    changed = ensure_evidence_question(
        g, "cp", "FALSIFY", "Does the boundary refute the patch?",
        dependency_ids=(evidence.node_id,), relevant_versions={"source": "v2"},
    )
    assert changed.status == "OPEN"
    assert evidence_question_summary(g, "cp")["open_question_ids"] == (question.question_id,)
    report = efficiency_report(g, None)
    assert report["evidence_question_count"] == 1
    assert report["question_reopen_count"] == 1


def test_context_keeps_exact_source_and_protocol_and_rehydrates_each_call():
    g = graph()
    source = "exact source\n" * 100
    messages = [{"role": "system", "content": "Do not change tests"},
                {"role": "tool", "tool_call_id": "a", "content": source},
                {"role": "tool", "tool_call_id": "b", "content": source}]
    packet = compact_evidence_context(g, messages, scope="initial")
    assert packet.messages[0] == messages[0]
    assert packet.messages[1]["content"] == source
    assert packet.messages[2]["tool_call_id"] == "b"
    assert packet.sent_bytes < packet.original_bytes
    again = compact_evidence_context(g, messages[1:2], scope="initial")
    assert again.messages[0]["content"] == source
    assert len(packet.evidence_ids) == 1
    assert efficiency_report(g, None)["context_elided_bytes"] > 0


def test_source_version_invalidates_only_changed_file():
    g = graph()
    read_evidence(g, "a.py", "v1", 1, 2, {"content": "old"})
    read_evidence(g, "b.py", "v1", 1, 2, {"content": "unchanged"})
    assert read_evidence(g, "a.py", "v1", 1, 2, {"content": "old"})["content"] == "old"
    read_evidence(g, "a.py", "v2", 1, 2, {"content": "new"})
    reads = [n for n in g.nodes.values() if n.metadata.get("evidence_kind") == "SOURCE_READ"]
    assert [(n.file, n.status) for n in reads].count(("a.py", "STALE")) == 1
    assert next(n for n in reads if n.file == "b.py").status == "VALID"


def test_budget_reserve_rejection_is_auditable_and_does_not_call_provider():
    class Provider:
        def complete(self, *args, **kwargs):
            pytest.fail("request should be rejected before provider call")
    budget = CaseBudget(100, max_tokens=100, final_validation_reserve=0)
    budget.state = SimpleNamespace(phase="INITIAL_GENERATION")
    with pytest.raises(CaseBudgetExhausted):
        BudgetedTransport(Provider(), budget).complete([{"content": "x" * 100}], tools=(), max_tokens=10)
    event = budget.events[-1]
    assert event["protected_tokens"] == 20
    assert event["input_reservation"] > event["remaining_tokens"]
    assert budget.model_calls == 0


def test_budget_records_provider_usage_separately_from_reservation():
    class Provider:
        def complete(self, *args, **kwargs):
            return {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    budget = CaseBudget(100, max_tokens=1000, final_validation_reserve=0)
    BudgetedTransport(Provider(), budget).complete([{"content": "question"}], tools=(), max_tokens=10)
    assert budget.tokens == 15
    assert budget.summary()["usage"]["prompt_tokens"] == 10
    assert budget.events[-1]["usage_reported"]


def test_stale_safe_alias_cannot_hide_confirmed_regression(tmp_path):
    from dataclasses import replace
    from reachpatch.models.execution import StateCheckpoint
    from reachpatch.reach_avoid.execution_checkpoint import select_final_checkpoint
    baseline = StateCheckpoint("base", None, str(tmp_path), "base", "", "BOOTSTRAP", 0)
    stale = StateCheckpoint("trial", "base", str(tmp_path), "trial", "diff", "OPEN", 1,
                            search_score=(1, 1, 1))
    current = replace(stale, confirmed_preservation_regression=True)
    state = SimpleNamespace(certified_checkpoint=None, best_checkpoint=stale, safe_checkpoint=stale,
        working_checkpoint=current, checkpoint_history={"base": baseline, "trial": current},
        rejected_patch_hashes=set())
    assert select_final_checkpoint(state).checkpoint_id == "base"


def test_initial_budget_exhaustion_seals_registered_bootstrap(tmp_path):
    from reachpatch.models.core import Instance
    from reachpatch.reach_avoid.controller import ReachAvoidController
    from reachpatch.reach_avoid.repair_player import RepairPlayer
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg.py").write_text("def target(x):\n    return x\n")

    class OutOfBudget:
        def revise(self, objective, tools, initial=False):
            raise CaseBudgetExhausted("TOKEN_BUDGET")

    result = ReachAvoidController(RepairPlayer(OutOfBudget())).run_case(
        Instance("budget-smoke", str(repo), "base", "target should accept empty input"),
        run_root=tmp_path / "run")
    assert result.status == "BEST_EFFORT_BUDGET_EXHAUSTED"
    assert (tmp_path / "run/token_efficiency.json").is_file()
    assert (tmp_path / "run/final.patch").read_text() == ""


@pytest.mark.parametrize("repeat_read", [False, True])
def test_single_edit_needs_no_model_validation_or_finish_turn(tmp_path, repeat_read):
    import json
    from reachpatch.models.core import Instance
    from reachpatch.reach_avoid.controller import ReachAvoidController
    from reachpatch.reach_avoid.repair_player import RepairPlayer
    from reachpatch.repair.deepseek_agent import DeepSeekAgent
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg.py").write_text("def target(values):\n    return [values[0]]\n")
    patch = "*** Begin Patch\n*** Update File: pkg.py\n@@\n-    return [values[0]]\n+    return [values[0]] if len(values) else []\n*** End Patch"

    class Provider:
        calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            assert all(s["function"]["name"] != "finish_revision" for s in kwargs["tools"])
            name, arguments = ("read_file", {"path": "pkg.py"}) if self.calls <= 1 + int(repeat_read) else ("apply_patch", {"patch": patch})
            assert self.calls <= 2 + int(repeat_read), "validation/submission must not consume a model call"
            return {"role": "assistant", "content": "", "_usage": {"total_tokens": 100},
                    "tool_calls": [{"id": str(self.calls), "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments)}}]}

    provider = Provider()
    result = ReachAvoidController(RepairPlayer(DeepSeekAgent(provider))).run_case(
        Instance("two-call-smoke", str(repo), "base",
                 "target fails on empty input\n\ntarget should not fail and should return an empty list:\n\n```python\nfrom pkg import target\ntarget([])\n```"),
        run_root=tmp_path / "run")
    assert result.status == "REACHED"
    assert provider.calls == 2 + int(repeat_read)
    budget = json.loads((tmp_path / "run/case_budget.json").read_text())
    assert budget["model_calls"] == provider.calls and budget["tokens"] == 100 * provider.calls


def test_fixed_recovery_has_target_recovery_stage_and_bounded_turns(tmp_path):
    import json
    from reachpatch.models.core import Instance
    from reachpatch.reach_avoid.controller import ReachAvoidController, ReachAvoidConfig
    from reachpatch.reach_avoid.repair_player import RepairPlayer
    from reachpatch.repair.deepseek_agent import DeepSeekAgent

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pkg.py").write_text("def target(values):\n    return [values[0]]\n")
    patch = """*** Begin Patch
*** Update File: pkg.py
@@
-    return [values[0]]
+    return [values[0]] if len(values) else []
*** End Patch"""

    class Provider:
        calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            tool_names = {item["function"]["name"] for item in kwargs["tools"]}
            if "apply_patch" in tool_names:
                name, arguments = (("read_file", {"path": "pkg.py"})
                                   if self.calls == 1 else
                                   ("apply_patch", {"patch": patch}))
                return {"role": "assistant", "content": "", "_usage": {"total_tokens": 100},
                        "tool_calls": [{"id": str(self.calls), "type": "function",
                                        "function": {"name": name,
                                                     "arguments": json.dumps(arguments)}}]}
            # The fixed arm must call recovery even though deterministic
            # facets already cover the target.  One empty turn is sufficient
            # for this accounting test and is bounded by the controller config.
            return {"role": "assistant", "content": "covered", "_usage": {"total_tokens": 100},
                    "tool_calls": []}

    provider = Provider()
    result = ReachAvoidController(
        RepairPlayer(DeepSeekAgent(provider)),
        ReachAvoidConfig(
            demand_driven_interaction_enabled=False,
            fixed_recovery_max_agent_turns=1,
            max_case_model_calls=4,
            max_case_tokens=50_000,
            execution_budget_seconds=120,
        ),
    ).run_case(
        Instance(
            "fixed-stage-smoke", str(repo), "base",
            "target should return an empty list:\n\n"
            "```python\nfrom pkg import target\nassert target([]) == []\n```",
        ),
        run_root=tmp_path / "run",
    )

    budget = json.loads((tmp_path / "run/case_budget.json").read_text())
    model_stages = [event["stage"] for event in budget["events"]
                    if event["kind"] == "MODEL"]
    assert result.status == "REACHED"
    assert provider.calls == 3
    assert model_stages == ["INITIAL_GENERATION", "INITIAL_GENERATION", "TARGET_RECOVERY"]
    graph_updates = (tmp_path / "run/graph_updates.jsonl").read_text()
    assert '"event":"FIXED_RECOVERY_SCHEDULED"' in graph_updates
    assert '"max_agent_turns":1' in graph_updates


def test_initial_context_bounds_source_not_requirement(tmp_path):
    from reachpatch.reach_avoid.evidence_context import initial_source_context
    from reachpatch.reach_avoid.dynamic_reach_avoid_graph import GraphNodeKind
    g = graph()
    g.add_node(GraphNodeKind.SYMBOL, file="pkg.py", symbol="target", source_span="line\n" * 3000)
    context = initial_source_context(g, max_source_chars=100)
    assert len(context["source_spans"][0]["source"]) <= 100
    assert context["source_spans"][0]["omitted_lines"] > 0


def test_validation_queue_runs_independent_checks_concurrently_in_stable_order(monkeypatch, tmp_path):
    from reachpatch.models.execution import CheckExecution, CheckStatus
    from reachpatch.reach_avoid.controller import ReachAvoidController

    active = 0
    peak = 0
    guard = threading.Lock()

    def fake_execute(tree, check, *, stability_runs, base_tree):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return CheckExecution(
            check_id=check.check_id, status=CheckStatus.PASS,
            observation=SimpleNamespace(), trace=SimpleNamespace(), runs=2,
            stable=True, semantic_signature=check.check_id,
            entered_project_code=True, entered_target_code=True,
        )

    monkeypatch.setattr("reachpatch.reach_avoid.controller.execute_check", fake_execute)
    checks = tuple(SimpleNamespace(check_id=f"check-{index}") for index in range(4))
    results = ReachAvoidController._execute_queue(
        tmp_path, checks, tmp_path, max_workers=2,
    )

    assert peak == 2
    assert [item.check_id for item in results] == [item.check_id for item in checks]


def test_compacted_repair_context_does_not_duplicate_inline_probe(monkeypatch):
    from reachpatch.repair.deepseek_agent import DeepSeekAgent

    probe = "assert target()\n" * 4000
    tools = SimpleNamespace(
        validation_status=lambda: {
            "required_count": 1, "pending_count": 1,
            "pending_commands": (("python", "-c", probe),),
            "pending_ids": ("target",), "failed_validation_ids": (),
            "unknown_validation_ids": (), "blocked_validation_ids": (),
            "satisfied_validation_ids": (), "outcomes": (), "ready": False,
        },
    )

    compact = DeepSeekAgent._prompt_validation_status(tools)

    assert probe not in str(compact)
    assert compact["pending_ids"] == ("target",)
    assert compact["pending_command_refs"][0]["command_hash"]
    assert compact["pending_command_refs"][0]["selector"] == "-c"


def test_bound_messages_does_not_duplicate_full_diff(tmp_path):
    from reachpatch.repair.deepseek_agent import DeepSeekAgent
    from reachpatch.repair.execution_objective import InitialPatchObjective

    marker = "UNIQUE_FULL_DIFF_MARKER"
    objective = InitialPatchObjective(
        "initial", (), (), marker, "hash", graph_context={},
    )
    evidence_graph = graph()
    tools = SimpleNamespace(
        read_file=lambda *args, **kwargs: {"path": "pkg.py", "start_line": 1,
                                           "end_line": 1, "content": "source"},
        inspect_diff=lambda: {"canonical_diff": marker, "patch_hash": "hash"},
        validation_status=lambda: {
            "required_count": 0, "pending_count": 0, "pending_commands": (),
            "pending_ids": (), "failed_validation_ids": (),
            "unknown_validation_ids": (), "blocked_validation_ids": (),
            "satisfied_validation_ids": (), "outcomes": (), "ready": True,
        },
        state=SimpleNamespace(dynamic_failure_graph=evidence_graph),
    )
    messages = [{"role": "system", "content": "system"}] + [
        {"role": "tool", "tool_call_id": str(index), "content": "x" * 9000}
        for index in range(7)
    ]

    compact = DeepSeekAgent._bound_messages(messages, objective, tools, {})

    assert compact[1]["content"].count(marker) == 1


def test_child_incremental_edit_preserves_parent_hunk_in_same_file(tmp_path):
    from types import SimpleNamespace
    from reachpatch.execution.worktree import copy_source_tree, diff_between
    from reachpatch.repair.execution_objective import InitialPatchObjective
    from reachpatch.repair.execution_tools import RepairToolExecutor

    clean = tmp_path / "clean"
    parent = tmp_path / "parent"
    clean.mkdir()
    (clean / "pkg.py").write_text(
        "import alpha\n\ndef target():\n    return 0\n", encoding="utf-8",
    )
    copy_source_tree(clean, parent)
    (parent / "pkg.py").write_text(
        "import alpha\n\ndef target():\n    return 1\n", encoding="utf-8",
    )
    state = SimpleNamespace(
        clean_snapshot=clean, run_root=tmp_path,
        working_checkpoint=SimpleNamespace(snapshot_tree=str(parent)),
    )
    objective = InitialPatchObjective("initial", (), (), "", "hash")
    tools = RepairToolExecutor(parent, state, objective)

    tools.apply_patch(
        "*** Begin Patch\n*** Update File: pkg.py\n@@\n-import alpha\n+import alpha\n+import beta\n*** End Patch"
    )

    assert (parent / "pkg.py").read_text(encoding="utf-8") == (
        "import alpha\nimport beta\n\ndef target():\n    return 1\n"
    )
    full = diff_between(clean, parent).canonical_diff
    assert "+import beta" in full and "+    return 1" in full
