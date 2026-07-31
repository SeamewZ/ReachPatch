from __future__ import annotations

import json
import os
from types import SimpleNamespace

from reachpatch.execution.models import CheckExecution, CheckRole, CheckStatus, ExecutableCheck
from reachpatch.execution.runners import DjangoRunner, PytestProjectRunner
from reachpatch.execution.target_recovery import (
    _ensure_django_reproduction_app_labels,
    _is_script_level_reproduction_failure,
    _public_issue,
    _write_reproduction,
    _issue_describes_executable_behavior,
    is_executable_test_path,
    _related_repository_tests,
    recover_executable_targets,
)


def test_issue_behavior_classifier_covers_normative_check_and_result_consistency():
    assert _issue_describes_executable_behavior(
        "Add check to ensure max_length fits the longest choice."
    )
    assert _issue_describes_executable_behavior(
        "QuerySet.Delete has an inconsistent result format when zero objects are deleted."
    )
from reachpatch.models.core import Instance
from reachpatch.repair.deepseek_agent import PersistentDeepSeekAgent


class _DirectedGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def generate_target_reproduction(self, **kwargs):
        self.calls += 1
        assert "hidden" not in json.dumps(kwargs).lower()
        return {
            "filename": "public-negative-value.py",
            "source": (
                "from pkg.api import public\n"
                "try:\n"
                "    public(-1)\n"
                "except ValueError:\n"
                "    pass\n"
                "else:\n"
                "    raise AssertionError('negative values must be rejected')\n"
            ),
            "expected_observation": "public(-1) raises ValueError",
        }


class _InvalidDirectedGenerator:
    def generate_target_reproduction(self, **kwargs):
        return {
            "filename": "test_invalid_reproduction.py",
            "source": (
                "from pkg.api import public\n"
                "public().missing_attribute\n"
            ),
            "expected_observation": "the public API satisfies the issue",
        }


def test_directed_reproduction_runs_once_and_must_fail_stably(tmp_path) -> None:
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "pkg" / "api.py").write_text(
        "def public(value):\n    return 0\n", encoding="utf-8",
    )
    artifact_root = tmp_path / "artifacts"
    runner = PytestProjectRunner(
        repository, artifact_root=artifact_root, base_commit="base",
    )
    generator = _DirectedGenerator()
    index = SimpleNamespace(test_references={}, symbols={})

    result = recover_executable_targets(
        Instance(
            "public-reproduction", str(repository), "base",
            "The public(value) API incorrectly accepts negative values; expected "
            "a ValueError instead.",
        ),
        index,
        runner,
        generator,
        artifact_root,
    )

    assert generator.calls == 1
    assert result.directed_reproduction_requests == 1
    assert len(result.targets) == 1
    assert result.execution_for(result.targets[0].check_id).stable
    assert result.execution_for(result.targets[0].check_id).status.value == "FAIL"
    assert "issue-behavior:pkg.api.public" in (
        result.targets[0].source_evidence_ids
    )
    reproduction = result.targets[0].temporary_artifact_paths[0]
    assert str(repository) not in reproduction


def test_script_level_reproduction_exception_is_rejected_not_target(tmp_path) -> None:
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "pkg" / "api.py").write_text(
        "def public():\n    return object()\n", encoding="utf-8",
    )
    runner = PytestProjectRunner(
        repository, artifact_root=tmp_path / "artifacts", base_commit="base",
    )
    result = recover_executable_targets(
        Instance(
            "invalid-reproduction", str(repository), "base",
            "The public API has an incorrect behavior and should be fixed.",
        ),
        SimpleNamespace(test_references={}, symbols={}),
        runner,
        _InvalidDirectedGenerator(),
        tmp_path / "artifacts",
    )

    assert not result.targets
    assert not result.environment_frontiers
    assert len(result.rejected_checks) == 1
    assert "before its observation assertion" in result.rejected_checks[0].reason


