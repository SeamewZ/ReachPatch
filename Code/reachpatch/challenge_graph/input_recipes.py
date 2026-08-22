from __future__ import annotations

import json
import ast
import re

from reachpatch.models.base import stable_id
from reachpatch.models.evidence import PublicEvidence, issue_witnesses
from reachpatch.models.graphs import (
    BindingUnit, InputRecipe, InputRecipeResult, PathClass, ProgramGraph,
    RequirementLeaf,
    ProgramEdgeKind, ProgramNodeKind,
)


def _mutate_input(value, kind: str):
    if kind == "EMPTY":
        return type(value)() if isinstance(value, (str, list, tuple, dict, set)) else ""
    if kind == "NONEMPTY":
        return value if value not in (None, "", (), [], {}) else "x"
    if kind == "NONE":
        return None
    if kind == "NON_NONE":
        return value if value is not None else 0
    if kind in {"BOUNDARY_BEFORE", "BOUNDARY_AT", "BOUNDARY_AFTER"}:
        base = value if isinstance(value, (int, float)) else 0
        return base + {"BOUNDARY_BEFORE": -1, "BOUNDARY_AT": 0, "BOUNDARY_AFTER": 1}[kind]
    if kind in {"REVERSE_DISPATCH", "FORWARD_DISPATCH"}:
        if isinstance(value, dict) and isinstance(value.get("__args__"), list):
            result = dict(value)
            result["__args__"] = list(reversed(value["__args__"]))
            return result
        if isinstance(value, (list, tuple)):
            return type(value)(reversed(value))
    if kind == "EXCEPTION_HANDLER":
        return None
    if kind in {"RETURN_CONSUMER", "STATE_READER", "DIRECT_CALLER", "RENDERING_CONSUMER"}:
        return value
    if kind in {"WRAPPER_TRUTHY", "BRANCH_TRUE"}:
        return value if bool(value) else 1
    if kind in {"WRAPPER_FALSY", "BRANCH_FALSE"}:
        return type(value)() if isinstance(value, (str, list, tuple, dict, set)) else 0
    return value


def _predicate_input(value, predicate: str, desired: bool):
    try:
        expression = ast.parse(predicate, mode="eval").body
    except SyntaxError:
        return _mutate_input(value, "BRANCH_TRUE" if desired else "BRANCH_FALSE")
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        return _mutate_input(value, "BRANCH_FALSE" if desired else "BRANCH_TRUE")
    if isinstance(expression, ast.Name):
        return _mutate_input(value, "BRANCH_TRUE" if desired else "BRANCH_FALSE")
    if not (
        isinstance(expression, ast.Compare)
        and len(expression.ops) == len(expression.comparators) == 1
        and isinstance(expression.left, (ast.Name, ast.Attribute))
        and isinstance(expression.comparators[0], ast.Constant)
    ):
        return _mutate_input(value, "BRANCH_TRUE" if desired else "BRANCH_FALSE")
    boundary = expression.comparators[0].value
    operator = expression.ops[0]
    if isinstance(operator, ast.Eq):
        if desired:
            return boundary
        if value != boundary:
            return value
        return boundary + 1 if isinstance(boundary, (int, float)) else None
    if isinstance(operator, ast.NotEq):
        return _predicate_input(value, f"x == {boundary!r}", not desired)
    if isinstance(boundary, (int, float)):
        if isinstance(operator, ast.Gt):
            return boundary + 1 if desired else boundary
        if isinstance(operator, ast.GtE):
            return boundary if desired else boundary - 1
        if isinstance(operator, ast.Lt):
            return boundary - 1 if desired else boundary
        if isinstance(operator, ast.LtE):
            return boundary if desired else boundary + 1
    return _mutate_input(value, "BRANCH_TRUE" if desired else "BRANCH_FALSE")


def _satisfies_domain(value, constraints: tuple[str, ...]) -> bool | None:
    known = False
    for constraint in constraints:
        match = __import__("re").fullmatch(
            r"[A-Za-z_]\w*\s*(>=|<=|>|<|==)\s*(-?\d+(?:\.\d+)?)",
            constraint,
        )
        if match and isinstance(value, (int, float)):
            known = True
            boundary = float(match.group(2))
            operation = match.group(1)
            valid = {
                ">": value > boundary,
                ">=": value >= boundary,
                "<": value < boundary,
                "<=": value <= boundary,
                "==": value == boundary,
            }[operation]
            if not valid:
                return False
        length = __import__("re").fullmatch(
            r"len\([A-Za-z_]\w*\)\s*(>|==)\s*0", constraint,
        )
        if length and hasattr(value, "__len__"):
            known = True
            if (len(value) > 0) != (length.group(1) == ">"):
                return False
        if constraint.endswith(" is not None"):
            known = True
            if value is None:
                return False
    return True if known else None


