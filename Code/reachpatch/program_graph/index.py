from __future__ import annotations

import ast
import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.core import Frontier
from reachpatch.program_graph.budget import Deadline

_EXCLUDES = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "build", "dist", "__pycache__"}
_TOKENS = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_MAX_TOKENS_PER_FILE = 5_000


@dataclass(frozen=True, slots=True)
class SymbolLocation(SerializableRecord):
    qualified_name: str
    relative_path: str
    line: int
    end_line: int
    kind: str
    public: bool


@dataclass(frozen=True, slots=True)
class ModuleSummary(SerializableRecord):
    module_name: str
    relative_path: str
    classes: tuple[str, ...]
    callables: tuple[str, ...]
    imports: tuple[str, ...]
    bases: tuple[str, ...]
    decorators: tuple[str, ...]
    public_symbols: tuple[str, ...]
    is_test: bool


@dataclass(slots=True)
class RepositoryIndex(SerializableRecord):
    repository_root: str
    source_hashes: dict[str, str]
    modules: dict[str, ModuleSummary]
    symbols: dict[str, tuple[SymbolLocation, ...]]
    imports: dict[str, tuple[str, ...]]
    inheritance: dict[str, tuple[str, ...]]
    test_references: dict[str, tuple[str, ...]]
    token_index: dict[str, tuple[str, ...]]
    parse_frontiers: tuple[Frontier, ...]
    build_seconds: float = 0.0
    scanned_files: int = 0