def test_reproduction_records_symbols_called_through_imported_modules(tmp_path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = PytestProjectRunner(
        repository, artifact_root=tmp_path / "artifacts", base_commit="base",
    )

    check = _write_reproduction(
        tmp_path / "artifacts",
        "attribute-call.py",
        "from django.db import models\nfrom django import forms\n"
        "models.FilePathField(path='.')\nforms.FilePathField(path='.')\n",
        runner,
        "directed-public-reproduction",
    )

    assert "issue-behavior:django.db.models.FilePathField" in check.source_evidence_ids
    assert "issue-behavior:django.forms.FilePathField" in check.source_evidence_ids


def test_deepseek_reproduction_tool_rejects_non_observable_source() -> None:
    def transport(messages, schemas):
        assert schemas[0]["function"]["name"] == "submit_reproduction"
        assert "mutually exclusive requirements" in messages[0]["content"]
        assert "Do not add a negative case" in messages[0]["content"]
        assert "end-to-end public behavior" in messages[0]["content"]
        assert "public serializer or writer output" in messages[0]["content"]
        assert "primary_issue is the normative authority" in messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        assert payload["primary_issue"] == "public behavior is wrong"
        assert payload["public_discussion"] == ""
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": "reproduction",
                "type": "function",
                "function": {
                    "name": "submit_reproduction",
                    "arguments": json.dumps({
                        "filename": "repro.py",
                        "setup_source": "print('no observation')\n",
                        "observation_expression": "True",
                        "expected_observation": "a value",
                    }),
                },
            }],
        }

    agent = PersistentDeepSeekAgent(transport)

    assert agent.generate_target_reproduction(
        issue="public behavior is wrong",
        source_context=(),
        project_runner="pytest",
    ) is None


def test_deepseek_reproduction_owns_directional_oracle(tmp_path) -> None:
    def transport(messages, schemas):
        properties = schemas[0]["function"]["parameters"]["properties"]
        assert "observation_expression" in properties
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": "reproduction",
                "type": "function",
                "function": {
                    "name": "submit_reproduction",
                    "arguments": json.dumps({
                        "filename": "repro.py",
                        "setup_source": "observed = 'broken'\n",
                        "observation_expression": "observed == 'fixed'",
                        "expected_observation": "the fixed public value",
                    }),
                },
            }],
        }

    proposal = PersistentDeepSeekAgent(transport).generate_target_reproduction(
        issue="The public value is broken and should be fixed.",
        source_context=(),
        project_runner="pytest",
    )

    assert proposal is not None
    assert "assert (observed == 'fixed')" in proposal["source"]
    reproduction = tmp_path / proposal["filename"]
    reproduction.write_text(proposal["source"], encoding="utf-8")
    completed = __import__("subprocess").run(
        [__import__("sys").executable, str(reproduction)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "the fixed public value" in completed.stderr


def test_deepseek_reproduction_rejects_model_owned_oracle() -> None:
    def transport(messages, schemas):
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": "reproduction",
                "type": "function",
                "function": {
                    "name": "submit_reproduction",
                    "arguments": json.dumps({
                        "filename": "repro.py",
                        "setup_source": (
                            "observed = 'broken'\n"
                            "assert observed == 'broken'\n"
                        ),
                        "observation_expression": "observed == 'fixed'",
                        "expected_observation": "the fixed public value",
                    }),
                },
            }],
        }

    assert PersistentDeepSeekAgent(transport).generate_target_reproduction(
        issue="The public value is broken and should be fixed.",
        source_context=(),
        project_runner="pytest",
    ) is None


def test_deepseek_reproduction_gets_one_mechanical_correction_turn() -> None:
    calls = []

    def transport(messages, schemas):
        calls.append(messages)
        if len(calls) == 2:
            assert messages[-2]["role"] == "assistant"
            assert "tool_calls" not in messages[-2]
        setup = (
            "observed = 'broken'\nassert observed == 'broken'\n"
            if len(calls) == 1 else "observed = 'broken'\n"
        )
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"reproduction-{len(calls)}",
                "type": "function",
                "function": {
                    "name": "submit_reproduction",
                    "arguments": json.dumps({
                        "filename": "test_public_value.py",
                        "setup_source": setup,
                        "observation_expression": "observed == 'fixed'",
                        "expected_observation": "the fixed public value",
                    }),
                },
            }],
        }

    proposal = PersistentDeepSeekAgent(transport).generate_target_reproduction(
        issue="The public value is broken and should be fixed.",
        source_context=(),
        project_runner="pytest",
    )

    assert proposal is not None
    assert len(calls) == 2
    assert "mechanically rejected" in calls[1][-1]["content"]
    assert "assert observed == 'broken'" not in proposal["source"]


