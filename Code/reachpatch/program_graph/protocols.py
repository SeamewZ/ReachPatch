from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from typing import Iterable

from reachpatch.models.base import stable_id
from reachpatch.models.graph import GraphNode
from reachpatch.program_graph.analysis import ModuleAnalysis, node_location, source_segment
from reachpatch.program_graph.models import ProgramGraph, ProtocolOperation

_BINARY_METHODS: dict[type[ast.operator], tuple[str, str]] = {
    ast.Add: ("__add__", "__radd__"),
    ast.Sub: ("__sub__", "__rsub__"),
    ast.Mult: ("__mul__", "__rmul__"),
    ast.MatMult: ("__matmul__", "__rmatmul__"),
    ast.Div: ("__truediv__", "__rtruediv__"),
    ast.FloorDiv: ("__floordiv__", "__rfloordiv__"),
    ast.Mod: ("__mod__", "__rmod__"),
    ast.Pow: ("__pow__", "__rpow__"),
    ast.LShift: ("__lshift__", "__rlshift__"),
    ast.RShift: ("__rshift__", "__rrshift__"),
    ast.BitOr: ("__or__", "__ror__"),
    ast.BitXor: ("__xor__", "__rxor__"),
    ast.BitAnd: ("__and__", "__rand__"),
}
_COMPARE_METHODS: dict[type[ast.cmpop], tuple[str, str]] = {
    ast.Lt: ("__lt__", "__gt__"),
    ast.LtE: ("__le__", "__ge__"),
    ast.Gt: ("__gt__", "__lt__"),
    ast.GtE: ("__ge__", "__le__"),
    ast.Eq: ("__eq__", "__eq__"),
    ast.NotEq: ("__ne__", "__ne__"),
}


@dataclass(frozen=True, slots=True)
class ProtocolFact:
    operation_id: str
    source_node_id: str
    kind: str
    label: str
    location: dict[str, int | str]
    left_expression: str | None
    right_expression: str | None
    method_names: tuple[str, ...]
    fallback_order: tuple[str, ...]
    not_implemented: bool
    conditions: tuple[str, ...]
    definitely_builtin: bool


def _builtin_target(graph: ProgramGraph, method: str) -> str:
    qualified = f"python.builtin.{method}"
    existing = graph.resolve_symbol(qualified)
    if existing:
        return existing[0]
    node = GraphNode.create(
        "external_interface",
        qualified,
        identity=qualified,
        attributes={
            "qualified_name": qualified,
            "file": "<python-runtime>",
            "externally_controllable": False,
            "builtin_protocol": True,
        },
    )
    graph.index_node(node)
    return node.node_id


