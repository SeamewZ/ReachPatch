"""Graph-selected, non-certifying input interventions across sibling snapshots."""
from __future__ import annotations

import ast
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from reachpatch.models.base import content_hash, stable_id
from .trace import run_trace


def instantiate_probe_command(command: Sequence[str], symbol_source: str, target: str,
                               recipe: dict[str, Any]) -> tuple[str, ...]:
    """Instantiate a single unambiguous inline call; refuse unsafe rewrites.

    Existing assertions are not transferred to a new input. This command only
    observes return shape/type or an exception; it has no expected result.
    """
    if len(command) != 3 or command[1] != "-c" or "python" not in Path(command[0]).name:
        return ()  # NO_INLINE_PYTHON_ADAPTER
    try:
        source = ast.parse(symbol_source)
        probe = ast.parse(command[2])
        parameter_expression = ast.parse(str(recipe.get("parameter", "")), mode="eval").body
    except SyntaxError:
        return ()  # UNPARSEABLE_PARAMETER_OR_PROBE
    parameter = (parameter_expression.id if isinstance(parameter_expression, ast.Name)
                 else parameter_expression.args[0].id if isinstance(parameter_expression, ast.Call)
                 and isinstance(parameter_expression.func, ast.Name) and parameter_expression.func.id == "len"
                 and len(parameter_expression.args) == 1 and isinstance(parameter_expression.args[0], ast.Name)
                 else "")
    definitions = [node for node in ast.walk(source) if isinstance(node, ast.FunctionDef)
                   and node.name == target.rsplit(".", 1)[-1]]
    if not parameter or len(definitions) != 1 or "value" not in recipe:
        return ()  # NO_UNAMBIGUOUS_PARAMETER_BINDING
    parameters = [argument.arg for argument in (*definitions[0].args.posonlyargs, *definitions[0].args.args)]
    if parameters and parameters[0] in {"self", "cls"}:
        parameters.pop(0)
    calls = [node for node in ast.walk(probe) if isinstance(node, ast.Call)
             and ((isinstance(node.func, ast.Name) and node.func.id == target.rsplit(".", 1)[-1])
                  or (isinstance(node.func, ast.Attribute) and node.func.attr == target.rsplit(".", 1)[-1]))]
    if len(calls) != 1:
        return ()  # MULTIPLE_OR_INDIRECT_TARGET_CALLS
    call = calls[0]
    owner_index = next((index for index, statement in enumerate(probe.body) if call in ast.walk(statement)), -1)
    if owner_index < 0 or not isinstance(probe.body[owner_index], (ast.Expr, ast.Assign, ast.Assert)):
        return ()  # TARGET_CALL_INSIDE_CONTROL_FLOW
    value = recipe["value"]
    if isinstance(parameter_expression, ast.Call):
        if type(value) is not int or not 0 <= value <= 256:
            return ()  # INPUT_LENGTH_OUTSIDE_BOUNDED_RECIPE
        value = [0] * value
    replacement = ast.parse(repr(value), mode="eval").body
    keyword = next((item for item in call.keywords if item.arg == parameter), None)
    if keyword is not None:
        keyword.value = replacement
    elif parameter in parameters and parameters.index(parameter) < len(call.args):
        if any(isinstance(argument, ast.Starred) for argument in call.args):
            return ()  # UNRESOLVED_SPLAT_ARGUMENTS
        call.args[parameters.index(parameter)] = replacement
    else:
        return ()  # PARAMETER_NOT_BOUND_BY_CALL
    prefix = ast.unparse(ast.Module(body=probe.body[:owner_index], type_ignores=[]))
    script = prefix + "\nimport json as _rp_json\ntry:\n"
    script += "    _rp_value = " + ast.unparse(call) + "\n"
    script += (
        "    _rp_summary = {'type': type(_rp_value).__name__, 'none': _rp_value is None}\n"
        "    if type(_rp_value) in (bool, int, float): _rp_summary['scalar'] = _rp_value\n"
        "    if type(_rp_value) in (list, tuple, dict, str, bytes, set, frozenset):\n"
        "        _rp_summary['length'] = len(_rp_value)\n"
        "        _rp_summary['truthy'] = bool(_rp_value)\n"
        "    if type(_rp_value) in (list, tuple):\n"
        "        _rp_summary['element_types'] = [type(item).__name__ for item in _rp_value[:8]]\n"
        "except Exception as _rp_error:\n"
        "    _rp_summary = {'exception': type(_rp_error).__name__}\n"
        "print('REACHPATCH_PROBE_OBSERVATION=' + _rp_json.dumps(_rp_summary, sort_keys=True))\n"
    )
    return (str(command[0]), "-c", script)


