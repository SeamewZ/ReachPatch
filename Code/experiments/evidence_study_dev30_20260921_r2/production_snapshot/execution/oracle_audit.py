"""Bounded return-oracle interventions; no edits to project or test files."""
from __future__ import annotations

import ast
import re
import time
from pathlib import Path
from typing import Any, Sequence

from reachpatch.models.base import content_hash, stable_id
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import GraphNodeKind, GraphEdgeKind
from .checks import observation_matches_check
from .trace import run_trace
from reachpatch.reach_avoid.graph_policy import contract_obligation_id


def uncovered_oracle_gaps(reports: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(item for item in reports if item.get("blocks_certification", item["status"] not in {
        "DISCRIMINATES_SAMPLED_MUTANTS", "AUDIT_NOT_APPLICABLE"}))


def _incompatible_returns(goal: Any) -> tuple[Any, ...]:
    comparator = str(goal.comparator).upper()
    if comparator in {"RAISES", "NOT_RAISES"}:
        return ()  # Exception-only goals do not constrain the return value.
    if comparator in {"EQUALS", "ORDER_EQUALS"}:
        return tuple(value for value in (None, [], 0) if value != goal.expected)
    if comparator == "LENGTH_EQUALS":
        return (None,) if goal.expected == 0 else (None, [])
    if comparator == "TYPE_IS":
        return tuple(value for value in (None, [], 0) if type(value).__name__ != str(goal.expected))
    quotes = " ".join(str(getattr(span, "quote", "")) for span in goal.evidence_spans)
    if re.search(r"\b(?:should|must)\s+return\s+[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", quotes):
        # Explicit qualified-object identity (e.g. a public type singleton).
        # Primitive replacement objects cannot satisfy that return contract.
        return (None, [], 0)
    # Only a deliberately narrow explicit return facet. No inference of shape,
    # ordering or values from the implementation or a model's preferred patch.
    if re.search(r"\b(?:should|must|expected|instead)\b[^.\n]*\breturn\s+(?:an?\s+)?empty\s+(?:lists?|arrays?|tuples?)", quotes, re.I):
        return (None, 0)
    return ()  # No supported incompatible return intervention.


def _target_binding(tree: Path, graph: Any, check: Any) -> tuple[str, str] | None:
    matches: set[tuple[str, str]] = set()
    exact_nodes = {node.node_id for node in graph.nodes.values() if node.kind is GraphNodeKind.SYMBOL
                   and node.symbol and any(str(symbol) == node.symbol or str(symbol).endswith("." + node.symbol)
                                           for symbol in check.target_symbols)}
    for node in graph.nodes.values():
        if node.kind is not GraphNodeKind.SYMBOL or not node.file or not node.symbol or node.status in {"RETIRED_SOURCE", "HISTORICAL_SOURCE"}:
            continue
        if exact_nodes and node.node_id not in exact_nodes:
            continue
        if not any(node.symbol == symbol or node.symbol.rsplit(".", 1)[-1] == str(symbol).rsplit(".", 1)[-1]
                   for symbol in check.target_symbols):
            continue
        source = (tree / node.file).resolve()
        if not source.is_relative_to(tree.resolve()) or not source.is_file():
            continue
        try:
            parsed = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        module_parts = [source.stem] if source.stem != "__init__" else []
        directory = source.parent
        while directory != tree and (directory / "__init__.py").is_file():
            module_parts.insert(0, directory.name)
            directory = directory.parent
        module = ".".join(module_parts)
        def visit(body: Sequence[ast.AST], prefix: str = "") -> None:
            for definition in body:
                if isinstance(definition, ast.ClassDef):
                    visit(definition.body, prefix + definition.name + ".")
                elif isinstance(definition, ast.FunctionDef):
                    qualified = prefix + definition.name
                    if (definition.lineno == node.line_start
                        and (qualified == node.symbol or definition.name == node.symbol)):
                        matches.add((module, qualified))
        visit(parsed.body)
    return next(iter(matches)) if len(matches) == 1 else None


def _intervention_command(check: Any, binding: tuple[str, str], value: Any, marker: str) -> tuple[str, ...] | None:
    command = tuple(check.command)
    if not command or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(command[0]).name):
        return None  # Shell/foreign-language commands need a different adapter.
    if len(command) >= 3 and command[1] == "-c":
        body = f"exec(compile({command[2]!r}, '<oracle-probe>', 'exec'), {{'__name__': '__main__'}})"
    elif len(command) >= 3 and command[1] == "-m":
        body = f"sys.argv={list(command[2:])!r}; runpy.run_module({command[2]!r}, run_name='__main__', alter_sys=True)"
    elif len(command) >= 2 and command[1].endswith(".py"):
        body = f"sys.argv={list(command[1:])!r}; runpy.run_path({command[1]!r}, run_name='__main__')"
    else:
        return None  # Unsupported interpreter flags cannot be silently dropped.
    module, symbol = binding
    script = (
        "import importlib, sys, runpy, os, inspect\n"
        f"owner = importlib.import_module({module!r})\n"
        f"parts = {symbol.split('.')!r}\n"
        "for part in parts[:-1]: owner = getattr(owner, part)\n"
        "original = inspect.getattr_static(owner, parts[-1])\n"
        "def replacement(*args, **kwargs):\n"
        f"    os.write(2, {marker.encode()!r})\n"
        f"    return {value!r}\n"
        "if isinstance(original, staticmethod): replacement = staticmethod(replacement)\n"
        "elif isinstance(original, classmethod): replacement = classmethod(replacement)\n"
        "elif isinstance(original, property): replacement = property(replacement)\n"
        "setattr(owner, parts[-1], replacement)\n" + body
    )
    return (command[0], "-c", script)


