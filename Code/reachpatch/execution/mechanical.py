from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.controller import MechanicalCheck
from reachpatch.models.enums import OutcomeStatus


@dataclass(frozen=True, slots=True)
class PublicCheckComparison(SerializableRecord):
    check_id: str
    command: tuple[str, ...]
    classification: str
    baseline_return_code: int | None
    patched_return_code: int | None
    baseline_stdout: str
    baseline_stderr: str
    patched_stdout: str
    patched_stderr: str
    duration_seconds: float

    @property
    def preservation_regression(self) -> bool:
        return self.classification == "PRESERVATION_REGRESSION"

    @property
    def target_fixed(self) -> bool:
        return self.classification == "TARGET_FIXED"


def execution_environment_blocked(stdout: str, stderr: str) -> bool:
    """Recognize infrastructure failures that cannot validate a source diff."""

    diagnostic = f"{stdout}\n{stderr}".lower()
    literal_markers = (
        "no module named",
        "modulenotfounderror",
        "command not found",
        "no such file or directory",
        "failed to create process",
        "importerror while loading conftest",
        "connection refused",
        "could not connect to server",
        "temporary failure in name resolution",
        "no tests ran",
        "collected 0 items",
    )
    return (
        any(marker in diagnostic for marker in literal_markers)
        or re.search(r"fixture ['\"][^'\"]+['\"] not found", diagnostic)
        is not None
        or re.search(r"cannot import name ['\"]_[a-z0-9_]+", diagnostic)
        is not None
    )


def _source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for current, directories, names in os.walk(root):
        directories[:] = sorted(
            name for name in directories
            if name not in {".git", ".venv", "venv", "__pycache__", ".reachpatch"}
        )
        for name in sorted(names):
            if not name.endswith((".py", ".pyi", ".toml", ".cfg", ".ini")):
                continue
            path = Path(current) / name
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _syntax_check(root: Path, actual_diff: ActualDiff, source_hash: str) -> MechanicalCheck:
    started = time.monotonic()
    errors: list[str] = []
    checked: list[str] = []
    python_files = {
        relative
        for relative in actual_diff.changed_files
        if relative.endswith((".py", ".pyi")) and relative not in actual_diff.deleted_files
    }
    for relative in sorted(python_files):
        path = root / relative
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=relative, type_comments=True)
            checked.append(relative)
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{relative}: {exc}")
    status = OutcomeStatus.PASS if not errors and actual_diff.applies else OutcomeStatus.FAIL
    return MechanicalCheck(
        check_id=stable_id("mechanical", "syntax", actual_diff.diff_id, source_hash),
        kind="SYNTAX",
        command=("ast.parse", *sorted(python_files)),
        status=status,
        return_code=0 if status == OutcomeStatus.PASS else 1,
        stdout="\n".join(checked),
        stderr="\n".join(errors),
        duration_seconds=time.monotonic() - started,
        source_hash=source_hash,
    )


def _scope_check(actual_diff: ActualDiff, source_hash: str) -> MechanicalCheck:
    forbidden = actual_diff.forbidden_paths + actual_diff.oracle_contamination_paths
    status = OutcomeStatus.PASS if not forbidden else OutcomeStatus.FAIL
    return MechanicalCheck(
        check_id=stable_id("mechanical", "scope", actual_diff.diff_id, forbidden),
        kind="SCOPE_AND_ORACLE_INTEGRITY",
        command=("internal:scope-check",),
        status=status,
        return_code=0 if status == OutcomeStatus.PASS else 1,
        stdout="",
        stderr="\n".join(forbidden),
        duration_seconds=0.0,
        source_hash=source_hash,
    )


