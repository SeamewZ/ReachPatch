from __future__ import annotations

import ast
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.evidence import ActualDiff, ExecutableCheck
from reachpatch.models.graphs import (
    GraphBudget, PathClass, ProgramEdge, ProgramEdgeKind, ProgramGraph,
    ProgramNode, ProgramNodeKind,
)


_EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "build",
    "dist", "__pycache__", ".reachpatch",
}
_AST_CACHE: dict[tuple[str, str, str], ast.AST] = {}
_SOURCE_SYMBOL_CACHE: dict[tuple[str, str, str], frozenset[str]] = {}
_CFG_CACHE: dict[
    tuple[object, ...],
    tuple[dict[str, ProgramNode], dict[str, ProgramEdge], dict[str, PathClass]],
] = {}
_EXTERNAL_EFFECT_NAMES = {
    "append", "close", "dump", "emit", "flush", "open", "post", "print",
    "put", "remove", "rmtree", "run", "save", "send", "unlink", "write",
}
_BINARY_PROTOCOL_ROUTES = {
    "__add__": "FORWARD", "__radd__": "REFLECTED",
    "__sub__": "FORWARD", "__rsub__": "REFLECTED",
    "__mul__": "FORWARD", "__rmul__": "REFLECTED",
    "__matmul__": "FORWARD", "__rmatmul__": "REFLECTED",
    "__truediv__": "FORWARD", "__rtruediv__": "REFLECTED",
    "__floordiv__": "FORWARD", "__rfloordiv__": "REFLECTED",
    "__mod__": "FORWARD", "__rmod__": "REFLECTED",
    "__divmod__": "FORWARD", "__rdivmod__": "REFLECTED",
    "__pow__": "FORWARD", "__rpow__": "REFLECTED",
    "__lshift__": "FORWARD", "__rlshift__": "REFLECTED",
    "__rshift__": "FORWARD", "__rrshift__": "REFLECTED",
    "__and__": "FORWARD", "__rand__": "REFLECTED",
    "__xor__": "FORWARD", "__rxor__": "REFLECTED",
    "__or__": "FORWARD", "__ror__": "REFLECTED",
}


