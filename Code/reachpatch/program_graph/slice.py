from __future__ import annotations

import ast
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from reachpatch.execution.reconcile import ActualDiff
from reachpatch.models.base import SerializableRecord
from reachpatch.program_graph.budget import GraphBudget, current_rss_mib
from reachpatch.program_graph.builder import PythonProgramGraphBuilder
from reachpatch.program_graph.index import RepositoryIndex
from reachpatch.program_graph.models import ProgramGraph

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_QUOTED = re.compile(r"[`'\"]([A-Za-z_][A-Za-z0-9_.]*)[`'\"]")


@dataclass(frozen=True, slots=True)
class SourceLocation(SerializableRecord):
    relative_path: str
    line: int
    end_line: int
    symbol: str | None = None


@dataclass(frozen=True, slots=True)
class ContextRequest(SerializableRecord):
    symbols: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()
    relation_kinds: tuple[str, ...] = ()
    reason: str = "generator_context_request"


@dataclass(frozen=True, slots=True)
class TracebackRecord(SerializableRecord):
    locations: tuple[SourceLocation, ...]
    exception_type: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class RepairSliceSeed(SerializableRecord):
    symbol_names: tuple[str, ...]
    file_paths: tuple[str, ...]
    test_paths: tuple[str, ...]
    traceback_locations: tuple[SourceLocation, ...]
    issue_tokens: tuple[str, ...]
    diff_locations: tuple[SourceLocation, ...]
    trace_locations: tuple[SourceLocation, ...]
    requested_context: tuple[ContextRequest, ...]


@dataclass(frozen=True, slots=True)
class ProgramGraphBuildResult(SerializableRecord):
    graph: ProgramGraph
    analyzed_files: tuple[str, ...]
    analyzed_callable_names: tuple[str, ...]
    deferred_files: tuple[str, ...]
    truncated_reason: str | None
    elapsed_seconds: float
    peak_rss_mib: float


def _relative(index: RepositoryIndex, path: str) -> str | None:
    candidate = Path(path)
    root = Path(index.repository_root)
    if candidate.is_absolute():
        try:
            return str(candidate.resolve().relative_to(root.resolve())).replace("\\", "/")
        except ValueError:
            return None
    return str(candidate).replace("\\", "/")


def recover_repair_slice_seeds(
    issue: str,
    visible_tests: tuple[str, ...],
    repository_index: RepositoryIndex,
    *,
    traceback: TracebackRecord | None = None,
    actual_diff: ActualDiff | None = None,
    trace_delta: dict[str, Any] | None = None,
    context_requests: tuple[ContextRequest, ...] = (),
) -> RepairSliceSeed:
    symbol_names: set[str] = set()
    files: set[str] = set()
    tests: set[str] = set()
    issue_tokens = {token.lower() for token in _IDENTIFIER.findall(issue)}
    explicit = set(_QUOTED.findall(issue))
    for name in explicit:
        if name in repository_index.symbols or name.rsplit(".", 1)[-1] in repository_index.symbols:
            symbol_names.add(name)
    for path in visible_tests:
        relative = _relative(repository_index, path)
        if relative and relative in repository_index.source_hashes:
            tests.add(relative)
            files.add(relative)
            for reference in repository_index.test_references.get(relative, ()):
                if reference in repository_index.symbols:
                    symbol_names.add(reference)
    traceback_locations = traceback.locations if traceback else ()
    for location in traceback_locations:
        if location.relative_path in repository_index.source_hashes:
            files.add(location.relative_path)
        if location.symbol:
            symbol_names.add(location.symbol)
    if traceback:
        issue_tokens.update(token.lower() for token in _IDENTIFIER.findall(
            " ".join(filter(None, (traceback.exception_type, traceback.message)))
        ))
    public_hits = [
        name for name, locations in repository_index.symbols.items()
        if name.lower() in issue_tokens and any(location.public for location in locations)
    ]
    symbol_names.update(public_hits)
    lexical_files: set[str] = set()
    for token in issue_tokens:
        lexical_files.update(repository_index.token_index.get(token, ()))
    diff_locations: list[SourceLocation] = []
    if actual_diff is not None:
        for relation in actual_diff.changed_relations:
            span = relation.new_span or relation.old_span or (1, 1)
            location = SourceLocation(
                relative_path=relation.file, line=int(span[0]), end_line=int(span[1]),
                symbol=relation.qualified_scope if relation.qualified_scope != "<module>" else None,
            )
            diff_locations.append(location)
            files.add(relation.file)
            if location.symbol:
                symbol_names.add(location.symbol)
    trace_locations = tuple(
        SourceLocation(
            relative_path=str(item["file"]), line=int(item.get("line", 1)),
            end_line=int(item.get("end_line", item.get("line", 1))),
            symbol=item.get("symbol"),
        )
        for item in (trace_delta or {}).get("locations", ())
        if str(item.get("file", "")) in repository_index.source_hashes
    )
    files.update(item.relative_path for item in trace_locations)
    for request in context_requests:
        symbol_names.update(request.symbols)
        files.update(
            relative for path in request.file_paths
            if (relative := _relative(repository_index, path)) is not None
        )
    for name in tuple(symbol_names):
        for location in repository_index.symbols.get(name, ()):
            files.add(location.relative_path)
        tail = name.rsplit(".", 1)[-1]
        for location in repository_index.symbols.get(tail, ()):
            files.add(location.relative_path)
    if not files:
        files.update(sorted(lexical_files)[:8])
    return RepairSliceSeed(
        symbol_names=tuple(sorted(symbol_names)), file_paths=tuple(sorted(files)),
        test_paths=tuple(sorted(tests)), traceback_locations=traceback_locations,
        issue_tokens=tuple(sorted(issue_tokens)), diff_locations=tuple(diff_locations),
        trace_locations=trace_locations, requested_context=context_requests,
    )


