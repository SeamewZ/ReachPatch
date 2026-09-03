from pathlib import Path

from reachpatch.execution.mechanical import run_mechanical_checks
from reachpatch.execution.target_recovery import (
    TargetRecoveryConfig, recover_target_checks,
)
from reachpatch.execution.worktree import diff_between
from reachpatch.models.evidence import public_evidence_from_instance
from reachpatch.requirement_graph.compiler import compile_goal_contracts


def _recover(tmp_path: Path, issue: str, checks=(), *, max_probes: int = 6):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "api.py").write_text(
        "def value(x):\n    return x\n", encoding="utf-8",
    )
    evidence = public_evidence_from_instance(
        issue, (), {"public_checks": checks}, repository,
    )
    goals = compile_goal_contracts(issue, evidence, (), None, tmp_path / "compile")
    result = recover_target_checks(
        repository, repository, repository, goals, evidence, None,
        tmp_path / "run", TargetRecoveryConfig(max_probes=max_probes),
    )
    return repository, evidence, goals, result


def test_public_candidate_clean_fail_twice_becomes_target_a(tmp_path):
    _, _, _, result = _recover(
        tmp_path, "`value` must return 2.", ({
            "check_id": "target",
            "command": ("python", "-c", "from api import value; print(value(1))"),
            "role": "TARGET", "authority": "A", "symbol_references": ("value",),
            "expected": 2,
        },),
    )
    assert len(result.target_checks) == 1
    check = result.target_checks[0]
    assert check.role.value == "TARGET"
    assert check.authority == "A"
    assert check.comparator == "EQUALS"


def test_public_candidate_clean_pass_becomes_preservation(tmp_path):
    _, _, _, result = _recover(
        tmp_path, "`value` must return 2.", ({
            "check_id": "preserve",
            "command": ("python", "-c", "from api import value; print(value(1))"),
            "role": "TARGET", "authority": "A", "symbol_references": ("value",),
            "expected": 1,
        },),
    )
    assert not result.target_checks
    assert len(result.preservation_checks) == 1
    assert result.preservation_checks[0].role.value == "PRESERVATION"


def test_diff_related_public_test_is_discovered_and_baseline_classified(tmp_path):
    clean = tmp_path / "clean"
    working = tmp_path / "working"
    clean.mkdir(); working.mkdir()
    issue = "`calc` must return 2."
    test_source = "from api import calc\n\ndef test_calc():\n    assert calc() == 2\n"
    (clean / "api.py").write_text(
        "def calc():\n    return 1\n", encoding="utf-8",
    )
    (working / "api.py").write_text(
        "def calc():\n    return 2\n", encoding="utf-8",
    )
    (clean / "test_calc.py").write_text(test_source, encoding="utf-8")
    (working / "test_calc.py").write_text(test_source, encoding="utf-8")
    evidence = public_evidence_from_instance(issue, (), {}, clean)
    goals = compile_goal_contracts(
        issue, evidence, (), None, tmp_path / "compile",
    )

    result = recover_target_checks(
        clean, clean, working, goals, evidence, None, tmp_path / "run",
        TargetRecoveryConfig(max_probes=6),
    )

    assert len(result.target_checks) == 1
    check = result.target_checks[0]
    assert check.command[-1] == "test_calc.py::test_calc"
    assert check.comparator == "EXIT_ZERO"
    assert check.role.value == "TARGET"


def test_issue_expected_exception_becomes_target_b(tmp_path):
    _, _, _, result = _recover(
        tmp_path, "The `value` call must raise ValueError.", ({
            "check_id": "target",
            "command": ("python", "-c", "from api import value; value(1)"),
            "role": "TARGET", "authority": "B", "symbol_references": ("value",),
        },),
    )
    assert len(result.target_checks) == 1
    assert result.target_checks[0].authority == "B"
    assert result.target_checks[0].comparator == "RAISES"
    assert result.target_checks[0].expected == {"exception_type": "ValueError"}


def test_environment_initialization_failure_is_blocked(tmp_path):
    _, _, _, result = _recover(
        tmp_path, "`value` must return 2.", ({
            "check_id": "target",
            "command": ("python", "-c", "import package_that_does_not_exist"),
            "role": "TARGET", "authority": "A", "symbol_references": ("value",),
        },),
    )
    assert not result.target_checks
    assert result.blocked_candidates
    assert result.blocked_candidates[0].reason == "BLOCKED"


def test_duplicate_commands_are_deduplicated(tmp_path):
    command = ("python", "-c", "from api import value; print(value(1))")
    _, _, _, result = _recover(
        tmp_path, "`value` must return 2.", (
            {"check_id": "a", "command": command, "authority": "A", "symbol_references": ("value",), "expected": 2},
            {"check_id": "b", "command": command, "authority": "A", "symbol_references": ("value",), "expected": 2},
        ),
    )
    assert len(result.target_checks) == 1
    assert any(item.reason == "DUPLICATE_COMMAND" for item in result.rejected_candidates)


def test_compileall_is_not_an_executable_target(tmp_path):
    repository = tmp_path / "tree"
    repository.mkdir()
    (repository / "api.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    mechanical = run_mechanical_checks(repository, diff_between(repository, repository))
    assert mechanical.passed
    _, _, _, result = _recover(
        tmp_path, "`value` must return 2.", ({
            "check_id": "compile",
            "command": ("python", "-m", "compileall", "-q", "api.py"),
            "role": "MECHANICAL", "authority": "A", "symbol_references": (),
        },),
    )
    assert not result.target_checks
    assert not result.preservation_checks


def test_preservation_candidates_do_not_consume_target_probe_quota(tmp_path):
    _, _, _, result = _recover(
        tmp_path, "`value` must return 2.", (
            {"check_id": "preserve", "command": ("python", "-c", "from api import value; print(value(1))"), "authority": "A", "symbol_references": ("value",), "expected": 1},
            {"check_id": "target", "command": ("python", "-c", "from api import value; print(value(1) + 0)"), "authority": "A", "symbol_references": ("value",), "expected": 2},
        ), max_probes=1,
    )
    assert len(result.preservation_checks) == 1
    assert len(result.target_checks) == 1


def test_no_oracle_candidate_reports_unresolved_goal(tmp_path):
    _, _, goals, result = _recover(tmp_path, "The implementation needs improvement.")
    assert not result.target_checks
    assert result.unresolved_goal_ids == tuple(
        goal.goal_id for goal in goals if goal.hard
    )


def test_provisional_clean_pass_is_not_preservation_oracle(tmp_path):
    _, _, _, result = _recover(
        tmp_path, "`value` must return 2.", ({
            "check_id": "provisional",
            "command": ("python", "-c", "from api import value; print(value(1))"),
            "role": "TARGET", "authority": "PROVISIONAL",
            "symbol_references": ("value",), "expected": 1,
        },),
    )
    assert not result.preservation_checks
    assert any(
        item.reason == "PRESERVATION_ORACLE_PROVISIONAL"
        for item in result.rejected_candidates
    )
