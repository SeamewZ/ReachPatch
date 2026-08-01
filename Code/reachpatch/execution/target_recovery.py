from __future__ import annotations

import ast
import json
import os
import re
import threading
import time
from queue import Queue
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from reachpatch.execution.models import (
    CheckExecution,
    CheckRole,
    CheckStatus,
    EnvironmentFrontier,
    EnvironmentHealth,
    EnvironmentHealthStatus,
    ExecutableCheck,
    EXECUTED_SYMBOLS_MARKER,
    RejectedCheck,
)
from reachpatch.execution.runners import BaseProjectRunner
from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.controller import ExecutableOracle
from reachpatch.models.isolation import assert_generation_payload, is_official_only_path
from reachpatch.oracle.models import ObservationContract


class TargetRecoveryStatus(StrEnum):
    TARGET_AVAILABLE = "TARGET_AVAILABLE"
    TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
    TARGET_ENVIRONMENT_BLOCKED = "TARGET_ENVIRONMENT_BLOCKED"


@dataclass(frozen=True, slots=True)
class TargetCandidate(SerializableRecord):
    target_id: str
    strategy: str
    input_source: str
    oracle_authority: str
    setup_commands: tuple[tuple[str, ...], ...]
    command: tuple[str, ...]
    observation_contract: ObservationContract
    oracle: ExecutableOracle | None
    target_requirement_ids: tuple[str, ...]
    source_evidence_ids: tuple[str, ...]
    executed_symbol_ids: tuple[str, ...]
    stability_runs: int
    status: str
    confidence: float = 0.0

    @property
    def authority(self) -> str:
        return self.oracle_authority

    @property
    def expected_relation(self) -> dict[str, Any] | None:
        return self.oracle.to_dict() if self.oracle is not None else None

    @property
    def source_evidence(self) -> tuple[str, ...]:
        return self.source_evidence_ids


@dataclass(frozen=True, slots=True)
class TargetCertification(SerializableRecord):
    target_id: str
    certified: bool
    trusted_oracle: bool
    stable_execution: bool
    exposes_issue: bool
    reaches_related_code: bool
    oracle_authority: str
    reason: str


@dataclass(frozen=True, slots=True)
class TargetRecoveryResult(SerializableRecord):
    targets: tuple[ExecutableCheck, ...]
    preservation_checks: tuple[ExecutableCheck, ...]
    rejected_checks: tuple[RejectedCheck, ...]
    environment_frontiers: tuple[EnvironmentFrontier, ...]
    baseline_executions: tuple[CheckExecution, ...] = ()
    health_checks: tuple[EnvironmentHealth, ...] = ()
    directed_reproduction_requests: int = 0
    status: str = TargetRecoveryStatus.TARGET_UNAVAILABLE.value
    candidates: tuple[TargetCandidate, ...] = ()
    exploration_candidates: tuple[TargetCandidate, ...] = ()
    elapsed_seconds: float = 0.0
    timed_out: bool = False
    certifications: tuple[TargetCertification, ...] = ()

    def execution_for(self, check_id: str) -> CheckExecution | None:
        return next(
            (item for item in self.baseline_executions if item.check_id == check_id),
            None,
        )

    @classmethod
    def unavailable(
        cls, *, environment_blocked: bool = False, timed_out: bool = False
    ) -> "TargetRecoveryResult":
        return cls(
            targets=(), preservation_checks=(), rejected_checks=(),
            environment_frontiers=(),
            status=(
                TargetRecoveryStatus.TARGET_ENVIRONMENT_BLOCKED.value
                if environment_blocked else TargetRecoveryStatus.TARGET_UNAVAILABLE.value
            ),
            timed_out=timed_out,
        )


def baseline_exposes_issue_failure(
    candidate: TargetCandidate,
    runs: Iterable[CheckExecution],
) -> bool:
    executions = tuple(runs)
    if candidate.oracle is None or not candidate.oracle.is_executable:
        return False
    if candidate.oracle.relation != "baseline_failure_must_become_pass":
        return False
    return bool(
        executions
        and all(
            item.check_id == candidate.target_id
            and item.status == CheckStatus.FAIL
            and item.stable
            and item.return_code not in {None, 0}
            for item in executions
        )
    )


def reaches_relevant_program_region(
    candidate: TargetCandidate,
    active_binding_graph: Any,
) -> bool:
    for unit in getattr(active_binding_graph, "units", {}).values():
        if candidate.target_id not in getattr(unit, "target_check_ids", ()):
            continue
        if any(
            edge.edge_type == "CHECK_EXECUTED_SYMBOL"
            and edge.source_id == candidate.target_id
            and edge.target_id in unit.program_symbol_ids
            for edge in getattr(active_binding_graph, "edges", ())
        ):
            return True
    return False