def _per_file_node_limit(budget: GraphBudget) -> int:
    """Keep one large source file from consuming the whole local graph.

    The changed files are parsed first, so reserving capacity for at least
    eight local files keeps their functions and branches available during an
    incremental rebuild while bounding the in-memory graph.
    """

    active_file_slots = max(1, min(budget.max_files, 8))
    return min(budget.max_nodes, max(32, budget.max_nodes // active_file_slots))


def _bounded_cache_put(cache: dict, key, value, limit: int = 512) -> None:
    if len(cache) >= limit:
        cache.pop(next(iter(cache)))
    cache[key] = value


def clear_program_graph_caches() -> None:
    _AST_CACHE.clear()
    _SOURCE_SYMBOL_CACHE.clear()
    _CFG_CACHE.clear()


@dataclass(slots=True)
class RepositoryIndex:
    repository: Path
    base_commit: str
    file_hashes: dict[str, str]
    symbol_files: dict[str, tuple[str, ...]]
    cache_hits: int = 0
    expanded_symbols: set[str] = field(default_factory=set)

    @classmethod
    def build(
        cls,
        repository: Path,
        base_commit: str,
        identifiers: tuple[str, ...],
        max_files: int,
    ) -> "RepositoryIndex":
        file_hashes: dict[str, str] = {}
        found: dict[str, list[str]] = {item: [] for item in identifiers}
        index_cache_hits = 0
        candidates = [
            path for path in repository.rglob("*.py")
            if not any(
                part in _EXCLUDED_PARTS
                for part in path.relative_to(repository).parts
            )
        ]
        normalized_identifiers = {
            identifier.casefold() for identifier in identifiers
            if len(identifier) >= 3
        }

        def path_priority(path: Path) -> tuple[int, int, str]:
            relative = path.relative_to(repository)
            path_tokens = {
                part.casefold() for part in relative.parts
            } | {relative.stem.casefold()}
            exact_stem_match = relative.stem.casefold() in normalized_identifiers
            path_match_count = len(path_tokens & normalized_identifiers)
            return (
                not exact_stem_match,
                -path_match_count,
                len(relative.parts),
                relative.as_posix(),
            )

        count = 0
        for path in sorted(candidates, key=path_priority):
            if any(part in _EXCLUDED_PARTS for part in path.relative_to(repository).parts):
                continue
            relative = path.relative_to(repository).as_posix()
            data = path.read_bytes()
            digest = content_hash(data.hex())
            file_hashes[relative] = digest
            if identifiers:
                cache_key = (base_commit, relative, digest)
                symbols = _SOURCE_SYMBOL_CACHE.get(cache_key)
                if symbols is None:
                    symbols = frozenset(re.findall(
                        r"\b[A-Za-z_]\w*\b",
                        data.decode("utf-8", errors="replace"),
                    ))
                    _bounded_cache_put(_SOURCE_SYMBOL_CACHE, cache_key, symbols)
                else:
                    index_cache_hits += 1
                for identifier in identifiers:
                    if identifier in symbols:
                        found[identifier].append(relative)
            count += 1
            if count >= max_files * 20:
                break
        return cls(
            repository=repository,
            base_commit=base_commit,
            file_hashes=file_hashes,
            symbol_files={key: tuple(value) for key, value in found.items()},
            cache_hits=index_cache_hits,
        )

    def expand_symbol(
        self,
        identifier: str,
        *,
        max_matches: int = 24,
    ) -> tuple[str, ...]:
        """Find one symbol outside the bounded initial indexing window.

        This is a token scan, not an AST/CFG build. Files are read one at a
        time and only matching files become local graph/tool candidates.
        """

        lookup = identifier.split(".")[-1]
        if not re.fullmatch(r"[A-Za-z_]\w*", lookup):
            return ()
        if lookup in self.expanded_symbols:
            return self.symbol_files.get(lookup, ())
        self.expanded_symbols.add(lookup)
        matches = list(self.symbol_files.get(lookup, ()))
        seen = set(self.file_hashes)
        candidates = [
            path for path in self.repository.rglob("*.py")
            if not any(
                part in _EXCLUDED_PARTS
                for part in path.relative_to(self.repository).parts
            )
        ]

        def priority(path: Path) -> tuple[bool, int, str]:
            relative = path.relative_to(self.repository)
            protected = any(
                part in {"test", "tests", "generated", "artifacts"}
                for part in relative.parts
            )
            return protected, len(relative.parts), relative.as_posix()

        for path in sorted(candidates, key=priority):
            relative = path.relative_to(self.repository).as_posix()
            if relative in seen:
                continue
            data = path.read_bytes()
            digest = content_hash(data.hex())
            self.file_hashes[relative] = digest
            cache_key = (self.base_commit, relative, digest)
            symbols = _SOURCE_SYMBOL_CACHE.get(cache_key)
            if symbols is None:
                symbols = frozenset(re.findall(
                    r"\b[A-Za-z_]\w*\b",
                    data.decode("utf-8", errors="replace"),
                ))
                _bounded_cache_put(_SOURCE_SYMBOL_CACHE, cache_key, symbols)
            else:
                self.cache_hits += 1
            if lookup not in symbols:
                continue
            matches.append(relative)
            if len(matches) >= max_matches:
                break
        self.symbol_files[lookup] = tuple(dict.fromkeys(matches))
        return self.symbol_files[lookup]


@dataclass(frozen=True, slots=True)
class ParseStats:
    cache_hit: bool
    file_reparsed: bool


def _issue_identifiers(issue: str) -> tuple[str, ...]:
    quoted = re.findall(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`", issue)
    paths = re.findall(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b", issue)
    return tuple(dict.fromkeys(
        part.split(".")[-1] for part in (*quoted, *paths)
        if part.split(".")[-1] not in {"None", "True", "False"}
    ))[:40]


def _parse(index: RepositoryIndex, relative: str) -> tuple[ast.AST | None, bool, str]:
    path = index.repository / relative
    if not path.is_file() or path.suffix != ".py":
        return None, False, ""
    digest = content_hash(path.read_bytes().hex())
    key = (index.base_commit, relative, digest)
    if key in _AST_CACHE:
        return _AST_CACHE[key], True, digest
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=relative)
    except SyntaxError:
        return None, False, digest
    _bounded_cache_put(_AST_CACHE, key, tree)
    return tree, False, digest


def _kind(node: ast.AST, parent: ast.AST | None) -> ProgramNodeKind | None:
    if isinstance(node, ast.ClassDef):
        return ProgramNodeKind.CLASS
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ProgramNodeKind.METHOD if isinstance(parent, ast.ClassDef) else ProgramNodeKind.FUNCTION
    if isinstance(node, (
        ast.If, ast.IfExp, ast.Match, ast.While, ast.For, ast.AsyncFor,
    )):
        return ProgramNodeKind.BRANCH
    if isinstance(node, ast.Return):
        return ProgramNodeKind.RETURN
    if isinstance(node, ast.Raise):
        return ProgramNodeKind.RAISE
    if isinstance(node, ast.Call):
        return ProgramNodeKind.CALL_SITE
    if isinstance(node, ast.arg):
        return ProgramNodeKind.PARAMETER
    if isinstance(node, ast.Attribute):
        if isinstance(node.ctx, ast.Store):
            return ProgramNodeKind.STATE_WRITE
        if isinstance(node.ctx, ast.Load):
            return ProgramNodeKind.STATE_READ
        return ProgramNodeKind.ATTRIBUTE
    if isinstance(node, ast.Name):
        return ProgramNodeKind.LOCAL_VALUE
    return None


def _node_symbol(node: ast.AST) -> str:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef, ast.arg)):
        return node.name if hasattr(node, "name") else node.arg
    if isinstance(node, ast.Call):
        return ast.unparse(node.func)
    if isinstance(node, ast.Attribute):
        return ast.unparse(node)
    if isinstance(node, (ast.If, ast.IfExp, ast.While)):
        return ast.unparse(node.test)
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return ast.unparse(node.target)
    return type(node).__name__


def _walk_with_parents(tree: ast.AST):
    stack: list[tuple[ast.AST, ast.AST | None, str]] = [(tree, None, "")]
    while stack:
        node, parent, scope = stack.pop()
        yield node, parent, scope
        next_scope = scope
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            next_scope = f"{scope}.{node.name}".strip(".")
        for child in reversed(list(ast.iter_child_nodes(node))):
            stack.append((child, node, next_scope))


def _diff_focus(
    actual_diff: ActualDiff,
) -> dict[str, tuple[tuple[int, ...], tuple[str, ...]]]:
    lines: dict[str, set[int]] = {}
    symbols: dict[str, set[str]] = {}
    for hunk in actual_diff.hunks:
        hunk_lines = hunk.changed_new_lines or (hunk.new_start,)
        lines.setdefault(hunk.path, set()).update(
            line for line in hunk_lines if line > 0
        )
        symbols.setdefault(hunk.path, set()).update(hunk.changed_symbols)
    return {
        path: (
            tuple(sorted(lines.get(path, ()))),
            tuple(sorted(symbols.get(path, ()))),
        )
        for path in set(lines) | set(symbols)
    }


def _focused_walk(
    tree: ast.AST,
    focus_lines: tuple[int, ...],
    focus_symbols: tuple[str, ...],
) -> tuple[tuple[ast.AST, ast.AST | None, str], ...]:
    """Prioritize the actual edit locus while retaining bounded context."""

    entries = tuple(_walk_with_parents(tree))
    if not focus_lines and not focus_symbols:
        return entries

    definitions = tuple(
        entry for entry in entries
        if isinstance(entry[0], (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    terminal_symbols = {symbol.rsplit(".", 1)[-1] for symbol in focus_symbols}

    def contains_line(node: ast.AST, line: int) -> bool:
        return (
            getattr(node, "lineno", 0)
            <= line
            <= getattr(node, "end_lineno", getattr(node, "lineno", 0))
        )

    roots: list[ast.AST] = []
    for line in focus_lines:
        enclosing_callables = [
            node for node, _, _ in definitions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and contains_line(node, line)
        ]
        if enclosing_callables:
            roots.append(min(
                enclosing_callables,
                key=lambda node: (
                    getattr(node, "end_lineno", 0) - getattr(node, "lineno", 0),
                    getattr(node, "lineno", 0),
                ),
            ))
    roots.extend(
        node for node, _, _ in definitions
        if getattr(node, "name", "") in terminal_symbols
    )
    roots = list(dict.fromkeys(roots))

    anchor_lines = set(focus_lines)
    anchor_lines.update(getattr(root, "lineno", 0) for root in roots)
    essential_ids = {
        id(node) for node, _, _ in definitions
        if any(contains_line(node, line) for line in anchor_lines if line > 0)
    }
    focused_ids = {
        id(item) for root in roots for item in ast.walk(root)
    }
    locus_ids = {
        id(node) for node, _, _ in entries
        if any(contains_line(node, line) for line in focus_lines)
    }

    def priority(entry: tuple[ast.AST, ast.AST | None, str]) -> int:
        node = entry[0]
        if id(node) in essential_ids:
            return 0
        if id(node) in locus_ids:
            return 1
        if id(node) in focused_ids:
            return 2
        return 3

    prioritized = sorted(
        enumerate(entries),
        key=lambda item: (priority(item[1]), item[0]),
    )
    return tuple(entry for _, entry in prioritized)


def _add_edge(
    edges: dict[str, ProgramEdge], source: str, target: str,
    kind: ProgramEdgeKind, *, dynamic: bool = False,
) -> None:
    edge_id = stable_id("program-edge", source, target, kind, dynamic)
    edges[edge_id] = ProgramEdge(edge_id, source, target, kind, dynamic)


def _parse_file(
    index: RepositoryIndex,
    relative: str,
    nodes: dict[str, ProgramNode],
    edges: dict[str, ProgramEdge],
    paths: dict[str, PathClass],
    budget: GraphBudget,
    *,
    focus_lines: tuple[int, ...] = (),
    focus_symbols: tuple[str, ...] = (),
) -> ParseStats:
    tree, cache_hit, digest = _parse(index, relative)
    if tree is None:
        return ParseStats(cache_hit=cache_hit, file_reparsed=False)
    file_node_limit = _per_file_node_limit(budget)
    cfg_key = (
        "local-v5", index.base_commit, relative, digest,
        file_node_limit, budget.max_edges,
        tuple(sorted(set(focus_lines))), tuple(sorted(set(focus_symbols))),
    )
    cached = _CFG_CACHE.get(cfg_key)
    if cached is not None and len(nodes) + len(cached[0]) <= budget.max_nodes:
        nodes.update(cached[0])
        edges.update(cached[1])
        paths.update(cached[2])
        return ParseStats(cache_hit=True, file_reparsed=False)
    before_nodes = set(nodes)
    before_edges = set(edges)
    before_paths = set(paths)
    if len(nodes) >= budget.max_nodes:
        return ParseStats(cache_hit=cache_hit, file_reparsed=False)
    file_node_count = 0
    module_id = stable_id("program-node", relative, "module")
    nodes[module_id] = ProgramNode(
        module_id, ProgramNodeKind.MODULE, relative, relative, 1,
        max((getattr(item, "end_lineno", 1) for item in ast.walk(tree)), default=1),
        True,
    )
    ast_ids: dict[int, str] = {id(tree): module_id}
    file_node_count += 1
    scopes: dict[str, str] = {"": module_id}
    functions: list[tuple[ast.AST, str, str]] = []
    for node, parent, scope in _focused_walk(
        tree, tuple(sorted(set(focus_lines))), tuple(sorted(set(focus_symbols))),
    ):
        if (
            node is tree
            or len(nodes) >= budget.max_nodes
            or file_node_count >= file_node_limit
        ):
            continue
        kind = _kind(node, parent)
        if kind is None:
            continue
        symbol = _node_symbol(node)
        node_id = stable_id(
            "program-node", relative, scope, kind, symbol,
            getattr(node, "lineno", 0), getattr(node, "end_lineno", 0),
        )
        metadata = {}
        if kind is ProgramNodeKind.BRANCH:
            metadata["predicate"] = symbol
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                metadata["call_form"] = "NAME"
            elif (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"self", "cls"}
            ):
                metadata["call_form"] = "BOUND_METHOD"
                metadata["receiver_scope"] = scope.rsplit(".", 1)[0]
            else:
                metadata["call_form"] = "DYNAMIC_ATTRIBUTE"
        if isinstance(node, ast.Attribute):
            metadata["attribute_name"] = node.attr
            metadata["receiver"] = ast.unparse(node.value)
            metadata["owner_scope"] = (
                scope.rsplit(".", 1)[0] if "." in scope else scope
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            route = _BINARY_PROTOCOL_ROUTES.get(node.name)
            if route is not None:
                metadata["protocol"] = route
            if isinstance(parent, ast.ClassDef):
                decorators = {
                    item.id for item in node.decorator_list
                    if isinstance(item, ast.Name)
                }
                metadata["method_binding"] = (
                    "STATIC" if "staticmethod" in decorators else
                    "CLASS" if "classmethod" in decorators else "INSTANCE"
                )
            else:
                metadata["method_binding"] = "FUNCTION"
            positional = tuple(node.args.posonlyargs) + tuple(node.args.args)
            defaulted = len(node.args.defaults)
            required_positional = positional[:len(positional) - defaulted]
            required_keyword_only = tuple(
                argument for argument, default in zip(
                    node.args.kwonlyargs, node.args.kw_defaults,
                )
                if default is None
            )
            metadata["parameters"] = tuple(
                argument.arg for argument in positional + tuple(node.args.kwonlyargs)
            )
            metadata["positional_parameters"] = tuple(
                argument.arg for argument in positional
            )
            metadata["required_parameters"] = tuple(
                argument.arg
                for argument in required_positional + required_keyword_only
                if argument.arg not in {"self", "cls"}
            )
            metadata["accepts_varargs"] = node.args.vararg is not None
            metadata["accepts_varkw"] = node.args.kwarg is not None
        nodes[node_id] = ProgramNode(
            node_id, kind, relative, f"{scope}.{symbol}".strip("."),
            getattr(node, "lineno", 1), getattr(node, "end_lineno", getattr(node, "lineno", 1)),
            not relative.startswith(("tests/", "test/")) and "/generated/" not in relative,
            metadata,
        )
        file_node_count += 1
        ast_ids[id(node)] = node_id
        container = scopes.get(scope, module_id)
        _add_edge(edges, container, node_id, ProgramEdgeKind.CONTAINS)
        if (
            kind is ProgramNodeKind.BRANCH
            and file_node_count + 2 <= file_node_limit
            and len(nodes) + 2 <= budget.max_nodes
        ):
            true_id = stable_id("program-node", relative, node_id, "true-block")
            false_id = stable_id("program-node", relative, node_id, "false-block")
            nodes[true_id] = ProgramNode(
                true_id, ProgramNodeKind.BASIC_BLOCK, relative,
                f"{scope}.true".strip("."),
                getattr(node, "lineno", 1), getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                False, {"guard_id": node_id, "outcome": True},
            )
            nodes[false_id] = ProgramNode(
                false_id, ProgramNodeKind.BASIC_BLOCK, relative,
                f"{scope}.false".strip("."),
                getattr(node, "lineno", 1), getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                False, {"guard_id": node_id, "outcome": False},
            )
            file_node_count += 2
            _add_edge(edges, node_id, true_id, ProgramEdgeKind.CONTROL_TRUE)
            _add_edge(edges, node_id, false_id, ProgramEdgeKind.CONTROL_FALSE)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            new_scope = f"{scope}.{node.name}".strip(".")
            scopes[new_scope] = node_id
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node, node_id, f"{scope}.{node.name}".strip(".")))
        if isinstance(node, ast.Call):
            metadata_target = stable_id("unresolved-call", symbol)
            _add_edge(edges, node_id, metadata_target, ProgramEdgeKind.MAY_CALL)
            if (
                symbol.split(".")[-1] in _EXTERNAL_EFFECT_NAMES
                and file_node_count < file_node_limit
                and len(nodes) < budget.max_nodes
            ):
                effect_id = stable_id(
                    "program-node", relative, scope, "external-effect", symbol,
                    getattr(node, "lineno", 0), getattr(node, "end_lineno", 0),
                )
                nodes[effect_id] = ProgramNode(
                    effect_id, ProgramNodeKind.EXTERNAL_EFFECT, relative,
                    f"{scope}.{symbol}".strip("."),
                    getattr(node, "lineno", 1),
                    getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                    False, {"callee": symbol},
                )
                file_node_count += 1
                _add_edge(edges, node_id, effect_id, ProgramEdgeKind.CALLS)
        if kind is ProgramNodeKind.LOCAL_VALUE:
            _add_edge(edges, container, node_id, ProgramEdgeKind.DATA_FLOW)
        if kind is ProgramNodeKind.RETURN:
            _add_edge(edges, container, node_id, ProgramEdgeKind.RETURN_FLOW)
        if kind is ProgramNodeKind.RAISE:
            _add_edge(edges, container, node_id, ProgramEdgeKind.EXCEPTION_FLOW)
        if isinstance(node, ast.Attribute):
            state_kind = (
                ProgramEdgeKind.STATE_WRITE if isinstance(node.ctx, ast.Store)
                else ProgramEdgeKind.STATE_READ
            )
            _add_edge(edges, container, node_id, state_kind)
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in _BINARY_PROTOCOL_ROUTES
            and file_node_count < file_node_limit
            and len(nodes) < budget.max_nodes
        ):
            protocol_id = stable_id("program-node", relative, node_id, "protocol")
            nodes[protocol_id] = ProgramNode(
                protocol_id, ProgramNodeKind.PROTOCOL_DISPATCH, relative,
                f"protocol:{node.name}", getattr(node, "lineno", 1),
                getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                False, {"method_id": node_id},
            )
            file_node_count += 1
            _add_edge(
                edges, protocol_id, node_id,
                ProgramEdgeKind.REFLECTED_DISPATCH
                if _BINARY_PROTOCOL_ROUTES[node.name] == "REFLECTED"
                else ProgramEdgeKind.DISPATCH,
            )

    for assignment in ast.walk(tree):
        if not isinstance(assignment, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            continue
        values = [assignment.value] if getattr(assignment, "value", None) is not None else []
        targets = (
            list(assignment.targets) if isinstance(assignment, ast.Assign)
            else [assignment.target]
        )
        source_ids = [
            ast_ids[id(item)] for value in values for item in ast.walk(value)
            if id(item) in ast_ids
            and nodes[ast_ids[id(item)]].kind in {
                ProgramNodeKind.LOCAL_VALUE, ProgramNodeKind.ATTRIBUTE,
            }
        ]
        target_ids = [
            ast_ids[id(item)] for target in targets for item in ast.walk(target)
            if id(item) in ast_ids
            and nodes[ast_ids[id(item)]].kind in {
                ProgramNodeKind.LOCAL_VALUE, ProgramNodeKind.ATTRIBUTE,
            }
        ]
        for source_id in source_ids:
            for target_id in target_ids:
                _add_edge(edges, source_id, target_id, ProgramEdgeKind.ALIAS)

    for function, function_id, qualified in functions:
        branch_nodes = [
            item for item in ast.walk(function)
            if isinstance(item, (
                ast.If, ast.IfExp, ast.Match, ast.While, ast.For, ast.AsyncFor,
            ))
            and item is not function
        ]
        exits = [item for item in ast.walk(function) if isinstance(item, (ast.Return, ast.Raise))]
        if not exits:
            exits = [function]
        protocol = _BINARY_PROTOCOL_ROUTES.get(
            qualified.split(".")[-1], "DIRECT",
        )
        for exit_node in exits[:12]:
            guard_variants: list[tuple[str, ...]] = [()]
            path_node_ids = [function_id]
            for branch in branch_nodes[:8]:
                branch_id = ast_ids.get(id(branch))
                if branch_id:
                    path_node_ids.append(branch_id)
                    if isinstance(branch, ast.IfExp):
                        outcomes = ("TRUE", "FALSE")
                    else:
                        body = getattr(branch, "body", ())
                        in_true_body = bool(body) and (
                            getattr(body[0], "lineno", 0)
                            <= getattr(exit_node, "lineno", 0)
                            <= getattr(
                                body[-1], "end_lineno",
                                getattr(body[-1], "lineno", 0),
                            )
                        )
                        outcomes = ("TRUE" if in_true_body else "FALSE",)
                    guard_variants = [
                        (*variant, f"{branch_id}:{outcome}")
                        for variant in guard_variants
                        for outcome in outcomes
                    ][:16]
            exit_id = ast_ids.get(id(exit_node), function_id)
            path_node_ids.append(exit_id)
            exit_kind = "RAISE" if isinstance(exit_node, ast.Raise) else "RETURN"
            has_loop = any(isinstance(item, (ast.For, ast.While, ast.AsyncFor)) for item in branch_nodes)
            recursive = any(
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Name)
                and item.func.id == qualified.split(".")[-1]
                for item in ast.walk(function)
            )
            for guards in guard_variants:
                for loop_class in (("0", "1", "MANY") if has_loop else ("0",)):
                    for recursion_class in (("NONE", "ONE", "DEPTH_LIMIT") if recursive else ("NONE",)):
                        path_id = stable_id(
                            "path-class", qualified, guards, protocol, exit_kind,
                            loop_class, recursion_class,
                        )
                        paths[path_id] = PathClass(
                            path_id, qualified, tuple(guards), protocol, exit_kind,
                            "EXCEPTION" if exit_kind == "RAISE" else "RETURN_VALUE",
                            loop_class=loop_class,
                            recursion_class=recursion_class,
                            node_ids=tuple(dict.fromkeys(path_node_ids)),
                        )
    cached_nodes = {key: value for key, value in nodes.items() if key not in before_nodes}
    cached_edges = {key: value for key, value in edges.items() if key not in before_edges}
    cached_paths = {key: value for key, value in paths.items() if key not in before_paths}
    if cached_nodes and len(cached_nodes) < budget.max_nodes:
        _bounded_cache_put(
            _CFG_CACHE, cfg_key, (cached_nodes, cached_edges, cached_paths),
        )
    return ParseStats(cache_hit=cache_hit, file_reparsed=True)


def _limit_edges(edges: dict[str, ProgramEdge], max_edges: int) -> None:
    if max_edges < 0:
        raise ValueError("max_edges must be non-negative")
    if len(edges) <= max_edges:
        return
    retained = sorted(edges)[:max_edges]
    for edge_id in tuple(edges):
        if edge_id not in retained:
            del edges[edge_id]


def _seed_files(
    repository: Path,
    issue: str,
    initial_diff: ActualDiff,
    checks: tuple[ExecutableCheck, ...],
    index: RepositoryIndex,
    budget: GraphBudget,
    relevant_symbols: tuple[str, ...] = (),
) -> tuple[str, ...]:
    result = list(initial_diff.changed_files)
    identifiers = _issue_identifiers(issue) + tuple(
        symbol for check in checks for symbol in check.symbol_references
    ) + tuple(
        symbol.rsplit(".", 1)[-1]
        for symbol in relevant_symbols
        if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", symbol)
    )
    for identifier in identifiers:
        result.extend(index.symbol_files.get(identifier, ()))
    for match in re.findall(r"(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py", issue):
        if (repository / match).is_file():
            result.append(match)
    return tuple(dict.fromkeys(
        item for item in result
        if (repository / item).is_file() and item.endswith(".py")
    ))[:budget.max_files]


def _resolve_call_edges(
    nodes: dict[str, ProgramNode], edges: dict[str, ProgramEdge],
) -> None:
    definitions: dict[str, list[str]] = {}
    contained: dict[str, list[str]] = {}
    for node in nodes.values():
        if node.kind in {ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD, ProgramNodeKind.CLASS}:
            definitions.setdefault(node.symbol.split(".")[-1], []).append(node.node_id)
    for edge in edges.values():
        if edge.kind in {
            ProgramEdgeKind.CONTAINS, ProgramEdgeKind.RETURN_FLOW,
            ProgramEdgeKind.EXCEPTION_FLOW,
        }:
            contained.setdefault(edge.source_id, []).append(edge.target_id)
    for node in tuple(nodes.values()):
        if node.kind is not ProgramNodeKind.CALL_SITE:
            continue
        targets = definitions.get(node.symbol.split(".")[-1], ())
        if node.metadata.get("call_form") == "BOUND_METHOD":
            owner = str(node.metadata.get("receiver_scope", ""))
            targets = tuple(
                target_id for target_id in targets
                if nodes[target_id].symbol.rsplit(".", 1)[0] == owner
            )
        statically_resolved = (
            node.metadata.get("call_form") in {"NAME", "BOUND_METHOD"}
            and len(targets) == 1
        )
        for target in targets:
            _add_edge(edges, node.node_id, target, ProgramEdgeKind.MAY_CALL)
            if statically_resolved:
                _add_edge(edges, node.node_id, target, ProgramEdgeKind.CALLS)
            if not statically_resolved:
                continue
            for effect_id in contained.get(target, ()):
                effect = nodes.get(effect_id)
                if effect is None:
                    continue
                if effect.kind is ProgramNodeKind.RETURN:
                    _add_edge(edges, effect_id, node.node_id, ProgramEdgeKind.RETURN_FLOW)
                    _add_edge(edges, effect_id, node.node_id, ProgramEdgeKind.CONSUMER)
                elif effect.kind is ProgramNodeKind.RAISE:
                    _add_edge(edges, effect_id, node.node_id, ProgramEdgeKind.EXCEPTION_FLOW)
    methods = {
        node.symbol.split(".")[-1]: node.node_id
        for node in nodes.values() if node.kind is ProgramNodeKind.METHOD
    }
    for name, node_id in tuple(methods.items()):
        if not name.startswith("__r"):
            continue
        forward = f"__{name[3:]}"
        if forward in methods:
            _add_edge(
                edges, methods[forward], node_id,
                ProgramEdgeKind.REFLECTED_DISPATCH,
            )


def build_initial_program_graph(
    repository: Path,
    issue: str,
    initial_diff: ActualDiff,
    public_checks: tuple[ExecutableCheck, ...],
    budget: GraphBudget,
    *,
    base_commit: str = "UNKNOWN",
    relevant_symbols: tuple[str, ...] = (),
) -> ProgramGraph:
    """Build a precise local graph around diff, issue, checks and depth-1 callers."""

    requirement_identifiers = tuple(
        symbol.rsplit(".", 1)[-1]
        for symbol in relevant_symbols
        if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", symbol)
    )
    identifiers = _issue_identifiers(issue) + requirement_identifiers + tuple(
        symbol for check in public_checks for symbol in check.symbol_references
    )
    index = RepositoryIndex.build(repository, base_commit, identifiers, budget.max_files)
    seeds = list(_seed_files(
        repository, issue, initial_diff, public_checks, index, budget,
        relevant_symbols,
    ))
    diff_focus = _diff_focus(initial_diff)
    nodes: dict[str, ProgramNode] = {}
    edges: dict[str, ProgramEdge] = {}
    paths: dict[str, PathClass] = {}
    parsed: set[str] = set()
    cache_hits = index.cache_hits
    files_reparsed = 0
    for relative in seeds:
        focus_lines, focus_symbols = diff_focus.get(relative, ((), ()))
        stats = _parse_file(
            index, relative, nodes, edges, paths, budget,
            focus_lines=focus_lines, focus_symbols=focus_symbols,
        )
        cache_hits += int(stats.cache_hit)
        files_reparsed += int(stats.file_reparsed)
        parsed.add(relative)
    public_check_paths: dict[str, set[str]] = {}
    for check in public_checks:
        for argument in check.command:
            if argument.endswith(".py"):
                public_check_paths.setdefault(
                    argument.replace("\\", "/").removeprefix("./"), set(),
                ).add(check.check_id)
    for node_id, node in tuple(nodes.items()):
        if node.path in public_check_paths:
            nodes[node_id] = replace(
                node,
                editable=False,
                metadata={
                    **node.metadata,
                    "public_check": True,
                    "public_check_ids": tuple(sorted(public_check_paths[node.path])),
                },
            )
    changed_names = {
        node.symbol.split(".")[-1]
        for node in nodes.values() if node.path in initial_diff.changed_files
        and node.kind in {ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD, ProgramNodeKind.CLASS}
    } | set(initial_diff.changed_symbols)
    if changed_names and budget.direct_caller_depth >= 1:
        for name in sorted(changed_names):
            for relative in index.symbol_files.get(name, ()):
                if relative in parsed or len(parsed) >= budget.max_files:
                    continue
                stats = _parse_file(index, relative, nodes, edges, paths, budget)
                cache_hits += int(stats.cache_hit)
                files_reparsed += int(stats.file_reparsed)
                parsed.add(relative)
    _resolve_call_edges(nodes, edges)
    _limit_edges(edges, budget.max_edges)
    file_hashes = {
        relative: index.file_hashes[relative]
        for relative in parsed if relative in index.file_hashes
    }
    graph = ProgramGraph(
        patch_hash=initial_diff.patch_hash,
        base_commit=base_commit,
        nodes=nodes,
        edges=edges,
        path_classes=paths,
        file_hashes=file_hashes,
        symbol_index=index.symbol_files,
        files_reparsed=files_reparsed,
        symbols_expanded=len(paths),
        cache_hits=cache_hits,
    )
    from .slicing import compute_impact_cone
    graph.impact_cone = compute_impact_cone(graph, initial_diff.hunks, public_checks)
    return graph
