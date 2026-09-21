import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from experiments.evidence_study.analysis import bootstrap_cost_ratio, missing_outcome_bounds, bootstrap_factorial, distribution
from experiments.evidence_study.protocol import build_block_schedule, make_protocol, validate_protocol
from experiments.evidence_study.run import evaluate
from reachpatch.execution.case_budget import BudgetedTransport, CaseBudget
from reachpatch.execution.request_journal import read_events, reconcile_requests
from reachpatch.reach_avoid.controller import ReachAvoidConfig
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import DynamicReachAvoidGraph, CheckpointState, GraphNodeKind
from reachpatch.reach_avoid.evidence_context import (
    claim_evidence_action, evidence_action_execution, compact_evidence_context, read_evidence,
)


def test_block_schedule_balanced_and_reproducible():
    rows = build_block_schedule(["a", "b"], ["F00", "F01", "F10", "F11"], 3, 42)
    assert rows == build_block_schedule(["b", "a"], ["F11", "F10", "F00", "F01"], 3, 42)
    assert len(rows) == 24
    for block in range(6):
        assert len({r["arm"] for r in rows if r["block"] == block}) == 4


def test_frozen_arms_only_differ_by_interventions():
    protocol = make_protocol("public_smoke", 1, 3, 42)
    validate_protocol(protocol)
    protocol["arms"]["F11"]["config"]["max_case_tokens"] += 1
    with pytest.raises(ValueError, match="non-intervention"):
        validate_protocol(protocol)


def test_process_death_preserves_unknown_request(tmp_path):
    journal = tmp_path / "request.jsonl"
    source = '''
import os, sys
from pathlib import Path
from reachpatch.execution.case_budget import CaseBudget, BudgetedTransport
class Provider:
    def complete(self, *args, **kwargs):
        os._exit(17)
budget = CaseBudget(100, max_tokens=1000, final_validation_reserve=0, journal_path=Path(sys.argv[1]))
BudgetedTransport(Provider(), budget).complete([{"content": "q"}], tools=(), max_tokens=10)
'''
    assert subprocess.run([sys.executable, "-c", source, str(journal)]).returncode == 17
    events, tail = read_events(journal)
    report = reconcile_requests(events + events)
    assert tail == 0 and report["model_calls"] == 1
    assert report["unknown_usage_requests"] == 1
    assert report["requests"][0]["provider_tokens"] is None
    assert report["requests"][0]["status"] == "UNKNOWN_INTERRUPTED"


def test_success_error_and_reservations_are_separate(tmp_path):
    class Provider:
        count = 0
        def complete(self, *args, **kwargs):
            self.count += 1
            if self.count == 2:
                raise TimeoutError("temporary")
            return {"usage": {"total_tokens": 12}, "_model": "actual-flash-id"}
    budget = CaseBudget(100, max_tokens=1000, final_validation_reserve=0,
                        journal_path=tmp_path / "requests.jsonl")
    transport = BudgetedTransport(Provider(), budget)
    transport.complete([{"content": "q"}], tools=(), max_tokens=10)
    with pytest.raises(TimeoutError):
        transport.complete([{"content": "q"}], tools=(), max_tokens=10)
    report = reconcile_requests(read_events(budget.journal_path)[0])
    assert report["reported_tokens_lower_bound"] == 12
    assert report["unknown_usage_requests"] == 1
    assert report["reservation_tokens"] != 12
    assert report["requests"][0]["provider_model"] == "actual-flash-id"