def certify_target(
    candidate: TargetCandidate,
    baseline_runs: Iterable[CheckExecution],
    active_binding_graph: Any,
) -> TargetCertification:
    runs = tuple(baseline_runs)
    trusted_oracle = bool(
        candidate.oracle is not None
        and candidate.oracle.is_executable
        and candidate.oracle_authority in {"A", "B", "C"}
    )
    stable_execution = bool(
        candidate.stability_runs >= 2
        and runs
        and all(item.stable for item in runs)
    )
    exposes_issue = baseline_exposes_issue_failure(candidate, runs)
    reaches_related_code = reaches_relevant_program_region(
        candidate, active_binding_graph,
    )
    certified = all((
        trusted_oracle,
        stable_execution,
        exposes_issue,
        reaches_related_code,
    ))
    failed = tuple(
        name for name, passed in (
            ("trusted_oracle", trusted_oracle),
            ("stable_execution", stable_execution),
            ("exposes_issue", exposes_issue),
            ("reaches_related_code", reaches_related_code),
        ) if not passed
    )
    return TargetCertification(
        target_id=candidate.target_id,
        certified=certified,
        trusted_oracle=trusted_oracle,
        stable_execution=stable_execution,
        exposes_issue=exposes_issue,
        reaches_related_code=reaches_related_code,
        oracle_authority=candidate.oracle_authority,
        reason="CERTIFIED" if certified else "MISSING:" + ",".join(failed),
    )


def certify_recovered_targets(
    recovery: TargetRecoveryResult,
    active_binding_graph: Any,
) -> tuple[TargetCertification, ...]:
    executions = {
        item.check_id: item for item in recovery.baseline_executions
    }
    return tuple(
        certify_target(
            candidate,
            tuple(filter(None, (executions.get(candidate.target_id),))),
            active_binding_graph,
        )
        for candidate in recovery.candidates
        if candidate.target_id in {item.check_id for item in recovery.targets}
    )


_CODE_BLOCK = re.compile(r"```(?:python|py)?\s*\n(?P<code>.*?)```", re.DOTALL | re.IGNORECASE)
_RETURN_LITERAL = re.compile(
    r"(?P<symbol>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\([^)]*\)"
    r"[^\n.]*?\b(?:must|should)\s+return\s+(?P<expected>\[[^\n]*?\]|\{[^\n]*?\}|"
    r"\([^\n]*?\)|None|True|False|[-+]?\d+(?:\.\d+)?|['\"][^'\"]*['\"])",
    re.IGNORECASE,
)
_RETURN_TYPE = re.compile(
    r"(?P<symbol>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*\([^)]*\)"
    r"[^\n.]*?\b(?:must|should)\s+return\s+(?:an?\s+)?(?P<kind>list|tuple|dict|set|str|string|int|integer|bool|boolean)",
    re.IGNORECASE,
)
_DJANGO_MODEL_CLASS = re.compile(
    r"(?m)^(?P<indent>\s*)class\s+\w+\s*\([^\n:]*\bmodels\.Model\b[^\n)]*\):"
)
_DJANGO_SCRIPT_FAILURE_MARKERS = (
    "doesn't declare an explicit app_label",
    "isn't in an application in installed_apps",
    "appregistrynotready",
    "apps aren't loaded yet",
    "settings aren't configured",
    "settings are not configured",
    "deferredattribute",
)


def _artifact_root(artifact_store: Any) -> Path:
    if isinstance(artifact_store, (str, Path)):
        root = Path(artifact_store)
    else:
        root = Path(
            getattr(artifact_store, "root", None)
            or getattr(artifact_store, "run_root", None)
            or getattr(getattr(artifact_store, "store", None), "root", None)
            or ".reachpatch"
        )
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _public_issue(instance: Any) -> str:
    issue = str(getattr(instance, "issue", ""))
    metadata = dict(getattr(instance, "public_metadata", {}) or {})
    environment = dict(getattr(instance, "environment", {}) or {})
    assert_generation_payload(metadata, path="target_recovery.public_metadata")
    assert_generation_payload(environment, path="target_recovery.environment")
    public_hints = str(metadata.get("hints_text", "")).strip()
    if public_hints and public_hints not in issue:
        issue = f"{issue.rstrip()}\n\nPublic discussion:\n{public_hints}"
    return issue


def _issue_tokens(issue: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z_]\w*", issue)
        if len(token) >= 3
    }


def is_executable_test_path(relative: str) -> bool:
    test_name = Path(str(relative)).name.lower()
    return (
        test_name in {"test.py", "tests.py"}
        or (test_name.startswith("test_") and test_name.endswith(".py"))
        or test_name.endswith("_test.py")
    )


