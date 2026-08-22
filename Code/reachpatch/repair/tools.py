from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from reachpatch.execution.worktree import (
    apply_patch_action, apply_unified_diff, diff_between,
)
from reachpatch.models.base import stable_id
from reachpatch.models.graphs import ProgramEdgeKind
from reachpatch.models.reach_avoid import ReachAvoidState, RepairObjective
from reachpatch.program_graph import RepositoryIndex


class RepairToolExecutor:
    def __init__(
        self,
        tree: Path,
        state: ReachAvoidState,
        objective: RepairObjective,
    ) -> None:
        self.tree = tree.resolve()
        self.state = state
        self.objective = objective
        self.finished = False
        self.finish_summary = ""
        self.allowed_commands = set(objective.reproduction_commands)
        self._indexed_source_nodes: dict[str, dict[str, Any]] = {}
        self._read_intervals: dict[str, list[tuple[int, int]]] = {}
        self._repository_index: RepositoryIndex | None = None
        self._validation_results: dict[tuple[str, ...], tuple[dict[str, Any], ...]] = {}
        self._tool_events: list[dict[str, Any]] = []

    @staticmethod
    def _observation_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "to_dict"):
            rendered = value.to_dict()
            return dict(rendered) if isinstance(rendered, dict) else {}
        return {}

    @staticmethod
    def _observations_match(expected: Any, current: dict[str, Any]) -> bool | None:
        expected_value = RepairToolExecutor._observation_dict(expected)
        comparable = tuple(
            key for key in ("return_code", "stdout", "stderr", "exception", "value")
            if key in expected_value
        )
        if not comparable:
            return None

        def normalized(key: str, value: Any) -> Any:
            if key in {"stdout", "stderr"} and isinstance(value, str):
                return value.rstrip()
            return value

        return all(
            normalized(key, expected_value.get(key))
            == normalized(key, current.get(key))
            for key in comparable
        )

    def _validation_specs(self) -> dict[tuple[str, ...], tuple[dict[str, Any], ...]]:
        specs: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        primary_is_preservation = bool(
            self.objective.primary_requirement.get("preservation")
            or self.objective.objective_kind == "PRESERVATION_REGRESSION"
        )
        if primary_is_preservation:
            for packet in self.objective.counterexamples:
                if packet.oracle_authority not in {"A", "B", "C"}:
                    continue
                expected = self._observation_dict(packet.baseline_observation)
                if self._observations_match(expected, expected) is None:
                    continue
                command = tuple(map(str, packet.reproduction_command))
                specs.setdefault(command, []).append({
                    "validation_id": stable_id(
                        "repair-validation", "counterexample",
                        packet.counterexample_id, command,
                    ),
                    "evidence_kind": "COUNTEREXAMPLE",
                    "evidence_id": packet.counterexample_id,
                    "requirement_id": packet.requirement_id,
                    "oracle_id": packet.oracle_id,
                    "oracle_authority": packet.oracle_authority,
                    "expected_relation": packet.expected_relation,
                    "expected_observation": expected,
                })
        for evidence in self.objective.observations:
            if evidence.get("evidence_kind") != "PROTECTED_TARGET_EXECUTION":
                continue
            challenge_id = str(evidence.get("challenge_id", ""))
            cell = self.state.graph_stack.challenge_graph.cells.get(challenge_id)
            if cell is None or cell.patch_hash != self.state.graph_stack.patch_hash:
                continue
            expected = self._observation_dict(evidence.get("actual"))
            if self._observations_match(expected, expected) is None:
                continue
            command = tuple(map(str, cell.execution_scenario.command))
            specs.setdefault(command, []).append({
                "validation_id": stable_id(
                    "repair-validation", "protected-target", challenge_id, command,
                ),
                "evidence_kind": "PROTECTED_TARGET_EXECUTION",
                "evidence_id": challenge_id,
                "requirement_id": str(evidence.get("requirement_id", "")),
                "oracle_id": str(evidence.get("oracle_id", "")),
                "oracle_authority": str(evidence.get("oracle_authority", "")),
                "expected_relation": str(evidence.get("expected", "")),
                "expected_observation": expected,
            })
        return {
            command: tuple(values)
            for command, values in specs.items()
            if command in self.allowed_commands
        }

    def validation_status(self) -> dict[str, Any]:
        specs = self._validation_specs()
        pending_commands = tuple(
            command for command in specs if command not in self._validation_results
        )
        outcomes = tuple(
            outcome
            for command in specs
            for outcome in self._validation_results.get(command, ())
        )
        failed_ids = tuple(
            str(item["validation_id"])
            for item in outcomes if item.get("outcome") == "FAILED"
        )
        unknown_ids = tuple(
            str(item["validation_id"])
            for item in outcomes if item.get("outcome") == "UNKNOWN"
        )
        satisfied_ids = tuple(
            str(item["validation_id"])
            for item in outcomes if item.get("outcome") == "SATISFIED"
        )
        return {
            "required_count": sum(len(items) for items in specs.values()),
            "pending_count": sum(len(specs[item]) for item in pending_commands),
            "pending_commands": pending_commands,
            "failed_validation_ids": failed_ids,
            "unknown_validation_ids": unknown_ids,
            "satisfied_validation_ids": satisfied_ids,
            "ready": not pending_commands and not failed_ids,
        }

    def validation_summary(self) -> dict[str, Any]:
        status = self.validation_status()
        return {
            key: value for key, value in status.items()
            if key != "pending_commands"
        } | {
            "pending_command_ids": tuple(
                stable_id("repair-validation-command", command)
                for command in status["pending_commands"]
            ),
        }

    def attempt_summary(self) -> dict[str, Any]:
        """Return bounded facts that the next generator revision can consume."""

        return {
            "tool_calls": tuple(self._tool_events[-24:]),
            "validation": self.validation_summary(),
        }

    def _record_tool_event(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        path = self.state.run_root / "repair_tool_events.jsonl"
        safe_arguments = {
            key: (
                stable_id("tool-patch", value)
                if name == "apply_patch" and key == "patch"
                else value
            )
            for key, value in arguments.items()
        }

        def summarize(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: summarize(item)
                    for key, item in value.items()
                    if key not in {"content", "stdout", "stderr", "source_context"}
                }
            if isinstance(value, (list, tuple)):
                return [summarize(item) for item in value[:20]]
            if isinstance(value, str) and len(value) > 1000:
                return value[:1000] + "..."
            return value

        summary = summarize(result)
        self._tool_events.append({
            "tool": name,
            "arguments": safe_arguments,
            "result": summary,
            "error_kind": type(error).__name__ if error else None,
            "error": str(error)[-1000:] if error else None,
        })
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "time_ns": time.time_ns(),
                "objective_id": self.objective.objective_id,
                "objective_kind": self.objective.objective_kind,
                "tool": name,
                "arguments": safe_arguments,
                "result": summary,
                "error_kind": type(error).__name__ if error else None,
                "error": str(error)[-1000:] if error else None,
            }, sort_keys=True, default=str) + "\n")

    def _path(self, relative: str) -> Path:
        path = (self.tree / relative).resolve()
        if not path.is_relative_to(self.tree):
            raise ValueError("path escapes working tree")
        return path

    def read_file(self, path: str, start_line: int = 1, end_line: int = 240) -> dict[str, Any]:
        source = self._path(path)
        if not source.is_file():
            raise FileNotFoundError(path)
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        line_count = len(lines)
        requested_start = max(1, start_line)
        if requested_start > line_count:
            return {
                "path": path,
                "start_line": requested_start,
                "end_line": line_count,
                "line_count": line_count,
                "eof": True,
                "next_start_line": None,
                "redundant": True,
                "content": "",
                "guidance": "The requested start is beyond EOF; inspect or edit source instead.",
            }
        start = requested_start
        end = min(line_count, max(start, end_line))
        intervals = self._read_intervals.setdefault(path, [])
        redundant = any(
            previous_start <= start and previous_end >= end
            for previous_start, previous_end in intervals
        )
        if redundant:
            return {
                "path": path,
                "start_line": start,
                "end_line": end,
                "line_count": line_count,
                "eof": end >= line_count,
                "next_start_line": None if end >= line_count else end + 1,
                "redundant": True,
                "content": "",
                "guidance": "This interval is already in the model context; do not read it again.",
            }
        intervals.append((start, end))
        return {
            "path": path,
            "start_line": start,
            "end_line": end,
            "line_count": line_count,
            "eof": end >= line_count,
            "next_start_line": None if end >= line_count else end + 1,
            "redundant": False,
            "content": "\n".join(
                f"{number}: {lines[number - 1]}" for number in range(start, end + 1)
            ),
        }

    def search_symbol(self, symbol: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", symbol):
            raise ValueError("symbol must be a Python identifier or dotted symbol")
        lookup = symbol.split(".")[-1]
        qualifier = symbol.rsplit(".", 1)[0] if "." in symbol else ""
        module_qualified = bool(qualifier and qualifier.split(".")[-1][:1].islower())
        matches = []
        for node in self.state.graph_stack.program_graph.nodes.values():
            if node.symbol.split(".")[-1] == lookup and (
                "." not in symbol or node.symbol.endswith(symbol)
            ):
                matches.append(node.to_dict())
        if not any(bool(match.get("editable")) for match in matches):
            if self._repository_index is None:
                self._repository_index = RepositoryIndex.build(
                    self.tree, self.state.base_commit, (lookup,), max_files=40,
                )
            index = self._repository_index
            index.expand_symbol(lookup)
            previously_read = tuple(
                relative for relative in self._read_intervals
                if (self.tree / relative).is_file()
            )
            candidate_paths = tuple(dict.fromkeys(
                previously_read + index.symbol_files.get(lookup, ())
            ))
            for relative in candidate_paths:
                path = self.tree / relative
                if not path.is_file():
                    continue
                source = path.read_text(encoding="utf-8", errors="replace")
                try:
                    parsed = ast.parse(source, filename=relative)
                except SyntaxError:
                    continue
                lines = source.splitlines()

                def visit(body: list[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
                    for item in body:
                        if not isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                            continue
                        qualified = ".".join((*prefix, item.name))
                        if (
                            qualified == symbol
                            or qualified.endswith(f".{symbol}")
                            or (module_qualified and qualified == lookup)
                        ):
                            line_number = item.lineno
                            node_id = stable_id(
                                "indexed-source-symbol", relative, qualified, line_number,
                            )
                            start = max(1, line_number - 8)
                            end_line = getattr(item, "end_lineno", line_number + 32)
                            end = min(
                                len(lines),
                                max(line_number + 8, min(end_line, line_number + 40)),
                            )
                            parts = Path(relative).parts
                            value = {
                                "node_id": node_id,
                                "kind": "INDEXED_SOURCE",
                                "path": relative,
                                "symbol": qualified,
                                "start_line": line_number,
                                "end_line": end,
                                "editable": not any(
                                    part in {"test", "tests", "generated", "artifacts"}
                                    for part in parts
                                ),
                                "source_context": "\n".join(
                                    f"{number}: {lines[number - 1]}"
                                    for number in range(start, end + 1)
                                ),
                            }
                            self._indexed_source_nodes[node_id] = value
                            matches.append(value)
                        visit(item.body, (*prefix, item.name))

                visit(parsed.body)
                if len(matches) >= 12:
                    break
        return {"symbol": symbol, "matches": matches[:12]}

    def inspect_callers(self, symbol_id: str) -> dict[str, Any]:
        graph = self.state.graph_stack.program_graph
        callers = []
        for edge in graph.edges.values():
            if edge.target_id == symbol_id and edge.kind in {
                ProgramEdgeKind.CALLS, ProgramEdgeKind.MAY_CALL,
                ProgramEdgeKind.EXECUTED_CALL,
            }:
                callers.append({
                    "edge": edge.to_dict(),
                    "caller": graph.nodes[edge.source_id].to_dict()
                    if edge.source_id in graph.nodes else None,
                })
        indexed = self._indexed_source_nodes.get(symbol_id)
        if not callers and indexed is not None:
            symbol = str(indexed["symbol"])
            index = RepositoryIndex.build(
                self.tree, self.state.base_commit, (symbol,), max_files=40,
            )
            pattern = re.compile(rf"\b{re.escape(symbol)}\s*\(")
            for relative in index.symbol_files.get(symbol, ()):
                path = self.tree / relative
                if not path.is_file():
                    continue
                lines = path.read_text(
                    encoding="utf-8", errors="replace",
                ).splitlines()
                for number, line in enumerate(lines, 1):
                    if not pattern.search(line):
                        continue
                    callers.append({
                        "edge": {
                            "kind": "INDEXED_CALL_REFERENCE",
                            "target_id": symbol_id,
                        },
                        "caller": {
                            "path": relative,
                            "start_line": number,
                            "end_line": number,
                            "symbol": line.strip(),
                        },
                    })
                    if len(callers) >= 40:
                        break
                if len(callers) >= 40:
                    break
        return {"symbol_id": symbol_id, "callers": callers[:40]}

    def inspect_trace(self, trace_bundle_id: str) -> dict[str, Any]:
        for execution in self.state.observations.by_challenge.values():
            if trace_bundle_id in {
                execution.paired_bundle_id,
                execution.baseline.trace_bundle_id,
                execution.patched.trace_bundle_id,
            }:
                return execution.to_dict()
        for packet in self.objective.counterexamples:
            if trace_bundle_id in packet.executed_path_ids:
                return {
                    "counterexample_id": packet.counterexample_id,
                    "executed_path_ids": packet.executed_path_ids,
                    "baseline_observation": packet.baseline_observation,
                    "patched_observation": packet.patched_observation,
                    "first_divergence": packet.first_divergence,
                }
        raise KeyError(trace_bundle_id)

    def inspect_diff(self) -> dict[str, Any]:
        return diff_between(self.state.base_repository, self.tree).to_dict()

    def inspect_incremental_diff(self) -> dict[str, Any]:
        return diff_between(
            Path(self.state.working_checkpoint.snapshot_tree), self.tree,
        ).to_dict()

    def _failed_patch_context(self, patch: str) -> str:
        contexts: list[str] = []
        current_path: str | None = None
        structured_anchor: str | None = None
        structured_update = False
        for line in patch.splitlines():
            if line.startswith("*** Update File: "):
                current_path = line.removeprefix("*** Update File: ").strip()
                structured_anchor = None
                structured_update = True
                continue
            if line.startswith("diff --git a/"):
                match = re.match(r"diff --git a/(.*?) b/(.*)$", line)
                current_path = match.group(2) if match else None
                structured_anchor = None
                structured_update = False
                continue
            hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk and current_path:
                try:
                    source = self._path(current_path)
                    lines = source.read_text(
                        encoding="utf-8", errors="replace",
                    ).splitlines()
                except (OSError, ValueError):
                    continue
                center = min(max(1, int(hunk.group(1))), max(1, len(lines)))
                start = max(1, center - 12)
                end = min(len(lines), center + 24)
                numbered = "\n".join(
                    f"{number}: {lines[number - 1]}"
                    for number in range(start, end + 1)
                )
                contexts.append(
                    f"Current source for {current_path} near requested line {center}:\n"
                    f"{numbered}"
                )
                if len(contexts) >= 2:
                    break
                continue
            if (
                not structured_update
                or not current_path
                or not line.startswith((" ", "-"))
            ):
                continue
            candidate = line[1:]
            if not candidate.strip() or candidate.startswith(("---", "***")):
                continue
            if structured_anchor is not None:
                continue
            structured_anchor = candidate
            try:
                source = self._path(current_path)
                lines = source.read_text(
                    encoding="utf-8", errors="replace",
                ).splitlines()
            except (OSError, ValueError):
                continue
            try:
                center = lines.index(candidate) + 1
            except ValueError:
                continue
            start = max(1, center - 12)
            end = min(len(lines), center + 24)
            numbered = "\n".join(
                f"{number}: {lines[number - 1]}"
                for number in range(start, end + 1)
            )
            contexts.append(
                f"Current source for {current_path} near requested line {center}:\n"
                f"{numbered}"
            )
            if len(contexts) >= 2:
                break
        return "\n\n".join(contexts)

    def _restore_cumulative_diff(self, expected, current) -> None:
        for relative in set(expected.changed_files) | set(current.changed_files):
            target = self._path(relative)
            source = (self.state.base_repository / relative).resolve()
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            if source.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(os.readlink(source))
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        if expected.canonical_diff.strip():
            apply_unified_diff(self.tree, expected.canonical_diff)
        restored = diff_between(self.state.base_repository, self.tree)
        if restored.patch_hash != expected.patch_hash:
            raise RuntimeError(
                "failed generator patch could not restore its pre-call working tree"
            )

    def apply_patch(self, patch: str) -> dict[str, Any]:
        before = diff_between(self.state.base_repository, self.tree)
        try:
            apply_patch_action(self.tree, patch)
            after = diff_between(self.state.base_repository, self.tree)
            if after.patch_hash == before.patch_hash:
                raise RuntimeError(
                    "generator patch is a no-op against the current working tree"
                )
            if (
                self.objective.objective_kind == "INITIAL_PATCH"
                and before.canonical_diff.strip()
                and not after.canonical_diff.strip()
            ):
                raise RuntimeError(
                    "generator patch removes the complete cumulative diff; submit a "
                    "replacement edit that keeps a non-empty executable patch"
                )
        except RuntimeError as exc:
            current = diff_between(self.state.base_repository, self.tree)
            if current.patch_hash != before.patch_hash:
                self._restore_cumulative_diff(before, current)
            rejected = self.state.run_root / "rejected_generator_patches.jsonl"
            with rejected.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "time_ns": time.time_ns(),
                    "objective_id": self.objective.objective_id,
                    "patch_id": stable_id("rejected-generator-patch", patch),
                    "patch": patch[:12000],
                    "error": str(exc)[:4000],
                }, sort_keys=True) + "\n")
            context = self._failed_patch_context(patch)
            detail = str(exc)
            if context:
                detail += (
                    "\nPatch was rejected without changing the tree. Rebuild the hunk "
                    "against this current source:\n" + context
                )
            self._read_intervals.clear()
            raise RuntimeError(detail) from exc
        self._read_intervals.clear()
        self._validation_results.clear()
        return {
            "before_patch_hash": before.patch_hash,
            "after_patch_hash": after.patch_hash,
            "changed_files": after.changed_files,
            "validation_status": self.validation_summary(),
        }

    def run_allowed_public_check(self, command: list[str] | tuple[str, ...]) -> dict[str, Any]:
        normalized = tuple(map(str, command))
        if normalized not in self.allowed_commands:
            raise ValueError("command is not grounded in the RepairObjective")
        try:
            patch_hash = diff_between(self.state.base_repository, self.tree).patch_hash
            environment = os.environ.copy()
            environment["PYTHONPYCACHEPREFIX"] = str(
                self.state.run_root / "repair_bytecode_cache" / patch_hash
            )
            result = subprocess.run(
                normalized, cwd=self.tree, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=120, check=False,
                env=environment,
            )
            current = {
                "status": "PASS" if result.returncode == 0 else "FAIL",
                "return_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exception": None,
                "value": None,
            }
        except subprocess.TimeoutExpired as exc:
            current = {
                "status": "UNKNOWN",
                "return_code": None,
                "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
                "exception": "TIMEOUT",
                "value": None,
            }
        validations = []
        for spec in self._validation_specs().get(normalized, ()):
            matched = self._observations_match(
                spec["expected_observation"], current,
            )
            validations.append({
                **spec,
                "current_observation": current,
                "outcome": (
                    "SATISFIED" if matched is True
                    else "FAILED" if matched is False
                    else "UNKNOWN"
                ),
            })
        self._validation_results[normalized] = tuple(validations)
        status = self.validation_status()
        failed = tuple(
            item for item in validations if item.get("outcome") == "FAILED"
        )
        satisfied = tuple(
            item for values in self._validation_results.values()
            for item in values if item.get("outcome") == "SATISFIED"
        )
        return {
            "command": normalized,
            "return_code": current["return_code"],
            "stdout": str(current["stdout"])[-6000:],
            "stderr": str(current["stderr"])[-6000:],
            "grounded_validations": validations,
            "repair_guidance": (
                "Do not finish this revision. Keep every SATISFIED observation "
                "unchanged, repair only the FAILED path identified by causal_guidance, "
                "and submit one combined cumulative edit before rerunning all commands. "
                "Do not revert the currently SATISFIED half of the repair."
                if failed else
                "Retain this observation while completing the remaining required "
                "graph-grounded validations."
            ),
            "satisfied_evidence_ids": tuple(
                str(item["evidence_id"]) for item in satisfied
            ),
            "validation_status": self.validation_summary(),
            "next_required_command": (
                status["pending_commands"][0]
                if status["pending_commands"] else None
            ),
        }

    def finish_revision(self, summary: str, mechanism: str = "causal_edit") -> dict[str, Any]:
        cumulative = diff_between(self.state.base_repository, self.tree)
        if cumulative.empty:
            raise RuntimeError(
                "finish_revision requires a non-empty cumulative patch; add the "
                "replacement causal edit before finishing"
            )
        current = diff_between(self.state.working_checkpoint.snapshot_tree, self.tree)
        if current.empty:
            raise RuntimeError(
                "finish_revision requires a real change from the current working patch"
            )
        if self.cumulative_patch_rejected(cumulative.patch_hash):
            raise RuntimeError(
                "finish_revision rejects a cumulative patch hash that Reach-Avoid "
                "previously rolled back from this working parent; apply a materially "
                "different causal edit before finishing"
            )
        validation = self.validation_status()
        if validation["pending_count"]:
            raise RuntimeError(
                "finish_revision requires the graph-grounded reproduction commands "
                "for every open preservation counterexample and protected target"
            )
        if validation["failed_validation_ids"]:
            raise RuntimeError(
                "finish_revision rejects an edit that still fails graph-grounded "
                "validation: " + ", ".join(validation["failed_validation_ids"])
            )
        self.finished = True
        self.finish_summary = summary
        return {
            "finished": True,
            "summary": summary,
            "mechanism": mechanism,
            "incremental_patch_hash": current.patch_hash,
            "changed_files": current.changed_files,
            "validation_status": self.validation_summary(),
        }

    def cumulative_patch_rejected(self, patch_hash: str | None = None) -> bool:
        value = patch_hash or diff_between(
            self.state.base_repository, self.tree,
        ).patch_hash
        parent_hash = self.state.working_checkpoint.patch_hash
        return any(
            event.get("patch_hash") == parent_hash
            and event.get("rejected_trial_patch_hash") == value
            for event in self.state.generator_session.conversation
        )

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        functions = {
            "read_file": self.read_file,
            "search_symbol": self.search_symbol,
            "inspect_callers": self.inspect_callers,
            "inspect_trace": self.inspect_trace,
            "inspect_diff": self.inspect_diff,
            "apply_patch": self.apply_patch,
            "run_allowed_public_check": self.run_allowed_public_check,
            "finish_revision": self.finish_revision,
        }
        if name not in functions:
            raise ValueError(f"unknown repair tool: {name}")
        try:
            result = functions[name](**arguments)
        except Exception as exc:
            self._record_tool_event(name, arguments, error=exc)
            raise
        self._record_tool_event(name, arguments, result=result)
        return result


TOOL_SCHEMAS = (
    {"type": "function", "function": {"name": "read_file", "description": "Read one source interval. Honor eof and next_start_line in the result; never request an interval marked redundant.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "search_symbol", "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "inspect_callers", "parameters": {"type": "object", "properties": {"symbol_id": {"type": "string"}}, "required": ["symbol_id"]}}},
    {"type": "function", "function": {"name": "inspect_trace", "parameters": {"type": "object", "properties": {"trace_bundle_id": {"type": "string"}}, "required": ["trace_bundle_id"]}}},
    {"type": "function", "function": {"name": "inspect_diff", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "apply_patch", "parameters": {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"]}}},
    {"type": "function", "function": {"name": "run_allowed_public_check", "parameters": {"type": "object", "properties": {"command": {"type": "array", "items": {"type": "string"}}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "finish_revision", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}, "mechanism": {"type": "string"}}, "required": ["summary"]}}},
)
