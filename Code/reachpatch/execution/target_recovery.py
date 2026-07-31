from __future__ import annotations

import ast
import json
import os
import re
import threading
import time
from queue import Queue
from dataclasses import dataclass
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
    RejectedCheck,
)
from reachpatch.execution.runners import BaseProjectRunner
from reachpatch.models.base import SerializableRecord, stable_id
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
    authority: str
    setup_commands: tuple[str, ...]
    command: str
    observation_contract: ObservationContract
    expected_relation: dict[str, Any] | None
    source_evidence: tuple[str, ...]
    stability_runs: int
    confidence: float
    status: str


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
    path = reproduction_root / name
    path.write_text(source.rstrip() + "\n", encoding="utf-8")
    check_id = stable_id("temporary-public-reproduction", evidence_id, source)
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
    )


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
    wall_time_seconds: float = 90.0,
) -> TargetRecoveryResult:
    """Recover stable baseline targets without using official-only evidence."""

    if min(
        max_target_candidates, max_llm_reproduction_attempts,
        max_stability_runs, wall_time_seconds,
    ) <= 0:
        raise ValueError("target recovery budgets must be positive")
    started = time.monotonic()

    def expired() -> bool:
        return time.monotonic() - started >= wall_time_seconds

    issue = _public_issue(instance)
    root = _artifact_root(artifact_store)
    visible = tuple(
        str(item) for item in getattr(instance, "visible_tests", ())
        if not is_official_only_path(str(item))
    )
    related = _related_repository_tests(issue, repository_index, visible)
    checks: list[ExecutableCheck] = []
    checks.extend(project_runner.compile_visible_checks(visible))
    checks.extend(project_runner.compile_visible_checks(
        related, authority="PUBLIC_REPOSITORY_TEST",
    ))
    explicit_commands = tuple(
        tuple(map(str, command))
        for command in getattr(project_runner, "explicit_commands", ())
    )
    checks.extend(project_runner.compile_command_checks(explicit_commands))
    checks.extend(_code_block_checks(issue, root, project_runner))
    directed_requests = 0
    behavior = _behavior_reproduction(issue, root, project_runner)
    if behavior is not None:
        checks.append(behavior)

    unique: dict[str, ExecutableCheck] = {}
    for check in checks:
        unique.setdefault(check.check_id, check)

    targets: list[ExecutableCheck] = []
    preservation: list[ExecutableCheck] = []
    rejected: list[RejectedCheck] = []
    frontiers: list[EnvironmentFrontier] = []
    executions: list[CheckExecution] = []
    health_checks: list[EnvironmentHealth] = []
    def evaluate(check: ExecutableCheck) -> None:
        health = project_runner.health_check(check)
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
            targets.append(check.with_role(CheckRole.TARGET))
        elif execution.stable and execution.status == CheckStatus.PASS:
            preservation.append(check.with_role(CheckRole.PRESERVATION))
        else:
            rejected.append(RejectedCheck(
                check.check_id,
                f"baseline result is not a stable PASS or FAIL: {execution.status.value}",
                execution.execution_id,
                check.selector,
            ))

    for check in tuple(unique.values())[: max_target_candidates * 4]:
        if expired():
            break
        evaluate(check)

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
        for attempt in range(max_llm_reproduction_attempts):
            if targets or expired():
                break
            directed_requests += 1
            proposal = reproduction_method(
                issue=str(getattr(instance, "issue", "")),
                public_discussion=public_discussion,
                source_context=_directed_source_context(
                    issue, repository_index, project_runner.repository,
                ),
                project_runner=project_runner.name,
            )
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
            evaluate(directed)

    targets = targets[:max_target_candidates]
    preservation = preservation[:max_target_candidates]
    execution_by_id = {item.check_id: item for item in executions}

    def candidate_strategy(check: ExecutableCheck) -> str:
        evidence = " ".join(check.source_evidence_ids).lower()
        selector = str(check.selector).lower()
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
        relation = None
        if trusted and execution is not None:
            relation = {
                "kind": (
                    "baseline_failure_must_become_pass"
                    if check.role == CheckRole.TARGET
                    else "baseline_pass_must_be_preserved"
                ),
                "baseline_status": execution.status.value,
                "expected_status": "PASS",
            }
        return TargetCandidate(
            target_id=check.check_id,
            strategy=candidate_strategy(check),
            authority=check.authority,
            setup_commands=(),
            command=" ".join(check.command),
            observation_contract=contract,
            expected_relation=relation,
            source_evidence=check.source_evidence_ids,
            stability_runs=min(max_stability_runs, 2),
            confidence=(0.95 if trusted else 0.35),
            status=("MECHANICALLY_VERIFIED" if trusted else "EXPLORATION_ONLY"),
        )

    verified_candidates = tuple(
        as_candidate(check, trusted=True) for check in (*targets, *preservation)
    )
    verified_ids = {item.target_id for item in verified_candidates}
    exploration_candidates = tuple(
        as_candidate(check, trusted=False)
        for check in unique.values()
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

    timeout = float(kwargs.get("wall_time_seconds", 90.0))
    result_queue: Queue[tuple[str, Any]] = Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put((
                "ok",
                recover_executable_targets(
                    instance, repository_index, project_runner,
                    generator_agent, artifact_store, **kwargs,
                ),
            ))
        except BaseException as exc:  # propagate only on the caller thread
            result_queue.put(("error", exc))

    thread = threading.Thread(
        target=worker, name="reachpatch-target-recovery", daemon=True,
    )
    thread.start()
    thread.join(timeout=max(0.001, timeout))
    if thread.is_alive():
        return TargetRecoveryResult.unavailable(timed_out=True)
    if result_queue.empty():
        return TargetRecoveryResult.unavailable()
    status, payload = result_queue.get()
    if status == "error":
        if type(payload).__name__ == "GeneratorBlockedExternal":
            raise payload
        raise payload
    return payload