def _balanced_call_expressions(source: str, operation: str) -> tuple[str, ...]:
    name = operation.split(".")[-1]
    starts = tuple(re.finditer(rf"\b{re.escape(name)}\s*\(", source))
    expressions: list[str] = []
    for match in starts:
        open_index = source.find("(", match.start())
        depth = 0
        quote: str | None = None
        escaped = False
        for index in range(open_index, len(source)):
            character = source[index]
            if quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in {"'", '"'}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    expressions.append(source[match.start():index + 1])
                    break
    return tuple(dict.fromkeys(expressions))


def _issue_witness_inputs(
    requirement: RequirementLeaf,
    public_evidence: PublicEvidence,
) -> tuple[tuple[object, str], ...]:
    if not requirement.witness_ids:
        return ()
    results: list[tuple[object, str]] = []
    observed_target_expressions: set[str] = set()
    for record in public_evidence.records:
        if record.source != "issue" or record.evidence_id not in requirement.evidence_ids:
            continue
        for witness in issue_witnesses(record):
            witness_id = str(witness["witness_id"])
            if witness_id not in requirement.witness_ids:
                continue
            target_expression = str(witness.get("target_expression", ""))
            if target_expression:
                observed_target_expressions.add(target_expression.strip())
            results.append(({
                "__reachpatch_issue_witness__": {
                    "witness_id": witness_id,
                    "script": str(witness["script"]),
                    "operation": str(witness["operation"]),
                    "target_expression": target_expression,
                },
            }, record.evidence_id))
        for expression in _balanced_call_expressions(
            record.content, requirement.operation,
        ):
            if (
                observed_target_expressions
                and expression.strip() not in observed_target_expressions
            ):
                # A helper call mentioned in the issue is not automatically the
                # reporter's observed scenario.  Only replay a literal call
                # whose full expression was actually observed.
                continue
            try:
                parsed = ast.parse(expression, mode="eval").body
                if not isinstance(parsed, ast.Call):
                    continue
                arguments = [ast.literal_eval(item) for item in parsed.args]
                keywords = {
                    item.arg: ast.literal_eval(item.value)
                    for item in parsed.keywords if item.arg is not None
                }
            except (SyntaxError, ValueError, TypeError):
                continue
            if len(arguments) == 1 and not keywords:
                concrete: object = arguments[0]
            else:
                concrete = {"__args__": arguments, "__kwargs__": keywords}
            results.append((concrete, record.evidence_id))
    return tuple(results)