def test_deepseek_reproduction_rejects_django_field_check_in_model_body() -> None:
    calls = 0

    def transport(messages, schemas):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert "field.check()" in messages[-1]["content"]
            setup = (
                "from django.db import models\n"
                "class ChoiceModel(models.Model):\n"
                "    class Meta:\n"
                "        app_label = 'repro'\n"
                "    field = models.CharField(max_length=1, choices=[('AB', 'AB')])\n"
                "errors = ChoiceModel.check()\n"
            )
        else:
            setup = (
                "from django.db import models\n"
                "class ChoiceModel(models.Model):\n"
                "    class Meta:\n"
                "        app_label = 'repro'\n"
                "    field = models.CharField(max_length=1, choices=[('AB', 'AB')])\n"
                "    errors = field.check()\n"
            )
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"reproduction-{calls}",
                "type": "function",
                "function": {
                    "name": "submit_reproduction",
                    "arguments": json.dumps({
                        "filename": "test_django_check.py",
                        "setup_source": setup,
                        "observation_expression": "len(errors) == 1",
                        "expected_observation": "the field check reports the choice length error",
                    }),
                },
            }],
        }

    proposal = PersistentDeepSeekAgent(transport).generate_target_reproduction(
        issue="Add a check to ensure max_length fits the longest choice.",
        source_context=(),
        project_runner="django",
    )

    assert proposal is not None
    assert calls == 2
    assert "errors = ChoiceModel.check()" in proposal["source"]


def test_deepseek_reproduction_rejects_unstated_intermediate_attribute_type() -> None:
    calls = 0

    def transport(messages, schemas):
        nonlocal calls
        calls += 1
        if calls == 1:
            setup = (
                "class Result:\n    path = 'value'\n"
                "result = Result()\n"
                "observed = isinstance(result.path, str)\n"
            )
        else:
            assert "intermediate object attribute" in messages[-1]["content"]
            setup = "public_result = 'fixed'\n"
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"reproduction-{calls}",
                "type": "function",
                "function": {
                    "name": "submit_reproduction",
                    "arguments": json.dumps({
                        "filename": "test_public_result.py",
                        "setup_source": setup,
                        "observation_expression": (
                            "observed" if calls == 1
                            else "public_result == 'fixed'"
                        ),
                        "expected_observation": "the public result is fixed",
                    }),
                },
            }],
        }

    proposal = PersistentDeepSeekAgent(transport).generate_target_reproduction(
        issue="The public operation must accept a deferred input.",
        source_context=(),
        project_runner="pytest",
    )

    assert proposal is not None
    assert calls == 2
    assert "public_result == 'fixed'" in proposal["source"]
    assert "result.path" not in proposal["source"]


def test_deepseek_reproduction_completes_recorded_observation_contract() -> None:
    def transport(messages, schemas):
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": "reproduction",
                "type": "function",
                "function": {
                    "name": "submit_reproduction",
                    "arguments": json.dumps({
                        "filename": "test_callable_path.py",
                        "setup_source": (
                            "init_success = True\n"
                            "migration_preserved = False\n"
                        ),
                        "observation_expression": "init_success",
                        "expected_observation": (
                            "callable path initializes and remains callable in migrations"
                        ),
                    }),
                },
            }],
        }

    proposal = PersistentDeepSeekAgent(transport).generate_target_reproduction(
        issue="Callable paths must be preserved in migrations.",
        source_context=(),
        project_runner="django",
    )

    assert proposal is not None
    assert "(init_success)" in proposal["source"]
    assert "(migration_preserved)" in proposal["source"]


def test_related_tests_prioritize_issue_symbol_in_test_path() -> None:
    references = {
        f"tests/generic/test_form_{index}.py": ("ValidationError", "field")
        for index in range(30)
    }
    references["tests/test_exceptions/test_validation_error.py"] = (
        "ValidationError", "messages", "sorted",
    )
    references["tests/test_exceptions/models.py"] = (
        "ValidationError", "messages", "sorted", "internal",
    )

    related = _related_repository_tests(
        "django.core.exceptions.ValidationError equality ignores error order",
        SimpleNamespace(test_references=references),
        (),
    )

    assert related[0] == "tests/test_exceptions/test_validation_error.py"
    assert "tests/test_exceptions/models.py" not in related


def test_django_reproduction_uses_public_test_settings(tmp_path) -> None:
    repository = tmp_path / "django"
    (repository / "django").mkdir(parents=True)
    (repository / "tests").mkdir()
    (repository / "tests" / "test_sqlite.py").write_text(
        "SECRET_KEY = 'test'\n", encoding="utf-8",
    )
    runner = DjangoRunner(
        repository,
        artifact_root=tmp_path / "artifacts",
        base_commit="base",
    )

    check = _write_reproduction(
        tmp_path / "run",
        "repro.py",
        "observed = True\nassert observed\n",
        runner,
        "public-issue",
    )

    assert check.environment["DJANGO_SETTINGS_MODULE"] == "test_sqlite"
    assert check.environment["PYTHONPATH"].split(os.pathsep) == [
        str(repository.resolve()),
        str((repository / "tests").resolve()),
    ]


