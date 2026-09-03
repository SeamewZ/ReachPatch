from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass
from pathlib import Path

from reachpatch.models.evidence import ActualDiff
from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.execution import MechanicalResult
from reachpatch.execution.trace import run_trace


_FORBIDDEN_ROOTS = ("tests/", "test/", "artifacts/", ".git/", "generated/")


@dataclass(frozen=True, slots=True)
class UndefinedNameFinding(SerializableRecord):
    file: str
    line: int
    column: int
    name: str
    scope: str
    source_line: str
    reason: str
    severity: str = "BLOCKER"


@dataclass(frozen=True, slots=True)
class MechanicalCommand(SerializableRecord):
    command: tuple[str, ...]
    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 120.0


def _bound_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.add(child.id)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
        elif isinstance(child, ast.alias):
            names.add(child.asname or child.name.split(".", 1)[0])
        elif isinstance(child, ast.ExceptHandler) and child.name:
            names.add(child.name)
        elif isinstance(child, ast.comprehension):
            names.update(
                item.id for item in ast.walk(child.target)
                if isinstance(item, ast.Name)
            )
    return names


class _DirectScopeBindings(ast.NodeVisitor):
    """Collect bindings owned by one lexical scope only."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(item.asname or item.name.split(".", 1)[0] for item in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(
            item.asname or item.name for item in node.names if item.name != "*"
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Lambda bodies form a nested scope; the enclosing scope collector
        # must not descend into them.
        del node
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        # Comprehensions are handled explicitly by _visit_comprehension.
        del node
        return

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp
    visit_DictComp = visit_ListComp


class _DirectScopeDirectives(ast.NodeVisitor):
    def __init__(self) -> None:
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Nested definitions are bindings of the enclosing scope; their local
        # names are collected when that scope is visited separately.
        del node
        return

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        del node
        return

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp
    visit_DictComp = visit_ListComp


def _direct_bound_names(node: ast.AST) -> set[str]:
    visitor = _DirectScopeBindings()
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        # Comprehensions execute in an implicit lexical scope. Their iterator
        # targets are bindings of that scope; visiting a missing body would
        # crash and miss this rule.
        for generator in node.generators:
            visitor.visit(generator.target)
        return visitor.names
    body = getattr(node, "body", ())
    children = body if isinstance(body, list) else ((body,) if isinstance(body, ast.AST) else ())
    for child in children:
        visitor.visit(child)
    return visitor.names


def _module_bindings(tree: ast.Module) -> tuple[set[str], bool]:
    bindings: set[str] = set()
    star_import = False
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            bindings.update(
                item.asname or item.name.split(".", 1)[0]
                for item in statement.names
            )
        elif isinstance(statement, ast.ImportFrom):
            for item in statement.names:
                if item.name == "*":
                    star_import = True
                else:
                    bindings.add(item.asname or item.name)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(statement.name)
        else:
            # Only collect bindings owned by the module.  ``ast.walk`` over a
            # function/class would incorrectly promote its local assignments
            # to globals and hide a newly introduced NameError.
            visitor = _DirectScopeBindings()
            visitor.visit(statement)
            bindings.update(visitor.names)
    return bindings, star_import


def _scope_name(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name
    if isinstance(node, ast.Lambda):
        return "<lambda>"
    if isinstance(node, ast.ClassDef):
        return node.name
    return "<module>"


class _UndefinedNameVisitor(ast.NodeVisitor):
    def __init__(
        self, tree: ast.Module, added_lines: set[int], source_lines: list[str],
        file: str, original_loads: set[str],
    ) -> None:
        self.module_bindings, self.star_import = _module_bindings(tree)
        self.added_lines = added_lines
        self.source_lines = source_lines
        self.file = file
        self.original_loads = original_loads
        self.scope_stack: list[tuple[str, set[str], set[str], set[str]]] = []
        self.findings: list[UndefinedNameFinding] = []

    def _scope_bindings(self, node: ast.AST) -> tuple[set[str], set[str], set[str]]:
        bindings = _direct_bound_names(node)
        directives = _DirectScopeDirectives()
        body = getattr(node, "body", ())
        children = body if isinstance(body, list) else ((body,) if isinstance(body, ast.AST) else ())
        for child in children:
            directives.visit(child)
        globals_, nonlocals = directives.globals, directives.nonlocals
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args
            bindings.update(argument.arg for argument in (
                *arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
            ))
            if arguments.vararg:
                bindings.add(arguments.vararg.arg)
            if arguments.kwarg:
                bindings.add(arguments.kwarg.arg)
        return bindings, globals_, nonlocals

    def _push(self, node: ast.AST) -> None:
        bindings, globals_, nonlocals = self._scope_bindings(node)
        self.scope_stack.append((_scope_name(node), bindings, globals_, nonlocals))

    def _pop(self) -> None:
        self.scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._push(node)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for statement in node.body:
            self.visit(statement)
        self._pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._push(node)
        self.visit(node.body)
        self._pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._push(node)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for statement in node.body:
            self.visit(statement)
        self._pop()

    def _visit_comprehension(self, node: ast.AST, generators, values) -> None:
        # The first iterable is evaluated before the implicit comprehension
        # scope; later iterables and the result see bound targets.
        if generators:
            self.visit(generators[0].iter)
        self._push(node)
        for index, generator in enumerate(generators):
            if index:
                self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self._pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, node.generators, (node.key, node.value))

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load) or node.lineno not in self.added_lines:
            return
        name = node.id
        if name in dir(builtins) or name in self.original_loads:
            return
        bound = set(self.module_bindings)
        scope_name = "<module>"
        for current_name, bindings, globals_, nonlocals in self.scope_stack:
            scope_name = current_name
            if name in globals_:
                bound = set(self.module_bindings)
                break
            if name in bindings and name not in nonlocals:
                bound = {name}
                break
        if name in bound:
            return
        self.findings.append(UndefinedNameFinding(
            self.file, node.lineno, node.col_offset, name, scope_name,
            self.source_lines[node.lineno - 1]
            if 0 < node.lineno <= len(self.source_lines) else "",
            "name is not bound in module or enclosing lexical scope",
            "UNKNOWN" if self.star_import else "BLOCKER",
        ))


def find_introduced_undefined_names(
    original_source: str,
    patched_source: str,
    added_line_numbers: set[int],
    file: str = "<source>",
) -> list[UndefinedNameFinding]:
    # Parse both versions and inspect only newly added source lines.
    try:
        original_tree = ast.parse(original_source, filename=file)
        patched_tree = ast.parse(patched_source, filename=file)
    except SyntaxError:
        return []
    original_loads = {
        node.id for node in ast.walk(original_tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    visitor = _UndefinedNameVisitor(
        patched_tree, set(added_line_numbers), patched_source.splitlines(),
        file, original_loads,
    )
    visitor.visit(patched_tree)
    unique: dict[tuple[str, int, str], UndefinedNameFinding] = {}
    for finding in visitor.findings:
        unique[(finding.file, finding.line, finding.name)] = finding
    return list(sorted(
        unique.values(), key=lambda item: (item.file, item.line, item.column, item.name),
    ))


def run_mechanical_checks(
    trial_tree: Path,
    cumulative_diff: ActualDiff,
    commands: tuple[tuple[str, ...], ...] = (),
    command_scenarios: tuple[MechanicalCommand, ...] = (),
    oracle_paths: tuple[str, ...] = (),
    source_tree: Path | None = None,
) -> MechanicalResult:
    reasons: list[str] = []
    results: list[dict[str, object]] = []
    undefined_findings: list[UndefinedNameFinding] = []
    protected = {
        path.replace("\\", "/").removeprefix("./")
        for path in oracle_paths
    }
    forbidden = any(path.startswith(_FORBIDDEN_ROOTS) for path in cumulative_diff.changed_files)
    contamination = bool(protected.intersection(cumulative_diff.changed_files))
    if forbidden:
        reasons.append("trial edits tests, artifacts, generated files, or repository metadata")
    if contamination:
        reasons.append("trial contains non-public oracle material")
    for relative in cumulative_diff.changed_files:
        path = trial_tree / relative
        if path.suffix != ".py" or not path.is_file():
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=relative)
        except SyntaxError as exc:
            reasons.append(f"syntax error in {relative}:{exc.lineno}: {exc.msg}")
        if source_tree is not None:
            original = source_tree / relative
            if original.is_file():
                try:
                    before_tree = ast.parse(
                        original.read_text(encoding="utf-8", errors="replace"),
                        filename=relative,
                    )
                    after_tree = ast.parse(
                        path.read_text(encoding="utf-8", errors="replace"),
                        filename=relative,
                    )
                except SyntaxError:
                    # The syntax diagnostics above remain authoritative.
                    continue
                if ast.dump(before_tree, include_attributes=False) == ast.dump(
                    after_tree, include_attributes=False,
                ):
                    reasons.append(
                        f"no executable AST change in {relative}; the patch is "
                        "comments/formatting or an unchanged source excerpt"
                    )
                added_lines = {
                    line for hunk in cumulative_diff.hunks
                    if hunk.path == relative for line in hunk.changed_new_lines
                }
                undefined_findings.extend(find_introduced_undefined_names(
                    original.read_text(encoding="utf-8", errors="replace"),
                    path.read_text(encoding="utf-8", errors="replace"),
                    added_lines,
                    relative,
                ))
    scenarios = tuple(command_scenarios) + tuple(
        MechanicalCommand(command=command)
        for command in commands
    )
    for scenario in scenarios:
        # Mechanical command execution shares the same backend as challenges,
        # probes and generator validations.  In particular, cwd, environment
        # and timeout are not silently replaced with host-process defaults.
        trace = run_trace(
            trial_tree, scenario.command, cwd=scenario.cwd,
            environment=scenario.environment,
            timeout_seconds=scenario.timeout_seconds, trace_enabled=False,
        )
        observation = trace.observation
        backend = (
            "CONTAINER"
            if dict(scenario.environment).get("REACHPATCH_EXECUTION_IMAGE")
            else "HOST"
        )
        results.append({
            "command": scenario.command,
            "cwd": scenario.cwd,
            "environment": scenario.environment,
            "timeout_seconds": scenario.timeout_seconds,
            "backend": backend,
            "return_code": observation.return_code,
            "stdout": observation.stdout[-4000:],
            "stderr": observation.stderr[-4000:],
            "duration_seconds": observation.duration_seconds,
            "timeout": observation.exception == "TIMEOUT",
            "first_project_frame": trace.first_project_frame,
        })
        if observation.return_code != 0:
            reasons.append(
                f"mechanical command failed: {' '.join(scenario.command)}"
            )
    removed_public = {
        line[1:].split("(", 1)[0].split(":", 1)[0].strip()
        for hunk in cumulative_diff.hunks for line in hunk.lines
        if line.startswith(("-def ", "-class "))
    }
    added_public = {
        line[1:].split("(", 1)[0].split(":", 1)[0].strip()
        for hunk in cumulative_diff.hunks for line in hunk.lines
        if line.startswith(("+def ", "+class "))
    }
    unsafe_api = bool(removed_public - added_public)
    if unsafe_api:
        reasons.append("trial deletes a public definition")
    definite_undefined = tuple(
        finding for finding in undefined_findings if finding.severity == "BLOCKER"
    )
    if definite_undefined:
        reasons.extend(
            f"introduced undefined name {finding.name} in {finding.file}:{finding.line}"
            for finding in definite_undefined
        )
    static_blocker_ids = tuple(sorted(
        stable_id("mechanical-blocker", finding.file, finding.line, finding.name)
        for finding in definite_undefined
    ))
    import_smoke_failures = tuple(
        item for item in results
        if item.get("return_code") not in {0, None}
        and any(token in str(item.get("stderr", "")) for token in (
            "ImportError", "ModuleNotFoundError", "NameError",
        ))
    )
    return MechanicalResult(
        passed=not reasons,
        failure_reasons=tuple(reasons),
        forbidden_edit=forbidden,
        oracle_contamination=contamination,
        unsafe_api_break=unsafe_api,
        high_risk_side_effect=False,
        command_results=tuple(results),
        undefined_name_findings=tuple(undefined_findings),
        import_smoke_failures=import_smoke_failures,
        static_blocker_ids=static_blocker_ids,
    )