def audit_return_oracles(tree: Path, graph: Any, goals: Sequence[Any], checks: Sequence[Any],
                         results: Sequence[Any], *, patch_hash: str,
                         wall_seconds: float = 60.0, max_interventions: int = 4) -> tuple[dict[str, Any], ...]:
    """A survivor exposes weak evidence; killed mutants do not prove correctness.

    Only already-passing trusted probes are audited. Each intervention runs in
    a fresh process twice, and must actually replace an executed target. Setup
    failures/timeouts are inconclusive, never successful mutant kills.
    """
    deadline = time.monotonic() + max(0.0, wall_seconds)
    executions = {item.check_id: item for item in results}
    reports: list[dict[str, Any]] = []
    count = 0
    for check in checks:
        # A preservation check may share the operation/goal binding but has
        # its own input contract. Target return mutations are not evidence
        # against that different partition's preservation oracle.
        if str(check.role) != "TARGET":
            continue
        execution = executions.get(check.check_id)
        if not check.trusted or execution is None or not execution.stable or str(execution.status) != "PASS":
            continue
        goal = next((item for item in goals if item.goal_id == check.goal_id and item.hard), None)
        if goal is None:
            continue
        # ``RELATION_HOLDS`` is certified by the executable probe's own
        # non-constant assertion (the recovery layer rejects print-only and
        # tautological probes before registration).  This audit can only
        # replace a target's *return value*; it cannot preserve the injected
        # dependency, exception path, state transition, or other relation
        # established by such a probe.  Treating that unsupported mutation as
        # an oracle gap used to downgrade a stable target PASS to
        # KEEP_REPAIRING even though the mutation was unrelated to the
        # contract under test.
        if str(goal.comparator).upper() == "RELATION_HOLDS":
            reports.append({
                "obligation_id": contract_obligation_id(goal, check),
                "goal_id": goal.goal_id,
                "check_id": check.check_id,
                "status": "AUDIT_NOT_APPLICABLE",
                "blocks_certification": False,
                "reason": (
                    "Relation contract is enforced by a grounded self-checking "
                    "probe; return-only intervention is not semantically valid."
                ),
            })
            continue
        if goal.comparator in {"EXIT_ZERO", "NOT_RAISES"} and any(
            item.hard and item.parent_goal_id == goal.goal_id
            and item.facet_kind in {"RETURN_TYPE", "RETURN_LENGTH", "RETURN_STRUCTURE", "SEMANTIC_RESULT"}
            for item in goals
        ):
            reports.append({"obligation_id": contract_obligation_id(goal, check),
                "goal_id": goal.goal_id, "check_id": check.check_id,
                "status": "AUDIT_NOT_APPLICABLE", "blocks_certification": False,
                "reason": "Process-only facet; separate required return facets control validation closure."})
            continue
        values = _incompatible_returns(goal)
        obligation_id = contract_obligation_id(goal, check)
        directly_covered = (str(check.comparator).upper() == str(goal.comparator).upper()
                            and check.expected == goal.expected and str(goal.comparator).upper() != "EXIT_ZERO")
        if not values:
            exception_only = str(goal.comparator).upper() in {"RAISES", "NOT_RAISES"}
            process_only = str(goal.comparator).upper() == "EXIT_ZERO" and not re.search(
                r"\breturn\b|\bshape\b|\bstate\b", " ".join(span.quote for span in goal.evidence_spans), re.I)
            reports.append({"obligation_id": obligation_id, "goal_id": goal.goal_id, "check_id": check.check_id,
                "status": "AUDIT_NOT_APPLICABLE" if exception_only or process_only else "AUDIT_UNSUPPORTED",
                "blocks_certification": not (directly_covered or exception_only or process_only),
                "reason": "No supported incompatible-return mutation; existing typed evidence is assessed separately."})
            continue
        audit_id = stable_id("oracle-audit", patch_hash, check.to_dict(), goal.to_dict())
        cached = graph.nodes.get(audit_id)
        if cached and cached.status in {"SURVIVOR", "DISCRIMINATES_SAMPLED_MUTANTS"}:
            reports.append(cached.metadata)
            continue
        binding = _target_binding(tree, graph, check)
        mutations: list[dict[str, Any]] = []
        for value in values:
            marker = f"REACHPATCH_ORACLE_HIT_{content_hash((audit_id, value))[:16]}\n"
            command = _intervention_command(check, binding, value, marker) if binding else None
            if command is None or count >= max_interventions or time.monotonic() >= deadline:
                mutations.append({"value": value, "status": "INCONCLUSIVE",
                                  "reason": "UNRESOLVED_BINDING_OR_COMMAND" if command is None else "AUDIT_BUDGET"})
                break
            count += 1
            runs = []
            for _ in range(2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                traced = run_trace(tree, command, cwd=check.cwd, environment=check.environment,
                                   timeout_seconds=min(float(check.timeout_seconds), remaining, 15.0), trace_enabled=False)
                observation = traced.observation
                runs.append({"hit": marker in observation.stderr,
                             "matches": observation_matches_check(observation, check),
                             "timeout": observation.exception == "TIMEOUT",
                             "observation": observation.to_dict()})
            stable = len(runs) == 2 and all(run["hit"] and not run["timeout"] for run in runs)
            outcome = ("SURVIVOR" if stable and all(run["matches"] for run in runs)
                       else "KILLED" if stable and all(not run["matches"] for run in runs)
                       else "INCONCLUSIVE")
            mutations.append({"value": value, "status": outcome, "command": command,
                              "cwd": check.cwd, "environment": check.environment, "runs": runs})
        status = ("SURVIVOR" if any(item["status"] == "SURVIVOR" for item in mutations)
                  else "DISCRIMINATES_SAMPLED_MUTANTS" if mutations and all(item["status"] == "KILLED" for item in mutations)
                  else "INCONCLUSIVE")
        report = {"audit_id": audit_id, "patch_hash": patch_hash, "check_id": check.check_id,
                  "obligation_id": obligation_id,
                  "blocks_certification": status == "SURVIVOR" or (status == "INCONCLUSIVE" and not directly_covered),
                  "goal_id": goal.goal_id, "evidence_span_ids": goal.evidence_span_ids,
                  "status": status, "mutations": mutations,
                  "reason": "Sampled return interventions are an oracle adequacy check, not a correctness certificate."}
        graph.add_node(GraphNodeKind.OBSERVATION, node_id=audit_id, status=status, metadata=report)
        oracle_id = stable_id("oracle", check.check_id)
        if oracle_id in graph.nodes:
            graph.add_edge(GraphEdgeKind.DERIVED_FROM, audit_id, oracle_id, static_or_dynamic="dynamic")
        graph.record_update("ORACLE_AUDIT", audit_id=audit_id, status=status, check_id=check.check_id)
        reports.append(report)
    return tuple(reports)