def _related_repository_tests(
    issue: str,
    repository_index: Any,
    visible: Iterable[str],
) -> tuple[str, ...]:
    visible_set = {str(item).replace("\\", "/") for item in visible}
    tokens = _issue_tokens(issue)
    ranked = []
    for relative, references in repository_index.test_references.items():
        if is_official_only_path(relative):
            continue
        if not is_executable_test_path(relative):
            continue
        normalized_references = {str(item).lower() for item in references}
        overlap = tokens & normalized_references
        if overlap and relative not in visible_set:
            compact_path = re.sub(r"[^a-z0-9]", "", relative.lower())
            path_matches = {
                token for token in tokens
                if len(token) >= 5 and token in compact_path
            }
            specific_symbol_score = sum(
                len(token) for token in overlap if len(token) >= 8
            )
            ranked.append((
                -sum(len(token) for token in path_matches),
                -specific_symbol_score,
                -len(overlap),
                relative,
            ))
    return tuple(relative for *_, relative in sorted(ranked)[:20])


def _write_reproduction(
    root: Path,
    name: str,
    source: str,
    runner: BaseProjectRunner,
    evidence_id: str,
) -> ExecutableCheck:
    reproduction_root = root / "target-reproductions"
    reproduction_root.mkdir(parents=True, exist_ok=True)
    if getattr(runner, "name", "") == "django":
        # Generated Django snippets sometimes define models before calling
        # setup(), which raises AppRegistryNotReady before the public behavior
        # is reached.  The project runner already supplies DJANGO_SETTINGS_MODULE;
        # insert the required bootstrap immediately before the first model.
        if (
            "django.setup(" not in source
            and "models.Model" in source
            and (match := _DJANGO_MODEL_CLASS.search(source)) is not None
        ):
            source = (
                source[:match.start()]
                + "import django\n\ndjango.setup()\n\n"
                + source[match.start():]
            )
        source = _ensure_django_reproduction_app_labels(source)
    imported_symbols = set(_called_import_symbols(source))
    source = _instrument_reproduction_source(
        source, tuple(sorted(imported_symbols)),
    )
    path = reproduction_root / name
    path.write_text(source.rstrip() + "\n", encoding="utf-8")
    check_id = stable_id("temporary-public-reproduction", evidence_id, source)

    source_evidence_ids = tuple(dict.fromkeys((
        evidence_id,
        *(f"issue-behavior:{symbol}" for symbol in sorted(imported_symbols)),
    )))
    environment = {"PYTHONPATH": str(runner.repository)}
    django_settings = runner.repository / "tests" / "test_sqlite.py"
    if runner.name == "django" and django_settings.is_file():
        environment.update({
            "DJANGO_SETTINGS_MODULE": "test_sqlite",
            "PYTHONPATH": os.pathsep.join((
                str(runner.repository),
                str(runner.repository / "tests"),
            )),
        })
    return ExecutableCheck(
        check_id=check_id,
        role=CheckRole.EXPLORATION,
        authority="ISSUE_PUBLIC_REPRODUCTION",
        command=(runner.python_executable, str(path)),
        cwd=str(runner.repository),
        environment=environment,
        timeout_seconds=60.0,
        source_evidence_ids=source_evidence_ids,
        target_requirement_ids=(),
        temporary_artifact_paths=(str(path),),
        selector=str(path),
        executed_symbol_ids=tuple(sorted(imported_symbols)),
    )


