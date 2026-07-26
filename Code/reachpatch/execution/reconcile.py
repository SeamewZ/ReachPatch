from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from reachpatch.models.base import SerializableRecord, content_hash, stable_id

_EXCLUDES = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".reachpatch"}
_TEXT_SUFFIXES = {
    ".py", ".pyi", ".toml", ".ini", ".cfg", ".json", ".yaml", ".yml", ".md", ".txt",
    ".rst", ".sh", ".sql", ".html", ".css", ".js", ".ts",
}


@dataclass(frozen=True, slots=True)
class DiffHunk(SerializableRecord):
    hunk_id: str
    file: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_lines: tuple[str, ...]
    deleted_lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangedRelation(SerializableRecord):
    relation_id: str
    kind: str
    file: str
    qualified_scope: str
    old_source: str | None
    new_source: str | None
    old_span: tuple[int, int] | None
    new_span: tuple[int, int] | None
    attributes: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ActualDiff(SerializableRecord):
    diff_id: str
    canonical_diff: str
    canonical_diff_hash: str
    base_tree_hash: str
    trial_tree_hash: str
    changed_files: tuple[str, ...]
    added_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    hunks: tuple[DiffHunk, ...]
    changed_relations: tuple[ChangedRelation, ...]
    forbidden_paths: tuple[str, ...]
    oracle_contamination_paths: tuple[str, ...]
    applies: bool
    empty: bool
    fingerprint: dict[str, Any]


def _files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for current_root, directories, names in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in _EXCLUDES)
        current = Path(current_root)
        for name in sorted(names):
            path = current / name
            relative = str(path.relative_to(root)).replace(os.sep, "/")
            result[relative] = path
    return result


def _tree_hash(root: Path, files: dict[str, Path]) -> str:
    digest = hashlib.sha256()
    for relative, path in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _text(path: Path | None) -> list[str]:
    if path is None:
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return []


def _parse_hunks(relative: str, diff_lines: list[str]) -> list[DiffHunk]:
    hunks: list[DiffHunk] = []
    header = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    current: dict[str, Any] | None = None
    for line in diff_lines:
        match = header.match(line)
        if match:
            if current:
                hunks.append(DiffHunk(**current))
            old_start, old_count, new_start, new_count = match.groups()
            current = {
                "hunk_id": "",
                "file": relative,
                "old_start": int(old_start),
                "old_count": int(old_count or 1),
                "new_start": int(new_start),
                "new_count": int(new_count or 1),
                "added_lines": [],
                "deleted_lines": [],
            }
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            current["added_lines"].append(line[1:].rstrip("\n"))
        elif current is not None and line.startswith("-") and not line.startswith("---"):
            current["deleted_lines"].append(line[1:].rstrip("\n"))
    if current:
        hunks.append(DiffHunk(**current))
    return [
        DiffHunk(
            hunk_id=stable_id("diff-hunk", item.file, item.old_start, item.new_start, item.added_lines, item.deleted_lines),
            file=item.file,
            old_start=item.old_start,
            old_count=item.old_count,
            new_start=item.new_start,
            new_count=item.new_count,
            added_lines=tuple(item.added_lines),
            deleted_lines=tuple(item.deleted_lines),
        )
        for item in hunks
    ]


@dataclass(frozen=True, slots=True)
class _AstFact:
    key: tuple[str, str, int]
    kind: str
    scope: str
    source: str
    span: tuple[int, int]
    attributes: dict[str, Any] = field(default_factory=dict)


