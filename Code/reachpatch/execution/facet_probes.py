"""Same-input observation adapters for explicitly grounded return facets."""
from __future__ import annotations

import ast
from dataclasses import replace
from typing import Sequence

from reachpatch.models.base import stable_id
from reachpatch.models.execution import ExecutableCheck, GoalContract


def materialize_return_facet_checks(check: ExecutableCheck, goals: Sequence[GoalContract]) -> tuple[ExecutableCheck, ...]:
    if len(check.command) != 3 or check.command[1] != "-c":
        return ()  # NO_INLINE_PYTHON_ADAPTER
    try:
        parsed = ast.parse(check.command[2])
    except SyntaxError:
        return ()  # UNPARSEABLE_WITNESS
    # The issue extractor wraps interactive expressions to preserve their
    # display observation. Unwrap only that exact literal adapter, not user
    # eval/exec calls or computed source strings.
    for index, statement in enumerate(parsed.body):
        outer = statement.value if isinstance(statement, ast.Expr) else None
        inner = outer.args[0] if isinstance(outer, ast.Call) and len(outer.args) == 1 else None
        if (isinstance(outer, ast.Call) and isinstance(outer.func, ast.Name) and outer.func.id == "exec"
            and isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "compile"
            and len(inner.args) == 3 and all(isinstance(value, ast.Constant) for value in inner.args)
            and inner.args[1].value == "<reachpatch-issue-witness>" and inner.args[2].value == "single"
            and isinstance(inner.args[0].value, str)):
            parsed.body[index:index + 1] = ast.parse(inner.args[0].value).body
            break
    names = {symbol.rsplit(".", 1)[-1] for symbol in check.target_symbols}
    calls = [node for node in ast.walk(parsed) if isinstance(node, ast.Call)
             and (node.func.id if isinstance(node.func, ast.Name)
                  else node.func.attr if isinstance(node.func, ast.Attribute) else "") in names]
    if len(calls) != 1:
        return ()  # AMBIGUOUS_TARGET_CALL
    call = calls[0]
    index = next((i for i, statement in enumerate(parsed.body)
                  if isinstance(statement, (ast.Expr, ast.Assign)) and statement.value is call), None)
    if index is None:
        return ()  # TARGET_CALL_REQUIRES_CONTROL_FLOW_ADAPTER
    # Keep setup and the original input; don't execute a nested call twice or
    # serialize full project objects. Only type and length leave the process.
    setup = ast.unparse(ast.Module(body=parsed.body[:index], type_ignores=[]))
    script = setup + "\nimport json as _rp_json\n_rp_value = " + ast.unparse(call) + "\n"
    script += ("_rp_summary = {'type': type(_rp_value).__name__, 'container_kinds': [name for name, cls in (('list', list), ('tuple', tuple)) if isinstance(_rp_value, cls)]}\n"
               "try:\n    _rp_summary['length'] = len(_rp_value)\n"
               "except TypeError:\n    _rp_summary['length'] = None\n"
               "print(_rp_json.dumps({'__reachpatch_return__': _rp_summary}))\n")
    result = []
    for goal in goals:
        if goal.parent_goal_id != check.goal_id or goal.unresolved_reason or goal.facet_kind not in {"RETURN_TYPE", "RETURN_LENGTH"}:
            continue
        result.append(replace(check, check_id=stable_id("facet-check", check.check_id, goal.goal_id),
            goal_id=goal.goal_id, comparator=goal.comparator, expected=goal.expected,
            command=(check.command[0], "-c", script), evidence_ids=goal.evidence_span_ids,
            authority=goal.authority, input_recipe={**(check.input_recipe or {}),
                "source_check_id": check.check_id, "facet_id": goal.goal_id,
                "observation_adapter": "SAFE_RETURN_SUMMARY_V1"}))
    return tuple(result)
