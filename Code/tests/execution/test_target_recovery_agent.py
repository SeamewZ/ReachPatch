from pathlib import Path
from types import SimpleNamespace

import pytest

from reachpatch.execution.target_recovery import (
    TARGET_RECOVERY_TOOL_SCHEMAS, TargetRecoveryAgent,
    TargetRecoveryToolExecutor,
)
from reachpatch.models.evidence import public_evidence_from_instance
from reachpatch.models.evidence import OutcomeStatus
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


def test_agent_pairs_every_multi_tool_call_before_next_turn(tmp_path):
    executor = _executor(tmp_path)

    class _PairingTransport:
        def __init__(self):
            self.messages = []
            self.turn = 0

        def complete(self, messages, **kwargs):
            self.messages.append(tuple(messages))
            self.turn += 1
            if self.turn == 1:
                return {"role": "assistant", "tool_calls": [
                    {"id": "search-1", "function": {
                        "name": "search_source", "arguments": '{"symbol":"value"}',
                    }},
                    {"id": "finish-1", "function": {
                        "name": "finish_target_recovery", "arguments": "{}",
                    }},
                ]}
            return {"role": "assistant", "tool_calls": [{
                "id": "finish-2", "function": {
                    "name": "finish_target_recovery", "arguments": "{}",
                },
            }]}

    transport = _PairingTransport()
    TargetRecoveryAgent(transport, max_turns=3, timeout_seconds=5).recover(executor, {})

    assert len(transport.messages) >= 2
    first = transport.messages[1]
    assistant_index = next(
        index for index, message in enumerate(first)
        if message.get("role") == "assistant"
        and len(message.get("tool_calls") or ()) == 2
    )
    following = first[assistant_index + 1:assistant_index + 3]
    assert [message.get("tool_call_id") for message in following] == [
        "search-1", "finish-1",
    ]
    assert following[1]["content"]
    assert executor.finished


def test_blocked_trace_attempt_does_not_consume_stability_sample(tmp_path, monkeypatch):
    executor = _executor(tmp_path)
    probe = executor.write_probe("probe", "from api import value\nassert value(1) == 2\n")
    statuses = iter((OutcomeStatus.BLOCKED, OutcomeStatus.FAIL, OutcomeStatus.FAIL))
    trace_modes = []

    def fake_run_trace(tree, command, **kwargs):
        trace_modes.append(kwargs["trace_enabled"])
        status = next(statuses)
        observation = SimpleNamespace(
            status=status, return_code=None if status is OutcomeStatus.BLOCKED else 1,
            stdout="", stderr="", duration_seconds=0.01,
            value=None, exception="TIMEOUT" if status is OutcomeStatus.BLOCKED else None,
            to_dict=lambda: {"status": status.value},
        )
        return SimpleNamespace(
            observation=observation, first_project_frame=(
                None if status is OutcomeStatus.BLOCKED else "api.py:1"
            ), trace_bundle_id=f"trace-{status.value}", command=command,
        )

    monkeypatch.setattr(
        "reachpatch.execution.target_recovery.run_trace", fake_run_trace,
    )
    first = executor.run_probe_on_clean(probe["probe_id"])
    second = executor.run_probe_on_clean(probe["probe_id"])
    third = executor.run_probe_on_clean(probe["probe_id"])

    assert not first["counts_toward_stability"]
    assert second["run_index"] == 1
    assert third["run_index"] == 2
    assert len(executor.probes[probe["probe_id"]].clean_runs) == 2
    assert trace_modes == [True, False, False]


def test_recovery_protocol_registers_contract_before_paired_runs(tmp_path):
    executor = _executor(tmp_path)

    class ProtocolTransport:
        def __init__(self):
            self.forced = []

        def complete(self, messages, **kwargs):
            import json

            choice = kwargs["tool_choice"]
            forced = (
                choice["function"]["name"]
                if isinstance(choice, dict) else "write_probe"
            )
            self.forced.append(forced)
            probe_id = None
            for message in reversed(messages):
                if message.get("role") != "tool":
                    continue
                payload = json.loads(message["content"])
                result = payload.get("result") or payload
                if result.get("probe_id"):
                    probe_id = result["probe_id"]
                    break
            arguments = {
                "write_probe": {
                    "name": "value_contract.py",
                    "source": "from api import value\nassert value(1) == 2\n",
                },
                "register_observation_contract": {
                    "probe_id": probe_id,
                    "contract": {
                        "comparator": "EXIT_ZERO",
                        "expected": {"exit_code": 0},
                        "observable": "process",
                    },
                    "authority": "B",
                },
                "run_probe_on_clean": {"probe_id": probe_id},
                "run_probe_on_working": {"probe_id": probe_id},
                "finish_target_recovery": {"summary": "complete"},
            }[forced]
            return {"tool_calls": [{
                "id": f"call-{len(self.forced)}",
                "function": {"name": forced, "arguments": json.dumps(arguments)},
            }]}

    transport = ProtocolTransport()
    TargetRecoveryAgent(
        transport, max_turns=8, timeout_seconds=30,
    ).recover(executor, {})

    assert transport.forced == [
        "write_probe", "register_observation_contract",
        "run_probe_on_clean", "run_probe_on_clean",
        "run_probe_on_working", "run_probe_on_working",
        "finish_target_recovery",
    ]
    recovered = next(iter(executor.probes.values()))
    assert recovered.contract is not None
    assert len(recovered.clean_runs) == 2
    assert len(recovered.working_runs) == 2
