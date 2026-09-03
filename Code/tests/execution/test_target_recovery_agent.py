from pathlib import Path

import pytest

from reachpatch.execution.target_recovery import (
    TARGET_RECOVERY_TOOL_SCHEMAS, TargetRecoveryAgent,
    TargetRecoveryToolExecutor,
)
from reachpatch.models.evidence import public_evidence_from_instance
from reachpatch.requirement_graph.builder import build_requirement_graph


class _Transport:
    def __init__(self, calls):
        self.calls = iter(calls)

    def complete(self, messages, **kwargs):
        assert kwargs["tool_choice"] == "required"
        assert {item["function"]["name"] for item in kwargs["tools"]} == {
            item["function"]["name"] for item in TARGET_RECOVERY_TOOL_SCHEMAS
        }
        return next(self.calls)


def _executor(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "api.py").write_text("def value(x):\n    return x\n", encoding="utf-8")
    evidence = public_evidence_from_instance("`value` must return 2.", (), {}, repo)
    graph = build_requirement_graph("`value` must return 2.", evidence)
    return TargetRecoveryToolExecutor(
        repo_root=repo, clean_snapshot=repo, working_snapshot=repo,
        requirement_graph=graph, program_slice=None, run_root=tmp_path / "run",
    )


def test_target_recovery_exposes_only_restricted_tools(tmp_path):
    executor = _executor(tmp_path)
    assert set(executor.allowed_tool_names) == {
        "search_source", "read_source", "write_probe",
        "run_probe_on_clean", "run_probe_on_working",
        "register_observation_contract", "finish_target_recovery",
    }
    with pytest.raises(ValueError):
        executor.invoke("apply_patch", {"patch": "..."})


def test_agent_contract_stays_provisional_without_public_evidence(tmp_path):
    executor = _executor(tmp_path)
    probe = executor.write_probe("probe", "from api import value\nprint(value(1))\n")
    registered = executor.register_observation_contract(
        probe["probe_id"],
        {"comparator": "EQUALS", "expected": 2, "observable": "stdout"},
        authority="A",
    )
    assert registered["authority"] == "PROVISIONAL"


def test_probe_command_is_self_contained_for_isolated_execution(tmp_path):
    executor = _executor(tmp_path)
    probe = executor.write_probe(
        "probe", "from api import value\nprint(value(1))\n",
    )

    executor.run_probe_on_clean(probe["probe_id"])

    trace = executor.probes[probe["probe_id"]].clean_runs[0]
    assert trace.command[:2] == ("python", "-c")
    assert str(executor.probes[probe["probe_id"]].source_path) not in trace.command


def test_agent_uses_required_tool_choice_and_finishes(tmp_path):
    executor = _executor(tmp_path)
    transport = _Transport([
        {"tool_calls": [{"id": "1", "function": {"name": "finish_target_recovery", "arguments": "{}"}}]},
    ])
    events = TargetRecoveryAgent(transport, max_turns=2, timeout_seconds=5).recover(executor, {})
    assert executor.finished
    assert any(event["tool"] == "finish_target_recovery" for event in events)