def test_incomplete_final_record_only_is_recoverable(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"event_id":"1","kind":"CASE_STARTED"}\n{"interrupted"')
    assert read_events(path)[1] == 1
    path.write_bytes(b'not-json\n')
    with pytest.raises(ValueError, match="corrupt"):
        read_events(path)


def test_independent_reuse_controls_activate():
    config = ReachAvoidConfig(compact_duplicate_payloads=False, reuse_source_evidence=False)
    graph = DynamicReachAvoidGraph()
    graph.register_checkpoint(CheckpointState("cp", None, "diff", "hash"))
    graph.add_node(GraphNodeKind.OBSERVATION, node_id="evidence-policy", metadata=config.evidence_policy())
    assert claim_evidence_action(graph, "cp", "REPAIR", "question", {"x": 1})
    assert not claim_evidence_action(graph, "cp", "REPAIR", "question", {"x": 1})
    messages = [{"role": "tool", "tool_call_id": str(i), "content": "x" * 1000} for i in range(2)]
    assert compact_evidence_context(graph, messages, scope="s").sent_bytes == compact_evidence_context(graph, messages, scope="s").original_bytes
    read_evidence(graph, "a.py", "v1", 1, 2, {"source": "one"})
    read_evidence(graph, "a.py", "v1", 1, 2, {"source": "one"})
    assert any(r["event"] == "SOURCE_READ_REPEATED" for r in graph.update_log)
    assert not any(r["event"] == "SOURCE_READ_REUSED" for r in graph.update_log)
    assert config.evidence_policy()["reuse_validation_results"]


def test_retryable_action_is_not_completed_or_retried_forever():
    graph = DynamicReachAvoidGraph()
    graph.register_checkpoint(CheckpointState("cp", None, "diff", "hash"))
    for _ in range(2):
        assert claim_evidence_action(graph, "cp", "REPAIR", "question", {"x": 1})
        with pytest.raises(TimeoutError):
            with evidence_action_execution(graph, "cp", "REPAIR"):
                raise TimeoutError("transient")
    assert not claim_evidence_action(graph, "cp", "REPAIR", "question", {"x": 1})
    assert claim_evidence_action(graph, "cp", "REPAIR", "question", {"x": 2})
    with evidence_action_execution(graph, "cp", "REPAIR"):
        assert True
    assert not claim_evidence_action(graph, "cp", "REPAIR", "question", {"x": 2})


def test_harness_cannot_start_before_global_seal(tmp_path):
    with pytest.raises(RuntimeError, match="all study cells"):
        evaluate(tmp_path, {"scope": "development"})


def test_statistics_hand_computable_and_missing_are_not_success():
    result = bootstrap_cost_ratio([(100, 50), (200, 100)], samples=100)
    assert result["reduction"] == .5 and result["ci95"] == [.5, .5]
    assert bootstrap_cost_ratio([(0, 0)])["reduction"] is None
    assert bootstrap_cost_ratio([(100, 50)])["ci95"] is None
    assert missing_outcome_bounds([True, False, None]) == {
        "n": 3, "resolved": 1, "missing": 1, "lower": 1/3, "upper": 2/3}
    contrasts = bootstrap_factorial([(100,80,90,60),(100,80,90,60)], samples=100)
    assert contrasts["effects"]["interaction"] == {"mean_tokens_per_issue": -10, "ci95": [-10,-10]}
    assert bootstrap_factorial([(100,80,90,60)], samples=100)["effects"]["interaction"]["ci95"] is None
    assert distribution([1,3])["p50"] == 2
    assert distribution([])["mean"] is None


@pytest.mark.parametrize("spec", ["test_pkg.py", "python -m pytest -q test_pkg.py"])
def test_visible_command_is_not_passed_as_pytest_filename(tmp_path, spec):
    from reachpatch.models.evidence import public_evidence_from_instance
    (tmp_path / "test_pkg.py").write_text("from pkg import target\ndef test_target(): assert target([1]) == [1]\n")
    evidence = public_evidence_from_instance("target should work", (spec,), {}, tmp_path)
    assert evidence.checks[0].command == ("python", "-m", "pytest", "-q", "test_pkg.py")
    assert "target" in evidence.checks[0].symbol_references


def test_missing_source_tool_cannot_silently_certify(tmp_path, monkeypatch):
    from reachpatch.reach_avoid.controller import ReachAvoidController
    from reachpatch.reach_avoid.repair_player import RepairPlayer
    from reachpatch.models.core import Instance
    from types import SimpleNamespace
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("reachpatch.reach_avoid.controller.shutil.which", lambda _: None)
    controller = ReachAvoidController(RepairPlayer(SimpleNamespace()))
    with pytest.raises(RuntimeError, match="SOURCE_DISCOVERY_UNAVAILABLE"):
        controller.run_case(Instance("id", str(repo), "base", "issue"), run_root=tmp_path / "run")
    assert (tmp_path / "run/request_journal.jsonl").is_file()
