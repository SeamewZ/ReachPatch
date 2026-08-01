from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from reachpatch.execution.models import (
    CheckStatus,
    EXECUTED_SYMBOLS_MARKER,
    EnvironmentHealthStatus,
)
from reachpatch.execution.public_docker import (
    PublicDockerExecutionBroker,
    public_swebench_image,
)
from reachpatch.execution.runners import (
    DjangoRunner,
    PytestProjectRunner,
    SymPyRunner,
)


def _runner(cls, repository: Path, artifact_root: Path, **kwargs):
    return cls(
        repository,
        artifact_root=artifact_root,
        base_commit="base-commit",
        **kwargs,
    )


def test_django_runner_executes_project_runtests_label(tmp_path):
    repository = tmp_path / "django-repo"
    (repository / "tests" / "sample").mkdir(parents=True)
    (repository / "django").mkdir()
    (repository / "tests" / "sample" / "test_case.py").write_text(
        "def test_value():\n    assert True\n", encoding="utf-8",
    )
    (repository / "tests" / "runtests.py").write_text(
        "import sys\n"
        "assert sys.argv[1:] == "
        "['sample.test_case.test_value', '--parallel', '1'], sys.argv\n",
        encoding="utf-8",
    )
    runner = _runner(DjangoRunner, repository, tmp_path / "artifacts")

    check = runner.compile_visible_checks((
        "tests/sample/test_case.py::test_value",
    ))[0]
    execution = runner.run_check(check)

    assert check.command == (
        sys.executable, "tests/runtests.py", "sample.test_case.test_value",
        "--parallel", "1",
    )
    assert execution.status == CheckStatus.PASS
    assert execution.stable


def test_sympy_runner_resolves_bare_function_and_uses_bin_test(tmp_path):
    repository = tmp_path / "sympy-repo"
    (repository / "sympy" / "functions" / "tests").mkdir(parents=True)
    (repository / "bin").mkdir()
    test_path = repository / "sympy" / "functions" / "tests" / "test_bell.py"
    test_path.write_text(
        "def test_bell():\n    assert True\n", encoding="utf-8",
    )
    (repository / "bin" / "test").write_text(
        "import sys\n"
        "assert sys.argv[1:] == "
        "['sympy/functions/tests/test_bell.py', '-k', 'test_bell'], sys.argv\n",
        encoding="utf-8",
    )
    runner = _runner(SymPyRunner, repository, tmp_path / "artifacts")

    check = runner.compile_visible_checks(("test_bell",))[0]
    execution = runner.run_check(check)

    assert check.selector == "sympy/functions/tests/test_bell.py::test_bell"
    assert check.command[1:3] == ("bin/test", "sympy/functions/tests/test_bell.py")
    assert execution.status == CheckStatus.PASS


