from __future__ import annotations

from dataclasses import dataclass, field
import ast
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.evidence import (
    ExecutableOracle, ObservationContract, OutcomeStatus, PublicEvidence,
    issue_witnesses,
)
from reachpatch.models.graphs import BindingGap, ChallengeCell, ChallengeStatus, ExecutableScenario, InputRecipe, RequirementGraph
from reachpatch.reach_avoid.semantics import scenario_semantic_key
from .trace import run_trace


@dataclass(frozen=True, slots=True)
class TargetRecoveryConfig(SerializableRecord):
    max_probes: int = 6
    stability_runs: int = 2
    timeout_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class RejectedTargetCandidate(SerializableRecord):
    candidate_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class TargetRecoveryResult(SerializableRecord):
    scenarios: tuple[ExecutableScenario, ...]
    challenge_cells: tuple[ChallengeCell, ...]
    gaps: tuple[BindingGap, ...]
    rejected_candidates: tuple[RejectedTargetCandidate, ...]
    timed_out: bool = False
    agent_events: tuple[dict[str, Any], ...] = ()


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
                 working_snapshot: Path, requirement_graph: RequirementGraph,
                 program_slice: Any, run_root: Path, max_probes: int = 6,
                 stability_runs: int = 2, timeout_seconds: float = 120.0):
        self.repo_root = Path(repo_root).resolve()
        self.clean_snapshot = Path(clean_snapshot).resolve()
        self.working_snapshot = Path(working_snapshot).resolve()
        self.requirement_graph = requirement_graph
        self.program_slice = program_slice
        self.run_root = Path(run_root).resolve()
        self.probe_root = self.run_root / "target_recovery_probes"
        self.max_probes = max(1, int(max_probes))
        self.stability_runs = max(2, int(stability_runs))
        self.timeout_seconds = float(timeout_seconds)
        self.probes: dict[str, _RecoveryProbe] = {}
        self.events: list[dict[str, Any]] = []
        self.finished = False

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
        if not matches:
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
        if len(self.probes) >= self.max_probes:
            raise RuntimeError("target recovery probe budget exhausted")
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
        trace = run_trace(tree, ("python", str(probe.source_path)), timeout_seconds=self.timeout_seconds, trace_enabled=True)
        runs.append(trace)
        observation = trace.observation.to_dict() if hasattr(trace.observation, "to_dict") else dict(trace.observation)
        result = {"probe_id": probe.probe_id, "tree": destination, "run_index": len(runs),
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
            clean_stable = len({(item.observation.status, item.observation.return_code, item.observation.stdout, item.observation.stderr, item.observation.exception) for item in probe.clean_runs}) == 1
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
    "type": "function", "function": {"name": name,
    "description": "Restricted target recovery operation.",
    "parameters": {"type": "object", "properties": {
        "symbol": {"type": "string"}, "path": {"type": "string"},
        "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
        "name": {"type": "string"}, "source": {"type": "string"},
        "probe_id": {"type": "string"}, "contract": {"type": "object"},
        "requirement_id": {"type": ["string", "null"]},
        "input_recipe": {"type": "object"}, "authority": {"type": "string"},
        "summary": {"type": "string"},
    }}}} for name in _TARGET_RECOVERY_TOOL_NAMES)