def _active_scope(
    index: RepositoryIndex,
    seeds: RepairSliceSeed,
    budget: GraphBudget,
) -> tuple[list[str], list[str], list[str]]:
    selected: list[str] = []
    selected_set: set[str] = set()

    def add(path: str) -> None:
        if path in index.source_hashes and path not in selected_set and len(selected) < budget.max_files:
            selected.append(path)
            selected_set.add(path)

    for path in (*seeds.file_paths, *seeds.test_paths):
        add(path)
    for symbol in seeds.symbol_names:
        for location in (*index.symbols.get(symbol, ()), *index.symbols.get(symbol.rsplit(".", 1)[-1], ())):
            add(location.relative_path)
    wanted_test_symbols = {
        name.rsplit(".", 1)[-1] for name in seeds.symbol_names
    }
    for test_path, references in sorted(index.test_references.items()):
        if wanted_test_symbols & set(references):
            add(test_path)
    modules_by_path = {summary.relative_path: name for name, summary in index.modules.items()}
    seed_modules = {modules_by_path[path] for path in selected if path in modules_by_path}
    for module in tuple(seed_modules):
        for imported in index.imports.get(module, ()):
            summary = index.modules.get(imported.lstrip("."))
            if summary:
                add(summary.relative_path)
        for caller, imported_names in index.imports.items():
            if module in {name.lstrip(".") for name in imported_names} and caller in index.modules:
                add(index.modules[caller].relative_path)
    active_names: list[str] = []
    wanted = set(seeds.symbol_names)
    for path in selected:
        module = index.modules.get(modules_by_path.get(path, ""))
        if not module:
            continue
        for name in module.callables:
            if not wanted or name in wanted or name.rsplit(".", 1)[-1] in wanted:
                active_names.append(name)
                if len(active_names) >= budget.max_functions:
                    break
        if len(active_names) >= budget.max_functions:
            break
    active_set = set(active_names)
    seed_tails = {name.rsplit(".", 1)[-1] for name in seeds.symbol_names}
    # Expand exactly one function-call radius. ASTs are local temporaries and
    # are released before the precise graph builder starts.
    for path in tuple(selected):
        if len(active_names) >= budget.max_functions or not budget.check():
            break
        module_name = modules_by_path.get(path)
        if module_name is None:
            continue
        try:
            tree = ast.parse(
                (Path(index.repository_root) / path).read_text(
                    encoding="utf-8", errors="replace"
                ),
                filename=path,
                type_comments=True,
            )
        except (OSError, SyntaxError):
            continue
        scope = [module_name]

        class CallRadiusVisitor(ast.NodeVisitor):
            def _callable(
                self, node: ast.FunctionDef | ast.AsyncFunctionDef
            ) -> None:
                qualified = ".".join((*scope, node.name))
                called = {
                    (
                        call.func.id if isinstance(call.func, ast.Name)
                        else call.func.attr
                    )
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, (ast.Name, ast.Attribute))
                }
                activates_caller = bool(called & seed_tails)
                activates_callees = qualified in active_set or node.name in seed_tails
                if activates_caller and qualified not in active_set:
                    active_names.append(qualified)
                    active_set.add(qualified)
                if activates_callees:
                    for called_name in sorted(called):
                        for location in index.symbols.get(called_name, ()):
                            if location.kind not in {"function", "method"}:
                                continue
                            if location.qualified_name not in active_set:
                                active_names.append(location.qualified_name)
                                active_set.add(location.qualified_name)
                            add(location.relative_path)
                            if len(active_names) >= budget.max_functions:
                                return

            visit_FunctionDef = _callable
            visit_AsyncFunctionDef = _callable

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                scope.append(node.name)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        self.visit(child)
                scope.pop()

        CallRadiusVisitor().visit(tree)
        del tree
    if not active_names:
        for path in selected:
            module = index.modules.get(modules_by_path.get(path, ""))
            if module:
                active_names.extend(module.callables[: max(0, budget.max_functions - len(active_names))])
            if len(active_names) >= budget.max_functions:
                break
    deferred = sorted(set(index.source_hashes) - selected_set)
    return selected, active_names[:budget.max_functions], deferred


def build_active_program_slice(
    repository_root: Path,
    repository_index: RepositoryIndex,
    seeds: RepairSliceSeed,
    *,
    previous: ProgramGraph | None,
    changed_files: tuple[str, ...] = (),
    budget: GraphBudget,
) -> ProgramGraphBuildResult:
    started = time.monotonic()
    selected, active_names, deferred = _active_scope(repository_index, seeds, budget)
    for path in changed_files:
        if path in repository_index.source_hashes and path not in selected and len(selected) < budget.max_files:
            selected.append(path)
    graph = PythonProgramGraphBuilder(
        repository_root,
        max_files=budget.max_files,
        include_files=selected,
        active_callable_ids=active_names,
        budget=budget,
        max_protocol_candidates_per_operation=(
            budget.max_protocol_candidates_per_operation
        ),
    ).build()
    graph.build_stats.update({
        "index_file_count": repository_index.scanned_files,
        "precise_file_count": len(selected),
        "precise_function_count": len(graph.cfgs),
        "deferred_file_count": len(deferred),
    })
    return ProgramGraphBuildResult(
        graph=graph, analyzed_files=tuple(selected),
        analyzed_callable_names=tuple(active_names), deferred_files=tuple(deferred),
        truncated_reason=budget.truncated_reason,
        elapsed_seconds=time.monotonic() - started, peak_rss_mib=current_rss_mib(),
    )