def _structured_witness_partition_matches(
    concrete_input,
    predicate: str,
    partition_kind: str,
) -> bool | None:
    structured = (
        concrete_input.get("__reachpatch_issue_witness__")
        if isinstance(concrete_input, dict) else None
    )
    if not isinstance(structured, dict) or not predicate.isidentifier():
        return None
    expression = str(structured.get("target_expression", ""))
    try:
        call = ast.parse(expression, mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(call, ast.Call):
        return None
    keyword = next((item for item in call.keywords if item.arg == predicate), None)
    if partition_kind == "NONE":
        return keyword is None or (
            isinstance(keyword.value, ast.Constant) and keyword.value.value is None
        )
    if partition_kind == "NON_NONE":
        return keyword is not None and not (
            isinstance(keyword.value, ast.Constant) and keyword.value.value is None
        )
    return None


def _graph_partition_input(
    requirement: RequirementLeaf,
    predicate: str | None,
    partition_kind: str,
):
    """Solve a small, explicit branch predicate without inventing domain data."""

    if not predicate:
        return None
    desired = partition_kind not in {"BRANCH_FALSE", "WRAPPER_FALSY", "STATE_BEFORE"}
    try:
        expression = ast.parse(predicate, mode="eval").body
    except SyntaxError:
        return None
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        expression = expression.operand
        desired = not desired
    if (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and expression.func.id == "callable"
        and len(expression.args) == 1
        and not expression.keywords
    ):
        operand = expression.args[0]
        if isinstance(operand, ast.Name):
            name = operand.id
        elif (
            isinstance(operand, ast.Attribute)
            and isinstance(operand.value, ast.Name)
            and operand.value.id in {"self", "cls"}
        ):
            name = operand.attr
        else:
            return None
        callable_result = (
            "." if any(token in name.casefold() for token in ("path", "file", "dir"))
            else 1
        )
        value = (
            {"__reachpatch_factory__": "CALLABLE", "return": callable_result}
            if desired else callable_result
        )
        return {"__args__": [], "__kwargs__": {name: value}}
    if isinstance(expression, ast.Name):
        value = 1 if desired else 0
        return {"__args__": [], "__kwargs__": {expression.id: value}}
    if (
        isinstance(expression, ast.Compare)
        and len(expression.ops) == len(expression.comparators) == 1
        and isinstance(expression.left, ast.Name)
        and isinstance(expression.comparators[0], ast.Constant)
    ):
        name = expression.left.id
        boundary = expression.comparators[0].value
        operator = expression.ops[0]
        if isinstance(operator, (ast.Is, ast.Eq)):
            value = boundary if desired else (0 if boundary is None else None)
        elif isinstance(operator, (ast.IsNot, ast.NotEq)):
            value = (0 if boundary is None else None) if desired else boundary
        elif isinstance(boundary, (int, float)):
            value = _predicate_input(boundary, predicate, desired)
        else:
            return None
        return {"__args__": [], "__kwargs__": {name: value}}
    return None


def _direct_call_command(
    binding: BindingUnit,
    program_graph: ProgramGraph,
    concrete_input,
    context_node_id: str | None = None,
    operation: str | None = None,
    target_expression: str | None = None,
) -> tuple[str, ...] | None:
    preferred: set[str] = set()
    if context_node_id in program_graph.nodes:
        queue = [context_node_id]
        seen = set(queue)
        while queue and not preferred:
            current = queue.pop(0)
            node = program_graph.nodes.get(current)
            if node is not None and node.kind is ProgramNodeKind.FUNCTION:
                preferred.add(current)
                break
            for edge in program_graph.edges.values():
                if (
                    edge.target_id == current
                    and edge.source_id in program_graph.nodes
                    and edge.kind in {
                        ProgramEdgeKind.CONTAINS,
                        ProgramEdgeKind.STATE_READ,
                        ProgramEdgeKind.STATE_WRITE,
                    }
                    and edge.source_id not in seen
                ):
                    seen.add(edge.source_id)
                    queue.append(edge.source_id)
        # CALL_SITE/RETURN/STATE nodes are contained by a project function.
        # Resolve that enclosing entrypoint so an Impact Cone challenge really
        # exercises the consumer, rather than silently replaying the callee.
        if not preferred:
            containers = [
                edge.source_id for edge in program_graph.edges.values()
                if edge.target_id == context_node_id
                and edge.kind is ProgramEdgeKind.CONTAINS
            ]
            for container_id in containers:
                container = program_graph.nodes.get(container_id)
                if container is not None and container.kind in {
                    ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD,
                }:
                    preferred.add(container_id)
                    break
    candidate_ids = tuple(preferred) if preferred else binding.program_symbol_ids
    candidates = [
        program_graph.nodes[item]
        for item in candidate_ids
        if item in program_graph.nodes
        and program_graph.nodes[item].kind in {
            ProgramNodeKind.CLASS, ProgramNodeKind.FUNCTION,
            ProgramNodeKind.METHOD,
        }
    ]
    if not candidates:
        return None
    operation_terminal = operation.rsplit(".", 1)[-1] if operation else None
    node = sorted(candidates, key=lambda item: (
        operation_terminal is not None
        and item.symbol.rsplit(".", 1)[-1] != operation_terminal,
        item.kind is not ProgramNodeKind.METHOD,
        item.path, item.start_line, item.node_id,
    ))[0]
    if target_expression and not _matches_direct_target_expression(
        target_expression, node.symbol, operation,
    ):
        # Do not turn a wrapper/property witness such as ``MyForm().media``
        # into a call to a lower-level helper merely because the requirement
        # mentions that helper.  Its script is the executable scenario.
        return None
    if not _static_call_shape_matches(node, concrete_input):
        return None
    if not node.path.endswith(".py") or node.path.startswith(("tests/", "test/")):
        return None
    module = node.path[:-3].replace("/", ".")
    if module.endswith(".__init__"):
        module = module.removesuffix(".__init__")
    symbol = node.symbol.split(".")[-1]
    instance_consumer = False
    if node.kind is ProgramNodeKind.METHOD:
        constructor = symbol == "__init__"
        if (
            not constructor
            and node.metadata.get("method_binding") not in {"STATIC", "CLASS"}
        ):
            if context_node_id is None or node.metadata.get("required_parameters"):
                return None
            instance_consumer = True
        parts = node.symbol.split(".")
        if len(parts) < 2:
            return None
        owner = parts[-2]
        owner_target = f"getattr(importlib.import_module({module!r}),{owner!r})"
        target = (
            owner_target if constructor
            else f"getattr({owner_target},{symbol!r})"
        )
    elif node.kind is ProgramNodeKind.CLASS:
        target = f"getattr(importlib.import_module({module!r}),{symbol!r})"
    else:
        target = f"getattr(importlib.import_module({module!r}),{symbol!r})"
    encoded = json.dumps(concrete_input, ensure_ascii=True, sort_keys=True)
    invocation = (
        f"instance={owner_target}(*args,**kwargs)\n"
        f"result=getattr(instance,{symbol!r})()"
        if instance_consumer else
        f"target={target}\nresult=target(*args,**kwargs)"
    )
    script = f"""import builtins
import importlib
import json

value = json.loads({encoded!r})

def materialize(item):
    if isinstance(item, dict) and item.get('__reachpatch_factory__') == 'CALLABLE':
        payload = item.get('return')
        return lambda: materialize(payload)
    if isinstance(item, dict):
        return {{key: materialize(value) for key, value in item.items()}}
    if isinstance(item, list):
        return [materialize(value) for value in item]
    return item

def normalize(item):
    if item is None or isinstance(item, (bool, int, float, str)):
        return item
    if callable(item):
        return {{'__callable__': f"{{getattr(item, '__module__', '')}}.{{getattr(item, '__qualname__', type(item).__qualname__)}}"}}
    if isinstance(item, dict):
        return {{str(key): normalize(value) for key, value in item.items()}}
    if isinstance(item, (list, tuple)):
        return [normalize(value) for value in item]
    if isinstance(item, set):
        return sorted((normalize(value) for value in item), key=repr)
    return {{'__type__': f"{{type(item).__module__}}.{{type(item).__qualname__}}"}}

args = materialize(value.get('__args__', [])) if isinstance(value, dict) and '__args__' in value else [materialize(value)]
kwargs = materialize(value.get('__kwargs__', {{}})) if isinstance(value, dict) and '__kwargs__' in value else {{}}
expected = value.get('__expected_order__', []) if isinstance(value, dict) else []
getattr(builtins, '__reachpatch_trace_reset__', lambda: None)()
{invocation}
rendered = repr(result)
assert not expected or (all(item in rendered for item in expected) and [rendered.index(item) for item in expected] == sorted(rendered.index(item) for item in expected))
print(json.dumps(normalize(result), sort_keys=True))
"""
    return ("python", "-c", script)


def _matches_direct_target_expression(
    target_expression: str,
    symbol: str,
    operation: str | None,
) -> bool:
    """Accept only a witness whose observed expression is this direct call.

    A reporter may show a wrapper, property, or protocol route and separately
    mention the implementation operation.  Rewriting that shape into a helper
    invocation invents a different scenario, so only a syntactic direct call
    is eligible for graph-synthesized invocation.
    """

    try:
        expression = ast.parse(target_expression, mode="eval").body
    except SyntaxError:
        return False
    if not isinstance(expression, ast.Call):
        return False
    if isinstance(expression.func, ast.Name):
        terminal = expression.func.id
    elif isinstance(expression.func, ast.Attribute):
        terminal = expression.func.attr
    else:
        return False
    expected = (operation or symbol).rsplit(".", 1)[-1]
    return terminal == expected == symbol.rsplit(".", 1)[-1]


def _static_call_shape_matches(node, concrete_input) -> bool:
    """Reject a synthesized call that cannot satisfy the parsed signature."""

    if not any(key in node.metadata for key in (
        "parameters", "positional_parameters", "required_parameters",
        "accepts_varargs", "accepts_varkw",
    )):
        # Hand-built/localized graph nodes may not have a parsed signature yet.
        # Leave those as an explicit execution frontier rather than rejecting a
        # valid direct probe solely because metadata is incomplete.
        return True

    if isinstance(concrete_input, dict) and "__args__" in concrete_input:
        args = concrete_input.get("__args__", ())
        kwargs = concrete_input.get("__kwargs__", {})
    else:
        args = (concrete_input,)
        kwargs = {}
    if not isinstance(args, (list, tuple)) or not isinstance(kwargs, dict):
        return False
    parameters = tuple(str(value) for value in node.metadata.get("parameters", ()))
    if node.kind is ProgramNodeKind.METHOD and node.metadata.get(
        "method_binding"
    ) not in {"STATIC", "CLASS"}:
        parameters = tuple(value for value in parameters if value not in {"self", "cls"})
    required = tuple(str(value) for value in node.metadata.get("required_parameters", ()))
    positional = tuple(str(value) for value in node.metadata.get(
        "positional_parameters", parameters,
    ))
    if node.kind is ProgramNodeKind.METHOD and node.metadata.get(
        "method_binding"
    ) not in {"STATIC", "CLASS"}:
        positional = tuple(value for value in positional if value not in {"self", "cls"})
    if not bool(node.metadata.get("accepts_varargs", False)) and len(args) > len(positional):
        return False
    if any(name in kwargs for name in positional[:len(args)]):
        return False
    if any(name not in kwargs and name not in positional[:len(args)] for name in required):
        return False
    if not bool(node.metadata.get("accepts_varkw", False)) and any(
        name not in parameters for name in kwargs
    ):
        return False
    return True


def compile_input_recipe(
    requirement: RequirementLeaf,
    path_class: PathClass,
    binding: BindingUnit,
    program_graph: ProgramGraph,
    public_evidence: PublicEvidence,
    *,
    partition_kind: str = "PUBLIC_REPLAY",
    partition_predicate: str | None = None,
    recipe_index: int = 0,
    context_node_id: str | None = None,
) -> InputRecipeResult:
    """Compile one deterministic executable recipe or expose a real frontier."""

    permitted_check_ids = (
        binding.preservation_check_ids
        if requirement.preservation else binding.target_check_ids
    )
    checks = [
        check for check in public_evidence.checks
        if check.check_id in permitted_check_ids
    ]
    checks = sorted(checks, key=lambda item: item.check_id)
    if partition_kind in {"NONE", "NON_NONE"} and partition_predicate:
        grounded = [
            check for check in checks
            if _structured_witness_partition_matches(
                check.concrete_input, partition_predicate, partition_kind,
            ) is True
        ]
        if grounded:
            checks = grounded
    if partition_kind == "ISSUE_WITNESS":
        witnesses = _issue_witness_inputs(requirement, public_evidence)
        if recipe_index >= len(witnesses):
            return InputRecipeResult(
                None,
                f"No safely executable issue witness variant {recipe_index} "
                f"for binding {binding.binding_id}",
            )
        concrete, evidence_id = witnesses[recipe_index]
        structured = (
            concrete.get("__reachpatch_issue_witness__")
            if isinstance(concrete, dict) else None
        )
        command = (
            ("python", "-c", str(structured["script"]))
            if isinstance(structured, dict) and isinstance(structured.get("script"), str)
            else _direct_call_command(
                binding, program_graph,
                concrete,
                context_node_id,
                requirement.operation,
                str(structured.get("target_expression", ""))
                if isinstance(structured, dict) else None,
            )
        )
        if command is None:
            return InputRecipeResult(
                None, "No executable project entrypoint for the issue witness",
            )
        return InputRecipeResult(InputRecipe(
            recipe_id=stable_id(
                "input-recipe", requirement.requirement_id,
                path_class.path_class_id, binding.binding_id,
                "ISSUE_WITNESS", evidence_id, concrete,
            ),
            kind="ISSUE_WITNESS",
            concrete_input=concrete,
            derivation=(
                f"replay executable issue witness grounded by {evidence_id}",
                "retain the Requirement's full quantified domain",
            ),
            command=command,
            source_check_id=None,
            trace_symbols=binding.program_symbol_ids,
            call_mode=(
                "ISSUE_WITNESS_SCRIPT"
                if isinstance(structured, dict) and structured.get("script") else
                "SYNTHESIZED_DIRECT"
            ),
        ), None)
    if recipe_index < len(checks):
        check = checks[recipe_index]
        source_input = check.concrete_input
        variable_names = tuple(variable.name for variable in requirement.variables)
        if isinstance(source_input, dict) and variable_names:
            # Evidence may use named inputs while direct-call scenarios need a
            # deterministic positional/keyword encoding. Preserve the
            # normative variable order and keep explicit __args__/__kwargs__.
            if "__args__" not in source_input and "__kwargs__" not in source_input:
                if all(name in source_input for name in variable_names):
                    source_input = {
                        "__args__": [source_input[name] for name in variable_names],
                    }
        structured_partition = _structured_witness_partition_matches(
            source_input, partition_predicate or "", partition_kind,
        )
        concrete = (
            source_input
            if structured_partition is True else
            _predicate_input(
                source_input,
                partition_predicate or "",
                partition_kind == "BRANCH_TRUE",
            )
            if partition_kind in {"BRANCH_TRUE", "BRANCH_FALSE"}
            else _mutate_input(source_input, partition_kind)
        )
        derivation = [f"replay public input from {check.check_id}"]
        if variable_names:
            derivation.append(
                "bind Requirement variables in declared order: "
                + ", ".join(variable_names)
            )
        if partition_kind != "PUBLIC_REPLAY":
            derivation.append(f"change exactly one graph constraint dimension: {partition_kind}")
            if partition_predicate:
                derivation.append(f"solve branch predicate: {partition_predicate}")
            if context_node_id:
                derivation.append(f"replay graph impact context: {context_node_id}")
        constraints = tuple(dict.fromkeys(
            requirement.preconditions + requirement.domain_constraints
        ))
        if _satisfies_domain(concrete, constraints) is False:
            proof = InputRecipe(
                recipe_id=stable_id(
                    "unreachable-recipe", requirement.requirement_id,
                    binding.binding_id, partition_kind, concrete,
                ),
                kind=partition_kind,
                concrete_input=concrete,
                derivation=tuple(derivation + [
                    "partition contradicts the normative Requirement preconditions/domain",
                ]),
                command=(),
                source_check_id=check.check_id,
                trace_symbols=binding.program_symbol_ids,
            )
            return InputRecipeResult(proof, None, True)
        environment = dict(check.environment)
        command = check.command
        if partition_kind != "PUBLIC_REPLAY" and structured_partition is not True:
            command = _direct_call_command(
                binding, program_graph, concrete, context_node_id,
                requirement.operation,
            )
            if command is None:
                return InputRecipeResult(
                    None,
                    f"No executable project function entrypoint for {partition_kind}",
                )
        recipe_id = stable_id(
            "input-recipe", requirement.requirement_id, path_class.path_class_id,
            binding.binding_id, check.check_id, partition_kind, context_node_id,
            concrete,
        )
        return InputRecipeResult(InputRecipe(
            recipe_id=recipe_id,
            kind=partition_kind,
            concrete_input=concrete,
            derivation=tuple(derivation),
            command=command,
            source_check_id=check.check_id,
            environment=tuple(sorted(environment.items())),
            trace_symbols=tuple(dict.fromkeys(
                binding.program_symbol_ids
                + ((context_node_id,) if context_node_id else ())
            )),
            call_mode=(
                "PUBLIC_CHECK"
                if partition_kind == "PUBLIC_REPLAY" or structured_partition is True
                else "SYNTHESIZED_DIRECT"
            ),
        ), None)
    graph_input = _graph_partition_input(
        requirement, partition_predicate, partition_kind,
    )
    if recipe_index == 0 and graph_input is not None:
        command = _direct_call_command(
            binding, program_graph, graph_input, context_node_id,
            requirement.operation,
        )
        if command is not None:
            return InputRecipeResult(InputRecipe(
                recipe_id=stable_id(
                    "input-recipe", requirement.requirement_id,
                    path_class.path_class_id, binding.binding_id,
                    "GRAPH_PREDICATE", partition_kind,
                    partition_predicate, context_node_id, graph_input,
                ),
                kind=partition_kind,
                concrete_input=graph_input,
                derivation=(
                    f"solve changed Program branch predicate: {partition_predicate}",
                    f"exercise deterministic {partition_kind} partition",
                    "bind only the predicate-named parameter",
                ),
                command=command,
                source_check_id=None,
                trace_symbols=tuple(dict.fromkeys(
                    binding.program_symbol_ids
                    + ((context_node_id,) if context_node_id else ())
                )),
                call_mode="SYNTHESIZED_DIRECT",
            ), None)
    return InputRecipeResult(
        None,
        (
            f"No evidence-grounded recipe variant {recipe_index} "
            f"for binding {binding.binding_id}"
        ),
    )
