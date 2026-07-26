from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from reachpatch.models.base import stable_id
from reachpatch.models.graph import GraphNode
from reachpatch.program_graph.budget import GraphBudget
from reachpatch.program_graph.models import CFGRecord, ProgramGraph


_SOURCE_LINES_CACHE: dict[int, tuple[str, list[str]]] = {}
_SOURCE_LINES_CACHE_LIMIT = 2


def _source_lines(source: str) -> list[str]:
    key = id(source)
    cached = _SOURCE_LINES_CACHE.get(key)
    if cached is not None and cached[0] is source:
        return cached[1]
    # Match ast.get_source_segment's parser line splitting, but perform it
    # once per source string instead of once per AST node.
    line_pattern = getattr(ast, "_line_pattern", None)
    if line_pattern is None:
        lines = source.splitlines(keepends=True)
    else:
        lines = [match[0] for match in line_pattern.finditer(source)]
    if len(_SOURCE_LINES_CACHE) >= _SOURCE_LINES_CACHE_LIMIT:
        _SOURCE_LINES_CACHE.pop(next(iter(_SOURCE_LINES_CACHE)))
    _SOURCE_LINES_CACHE[key] = (source, lines)
    return lines


def source_segment(source: str, node: ast.AST) -> str:
    try:
        if node.end_lineno is None or node.end_col_offset is None:
            segment = None
        else:
            lineno = node.lineno - 1
            end_lineno = node.end_lineno - 1
            col_offset = node.col_offset
            end_col_offset = node.end_col_offset
            lines = _source_lines(source)
            if end_lineno == lineno:
                segment = lines[lineno].encode()[col_offset:end_col_offset].decode()
            else:
                first = lines[lineno].encode()[col_offset:].decode()
                last = lines[end_lineno].encode()[:end_col_offset].decode()
                segment = "".join([first, *lines[lineno + 1:end_lineno], last])
    except (AttributeError, IndexError, UnicodeDecodeError):
        segment = ast.get_source_segment(source, node)
    result = segment if segment is not None else ast.dump(
        node, annotate_fields=True, include_attributes=False,
    )
    return result


def node_location(relative_path: str, node: ast.AST) -> dict[str, int | str]:
    line = int(getattr(node, "lineno", 1))
    column = int(getattr(node, "col_offset", 0))
    end_line = int(getattr(node, "end_lineno", line))
    end_column = int(getattr(node, "end_col_offset", column))
    return {
        "file": relative_path,
        "line": line,
        "column": column,
        "end_line": end_line,
        "end_column": end_column,
    }


@dataclass(slots=True)
class ModuleAnalysis:
    relative_path: str
    module_name: str
    source: str
    tree: ast.Module
    module_node_id: str
    ast_node_ids: dict[int, str]
    parent_ids: dict[int, str]
    qualified_by_ast: dict[int, str]
    active_callable_ast_ids: set[int]