def _module_name(path: str) -> str:
    parts = list(Path(path[:-3]).parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__root__"


def _names(node: ast.AST) -> tuple[str, ...]:
    return tuple(sorted({
        item.id if isinstance(item, ast.Name) else item.attr
        for item in ast.walk(node)
        if isinstance(item, (ast.Name, ast.Attribute))
    }))


def build_repository_index(
    repository_root: Path,
    *,
    max_files: int,
    deadline: Deadline,
    include_files: tuple[str, ...] | None = None,
) -> RepositoryIndex:
    root = repository_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    started = time.monotonic()
    hashes: dict[str, str] = {}
    modules: dict[str, ModuleSummary] = {}
    symbol_lists: dict[str, list[SymbolLocation]] = {}
    imports: dict[str, tuple[str, ...]] = {}
    inheritance: dict[str, tuple[str, ...]] = {}
    tests: dict[str, tuple[str, ...]] = {}
    tokens: dict[str, set[str]] = {}
    frontiers: list[Frontier] = []
    paths: list[Path] = []
    if include_files is not None:
        for relative in sorted(set(include_files)):
            path = (root / relative).resolve()
            if path.is_relative_to(root) and path.is_file() and path.suffix == ".py":
                paths.append(path)
            if len(paths) >= max_files or deadline.expired:
                break
    else:
        for current, directories, names in os.walk(root):
            directories[:] = sorted(name for name in directories if name not in _EXCLUDES)
            for name in sorted(names):
                if name.endswith(".py"):
                    paths.append(Path(current) / name)
                    if len(paths) >= max_files or deadline.expired:
                        break
            if len(paths) >= max_files or deadline.expired:
                break
    truncated = deadline.expired or (
        include_files is None and len(paths) >= max_files
    )
    for path in paths:
        if deadline.expired:
            truncated = True
            break
        relative = str(path.relative_to(root)).replace(os.sep, "/")
        raw = path.read_bytes()
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        source = raw.decode("utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path), type_comments=True)
        except SyntaxError as exc:
            frontiers.append(Frontier(
                frontier_id=stable_id("index-frontier", relative, str(exc)),
                kind="PYTHON_PARSE_ERROR", owner_id=relative,
                reason=str(exc), resolution_action="repair syntax or exclude generated source",
                hard=False, evidence_ids=(), status="OPEN",
            ))
            continue
        module = _module_name(relative)
        is_test = "tests" in Path(relative).parts or Path(relative).name.startswith("test_")
        classes: list[str] = []
        callables: list[str] = []
        module_imports: set[str] = set()
        module_bases: set[str] = set()
        decorators: set[str] = set()
        public: set[str] = set()
        reference_names: set[str] = set()
        scope: list[str] = [module]

        class SummaryVisitor(ast.NodeVisitor):
            def visit_Module(self, node: ast.Module) -> None:
                for child in node.body:
                    if is_test or isinstance(child, (
                        ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
                        ast.Import, ast.ImportFrom,
                    )):
                        self.visit(child)

            def _record(self, node: ast.AST, name: str, kind: str) -> None:
                qualified = ".".join((*scope, name))
                location = SymbolLocation(
                    qualified_name=qualified, relative_path=relative,
                    line=int(getattr(node, "lineno", 1)),
                    end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                    kind=kind, public=not name.startswith("_"),
                )
                for key in {name, qualified}:
                    symbol_lists.setdefault(key, []).append(location)
                if not name.startswith("_"):
                    public.add(name)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self._record(node, node.name, "class")
                classes.append(".".join((*scope, node.name)))
                bases = tuple(ast.unparse(base) for base in node.bases)
                module_bases.update(bases)
                inheritance[".".join((*scope, node.name))] = bases
                decorators.update(ast.unparse(item) for item in node.decorator_list)
                scope.append(node.name)
                for child in node.body:
                    if is_test or isinstance(child, (
                        ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef,
                        ast.Import, ast.ImportFrom,
                    )):
                        self.visit(child)
                scope.pop()

            def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                self._record(node, node.name, "method" if len(scope) > 1 else "function")
                callables.append(".".join((*scope, node.name)))
                decorators.update(ast.unparse(item) for item in node.decorator_list)
                scope.append(node.name)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        self.visit(child)
                    elif is_test:
                        reference_names.update(_names(child))
                scope.pop()

            visit_FunctionDef = _function
            visit_AsyncFunctionDef = _function

            def visit_Import(self, node: ast.Import) -> None:
                module_imports.update(item.name for item in node.names)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                module_imports.add(("." * node.level) + (node.module or ""))

            def visit_Name(self, node: ast.Name) -> None:
                if is_test:
                    reference_names.add(node.id)

            def visit_Attribute(self, node: ast.Attribute) -> None:
                if is_test:
                    reference_names.add(node.attr)
                    self.generic_visit(node)

        SummaryVisitor().visit(tree)
        modules[module] = ModuleSummary(
            module_name=module, relative_path=relative,
            classes=tuple(sorted(classes)), callables=tuple(sorted(callables)),
            imports=tuple(sorted(module_imports)), bases=tuple(sorted(module_bases)),
            decorators=tuple(sorted(decorators)), public_symbols=tuple(sorted(public)),
            is_test=is_test,
        )
        imports[module] = tuple(sorted(module_imports))
        if is_test:
            tests[relative] = tuple(sorted(reference_names))
        file_tokens = set(_TOKENS.findall(source))
        if len(file_tokens) > _MAX_TOKENS_PER_FILE:
            frontiers.append(Frontier(
                frontier_id=stable_id(
                    "index-frontier", relative, "TOKEN_INDEX_TRUNCATED"
                ),
                kind="ANALYSIS_TRUNCATED", owner_id=relative,
                reason=(
                    f"token summary exceeded {_MAX_TOKENS_PER_FILE} unique identifiers"
                ),
                resolution_action="request a targeted lexical scan for this file",
                hard=False, evidence_ids=(), status="OPEN",
            ))
        for token in sorted(file_tokens)[:_MAX_TOKENS_PER_FILE]:
            tokens.setdefault(token.lower(), set()).add(relative)
        del tree
    if truncated:
        frontiers.append(Frontier(
            frontier_id=stable_id("index-frontier", "ANALYSIS_TRUNCATED", len(hashes)),
            kind="ANALYSIS_TRUNCATED", owner_id=str(root),
            reason="repository index file or deadline budget reached",
            resolution_action="use existing partial index and request targeted files",
            hard=False, evidence_ids=(), status="OPEN",
        ))
    return RepositoryIndex(
        repository_root=str(root), source_hashes=hashes, modules=modules,
        symbols={key: tuple(sorted(value, key=lambda item: (item.relative_path, item.line, item.qualified_name))) for key, value in symbol_lists.items()},
        imports=imports, inheritance=inheritance, test_references=tests,
        token_index={key: tuple(sorted(value)) for key, value in tokens.items()},
        parse_frontiers=tuple(frontiers), build_seconds=time.monotonic() - started,
        scanned_files=len(hashes),
    )


def update_repository_index(
    previous: RepositoryIndex,
    repository_root: Path,
    changed_files: tuple[str, ...],
    *,
    deadline: Deadline,
) -> RepositoryIndex:
    """Replace summaries for changed files without rescanning the repository."""

    changed = {
        path.replace("\\", "/") for path in changed_files if path.endswith(".py")
    }
    if not changed:
        return previous
    partial = build_repository_index(
        repository_root, max_files=max(1, len(changed)), deadline=deadline,
        include_files=tuple(sorted(changed)),
    )
    source_hashes = {
        path: digest for path, digest in previous.source_hashes.items()
        if path not in changed
    }
    source_hashes.update(partial.source_hashes)
    modules = {
        name: summary for name, summary in previous.modules.items()
        if summary.relative_path not in changed
    }
    modules.update(partial.modules)
    symbols: dict[str, tuple[SymbolLocation, ...]] = {}
    for name in set(previous.symbols) | set(partial.symbols):
        retained = [
            location for location in previous.symbols.get(name, ())
            if location.relative_path not in changed
        ]
        merged = retained + list(partial.symbols.get(name, ()))
        if merged:
            symbols[name] = tuple(sorted(
                set(merged),
                key=lambda item: (item.relative_path, item.line, item.qualified_name),
            ))
    changed_modules = {
        name for name, summary in previous.modules.items()
        if summary.relative_path in changed
    }
    imports = {
        name: values for name, values in previous.imports.items()
        if name not in changed_modules
    }
    imports.update(partial.imports)
    inheritance = {
        name: values for name, values in previous.inheritance.items()
        if not any(name == module or name.startswith(module + ".") for module in changed_modules)
    }
    inheritance.update(partial.inheritance)
    tests = {
        path: values for path, values in previous.test_references.items()
        if path not in changed
    }
    tests.update(partial.test_references)
    token_index: dict[str, tuple[str, ...]] = {}
    for token in set(previous.token_index) | set(partial.token_index):
        paths = (
            set(previous.token_index.get(token, ())) - changed
        ) | set(partial.token_index.get(token, ()))
        if paths:
            token_index[token] = tuple(sorted(paths))
    return RepositoryIndex(
        repository_root=str(repository_root.resolve()),
        source_hashes=source_hashes, modules=modules, symbols=symbols,
        imports=imports, inheritance=inheritance, test_references=tests,
        token_index=token_index,
        parse_frontiers=tuple(previous.parse_frontiers) + tuple(partial.parse_frontiers),
        build_seconds=partial.build_seconds,
        scanned_files=len(source_hashes),
    )
