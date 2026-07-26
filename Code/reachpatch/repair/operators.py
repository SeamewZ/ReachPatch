from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from reachpatch.execution.reconcile import ActualDiff, reconcile_actual_diff
from reachpatch.models.base import SerializableRecord
from reachpatch.models.controller import RepairAction, StructuredEditIntent
from reachpatch.program_graph.models import ProgramGraph


@dataclass(frozen=True, slots=True)
class RegisteredDiffOperator(SerializableRecord):
    name: str
    allowed_ast_kinds: tuple[str, ...]
    permits_deletion: bool
    permits_insertion: bool
    control_flow_effects: tuple[str, ...]
    exception_effects: tuple[str, ...]
    object_shape_effects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperatorApplication(SerializableRecord):
    action_id: str
    edit_ids: tuple[str, ...]
    actual_diff: ActualDiff
    touched_node_ids: tuple[str, ...]
    declared_read_set: tuple[str, ...]
    declared_write_set: tuple[str, ...]


_REGISTRY = {
    item.name: item
    for item in (
        RegisteredDiffOperator(
            "replace_return", ("Return",), False, False,
            ("return_source",), (), ("return_representation",),
        ),
        RegisteredDiffOperator(
            "replace_guard", ("If", "While", "IfExp"), False, False,
            ("branch_partition",), (), (),
        ),
        RegisteredDiffOperator(
            "replace_node",
            ("Return", "Raise", "Assign", "AnnAssign", "AugAssign", "Expr", "If", "While"),
            False, False, ("arbitrary_local",), ("may_change_exception",), ("may_change_shape",),
        ),
        RegisteredDiffOperator(
            "delete_node", ("Return", "Raise", "Assign", "AnnAssign", "Expr", "If"),
            True, False, ("deletion",), ("may_remove_exception",), ("may_remove_shape",),
        ),
        RegisteredDiffOperator(
            "insert_before", ("Return", "Raise", "Assign", "AnnAssign", "Expr", "If", "While"),
            False, True, ("new_predecessor",), ("may_add_exception",), ("may_add_shape",),
        ),
    )
}


def registered_operator(name: str) -> RegisteredDiffOperator | None:
    return _REGISTRY.get(name)


def _node_for_edit(tree: ast.AST, edit: StructuredEditIntent, node_kind: str) -> ast.AST:
    line, end_line = edit.expected_span or (0, 0)
    column = int(edit.payload.get("column", -1))
    end_column = int(edit.payload.get("end_column", -1))
    candidates = [
        node
        for node in ast.walk(tree)
        if type(node).__name__ == node_kind
        and int(getattr(node, "lineno", -1)) == line
        and int(getattr(node, "end_lineno", -1)) == end_line
        and (column < 0 or int(getattr(node, "col_offset", -1)) == column)
        and (end_column < 0 or int(getattr(node, "end_col_offset", -1)) == end_column)
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"edit {edit.edit_id} AST precondition matched {len(candidates)} nodes"
        )
    return candidates[0]


def _offset(lines: list[str], line: int, column: int) -> int:
    if line < 1 or line > len(lines):
        raise ValueError(f"line {line} outside source")
    return sum(len(item) for item in lines[: line - 1]) + column


def _validate_and_locate(
    source: str,
    edit: StructuredEditIntent,
    operator: RegisteredDiffOperator,
) -> tuple[int, int, str]:
    if edit.expected_span is None:
        raise ValueError(f"edit {edit.edit_id} requires an exact source span")
    tree = ast.parse(source, type_comments=True)
    node_kind = str(edit.payload.get("ast_kind", ""))
    if node_kind not in operator.allowed_ast_kinds:
        raise ValueError(
            f"operator {operator.name} does not accept AST kind {node_kind!r}"
        )
    node = _node_for_edit(tree, edit, node_kind)
    segment = ast.get_source_segment(source, node)
    if edit.expected_source is not None and segment != edit.expected_source:
        raise ValueError(
            f"edit {edit.edit_id} source precondition changed: {segment!r}"
        )
    lines = source.splitlines(keepends=True)
    start = _offset(lines, int(node.lineno), int(node.col_offset))
    end = _offset(lines, int(node.end_lineno), int(node.end_col_offset))
    replacement = edit.replacement or ""
    if operator.name == "delete_node" and not operator.permits_deletion:
        raise ValueError(f"operator {operator.name} cannot delete")
    if operator.name == "insert_before":
        indentation = " " * int(node.col_offset)
        inserted = "\n".join(
            indentation + line if line else line
            for line in replacement.splitlines()
        )
        replacement = inserted + "\n" + indentation + (segment or "")
    elif replacement:
        probe = replacement
        if node_kind in {"Return", "Raise"}:
            ast.parse("def _repair_probe():\n    " + probe.replace("\n", "\n    "))
        elif node_kind in {"Assign", "AnnAssign", "AugAssign", "Expr", "If", "While"}:
            ast.parse(probe)
    return start, end, replacement


