from reachpatch.execution.mechanical import run_mechanical_checks
from reachpatch.execution.target_recovery import recover_target_scenarios
from reachpatch.execution.worktree import diff_between
from reachpatch.models.evidence import public_evidence_from_instance
from reachpatch.models.graphs import ExecutableScenario
from reachpatch.requirement_graph.builder import build_requirement_graph


def _recover(tmp_path, issue, checks):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "api.py").write_text("def value(x):\n    return x\n", encoding="utf-8")
    evidence = public_evidence_from_instance(issue, (), {"public_checks": checks}, repository)
    requirements = build_requirement_graph(issue, evidence)
    return repository, evidence, recover_target_scenarios(
        repository, repository, repository, requirements, None, evidence, None,
        tmp_path / "run",
    )


def test_compileall_and_ast_are_mechanical_only(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "api.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    scenario = ExecutableScenario("compile", ("python", "-m", "compileall", "-q", "api.py"), ".", (), 10.0)
    result = run_mechanical_checks(tree, diff_between(tree, tree), command_scenarios=(scenario,))
    assert result.passed
    assert result.command_results


def test_public_check_assertion_is_authority_a_target(tmp_path):
    _, _, recovery = _recover(
        tmp_path,
        "\x60value\x60 must return 2.",
        ({"check_id": "target", "command": ("python", "-c", "from api import value; assert value(1) == 2"), "role": "TARGET", "authority": "A", "symbol_references": ("value",)},),
    )
    assert recovery.challenge_cells
    cell = recovery.challenge_cells[0]
    assert cell.authority == "A"
    assert cell.terminal_status.value == "FAIL"
    assert cell.hard


def test_issue_expected_exception_is_authority_b(tmp_path):
    issue = "The call must raise ValueError."
    _, _, recovery = _recover(
        tmp_path, issue,
        ({"check_id": "target", "command": ("python", "-c", "raise ValueError('bad')"), "role": "TARGET", "authority": "B", "symbol_references": ("value",)},),
    )
    assert any(cell.authority == "B" for cell in recovery.challenge_cells)


def test_baseline_preservation_check_is_authority_c(tmp_path):
    _, _, recovery = _recover(
        tmp_path,
        "\x60value\x60 must return 1.",
        ({"check_id": "preserve", "command": ("python", "-c", "from api import value; assert value(1) == 1"), "role": "PRESERVATION", "authority": "C", "symbol_references": ("value",)},),
    )
    assert any(cell.kind == "PRESERVATION" and cell.authority == "C" for cell in recovery.challenge_cells)


def test_environment_initialization_is_blocked(tmp_path):
    _, _, recovery = _recover(
        tmp_path,
        "\x60value\x60 must return 2.",
        ({"check_id": "target", "command": ("python", "-c", "import package_that_does_not_exist"), "role": "TARGET", "authority": "A", "symbol_references": ("value",)},),
    )
    assert not recovery.challenge_cells
    assert any(item.reason == "ENVIRONMENT_BLOCKED" for item in recovery.rejected_candidates)


def test_repeated_baseline_failure_is_stable_and_executable(tmp_path):
    _, _, recovery = _recover(
        tmp_path,
        "\x60value\x60 must return 2.",
        ({"check_id": "target", "command": ("python", "-c", "from api import value; assert value(1) == 2"), "role": "TARGET", "authority": "A", "symbol_references": ("value",)},),
    )
    assert recovery.challenge_cells[0].stability_runs == 2
    assert recovery.challenge_cells[0].terminal_status.value == "FAIL"


def test_duplicate_target_commands_are_deduplicated(tmp_path):
    _, _, recovery = _recover(
        tmp_path,
        "\x60value\x60 must return 2.",
        (
            {"check_id": "target-a", "command": ("python", "-c", "from api import value; assert value(1) == 2"), "role": "TARGET", "authority": "A", "symbol_references": ("value",)},
            {"check_id": "target-b", "command": ("python", "-c", "from api import value; assert value(1) == 2"), "role": "TARGET", "authority": "A", "symbol_references": ("value",)},
        ),
    )
    assert len(recovery.challenge_cells) == 1


def test_no_oracle_candidate_is_not_certified(tmp_path):
    _, _, recovery = _recover(tmp_path, "The implementation needs work.", ())
    assert not recovery.challenge_cells
    assert not recovery.scenarios


def test_blocked_candidates_do_not_become_target_results(tmp_path):
    _, _, recovery = _recover(
        tmp_path,
        "\x60value\x60 must return 2.",
        ({"check_id": "target", "command": ("python", "-c", "import package_that_does_not_exist"), "role": "TARGET", "authority": "A", "symbol_references": ("value",)},),
    )
    assert not recovery.challenge_cells
