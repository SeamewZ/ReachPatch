from __future__ import annotations

import ast
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from reachpatch.execution.checks import execute_check
from reachpatch.execution.worktree import (
    apply_patch_action, apply_unified_diff, copy_source_tree, diff_between,
)
from reachpatch.models.base import canonical_json, stable_id
from reachpatch.models.execution import CheckStatus, ExecutableCheck, ReachAvoidState
from reachpatch.repair.execution_objective import InitialPatchObjective, RepairObjective


class RepairToolExecutor:
    def __init__(
        self,
        tree: Path,
        state: ReachAvoidState,
        objective: RepairObjective | InitialPatchObjective,
    ) -> None:
        self.tree = Path(tree).resolve()
        self.state = state
        self.objective = objective
        self.finished = False
        self.finish_summary = ""
        self._tool_events: list[dict[str, Any]] = []
        self._read_intervals: dict[str, list[tuple[int, int]]] = {}
        self._validation_results: dict[str, Any] = {}
        self._checks = self._validation_checks()
        self.allowed_commands = {item.command for item in self._checks}

    def _validation_checks(self) -> tuple[ExecutableCheck, ...]:
        if isinstance(self.objective, InitialPatchObjective):
            return ()
        checks: list[ExecutableCheck] = [
            *self.objective.locked_checks,
            *self.objective.preservation_checks,
        ]
        active = self.objective.active_failure
        active_check = next((
            item for item in (
                *self.state.target_checks,
                *self.state.preservation_checks,
                *self.state.challenge_checks,
            ) if item.check_id == active.check_id
        ), None)
        if active_check is not None:
            checks.append(active_check)
        unique: dict[str, ExecutableCheck] = {}
        for check in checks:
            unique.setdefault(check.check_id, check)
        return tuple(unique.values())

    def _record(self, name: str, arguments: dict[str, Any], result: Any = None, error: Exception | None = None) -> None:
        self._tool_events.append({
            "tool": name,
            "arguments": {
                key: ("<omitted>" if key == "patch" else value)
                for key, value in arguments.items()
            },
            "result": result,
            "error": str(error) if error else None,
        })

    def _safe_path(self, raw: str) -> Path:
        relative = Path(str(raw)).as_posix().lstrip("./")
        path = (self.tree / relative).resolve()
        if not path.is_relative_to(self.tree):
            raise ValueError("source path escapes working tree")
        return path

    def read_file(self, path: str, start_line: int = 1, end_line: int = 240) -> dict[str, Any]:
        source = self._safe_path(path)
        if not source.is_file():
            raise FileNotFoundError(path)
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        end = min(len(lines), max(start, int(end_line)))
        intervals = self._read_intervals.setdefault(Path(path).as_posix(), [])
        redundant = any(start >= left and end <= right for left, right in intervals)
        intervals.append((start, end))
        return {
            "path": Path(path).as_posix(),
            "start_line": start,
            "end_line": end,
            "content": "\n".join(
                f"{number}: {lines[number - 1]}"
                for number in range(start, end + 1)
            ),
            "eof": end >= len(lines),
            "next_start_line": None if end >= len(lines) else end + 1,
            "redundant": redundant,
        }

    def search_symbol(self, symbol: str) -> dict[str, Any]:
        raw_symbol = str(symbol).strip()
        needle = raw_symbol.rsplit(".", 1)[-1]
        if not re.fullmatch(r"[A-Za-z_]\w*", needle):
            raise ValueError("symbol must be a Python identifier")
        preferred = [
            item.path for item in getattr(self.objective, "relevant_source_slices", ())
        ]
        preferred.extend(diff_between(self.state.clean_snapshot, self.tree).changed_files)
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        # Initial generation has no diff or dynamic failure slice yet. Give
        # the model a bounded local AST index so it can locate an issue-named
        # API without constructing any static program graph. Tests, build
        # output and hidden artifacts are excluded from this source search.
        if not preferred:
            for path in self.tree.rglob("*.py"):
                relative = path.relative_to(self.tree).as_posix()
                if any(part in {
                    ".git", ".venv", "venv", "build", "dist",
                    "node_modules", "__pycache__", "artifacts", "harness",
                    "tests", "test",
                } for part in Path(relative).parts):
                    continue
                preferred.append(relative)
                if len(preferred) >= 2000:
                    break
        for relative in preferred:
            relative = str(relative).replace("\\", "/")
            if relative in seen:
                continue
            seen.add(relative)
            path = self.tree / relative
            if path.suffix != ".py" or not path.is_file():
                continue
            try:
                parsed = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(parsed):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == needle:
                    parent_name = None
                    if "." in raw_symbol:
                        requested_parent = raw_symbol.rsplit(".", 1)[0].rsplit(".", 1)[-1]
                        for parent in ast.walk(parsed):
                            if not isinstance(parent, ast.ClassDef) or parent.name != requested_parent:
                                continue
                            if any(child is node for child in ast.walk(parent)):
                                parent_name = parent.name
                                break
                        if parent_name is None:
                            continue
                    matches.append({
                        "path": relative,
                        "symbol": f"{parent_name}.{node.name}" if parent_name else node.name,
                        "start_line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                    })
                    if len(matches) >= 20:
                        return {"symbol": symbol, "matches": tuple(matches)}
        return {"symbol": symbol, "matches": tuple(matches[:20])}

    def inspect_diff(self) -> dict[str, Any]:
        return diff_between(self.state.clean_snapshot, self.tree).to_dict()

    def inspect_incremental_diff(self) -> dict[str, Any]:
        return diff_between(Path(self.state.working_checkpoint.snapshot_tree), self.tree).to_dict()

    def inspect_trace(self) -> dict[str, Any]:
        if isinstance(self.objective, InitialPatchObjective):
            return {"available": False, "reason": "INITIAL_PATCH"}
        failure = self.objective.active_failure
        return {
            "available": True,
            "failure_id": failure.failure_id,
            "first_project_frame": failure.first_project_frame,
            "traceback_frames": failure.traceback_frames,
            "entered_project_code": failure.entered_project_code,
        }

    def inspect_callers(self, symbol: str = "") -> dict[str, Any]:
        if isinstance(self.objective, InitialPatchObjective):
            return {"symbol": symbol, "callers": ()}
        graph = self.objective.dynamic_failure_graph
        if graph is None:
            return {"symbol": symbol, "callers": ()}
        nodes = graph.nodes
        callers = []
        for edge in graph.edges.values():
            if str(edge.kind) != "DYNAMIC_CALL":
                continue
            target = nodes.get(edge.target_id)
            source = nodes.get(edge.source_id)
            if target is None or source is None:
                continue
            if symbol and symbol not in target.symbol:
                continue
            callers.append({
                "caller": source.symbol, "callee": target.symbol,
                "path": source.path, "distance": edge.distance,
            })
        return {"symbol": symbol, "callers": tuple(callers[:20])}

    def _apply_incremental(self, patch: str) -> None:
        apply_patch_action(self.tree, patch)

    def _apply_cumulative_as_incremental(self, patch: str) -> bool:
        current = diff_between(self.state.clean_snapshot, self.tree)
        if current.empty:
            return False
        temporary = Path(tempfile.mkdtemp(prefix="reachpatch-cumulative-", dir=self.state.run_root))
        expected = temporary / "expected"
        try:
            copy_source_tree(self.state.clean_snapshot, expected)
            apply_patch_action(expected, patch)
            proposed = diff_between(self.state.clean_snapshot, expected)
            # A cumulative response must carry every currently changed file.
            # Otherwise it is an incremental patch that merely also applies to
            # clean and must be applied directly to the current working tree.
            if not set(current.changed_files).issubset(proposed.changed_files):
                return False
            incremental = diff_between(self.tree, expected)
            if incremental.empty:
                raise RuntimeError("model returned the already-applied cumulative patch")
            apply_unified_diff(self.tree, incremental.canonical_diff)
            return True
        except RuntimeError:
            return False
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def apply_patch(self, patch: str) -> dict[str, Any]:
        patch = str(patch)
        if not patch.strip():
            raise ValueError("patch is empty")
        backup_root = Path(tempfile.mkdtemp(prefix="reachpatch-edit-backup-", dir=self.state.run_root))
        backup = backup_root / "tree"
        copy_source_tree(self.tree, backup)
        before = diff_between(self.state.clean_snapshot, self.tree)
        try:
            normalized_cumulative = self._apply_cumulative_as_incremental(patch)
            if not normalized_cumulative:
                self._apply_incremental(patch)
            after = diff_between(self.state.clean_snapshot, self.tree)
            if after.patch_hash == before.patch_hash:
                raise RuntimeError("patch made no executable tree change")
        except Exception:
            shutil.rmtree(self.tree)
            copy_source_tree(backup, self.tree)
            raise
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)
        self._validation_results.clear()
        self._read_intervals.clear()
        return {
            "before_patch_hash": before.patch_hash,
            "after_patch_hash": after.patch_hash,
            "changed_files": after.changed_files,
            "normalized_cumulative_response": normalized_cumulative,
            "validation_status": self.validation_status(),
        }

    def run_allowed_public_check(self, command: list[str] | tuple[str, ...]) -> dict[str, Any]:
        normalized = tuple(str(item) for item in command)
        matching = tuple(item for item in self._checks if item.command == normalized)
        if not matching:
            raise ValueError("command is not grounded in the RepairObjective")
        outcomes = []
        for check in matching:
            execution = execute_check(
                self.tree, check, stability_runs=2,
                base_tree=self.state.clean_snapshot,
            )
            self._validation_results[check.check_id] = execution
            outcomes.append({
                "check_id": check.check_id,
                "role": check.role,
                "authority": check.authority,
                "status": execution.status,
                "stable": execution.stable,
                "semantic_signature": execution.semantic_signature,
                "observation": execution.observation.to_dict(),
                "entered_project_code": execution.entered_project_code,
            })
        return {
            "command": normalized,
            "outcomes": tuple(outcomes),
            "validation_status": self.validation_status(),
        }

    def validation_status(self) -> dict[str, Any]:
        pending_checks = tuple(
            item for item in self._checks
            if item.check_id not in self._validation_results
        )
        pending_commands = tuple(dict.fromkeys(item.command for item in pending_checks))
        results = tuple(self._validation_results.values())
        unknown_ids = tuple(
            item.check_id for item in results if item.status is CheckStatus.UNKNOWN
        )
        blocked_ids = tuple(
            item.check_id for item in results
            if item.status in {CheckStatus.BLOCKED, CheckStatus.UNSUPPORTED}
        )
        return {
            "required_count": len(self._checks),
            "pending_count": len(pending_checks),
            "pending_commands": pending_commands,
            "pending_ids": tuple(item.check_id for item in pending_checks),
            "failed_validation_ids": tuple(
                item.check_id for item in results if item.status is CheckStatus.FAIL
            ),
            "unknown_validation_ids": unknown_ids,
            "blocked_validation_ids": blocked_ids,
            "satisfied_validation_ids": tuple(
                item.check_id for item in results if item.status is CheckStatus.PASS
            ),
            "outcomes": tuple(item.to_dict() for item in results),
            # A stable FAIL is a valid trial observation. Transition logic,
            # rather than the model tool, decides whether it made progress.
            "ready": not pending_checks and not unknown_ids and not blocked_ids,
        }

    def validation_summary(self) -> dict[str, Any]:
        return self.validation_status()

    def finish_revision(self, summary: str, mechanism: str = "causal_edit") -> dict[str, Any]:
        incremental = diff_between(Path(self.state.working_checkpoint.snapshot_tree), self.tree)
        cumulative = diff_between(self.state.clean_snapshot, self.tree)
        if incremental.empty:
            raise RuntimeError("finish_revision requires a real incremental edit")
        if cumulative.empty:
            raise RuntimeError("finish_revision requires a non-empty cumulative patch")
        if self.validation_status()["pending_count"]:
            raise RuntimeError("finish_revision requires all grounded checks to execute")
        validation_root = Path(tempfile.mkdtemp(prefix="reachpatch-validate-", dir=self.state.run_root))
        validation_tree = validation_root / "tree"
        try:
            copy_source_tree(self.state.clean_snapshot, validation_tree)
            apply_unified_diff(validation_tree, cumulative.canonical_diff)
            verified = diff_between(self.state.clean_snapshot, validation_tree)
            if verified.patch_hash != cumulative.patch_hash:
                raise RuntimeError("cumulative diff does not reconstruct the staging tree")
        finally:
            shutil.rmtree(validation_root, ignore_errors=True)
        self.finished = True
        self.finish_summary = str(summary)
        return {
            "finished": True,
            "summary": str(summary),
            "mechanism": str(mechanism),
            "incremental_patch_hash": incremental.patch_hash,
            "cumulative_patch_hash": cumulative.patch_hash,
            "validation_status": self.validation_status(),
        }

    def cumulative_patch_rejected(self, patch_hash: str | None = None) -> bool:
        value = patch_hash or diff_between(self.state.clean_snapshot, self.tree).patch_hash
        return value in self.state.rejected_patch_hashes

    def attempt_summary(self) -> dict[str, Any]:
        return {
            "tool_calls": tuple(self._tool_events),
            "validation": self.validation_status(),
        }

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        functions = {
            "read_file": self.read_file,
            "search_symbol": self.search_symbol,
            "inspect_trace": self.inspect_trace,
            "inspect_callers": self.inspect_callers,
            "inspect_diff": self.inspect_diff,
            "inspect_incremental_diff": self.inspect_incremental_diff,
            "apply_patch": self.apply_patch,
            "run_allowed_public_check": self.run_allowed_public_check,
            "finish_revision": self.finish_revision,
        }
        if name not in functions:
            raise ValueError(f"unknown repair tool: {name}")
        try:
            result = functions[name](**dict(arguments))
        except Exception as exc:
            self._record(name, dict(arguments), error=exc)
            raise
        self._record(name, dict(arguments), result=result)
        return result


TOOL_SCHEMAS = (
    {"type": "function", "function": {"name": "read_file", "description": "Read one project source interval.", "parameters": {"type": "object", "additionalProperties": False, "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "search_symbol", "parameters": {"type": "object", "additionalProperties": False, "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "inspect_trace", "parameters": {"type": "object", "additionalProperties": False, "properties": {}}}},
    {"type": "function", "function": {"name": "inspect_callers", "parameters": {"type": "object", "additionalProperties": False, "properties": {"symbol": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "inspect_diff", "parameters": {"type": "object", "additionalProperties": False, "properties": {}}}},
    {"type": "function", "function": {"name": "inspect_incremental_diff", "parameters": {"type": "object", "additionalProperties": False, "properties": {}}}},
    {"type": "function", "function": {"name": "apply_patch", "description": "Apply one real source edit. patch must be a complete git unified diff (diff --git plus ---/+++ and @@ hunks) or a complete structured patch beginning with *** Begin Patch and ending with *** End Patch. Use exact current-source context; do not send prose.", "parameters": {"type": "object", "additionalProperties": False, "properties": {"patch": {"type": "string", "description": "Complete git unified diff or structured *** Begin Patch action; never prose or a partial hunk."}}, "required": ["patch"]}}},
    {"type": "function", "function": {"name": "run_allowed_public_check", "parameters": {"type": "object", "additionalProperties": False, "properties": {"command": {"type": "array", "items": {"type": "string"}}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "finish_revision", "parameters": {"type": "object", "additionalProperties": False, "properties": {"summary": {"type": "string"}, "mechanism": {"type": "string"}}, "required": ["summary"]}}},
)
