from __future__ import annotations

from dataclasses import dataclass, field, replace
import ast
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.evidence import (
    ExecutableCheck as LegacyExecutableCheck, ExecutableOracle,
    ObservationContract, OutcomeStatus, PublicEvidence,
    discover_diff_public_checks, issue_witnesses,
)
from reachpatch.models.execution import (
    CheckRole, CheckStatus, ExecutableCheck, GoalContract,
)
from .trace import run_trace
from .checks import (
    execute_check, semantic_observation_signature,
)
from .worktree import diff_between
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    CheckpointState, DynamicReachAvoidGraph, materialize_graph_guided_challenges as _materialize_graph_guided_challenges,
)


@dataclass(frozen=True, slots=True)
class BlockedTargetCandidate(SerializableRecord):
    candidate_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class TargetRecoveryConfig(SerializableRecord):
    max_probes: int = 6
    stability_runs: int = 2
    timeout_seconds: float = 1200.0
    max_agent_turns: int = 24
    provider_request_timeout_seconds: float = 180.0
    check_timeout_seconds: float = 120.0
    candidate_allowance: int = 2


@dataclass(frozen=True, slots=True)
class RejectedTargetCandidate(SerializableRecord):
    candidate_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class TargetRecoveryResult(SerializableRecord):
    target_checks: tuple[ExecutableCheck, ...] = ()
    preservation_checks: tuple[ExecutableCheck, ...] = ()
    rejected_candidates: tuple[RejectedTargetCandidate, ...] = ()
    blocked_candidates: tuple[BlockedTargetCandidate, ...] = ()
    unresolved_goal_ids: tuple[str, ...] = ()
    agent_events: tuple[dict[str, Any], ...] = ()
    timed_out: bool = False
    attempt_count: int = 0
    exhausted_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LegacyTargetRecoveryResult(SerializableRecord):
    scenarios: tuple[Any, ...] = ()
    challenge_cells: tuple[Any, ...] = ()
    gaps: tuple[Any, ...] = ()
    rejected_candidates: tuple[RejectedTargetCandidate, ...] = ()
    timed_out: bool = False
    agent_events: tuple[dict[str, Any], ...] = ()
    target_checks: tuple[LegacyExecutableCheck, ...] = ()
    preservation_checks: tuple[LegacyExecutableCheck, ...] = ()


_TARGET_RECOVERY_TOOL_NAMES = (
    "search_source", "read_source", "write_probe",
    "run_probe_on_clean", "run_probe_on_working",
    "register_observation_contract", "finish_target_recovery",
)


@dataclass(slots=True)
class _RecoveryProbe:
    probe_id: str
    source_path: Path
    source: str
    clean_runs: list[Any] = field(default_factory=list)
    working_runs: list[Any] = field(default_factory=list)
    clean_attempts: int = 0
    working_attempts: int = 0
    contract: ObservationContract | None = None
    requirement_id: str | None = None
    authority: str = "PROVISIONAL"
    input_recipe: dict[str, Any] = field(default_factory=dict)


class TargetRecoveryToolExecutor:
    """Restricted, source-only executor used by target recovery.

    This intentionally does not share the repair-player tool registry: a target
    recovery model can observe and run an isolated probe, but cannot edit the
    project, run arbitrary commands, or inspect hidden/official artifacts.
    """

    def __init__(self, *, repo_root: Path, clean_snapshot: Path,
                 working_snapshot: Path, goal_contracts: Any = None,
                 program_slice: Any = None, run_root: Path,
                 max_probes: int = 6, stability_runs: int = 2,
                 timeout_seconds: float = 120.0, **legacy_kwargs: Any):
        self.repo_root = Path(repo_root).resolve()
        self.clean_snapshot = Path(clean_snapshot).resolve()
        self.working_snapshot = Path(working_snapshot).resolve()
        # ``requirement_graph`` is accepted only for historical callers; the
        # production recovery path passes the flat GoalContract sequence.
        self.goal_contracts = (
            goal_contracts if goal_contracts is not None
            else legacy_kwargs.pop("requirement_graph", ())
        )
        if legacy_kwargs:
            raise TypeError(
                "unsupported target recovery arguments: "
                + ", ".join(sorted(legacy_kwargs))
            )
        self.program_slice = program_slice
        self.run_root = Path(run_root).resolve()
        self.probe_root = self.run_root / "target_recovery_probes"
        self.max_probes = max(1, int(max_probes))
        self.stability_runs = max(2, int(stability_runs))
        self.timeout_seconds = float(timeout_seconds)
        self.probes: dict[str, _RecoveryProbe] = {}
        self.events: list[dict[str, Any]] = []
        self.finished = False
        self.timed_out = False

    @property
    def allowed_tool_names(self) -> tuple[str, ...]:
        return _TARGET_RECOVERY_TOOL_NAMES

    def _record(self, name: str, arguments: dict[str, Any], result: Any = None,
                error: Exception | None = None) -> None:
        self.events.append({
            "time_ns": time.time_ns(), "tool": name,
            "arguments": {key: value for key, value in arguments.items()
                           if key not in {"source", "content"}},
            "result": result if isinstance(result, (str, int, float, bool, type(None), dict, list)) else str(result),
            "error": str(error) if error else None,
        })

    def _safe_repo_path(self, raw: str) -> Path:
        relative = Path(str(raw)).as_posix().lstrip("./")
        path = (self.repo_root / relative).resolve()
        if not path.is_relative_to(self.repo_root):
            raise ValueError("source path escapes repository")
        return path

    def search_source(self, symbol: str) -> dict[str, Any]:
        symbol = str(symbol).strip()
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", symbol):
            raise ValueError("symbol must be a Python identifier or dotted symbol")
        needle = symbol.rsplit(".", 1)[-1]
        matches: list[dict[str, Any]] = []
        graph = getattr(self.program_slice, "graph", self.program_slice)
        for node in getattr(graph, "nodes", {}).values():
            if needle == str(getattr(node, "symbol", "")).rsplit(".", 1)[-1]:
                matches.append(node.to_dict() if hasattr(node, "to_dict") else dict(node))
        if not matches and graph is not None:
            from reachpatch.reach_avoid.dynamic_reach_avoid_graph import expand_dynamic_graph_frontier
            expand_dynamic_graph_frontier(graph, self.repo_root, ())
            matches = [node.to_dict() for node in graph.nodes.values()
                       if needle == str(node.symbol or "").rsplit(".", 1)[-1]
                       and node.status not in {"HISTORICAL_SOURCE", "RETIRED_SOURCE"}]
        if not matches and graph is None:
            for path in sorted(self.repo_root.rglob("*.py")):
                if any(part in {".git", "artifacts", "official_harness", "harness"}
                       for part in path.parts):
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line, source_line in enumerate(text.splitlines(), 1):
                    if re.search(rf"\b{re.escape(needle)}\b", source_line):
                        matches.append({"path": path.relative_to(self.repo_root).as_posix(),
                                        "start_line": line, "end_line": line,
                                        "source_line": source_line})
                        break
                if len(matches) >= 12:
                    break
        result = {"symbol": symbol, "matches": matches[:12]}
        self._record("search_source", {"symbol": symbol}, result)
        return result

    def read_source(self, path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
        source_path = self._safe_repo_path(path)
        if not source_path.is_file():
            raise FileNotFoundError(path)
        lines = source_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line)); end = min(len(lines), max(start, int(end_line)))
        result = {"path": Path(path).as_posix(), "start_line": start,
                  "end_line": end, "content": "\n".join(
                      f"{number}: {lines[number - 1]}" for number in range(start, end + 1))}
        self._record("read_source", {"path": path, "start_line": start, "end_line": end}, result)
        return result

    def write_probe(self, name: str, source: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(name)):
            raise ValueError("invalid probe name")
        parsed = ast.parse(str(source), filename=str(name))
        forbidden_modules = {"os", "subprocess", "shutil", "socket", "pathlib", "urllib", "requests"}
        forbidden_calls = {"eval", "exec", "open", "__import__", "input"}
        for node in ast.walk(parsed):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [alias.name.split(".", 1)[0] for alias in node.names]
                if forbidden_modules.intersection(modules):
                    raise ValueError("probe imports a forbidden module")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                raise ValueError("probe contains a forbidden call")
        self.probe_root.mkdir(parents=True, exist_ok=True)
        probe_id = stable_id("target-recovery-probe", name, source)
        if probe_id in self.probes:
            raise ValueError("duplicate target recovery probe")
        if len(self.probes) >= self.max_probes:
            raise RuntimeError("target recovery probe budget exhausted")
        path = self.probe_root / f"{probe_id}.py"
        path.write_text(str(source), encoding="utf-8")
        self.probes[probe_id] = _RecoveryProbe(probe_id, path, str(source))
        result = {"probe_id": probe_id, "source_path": str(path), "validated": True}
        self._record("write_probe", {"name": name}, result)
        return result

    def _probe(self, probe_id: str, tree: Path, destination: str) -> dict[str, Any]:
        probe = self.probes.get(str(probe_id))
        if probe is None:
            raise KeyError("unknown probe_id")
        runs = getattr(probe, f"{destination}_runs")
        if len(runs) >= self.stability_runs:
            raise RuntimeError("probe stability budget exhausted")
        attempts_name = f"{destination}_attempts"
        attempts = int(getattr(probe, attempts_name))
        # A traced cold start can exceed the per-check timeout in a large
        # dependency image even though the same probe runs normally without
        # instrumentation. BLOCKED/UNSUPPORTED observations are therefore
        # execution attempts, not semantic stability samples. Permit two
        # bounded retries and require ``stability_runs`` actual PASS/FAIL
        # observations; this avoids both false nondeterminism and an
        # unbounded recovery loop.
        if attempts >= self.stability_runs + 2:
            raise RuntimeError("probe execution attempt budget exhausted")
        setattr(probe, attempts_name, attempts + 1)
        # Only the first attempt carries tracing overhead. A retry after a
        # traced timeout must be untraced or it would reproduce the timeout
        # instead of measuring the target behaviour.
        command = ("python", "-c", probe.source)
        trace = run_trace(
            tree, command,
            timeout_seconds=self.timeout_seconds, trace_enabled=attempts == 0,
            target_symbols=tuple(symbol for goal in self.goal_contracts if hasattr(goal, "target_symbols")
                                 for symbol in goal.target_symbols) if isinstance(self.goal_contracts, (tuple, list)) else (),
        )
        status = getattr(trace.observation.status, "value", trace.observation.status)
        valid_semantic_sample = str(status) in {"PASS", "FAIL"}
        if valid_semantic_sample:
            runs.append(trace)
        observation = trace.observation.to_dict() if hasattr(trace.observation, "to_dict") else dict(trace.observation)
        result = {"probe_id": probe.probe_id, "tree": destination,
                  "attempt_index": attempts + 1,
                  "run_index": len(runs) if valid_semantic_sample else None,
                  "counts_toward_stability": valid_semantic_sample,
                  "observation": observation, "first_project_frame": trace.first_project_frame,
                  "trace_bundle_id": trace.trace_bundle_id}
        self._record("run_probe_on_" + destination, {"probe_id": probe_id}, result)
        return result

    def run_probe_on_clean(self, probe_id: str) -> dict[str, Any]:
        return self._probe(probe_id, self.clean_snapshot, "clean")

    def run_probe_on_working(self, probe_id: str) -> dict[str, Any]:
        return self._probe(probe_id, self.working_snapshot, "working")

    def register_observation_contract(self, probe_id: str, contract: dict[str, Any],
                                      requirement_id: str | None = None,
                                      input_recipe: dict[str, Any] | None = None,
                                      authority: str = "PROVISIONAL") -> dict[str, Any]:
        probe = self.probes.get(str(probe_id))
        if probe is None:
            raise KeyError("write_probe before registering a contract")
        if not isinstance(contract, dict) or "expected" not in contract:
            raise ValueError("contract requires structured expected payload")
        observation = ObservationContract(
            str(contract.get("relation", "probe observation")), contract["expected"],
            str(contract.get("observable", "return")),
            str(contract.get("comparator", "EQUALS")),
        )
        if observation.normalized_comparator not in ObservationContract._COMPARATORS:
            raise ValueError("unsupported observation comparator")
        requested = str(authority).upper()
        probe.contract = observation
        probe.requirement_id = str(requirement_id) if requirement_id else None
        # A model cannot mint A/B/C authority.  Public checks and exact issue
        # witnesses are registered by the deterministic recovery path; an
        # agent-generated contract stays provisional until it is matched to
        # one of those sources.
        probe.authority = "PROVISIONAL"
        probe.input_recipe = dict(input_recipe or {})
        result = {"probe_id": probe.probe_id, "contract_id": observation.contract_id,
                  "authority": probe.authority, "requirement_id": probe.requirement_id}
        self._record("register_observation_contract", {"probe_id": probe_id, "authority": requested}, result)
        return result

    def finish_target_recovery(self, summary: str = "") -> dict[str, Any]:
        self.finished = True
        accepted = []
        for probe in self.probes.values():
            if probe.contract is None or len(probe.clean_runs) < self.stability_runs:
                continue
            clean = probe.clean_runs[-1].observation
            check = LegacyExecutableCheck(
                check_id=probe.probe_id, command=("python", "-c", probe.source),
                role="CANDIDATE", authority="PROVISIONAL", expected=probe.contract,
            )
            clean_stable = len({
                semantic_observation_signature(item.observation, check)
                for item in probe.clean_runs
            }) == 1
            if clean_stable and not probe.contract.matches(clean):
                accepted.append(probe.probe_id)
        result = {"finished": True, "accepted_probe_ids": tuple(accepted), "summary": str(summary)}
        self._record("finish_target_recovery", {}, result)
        return result

    def invoke(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self.allowed_tool_names:
            raise ValueError(f"target recovery tool is not allowed: {name}")
        method = getattr(self, name)
        try:
            return method(**dict(arguments))
        except Exception as exc:
            self._record(name, dict(arguments), error=exc)
            raise


TARGET_RECOVERY_TOOL_SCHEMAS = tuple({
    "type": "function",
    "function": {
        "name": name,
        "description": "Restricted target recovery operation.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
        },
    },
} for name, properties, required in (
    ("search_source", {"symbol": {"type": "string"}}, ["symbol"]),
    ("read_source", {
        "path": {"type": "string"},
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
    }, ["path"]),
    ("write_probe", {
        "name": {"type": "string"}, "source": {"type": "string"},
    }, ["name", "source"]),
    ("run_probe_on_clean", {"probe_id": {"type": "string"}}, ["probe_id"]),
    ("run_probe_on_working", {"probe_id": {"type": "string"}}, ["probe_id"]),
    ("register_observation_contract", {
        "probe_id": {"type": "string"},
        "contract": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "relation": {"type": "string"},
                "expected": {},
                "observable": {"type": "string"},
                "comparator": {"type": "string", "enum": sorted(
                    ObservationContract._COMPARATORS
                )},
            },
            "required": ["expected", "comparator"],
        },
        "requirement_id": {"type": ["string", "null"]},
        "input_recipe": {"type": "object"},
        "authority": {"type": "string"},
    }, ["probe_id", "contract"]),
    ("finish_target_recovery", {"summary": {"type": "string"}}, []),
))


