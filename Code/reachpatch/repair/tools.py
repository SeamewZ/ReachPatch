from __future__ import annotations

import ast
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from reachpatch.execution import execute_transition_triplet
from reachpatch.execution.worktree import (
    apply_patch_action, apply_unified_diff, diff_between,
)
from reachpatch.execution.trace import run_trace
from reachpatch.models.base import stable_id
from reachpatch.models.evidence import ObservationContract
from reachpatch.models.graphs import InputRecipe, ProgramEdgeKind
from reachpatch.models.reach_avoid import (
    ProbeRegistration, ReachAvoidState, RepairObjective,
)
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
        self.allowed_commands = {tuple(item.command) for item in objective.validation_obligations}
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
    def _stable_observation(value: Any) -> dict[str, Any]:
        rendered = RepairToolExecutor._observation_dict(value)
        result = {
            key: rendered[key] for key in
            ("status", "return_code", "exit_code", "stdout", "stderr", "exception", "value")
            if key in rendered
        }
        if "exit_code" in result and "return_code" not in result:
            result["return_code"] = result["exit_code"]
        return result

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
            == normalized(key, current.get("return_code") if key == "exit_code" else current.get(key))
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
                expected = self._stable_observation(packet.baseline_observation)
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
            expected = self._stable_observation(evidence.get("actual"))
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
        for obligation in self.objective.validation_obligations:
            if obligation.authority not in {"A", "B", "C"}:
                continue
            if obligation.role != "MECHANICAL" and obligation.expected_observation is None:
                # A relation without a structured expected payload is an
                # evidence gap, not an executable validation obligation.
                # Keep it in Reach-Avoid state for recovery, but do not make
                # the edit agent finish against an obligation that can only
                # produce UNKNOWN.
                continue
            command = tuple(map(str, obligation.command))
            if command not in self.allowed_commands:
                self.allowed_commands.add(command)
            expected = self._stable_observation(obligation.expected_observation)
            specs.setdefault(command, []).append({
                "validation_id": obligation.validation_id,
                "evidence_kind": obligation.role,
                "evidence_id": obligation.validation_id,
                "requirement_id": obligation.requirement_id,
                "oracle_id": obligation.oracle_id,
                "oracle_authority": obligation.authority,
                "expected_relation": obligation.expected_relation,
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
        required_count = sum(len(items) for items in specs.values())
        return {
            "required_count": required_count,
            "pending_count": sum(len(specs[item]) for item in pending_commands),
            "pending_commands": pending_commands,
            "failed_validation_ids": failed_ids,
            "unknown_validation_ids": unknown_ids,
            "satisfied_validation_ids": satisfied_ids,
            "outcomes": outcomes,
            "ready": bool(required_count) and not pending_commands and not failed_ids and not unknown_ids,
        }

    def validation_summary(self) -> dict[str, Any]:
        status = self.validation_status()
        return {
            key: value for key, value in status.items()
            if key not in {"pending_commands", "outcomes"}
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

    def inspect_frontier(self, frontier_id: str) -> dict[str, Any]:
        frontier = self.state.repair_frontiers.get(frontier_id)
        if frontier is None and self.objective.selected_frontier is not None:
            if self.objective.selected_frontier.frontier_id == frontier_id:
                frontier = self.objective.selected_frontier
        if frontier is None:
            raise KeyError(frontier_id)
        return frontier.to_dict()

    def inspect_requirement(self, requirement_id: str) -> dict[str, Any]:
        leaf = self.state.graph_stack.requirement_graph.leaves.get(requirement_id)
        if leaf is None:
            raise KeyError(requirement_id)
        return leaf.to_dict()

    def inspect_binding(self, binding_id: str) -> dict[str, Any]:
        binding = self.state.graph_stack.binding_graph.units.get(binding_id)
        if binding is None:
            raise KeyError(binding_id)
        return binding.to_dict()

    def inspect_program_slice(self, slice_id: str) -> dict[str, Any]:
        graph = self.state.graph_stack.program_graph
        node = graph.nodes.get(slice_id)
        if node is not None:
            return node.to_dict()
        cut = graph.causal_cuts.get(slice_id)
        if cut is not None:
            return cut.to_dict()
        for item in self.objective.editable_source_slices:
            if item.get("path") == slice_id or item.get("slice_id") == slice_id:
                return item
        raise KeyError(slice_id)

    def write_probe(self, name: str, source: str) -> dict[str, Any]:
        """Write an isolated, AST-checked probe; never project source."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("invalid probe name")
        probe_root = self.state.run_root / "probes"
        probe_root.mkdir(parents=True, exist_ok=True)
        path = (probe_root / name).with_suffix(".py")
        tree = ast.parse(source, filename=str(path))
        forbidden = (ast.Import, ast.ImportFrom)
        for node in ast.walk(tree):
            if isinstance(node, forbidden):
                modules = [alias.name.split(".", 1)[0] for alias in node.names]
                if any(module in {"os", "subprocess", "shutil", "socket", "pathlib"} for module in modules):
                    raise ValueError("probe imports a forbidden module")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "open", "__import__"}:
                raise ValueError("probe contains a forbidden call")
        path.write_text(source, encoding="utf-8")
        probe_id = stable_id("probe", name, source)
        metadata = {
            "probe_id": probe_id, "source_path": str(path),
            "path": str(path),
            "name": name, "validated": True,
        }
        (probe_root / f"{probe_id}.json").write_text(
            json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8",
        )
        self._record_tool_event("write_probe", {"name": name}, result=metadata)
        return metadata

    def _probe_metadata(self, probe_id: str) -> dict[str, Any]:
        path = self.state.run_root / "probes" / f"{probe_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown probe_id: {probe_id}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        source_path = Path(str(raw.get("source_path", ""))).resolve()
        probe_root = (self.state.run_root / "probes").resolve()
        if not source_path.is_relative_to(probe_root) or not source_path.is_file():
            raise ValueError("probe source is missing or outside the isolated probe directory")
        return raw

    def register_observation_contract(
        self,
        contract: dict[str, Any],
        *,
        probe_id: str | None = None,
        input_recipe: dict[str, Any] | None = None,
        frontier_key: str | None = None,
        authority: str | None = None,
    ) -> dict[str, Any]:
        """Register a validated probe contract in the live repair state.

        A model-supplied contract is provisional unless it identifies existing
        public A/B/C evidence.  The registration is later merged into the
        trial Binding/Challenge graphs by the transition evaluator.
        """
        if not isinstance(contract, dict):
            raise ValueError("observation contract must be an object")
        if "expected" not in contract:
            raise ValueError("observation contract requires an expected payload")
        observation = ObservationContract(
            relation=str(contract.get("relation", "probe observation")),
            expected=contract["expected"],
            observable=str(contract.get("observable", "stdout")),
            comparator=str(contract.get("comparator", "EQUALS")),
        )
        if observation.normalized_comparator == "RELATION_HOLDS" and (
            str(contract.get("comparator", "EQUALS")).upper()
            not in {"RELATION_HOLDS", "RELATION HOLDS"}
        ):
            raise ValueError("unsupported observation comparator")
        selected = self.objective.selected_frontier
        linked_key = frontier_key or (selected.semantic_key if selected else "")
        if not linked_key:
            raise ValueError("probe must be linked to the selected RepairFrontier")
        recipe_raw = dict(input_recipe or contract.get("input_recipe") or {})
        command = tuple(map(str, recipe_raw.get("command", contract.get("command", ()))))
        recipe = InputRecipe(
            recipe_id=stable_id("probe-input", linked_key, command, recipe_raw.get("concrete_input")),
            kind=str(recipe_raw.get("kind", "PROBE")),
            concrete_input=recipe_raw.get("concrete_input"),
            derivation=tuple(map(str, recipe_raw.get("derivation", ("registered probe",)))),
            command=command,
            environment=tuple(sorted(
                (str(key), str(value))
                for key, value in dict(recipe_raw.get("environment", {})).items()
            )),
            trace_symbols=tuple(map(str, recipe_raw.get("trace_symbols", ()))),
            call_mode="PROBE",
        )
        requested_authority = authority or str(contract.get("authority", "PROVISIONAL"))
        trusted = {
            obligation.authority
            for obligation in self.objective.validation_obligations
            if obligation.authority in {"A", "B", "C"}
            and observation.contract_id == stable_id(
                "objective-contract", obligation.expected_relation, obligation.expected_observation,
            )
        }
        effective_authority = requested_authority if requested_authority in trusted else "PROVISIONAL"
        metadata = self._probe_metadata(probe_id) if probe_id else {
            "probe_id": stable_id("registered-probe", linked_key, recipe, observation),
            "source_path": "",
        }
        requirement_id = str(recipe_raw.get("requirement_id") or next(
            iter(getattr(selected, "requirement_ids", ())), ""
        ))
        if not requirement_id:
            raise ValueError("probe must identify a Requirement")
        binding_id = recipe_raw.get("binding_id") or next(
            iter(getattr(selected, "binding_ids", ())), None
        )
        registration = ProbeRegistration(
            probe_id=str(metadata["probe_id"]), source_path=str(metadata.get("source_path", "")),
            input_recipe=recipe, observation_contract=observation,
            linked_frontier_key=linked_key, authority=effective_authority,
            requirement_id=requirement_id, binding_id=str(binding_id) if binding_id else None,
            path_class_id=(str(recipe_raw["path_class_id"]) if recipe_raw.get("path_class_id") else None),
            cwd=str(recipe_raw.get("cwd", ".")),
            environment=recipe.environment,
            timeout_seconds=float(recipe_raw.get("timeout_seconds", 60.0)),
            backend=str(recipe_raw.get("backend", "shared-executor")),
        )
        self.state.probe_registrations[registration.probe_id] = registration
        destination = self.state.run_root / "probe_registrations.jsonl"
        with destination.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(registration.to_dict(), sort_keys=True, default=str) + "\n")
        self._record_tool_event(
            "register_observation_contract", {"probe_id": registration.probe_id},
            result={"probe_id": registration.probe_id, "authority": effective_authority,
                    "obligation_key": registration.atomic_obligation.key},
        )
        return {
            "probe_id": registration.probe_id, "contract_id": observation.contract_id,
            "authority": effective_authority,
            "obligation_key": registration.atomic_obligation.key,
        }

    def run_probe(
        self, command: tuple[str, ...] | list[str] | None = None, *,
        probe_id: str | None = None, cwd: str = ".",
        timeout_seconds: float = 60.0, tree: str = "triplet",
    ) -> dict[str, Any]:
        """Run a registered probe on baseline, incumbent and trial trees."""
        normalized = tuple(map(str, command or ()))
        if probe_id is not None:
            registration = self.state.probe_registrations.get(probe_id)
        else:
            registration = next((item for item in self.state.probe_registrations.values()
                                 if not normalized or item.input_recipe.command == normalized), None)
        if registration is None:
            raise KeyError("register an observation contract before running a probe")
        if normalized and normalized != registration.input_recipe.command:
            raise ValueError("probe command differs from its registered InputRecipe")
        if tree not in {"triplet", "baseline", "incumbent", "trial", "working"}:
            raise ValueError("probe tree must identify baseline, incumbent, trial, or triplet")
        obligation = registration.atomic_obligation
        bundle = execute_transition_triplet(
            self.state.base_repository, Path(self.state.working_checkpoint.snapshot_tree),
            self.tree, (obligation,), {
                "stability_runs": 2, "backend": registration.backend,
                "timeout_seconds": registration.timeout_seconds,
                "cwd": registration.cwd,
                "environment": dict(registration.environment),
            },
        )
        updated = ProbeRegistration(
            probe_id=registration.probe_id, source_path=registration.source_path,
            input_recipe=registration.input_recipe,
            observation_contract=registration.observation_contract,
            linked_frontier_key=registration.linked_frontier_key, authority=registration.authority,
            requirement_id=registration.requirement_id, binding_id=registration.binding_id,
            path_class_id=registration.path_class_id, cwd=registration.cwd,
            environment=registration.environment, timeout_seconds=registration.timeout_seconds,
            backend=registration.backend, execution_results={
                "baseline": bundle.baseline[obligation.key],
                "incumbent": bundle.incumbent[obligation.key],
                "trial": bundle.trial[obligation.key],
            },
        )
        self.state.probe_registrations[updated.probe_id] = updated
        self.state.atomic_obligations[obligation.key] = obligation
        self.state.atomic_evidence[obligation.key] = bundle.trial[obligation.key]
        result = {
            "probe_id": updated.probe_id,
            "obligation_key": obligation.key,
            "baseline": bundle.baseline[obligation.key].to_dict(),
            "incumbent": bundle.incumbent[obligation.key].to_dict(),
            "trial": bundle.trial[obligation.key].to_dict(),
        }
        self._record_tool_event("run_probe", {"probe_id": updated.probe_id}, result=result)
        return result

    def run_validation(self, validation_id: str) -> dict[str, Any]:
        obligation = next((item for item in self.objective.validation_obligations if item.validation_id == validation_id), None)
        if obligation is None:
            # validation_summary exposes stable command keys so the model can
            # execute a pending obligation without depending on an internal
            # per-obligation id. Accept those keys here as well; previously a
            # perfectly valid pending command caused a KeyError and trapped
            # revision generation in a read/retry loop.
            obligation = next(
                (
                    item for item in self.objective.validation_obligations
                    if stable_id("repair-validation-command", tuple(item.command))
                    == validation_id
                ),
                None,
            )
        if obligation is None:
            raise KeyError(validation_id)
        return self.run_allowed_public_check(obligation.command)

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
            if before.canonical_diff.strip() and not after.canonical_diff.strip():
                # A revision is an edit to the incumbent working patch, never
                # a reset to the baseline. Allowing a model to erase the
                # incumbent here silently converts a real revision attempt
                # into an empty incremental diff.
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
        spec = next((
            item for item in self.objective.validation_obligations
            if tuple(item.command) == normalized
        ), None)
        cwd = spec.cwd if spec is not None else "."
        patch_hash = diff_between(self.state.base_repository, self.tree).patch_hash
        env_values = dict(spec.environment) if spec is not None else {}
        env_values["PYTHONPYCACHEPREFIX"] = str(
            self.state.run_root / "repair_bytecode_cache" / patch_hash
        )
        environment = tuple(env_values.items())
        timeout = float(spec.timeout_seconds) if spec is not None else 120.0
        trace = run_trace(
            self.tree, normalized, cwd=cwd, environment=environment,
            timeout_seconds=timeout, trace_enabled=True,
        )
        current = {
            "status": trace.observation.status.value,
            "return_code": trace.observation.return_code,
            "stdout": trace.observation.stdout,
            "stderr": trace.observation.stderr,
            "exception": trace.observation.exception,
            "value": trace.observation.value,
            "first_project_frame": trace.first_project_frame,
            "executed_path_ids": trace.executed_path_ids,
            "backend": "shared-executor",
            "cwd": cwd,
            "environment": environment,
            "timeout_seconds": timeout,
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
        validation_tree = self.state.run_root / "revision_validation_tree"
        if validation_tree.exists():
            shutil.rmtree(validation_tree)
        from reachpatch.execution.worktree import copy_source_tree
        copy_source_tree(self.state.base_repository, validation_tree)
        try:
            apply_unified_diff(validation_tree, cumulative.canonical_diff)
        except RuntimeError as exc:
            shutil.rmtree(validation_tree, ignore_errors=True)
            raise RuntimeError("finish_revision rejects a cumulative patch that cannot apply: " + str(exc)) from exc
        shutil.rmtree(validation_tree, ignore_errors=True)
        validation = self.validation_status()
        if validation["pending_count"]:
            raise RuntimeError(
                "finish_revision requires the graph-grounded reproduction commands "
                "for every open preservation counterexample and protected target"
            )
        unknown_validations = tuple(
            item for item in validation.get("outcomes", ())
            if item.get("outcome") == "UNKNOWN"
        )
        if unknown_validations:
            raise RuntimeError(
                "finish_revision requires rerunning UNKNOWN validations: "
                + ", ".join(str(item.get("validation_id")) for item in unknown_validations)
            )
        failed_validations = tuple(
            item for item in validation.get("outcomes", ())
            if item.get("outcome") == "FAILED"
        )
        selected_kind = getattr(
            getattr(self.objective, "selected_frontier", None), "kind", None,
        )
        selected_kind = getattr(selected_kind, "value", selected_kind)
        deferred_preservation_only = (
            selected_kind == "BEHAVIOR_FAILURE"
            or (
                selected_kind is None
                and self.objective.objective_kind in {"CONFIRMED_FAILURE", "BEHAVIOR_FAILURE"}
            )
        ) and bool(failed_validations) and all(
            item.get("evidence_kind") in {"PRESERVATION", "IMPACT"}
            for item in failed_validations
        ) and any(
            item.get("outcome") == "SATISFIED"
            and item.get("evidence_kind") == "TARGET"
            for item in validation.get("outcomes", ())
        )
        # A failed target is a valid end to this editing turn.  Reach-Avoid
        # will classify the resulting trial and decide whether to continue;
        # the tool must not claim that the edit is validated.
        self.finished = True
        self.finish_summary = summary
        return {
            "finished": True,
            "summary": summary,
            "mechanism": mechanism,
            "incremental_patch_hash": current.patch_hash,
            "changed_files": current.changed_files,
            "validation_status": self.validation_summary(),
            "evidence_limited": not bool(validation.get("required_count")),
            "deferred_preservation_validation_ids": tuple(
                str(item["validation_id"]) for item in failed_validations
            ) if deferred_preservation_only else (),
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
            "inspect_frontier": self.inspect_frontier,
            "inspect_requirement": self.inspect_requirement,
            "inspect_binding": self.inspect_binding,
            "inspect_program_slice": self.inspect_program_slice,
            "inspect_diff": self.inspect_diff,
            "inspect_incremental_diff": self.inspect_incremental_diff,
            "apply_patch": self.apply_patch,
            "run_allowed_public_check": self.run_allowed_public_check,
            "write_probe": self.write_probe,
            "run_probe": self.run_probe,
            "register_observation_contract": self.register_observation_contract,
            "run_validation": self.run_validation,
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
    {"type": "function", "function": {"name": "inspect_frontier", "parameters": {"type": "object", "properties": {"frontier_id": {"type": "string"}}, "required": ["frontier_id"]}}},
    {"type": "function", "function": {"name": "inspect_requirement", "parameters": {"type": "object", "properties": {"requirement_id": {"type": "string"}}, "required": ["requirement_id"]}}},
    {"type": "function", "function": {"name": "inspect_binding", "parameters": {"type": "object", "properties": {"binding_id": {"type": "string"}}, "required": ["binding_id"]}}},
    {"type": "function", "function": {"name": "inspect_program_slice", "parameters": {"type": "object", "properties": {"slice_id": {"type": "string"}}, "required": ["slice_id"]}}},
    {"type": "function", "function": {"name": "inspect_diff", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "inspect_incremental_diff", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "apply_patch", "parameters": {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"]}}},
    {"type": "function", "function": {"name": "run_allowed_public_check", "parameters": {"type": "object", "properties": {"command": {"type": "array", "items": {"type": "string"}}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "write_probe", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "source": {"type": "string"}}, "required": ["name", "source"]}}},
    {"type": "function", "function": {"name": "run_probe", "parameters": {"type": "object", "properties": {"probe_id": {"type": "string"}, "command": {"type": "array", "items": {"type": "string"}}, "cwd": {"type": "string"}, "timeout_seconds": {"type": "number"}, "tree": {"type": "string"}}, "required": ["probe_id"]}}},
    {"type": "function", "function": {"name": "register_observation_contract", "parameters": {"type": "object", "properties": {"contract": {"type": "object"}, "probe_id": {"type": "string"}, "input_recipe": {"type": "object"}, "frontier_key": {"type": "string"}, "authority": {"type": "string"}}, "required": ["contract"]}}},
    {"type": "function", "function": {"name": "run_validation", "parameters": {"type": "object", "properties": {"validation_id": {"type": "string"}}, "required": ["validation_id"]}}},
    {"type": "function", "function": {"name": "finish_revision", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}, "mechanism": {"type": "string"}}, "required": ["summary"]}}},
)