def _apply_file_edits(root: Path, relative: str, edits: list[StructuredEditIntent]) -> None:
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"edit escapes trial root: {relative}")
    if not path.is_file() or path.suffix not in {".py", ".pyi"}:
        raise ValueError(f"registered AST edits require a Python file: {relative}")
    source = path.read_text(encoding="utf-8")
    replacements: list[tuple[int, int, str, str]] = []
    for edit in edits:
        operator = registered_operator(edit.operator)
        if operator is None:
            raise ValueError(f"unregistered diff operator {edit.operator!r}")
        start, end, replacement = _validate_and_locate(source, edit, operator)
        replacements.append((start, end, replacement, edit.edit_id))
    ordered = sorted(replacements, key=lambda item: item[0], reverse=True)
    for index, current in enumerate(ordered):
        for other in ordered[index + 1:]:
            if other[1] > current[0]:
                raise ValueError(f"overlapping edit intents {current[3]} and {other[3]}")
    updated = source
    for start, end, replacement, _ in ordered:
        updated = updated[:start] + replacement + updated[end:]
    ast.parse(updated, filename=relative, type_comments=True)
    path.write_text(updated, encoding="utf-8")


def apply_registered_operator(
    action: RepairAction,
    checkpoint_root: str | Path,
    trial_root: str | Path,
    program_graph: ProgramGraph,
    *,
    forbidden_patterns: Iterable[str] = (),
) -> OperatorApplication:
    checkpoint = Path(checkpoint_root).resolve()
    trial = Path(trial_root).resolve()
    if not checkpoint.is_dir() or not trial.is_dir():
        raise FileNotFoundError("checkpoint or trial tree is missing")
    if not action.edit_intents:
        raise ValueError("repair action contains no concrete edits")
    allowed_nodes = set(action.causal_cut_ids)
    by_file: dict[str, list[StructuredEditIntent]] = {}
    for edit in action.edit_intents:
        if edit.target_node_id not in program_graph.nodes:
            raise ValueError(f"edit targets unknown graph node {edit.target_node_id}")
        node = program_graph.nodes[edit.target_node_id]
        if str(node.attributes.get("file", "")) != edit.relative_path:
            raise ValueError(f"edit path disagrees with target node {edit.target_node_id}")
        if edit.target_node_id not in allowed_nodes:
            raise ValueError(f"edit {edit.edit_id} crosses the declared causal cut")
        by_file.setdefault(edit.relative_path, []).append(edit)
    for relative, edits in sorted(by_file.items()):
        _apply_file_edits(trial, relative, edits)
    actual = reconcile_actual_diff(
        checkpoint, trial, forbidden_patterns=forbidden_patterns
    )
    expected_files = set(by_file)
    unexpected = set(actual.changed_files) - expected_files
    if unexpected:
        raise ValueError(f"registered edits changed undeclared files: {sorted(unexpected)}")
    if actual.empty or not actual.applies:
        raise ValueError("registered repair produced an empty or invalid diff")
    return OperatorApplication(
        action_id=action.action_id,
        edit_ids=tuple(edit.edit_id for edit in action.edit_intents),
        actual_diff=actual,
        touched_node_ids=tuple(sorted(allowed_nodes)),
        declared_read_set=action.read_set,
        declared_write_set=action.write_set,
    )
