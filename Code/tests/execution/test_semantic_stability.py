from pathlib import Path

from reachpatch.execution.checks import ExecutionStatus, execute_check, semantic_observation_signature
from reachpatch.execution.trace import run_trace
from dataclasses import replace

from reachpatch.models.evidence import ExecutableCheck, ObservationContract, OutcomeStatus, RunObservation, TraceBundle


def _check(tmp_path, comparator, expected):
    return ExecutableCheck(
        check_id="check", command=("python", "-c", "print(3)"),
        role="TARGET", authority="A", cwd=".",
        expected=ObservationContract("contract", expected, comparator=comparator),
    )


def test_equals_value_changes_are_not_stable(tmp_path):
    tree = tmp_path / "tree"; tree.mkdir()
    check = _check(tree, "EQUALS", 3)
    first = run_trace(tree, check.command, trace_enabled=False)
    second = run_trace(tree, ("python", "-c", "print(4)"), trace_enabled=False)
    assert semantic_observation_signature(first.observation, check) != semantic_observation_signature(second.observation, check)


def test_ansi_and_temp_noise_do_not_change_exit_zero_signature(tmp_path):
    tree = tmp_path / "tree"; tree.mkdir()
    check = ExecutableCheck(
        check_id="exit", command=("python", "-c", "print('ok')"),
        role="TARGET", authority="A", expected=ObservationContract("ok", {"exit_code": 0}, comparator="EXIT_ZERO"),
    )
    one = run_trace(tree, check.command, trace_enabled=False)
    two = run_trace(tree, check.command, trace_enabled=False)
    assert semantic_observation_signature(one.observation, check) == semantic_observation_signature(two.observation, check)


def test_execute_check_uses_first_trace_metadata(tmp_path):
    tree = tmp_path / "tree"; tree.mkdir()
    (tree / "api.py").write_text("def value():\n    return 3\n", encoding="utf-8")
    check = ExecutableCheck(
        check_id="trace", command=("python", "-c", "from api import value; assert value() == 3"),
        role="TARGET", authority="A", expected=ObservationContract("pass", {"exit_code": 0}, comparator="EXIT_ZERO"),
    )
    result = execute_check(tree, check, stability_runs=2)
    assert result.status == ExecutionStatus.PASS
    assert result.stable and result.entered_project_code
    assert result.trace is not None and result.trace.executed_line_ids


def test_run_trace_does_not_reuse_same_size_stale_bytecode(tmp_path):
    tree = tmp_path / "tree"; tree.mkdir()
    source = tree / "api.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    command = ("python", "-c", "from api import value; print(value())")

    first = run_trace(tree, command, trace_enabled=False)
    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    second = run_trace(tree, command, trace_enabled=False)

    assert first.observation.stdout.strip() == "1"
    assert second.observation.stdout.strip() == "2"
    assert not (tree / "__pycache__").exists()


def test_execute_check_overlays_files_changed_from_base(tmp_path, monkeypatch):
    base = tmp_path / "base"; base.mkdir()
    tree = tmp_path / "tree"; tree.mkdir()
    (base / "api.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tree / "api.py").write_text("VALUE = 2\n", encoding="utf-8")
    seen = []

    def fake_trace(root, command, **kwargs):
        seen.append(tuple(kwargs["overlay_paths"]))
        return TraceBundle(
            "trace", "tree", tuple(command),
            RunObservation(OutcomeStatus.PASS, 0, "", "", 0.01),
            ("api.VALUE",), ("api.py:1",), ("api.py:1",),
            first_project_frame="api.py:1",
        )

    monkeypatch.setattr("reachpatch.execution.checks.run_trace", fake_trace)
    check = ExecutableCheck(
        check_id="overlay", command=("python", "-c", "pass"),
        role="TARGET", authority="A",
        expected=ObservationContract("pass", {"exit_code": 0}, comparator="EXIT_ZERO"),
    )
    result = execute_check(tree, check, stability_runs=2, base_tree=base)

    assert result.status == ExecutionStatus.PASS
    assert seen == [("api.py",), ("api.py",)]


def test_exit_zero_ignores_runtime_ansi_temp_path_and_pid_noise(tmp_path):
    check = ExecutableCheck(
        check_id="noise", command=("python",), role="TARGET", authority="A",
        expected=ObservationContract("succeeds", {"exit_code": 0}, comparator="EXIT_ZERO"),
    )
    first = RunObservation(OutcomeStatus.PASS, 0, "ok in 0.001s pid=12 /tmp/a \x1b[31mred\x1b[0m", "", 0.001)
    second = replace(first, stdout="ok in 0.000s pid=99 /tmp/b red", duration_seconds=0.0001)
    assert semantic_observation_signature(first, check) == semantic_observation_signature(second, check)


def test_raises_signature_compares_expected_exception_type():
    check = ExecutableCheck(
        check_id="raises", command=("python",), role="TARGET", authority="B",
        expected=ObservationContract("raises", {"exception_type": "ValueError"}, comparator="RAISES"),
    )
    value = RunObservation(OutcomeStatus.FAIL, 1, "", "ValueError: bad", 0.1, exception="ValueError: bad")
    other = replace(value, stderr="TypeError: bad", exception="TypeError: bad")
    assert semantic_observation_signature(value, check) != semantic_observation_signature(other, check)