def _instrument_reproduction_source(
    source: str,
    symbol_hints: tuple[str, ...] = (),
) -> str:
    """Capture calls that really entered the mounted public project tree."""

    prelude = f'''import atexit as _reachpatch_atexit
import json as _reachpatch_json
import os as _reachpatch_os
import sys as _reachpatch_sys

_reachpatch_root = _reachpatch_os.path.realpath(_reachpatch_os.getcwd())
_reachpatch_root_prefix = _reachpatch_root + _reachpatch_os.sep
_reachpatch_hints = {tuple(sorted(set(map(str, symbol_hints))))!r}
_reachpatch_symbols = set()

def _reachpatch_profile(frame, event, arg):
    if event != "call" or len(_reachpatch_symbols) >= 500:
        return
    raw_filename = frame.f_code.co_filename
    if not raw_filename or raw_filename.startswith("<"):
        return
    if _reachpatch_os.path.isabs(raw_filename):
        if not raw_filename.startswith(_reachpatch_root_prefix):
            return
        relative = raw_filename[len(_reachpatch_root_prefix):]
    else:
        filename = _reachpatch_os.path.realpath(raw_filename)
        if not filename.startswith(_reachpatch_root_prefix):
            return
        relative = filename[len(_reachpatch_root_prefix):]
    if not relative:
        return
    module = relative.replace(_reachpatch_os.sep, ".")
    if module.endswith(".py"):
        module = module[:-3]
    if module.endswith(".__init__"):
        module = module[:-9]
    qualname = getattr(frame.f_code, "co_qualname", frame.f_code.co_name)
    if qualname and qualname != "<module>":
        symbol = module + "." + qualname
        if not _reachpatch_hints or any(
            symbol == hint
            or symbol.endswith("." + hint)
            or hint.endswith("." + symbol)
            for hint in _reachpatch_hints
        ):
            _reachpatch_symbols.add(symbol)

def _reachpatch_emit_symbols():
    _reachpatch_sys.setprofile(None)
    print(
        {EXECUTED_SYMBOLS_MARKER!r}
        + _reachpatch_json.dumps(sorted(_reachpatch_symbols), separators=(",", ":")),
        file=_reachpatch_sys.stderr,
    )

_reachpatch_atexit.register(_reachpatch_emit_symbols)
_reachpatch_sys.setprofile(_reachpatch_profile)
'''
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return prelude + "\n" + source
    insert_after = 0
    for index, node in enumerate(tree.body):
        is_docstring = (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if not (is_docstring or is_future):
            break
        insert_after = int(getattr(node, "end_lineno", node.lineno))
    lines = source.splitlines(keepends=True)
    lines.insert(insert_after, prelude + "\n")
    return "".join(lines)


def _called_import_symbols(source: str) -> tuple[str, ...]:
    """Return imported call targets that a public check actually invokes."""

    imported_symbols: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if tree is not None:
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for name in node.names:
                    if name.name == "*":
                        continue
                    aliases[name.asname or name.name] = (
                        f"{node.module}.{name.name}"
                    )
            elif isinstance(node, ast.Import):
                for name in node.names:
                    aliases[name.asname or name.name.split(".", 1)[0]] = name.name
        called_names = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        imported_symbols.update(
            aliases[name] for name in called_names if name in aliases
        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts: list[str] = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if not isinstance(current, ast.Name) or current.id not in aliases:
                continue
            imported_symbols.add(".".join((
                aliases[current.id], *reversed(parts),
            )))
    return tuple(sorted(imported_symbols))


def _command_called_symbols(command: tuple[str, ...]) -> tuple[str, ...]:
    """Extract statically grounded call targets from Python command checks."""

    try:
        inline_index = command.index("-c")
    except ValueError:
        return ()
    if inline_index + 1 >= len(command):
        return ()
    return _called_import_symbols(command[inline_index + 1])


def _ensure_django_reproduction_app_labels(source: str) -> str:
    """Make standalone generated model classes loadable by Django.

    Temporary reproductions are not installed as Django applications. Model
    classes without an explicit app label otherwise fail during class
    construction, before the public observation can execute. Existing ``Meta``
    definitions are left untouched so a missing label is rejected explicitly.
    """

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    lines = source.splitlines(keepends=True)
    insertions: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(
            isinstance(base, ast.Attribute) and base.attr == "Model"
            for base in node.bases
        ):
            continue
        if any(
            isinstance(child, ast.ClassDef) and child.name == "Meta"
            for child in node.body
        ):
            continue
        class_line = lines[node.lineno - 1]
        class_indent = class_line[: len(class_line) - len(class_line.lstrip())]
        body_indent = class_indent + "    "
        insertions.append((
            node.end_lineno,
            "".join((
                f"{body_indent}class Meta:\n",
                f"{body_indent}    app_label = 'reachpatch_reproduction'\n",
            )),
        ))
    for end_line, block in sorted(insertions, reverse=True):
        lines[end_line:end_line] = [block]
    return "".join(lines)


def _is_script_level_reproduction_failure(
    check: ExecutableCheck,
    execution: CheckExecution,
) -> bool:
    """Reject generated scripts that fail before observing project behavior."""

    if check.authority != "ISSUE_PUBLIC_REPRODUCTION":
        return False
    if execution.status != CheckStatus.FAIL or not execution.stable:
        return False
    diagnostic = f"{execution.stdout}\n{execution.stderr}"
    if any(marker in diagnostic.lower() for marker in _DJANGO_SCRIPT_FAILURE_MARKERS):
        return True
    script_names = {
        Path(path).name for path in check.temporary_artifact_paths
    }
    if not any(name and name in diagnostic for name in script_names):
        return False
    # The compiler appends the observation assertion. Other uncaught
    # exceptions indicate a malformed setup or an invalid public entrypoint.
    return "AssertionError" not in diagnostic


def _code_block_checks(
    issue: str,
    root: Path,
    runner: BaseProjectRunner,
) -> tuple[ExecutableCheck, ...]:
    checks = []
    for index, match in enumerate(_CODE_BLOCK.finditer(issue), 1):
        source = match.group("code").strip()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        # Narrative snippets without an assertion, explicit exit, or raised
        # observation cannot establish a failing target.
        observable = any(isinstance(node, (ast.Assert, ast.Raise)) for node in ast.walk(tree))
        observable = observable or "sys.exit" in source
        if not observable:
            continue
        checks.append(_write_reproduction(
            root,
            f"issue-example-{index}.py",
            source,
            runner,
            f"issue-code-block:{index}",
        ))
    return tuple(checks)


def _behavior_reproduction(
    issue: str,
    root: Path,
    runner: BaseProjectRunner,
) -> ExecutableCheck | None:
    literal = _RETURN_LITERAL.search(issue)
    type_match = _RETURN_TYPE.search(issue) if literal is None else None
    match = literal or type_match
    if match is None:
        return None
    symbol = match.group("symbol")
    module_name, _, callable_name = symbol.rpartition(".")
    if not module_name:
        return None
    locations = []
    for candidate in runner.repository.rglob(f"{module_name.rsplit('.', 1)[-1]}.py"):
        if any(part in {".git", ".venv", "venv", "__pycache__"} for part in candidate.parts):
            continue
        locations.append(candidate)
    if not locations and not (runner.repository / (module_name.replace(".", "/") + ".py")).is_file():
        return None
    values = "([], [1], None, 0, 'value')"
    if literal is not None:
        expected_text = literal.group("expected")
        try:
            ast.literal_eval(expected_text)
        except (SyntaxError, ValueError):
            return None
        observation = f"result == {expected_text}"
    else:
        kind = type_match.group("kind").lower()
        aliases = {"string": "str", "integer": "int", "boolean": "bool"}
        observation = f"isinstance(result, {aliases.get(kind, kind)})"
    source = f"""\
import importlib

module = importlib.import_module({module_name!r})
callable_under_test = getattr(module, {callable_name!r})
observed = False
for candidate in {values}:
    try:
        result = callable_under_test(candidate)
    except (TypeError, ValueError):
        continue
    observed = True
    assert {observation}, (candidate, result)
assert observed, "public API accepted none of the bounded issue witnesses"
"""
    return _write_reproduction(
        root,
        "generated-public-behavior.py",
        source,
        runner,
        f"issue-behavior:{symbol}",
    )


def _issue_describes_executable_behavior(issue: str) -> bool:
    lowered = issue.lower()
    return any(marker in lowered for marker in (
        " bug", "error", "fail", "incorrect", "unexpected", "expected",
        "should", "must", "return", "raise", "crash", "regression",
        "allow", "support", "accept", "break", "ensure", "check",
        "inconsistent", "consistency", "format",
    ))


def _proposal_uses_issue_oracle(issue: str, expected_observation: str) -> bool:
    """Return whether the model supplied only input/setup for an issue oracle."""

    expected = expected_observation.lower()
    strong_tokens = set(re.findall(
        r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b",
        issue,
    ))
    strong_tokens.update(re.findall(
        r"(?i)\b(?:True|False|None|NotImplemented)\b|"
        r"\[[^\n]{0,80}\]|\{[^\n]{0,80}\}|"
        r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?![A-Za-z_])",
        issue,
    ))
    return any(str(token).strip().lower() in expected for token in strong_tokens)


def _directed_source_context(
    issue: str,
    repository_index: Any,
    repository: Path,
) -> tuple[dict[str, Any], ...]:
    tokens = _issue_tokens(issue)
    ranked = []
    for symbol, locations in repository_index.symbols.items():
        leaf = str(symbol).rsplit(".", 1)[-1].lower()
        if leaf not in tokens:
            continue
        for location in locations:
            relative = str(location.relative_path).replace("\\", "/")
            if "tests" in Path(relative).parts or is_official_only_path(relative):
                continue
            ranked.append((location.line, symbol, location))
    snippets = []
    seen: set[tuple[str, int]] = set()
    for _, symbol, location in sorted(ranked, key=lambda item: (item[0], item[1])):
        key = (location.relative_path, location.line)
        if key in seen:
            continue
        seen.add(key)
        path = repository / location.relative_path
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        start = max(1, location.line - 20)
        end = min(len(lines), max(location.end_line, location.line) + 30)
        snippets.append({
            "path": location.relative_path,
            "symbol": symbol,
            "start_line": start,
            "end_line": end,
            "source": "\n".join(lines[start - 1:end]),
        })
        if len(snippets) >= 6:
            break
    return tuple(snippets)


def recover_executable_targets(
    instance: Any,
    repository_index: Any,
    project_runner: BaseProjectRunner,
    generator_agent: Any,
    artifact_store: Any,
    *,
    max_target_candidates: int = 3,
    max_llm_reproduction_attempts: int = 2,
    max_stability_runs: int = 2,
    wall_time_seconds: float = 45.0,
    _cancel_event: threading.Event | None = None,
) -> TargetRecoveryResult:
    """Recover stable baseline targets without using official-only evidence."""

    if min(
        max_target_candidates, max_llm_reproduction_attempts,
        max_stability_runs, wall_time_seconds,
    ) <= 0:
        raise ValueError("target recovery budgets must be positive")
    started = time.monotonic()
    deadline = started + wall_time_seconds

    def expired() -> bool:
        return (
            (_cancel_event is not None and _cancel_event.is_set())
            or time.monotonic() >= deadline
        )

    def remaining(until: float | None = None) -> float:
        active_deadline = min(deadline, until) if until is not None else deadline
        return max(0.0, active_deadline - time.monotonic())

    issue = _public_issue(instance)
    root = _artifact_root(artifact_store)
    visible = tuple(
        str(item) for item in getattr(instance, "visible_tests", ())
        if not is_official_only_path(str(item))
    )
    related = _related_repository_tests(issue, repository_index, visible)
    checks: list[ExecutableCheck] = []
    checks.extend(project_runner.compile_visible_checks(visible))
    explicit_commands = tuple(
        tuple(map(str, command))
        for command in getattr(project_runner, "explicit_commands", ())
    )
    checks.extend(project_runner.compile_command_checks(explicit_commands))
    checks.extend(project_runner.compile_visible_checks(
        related, authority="PUBLIC_REPOSITORY_TEST",
    ))
    checks.extend(_code_block_checks(issue, root, project_runner))
    directed_requests = 0
    behavior = _behavior_reproduction(issue, root, project_runner)
    if behavior is not None:
        checks.append(behavior)

    annotated_checks: list[ExecutableCheck] = []
    for check in checks:
        selector_path = str(check.selector).split("::", 1)[0].replace("\\", "/")
        references = tuple(map(str, getattr(repository_index, "test_references", {}).get(
            selector_path, (),
        )))
        annotated_checks.append(replace(
            check,
            executed_symbol_ids=tuple(dict.fromkeys((
                *check.executed_symbol_ids,
                *references,
                *_command_called_symbols(check.command),
            ))),
        ))
    checks = annotated_checks

    unique: dict[str, ExecutableCheck] = {}
    for check in checks:
        unique.setdefault(check.check_id, check)

    targets: list[ExecutableCheck] = []
    preservation: list[ExecutableCheck] = []
    exploration_checks: list[ExecutableCheck] = []
    rejected: list[RejectedCheck] = []
    frontiers: list[EnvironmentFrontier] = []
    executions: list[CheckExecution] = []
    health_checks: list[EnvironmentHealth] = []
    def evaluate(
        check: ExecutableCheck,
        *,
        allow_trusted: bool = True,
        phase_deadline: float | None = None,
    ) -> None:
        available = remaining(phase_deadline)
        if expired() or available <= 0:
            return
        # BaseProjectRunner performs two stability runs.  Freeze a per-run
        # timeout from the shared phase deadline so one slow public check
        # cannot outlive target recovery and later overwrite its audit.
        per_run_timeout = max(
            0.25,
            (available - min(0.25, available / 10.0))
            / max(2, max_stability_runs),
        )
        bounded_check = replace(
            check,
            timeout_seconds=min(check.timeout_seconds, per_run_timeout),
        )
        health = project_runner.health_check(bounded_check)
        if expired() or (
            phase_deadline is not None and time.monotonic() >= phase_deadline
        ):
            return
        health_checks.append(health)
        execution = health.execution
        if execution is None:
            rejected.append(RejectedCheck(
                check.check_id, health.detail, None, check.selector,
            ))
            return
        executions.append(execution)
        if health.status != EnvironmentHealthStatus.HEALTHY:
            frontier = EnvironmentFrontier(
                frontier_id=stable_id(
                    "environment-frontier", check.check_id,
                    health.status.value, execution.execution_id,
                ),
                check_id=check.check_id,
                health_status=health.status,
                reason=health.detail,
                execution_id=execution.execution_id,
            )
            frontiers.append(frontier)
            rejected.append(RejectedCheck(
                check.check_id, health.detail, execution.execution_id, check.selector,
            ))
            return
        if execution.stable and execution.status == CheckStatus.FAIL:
            if _is_script_level_reproduction_failure(check, execution):
                rejected.append(RejectedCheck(
                    check.check_id,
                    "temporary reproduction failed before its observation assertion",
                    execution.execution_id,
                    check.selector,
                ))
                return
            if allow_trusted:
                targets.append(check.with_role(CheckRole.TARGET))
            else:
                exploration_checks.append(check.with_role(CheckRole.EXPLORATION))
        elif execution.stable and execution.status == CheckStatus.PASS:
            if allow_trusted:
                preservation.append(check.with_role(CheckRole.PRESERVATION))
            else:
                exploration_checks.append(check.with_role(CheckRole.EXPLORATION))
        else:
            rejected.append(RejectedCheck(
                check.check_id,
                f"baseline result is not a stable PASS or FAIL: {execution.status.value}",
                execution.execution_id,
                check.selector,
            ))

    public_phase_end = started + min(10.0, wall_time_seconds)
    for check in tuple(unique.values())[: max_target_candidates * 4]:
        if expired() or time.monotonic() >= public_phase_end:
            break
        evaluate(check, phase_deadline=public_phase_end)
        if len(targets) + len(preservation) >= max_target_candidates:
            break

    reproduction_method = getattr(
        generator_agent, "generate_target_reproduction", None,
    )
    if (
        not targets
        and callable(reproduction_method)
        and _issue_describes_executable_behavior(issue)
        and not expired()
    ):
        public_discussion = str(
            dict(getattr(instance, "public_metadata", {}) or {}).get(
                "hints_text", "",
            )
        ).strip()
        seen_proposals: set[str] = set()
        llm_phase_end = min(started + wall_time_seconds, time.monotonic() + 15.0)
        for attempt in range(max_llm_reproduction_attempts):
            if targets or expired() or time.monotonic() >= llm_phase_end:
                break
            directed_requests += 1
            transport = getattr(generator_agent, "transport", None)
            previous_timeout = getattr(
                transport, "request_timeout_seconds", None,
            )
            try:
                if previous_timeout is not None:
                    attempts_left = max(1, max_llm_reproduction_attempts - attempt)
                    transport.request_timeout_seconds = min(
                        float(previous_timeout),
                        max(0.25, remaining(llm_phase_end) / attempts_left),
                    )
                proposal = reproduction_method(
                    issue=str(getattr(instance, "issue", "")),
                    public_discussion=public_discussion,
                    source_context=_directed_source_context(
                        issue, repository_index, project_runner.repository,
                    ),
                    project_runner=project_runner.name,
                )
            finally:
                if previous_timeout is not None:
                    transport.request_timeout_seconds = previous_timeout
            if expired() or time.monotonic() >= llm_phase_end:
                break
            if proposal is None:
                continue
            proposal_key = stable_id(
                "directed-proposal",
                str(proposal.get("source", "")),
                str(proposal.get("expected_observation", "")),
            )
            if proposal_key in seen_proposals:
                continue
            seen_proposals.add(proposal_key)
            directed = _write_reproduction(
                root,
                Path(str(proposal["filename"])).name,
                str(proposal["source"]),
                project_runner,
                stable_id(
                    "directed-public-reproduction",
                    issue,
                    str(proposal["expected_observation"]),
                    attempt,
                ),
            )
            proposal_text = str(proposal.get("expected_observation", ""))
            contract_overlap = _proposal_uses_issue_oracle(
                issue, proposal_text,
            )
            if contract_overlap:
                directed = replace(
                    directed,
                    source_evidence_ids=tuple(dict.fromkeys((
                        *directed.source_evidence_ids,
                        "issue-contract-witness",
                    ))),
                )
            # Generated inputs are trusted only when the expected relation is
            # independently present in the issue (Authority B); otherwise the
            # reproduction remains an Authority E exploration probe.
            evaluate(
                directed,
                allow_trusted=contract_overlap,
                phase_deadline=llm_phase_end,
            )

    targets = targets[:max_target_candidates]
    preservation = preservation[:max_target_candidates]
    execution_by_id = {item.check_id: item for item in executions}

    def candidate_strategy(check: ExecutableCheck) -> str:
        evidence = " ".join(check.source_evidence_ids).lower()
        selector = str(check.selector).lower()
        if "issue-contract-witness" in evidence:
            return "issue_executable_witness"
        if "directed-public-reproduction" in evidence:
            return "llm_reproduction"
        if "issue-behavior" in evidence:
            return "return_or_exception_api_contract"
        if "code-block" in evidence or "reproduction" in selector:
            return "issue_executable_witness"
        if "test" in selector or "test" in evidence:
            return "related_public_test"
        if check.role == CheckRole.PRESERVATION:
            return "baseline_differential_relation"
        return "object_state_or_protocol_relation"

    def as_candidate(check: ExecutableCheck, *, trusted: bool) -> TargetCandidate:
        execution = execution_by_id.get(check.check_id)
        channels = (
            "process_status", "return_value", "exception", "stdout", "stderr",
        )
        contract = ObservationContract(
            contract_id=stable_id("target-observation-contract", check.check_id, channels),
            channels=channels,
            capture_protocol_selection=(
                "protocol" in " ".join(check.source_evidence_ids).lower()
            ),
        )
        oracle = None
        strategy = candidate_strategy(check)
        oracle_authority = (
            "A" if strategy == "related_public_test"
            else "B" if strategy in {
                "issue_executable_witness", "return_or_exception_api_contract",
            }
            else "C" if trusted
            else "E"
        )
        if trusted and execution is not None:
            relation = (
                    "baseline_failure_must_become_pass"
                    if check.role == CheckRole.TARGET
                    else "baseline_pass_must_be_preserved"
            )
            oracle = ExecutableOracle(
                oracle_id=stable_id(
                    "target-oracle", check.check_id, oracle_authority, relation,
                ),
                authority=oracle_authority,
                relation=relation,
                requirement_id=(
                    check.target_requirement_ids[0]
                    if check.target_requirement_ids else None
                ),
                is_executable=True,
            )
        return TargetCandidate(
            target_id=check.check_id,
            strategy=strategy,
            input_source=(
                "LLM_INPUT_SYNTHESIS_D"
                if "directed-public-reproduction" in " ".join(
                    check.source_evidence_ids
                ).lower() and trusted
                else "LLM_EXPLORATION_E" if not trusted
                else "PUBLIC_REPOSITORY" if strategy == "related_public_test"
                else "ISSUE_CONTRACT"
            ),
            oracle_authority=oracle_authority,
            setup_commands=(),
            command=check.command,
            observation_contract=contract,
            oracle=oracle,
            target_requirement_ids=check.target_requirement_ids,
            source_evidence_ids=check.source_evidence_ids,
            executed_symbol_ids=(
                execution.executed_symbol_ids if execution is not None else ()
            ),
            stability_runs=min(max_stability_runs, 2),
            status=("MECHANICALLY_VERIFIED" if trusted else "EXPLORATION_ONLY"),
            confidence=(0.95 if trusted else 0.35),
        )

    verified_candidates = tuple(
        as_candidate(check, trusted=True) for check in (*targets, *preservation)
    )
    verified_ids = {item.target_id for item in verified_candidates}
    exploration_candidates = tuple(
        as_candidate(check, trusted=False)
        for check in (*unique.values(), *exploration_checks)
        if check.check_id not in verified_ids
    )[:max_target_candidates]
    timed_out = expired()
    if targets:
        recovery_status = TargetRecoveryStatus.TARGET_AVAILABLE.value
    elif frontiers:
        recovery_status = TargetRecoveryStatus.TARGET_ENVIRONMENT_BLOCKED.value
    else:
        recovery_status = TargetRecoveryStatus.TARGET_UNAVAILABLE.value

    result = TargetRecoveryResult(
        targets=tuple(targets),
        preservation_checks=tuple(preservation),
        rejected_checks=tuple(rejected),
        environment_frontiers=tuple(frontiers),
        baseline_executions=tuple(executions),
        health_checks=tuple(health_checks),
        directed_reproduction_requests=directed_requests,
        status=recovery_status,
        candidates=verified_candidates,
        exploration_candidates=exploration_candidates,
        elapsed_seconds=time.monotonic() - started,
        timed_out=timed_out,
    )
    if _cancel_event is None or not _cancel_event.is_set():
        audit_path = root / "target_recovery_result.json"
        audit_path.write_text(
            json.dumps(result.to_dict(), sort_keys=True, indent=2), encoding="utf-8",
        )
    return result


def recover_executable_targets_bounded(
    instance: Any,
    repository_index: Any,
    project_runner: BaseProjectRunner,
    generator_agent: Any,
    artifact_store: Any,
    **kwargs: Any,
) -> TargetRecoveryResult:
    """Run optional recovery on a daemon worker so a wall timeout is real."""

    timeout = float(kwargs.get("wall_time_seconds", 45.0))
    # Leave bounded cleanup/audit headroom inside the configured public wall
    # time.  The externally visible timeout remains the configured value.
    worker_timeout = max(0.001, timeout - min(0.5, timeout / 10.0))
    worker_kwargs = dict(kwargs)
    worker_kwargs["wall_time_seconds"] = worker_timeout
    cancel_event = threading.Event()
    result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put((
                "ok",
                recover_executable_targets(
                    instance, repository_index, project_runner,
                    generator_agent, artifact_store,
                    _cancel_event=cancel_event,
                    **worker_kwargs,
                ),
            ))
        except BaseException as exc:  # propagate only on the caller thread
            result_queue.put(("error", exc))

    thread = threading.Thread(
        target=worker, name="reachpatch-target-recovery", daemon=True,
    )
    thread.start()
    thread.join(timeout=max(0.001, worker_timeout))
    if thread.is_alive():
        cancel_event.set()
        result = replace(
            TargetRecoveryResult.unavailable(timed_out=True),
            elapsed_seconds=worker_timeout,
        )
        root = _artifact_root(artifact_store)
        (root / "target_recovery_result.json").write_text(
            json.dumps(result.to_dict(), sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return result
    if result_queue.empty():
        return TargetRecoveryResult.unavailable()
    status, payload = result_queue.get()
    if status == "error":
        if type(payload).__name__ == "GeneratorBlockedExternal":
            raise payload
        raise payload
    return payload