class TargetRecoveryAgent:
    """Run a bounded DeepSeek recovery conversation with only safe tools."""

    def __init__(self, transport: Any, *, max_turns: int = 16, max_tokens: int = 12000,
                 timeout_seconds: float = 1200.0):
        self.transport = transport
        self.max_turns = max(1, int(max_turns))
        self.max_tokens = int(max_tokens)
        self.timeout_seconds = float(timeout_seconds)

    def recover(self, executor: TargetRecoveryToolExecutor, context: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        messages = [
            {"role": "system", "content": (
                "You are a restricted target recovery agent. You may only use the supplied tools. "
                "Do not edit project files, read hidden files, use the network, or claim an Oracle from prose. "
                "A target scenario is accepted only after two stable clean runs violate its structured contract.")},
            {"role": "user", "content": json.dumps(context, sort_keys=True, default=str)},
        ]
        deadline = time.monotonic() + self.timeout_seconds
        for _ in range(self.max_turns):
            if executor.finished or time.monotonic() >= deadline:
                break
            try:
                message = self.transport.complete(
                    messages, tools=TARGET_RECOVERY_TOOL_SCHEMAS,
                    max_tokens=self.max_tokens,
                    timeout_seconds=max(1.0, deadline - time.monotonic()),
                    tool_choice="required",
                )
            except Exception as exc:
                executor._record("transport", {}, error=exc)
                break
            if not isinstance(message, dict):
                break
            messages.append(message)
            calls = message.get("tool_calls") or ()
            if not calls:
                continue
            for call in calls[:1]:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                name = str(function.get("name", ""))
                raw = function.get("arguments", {})
                arguments = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
                try:
                    result = executor.invoke(name, arguments)
                except Exception as exc:
                    result = {"error": type(exc).__name__, "message": str(exc)}
                messages.append({"role": "tool", "tool_call_id": call.get("id", name),
                                 "name": name, "content": json.dumps(result, default=str)})
                if executor.finished:
                    break
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
    if "exception" in expected:
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


def _stable_run(tree: Path, scenario: ExecutableScenario, runs: int):
    traces = tuple(run_trace(tree, scenario.command, cwd=scenario.cwd, environment=scenario.environment, timeout_seconds=scenario.timeout_seconds, trace_enabled=index == 0) for index in range(runs))
    signatures = {(trace.observation.status, trace.observation.return_code, trace.observation.stdout, trace.observation.stderr, trace.observation.exception) for trace in traces}
    return traces[-1], runs if len(signatures) == 1 else 1


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
    leaves = tuple(requirement_graph.leaves.values())
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
                recipe.command, ".", (), config.timeout_seconds,
            )
            semantic = scenario_semantic_key(
                requirement_contract_id=requirement.expected_observation.contract_id,
                role="TARGET", input_recipe=recipe,
                observation_contract=contract,
            )
            if semantic in seen:
                continue
            seen.add(semantic)
            clean_trace, clean_runs = _stable_run(Path(clean_snapshot), scenario, config.stability_runs)
            working_trace, working_runs = _stable_run(Path(working_snapshot), scenario, config.stability_runs)
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
            float(getattr(check, "timeout_seconds", config.timeout_seconds)),
        )
        semantic = scenario_semantic_key(
            requirement_contract_id=str(getattr(check, "check_id", "")),
            role="PRESERVATION", input_recipe=recipe,
            observation_contract=contract,
        )
        if semantic in seen:
            continue
        seen.add(semantic)
        baseline, baseline_runs = _stable_run(Path(clean_snapshot), scenario, config.stability_runs)
        working, working_runs = _stable_run(Path(working_snapshot), scenario, config.stability_runs)
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
        stability_runs=config.stability_runs, timeout_seconds=config.timeout_seconds,
    )
    leaves = tuple(leaf for leaf in requirement_graph.leaves.values()
                   if not leaf.preservation and leaf.hard)
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
        clean_stable = len({(run.observation.status, run.observation.return_code, run.observation.stdout, run.observation.stderr, run.observation.exception) for run in probe.clean_runs}) == 1
        working_stable = len({(run.observation.status, run.observation.return_code, run.observation.stdout, run.observation.stderr, run.observation.exception) for run in probe.working_runs}) == 1
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
            command, ".", (), config.timeout_seconds,
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
    config = config or TargetRecoveryConfig()
    scenarios: list[ExecutableScenario] = []
    cells: list[ChallengeCell] = []
    gaps: list[BindingGap] = []
    rejected: list[RejectedTargetCandidate] = []
    seen: set[str] = set()
    target_leaves = tuple(item for item in requirement_graph.leaves.values() if not item.preservation and item.hard)
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
        scenario = ExecutableScenario(stable_id("target-scenario", requirement.requirement_id, command, contract.normalized()), command, str(getattr(check, "cwd", ".")), tuple(getattr(check, "environment", ())), float(getattr(check, "timeout_seconds", config.timeout_seconds)))
        semantic = scenario_semantic_key(requirement_contract_id=requirement.expected_observation.contract_id, role="TARGET", input_recipe=recipe, observation_contract=contract)
        if semantic in seen:
            continue
        seen.add(semantic)
        clean_trace, clean_runs = _stable_run(Path(clean_snapshot), scenario, config.stability_runs)
        working_trace, working_runs = _stable_run(Path(working_snapshot), scenario, config.stability_runs)
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
    return TargetRecoveryResult(
        tuple(scenarios), tuple(cells), tuple(gaps), tuple(rejected), timed_out,
        agent_events,
    )