class _KeywordConflictVisitor(ast.NodeVisitor):
    """Detect parseable keyword collisions introduced through ``kwargs``."""

    def __init__(self) -> None:
        self.function_stack: list[str] = []
        self.defaults: dict[str, set[str]] = {}
        self.conflicts: list[tuple[str, str, int]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        function = self.function_stack[-1] if self.function_stack else "<module>"
        explicit_names = [item.arg for item in node.keywords if item.arg is not None]
        if len(explicit_names) != len(set(explicit_names)):
            self.conflicts.append((function, "<duplicate-keyword>", int(getattr(node, "lineno", 0))))
        if isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault":
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id in {"kwargs", "options", "params"}:
                if node.args and isinstance(node.args[0], ast.Constant):
                    key = node.args[0].value
                    if isinstance(key, str):
                        self.defaults.setdefault(owner.id, set()).add(key)
        expanded = {
            item.value.id
            for item in node.keywords
            if item.arg is None and isinstance(item.value, ast.Name)
        }
        explicit = {item.arg for item in node.keywords if item.arg is not None}
        for mapping in expanded:
            for key in self.defaults.get(mapping, ()):
                if key in explicit:
                    self.conflicts.append((function, key, int(getattr(node, "lineno", 0))))
        self.generic_visit(node)


def _command_option_names(path: Path) -> set[str]:
    """Recover mechanically declared options from a literal command module."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return set()
    options: set[str] = set()
    option_mappings = {"options", "kwargs", "params"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_argument":
                for argument in node.args:
                    if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                        continue
                    raw = argument.value
                    if raw.startswith("-"):
                        options.add(raw.lstrip("-").replace("-", "_"))
                    elif raw.isidentifier():
                        options.add(raw)
                for keyword in node.keywords:
                    if (
                        keyword.arg == "dest"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        options.add(keyword.value.value)
            owner = node.func.value
            if (
                node.func.attr in {"get", "pop", "setdefault"}
                and isinstance(owner, ast.Name)
                and owner.id in option_mappings
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                options.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id not in option_mappings:
                continue
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                options.add(key.value)
    return options


def _literal_command_modules(root: Path, command_name: str, *, limit: int = 8) -> tuple[Path, ...]:
    """Find a bounded set of public command implementations by file name."""

    target = f"{command_name}.py"
    matches: list[Path] = []
    for current, directories, files in os.walk(root):
        directories[:] = sorted(
            name for name in directories
            if name not in {".git", ".venv", "venv", "__pycache__", ".reachpatch"}
        )
        if target in files:
            matches.append(Path(current) / target)
            if len(matches) >= limit:
                break
    return tuple(matches)


def _unsupported_literal_command_options(
    root: Path,
    tree: ast.AST,
    relative: str,
    added_lines: set[int],
) -> tuple[str, ...]:
    """Reject invented flags on literal command dispatch calls.

    Dispatch helpers commonly accept ``**options``, so Python signature checks
    cannot reject a fabricated option. A newly supplied flag is accepted only
    when the command's public parser/option accesses mechanically declare it,
    apart from the dispatch framework's common execution controls.
    """

    errors: list[str] = []
    option_cache: dict[str, set[str] | None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or int(getattr(node, "lineno", 0)) not in added_lines:
            continue
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        if "command" not in function_name.lower() or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        supplied = {keyword.arg for keyword in node.keywords if keyword.arg is not None}
        command_name = first.value.replace("-", "_")
        if command_name not in option_cache:
            modules = _literal_command_modules(root, first.value)
            if not modules:
                option_cache[command_name] = None
            else:
                option_cache[command_name] = set().union(
                    *(_command_option_names(path) for path in modules)
                )
        declared = option_cache[command_name]
        framework_options = {
            "stdout", "stderr", "no_color", "force_color", "skip_checks",
            "traceback",
        }
        unsupported = sorted(
            supplied - (declared or set()) - framework_options
        ) if declared is not None else []
        for option in unsupported:
            errors.append(
                f"{relative}:{getattr(node, 'lineno', 0)}: command {first.value!r} "
                f"does not declare option {option!r}; remove the invented flag "
                "or derive the call from an executable public command contract"
            )
    return tuple(errors)


def _structural_check(
    root: Path,
    actual_diff: ActualDiff,
    source_hash: str,
    baseline_root: Path | None = None,
) -> MechanicalCheck:
    """Reject silent class/function shadowing and guaranteed keyword errors.

    These failures pass ``ast.parse`` and often pass an import check, but make
    the generated module observably different from the intended edit.  The
    check is restricted to changed Python files and therefore remains a
    bounded patch-quality gate rather than a repository-wide lint pass.
    """
    started = time.monotonic()
    errors: list[str] = []
    advisories: list[str] = []
    checked: list[str] = []
    added_lines_by_file = {
        relative: {
            line
            for hunk in actual_diff.hunks if hunk.file == relative
            for line in range(hunk.new_start, hunk.new_start + max(1, hunk.new_count))
        }
        for relative in actual_diff.changed_files
    }
    for relative in sorted(actual_diff.changed_files):
        if not relative.endswith(".py") or relative in actual_diff.deleted_files:
            continue
        path = root / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative, type_comments=True)
        except (OSError, SyntaxError, UnicodeError):
            continue
        checked.append(relative)
        from reachpatch.repair.tools import (
            _binary_protocol_coercion_errors,
            _caller_owned_mutation_errors,
            _partial_rectangular_index_fix_errors,
            _placeholder_definition_errors,
            _reversed_set_operation_errors,
            _shadowing_definition_errors,
        )
        baseline_tree: ast.AST = ast.Module(body=[], type_ignores=[])
        if baseline_root is not None:
            baseline_path = baseline_root / relative
            try:
                baseline_tree = ast.parse(
                    baseline_path.read_text(encoding="utf-8"),
                    filename=relative,
                    type_comments=True,
                )
            except (OSError, SyntaxError, UnicodeError):
                pass
        for placeholder in sorted(
            _placeholder_definition_errors(tree)
            - _placeholder_definition_errors(baseline_tree)
        ):
            errors.append(f"{relative}: {placeholder}")
        for reversed_set in sorted(
            _reversed_set_operation_errors(tree)
            - _reversed_set_operation_errors(baseline_tree)
        ):
            errors.append(f"{relative}: {reversed_set}")
        for alias_mutation in sorted(
            _caller_owned_mutation_errors(tree)
            - _caller_owned_mutation_errors(baseline_tree)
        ):
            errors.append(f"{relative}: {alias_mutation}")
        for protocol_coercion in sorted(
            _binary_protocol_coercion_errors(tree)
            - _binary_protocol_coercion_errors(baseline_tree)
        ):
            errors.append(f"{relative}: {protocol_coercion}")
        for partial_index_repair in sorted(
            _partial_rectangular_index_fix_errors(baseline_tree, tree)
        ):
            errors.append(f"{relative}: {partial_index_repair}")
        for shadowing in sorted(
            _shadowing_definition_errors(tree)
            - _shadowing_definition_errors(baseline_tree)
        ):
            errors.append(f"{relative}: {shadowing}")
        visitor = _KeywordConflictVisitor()
        visitor.visit(tree)
        baseline_visitor = _KeywordConflictVisitor()
        baseline_visitor.visit(baseline_tree)
        for function, key, line in sorted(
            set(visitor.conflicts) - set(baseline_visitor.conflicts)
        ):
            errors.append(
                (
                    f"{relative}:{line}: duplicate keyword arguments in {function}"
                    if key == "<duplicate-keyword>" else
                    f"{relative}:{line}: kwargs.setdefault({key!r}) conflicts with explicit keyword in {function}"
                )
            )
        errors.extend(_unsupported_literal_command_options(
            root, tree, relative, added_lines_by_file.get(relative, set()),
        ))

        def assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                return None
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if len(targets) != 1 or node.value is None:
                return None
            return ast.unparse(targets[0]), node.value

        def walk_statement_list(statements: list[ast.stmt]) -> None:
            for index, statement in enumerate(statements):
                if (
                    isinstance(statement, ast.If)
                    and int(getattr(statement, "lineno", 0))
                    in added_lines_by_file.get(relative, set())
                    and index > 0
                ):
                    previous = assignment(statements[index - 1])
                    if previous is not None:
                        state_target, previous_value = previous
                        previous_text = ast.unparse(previous_value)
                        if ".difference(" in previous_text and state_target in ast.unparse(statement.test):
                            difference_calls = [
                                child for child in ast.walk(previous_value)
                                if isinstance(child, ast.Call)
                                and isinstance(child.func, ast.Attribute)
                                and child.func.attr == "difference"
                            ]
                            removed_inputs = {
                                child.id
                                for call in difference_calls
                                for argument in call.args
                                for child in ast.walk(argument)
                                if isinstance(child, ast.Name)
                            }
                            branch_assignments = [
                                current
                                for branch in (statement.body, statement.orelse)
                                for child in branch
                                if (current := assignment(child)) is not None
                            ]
                            derived_state_targets = {
                                target
                                for target, value in branch_assignments
                                if state_target in {
                                    item.id for item in ast.walk(value)
                                    if isinstance(item, ast.Name)
                                }
                            }
                            candidate_targets = derived_state_targets or {state_target}
                            for child in (*statement.body, *statement.orelse):
                                current = assignment(child)
                                if current is None or current[0] not in candidate_targets:
                                    continue
                                replacement_text = ast.unparse(current[1])
                                replacement_names = {
                                    item.id for item in ast.walk(current[1])
                                    if isinstance(item, ast.Name)
                                }
                                if (
                                    removed_inputs & replacement_names
                                    and ".difference(" not in replacement_text
                                ):
                                    errors.append(
                                        f"{relative}:{getattr(child, 'lineno', 0)}: state transition "
                                        "reintroduces the raw input after a difference operation; "
                                        "compute both existing-minus-incoming and "
                                        "incoming-minus-existing residuals; a mode switch may carry "
                                        "only the incoming-only residual; audit every empty-state "
                                        "producer before changing a consumer guard and verify "
                                        "empty/non-empty and chained/batched equivalence"
                                    )
                                previous_mode = (
                                    previous_value.elts[-1].value
                                    if isinstance(previous_value, ast.Tuple)
                                    and previous_value.elts
                                    and isinstance(previous_value.elts[-1], ast.Constant)
                                    and isinstance(previous_value.elts[-1].value, bool)
                                    else None
                                )
                                replacement_mode = (
                                    current[1].elts[-1].value
                                    if isinstance(current[1], ast.Tuple)
                                    and current[1].elts
                                    and isinstance(current[1].elts[-1], ast.Constant)
                                    and isinstance(current[1].elts[-1].value, bool)
                                    else None
                                )
                                if (
                                    previous_mode is not None
                                    and replacement_mode is not None
                                    and previous_mode != replacement_mode
                                    and state_target in replacement_text
                                    and ".difference(" not in replacement_text
                                ):
                                    errors.append(
                                        f"{relative}:{getattr(child, 'lineno', 0)}: empty difference "
                                        "state only flips its companion mode/tag while retaining the "
                                        "same empty value; compute the incoming-only residual before "
                                        "switching modes and audit no-argument/reset producers before "
                                        "changing a consumer emptiness guard"
                                    )
                nested_lists = [
                    value for _field, value in ast.iter_fields(statement)
                    if isinstance(value, list) and value
                    and all(isinstance(item, ast.stmt) for item in value)
                ]
                for nested in nested_lists:
                    walk_statement_list(nested)

        walk_statement_list(tree.body)
    # A newly added conjunct involving a collection/arity literal is a common
    # preservation risk, but it is not a mechanical failure. Keep the risk in
    # the check evidence so ActiveBindingGraph/DICC can inspect sibling
    # partitions, while allowing real paired public checks to decide whether
    # the narrowed guard is actually wrong.
    for relation in getattr(actual_diff, "changed_relations", ()):
        old = str(getattr(relation, "old_source", "") or "")
        new = str(getattr(relation, "new_source", "") or "")
        if not old or not new or " and " not in new or " and " in old:
            continue
        if not any(token in new for token in ("len(", "isinstance(", "type(")):
            continue
        old_terms = {part.strip() for part in old.split(" and ")}
        new_terms = {part.strip() for part in new.split(" and ")}
        added_terms = sorted(new_terms - old_terms)
        if added_terms:
            advisories.append(
                f"{getattr(relation, 'file', '<changed-file>')}: guard narrowed by new conjunct(s) "
                f"{added_terms}; inspect sibling input partitions and preservation behavior"
            )
    status = OutcomeStatus.PASS if not errors else OutcomeStatus.FAIL
    return MechanicalCheck(
        check_id=stable_id(
            "mechanical", "structural", actual_diff.diff_id, source_hash,
            errors, advisories,
        ),
        kind="STRUCTURAL",
        command=("internal:structural-check", *checked),
        status=status,
        return_code=0 if status == OutcomeStatus.PASS else 1,
        stdout="\n".join((*checked, *advisories)),
        stderr="\n".join(errors),
        duration_seconds=time.monotonic() - started,
        source_hash=source_hash,
    )


def _import_check(
    root: Path,
    actual_diff: ActualDiff,
    source_hash: str,
    timeout: float,
    baseline_root: Path | None,
) -> MechanicalCheck:
    modules = []
    for relative in actual_diff.changed_files:
        path = Path(relative)
        if (
            path.suffix != ".py" or relative in actual_diff.deleted_files
            or "tests" in path.parts or path.name.startswith("test_")
        ):
            continue
        parts = list(path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        if parts and all(part.isidentifier() for part in parts):
            modules.append(".".join(parts))
    started = time.monotonic()
    if not modules:
        return MechanicalCheck(
            check_id=stable_id("mechanical", "import", actual_diff.diff_id, ()),
            kind="IMPORT",
            command=("internal:no-importable-changed-module",),
            status=OutcomeStatus.PASS,
            return_code=0,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started,
            source_hash=source_hash,
        )
    code = (
        "import importlib\n"
        + "\n".join(f"importlib.import_module({module!r})" for module in sorted(set(modules)))
    )
    def execute_import(check_root: Path):
        return subprocess.run(
            (sys.executable, "-c", code), cwd=check_root,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
            capture_output=True, text=True, timeout=min(timeout, 60.0),
            check=False,
        )

    kind = "IMPORT"
    try:
        process = execute_import(root)
        status = OutcomeStatus.PASS if process.returncode == 0 else OutcomeStatus.FAIL
        return_code = process.returncode
        stdout, stderr = process.stdout, process.stderr
        if status == OutcomeStatus.FAIL and baseline_root is not None:
            baseline = execute_import(baseline_root)
            if baseline.returncode != 0:
                kind = "IMPORT_BASELINE_BLOCKED"
                status = OutcomeStatus.PASS
                return_code = 0
                stderr = (
                    "baseline import was already blocked; no confirmed import regression\n"
                    f"BASELINE:\n{baseline.stderr}\nTRIAL:\n{process.stderr}"
                )
    except subprocess.TimeoutExpired as exc:
        status = OutcomeStatus.UNKNOWN_EXECUTION
        return_code = None
        stdout, stderr = str(exc.stdout or ""), str(exc.stderr or "")
    return MechanicalCheck(
        check_id=stable_id("mechanical", "import", modules, source_hash, status),
        kind=kind,
        command=(sys.executable, "-c", code), status=status,
        return_code=return_code, stdout=stdout, stderr=stderr,
        duration_seconds=time.monotonic() - started, source_hash=source_hash,
    )


def _command_check(root: Path, command: tuple[str, ...], source_hash: str, timeout: float) -> MechanicalCheck:
    started = time.monotonic()
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    try:
        process = subprocess.run(
            command,
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        status = OutcomeStatus.PASS if process.returncode == 0 else OutcomeStatus.FAIL
        return_code = process.returncode
        stdout = process.stdout
        stderr = process.stderr
    except subprocess.TimeoutExpired as exc:
        status = OutcomeStatus.UNKNOWN
        return_code = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
    return MechanicalCheck(
        check_id=stable_id("mechanical", command, source_hash, status),
        kind="COMMAND",
        command=command,
        status=status,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=time.monotonic() - started,
        source_hash=source_hash,
    )


def run_mechanical_checks(
    trial_root: str | Path,
    actual_diff: ActualDiff,
    *,
    commands: Iterable[Iterable[str]] = (),
    timeout_seconds: float = 300.0,
    baseline_root: str | Path | None = None,
) -> tuple[MechanicalCheck, ...]:
    root = Path(trial_root).resolve()
    source_hash = _source_hash(root)
    checks = [
        _syntax_check(root, actual_diff, source_hash),
        _scope_check(actual_diff, source_hash),
        _structural_check(
            root,
            actual_diff,
            source_hash,
            Path(baseline_root).resolve() if baseline_root is not None else None,
        ),
        _import_check(
            root, actual_diff, source_hash, timeout_seconds,
            Path(baseline_root).resolve() if baseline_root is not None else None,
        ),
    ]
    checks.extend(
        _command_check(root, tuple(command), source_hash, timeout_seconds)
        for command in commands
    )
    return tuple(checks)


def run_public_checks_paired(
    baseline_root: str | Path,
    patched_root: str | Path,
    commands: Iterable[Iterable[str]],
    *,
    timeout_seconds: float = 120.0,
) -> tuple[PublicCheckComparison, ...]:
    """Run public checks on both trees and classify change, not just exit status."""

    baseline = Path(baseline_root).resolve()
    patched = Path(patched_root).resolve()
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }

    def execute(command: tuple[str, ...], root: Path):
        try:
            process = subprocess.run(
                command, cwd=root, env=environment, capture_output=True,
                text=True, timeout=timeout_seconds, check=False, shell=False,
            )
            return process.returncode, process.stdout[-12000:], process.stderr[-12000:], None
        except subprocess.TimeoutExpired as exc:
            return None, str(exc.stdout or "")[-12000:], str(exc.stderr or "")[-12000:], "TIMEOUT"
        except OSError as exc:
            return None, "", str(exc)[-12000:], "BLOCKED_EXTERNAL"

    comparisons: list[PublicCheckComparison] = []

    for raw_command in commands:
        command = tuple(map(str, raw_command))
        if not command:
            continue
        started = time.monotonic()
        base_rc, base_out, base_err, base_error = execute(command, baseline)
        patch_rc, patch_out, patch_err, patch_error = execute(command, patched)
        if base_error == "TIMEOUT" or patch_error == "TIMEOUT":
            classification = "UNKNOWN_EXECUTION"
        elif base_error or patch_error:
            classification = "BLOCKED_EXTERNAL"
        elif execution_environment_blocked(base_out, base_err) or execution_environment_blocked(
            patch_out, patch_err
        ):
            classification = "BLOCKED_EXTERNAL"
        elif base_rc == 0 and patch_rc == 0:
            classification = "PASS_PRESERVED"
        elif base_rc != 0 and patch_rc == 0:
            classification = "TARGET_FIXED"
        elif base_rc == 0 and patch_rc != 0:
            classification = "PRESERVATION_REGRESSION"
        else:
            classification = "STABLE_FAIL"
        comparisons.append(PublicCheckComparison(
            check_id=stable_id(
                "public-check-comparison", command, base_rc, patch_rc,
                classification, base_out, base_err, patch_out, patch_err,
            ),
            command=command,
            classification=classification,
            baseline_return_code=base_rc,
            patched_return_code=patch_rc,
            baseline_stdout=base_out,
            baseline_stderr=base_err,
            patched_stdout=patch_out,
            patched_stderr=patch_err,
            duration_seconds=time.monotonic() - started,
        ))
    return tuple(comparisons)


def mechanical_pass(checks: Iterable[MechanicalCheck]) -> bool:
    selected = tuple(checks)
    return bool(selected) and all(item.status == OutcomeStatus.PASS for item in selected)