class TargetRecoveryAgent:
    """Run a bounded DeepSeek recovery conversation with only safe tools."""

    def __init__(self, transport: Any, *, max_turns: int = 16, max_tokens: int = 12000,
                 timeout_seconds: float = 1200.0,
                 provider_request_timeout_seconds: float = 180.0):
        self.transport = transport
        self.max_turns = max(1, int(max_turns))
        self.max_tokens = int(max_tokens)
        self.timeout_seconds = float(timeout_seconds)
        self.provider_request_timeout_seconds = float(provider_request_timeout_seconds)

    def recover(self, executor: TargetRecoveryToolExecutor, context: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        messages = [
            {"role": "system", "content": (
                "You are a restricted target recovery agent. You may only use the supplied tools. "
                "Do not edit project files, read hidden files, use the network, or claim an Oracle from prose. "
                "Use the supplied source_hints and working_diff to identify the target entrypoint before searching broadly. "
                "After locating it, write one minimal reproduction probe, register its structured observation contract, "
                "then collect two valid semantic runs on clean and working, and finish. "
                "A BLOCKED/UNSUPPORTED execution is not a semantic run; retry the same probe when the tool requests it. "
                "A target scenario is accepted only after two stable clean runs violate its structured contract.")},
            {"role": "user", "content": json.dumps(context, sort_keys=True, default=str)},
        ]
        deadline = time.monotonic() + self.timeout_seconds
        last_signature: str | None = None
        repeated_tool_count = 0
        read_tool_count = 0
        for _ in range(self.max_turns):
            if executor.finished or time.monotonic() >= deadline:
                break
            # Recovery is a finite evidence-building protocol.  Models often
            # reread the same source interval while never producing a probe;
            # after a bounded number of such turns, expose only the next
            # state-advancing tool.  This does not create an oracle or invent
            # an input—the model still has to supply a concrete probe and
            # contract, but cannot consume the whole turn budget on reads.
            available_tools = TARGET_RECOVERY_TOOL_SCHEMAS
            forced_choice: str | dict[str, Any] = "required"
            # Once a probe exists, use an explicit evidence protocol instead
            # of allowing the model to spend the remaining turns out of
            # order. In particular, the contract must be registered before
            # paired execution; otherwise stable failures still cannot be
            # aligned to an Authority-A/B/C goal and are discarded.
            forced_progress_tool: str | None = None
            if executor.probes:
                attempt_limit = executor.stability_runs + 2
                pending = next((
                    probe for probe in executor.probes.values()
                    if probe.contract is None
                    and probe.clean_attempts < attempt_limit
                    and probe.working_attempts < attempt_limit
                ), None)
                if pending is not None:
                    forced_progress_tool = "register_observation_contract"
                else:
                    pending = next((
                        probe for probe in executor.probes.values()
                        if probe.contract is not None
                        and len(probe.clean_runs) < executor.stability_runs
                        and probe.clean_attempts < attempt_limit
                    ), None)
                    if pending is not None:
                        forced_progress_tool = "run_probe_on_clean"
                    else:
                        pending = next((
                            probe for probe in executor.probes.values()
                            if probe.contract is not None
                            and len(probe.clean_runs) >= executor.stability_runs
                            and len(probe.working_runs) < executor.stability_runs
                            and probe.working_attempts < attempt_limit
                        ), None)
                        if pending is not None:
                            forced_progress_tool = "run_probe_on_working"
                        elif any(
                            probe.contract is not None
                            and len(probe.clean_runs) >= executor.stability_runs
                            and len(probe.working_runs) >= executor.stability_runs
                            for probe in executor.probes.values()
                        ):
                            forced_progress_tool = "finish_target_recovery"
                        elif len(executor.probes) < executor.max_probes:
                            forced_progress_tool = "write_probe"
                        else:
                            forced_progress_tool = "finish_target_recovery"
            elif repeated_tool_count >= 2 or read_tool_count >= 6:
                if not executor.probes:
                    available_tools = tuple(
                        schema for schema in TARGET_RECOVERY_TOOL_SCHEMAS
                        if schema["function"]["name"] == "write_probe"
                    )
                    forced_choice = {
                        "type": "function", "function": {"name": "write_probe"},
                    }
            if forced_progress_tool is not None:
                available_tools = tuple(
                    schema for schema in TARGET_RECOVERY_TOOL_SCHEMAS
                    if schema["function"]["name"] == forced_progress_tool
                )
                forced_choice = {
                    "type": "function",
                    "function": {"name": forced_progress_tool},
                }
            try:
                message = self.transport.complete(
                    messages, tools=available_tools,
                    max_tokens=self.max_tokens,
                    timeout_seconds=min(
                        self.provider_request_timeout_seconds,
                        max(1.0, deadline - time.monotonic()),
                    ),
                    tool_choice=forced_choice,
                )
            except Exception as exc:
                executor._record("transport", {}, error=exc)
                break
            if not isinstance(message, dict):
                break
            request_id = message.get("_request_id")
            if request_id:
                executor.events.append({
                    "time_ns": time.time_ns(), "tool": "model_request",
                    "request_id": str(request_id), "phase": "target_recovery",
                    "phase_key": "case:target_recovery",
                })
            # A provider assistant message containing ``tool_calls`` must be
            # followed by exactly one ``tool`` response for every call before
            # another assistant turn.  We may execute only the first call in
            # a recovery turn (the first call can change the probe state), but
            # all additional calls still receive an explicit deferred result;
            # omitting them makes the next DeepSeek request invalid (HTTP 400).
            messages.append(message)
            calls = message.get("tool_calls") or ()
            if not calls:
                continue
            executed = False
            available_tool_names = {
                schema["function"]["name"] for schema in available_tools
            }
            for call in calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = str(function.get("name", ""))
                raw = function.get("arguments", {})
                call_id = str(call.get("id", name)) if isinstance(call, dict) else name
                if executed:
                    result = {
                        "error": "DeferredToolCall",
                        "message": (
                            "Only the first recovery tool call in a turn is executed; "
                            "re-evaluate the updated probe state on the next turn."
                        ),
                    }
                else:
                    try:
                        if name not in available_tool_names:
                            raise ValueError(
                                f"tool {name!r} is not valid in the current recovery state"
                            )
                        arguments = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
                        signature = json.dumps(
                            {"name": name, "arguments": arguments},
                            sort_keys=True, default=str,
                        )
                        if signature == last_signature:
                            repeated_tool_count += 1
                        else:
                            last_signature = signature
                            repeated_tool_count = 1
                        if name in {"read_source", "search_source"}:
                            read_tool_count += 1
                        result = executor.invoke(name, arguments)
                    except Exception as exc:
                        result = {"error": type(exc).__name__, "message": str(exc)}
                    executed = True
                messages.append({"role": "tool", "tool_call_id": call_id,
                                 "name": name, "content": json.dumps(result, default=str)})
            if repeated_tool_count >= 2 or read_tool_count >= 6:
                messages.append({
                    "role": "user",
                    "content": (
                        "Stop source exploration. The next turn is restricted to the "
                        "state-advancing recovery tool shown in the tool list: write one "
                        "minimal probe, run it on clean/working twice, register the exact "
                        "structured observation contract, or finish. Do not reread the "
                        "same source interval."
                    ),
                })
        executor.timed_out = time.monotonic() >= deadline
        if not executor.finished:
            executor.finish_target_recovery("bounded recovery budget exhausted")
        return tuple(executor.events)


def _check_contract(check: Any) -> ObservationContract:
    expected = getattr(check, "expected", None)
    if isinstance(expected, ObservationContract):
        return expected
    if isinstance(expected, dict):
        return ObservationContract(
            str(expected.get("relation", "public check succeeds")),
            expected.get("expected", {"exit_code": 0}),
            str(expected.get("observable", "process")),
            str(expected.get("comparator", "EXIT_ZERO")),
        )
    return ObservationContract("public check succeeds", {"exit_code": 0}, "process", "EXIT_ZERO")


def _witness_contract(witness: dict[str, Any]) -> ObservationContract:
    expected = witness.get("expected", {"exit_code": 0})
    if not isinstance(expected, dict):
        expected = {"value": expected}
    if any(key in expected for key in ("exception", "exception_type", "type")):
        comparator = "RAISES"
        observable = "exception"
    elif "stdout" in expected or "stderr" in expected or "exit_code" in expected:
        comparator = "EXIT_ZERO"
        observable = "process"
    elif "value" in expected:
        comparator = "EQUALS"
        observable = "return"
    else:
        comparator = "RELATION_HOLDS"
        observable = "return"
    return ObservationContract(
        str(witness.get("expected_relation", "issue witness contract")),
        expected,
        observable,
        comparator,
    )


def _stable_run(
    tree: Path,
    scenario: ExecutableScenario,
    runs: int,
    contract: ObservationContract | None = None,
    *,
    base_tree: Path | None = None,
):
    """Run a scenario repeatedly using the same semantic stability rules.

    The first run carries tracing metadata while the final run supplies the
    observation used by callers.  Raw stdout/stderr are deliberately excluded
    from the stability decision; comparator-specific signatures are shared with
    the normal execution queue.
    """
    expected = contract or ObservationContract(
        "scenario succeeds", {"exit_code": 0},
        observable="process", comparator="EXIT_ZERO",
    )
    check = LegacyExecutableCheck(
        check_id=stable_id("recovery-stability-check", scenario.scenario_id, expected.normalized()),
        command=tuple(scenario.command), role="CANDIDATE", authority="PROVISIONAL",
        cwd=scenario.cwd, environment=tuple(scenario.environment),
        timeout_seconds=scenario.timeout_seconds, expected=expected,
    )
    overlay_paths = (
        diff_between(Path(base_tree), Path(tree)).changed_files
        if base_tree is not None else ()
    )
    traces = tuple(
        run_trace(
            tree, scenario.command, cwd=scenario.cwd,
            environment=scenario.environment,
            timeout_seconds=scenario.timeout_seconds,
            trace_enabled=index == 0,
            overlay_paths=overlay_paths,
        )
        for index in range(max(1, int(runs)))
    )
    signatures = tuple(
        semantic_observation_signature(trace.observation, check)
        for trace in traces
    )
    stable = len(set(signatures)) == 1
    first, last = traces[0], traces[-1]
    merged = replace(
        first, observation=last.observation,
        stable_runs=len(traces) if stable else 0, comparable=stable,
    )
    return merged, len(traces) if stable else 1


def _environment_blocked(trace: Any) -> bool:
    """Classify setup/dependency failures before target code as BLOCKED."""
    observation = trace.observation
    if trace.first_project_frame:
        return False
    text = f"{observation.exception or ''} {observation.stderr or ''}".casefold()
    return any(token in text for token in (
        "modulenotfounderror", "no module named", "importerror",
        "environmenterror", "dependency", "cannot import",
    ))


def _candidate_requirement(requirements: tuple[Any, ...], operation: str, witness: dict[str, Any] | None = None) -> Any | None:
    operation = operation.casefold()
    candidates = [
        leaf for leaf in requirements
        if not getattr(leaf, "preservation", False)
        and getattr(leaf, "hard", False)
    ]
    exact = [
        leaf for leaf in candidates
        if operation and operation in str(getattr(leaf, "operation", "")).casefold()
        or operation and str(getattr(leaf, "operation", "")).casefold().endswith(operation)
    ]
    return (exact or candidates or [None])[0]


def _goal_items(requirements: Any) -> tuple[Any, ...]:
    """Normalize new flat GoalContracts and historical graph leaves."""
    leaves = getattr(requirements, "leaves", None)
    if isinstance(leaves, dict):
        return tuple(leaves.values())
    if isinstance(requirements, (tuple, list)):
        return tuple(requirements)
    return tuple(requirements or ())


def _append_witness_scenarios(
    *,
    clean_snapshot: Path,
    working_snapshot: Path,
    requirement_graph: RequirementGraph,
    public_evidence: PublicEvidence,
    config: TargetRecoveryConfig,
    scenarios: list[ExecutableScenario],
    cells: list[ChallengeCell],
    rejected: list[RejectedTargetCandidate],
    seen: set[str],
) -> None:
    leaves = _goal_items(requirement_graph)
    for record in getattr(public_evidence, "records", ()):
        if getattr(record, "source", None) != "issue":
            continue
        for witness in issue_witnesses(record):
            script = witness.get("script")
            if not isinstance(script, str) or not script.strip():
                rejected.append(RejectedTargetCandidate(
                    str(witness.get("witness_id", "")), "NO_EXECUTABLE_WITNESS_SCRIPT",
                ))
                continue
            requirement = _candidate_requirement(leaves, str(witness.get("operation", "")), witness)
            if requirement is None:
                rejected.append(RejectedTargetCandidate(
                    str(witness.get("witness_id", "")), "NO_TARGET_REQUIREMENT",
                ))
                continue
            contract = _witness_contract(witness)
            recipe = InputRecipe(
                stable_id("target-witness-recipe", requirement.requirement_id, witness.get("witness_id")),
                "ISSUE_WITNESS", {"__reachpatch_issue_witness__": dict(witness)},
                ("ISSUE_EVIDENCE", str(record.evidence_id)),
                ("python", "-c", script), None, (), (),
            )
            scenario = ExecutableScenario(
                stable_id("target-witness-scenario", requirement.requirement_id, witness.get("witness_id"), contract.normalized()),
                recipe.command, ".", (), config.check_timeout_seconds,
            )
            semantic = scenario_semantic_key(
                requirement_contract_id=requirement.expected_observation.contract_id,
                role="TARGET", input_recipe=recipe,
                observation_contract=contract,
            )
            if semantic in seen:
                continue
            seen.add(semantic)
            clean_trace, clean_runs = _stable_run(Path(clean_snapshot), scenario, config.stability_runs, contract, base_tree=Path(clean_snapshot))
            working_trace, working_runs = _stable_run(Path(working_snapshot), scenario, config.stability_runs, contract, base_tree=Path(clean_snapshot))
            if _environment_blocked(clean_trace) or _environment_blocked(working_trace):
                rejected.append(RejectedTargetCandidate(scenario.scenario_id, "ENVIRONMENT_BLOCKED"))
                continue
            if clean_runs < config.stability_runs:
                rejected.append(RejectedTargetCandidate(scenario.scenario_id, "BASELINE_NOT_STABLE"))
                continue
            clean_pass = contract.matches(clean_trace.observation)
            working_pass = contract.matches(working_trace.observation)
            # A witness that already passes on clean is preservation evidence,
            # not proof of the target defect.  Keep it out of target recovery.
            if clean_pass:
                rejected.append(RejectedTargetCandidate(scenario.scenario_id, "BASELINE_ALREADY_PASS"))
                continue
            authority = "B"
            oracle = ExecutableOracle(
                stable_id("target-witness-oracle", witness.get("witness_id"), contract.normalized()),
                authority, contract.relation, contract.expected, True,
                (str(record.evidence_id),),
            )
            terminal = ChallengeStatus.PASS if working_pass else ChallengeStatus.FAIL
            cells.append(ChallengeCell(
                stable_id("target-witness-cell", semantic),
                "", requirement.requirement_id,
                stable_id("target-witness-binding", requirement.requirement_id, witness.get("operation", "")),
                "", (), "TARGET", recipe, scenario, contract, oracle, authority,
                clean_trace.observation.status, working_trace.observation.status,
                working_trace.trace_bundle_id, min(clean_runs, working_runs), terminal,
                requirement.hard, "ISSUE_WITNESS",
            ))
            scenarios.append(scenario)
            if len(scenarios) >= config.max_probes:
                return


def _append_preservation_checks(
    *,
    clean_snapshot: Path,
    working_snapshot: Path,
    public_evidence: PublicEvidence,
    config: TargetRecoveryConfig,
    scenarios: list[ExecutableScenario],
    cells: list[ChallengeCell],
    seen: set[str],
) -> None:
    """Promote stable public preservation checks to Authority A/C cells."""
    for check in getattr(public_evidence, "checks", ()):
        if str(getattr(check, "role", "")).upper() != "PRESERVATION":
            continue
        command = tuple(map(str, getattr(check, "command", ())))
        if not command:
            continue
        contract = _check_contract(check)
        recipe = InputRecipe(
            stable_id("preservation-recipe", getattr(check, "check_id", ""), command),
            "PUBLIC_REPLAY", getattr(check, "concrete_input", None),
            ("PUBLIC_PRESERVATION",), command, getattr(check, "check_id", None),
            tuple(getattr(check, "environment", ())), tuple(getattr(check, "symbol_references", ())),
        )
        scenario = ExecutableScenario(
            stable_id("preservation-scenario", getattr(check, "check_id", ""), contract.normalized()),
            command, str(getattr(check, "cwd", ".")), tuple(getattr(check, "environment", ())),
            min(float(getattr(check, "timeout_seconds", config.check_timeout_seconds)), config.check_timeout_seconds),
        )
        semantic = scenario_semantic_key(
            requirement_contract_id=str(getattr(check, "check_id", "")),
            role="PRESERVATION", input_recipe=recipe,
            observation_contract=contract,
        )
        if semantic in seen:
            continue
        seen.add(semantic)
        baseline, baseline_runs = _stable_run(Path(clean_snapshot), scenario, config.stability_runs, contract, base_tree=Path(clean_snapshot))
        working, working_runs = _stable_run(Path(working_snapshot), scenario, config.stability_runs, contract, base_tree=Path(clean_snapshot))
        if _environment_blocked(baseline) or _environment_blocked(working):
            continue
        if baseline_runs < config.stability_runs or not contract.matches(baseline.observation):
            continue
        authority = str(getattr(check, "authority", "A"))
        if authority not in {"A", "B", "C"}:
            authority = "C"
        oracle = ExecutableOracle(
            stable_id("preservation-oracle", getattr(check, "check_id", ""), contract.normalized()),
            authority, contract.relation, contract.expected, True,
            tuple(getattr(check, "source_evidence_ids", ())),
        )
        terminal = ChallengeStatus.PASS if contract.matches(working.observation) else ChallengeStatus.FAIL
        requirement_id = next(iter(getattr(check, "requirement_ids", ())), stable_id("preservation-requirement", getattr(check, "check_id", "")))
        cells.append(ChallengeCell(
            stable_id("preservation-cell", semantic), "", requirement_id,
            stable_id("preservation-binding", requirement_id), "", (), "PRESERVATION",
            recipe, scenario, contract, oracle, authority,
            baseline.observation.status, working.observation.status,
            working.trace_bundle_id, min(baseline_runs, working_runs), terminal,
            False, "PUBLIC_CHECK",
        ))
        scenarios.append(scenario)
        if len(scenarios) >= config.max_probes * 2:
            return


def _append_agent_scenarios(
    *, clean_snapshot: Path, working_snapshot: Path,
    requirement_graph: RequirementGraph, program_slice: Any,
    public_evidence: PublicEvidence,
    transport: Any, run_root: Path, config: TargetRecoveryConfig,
    scenarios: list[ExecutableScenario], cells: list[ChallengeCell],
    rejected: list[RejectedTargetCandidate], seen: set[str],
) -> tuple[dict[str, Any], ...]:
    """Ask the restricted recovery player for missing executable witnesses."""
    if transport is None or len(scenarios) >= config.max_probes:
        return ()
    executor = TargetRecoveryToolExecutor(
        repo_root=Path(clean_snapshot), clean_snapshot=Path(clean_snapshot),
        working_snapshot=Path(working_snapshot), requirement_graph=requirement_graph,
        program_slice=program_slice, run_root=Path(run_root),
        max_probes=config.max_probes - len(scenarios),
        stability_runs=config.stability_runs, timeout_seconds=config.check_timeout_seconds,
    )
    leaves = tuple(leaf for leaf in _goal_items(requirement_graph)
                   if not getattr(leaf, "preservation", False) and getattr(leaf, "hard", False))
    graph = getattr(program_slice, "graph", program_slice)
    context = {
        "claims": [leaf.to_dict() for leaf in leaves],
        "target_symbols": tuple(symbol for leaf in leaves for symbol in leaf.operation.split(".")),
        "source_slices": [node.to_dict() for node in getattr(graph, "nodes", {}).values()][:24],
        "issue_evidence": [record.to_dict() for record in getattr(public_evidence, "records", ())],
        "constraints": {"max_probes": config.max_probes, "stability_runs": config.stability_runs},
    }
    try:
        events = TargetRecoveryAgent(
            transport, max_turns=max(8, config.max_probes * 3),
            timeout_seconds=config.timeout_seconds,
        ).recover(executor, context)
    except Exception as exc:
        rejected.append(RejectedTargetCandidate("target-recovery-agent", type(exc).__name__))
        return ()
    accepted = {
        str(item) for event in events if event.get("tool") == "finish_target_recovery"
        for item in event.get("result", {}).get("accepted_probe_ids", ())
        if isinstance(event.get("result"), dict)
    }
    for probe_id, probe in executor.probes.items():
        if probe_id not in accepted or probe.contract is None:
            continue
        if len(probe.clean_runs) < config.stability_runs or len(probe.working_runs) < config.stability_runs:
            continue
        clean = probe.clean_runs[-1]; working = probe.working_runs[-1]
        probe_check = LegacyExecutableCheck(
            check_id=probe_id, command=("python", str(probe.source_path)),
            role="CANDIDATE", authority="PROVISIONAL", expected=probe.contract,
        )
        clean_stable = len({
            semantic_observation_signature(run.observation, probe_check)
            for run in probe.clean_runs
        }) == 1
        working_stable = len({
            semantic_observation_signature(run.observation, probe_check)
            for run in probe.working_runs
        }) == 1
        if not clean_stable or not working_stable or probe.contract.matches(clean.observation):
            continue
        requirement = next((leaf for leaf in leaves if leaf.requirement_id == probe.requirement_id), None) or (leaves[0] if leaves else None)
        if requirement is None:
            rejected.append(RejectedTargetCandidate(probe_id, "NO_TARGET_REQUIREMENT"))
            continue
            command = ("python", str(probe.source_path))
        recipe = InputRecipe(
            stable_id("agent-target-recipe", probe_id, probe.input_recipe),
            "TARGET_RECOVERY_PROBE", probe.input_recipe.get("concrete_input"),
            ("DEEPSEEK_TARGET_RECOVERY",), command, None, (), (),
        )
        scenario = ExecutableScenario(
            stable_id("agent-target-scenario", probe_id, probe.contract.normalized()),
            command, ".", (), config.check_timeout_seconds,
        )
        semantic = scenario_semantic_key(
            requirement_contract_id=requirement.expected_observation.contract_id,
            role="TARGET", input_recipe=recipe, observation_contract=probe.contract,
        )
        if semantic in seen:
            continue
        seen.add(semantic)
        oracle = ExecutableOracle(
            stable_id("agent-target-oracle", probe_id, probe.contract.normalized()),
            "PROVISIONAL", probe.contract.relation, probe.contract.expected,
            False, (),
        )
        terminal = ChallengeStatus.PASS if probe.contract.matches(working.observation) else ChallengeStatus.FAIL
        cells.append(ChallengeCell(
            stable_id("agent-target-cell", semantic), "", requirement.requirement_id,
            stable_id("agent-target-binding", requirement.requirement_id), "", (), "TARGET",
            recipe, scenario, probe.contract, oracle, "PROVISIONAL",
            clean.observation.status, working.observation.status, working.trace_bundle_id,
            config.stability_runs, terminal, requirement.hard, "DEEPSEEK_RECOVERY",
        ))
        scenarios.append(scenario)
        if len(scenarios) >= config.max_probes:
            break
    return events


def recover_target_scenarios(repo_root: Path, clean_snapshot: Path, working_snapshot: Path, requirement_graph: RequirementGraph, program_slice: Any, public_evidence: PublicEvidence, transport: Any, run_root: Path, config: TargetRecoveryConfig | None = None) -> TargetRecoveryResult:
    # Historical adapter only. Production calls ``recover_target_checks`` and
    # therefore never imports or constructs these graph records.
    global BindingGap, ChallengeCell, ChallengeStatus, ExecutableScenario, InputRecipe, scenario_semantic_key
    from reachpatch.models.graphs import (
        BindingGap, ChallengeCell, ChallengeStatus, ExecutableScenario,
        InputRecipe,
    )
    from reachpatch.reach_avoid.semantics import scenario_semantic_key
    config = config or TargetRecoveryConfig()
    scenarios: list[ExecutableScenario] = []
    cells: list[ChallengeCell] = []
    gaps: list[BindingGap] = []
    rejected: list[RejectedTargetCandidate] = []
    seen: set[str] = set()
    target_leaves = tuple(item for item in _goal_items(requirement_graph) if not getattr(item, "preservation", False) and getattr(item, "hard", False))
    for check in getattr(public_evidence, "checks", ()):
        if str(getattr(check, "role", "TARGET")).upper() != "TARGET":
            continue
        command = tuple(str(item) for item in getattr(check, "command", ()))
        if not command:
            continue
        contract = _check_contract(check)
        requirement = next((leaf for leaf in target_leaves if leaf.requirement_id in getattr(check, "requirement_ids", ()) or any(symbol.casefold() in leaf.operation.casefold() for symbol in getattr(check, "symbol_references", ()))), target_leaves[0] if target_leaves else None)
        if requirement is None:
            rejected.append(RejectedTargetCandidate(stable_id("target-candidate", command), "NO_TARGET_REQUIREMENT"))
            continue
        recipe = InputRecipe(stable_id("target-recipe", requirement.requirement_id, command, getattr(check, "concrete_input", None)), "PUBLIC_REPLAY", getattr(check, "concrete_input", None), ("PUBLIC_CHECK",), command, getattr(check, "check_id", None), tuple(getattr(check, "environment", ())), tuple(getattr(check, "symbol_references", ())), "PUBLIC_CHECK")
        scenario = ExecutableScenario(stable_id("target-scenario", requirement.requirement_id, command, contract.normalized()), command, str(getattr(check, "cwd", ".")), tuple(getattr(check, "environment", ())), min(float(getattr(check, "timeout_seconds", config.check_timeout_seconds)), config.check_timeout_seconds))
        semantic = scenario_semantic_key(requirement_contract_id=requirement.expected_observation.contract_id, role="TARGET", input_recipe=recipe, observation_contract=contract)
        if semantic in seen:
            continue
        seen.add(semantic)
        clean_trace, clean_runs = _stable_run(Path(clean_snapshot), scenario, config.stability_runs, contract, base_tree=Path(clean_snapshot))
        working_trace, working_runs = _stable_run(Path(working_snapshot), scenario, config.stability_runs, contract, base_tree=Path(clean_snapshot))
        clean_pass = contract.matches(clean_trace.observation)
        working_pass = contract.matches(working_trace.observation)
        if (
            clean_trace.observation.status in {OutcomeStatus.BLOCKED, OutcomeStatus.UNSUPPORTED}
            or working_trace.observation.status in {OutcomeStatus.BLOCKED, OutcomeStatus.UNSUPPORTED}
            or _environment_blocked(clean_trace) or _environment_blocked(working_trace)
        ):
            rejected.append(RejectedTargetCandidate(scenario.scenario_id, "ENVIRONMENT_BLOCKED"))
            continue
        if clean_runs < config.stability_runs:
            rejected.append(RejectedTargetCandidate(scenario.scenario_id, "BASELINE_NOT_STABLE"))
            continue
        if clean_pass:
            rejected.append(RejectedTargetCandidate(scenario.scenario_id, "BASELINE_ALREADY_PASS"))
            continue
        oracle = ExecutableOracle(stable_id("target-oracle", getattr(check, "check_id", ""), contract.normalized()), str(getattr(check, "authority", "A")), contract.relation, contract.expected, True, tuple(getattr(check, "source_evidence_ids", ())))
        terminal = ChallengeStatus.PASS if working_pass else ChallengeStatus.FAIL
        cells.append(ChallengeCell(stable_id("target-cell", semantic), "", requirement.requirement_id, stable_id("target-binding", requirement.requirement_id), "", (), "TARGET", recipe, scenario, contract, oracle, oracle.authority, OutcomeStatus.PASS if clean_pass else OutcomeStatus.FAIL, OutcomeStatus.PASS if working_pass else OutcomeStatus.FAIL, working_trace.trace_bundle_id, min(clean_runs, working_runs), terminal, requirement.hard, "PUBLIC_CHECK"))
        scenarios.append(scenario)
        if len(scenarios) >= config.max_probes:
            break
    _append_witness_scenarios(
        clean_snapshot=Path(clean_snapshot),
        working_snapshot=Path(working_snapshot),
        requirement_graph=requirement_graph,
        public_evidence=public_evidence,
        config=config,
        scenarios=scenarios,
        cells=cells,
        rejected=rejected,
        seen=seen,
    )
    _append_preservation_checks(
        clean_snapshot=Path(clean_snapshot),
        working_snapshot=Path(working_snapshot),
        public_evidence=public_evidence,
        config=config,
        scenarios=scenarios,
        cells=cells,
        seen=seen,
    )
    agent_events: tuple[dict[str, Any], ...] = ()
    target_count = sum(1 for cell in cells if str(getattr(cell, "kind", "")).upper() == "TARGET")
    if transport is not None and target_count == 0 and target_leaves:
        agent_events = _append_agent_scenarios(
            clean_snapshot=Path(clean_snapshot),
            working_snapshot=Path(working_snapshot),
            requirement_graph=requirement_graph, program_slice=program_slice,
            public_evidence=public_evidence, transport=transport,
            run_root=Path(run_root), config=config, scenarios=scenarios,
            cells=cells, rejected=rejected, seen=seen,
        )
    if not scenarios:
        gaps.extend(BindingGap(leaf.requirement_id, "NO_EXECUTABLE_TARGET", leaf.hard, (leaf.operation,), (), stable_id("target-gap", leaf.requirement_id)) for leaf in target_leaves)
    timed_out = any(
        "timeout" in str(event.get("error", "")).casefold()
        for event in agent_events if isinstance(event, dict)
    )
    # Keep the scenario/cell views for historical artifact readers, while
    # exposing the execution-driven check queue as the authoritative result.
    # Checks are reconstructed from the already validated cells; no graph
    # alignment or static edge is used to grant target authority.
    target_checks: list[LegacyExecutableCheck] = []
    preservation_checks: list[LegacyExecutableCheck] = []
    for cell in cells:
        role = str(getattr(cell, "kind", "")).upper()
        if role not in {"TARGET", "PRESERVATION"}:
            continue
        check = LegacyExecutableCheck(
            check_id=stable_id("recovered-check", cell.challenge_id),
            command=tuple(cell.execution_scenario.command),
            role=role, authority=str(cell.authority),
            requirement_ids=(str(cell.requirement_id),),
            symbol_references=(), cwd=str(cell.execution_scenario.cwd),
            environment=tuple(cell.execution_scenario.environment),
            timeout_seconds=float(cell.execution_scenario.timeout_seconds),
            expected=cell.observation_contract,
            source_evidence_ids=tuple(cell.oracle.source_evidence_ids),
            goal_id=str(cell.requirement_id), evidence_ids=tuple(cell.oracle.source_evidence_ids),
        )
        (target_checks if role == "TARGET" else preservation_checks).append(check)
    return LegacyTargetRecoveryResult(
        tuple(scenarios), tuple(cells), tuple(gaps), tuple(rejected), timed_out,
        agent_events, tuple(target_checks), tuple(preservation_checks),
    )


def _execution_check_from_public(check: Any, goal_id: str | None, goals: tuple[Any, ...] = ()) -> ExecutableCheck | None:
    """Convert public evidence to a command plus typed oracle.

    This is intentionally a one-way adapter.  No graph scenario/cell is
    created and a mechanical command is never eligible for this conversion.
    """
    command = tuple(str(item) for item in getattr(check, "command", ()))
    if not command or str(getattr(check, "role", "")).upper() == "MECHANICAL":
        return None
    expected = getattr(check, "expected", None)
    comparator = "EXIT_ZERO"
    # When a public command is supplied without an inline expected payload, an
    # exact issue contract may still provide a typed exception Oracle.  Carry
    # that Authority-B contract into the executable check; never infer a
    # scalar return value from prose for an assertion-style command.
    if expected is None and goal_id:
        goal = next((item for item in goals if str(getattr(item, "goal_id", "")) == str(goal_id)), None)
        if goal is not None and str(getattr(goal, "comparator", "")).upper() == "RAISES":
            expected = getattr(goal, "expected", None)
            comparator = "RAISES"
    if isinstance(expected, ObservationContract):
        comparator = expected.normalized_comparator
        expected = expected.expected
    elif expected is None:
        expected = {"exit_code": 0}
    authority = str(getattr(check, "authority", "PROVISIONAL"))
    if authority not in {"A", "B", "C"}:
        authority = "PROVISIONAL"
    target_symbols = tuple(
        getattr(check, "target_symbols", ())
        or getattr(check, "symbol_references", ())
    )
    if not target_symbols and goal_id:
        goal = next(
            (item for item in goals
             if str(getattr(item, "goal_id", "")) == str(goal_id)),
            None,
        )
        if goal is not None:
            target_symbols = tuple(dict.fromkeys(
                str(item) for item in (
                    *getattr(goal, "target_symbols", ()),
                    getattr(goal, "operation", ""),
                ) if str(item).strip() and str(item) != "UNRESOLVED_TARGET"
            ))
    return ExecutableCheck(
        check_id=str(getattr(check, "check_id", "") or stable_id("public-check", command)),
        goal_id=goal_id, role=CheckRole.TARGET, authority=authority,
        command=command, cwd=str(getattr(check, "cwd", ".")),
        environment=tuple(getattr(check, "environment", ())),
        timeout_seconds=float(getattr(check, "timeout_seconds", 120.0)),
        comparator=comparator, expected=expected,
        evidence_ids=tuple(getattr(check, "evidence_ids", ()) or getattr(check, "source_evidence_ids", ())),
        target_symbols=target_symbols,
        input_recipe=getattr(check, "input_recipe", None) or getattr(check, "concrete_input", None),
    )


def _goal_for_check(goals: tuple[Any, ...], check: Any) -> str | None:
    symbols = {str(item).casefold().rsplit(".", 1)[-1] for item in (
        tuple(getattr(check, "target_symbols", ()))
        + tuple(getattr(check, "symbol_references", ()))
    )}
    for goal in goals:
        goal_symbols = {str(item).casefold().rsplit(".", 1)[-1] for item in getattr(goal, "target_symbols", ())}
        if symbols and goal_symbols.intersection(symbols):
            return str(getattr(goal, "goal_id", "")) or None
    if symbols:
        return None
    hard = [goal for goal in goals if bool(getattr(goal, "hard", False))]
    return str(getattr(hard[0], "goal_id", "")) if len(hard) == 1 else None


def _contract_record_check(record: Any, goals: tuple[Any, ...]) -> ExecutableCheck | None:
    """Convert an explicitly executable public API/contract record.

    Contract records are accepted only when their metadata supplies a full
    command and typed expected observation.  Prose docstrings never become an
    executable oracle by themselves.
    """
    metadata = dict(getattr(record, "metadata", {}) or {})
    raw_command = metadata.get("command")
    if not isinstance(raw_command, (tuple, list)) or not raw_command:
        return None
    expected = metadata.get("expected")
    if expected is None:
        return None
    if isinstance(expected, dict) and "kind" in expected:
        contract = ObservationContract(
            str(expected.get("relation", getattr(record, "content", "public contract"))),
            expected.get("expected"), str(expected.get("observable", "return")),
            str(expected.get("kind", expected.get("comparator", "EQUALS"))),
        )
    elif isinstance(expected, dict) and "comparator" in expected:
        contract = ObservationContract(
            str(expected.get("relation", getattr(record, "content", "public contract"))),
            expected.get("expected"), str(expected.get("observable", "return")),
            str(expected.get("comparator", "EQUALS")),
        )
    else:
        contract = ObservationContract(str(getattr(record, "content", "public contract")), expected)
    authority = str(getattr(record, "authority", "C")).upper()
    if authority not in {"A", "B", "C"}:
        return None
    target_symbols = tuple(str(item) for item in metadata.get("target_symbols", metadata.get("symbols", ())))
    return ExecutableCheck(
        check_id=stable_id("public-contract-check", getattr(record, "evidence_id", ""), tuple(raw_command)),
        goal_id=_goal_for_check(goals, type("ContractSymbols", (), {"target_symbols": target_symbols, "symbol_references": target_symbols})()),
        role=CheckRole.TARGET, authority=authority,
        command=tuple(str(item) for item in raw_command), cwd=str(metadata.get("cwd", ".")),
        environment=tuple(sorted((str(k), str(v)) for k, v in dict(metadata.get("environment", {})).items())),
        timeout_seconds=float(metadata.get("timeout_seconds", 120.0)),
        comparator=contract.normalized_comparator, expected=contract.expected,
        evidence_ids=(str(getattr(record, "evidence_id", "")),),
        target_symbols=target_symbols, input_recipe=metadata.get("input"),
    )


def recover_target_checks(
    repo_root: Path,
    clean_snapshot: Path,
    working_snapshot: Path,
    goals: tuple[Any, ...],
    public_evidence: PublicEvidence | tuple[Any, ...] | list[Any],
    transport: Any,
    run_root: Path,
    config: TargetRecoveryConfig | None = None,
    source_hints: tuple[Any, ...] | list[Any] = (),
    oracle_review: Sequence[dict[str, Any]] = (),
    dynamic_graph: Any = None,
) -> TargetRecoveryResult:
    """Recover real executable TARGET/PRESERVATION checks.

    A public command is classified by two clean runs, rather than by an
    annotation supplied by a test discoverer.  A stable clean failure with a
    trusted oracle becomes TARGET; a stable clean pass becomes PRESERVATION.
    The working patch is not allowed to manufacture target authority.
    """
    # The restricted recovery agent is invoked only after deterministic
    # public/issue candidates are exhausted. Its observations can suggest a
    # probe, but without a source-backed A/B/C contract they remain provisional
    # and are never promoted to TARGET by this function.
    config = config or TargetRecoveryConfig()
    supplied_checks = (
        tuple(getattr(public_evidence, "checks", ()))
        if not isinstance(public_evidence, (tuple, list)) else tuple(public_evidence)
    )
    current_diff = diff_between(Path(clean_snapshot), Path(working_snapshot))
    issue_text = "\n".join(
        str(getattr(record, "content", ""))
        for record in getattr(public_evidence, "records", ())
        if getattr(record, "source", "") == "issue"
    )
    generic_symbols = {
        "error", "exception", "assertionerror", "attributeerror",
        "importerror", "indexerror", "keyerror", "nameerror",
        "runtimeerror", "typeerror", "valueerror",
    }
    issue_symbols = tuple(item for item in dict.fromkeys((
        *(
            str(item)
            for goal in goals
            for item in (*getattr(goal, "target_symbols", ()), getattr(goal, "operation", ""))
            if str(item).strip() and str(item) != "UNRESOLVED_TARGET"
        ),
        *re.findall(r"\b([A-Za-z_]\w*)\s*\(", issue_text),
        *re.findall(r"(?m)\bin\s+([A-Za-z_]\w*)\s*$", issue_text),
        *(str(getattr(hint, "symbol", "")) for hint in source_hints if getattr(hint, "symbol", "")),
        *(str(symbol) for check in supplied_checks for symbol in (
            *getattr(check, "target_symbols", ()),
            *getattr(check, "symbol_references", ()),
        )),
    )) if item.casefold().rsplit(".", 1)[-1] not in generic_symbols)[:64]
    # Discover from the complete evidence symbol set before and after P0.
    # An empty diff is valid here: the discovery helper still finds bounded
    # public tests referencing issue/goal/traceback/source-hint symbols.  P0
    # changed symbols augment this set; they never replace it.
    discovered_checks = discover_diff_public_checks(
        Path(working_snapshot), current_diff, supplied_checks,
        max_checks=max(1, config.max_probes),
        target_symbols=issue_symbols,
        preferred_symbols=tuple(symbol for goal in goals if goal.hard for symbol in goal.target_symbols),
    )
    # Probe budget bounds execution work as well as the number of contracts
    # eventually promoted.  Running every public test twice made recovery
    # exceed its configured wall budget before the repair loop could start.
    candidates = (*supplied_checks, *discovered_checks)
    unique_candidates: list[Any] = []
    seen_candidate_keys: set[tuple[Any, ...]] = set()
    duplicate_candidates: list[Any] = []
    for candidate in candidates:
        key = (
            tuple(getattr(candidate, "command", ())),
            str(getattr(candidate, "cwd", ".")),
            tuple(getattr(candidate, "environment", ())),
        )
        if key in seen_candidate_keys:
            duplicate_candidates.append(candidate)
            continue
        seen_candidate_keys.add(key)
        unique_candidates.append(candidate)
    unique_candidates.sort(key=lambda candidate: (
        _goal_for_check(tuple(goals), candidate) is None,
        str(getattr(candidate, "role", "")).upper() != "TARGET",
    ))
    # Preserve a small allowance for clean baseline candidates.  Preservation
    # checks do not consume the target quota, and a public list may place one
    # before the target scenario that establishes the hard goal.
    checks = tuple(unique_candidates[:max(1, config.max_probes) + max(0, int(config.candidate_allowance))])
    targets: list[ExecutableCheck] = []
    preservation: list[ExecutableCheck] = []
    deterministic_events: list[dict[str, Any]] = []
    rejected: list[RejectedTargetCandidate] = [
        RejectedTargetCandidate(
            str(getattr(item, "check_id", stable_id("duplicate-check", repr(item)))),
            "DUPLICATE_COMMAND",
        )
        for item in duplicate_candidates
    ]
    blocked: list[BlockedTargetCandidate] = []
    seen: set[tuple[Any, ...]] = set()
    # Issue code/reproduction witnesses are Authority B candidates. Their
    # source script is executable evidence, while the surrounding prose never
    # becomes an implicit expected value.
    issue_records = tuple(
        item for item in getattr(public_evidence, "records", ())
        if getattr(item, "source", "") == "issue"
    )
    for record in issue_records:
        for witness in issue_witnesses(record):
            script = witness.get("script")
            if not isinstance(script, str) or not script.strip():
                continue
            authority = str(witness.get("authority", "PROVISIONAL")).upper()
            if authority not in {"A", "B", "C"}:
                rejected.append(RejectedTargetCandidate(str(witness.get("witness_id", "")), "ORACLE_PROVISIONAL"))
                continue
            operation = str(witness.get("operation", "")).strip()
            goal = next((item for item in goals if operation.casefold() in {str(symbol).casefold().rsplit(".", 1)[-1] for symbol in item.target_symbols}), None)
            if goal is None:
                hard = [item for item in goals if item.hard]
                goal = hard[0] if len(hard) == 1 else None
            if goal is None:
                rejected.append(RejectedTargetCandidate(str(witness.get("witness_id", "")), "NO_MATCHING_GOAL"))
                continue
            witness_expected = witness.get("expected", {"exit_code": 0})
            if isinstance(witness_expected, dict) and (witness_expected.get("exception_type") or witness_expected.get("type")):
                comparator = "RAISES"
                observable = "exception"
            elif isinstance(witness_expected, dict) and any(key in witness_expected for key in ("exit_code", "stdout", "stderr")):
                comparator = "EXIT_ZERO"
                observable = "process"
            else:
                comparator = "EQUALS"
                observable = "return"
            contract = ObservationContract(
                str(witness.get("expected_relation", "issue witness contract")),
                witness_expected, observable, comparator,
            )
            check_id = stable_id("issue-witness-check", record.evidence_id, witness.get("witness_id"))
            check = ExecutableCheck(
                check_id=check_id, goal_id=str(goal.goal_id), role=CheckRole.TARGET,
                authority=authority, command=("python", "-c", script), cwd=".",
                # A single executable witness is bounded by the per-check
                # budget.  The larger ``timeout_seconds`` value is reserved
                # for the recovery agent session itself; using it here can
                # stall Stage A before the repair loop ever starts.
                environment=(), timeout_seconds=float(config.check_timeout_seconds),
                comparator=contract.normalized_comparator, expected=contract.expected,
                evidence_ids=(str(record.evidence_id),), target_symbols=(operation,),
                input_recipe={"witness_id": witness.get("witness_id")},
            )
            identity = (check.command, check.cwd, check.environment)
            if identity in seen:
                continue
            seen.add(identity)
            clean = execute_check(Path(clean_snapshot), check, stability_runs=config.stability_runs)
            if clean.status in {CheckStatus.BLOCKED, CheckStatus.UNSUPPORTED} or not clean.stable:
                rejected.append(RejectedTargetCandidate(check_id, "BASELINE_NONDETERMINISTIC_OR_BLOCKED"))
            elif clean.status is CheckStatus.FAIL and getattr(clean, "entered_target_code", clean.entered_project_code):
                if len(targets) < config.max_probes:
                    targets.append(replace(check, role=CheckRole.TARGET))
                    from .facet_probes import materialize_return_facet_checks
                    for facet_check in materialize_return_facet_checks(check, goals):
                        if len(targets) >= config.max_probes:
                            rejected.append(RejectedTargetCandidate(facet_check.check_id, "FACET_PROBE_BUDGET"))
                            break
                        facet_run = execute_check(Path(clean_snapshot), facet_check, stability_runs=config.stability_runs)
                        if facet_run.stable and facet_run.entered_target_code is True and facet_run.status in {CheckStatus.PASS, CheckStatus.FAIL}:
                            # The same input scenario already stably fails;
                            # passing facets are required invariants of it.
                            targets.append(replace(facet_check, role=CheckRole.TARGET))
                            deterministic_events.append({"event": "FACET_RECOVERED", "check_id": facet_check.check_id,
                                "source_check_id": check.check_id, "baseline_status": str(facet_run.status)})
                        else:
                            rejected.append(RejectedTargetCandidate(facet_check.check_id, "FACET_BASELINE_NOT_STABLE_OR_BLOCKED"))
                else:
                    rejected.append(RejectedTargetCandidate(check_id, "TARGET_PROBE_QUOTA_EXHAUSTED"))
            elif clean.status is CheckStatus.PASS:
                if check.authority in {"A", "B", "C"}:
                    preservation.append(replace(check, role=CheckRole.PRESERVATION))
                else:
                    rejected.append(RejectedTargetCandidate(
                        check_id, "PRESERVATION_ORACLE_PROVISIONAL",
                    ))
            else:
                rejected.append(RejectedTargetCandidate(check_id, "BASELINE_NOT_TARGET_FAILURE"))
    for raw in checks:
        goal_id = _goal_for_check(tuple(goals), raw)
        check = _execution_check_from_public(raw, goal_id, tuple(goals))
        if check is None:
            continue
        # The same command is one executable scenario even if two metadata
        # records attach different prose expectations.  Run it once and let
        # the explicit typed contract decide its role.
        identity = (check.command, check.cwd, check.environment)
        if identity in seen:
            rejected.append(RejectedTargetCandidate(check.check_id, "DUPLICATE_COMMAND"))
            continue
        seen.add(identity)
        bounded_check = replace(
            check,
            timeout_seconds=min(
                float(check.timeout_seconds), float(config.check_timeout_seconds),
            ),
        )
        clean = execute_check(
            Path(clean_snapshot), bounded_check,
            stability_runs=config.stability_runs,
        )
        if clean.status in {CheckStatus.BLOCKED, CheckStatus.UNSUPPORTED}:
            blocked.append(BlockedTargetCandidate(check.check_id, clean.status))
            continue
        if not clean.stable:
            rejected.append(RejectedTargetCandidate(check.check_id, "BASELINE_NONDETERMINISTIC"))
            continue
        if clean.status is CheckStatus.FAIL:
            if check.authority not in {"A", "B", "C"}:
                rejected.append(RejectedTargetCandidate(check.check_id, "ORACLE_PROVISIONAL"))
            elif not getattr(clean, "entered_target_code", clean.entered_project_code):
                blocked.append(BlockedTargetCandidate(check.check_id, "ENVIRONMENT_BLOCKED"))
            elif not check.goal_id:
                rejected.append(RejectedTargetCandidate(check.check_id, "NO_MATCHING_GOAL"))
            elif len(targets) >= config.max_probes:
                rejected.append(RejectedTargetCandidate(check.check_id, "TARGET_PROBE_QUOTA_EXHAUSTED"))
            else:
                targets.append(replace(bounded_check, role=CheckRole.TARGET))
                # Stage B receives a non-empty clean->P0 diff. Re-execute the
                # already trusted target on P0 twice and retain the semantic
                # observation in the recovery artifact. Stage A deliberately
                # skips this duplicate because working==clean there.
                if not current_diff.empty:
                    working_execution = execute_check(
                        Path(working_snapshot), bounded_check,
                        stability_runs=config.stability_runs,
                    )
                    deterministic_events.append({
                        "time_ns": time.time_ns(),
                        "tool": "run_probe_on_working",
                        "source": "DETERMINISTIC_STAGE_B_REPLAY",
                        "arguments": {"check_id": bounded_check.check_id},
                        "result": {
                            "check_id": bounded_check.check_id,
                            "stable": working_execution.stable,
                            "status": working_execution.status.value,
                            "observation": working_execution.observation.to_dict(),
                            "trace_bundle_id": working_execution.trace.trace_bundle_id,
                        },
                    })
        elif clean.status is CheckStatus.PASS:
            if check.authority in {"A", "B", "C"}:
                preservation.append(replace(bounded_check, role=CheckRole.PRESERVATION))
            else:
                rejected.append(RejectedTargetCandidate(
                    check.check_id, "PRESERVATION_ORACLE_PROVISIONAL",
                ))
        else:
            rejected.append(RejectedTargetCandidate(check.check_id, clean.status))
    # Authority-C executable contracts are ordinary public evidence.  They are
    # baseline-classified exactly like tests, and therefore cannot certify a
    # target merely because a model described a relation.
    for record in tuple(getattr(public_evidence, "api_contracts", ())) + tuple(getattr(public_evidence, "baseline_contracts", ())):
        candidate = _contract_record_check(record, tuple(goals))
        if candidate is None:
            continue
        identity = (candidate.command, candidate.cwd, candidate.environment)
        if identity in seen:
            continue
        seen.add(identity)
        candidate = replace(
            candidate,
            timeout_seconds=min(
                float(candidate.timeout_seconds), float(config.check_timeout_seconds),
            ),
        )
        baseline = execute_check(
            Path(clean_snapshot), candidate,
            stability_runs=config.stability_runs,
        )
        if baseline.status in {CheckStatus.BLOCKED, CheckStatus.UNSUPPORTED}:
            blocked.append(BlockedTargetCandidate(candidate.check_id, baseline.status)); continue
        if not baseline.stable:
            rejected.append(RejectedTargetCandidate(candidate.check_id, "BASELINE_NONDETERMINISTIC")); continue
        if baseline.status is CheckStatus.FAIL and getattr(baseline, "entered_target_code", baseline.entered_project_code) and candidate.goal_id:
            targets.append(replace(candidate, role=CheckRole.TARGET))
        elif baseline.status is CheckStatus.PASS:
            if candidate.authority in {"A", "B", "C"}:
                preservation.append(replace(candidate, role=CheckRole.PRESERVATION))
            else:
                rejected.append(RejectedTargetCandidate(
                    candidate.check_id, "PRESERVATION_ORACLE_PROVISIONAL",
                ))
    agent_events: list[dict[str, Any]] = []
    agent_timed_out = False
    # Recovery must also run for an unresolved/soft goal.  The agent may only
    # propose probes; authority is still determined by the typed evidence
    # checks below, so this cannot manufacture an Oracle.
    missing_facets = tuple(goal for goal in goals if goal.hard
                           and goal.goal_id not in {check.goal_id for check in targets})
    if (not targets or oracle_review or missing_facets) and transport is not None:
        try:
            executor = TargetRecoveryToolExecutor(
                repo_root=Path(clean_snapshot), clean_snapshot=Path(clean_snapshot),
                working_snapshot=Path(working_snapshot), goal_contracts=goals,
                program_slice=dynamic_graph, run_root=Path(run_root), max_probes=config.max_probes,
                stability_runs=config.stability_runs, timeout_seconds=config.check_timeout_seconds,
            )
            context = {
                "goal_contracts": [goal.to_dict() if hasattr(goal, "to_dict") else goal for goal in goals],
                "issue_evidence": [record.to_dict() if hasattr(record, "to_dict") else record for record in getattr(public_evidence, "records", ())],
                "source_hints": [hint.to_dict() if hasattr(hint, "to_dict") else hint for hint in source_hints],
                "graph_context": dynamic_graph.initial_generation_context() if dynamic_graph is not None else None,
                "working_diff": current_diff.canonical_diff,
                "oracle_review": list(oracle_review),
                "missing_required_facets": [goal.to_dict() for goal in missing_facets],
                "existing_checks": [check.to_dict() for check in targets],
                "previous_recovery_decisions": list(dynamic_graph.nodes.get("recovery-history").metadata.get("decisions", ()))
                    if dynamic_graph is not None and "recovery-history" in dynamic_graph.nodes else [],
                "instructions": "Generate probes only; an Oracle must cite Authority A/B/C evidence. If oracle_review identifies a surviving incorrect return, recover missing return assertions from the cited requirement. Do not copy the mutant as an expected value or weaken the requirement.",
            }
            agent_events.extend(TargetRecoveryAgent(
                transport,
                max_turns=max(8, config.max_agent_turns),
                timeout_seconds=config.timeout_seconds,
                provider_request_timeout_seconds=config.provider_request_timeout_seconds,
            ).recover(executor, context))
            agent_timed_out = executor.timed_out
            for probe_id, probe in executor.probes.items():
                if probe.contract is None:
                    continue
                # A model may only suggest a probe. Promote it when its typed
                # contract exactly matches an existing issue-grounded goal
                # (Authority B) and the two clean runs stably violate that
                # contract. Otherwise retain the explicit provisional fate.
                from reachpatch.requirement_graph.facets import match_grounded_probe_goal
                matching_goal = match_grounded_probe_goal(goals, probe)
                if matching_goal is None or len(probe.clean_runs) < config.stability_runs:
                    rejected.append(RejectedTargetCandidate(probe_id, "ORACLE_PROVISIONAL"))
                    continue
                clean_runs = probe.clean_runs
                probe_check = ExecutableCheck(
                    check_id=probe_id, goal_id=str(matching_goal.goal_id),
                    role=CheckRole.TARGET, authority="B",
                    command=("python", "-c", probe.source), cwd=".",
                    environment=(), timeout_seconds=config.check_timeout_seconds,
                    comparator=probe.contract.normalized_comparator,
                    expected=probe.contract.expected, evidence_ids=(),
                    target_symbols=tuple(matching_goal.target_symbols),
                    input_recipe=probe.input_recipe,
                )
                signatures = tuple(
                    semantic_observation_signature(run.observation, probe_check)
                    for run in clean_runs
                )
                if (
                    len(set(signatures)) != 1
                    or probe.contract.matches(clean_runs[-1].observation)
                    or not clean_runs[0].first_project_frame
                ):
                    rejected.append(RejectedTargetCandidate(probe_id, "BASELINE_NOT_STABLE_OR_NOT_TARGET"))
                    continue
                command = ("python", "-c", probe.source)
                promoted = ExecutableCheck(
                    check_id=stable_id("agent-target-check", probe_id, probe.contract.normalized()),
                    goal_id=str(matching_goal.goal_id), role=CheckRole.TARGET,
                    authority=str(getattr(matching_goal, "authority", "B")),
                    command=command, cwd=".", environment=(),
                    timeout_seconds=config.check_timeout_seconds,
                    comparator=probe.contract.normalized_comparator,
                    expected=probe.contract.expected,
                    evidence_ids=tuple(stable_id(
                        "goal-evidence", matching_goal.goal_id, span.start, span.end,
                    ) for span in matching_goal.evidence_spans),
                    target_symbols=tuple(matching_goal.target_symbols),
                    input_recipe=probe.input_recipe,
                )
                verified = execute_check(Path(clean_snapshot), promoted, stability_runs=config.stability_runs)
                if verified.stable and verified.status is CheckStatus.FAIL and verified.entered_target_code is True:
                    targets.append(promoted)
                else:
                    rejected.append(RejectedTargetCandidate(probe_id, "STRICT_TARGET_BINDING_OR_STABILITY_FAILED"))
        except Exception as exc:
            rejected.append(RejectedTargetCandidate("target-recovery-agent", type(exc).__name__))
    hard_goal_ids = {str(getattr(goal, "goal_id", "")) for goal in goals if bool(getattr(goal, "hard", False))}
    unresolved = tuple(sorted(hard_goal_ids - {item.goal_id for item in targets if item.goal_id}))
    reasons: list[str] = []
    if not targets:
        reasons.append("NO_TRUSTED_TARGET")
    if blocked:
        reasons.append("ENVIRONMENT_OR_EXECUTION_BLOCKED")
    if any(item.reason in {"BASELINE_NONDETERMINISTIC", "BASELINE_NOT_STABLE_OR_NOT_TARGET"}
           for item in rejected):
        reasons.append("BASELINE_NOT_STABLE")
    if any(item.reason in {"ORACLE_PROVISIONAL", "PRESERVATION_ORACLE_PROVISIONAL"}
           for item in rejected):
        reasons.append("ORACLE_AUTHORITY_UNSUPPORTED")
    if any(item.reason == "NO_MATCHING_GOAL" for item in rejected):
        reasons.append("EVIDENCE_GOAL_ALIGNMENT_MISSING")
    if agent_timed_out:
        reasons.append("AGENT_TIMEOUT")
    if any(event.get("tool") == "transport" and event.get("error") for event in agent_events):
        reasons.append("AGENT_TRANSPORT_FAILURE")
    if targets:
        reasons.append("TARGET_RECOVERY_SUCCEEDED")
    if dynamic_graph is not None:
        from reachpatch.reach_avoid.dynamic_reach_avoid_graph import GraphNodeKind
        prior = dynamic_graph.nodes.get("recovery-history")
        decisions = tuple(prior.metadata.get("decisions", ())) if prior else ()
        decision = {"target_discovered": bool(targets), "baseline_stable": bool(targets),
            "required_facets_covered": not unresolved, "unresolved_goal_ids": unresolved,
            "rejected": [item.to_dict() for item in rejected], "reasons": reasons,
            "probe_contracts": [{"goal_id": check.goal_id, "command_hash": stable_id("command", check.command),
                                 "comparator": check.comparator, "expected": check.expected} for check in targets]}
        dynamic_graph.add_node(GraphNodeKind.OBSERVATION, node_id="recovery-history",
            status="RECOVERED" if not unresolved and targets else "EVIDENCE_GAP",
            metadata={"decisions": (*decisions, decision)})
    return TargetRecoveryResult(
        rejected_candidates=tuple(rejected), blocked_candidates=tuple(blocked),
        target_checks=tuple(targets), preservation_checks=tuple(preservation),
        unresolved_goal_ids=unresolved, timed_out=(
            agent_timed_out
            or any(
                "timeout" in str(item.get("error", "")).casefold()
                for item in agent_events
            )
        ),
        agent_events=tuple((*deterministic_events, *agent_events)),
        attempt_count=len(deterministic_events) + len(agent_events),
        exhausted_reasons=tuple(dict.fromkeys(reasons)),
    )


def materialize_diff_checks(
    working_snapshot: Path,
    current_diff: str,
    active_failure: Any,
    target_checks: Sequence[ExecutableCheck],
    dynamic_graph: Any | None = None,
    previous_checks: Sequence[ExecutableCheck] = (),
) -> tuple[ExecutableCheck, ...]:
    """Return only executable, oracle-backed checks adjacent to the diff.

    The generator may provide a bounded dynamic graph for source context, but
    this queue is deliberately conservative: a check is materialized only
    from an existing A/B/C executable contract.  We never invent a target
    oracle or turn static predicates into certification evidence.
    """
    # The graph is the source of challenge context.  It is intentionally not
    # discarded: callers can attach branch/value-flow IDs from graph nodes to
    # each generated executable check for audit and validation scheduling.
    existing = {
        (tuple(item.command), item.comparator, repr(item.expected), repr(getattr(item, "input_recipe", None)))
        for item in previous_checks
    }
    result: list[ExecutableCheck] = list(previous_checks)
    command = tuple(getattr(active_failure, "command", ()))
    if not command:
        return tuple(result)
    # A challenge is meaningful only when the current diff actually touches a
    # predicate/return/exception boundary.  Do not manufacture a duplicate
    # target check for an unrelated textual edit.
    added_lines = tuple(
        line[1:] for line in str(current_diff).splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    predicate_tokens = (
        "if " , "elif " , "for " , "while " , "isinstance",
        "==", "!=", "<=", ">=", " in ", "return",
        "raise", "except", " and ", " or ", " not ",
    )
    if not any(token in line for line in added_lines for token in predicate_tokens):
        return tuple(result)

    def diff_kind() -> str:
        text = "\n".join(added_lines).casefold()
        if "isinstance" in text:
            return "ISINSTANCE"
        if " in " in text:
            return "MEMBERSHIP"
        if any(token in text for token in ("raise", "except")):
            return "EXCEPTION"
        if "return" in text:
            return "RETURN_SHAPE"
        if any(token in text for token in ("==", "!=", "<=", ">=", "<", ">")):
            return "BOUNDARY"
        if any(token in text for token in (" and ", " or ", " not ")):
            return "TRUTHINESS"
        return "PREDICATE"

    kind = diff_kind()
    for check in target_checks:
        if check.authority not in {"A", "B", "C"} or tuple(check.command) != command:
            continue
        recipe = check.input_recipe
        variants: list[dict[str, Any]] = []
        if isinstance(recipe, dict):
            raw_variants = recipe.get("variants") or recipe.get("challenge_commands") or recipe.get("adjacent_inputs") or ()
            if isinstance(raw_variants, (tuple, list)):
                for item in raw_variants:
                    if isinstance(item, dict):
                        variants.append(dict(item))
                    elif isinstance(item, (tuple, list)):
                        variants.append({"command": tuple(str(part) for part in item)})
        if dynamic_graph is not None and not variants:
            graph_nodes = getattr(dynamic_graph, "nodes", {})
            for branch in tuple(graph_nodes.values()):
                branch_kind = getattr(getattr(branch, "kind", None), "value", getattr(branch, "kind", ""))
                if str(branch_kind).upper() != "BRANCH":
                    continue
                predicate = str(getattr(branch, "metadata", {}).get("predicate", getattr(branch, "source_span", "")))
                if not predicate or (getattr(branch, "file", None) and not any(str(branch.file) == str(path) for path in getattr(check, "evidence_ids", ()) + getattr(check, "target_symbols", ()) )):
                    # A branch without an explicit file match is still useful
                    # when the diff contains executable predicate text.
                    if not any(token in predicate for token in predicate_tokens):
                        continue
                variants.extend({
                    "command": tuple(check.command), "kind": kind,
                    "source_branch_id": getattr(branch, "node_id", ""),
                    "source_value_flow_ids": tuple(
                        edge.edge_id for edge in getattr(dynamic_graph, "edges", {}).values()
                        if edge.source_id == getattr(branch, "node_id", "") or edge.target_id == getattr(branch, "node_id", "")
                    ), "variant_value": value,
                } for value in (("None" if "none" in predicate.casefold() else "0"), "1", "-1"))
        # A challenge must be a genuinely different executable input. The
        # target command itself is already run by the main queue and is never
        # duplicated as a CHALLENGE check.
        for index, variant in enumerate(variants):
            oracle = variant.get("oracle")
            if (not isinstance(oracle, dict) or not oracle.get("comparator")
                or "expected" not in oracle or variant.get("authority") not in {"A", "B", "C"}
                or not variant.get("evidence_ids")):
                continue
            raw_command = variant.get("command")
            if not isinstance(raw_command, (tuple, list)) or not raw_command:
                continue
            variant_command = tuple(str(part) for part in raw_command)
            if variant_command == tuple(check.command):
                continue
            key = (variant_command, check.comparator, repr(check.expected), repr(variant))
            if key in existing:
                continue
            result.append(replace(
                check,
                check_id=stable_id(
                    "diff-adjacent-check", check.check_id, kind,
                    variant_command, index,
                ),
                role=CheckRole.CHALLENGE,
                command=variant_command,
                authority=variant["authority"], comparator=oracle["comparator"], expected=oracle["expected"],
                evidence_ids=tuple(variant["evidence_ids"]),
                input_recipe={
                    "kind": str(variant.get("kind", kind)),
                    "source_check_id": check.check_id,
                    "variant_index": index,
                    "source_branch_id": variant.get("source_branch_id"),
                    "source_value_flow_ids": tuple(variant.get("source_value_flow_ids", ())),
                    "input_recipe": variant.get("variant_value", variant),
                },
            ))
            existing.add(key)
    return tuple(result)


def materialize_graph_guided_challenges(
    repo_root: Path,
    graph: DynamicReachAvoidGraph,
    checkpoint: CheckpointState,
    observations: tuple[Any, ...] | list[Any] = (),
) -> tuple[Any, ...]:
    """Public validation/challenge projection owned by the unified graph."""
    return _materialize_graph_guided_challenges(repo_root, graph, checkpoint, observations)