def test_pytest_runner_executes_selector_in_isolated_environment(tmp_path):
    repository = tmp_path / "pytest-repo"
    repository.mkdir()
    (repository / "test_environment.py").write_text(
        "import os\n"
        "from pathlib import Path\n\n"
        "def test_isolated_environment():\n"
        "    for key in ('HOME', 'XDG_CACHE_HOME', 'MPLCONFIGDIR', 'TMPDIR'):\n"
        "        path = Path(os.environ[key])\n"
        "        assert path.is_dir()\n"
        "        assert os.access(path, os.W_OK)\n"
        "    assert os.environ['PYTHONHASHSEED'] == '0'\n"
        "    assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'\n"
        "    assert os.environ['LANG'] == 'C.UTF-8'\n"
        "    assert os.environ['LC_ALL'] == 'C.UTF-8'\n"
        "    assert __import__('sys').getfilesystemencoding().lower() == 'utf-8'\n"
        "    (Path(os.environ['TMPDIR']) / '\u2297.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    first = runner.prepare_environment("one")
    second = runner.prepare_environment("one")

    check = runner.compile_visible_checks(("test_environment.py",))[0]
    execution = runner.run_check(check)

    assert first.run_directory != second.run_directory
    assert execution.status == CheckStatus.PASS
    assert execution.stable


def test_health_check_distinguishes_missing_dependency_and_invalid_selector(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    missing = runner.compile_command_checks(((
        sys.executable, "-c", "import reachpatch_dependency_that_does_not_exist",
    ),))[0]
    invalid = runner.compile_visible_checks(("missing_test.py::test_missing",))[0]

    missing_health = runner.health_check(missing)
    invalid_health = runner.health_check(invalid)

    assert missing_health.status == EnvironmentHealthStatus.DEPENDENCY_MISSING
    assert missing_health.execution.status == CheckStatus.INVALID_ENVIRONMENT
    assert invalid_health.status == EnvironmentHealthStatus.INVALID_SELECTOR
    assert invalid_health.execution.status == CheckStatus.INVALID_SELECTOR


def test_unittest_zero_tests_is_an_invalid_selector(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    check = runner.compile_command_checks(((
        sys.executable, "-c",
        "import sys; sys.stderr.write('Ran 0 tests in 0.000s\\n')",
    ),))[0]

    health = runner.health_check(check)

    assert health.status == EnvironmentHealthStatus.INVALID_SELECTOR
    assert health.execution.status == CheckStatus.INVALID_SELECTOR


def test_required_database_backend_is_an_environment_frontier(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    check = runner.compile_command_checks(((
        sys.executable, "-c",
        "import sys; print('A GIS database backend is required'); sys.exit(1)",
    ),))[0]

    health = runner.health_check(check)

    assert health.status == EnvironmentHealthStatus.EXTERNAL_SERVICE_REQUIRED
    assert health.execution.status == CheckStatus.INVALID_ENVIRONMENT


def test_failure_signature_ignores_traceback_line_number_only() -> None:
    first = PytestProjectRunner._failure_signature(
        CheckStatus.FAIL,
        1,
        "",
        'Traceback:\n  File "/repo/pkg/api.py", line 10, in public\nValueError: bad',
    )
    shifted = PytestProjectRunner._failure_signature(
        CheckStatus.FAIL,
        1,
        "",
        'Traceback:\n  File "/repo/pkg/api.py", line 12, in public\nValueError: bad',
    )
    changed_mechanism = PytestProjectRunner._failure_signature(
        CheckStatus.FAIL,
        1,
        "",
        'Traceback:\n  File "/repo/pkg/api.py", line 12, in public\nTypeError: bad',
    )

    assert first == shifted
    assert first != changed_mechanism


def test_container_traceback_maps_testbed_to_project_frame(tmp_path) -> None:
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "api.py").write_text(
        "def public():\n    raise ValueError('bad')\n", encoding="utf-8",
    )
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    stderr = (
        'Traceback (most recent call last):\n'
        '  File "/outside/run/test_repro.py", line 10, in <module>\n'
        '  File "/testbed/pkg/api.py", line 2, in public\n'
        'ValueError: bad\n'
    )

    frame = runner._first_project_frame("", stderr, repository)

    assert frame == {
        "relative_path": "pkg/api.py",
        "line": 2,
        "symbol": "public",
    }


def test_old_project_runtime_import_failure_is_not_a_repair_target(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    check = runner.compile_command_checks(((
        sys.executable, "-c",
        "import sys; sys.stderr.write(\"ImportError: cannot import name "
        "'Mapping' from 'collections'\"); sys.exit(1)",
    ),))[0]

    health = runner.health_check(check)

    assert health.status == EnvironmentHealthStatus.UNSUPPORTED_RUNTIME
    assert health.execution.status == CheckStatus.INVALID_ENVIRONMENT


def test_unconfigured_django_settings_are_an_environment_failure(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    check = runner.compile_command_checks(((
        sys.executable, "-c",
        "import sys; sys.stderr.write("
        "'django.core.exceptions.ImproperlyConfigured: Requested setting '"
        "+ 'USE_I18N, but settings are not configured.'); sys.exit(1)",
    ),))[0]

    health = runner.health_check(check)

    assert health.status == EnvironmentHealthStatus.UNSUPPORTED_RUNTIME
    assert health.execution.status == CheckStatus.INVALID_ENVIRONMENT


def test_uninitialized_django_app_registry_is_an_environment_failure(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    check = runner.compile_command_checks(((
        sys.executable, "-c",
        "import sys; sys.stderr.write(\"django.core.exceptions."
        "AppRegistryNotReady: Apps aren't loaded yet.\"); sys.exit(1)",
    ),))[0]

    health = runner.health_check(check)

    assert health.status == EnvironmentHealthStatus.UNSUPPORTED_RUNTIME
    assert health.execution.status == CheckStatus.INVALID_ENVIRONMENT


def test_unwritable_home_and_matplotlib_config_are_environment_failures(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        runner = _runner(
            PytestProjectRunner,
            repository,
            tmp_path / "artifacts",
            environment={"HOME": str(locked), "MPLCONFIGDIR": str(locked)},
        )
        check = runner.compile_command_checks(((sys.executable, "-c", "pass"),))[0]

        health = runner.health_check(check)

        assert health.status == EnvironmentHealthStatus.UNSUPPORTED_RUNTIME
        assert health.execution.status == CheckStatus.INVALID_ENVIRONMENT
        assert "writable" in health.execution.stderr
    finally:
        locked.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def test_baseline_health_cache_reuses_base_commit_environment_and_check(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    test_path = repository / "test_cached.py"
    test_path.write_text("def test_cached():\n    assert True\n", encoding="utf-8")
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    check = runner.compile_visible_checks(("test_cached.py",))[0]

    first = runner.health_check(check)
    test_path.write_text("def test_cached():\n    assert False\n", encoding="utf-8")
    second = runner.health_check(check)

    assert first.status == second.status == EnvironmentHealthStatus.HEALTHY
    assert first.execution.execution_id == second.execution.execution_id
    assert second.execution.status == CheckStatus.PASS


def test_stable_execution_keeps_only_symbols_seen_in_every_run(
    tmp_path, monkeypatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    check = runner.compile_command_checks(((sys.executable, "-c", "pass"),))[0]
    observations = iter((
        (
            CheckStatus.FAIL, 1, "", "failure", 0.01, "same", None,
            ("pkg.api.always", "pkg.api.first_only"),
        ),
        (
            CheckStatus.FAIL, 1, "", "failure", 0.01, "same", None,
            ("pkg.api.always", "pkg.api.second_only"),
        ),
    ))
    monkeypatch.setattr(runner, "_execute_once", lambda *_: next(observations))

    execution = runner.run_check(check, repeats=2)

    assert execution.stable
    assert execution.executed_symbol_ids == ("pkg.api.always",)


def test_runner_traces_project_symbols_for_explicit_python_checks(tmp_path) -> None:
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "pkg" / "api.py").write_text(
        "def public(value):\n    return value + 1\n", encoding="utf-8",
    )
    runner = _runner(PytestProjectRunner, repository, tmp_path / "artifacts")
    check = runner.compile_command_checks(((
        sys.executable,
        "-c",
        "from pkg.api import public; raise SystemExit(public(1) != 2)",
    ),))[0]

    execution = runner.run_check(check, repeats=2)

    assert execution.status == CheckStatus.PASS
    assert execution.executed_symbol_ids == ("pkg.api.public",)


def test_baseline_cache_restores_executed_symbols_as_tuple_and_strips_marker(
    tmp_path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    artifacts = tmp_path / "artifacts"
    runner = _runner(PytestProjectRunner, repository, artifacts)
    marker = EXECUTED_SYMBOLS_MARKER + '["pkg.api.public"]'
    check = runner.compile_command_checks(((
        sys.executable,
        "-c",
        f"import sys; print({marker!r}, file=sys.stderr); raise SystemExit(1)",
    ),))[0]

    first = runner.run_baseline_check(check)
    restored = _runner(
        PytestProjectRunner, repository, artifacts,
    ).run_baseline_check(check)

    assert first.executed_symbol_ids == ("pkg.api.public",)
    assert restored.executed_symbol_ids == ("pkg.api.public",)
    assert isinstance(restored.executed_symbol_ids, tuple)
    assert EXECUTED_SYMBOLS_MARKER not in restored.stderr
    assert first.failure_signature == restored.failure_signature


def test_trial_execution_rebases_repository_owned_pythonpath_entries(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    for repository, value in ((baseline, "baseline"), (trial, "trial")):
        (repository / "tests").mkdir(parents=True)
        (repository / "tests" / "public_settings.py").write_text(
            f"VALUE = {value!r}\n", encoding="utf-8",
        )
    runner = _runner(PytestProjectRunner, baseline, tmp_path / "artifacts")
    check = runner.compile_command_checks(((
        sys.executable,
        "-c",
        "import public_settings; print(public_settings.VALUE)",
    ),))[0]
    check = __import__("dataclasses").replace(
        check,
        environment={
            "PYTHONPATH": os.pathsep.join((
                str(baseline), str(baseline / "tests"),
            )),
        },
    )

    execution = runner.run_check(check, repository=trial, repeats=1)

    assert execution.status == CheckStatus.PASS
    assert execution.stdout.strip() == "trial"


def test_public_image_name_uses_only_public_instance_id() -> None:
    assert public_swebench_image("django__django-13220") == (
        "swebench/sweb.eval.x86_64.django_1776_django-13220:latest"
    )


def test_public_docker_broker_executes_real_check_without_daemon_exposure(
    tmp_path: Path, monkeypatch,
) -> None:
    repository = tmp_path / "case-tree"
    repository.mkdir()
    (repository / ".git").write_text(
        "gitdir: /hidden/repository/worktrees/case-tree\n", encoding="utf-8",
    )
    (repository / "check.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "assert os.environ['PYTHONHASHSEED'] == '0'\n"
        "assert Path(os.environ['HOME']).is_dir()\n"
        "print('public-container-check-passed')\n",
        encoding="utf-8",
    )
    case_run_root = tmp_path / "runs" / "case"
    case_run_root.mkdir(parents=True)
    broker = PublicDockerExecutionBroker(
        socket_path=tmp_path / "broker.sock",
        image="python:3.11-slim",
        case_tree=repository,
        case_run_root=case_run_root,
        max_live_containers=1,
    )
    if not broker.available():
        pytest.skip("python:3.11-slim is unavailable")

    with broker:
        for key, value in broker.worker_environment().items():
            monkeypatch.setenv(key, value)
        runner = _runner(
            PytestProjectRunner, repository, case_run_root / "execution",
        )
        check = runner.compile_command_checks((
            (sys.executable, "check.py"),
        ))[0]
        execution = runner.run_check(check, repeats=1)

    assert execution.status == CheckStatus.PASS
    assert execution.stable
    assert "public-container-check-passed" in execution.stdout