class ProtocolAnalyzer:
    """Compile Python language operations into explicit candidate/selection IR."""

    def __init__(
        self,
        graph: ProgramGraph,
        analysis: ModuleAnalysis,
        *,
        defer_materialization: bool = False,
    ) -> None:
        self.graph = graph
        self.analysis = analysis
        self.defer_materialization = defer_materialization
        self.facts: list[ProtocolFact] = []

    @staticmethod
    def _definitely_builtin(expression: ast.AST) -> bool:
        return isinstance(
            expression,
            (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set),
        )

    def _record(
        self,
        node: ast.AST,
        *,
        kind: str,
        operands: tuple[ast.AST | None, ast.AST | None],
        methods: Iterable[str],
        fallback: Iterable[str],
        not_implemented: bool,
        conditions: Iterable[str] = (),
    ) -> ProtocolOperation | ProtocolFact:
        method_names = tuple(methods)
        source_node_id = self.analysis.ast_node_ids[id(node)]
        left_expression = ast.unparse(operands[0]) if operands[0] is not None else None
        right_expression = ast.unparse(operands[1]) if operands[1] is not None else None
        location = node_location(self.analysis.relative_path, node)
        fact = ProtocolFact(
            operation_id=stable_id(
                "protocol",
                self.analysis.relative_path,
                kind,
                location,
                method_names,
                left_expression,
                right_expression,
            ),
            source_node_id=source_node_id,
            kind=kind,
            label=source_segment(self.analysis.source, node),
            location=location,
            left_expression=left_expression,
            right_expression=right_expression,
            method_names=method_names,
            fallback_order=tuple(fallback),
            not_implemented=not_implemented,
            conditions=tuple(conditions),
            definitely_builtin=all(
                operand is None or self._definitely_builtin(operand)
                for operand in operands
            ),
        )
        if self.defer_materialization:
            self.facts.append(fact)
            return fact
        return self.materialize_fact(self.graph, fact)

    @staticmethod
    def materialize_fact(
        graph: ProgramGraph,
        fact: ProtocolFact,
    ) -> ProtocolOperation:
        method_names = fact.method_names
        targets_by_method = {
            method: tuple(graph.resolve_symbol(method))
            for method in method_names
        }
        candidates = graph.intern_target_ids({
            target for targets in targets_by_method.values() for target in targets
        })
        selected: str | None = None
        status = "candidate"
        if fact.definitely_builtin and method_names:
            selected = _builtin_target(graph, method_names[0])
            candidates = graph.intern_target_ids((*candidates, selected))
            status = "selected"
        operation_node = GraphNode(
            node_id=fact.operation_id,
            kind="protocol_operation",
            label=fact.label,
            attributes={
                **fact.location,
                "qualified_name": fact.operation_id,
                "operation_kind": fact.kind,
            },
            provenance_ids=(),
        )
        graph.index_node(operation_node)
        graph.add_relation(
            "containment", [fact.source_node_id], [fact.operation_id]
        )
        candidate_set = set(candidates)
        if selected is not None:
            graph.add_relation(
                "protocol_candidate",
                [fact.operation_id],
                [selected],
                confidence=1.0,
                attributes={
                    "method_names": list(method_names),
                    "dispatch_order": list(fact.fallback_order),
                },
            )
            candidate_set.discard(selected)
        if candidate_set:
            graph.add_relation(
                "protocol_candidate",
                [fact.operation_id],
                graph.intern_target_ids(candidate_set),
                confidence=0.6,
                attributes={
                    "method_names": list(method_names),
                    "dispatch_order": list(fact.fallback_order),
                    "candidate_set": True,
                },
            )
        for index, method in enumerate(fact.fallback_order):
            targets_for_method = list(targets_by_method.get(method, ()))
            if method == method_names[0] and selected is not None:
                targets_for_method.append(selected)
            if targets_for_method:
                graph.add_relation(
                    "protocol_fallback",
                    [fact.operation_id],
                    targets_for_method,
                    condition=f"fallback_position == {index}",
                    attributes={"position": index, "token": method},
                )
        if selected is not None:
            graph.add_relation(
                "protocol_selected", [fact.operation_id], [selected],
                attributes={"basis": "builtin_static_type"},
            )
        if not candidates:
            infeasible = GraphNode.create(
                "exception",
                f"infeasible protocol target: {method_names}",
                identity=(fact.operation_id, "infeasible"),
                attributes={
                    "qualified_name": f"protocol.infeasible.{fact.operation_id}",
                    "protocol_infeasible": True,
                    "method_names": list(method_names),
                },
            )
            graph.index_node(infeasible)
            graph.add_relation(
                "protocol_infeasible",
                [fact.operation_id],
                [infeasible.node_id],
                condition="no_candidate_target",
                attributes={"fallback_order": list(fact.fallback_order)},
            )
            frontier = graph.create_frontier(
                "DYNAMIC_PROTOCOL_TARGET",
                fact.operation_id,
                f"no statically resolved target for {method_names}",
                "run a targeted protocol-selection trace",
                hard=False,
            )
            status = f"frontier:{frontier.frontier_id}"
        operation = ProtocolOperation(
            operation_id=fact.operation_id,
            kind=fact.kind,
            source_node_id=fact.source_node_id,
            left_expression=fact.left_expression,
            right_expression=fact.right_expression,
            candidate_method_names=method_names,
            candidate_target_ids=graph.intern_target_ids(candidates),
            selected_target_id=selected,
            fallback_order=fact.fallback_order,
            status=status,
            conditions=fact.conditions,
            not_implemented_fallback=fact.not_implemented,
        )
        graph.add_protocol_operation(operation)
        return operation

    def run(self) -> dict[str, ProtocolOperation]:
        for node in ast.walk(self.analysis.tree):
            if id(node) not in self.analysis.ast_node_ids:
                continue
            if isinstance(node, ast.BinOp):
                methods = _BINARY_METHODS.get(type(node.op))
                if methods:
                    self._record(
                        node,
                        kind="binary",
                        operands=(node.left, node.right),
                        methods=methods,
                        fallback=(methods[0], "NotImplemented", methods[1], "TypeError"),
                        not_implemented=True,
                    )
            elif isinstance(node, ast.Compare):
                for operator, comparator in zip(node.ops, node.comparators, strict=True):
                    if isinstance(operator, (ast.In, ast.NotIn)):
                        self._record(
                            node,
                            kind="containment",
                            operands=(node.left, comparator),
                            methods=("__contains__", "__iter__", "__getitem__"),
                            fallback=("__contains__", "__iter__", "__getitem__"),
                            not_implemented=False,
                        )
                    elif type(operator) in _COMPARE_METHODS:
                        methods = _COMPARE_METHODS[type(operator)]
                        self._record(
                            node,
                            kind="comparison",
                            operands=(node.left, comparator),
                            methods=methods,
                            fallback=(methods[0], "NotImplemented", methods[1], "identity_fallback"),
                            not_implemented=True,
                        )
            elif isinstance(node, ast.Subscript):
                self._record(
                    node,
                    kind="indexing",
                    operands=(node.value, node.slice),
                    methods=("__getitem__",),
                    fallback=("__getitem__", "IndexError_or_KeyError"),
                    not_implemented=False,
                )
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                self._record(
                    node,
                    kind="iteration",
                    operands=(node.iter, None),
                    methods=("__iter__", "__next__", "__getitem__"),
                    fallback=("__iter__", "__getitem__", "StopIteration"),
                    not_implemented=False,
                    conditions=("empty", "nonempty"),
                )
            elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
                test = node.test
                self._record(
                    node,
                    kind="truthiness",
                    operands=(test, None),
                    methods=("__bool__", "__len__"),
                    fallback=("__bool__", "__len__", "truthy_default"),
                    not_implemented=False,
                    conditions=("truthy", "falsy", "empty", "nonempty"),
                )
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    methods = (
                        ("__aenter__", "__aexit__")
                        if isinstance(node, ast.AsyncWith)
                        else ("__enter__", "__exit__")
                    )
                    self._record(
                        node,
                        kind="context_manager",
                        operands=(item.context_expr, None),
                        methods=methods,
                        fallback=methods + ("exception_propagation",),
                        not_implemented=False,
                    )
        return self.graph.protocol_operations


def merge_observed_protocol_selection(
    graph: ProgramGraph,
    operation_id: str,
    selected_qualified_name: str,
    *,
    evidence_id: str,
) -> ProtocolOperation:
    operation = graph.protocol_operations[operation_id]
    targets = graph.resolve_symbol(selected_qualified_name)
    if not targets:
        target = GraphNode.create(
            "external_interface",
            selected_qualified_name,
            identity=(selected_qualified_name, evidence_id),
            attributes={
                "qualified_name": selected_qualified_name,
                "file": "<dynamic>",
                "observed": True,
            },
            provenance_ids=[evidence_id],
        )
        graph.index_node(target)
        targets = [target.node_id]
    selected = targets[0]
    graph.add_relation(
        "protocol_selected",
        [operation_id],
        [selected],
        attributes={"basis": "dynamic_trace"},
        provenance_ids=[evidence_id],
    )
    updated = replace(
        operation,
        candidate_target_ids=tuple(sorted(set(operation.candidate_target_ids) | {selected})),
        selected_target_id=selected,
        status="observed",
    )
    graph.add_protocol_operation(updated)
    return updated