def execute_sibling_probes(graph: Any, cells: Sequence[Any], snapshots: Mapping[str, Path],
                            *, clean: Path, wall_seconds: float = 30.0,
                            max_probes: int = 2) -> tuple[dict[str, Any], ...]:
    """Measure disagreements. Neither agreement nor majority grants authority."""
    from reachpatch.reach_avoid.dynamic_reach_avoid_graph import GraphNodeKind, GraphEdgeKind
    deadline = time.monotonic() + max(0.0, wall_seconds)
    reports: list[dict[str, Any]] = []
    if not snapshots:
        return ()  # NO_CHECKPOINT_TO_COMPARE_WITH_CLEAN
    for cell in (item for item in cells if item.command and item.status == "EXPLORATION_ONLY"):
        if len(reports) >= max_probes or time.monotonic() >= deadline:
            break
        probe_key = stable_id("sibling-probe", cell.command, sorted(snapshots))
        if probe_key in graph.nodes:
            continue
        outcomes: dict[str, Any] = {}
        source_check_id = cell.input_recipe.get("source_check_id")
        obligation = next((node for node in graph.nodes.values()
                           if node.kind is GraphNodeKind.OBLIGATION and node.metadata.get("check_id") == source_check_id), None)
        cwd = str(obligation.metadata.get("cwd", ".")) if obligation else "."
        environment = tuple(obligation.metadata.get("environment", ())) if obligation else ()
        for checkpoint_id, tree in (("CLEAN", clean), *sorted(snapshots.items())):
            samples = []
            for _ in range(2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                trace = run_trace(tree, cell.command, cwd=cwd, environment=environment,
                                  timeout_seconds=min(5.0, remaining), trace_enabled=False)
                lines = [line.partition("=")[2] for line in trace.observation.stdout.splitlines()
                         if line.startswith("REACHPATCH_PROBE_OBSERVATION=")]
                value = json.loads(lines[-1]) if lines else None
                samples.append({"value": value, "observation": trace.observation.to_dict()})
            stable = (len(samples) == 2 and all(item["value"] is not None for item in samples)
                      and samples[0]["value"] == samples[1]["value"])
            outcomes[checkpoint_id] = {"stable": stable, "value": samples[-1]["value"] if samples else None,
                                       "samples": samples}
        distinct = {content_hash(value["value"]) for key, value in outcomes.items()
                    if key != "CLEAN" and value["stable"]}
        report = {"probe_id": probe_key, "challenge_id": cell.challenge_id,
                  "source_branch_id": cell.source_branch_id, "source_value_flow_ids": cell.source_value_flow_ids,
                  "command": cell.command, "cwd": cwd, "environment": environment,
                  "outcomes": outcomes, "disagreement": len(distinct) > 1,
                  "authority": "PROVISIONAL", "certifying": False}
        graph.add_node(GraphNodeKind.OBSERVATION, node_id=probe_key, status="EXPLORATORY", metadata=report)
        challenge = graph.nodes[cell.challenge_id]
        graph.nodes[cell.challenge_id] = replace(challenge, metadata={**challenge.metadata,
            "observation_ids": tuple(dict.fromkeys((*challenge.metadata.get("observation_ids", ()), probe_key))),
            "discriminates_candidates": report["disagreement"],
            "lifecycle": "PROBED_EXPLORATORY", "certifying": False})
        graph.add_edge(GraphEdgeKind.DERIVED_FROM, probe_key, cell.challenge_id, static_or_dynamic="dynamic")
        for checkpoint_id in snapshots:
            graph.add_edge(GraphEdgeKind.TESTS, probe_key, checkpoint_id, static_or_dynamic="dynamic")
        branch = graph.nodes.get(cell.source_branch_id)
        if branch and len(distinct) > 1:
            graph.nodes[branch.node_id] = replace(branch, metadata={**branch.metadata,
                "sibling_disagreement_probe_ids": tuple(dict.fromkeys((*branch.metadata.get("sibling_disagreement_probe_ids", ()), probe_key)))})
        graph.record_update("SIBLING_PROBE", probe_id=probe_key, disagreement=report["disagreement"], certifying=False)
        reports.append(report)
    return tuple(reports)