class _FactCollector(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.scope: list[str] = []
        self.facts: list[_AstFact] = []

    @property
    def qualified_scope(self) -> str:
        return ".".join(self.scope) or "<module>"

    def _add(self, node: ast.AST, kind: str, **attributes: Any) -> None:
        segment = ast.get_source_segment(self.source, node) or ast.unparse(node)
        line = int(getattr(node, "lineno", 1))
        end = int(getattr(node, "end_lineno", line))
        ordinal = sum(1 for fact in self.facts if fact.kind == kind and fact.scope == self.qualified_scope)
        self.facts.append(_AstFact(
            key=(self.qualified_scope, kind, ordinal),
            kind=kind,
            scope=self.qualified_scope,
            source=segment,
            span=(line, end),
            attributes=attributes,
        ))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_If(self, node: ast.If) -> None:
        self._add(node.test, "guard", predicate=ast.unparse(node.test))
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self._add(node.test, "guard", predicate=ast.unparse(node.test), loop=True)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        callee = ast.unparse(node.func)
        self._add(
            node,
            "call",
            callee=callee,
            reflection=callee.rsplit(".", 1)[-1] in {"getattr", "setattr", "eval", "exec", "__import__"},
            external=callee.split(".", 1)[0] in {"open", "socket", "requests", "subprocess", "sqlite3"},
        )
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self._add(node, "return", value=ast.unparse(node.value) if node.value else None)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self._add(node, "exception", exception=ast.unparse(node.exc) if node.exc else None)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        targets = [ast.unparse(target) for target in node.targets]
        kind = "state" if any("." in target or "[" in target for target in targets) else "assignment"
        self._add(node, kind, targets=targets, value=ast.unparse(node.value))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        target = ast.unparse(node.target)
        kind = "state" if "." in target or "[" in target else "assignment"
        self._add(node, kind, targets=[target], value=ast.unparse(node.value) if node.value else None)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        target = ast.unparse(node.target)
        kind = "state" if "." in target or "[" in target else "assignment"
        self._add(node, kind, targets=[target], value=ast.unparse(node.value))
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        self._add(node, "dispatch", operation=type(node.op).__name__)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        self._add(node, "dispatch", operation=[type(item).__name__ for item in node.ops])
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self._add(node, "resource", async_context=False)
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._add(node, "resource", async_context=True)
        self.generic_visit(node)


def _ast_facts(source: str) -> dict[tuple[str, str, int], _AstFact]:
    tree = ast.parse(source)
    collector = _FactCollector(source)
    collector.visit(tree)
    return {fact.key: fact for fact in collector.facts}


def _changed_relations(relative: str, before: str, after: str) -> list[ChangedRelation]:
    try:
        old = _ast_facts(before) if before else {}
        new = _ast_facts(after) if after else {}
    except SyntaxError as exc:
        return [ChangedRelation(
            relation_id=stable_id("changed-relation", relative, "syntax", str(exc)),
            kind="syntax",
            file=relative,
            qualified_scope="<module>",
            old_source=None,
            new_source=None,
            old_span=None,
            new_span=None,
            attributes={"error": str(exc)},
        )]
    relations: list[ChangedRelation] = []
    for key in sorted(old.keys() | new.keys()):
        left = old.get(key)
        right = new.get(key)
        if left and right and left.source == right.source and left.attributes == right.attributes:
            continue
        base_kind = (right or left).kind
        change = "modified" if left and right else "added" if right else "deleted"
        kind = f"{base_kind}_{change}"
        attributes = {
            "change": change,
            "old": left.attributes if left else None,
            "new": right.attributes if right else None,
        }
        if base_kind == "call" and right and right.attributes.get("external"):
            kind = "external_effect_added" if not left else "external_effect_modified"
        if base_kind == "return" and left and not right:
            kind = "fallback_deleted"
        relations.append(ChangedRelation(
            relation_id=stable_id("changed-relation", relative, key, left, right),
            kind=kind,
            file=relative,
            qualified_scope=(right or left).scope,
            old_source=left.source if left else None,
            new_source=right.source if right else None,
            old_span=left.span if left else None,
            new_span=right.span if right else None,
            attributes=attributes,
        ))
    return relations


def reconcile_actual_diff(
    base_root: str | Path,
    trial_root: str | Path,
    *,
    forbidden_patterns: Iterable[str] = (),
) -> ActualDiff:
    base = Path(base_root).resolve()
    trial = Path(trial_root).resolve()
    if not base.is_dir() or not trial.is_dir():
        raise FileNotFoundError(f"base/trial directory missing: {base}, {trial}")
    base_files = _files(base)
    trial_files = _files(trial)
    changed: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    all_diff_lines: list[str] = []
    hunks: list[DiffHunk] = []
    relations: list[ChangedRelation] = []
    for relative in sorted(base_files.keys() | trial_files.keys()):
        before_path = base_files.get(relative)
        after_path = trial_files.get(relative)
        before_bytes = before_path.read_bytes() if before_path else b""
        after_bytes = after_path.read_bytes() if after_path else b""
        if before_bytes == after_bytes:
            continue
        changed.append(relative)
        if before_path is None:
            added.append(relative)
        if after_path is None:
            deleted.append(relative)
        before_lines = _text(before_path)
        after_lines = _text(after_path)
        diff_lines = list(difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            lineterm="\n",
        ))
        all_diff_lines.extend(diff_lines)
        hunks.extend(_parse_hunks(relative, diff_lines))
        if Path(relative).suffix == ".py":
            relations.extend(_changed_relations(
                relative,
                "".join(before_lines),
                "".join(after_lines),
            ))
        else:
            relations.append(ChangedRelation(
                relation_id=stable_id("changed-relation", relative, "non-python"),
                kind="external_surface_modified",
                file=relative,
                qualified_scope="<file>",
                old_source=None,
                new_source=None,
                old_span=None,
                new_span=None,
                attributes={"suffix": Path(relative).suffix},
            ))
    canonical_diff = "".join(all_diff_lines)
    forbidden = tuple(sorted(
        relative
        for relative in changed
        if any(Path(relative).match(pattern) for pattern in forbidden_patterns)
        or relative.startswith((".git/", ".reachpatch/"))
    ))
    contamination = tuple(sorted(
        relative
        for relative in changed
        if _is_oracle_path(relative)
    ))
    relation_fingerprint = [
        {
            "kind": relation.kind,
            "scope": relation.qualified_scope,
            "old": relation.old_source,
            "new": relation.new_source,
        }
        for relation in relations
    ]
    canonical_hash = content_hash(canonical_diff)
    return ActualDiff(
        diff_id=stable_id("actual-diff", canonical_hash, _tree_hash(base, base_files), _tree_hash(trial, trial_files)),
        canonical_diff=canonical_diff,
        canonical_diff_hash=canonical_hash,
        base_tree_hash=_tree_hash(base, base_files),
        trial_tree_hash=_tree_hash(trial, trial_files),
        changed_files=tuple(changed),
        added_files=tuple(added),
        deleted_files=tuple(deleted),
        hunks=tuple(hunks),
        changed_relations=tuple(relations),
        forbidden_paths=forbidden,
        oracle_contamination_paths=contamination,
        applies=not any(relation.kind == "syntax" for relation in relations),
        empty=not changed,
        fingerprint={
            "qualified_scopes": sorted({item.qualified_scope for item in relations}),
            "operations": relation_fingerprint,
            "changed_files": changed,
            "hash": content_hash(relation_fingerprint),
        },
    )


def _is_oracle_path(relative: str) -> bool:
    parts = Path(relative).parts
    name = Path(relative).name.lower()
    return (
        any(part.lower() in {"test", "tests", "testing"} for part in parts)
        or name.startswith("test_")
        or name.endswith("_test.py")
        or "oracle" in name
        or "gold" in name
        or "hidden" in name
    )