class DefinitionScopeAnalyzer(ast.NodeVisitor):
    """Create stable structural nodes before relation-specific passes."""

    def __init__(
        self,
        graph: ProgramGraph,
        *,
        relative_path: str,
        module_name: str,
        source: str,
        tree: ast.Module,
        is_test: bool,
        declarations_only: bool = False,
        active_callable_ids: set[str] | None = None,
        precise: bool = True,
        budget: GraphBudget | None = None,
    ) -> None:
        self.graph = graph
        self.relative_path = relative_path
        self.module_name = module_name
        self.source = source
        self.tree = tree
        self.is_test = is_test
        self.declarations_only = declarations_only
        self.active_callable_ids = active_callable_ids
        self.precise = precise
        self.budget = budget
        self.active_callable_ast_ids: set[int] = set()
        self.ast_node_ids: dict[int, str] = {}
        self.parent_ids: dict[int, str] = {}
        self.qualified_by_ast: dict[int, str] = {}
        self.scope_names: list[str] = [module_name]
        self.container_ids: list[str] = []
        module_node = self._make_node(
            tree,
            "test" if is_test else "module",
            module_name,
            qualified_name=module_name,
            externally_controllable=is_test,
        )
        self.module_node_id = module_node.node_id
        self.container_ids.append(module_node.node_id)

    @property
    def qualified_scope(self) -> str:
        return ".".join(self.scope_names)

    def _visit_index_symbols(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                self.visit(child)
            elif isinstance(child, ast.Name):
                self.visit_Name(child)
            elif isinstance(child, ast.Attribute):
                self.visit_Attribute(child)
            else:
                self._visit_index_symbols(child)

    def _make_node(
        self,
        node: ast.AST,
        kind: str,
        label: str,
        **attributes: object,
    ) -> GraphNode:
        if self.budget is not None and not self.budget.check(
            nodes=len(self.graph.nodes), edges=len(self.graph.edges)
        ):
            raise GraphBudgetReached(self.budget.truncated_reason or "GRAPH_LIMIT")
        location = node_location(self.relative_path, node)
        node_id = stable_id(
            "program-node",
            self.relative_path,
            kind,
            location["line"],
            location["column"],
            label,
        )
        record = GraphNode(
            node_id=node_id,
            kind=kind,
            label=label,
            attributes={
                **location,
                "ast_kind": type(node).__name__,
                **attributes,
            },
            provenance_ids=(),
        )
        self.graph.index_node(record)
        self.ast_node_ids[id(node)] = node_id
        if (
            not self.declarations_only
            and self.container_ids
            and record.node_id != self.container_ids[-1]
        ):
            self.parent_ids[id(node)] = self.container_ids[-1]
            self.graph.add_relation("containment", [self.container_ids[-1]], [record.node_id])
        return record

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = f"{self.qualified_scope}.{node.name}"
        record = self._make_node(
            node,
            "class",
            node.name,
            qualified_name=qualified,
            externally_controllable=(not node.name.startswith("_") and len(self.scope_names) == 1),
        )
        self.qualified_by_ast[id(node)] = qualified
        if not self.declarations_only:
            self.graph.add_relation("defines", [self.container_ids[-1]], [record.node_id])
        if self.declarations_only:
            for decorator in node.decorator_list:
                self._visit_index_symbols(decorator)
        else:
            # Class decorators are evaluated in the enclosing scope before
            # the class name is bound. Keep both analysis passes aligned with
            # that Python evaluation rule.
            for decorator in node.decorator_list:
                self.visit(decorator)
        self.scope_names.append(node.name)
        self.container_ids.append(record.node_id)
        if self.declarations_only:
            for child in node.body:
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.visit(child)
                else:
                    self._visit_index_symbols(child)
        else:
            for child in node.body:
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.visit(child)
                elif self.active_callable_ids is None and self.precise:
                    self.visit(child)
        self.container_ids.pop()
        self.scope_names.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = f"{self.qualified_scope}.{node.name}"
        is_method = len(self.scope_names) > 1
        kind = "method" if is_method else "function"
        if any(isinstance(item, ast.Name) and item.id == "property" for item in node.decorator_list):
            kind = "property"
        if self.is_test and node.name.startswith("test"):
            kind = "test"
        record = self._make_node(
            node,
            kind,
            node.name,
            qualified_name=qualified,
            externally_controllable=(
                self.is_test or (not node.name.startswith("_") and len(self.scope_names) == 1)
            ),
            async_function=isinstance(node, ast.AsyncFunctionDef),
        )
        self.qualified_by_ast[id(node)] = qualified
        active = (
            self.precise
            and (
                self.active_callable_ids is None
                or qualified in self.active_callable_ids
                or node.name in self.active_callable_ids
            )
        )
        if active:
            self.active_callable_ast_ids.add(id(node))
        if not self.declarations_only and active:
            self.graph.add_relation("defines", [self.container_ids[-1]], [record.node_id])
        if not self.declarations_only:
            for decorator in node.decorator_list:
                decorator_id_before = set(self.ast_node_ids.values())
                self.visit(decorator)
                for decorator_id in set(self.ast_node_ids.values()) - decorator_id_before:
                    self.graph.add_relation("decorates", [decorator_id], [record.node_id])
        else:
            for decorator in node.decorator_list:
                self._visit_index_symbols(decorator)
        self.scope_names.append(node.name)
        self.container_ids.append(record.node_id)
        arguments = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        if node.args.vararg:
            arguments.append(node.args.vararg)
        if node.args.kwarg:
            arguments.append(node.args.kwarg)
        for argument in arguments if (self.declarations_only or active) else ():
            argument_qualified = f"{qualified}.{argument.arg}"
            parameter = self._make_node(
                argument,
                "parameter",
                argument.arg,
                qualified_name=argument_qualified,
                parameter_of=record.node_id,
                annotation=ast.unparse(argument.annotation) if argument.annotation else None,
            )
            self.qualified_by_ast[id(argument)] = argument_qualified
            self.graph.add_relation("defines", [record.node_id], [parameter.node_id])
        if self.declarations_only:
            for child in node.body:
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.visit(child)
                else:
                    self._visit_index_symbols(child)
        elif active:
            for child in node.body:
                self.visit(child)
        else:
            for child in node.body:
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.visit(child)
        self.container_ids.pop()
        self.scope_names.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_If(self, node: ast.If) -> None:
        record = self._make_node(
            node,
            "branch",
            source_segment(self.source, node.test),
            predicate=ast.unparse(node.test),
        )
        self.container_ids.append(record.node_id)
        self.visit(node.test)
        for child in node.body + node.orelse:
            self.visit(child)
        self.container_ids.pop()

    def visit_Match(self, node: ast.Match) -> None:
        record = self._make_node(
            node,
            "branch",
            source_segment(self.source, node.subject),
            predicate=ast.unparse(node.subject),
            match_cases=len(node.cases),
        )
        self.container_ids.append(record.node_id)
        self.generic_visit(node)
        self.container_ids.pop()

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node, f"for {ast.unparse(node.target)} in {ast.unparse(node.iter)}")

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node, f"async for {ast.unparse(node.target)} in {ast.unparse(node.iter)}")

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node, f"while {ast.unparse(node.test)}")

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While, label: str) -> None:
        record = self._make_node(node, "loop", label, predicate=label)
        self.container_ids.append(record.node_id)
        self.generic_visit(node)
        self.container_ids.pop()

    def visit_Assert(self, node: ast.Assert) -> None:
        self._make_node(
            node,
            "assertion",
            ast.unparse(node.test),
            observation=True,
            externally_controllable=self.is_test,
        )
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self._make_node(node, "return", ast.unparse(node.value) if node.value else "return")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self._make_node(node, "exception", source_segment(self.source, node))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._make_node(
            node,
            "call_site",
            ast.unparse(node.func),
            callee_expression=ast.unparse(node.func),
        )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        kind = "local"
        record = self._make_node(
            node,
            kind,
            node.id,
            qualified_name=f"{self.qualified_scope}.{node.id}",
            context=type(node.ctx).__name__,
            symbol=node.id,
        )
        self.qualified_by_ast[id(node)] = str(record.attributes["qualified_name"])

    def visit_Attribute(self, node: ast.Attribute) -> None:
        base = ast.unparse(node.value)
        self._make_node(
            node,
            "field",
            f"{base}.{node.attr}",
            qualified_name=f"{self.qualified_scope}.{base}.{node.attr}",
            context=type(node.ctx).__name__,
            field=node.attr,
            owner_expression=base,
        )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._make_node(node, "statement", source_segment(self.source, node))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._make_node(node, "statement", source_segment(self.source, node))
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._make_node(node, "statement", source_segment(self.source, node))
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        self._make_node(node, "statement", source_segment(self.source, node))
        self.generic_visit(node)

    def generic_visit(self, node: ast.AST) -> None:
        if self.declarations_only:
            if isinstance(node, ast.Module):
                for child in node.body:
                    if isinstance(
                        child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        self.visit(child)
                    else:
                        self._visit_index_symbols(child)
            else:
                self._visit_index_symbols(node)
            return
        if (
            isinstance(node, ast.Module)
            and self.active_callable_ids is not None
        ):
            for child in node.body:
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.visit(child)
            return
        if id(node) not in self.ast_node_ids and isinstance(node, (ast.expr, ast.stmt)):
            kind = "statement" if isinstance(node, ast.stmt) else "expression"
            if isinstance(node, (ast.List, ast.Tuple, ast.Dict, ast.Set)):
                kind = "container_shape"
            self._make_node(node, kind, source_segment(self.source, node))
        super().generic_visit(node)

    def result(self) -> ModuleAnalysis:
        return ModuleAnalysis(
            relative_path=self.relative_path,
            module_name=self.module_name,
            source=self.source,
            tree=self.tree,
            module_node_id=self.module_node_id,
            ast_node_ids=self.ast_node_ids,
            parent_ids=self.parent_ids,
            qualified_by_ast=self.qualified_by_ast,
            active_callable_ast_ids=self.active_callable_ast_ids,
        )


class GraphBudgetReached(RuntimeError):
    """Stops a precise graph pass while preserving the completed partial graph."""


def iter_callable_body_without_nested_callables(
    callable_ast: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
) -> Iterable[ast.AST]:
    """Iterate one callable without charging nested callable/class bodies twice."""

    stack = list(reversed(list(ast.iter_child_nodes(callable_ast))))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        yield node
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


class CFGBuilder:
    def __init__(
        self,
        graph: ProgramGraph,
        analysis: ModuleAnalysis,
        *,
        budget: GraphBudget | None = None,
    ) -> None:
        self.graph = graph
        self.analysis = analysis
        self.budget = budget
        self.edge_ids_by_callable: dict[str, list[str]] = defaultdict(list)

    def _node_id(self, node: ast.AST) -> str | None:
        return self.analysis.ast_node_ids.get(id(node))

    def _edge(self, source: str, target: str, condition: str = "True", **attributes: object) -> None:
        if self.budget is not None and not self.budget.check(
            nodes=len(self.graph.nodes), edges=len(self.graph.edges)
        ):
            return
        edge = self.graph.add_relation(
            "control_flow",
            [source],
            [target],
            condition=condition,
            attributes=dict(attributes),
        )
        owner = self._callable_owner(source)
        if owner:
            self.edge_ids_by_callable[owner].append(edge.edge_id)

    def _callable_owner(self, node_id: str) -> str | None:
        cursor = node_id
        seen: set[str] = set()
        while cursor not in seen:
            seen.add(cursor)
            incoming = self.graph.incoming(cursor, {"containment"})
            if not incoming:
                return None
            cursor = incoming[0].source_ids[0]
            if self.graph.nodes[cursor].kind in {"function", "method", "property", "test"}:
                return cursor
        return None

    def _statement_entry(self, node: ast.stmt) -> str | None:
        return self._node_id(node)

    def _build_iterative(self, statements: list[ast.stmt]) -> tuple[str | None, set[str]]:
        entry = next((self._statement_entry(item) for item in statements if self._statement_entry(item)), None)
        exits: set[str] = set()
        worklist: list[tuple[list[ast.stmt], str | None, str | None, str | None]] = [
            (statements, None, None, None)
        ]
        while worklist:
            if self.budget is not None and not self.budget.check(
                nodes=len(self.graph.nodes), edges=len(self.graph.edges)
            ):
                break
            block, following, loop_header, loop_exit = worklist.pop()
            for index, statement in enumerate(block):
                if self.budget is not None and not self.budget.check(
                    nodes=len(self.graph.nodes), edges=len(self.graph.edges)
                ):
                    return entry, exits
                node_id = self._statement_entry(statement)
                if node_id is None:
                    continue
                next_id = next(
                    (self._statement_entry(item) for item in block[index + 1:] if self._statement_entry(item)),
                    following,
                )
                if isinstance(statement, ast.If):
                    true_entry = next((self._statement_entry(item) for item in statement.body if self._statement_entry(item)), next_id)
                    false_entry = next((self._statement_entry(item) for item in statement.orelse if self._statement_entry(item)), next_id)
                    if true_entry:
                        self._edge(node_id, true_entry, ast.unparse(statement.test), branch_outcome=True)
                    if false_entry:
                        self._edge(node_id, false_entry, f"not ({ast.unparse(statement.test)})", branch_outcome=False)
                    worklist.append((statement.body, next_id, loop_header, loop_exit))
                    worklist.append((statement.orelse, next_id, loop_header, loop_exit))
                elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                    predicate = ast.unparse(statement.test) if isinstance(statement, ast.While) else f"iterable({ast.unparse(statement.iter)})"
                    body_entry = next((self._statement_entry(item) for item in statement.body if self._statement_entry(item)), node_id)
                    self._edge(node_id, body_entry, predicate, loop_class="one_or_many")
                    if next_id:
                        self._edge(node_id, next_id, f"not ({predicate})", loop_class="zero_or_exit")
                    worklist.append((statement.body, node_id, node_id, next_id))
                    worklist.append((statement.orelse, next_id, loop_header, loop_exit))
                elif isinstance(statement, ast.Try):
                    final_entry = next((self._statement_entry(item) for item in statement.finalbody if self._statement_entry(item)), next_id)
                    body_entry = next((self._statement_entry(item) for item in statement.body if self._statement_entry(item)), final_entry)
                    if body_entry:
                        self._edge(node_id, body_entry, "normal", exit_kind="normal")
                    for handler in statement.handlers:
                        handler_entry = next((self._statement_entry(item) for item in handler.body if self._statement_entry(item)), final_entry)
                        if handler_entry:
                            exception_name = ast.unparse(handler.type) if handler.type else "BaseException"
                            edge = self.graph.add_relation("exception_flow", [node_id], [handler_entry], condition=f"raises({exception_name})", attributes={"exception": exception_name})
                            self.edge_ids_by_callable[self._callable_owner(node_id) or node_id].append(edge.edge_id)
                        worklist.append((handler.body, final_entry, loop_header, loop_exit))
                    worklist.append((statement.body, final_entry, loop_header, loop_exit))
                    worklist.append((statement.orelse, final_entry, loop_header, loop_exit))
                    worklist.append((statement.finalbody, next_id, loop_header, loop_exit))
                elif isinstance(statement, (ast.With, ast.AsyncWith)):
                    body_entry = next((self._statement_entry(item) for item in statement.body if self._statement_entry(item)), next_id)
                    if body_entry:
                        self._edge(node_id, body_entry, "context_enter")
                    worklist.append((statement.body, next_id, loop_header, loop_exit))
                elif isinstance(statement, ast.Break):
                    if loop_exit:
                        self._edge(node_id, loop_exit, "break")
                    else:
                        exits.add(node_id)
                elif isinstance(statement, ast.Continue):
                    if loop_header:
                        self._edge(node_id, loop_header, "continue")
                    else:
                        exits.add(node_id)
                elif isinstance(statement, (ast.Return, ast.Raise)):
                    exits.add(node_id)
                elif next_id:
                    self._edge(node_id, next_id)
                elif loop_header:
                    self._edge(node_id, loop_header, "loop_back", loop_class="many")
                else:
                    exits.add(node_id)
        return entry, exits

    def build(self) -> dict[str, CFGRecord]:
        callables = [
            node
            for node in ast.walk(self.analysis.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for callable_ast in callables:
            if id(callable_ast) not in self.analysis.active_callable_ast_ids:
                continue
            callable_id = self._node_id(callable_ast)
            if callable_id is None:
                continue
            entry, exits = self._build_iterative(callable_ast.body)
            if entry:
                self._edge(callable_id, entry, "entry")
            statement_ids = tuple(
                node_id
                for node in iter_callable_body_without_nested_callables(callable_ast)
                if isinstance(node, ast.stmt)
                and (node_id := self._node_id(node)) is not None
            )
            self.graph.add_cfg(CFGRecord(
                callable_id=callable_id,
                entry_node_id=entry or callable_id,
                exit_node_ids=tuple(sorted(exits or {callable_id})),
                statement_node_ids=tuple(sorted(set(statement_ids))),
                edge_ids=tuple(sorted(set(self.edge_ids_by_callable.get(callable_id, [])))),
            ))
        return self.graph.cfgs


class DefUseAnalyzer:
    def __init__(
        self,
        graph: ProgramGraph,
        analysis: ModuleAnalysis,
        *,
        budget: GraphBudget | None = None,
    ) -> None:
        self.graph = graph
        self.analysis = analysis
        self.budget = budget

    def run(self) -> None:
        for callable_ast in [
            node
            for node in ast.walk(self.analysis.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            if self.budget is not None and not self.budget.check(
                nodes=len(self.graph.nodes), edges=len(self.graph.edges)
            ):
                return
            if id(callable_ast) not in self.analysis.active_callable_ast_ids:
                continue
            last_defs: dict[str, set[str]] = defaultdict(set)
            parameter_by_name = {
                argument.arg: self.analysis.ast_node_ids[id(argument)]
                for argument in (
                    list(callable_ast.args.posonlyargs)
                    + list(callable_ast.args.args)
                    + list(callable_ast.args.kwonlyargs)
                )
                if id(argument) in self.analysis.ast_node_ids
            }
            for name, node_id in parameter_by_name.items():
                last_defs[name].add(node_id)
            ordered_nodes = sorted(
                iter_callable_body_without_nested_callables(callable_ast),
                key=lambda item: (
                    int(getattr(item, "lineno", 0)),
                    int(getattr(item, "col_offset", 0)),
                    type(item).__name__,
                ),
            )
            for node in ordered_nodes:
                if self.budget is not None and not self.budget.check(
                    nodes=len(self.graph.nodes), edges=len(self.graph.edges)
                ):
                    return
                if isinstance(node, ast.Name):
                    node_id = self.analysis.ast_node_ids.get(id(node))
                    if node_id is None:
                        continue
                    if isinstance(node.ctx, ast.Load):
                        for definition_id in sorted(last_defs.get(node.id, ())):
                            self.graph.add_relation("def_use", [definition_id], [node_id])
                            self.graph.add_relation("data_flow", [definition_id], [node_id])
                    else:
                        last_defs[node.id] = {node_id}
                elif isinstance(node, ast.Attribute):
                    node_id = self.analysis.ast_node_ids.get(id(node))
                    if node_id is None:
                        continue
                    context = getattr(node, "ctx", ast.Load())
                    relation = "state_read" if isinstance(context, ast.Load) else "state_write"
                    owner_nodes = [
                        child_id
                        for child in iter_ast_without_nested_callables(node.value)
                        if (child_id := self.analysis.ast_node_ids.get(id(child))) is not None
                    ]
                    if owner_nodes:
                        self.graph.add_relation(relation, [owner_nodes[0]], [node_id])
                        self.graph.add_relation("field_flow", [owner_nodes[0]], [node_id])
                elif isinstance(node, ast.Return) and node.value is not None:
                    return_id = self.analysis.ast_node_ids.get(id(node))
                    value_id = self.analysis.ast_node_ids.get(id(node.value))
                    if return_id and value_id:
                        self.graph.add_relation("return_flow", [value_id], [return_id])
                elif isinstance(node, ast.Raise) and node.exc is not None:
                    raise_id = self.analysis.ast_node_ids.get(id(node))
                    exception_id = self.analysis.ast_node_ids.get(id(node.exc))
                    if raise_id and exception_id:
                        self.graph.add_relation("raises", [exception_id], [raise_id])


def iter_ast_without_nested_callables(root: ast.AST) -> Iterable[ast.AST]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        if node is not root and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def build_cfg_iterative(
    callable_ast: ast.FunctionDef | ast.AsyncFunctionDef,
    analysis: ModuleAnalysis,
    graph: ProgramGraph,
) -> CFGRecord | None:
    builder = CFGBuilder(graph, analysis)
    callable_id = analysis.ast_node_ids.get(id(callable_ast))
    if callable_id is None or id(callable_ast) not in analysis.active_callable_ast_ids:
        return None
    entry, exits = builder._build_iterative(callable_ast.body)
    if entry:
        builder._edge(callable_id, entry, "entry")
    record = CFGRecord(
        callable_id=callable_id, entry_node_id=entry or callable_id,
        exit_node_ids=tuple(sorted(exits or {callable_id})),
        statement_node_ids=tuple(sorted({
            node_id for node in iter_callable_body_without_nested_callables(callable_ast)
            if isinstance(node, ast.stmt)
            and (node_id := analysis.ast_node_ids.get(id(node))) is not None
        })),
        edge_ids=tuple(sorted(set(builder.edge_ids_by_callable.get(callable_id, ())))),
    )
    graph.add_cfg(record)
    return record