def test_django_reproduction_bootstraps_before_model_definition(tmp_path) -> None:
    repository = tmp_path / "django"
    (repository / "django").mkdir(parents=True)
    (repository / "tests").mkdir()
    (repository / "tests" / "test_sqlite.py").write_text(
        "SECRET_KEY = 'test'\n", encoding="utf-8",
    )
    runner = DjangoRunner(
        repository,
        artifact_root=tmp_path / "artifacts",
        base_commit="base",
    )

    check = _write_reproduction(
        tmp_path / "run",
        "repro.py",
        "from django.db import models\n\n"
        "class Sample(models.Model):\n"
        "    value = models.CharField(max_length=2)\n",
        runner,
        "public-issue",
    )

    source = (tmp_path / "run" / "target-reproductions" / "repro.py").read_text(
        encoding="utf-8",
    )
    assert source.index("django.setup()") < source.index("class Sample")


def test_django_reproduction_adds_app_label_for_standalone_models() -> None:
    source = (
        "from django.db import models\n\n"
        "class Sample(models.Model):\n"
        "    value = models.CharField(max_length=2)\n"
    )

    normalized = _ensure_django_reproduction_app_labels(source)

    assert "class Meta:" in normalized
    assert "app_label = 'reachpatch_reproduction'" in normalized
    compile(normalized, "reproduction.py", "exec")


def test_django_script_configuration_failure_is_rejected_with_project_frame(tmp_path) -> None:
    check = ExecutableCheck(
        check_id="reproduction",
        role=CheckRole.EXPLORATION,
        authority="ISSUE_PUBLIC_REPRODUCTION",
        command=("python", "repro.py"),
        cwd=str(tmp_path),
        environment={},
        timeout_seconds=60.0,
        source_evidence_ids=(),
        target_requirement_ids=(),
        temporary_artifact_paths=(str(tmp_path / "repro.py"),),
        selector="repro.py",
    )
    execution = CheckExecution(
        execution_id="execution",
        check_id=check.check_id,
        tree_hash="base",
        status=CheckStatus.FAIL,
        return_code=1,
        stdout="",
        stderr=(
            f"File \"{tmp_path / 'repro.py'}\", line 8, in <module>\n"
            "RuntimeError: Model class __main__.Sample doesn't declare an "
            "explicit app_label and isn't in an application in INSTALLED_APPS.\n"
        ),
        duration_seconds=0.1,
        stable=True,
        failure_signature="django-model-setup",
        first_project_frame={
            "path": "django/db/models/base.py",
            "line": 112,
            "symbol": "ModelBase.__new__",
        },
    )

    assert _is_script_level_reproduction_failure(check, execution)


def test_django_templates_setup_failure_is_rejected_with_project_frame(tmp_path) -> None:
    check = ExecutableCheck(
        check_id="template-reproduction",
        role=CheckRole.EXPLORATION,
        authority="ISSUE_PUBLIC_REPRODUCTION",
        command=("python", "template_repro.py"),
        cwd=str(tmp_path),
        environment={},
        timeout_seconds=60.0,
        source_evidence_ids=(),
        target_requirement_ids=(),
        temporary_artifact_paths=(str(tmp_path / "template_repro.py"),),
        selector="template_repro.py",
    )
    execution = CheckExecution(
        execution_id="template-execution",
        check_id=check.check_id,
        tree_hash="base",
        status=CheckStatus.FAIL,
        return_code=1,
        stdout="",
        stderr=(
            f"File \"{tmp_path / 'template_repro.py'}\", line 7, in <module>\n"
            "  rendered = form.as_p()\n"
            "File \"django/forms/utils.py\", line 87, in as_p\n"
            "django.core.exceptions.ImproperlyConfigured: "
            "No DjangoTemplates backend is configured.\n"
        ),
        duration_seconds=0.1,
        stable=True,
        failure_signature="django-template-setup",
        first_project_frame={
            "path": "django/forms/utils.py",
            "line": 87,
            "symbol": "RenderableFormMixin.as_p",
        },
    )

    assert _is_script_level_reproduction_failure(check, execution)


def test_public_issue_includes_public_discussion_and_test_paths_are_strict() -> None:
    instance = Instance(
        "public-hints", "/repo", "base", "Allow path to accept a callable.",
        public_metadata={
            "hints_text": "Calling the form currently raises TypeError.",
        },
    )

    issue = _public_issue(instance)

    assert "Allow path to accept a callable." in issue
    assert "Calling the form currently raises TypeError." in issue
    assert is_executable_test_path("tests/forms/test_fields.py")
    assert is_executable_test_path("tests/forms/tests.py")
    assert not is_executable_test_path("tests/forms/models.py")
    assert not is_executable_test_path("tests/forms/fixtures.py")
