from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from reachpatch.models.isolation import is_official_only_path
from reachpatch.program_graph.index import RepositoryIndex
from reachpatch.program_graph.slice import ContextRequest


@dataclass(frozen=True, slots=True)
class ProposedEdit:
    relative_path: str
    start_line: int
    end_line: int
    expected_source: str
    replacement: str


@dataclass(slots=True)
class RepairToolExecutor:
    repository_root: Path
    repository_index: RepositoryIndex
    current_diff: str = ""
    public_checks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    allowed_test_paths: set[str] = field(default_factory=set)
    staged_edits: list[ProposedEdit] = field(default_factory=list)
    context_requests: list[ContextRequest] = field(default_factory=list)

    @staticmethod
    def _is_test_path(relative_path: str) -> bool:
        path = Path(relative_path)
        return (
            "tests" in path.parts or "test" in path.parts
            or path.name.startswith("test_") or path.name.endswith("_test.py")
        )

    @staticmethod
    def _is_official_only_path(relative_path: str) -> bool:
        return is_official_only_path(relative_path)

    def _path(self, relative_path: str, *, for_edit: bool = False) -> Path:
        root = self.repository_root.resolve()
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("path escapes repository")
        if any(part in {".git", ".reachpatch"} for part in Path(relative_path).parts):
            raise ValueError("metadata paths are forbidden")
        normalized = str(path.relative_to(root)).replace("\\", "/")
        if self._is_official_only_path(normalized):
            raise ValueError("official harness or gold evidence paths are forbidden")
        if self._is_test_path(normalized):
            if for_edit:
                raise ValueError("test edits are forbidden")
            if normalized not in self.allowed_test_paths:
                raise ValueError("test path is not public evidence for this instance")
        return path

    def search_code(self, query: str, paths: Iterable[str] | None = None) -> dict:
        if not query or len(query) > 300:
            raise ValueError("invalid search query")
        expression = re.compile(re.escape(query), re.IGNORECASE)
        selected = set(paths or self.repository_index.source_hashes)
        matches = []
        for relative in sorted(selected):
            if relative not in self.repository_index.source_hashes:
                continue
            try:
                path = self._path(relative)
            except ValueError:
                continue
            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if expression.search(line):
                    matches.append({"path": relative, "line": line_no, "text": line[:500]})
                    if len(matches) >= 100:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
        source = self._path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start_line or 1)
        end = min(len(source), end_line or min(len(source), start + 399))
        if end < start or end - start > 500:
            raise ValueError("read range must contain at most 501 lines")
        return {"path": path, "start_line": start, "end_line": end,
                "content": "\n".join(source[start - 1:end])}

    def inspect_symbol(self, symbol: str) -> dict:
        locations = self.repository_index.symbols.get(symbol, ()) or self.repository_index.symbols.get(symbol.rsplit(".", 1)[-1], ())
        return {"symbol": symbol, "locations": [item.to_dict() for item in locations[:20]]}

    def find_references(self, symbol: str) -> dict:
        return self.search_code(symbol.rsplit(".", 1)[-1])

    def find_callers(self, symbol: str) -> dict:
        references = self.find_references(symbol)
        definitions = {(item.relative_path, item.line) for item in self.repository_index.symbols.get(symbol, ())}
        references["matches"] = [
            item for item in references["matches"]
            if (item["path"], item["line"]) not in definitions
        ]
        return references

    def show_current_diff(self) -> dict:
        return {"diff": self.current_diff}

    def run_public_check(self, check_id: str) -> dict:
        command = self.public_checks.get(check_id)
        if command is None:
            raise ValueError("unknown public check id")
        if self.staged_edits:
            return {
                "check_id": check_id,
                "deferred_to_transition": True,
                "reason": "proposed edits are applied transactionally by the controller",
            }
        completed = subprocess.run(
            command, cwd=self.repository_root, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
            check=False, shell=False,
        )
        return {"check_id": check_id, "return_code": completed.returncode,
                "stdout": completed.stdout[-8000:], "stderr": completed.stderr[-8000:]}

    def request_program_slice(self, symbols: Iterable[str], relation_kinds: Iterable[str]) -> dict:
        request = ContextRequest(
            symbols=tuple(sorted(set(map(str, symbols)))),
            relation_kinds=tuple(sorted(set(map(str, relation_kinds)))),
        )
        self.context_requests.append(request)
        return {"accepted": True, "request": request.to_dict()}

    def apply_edits(self, edits: Iterable[ProposedEdit]) -> dict:
        candidate: list[ProposedEdit] = []
        relocated: list[dict[str, int | str]] = []
        for edit in edits:
            path = self._path(edit.relative_path, for_edit=True)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            expected_lines = edit.expected_source.rstrip("\n").splitlines()
            start = edit.start_line
            end = edit.end_line
            actual = "\n".join(lines[start - 1:end])
            if actual != edit.expected_source.rstrip("\n") and expected_lines:
                matches = [
                    index + 1
                    for index in range(len(lines) - len(expected_lines) + 1)
                    if lines[index:index + len(expected_lines)] == expected_lines
                ]
                if len(matches) == 1:
                    start = matches[0]
                    end = start + len(expected_lines) - 1
                    relocated.append({
                        "path": edit.relative_path,
                        "from_start_line": edit.start_line,
                        "to_start_line": start,
                    })
                elif len(matches) > 1:
                    raise ValueError(
                        f"expected source is ambiguous: {edit.relative_path}:{edit.start_line}"
                    )
            candidate.append(ProposedEdit(
                relative_path=edit.relative_path,
                start_line=start,
                end_line=end,
                expected_source=edit.expected_source,
                replacement=edit.replacement,
            ))
        occupied: dict[str, list[tuple[int, int]]] = {}
        for staged in self.staged_edits:
            occupied.setdefault(staged.relative_path, []).append(
                (staged.start_line, staged.end_line)
            )
        for edit in candidate:
            if edit.start_line < 1 or edit.end_line < edit.start_line:
                raise ValueError("invalid edit range")
            path = self._path(edit.relative_path, for_edit=True)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            actual = "\n".join(lines[edit.start_line - 1:edit.end_line])
            if actual != edit.expected_source.rstrip("\n"):
                raise ValueError(f"expected source mismatch: {edit.relative_path}:{edit.start_line}")
            ranges = occupied.setdefault(edit.relative_path, [])
            if any(not (edit.end_line < start or edit.start_line > end) for start, end in ranges):
                raise ValueError("overlapping edits in one revision")
            ranges.append((edit.start_line, edit.end_line))
        self.staged_edits.extend(candidate)
        return {"accepted": True, "edit_count": len(candidate),
                "paths": sorted({item.relative_path for item in candidate}),
                "relocated": relocated}

    def finish_revision(self, summary: str) -> dict:
        if not self.staged_edits:
            raise ValueError("revision has no staged edits")
        return {"finished": True, "summary": summary,
                "edit_count": len(self.staged_edits)}
