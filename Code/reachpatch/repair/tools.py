from __future__ import annotations

import ast
import difflib
import re
import subprocess
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
from typing import Iterable

from reachpatch.models.isolation import is_official_only_path
from reachpatch.program_graph.index import RepositoryIndex
from reachpatch.program_graph.slice import ContextRequest


def _expression_path(node: ast.AST) -> str:
    """Return a stable dotted path for names/attributes when one exists."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _expression_path(node.value)
        return f"{owner}.{node.attr}" if owner else ""
    return ""


def _caller_owned_mutation_errors(tree: ast.AST) -> set[str]:
    """Find writes through aliases which can still refer to caller state.

    The analysis is deliberately local to each function and follows only
    mechanically certain aliases: parameters, attributes/subscripts read from
    them, and ``getattr`` fallbacks.  Explicit copy/clone operations terminate
    an alias.  This catches constructor code which stores a caller's object and
    then mutates it, while permitting the usual clone-before-write pattern.
    """

    errors: set[str] = set()
    mutating_methods = {
        "add", "append", "clear", "discard", "extend", "insert", "pop",
        "remove", "reverse", "setdefault", "sort", "update",
    }
    copy_methods = {"copy", "clone", "deepcopy", "_clone", "_chain"}

    def analyze(function: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        arguments = (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
        owned = {
            argument.arg for argument in arguments
            if argument.arg not in {"self", "cls"}
        }
        # ``*args`` and ``**kwargs`` themselves are newly allocated containers
        # at the call boundary. Their elements may be borrowed, but assigning
        # a slot/key on the container does not mutate the caller's container.

        def is_owned(value: ast.AST) -> bool:
            path = _expression_path(value)
            if path and any(
                path == candidate or path.startswith(candidate + ".")
                for candidate in owned
            ):
                return True
            if isinstance(value, ast.Subscript):
                return is_owned(value.value)
            if isinstance(value, ast.IfExp):
                return is_owned(value.body) or is_owned(value.orelse)
            if isinstance(value, ast.BoolOp):
                return any(is_owned(item) for item in value.values)
            if isinstance(value, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                # A literal constructs an owned container even when it holds
                # references to caller-owned values.
                return False
            if isinstance(value, ast.Call):
                name = _expression_path(value.func)
                leaf = name.rsplit(".", 1)[-1]
                if leaf in copy_methods:
                    return False
                if name in {"copy.copy", "copy.deepcopy"}:
                    return False
                if isinstance(value.func, ast.Name) and value.func.id in {
                    "dict", "frozenset", "list", "set", "tuple",
                }:
                    return False
                if name in {"getattr", "vars"}:
                    return any(is_owned(argument) for argument in value.args)
            return False

        def target_container(target: ast.AST) -> ast.AST | None:
            if isinstance(target, ast.Attribute):
                return target.value
            if isinstance(target, ast.Subscript):
                return target.value
            return None

        def target_paths(target: ast.AST) -> tuple[str, ...]:
            if isinstance(target, (ast.Tuple, ast.List)):
                return tuple(
                    path for item in target.elts
                    if (path := _expression_path(item))
                )
            path = _expression_path(target)
            return (path,) if path else ()

        def record_write(target: ast.AST, line: int) -> None:
            container = target_container(target)
            if container is None or not is_owned(container):
                return
            errors.add(
                f"{function.name}: writes through caller-owned alias "
                f"{ast.unparse(target)!r}; clone/copy the input before changing state"
            )

        def update_alias(target: ast.AST, value: ast.AST) -> None:
            value_owned = is_owned(value)
            for path in target_paths(target):
                if value_owned:
                    owned.add(path)
                else:
                    owned.discard(path)
                    owned.difference_update({
                        candidate for candidate in owned
                        if candidate.startswith(path + ".")
                    })

        def process(statements: list[ast.stmt]) -> None:
            for statement in statements:
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        record_write(target, int(getattr(statement, "lineno", 0)))
                        update_alias(target, statement.value)
                elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                    record_write(statement.target, int(getattr(statement, "lineno", 0)))
                    update_alias(statement.target, statement.value)
                elif isinstance(statement, ast.AugAssign):
                    record_write(statement.target, int(getattr(statement, "lineno", 0)))
                elif isinstance(statement, ast.Delete):
                    for target in statement.targets:
                        record_write(target, int(getattr(statement, "lineno", 0)))
                elif (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Attribute)
                    and statement.value.func.attr in mutating_methods
                    and is_owned(statement.value.func.value)
                ):
                    errors.add(
                        f"{function.name}: calls mutating "
                        f"method {statement.value.func.attr!r} through a caller-owned alias; "
                        "clone/copy the input before changing state"
                    )

                # Branches are conservatively may-alias. Analyze each branch
                # from the same input state and merge their possible aliases.
                if isinstance(statement, ast.If):
                    original = set(owned)
                    process(statement.body)
                    body_owned = set(owned)
                    owned.clear()
                    owned.update(original)
                    process(statement.orelse)
                    owned.update(body_owned)
                elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                    before_loop = set(owned)
                    process(statement.body)
                    process(statement.orelse)
                    owned.update(before_loop)
                elif isinstance(statement, (ast.With, ast.AsyncWith)):
                    process(statement.body)
                elif isinstance(statement, ast.Try):
                    branch_states: list[set[str]] = []
                    before_try = set(owned)
                    process(statement.body)
                    branch_states.append(set(owned))
                    for handler in statement.handlers:
                        owned.clear()
                        owned.update(before_try)
                        process(handler.body)
                        branch_states.append(set(owned))
                    owned.clear()
                    owned.update(set().union(*branch_states, before_try))
                    process(statement.orelse)
                    process(statement.finalbody)

        process(function.body)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            analyze(node)
    return errors


def _binary_protocol_coercion_errors(tree: ast.AST) -> set[str]:
    """Reject newly introduced operand wrappers in binary protocol paths.

    A wrapper changes operand identity and commonly bypasses reverse dispatch.
    Capability checks or ``NotImplemented`` preserve Python's binary protocol;
    coercing ``other`` into the receiver type does not.
    """

    errors: set[str] = set()
    protocol_names = {
        "__add__", "__and__", "__div__", "__floordiv__", "__matmul__",
        "__mod__", "__mul__", "__or__", "__pow__", "__sub__",
        "__truediv__", "__xor__", "__radd__", "__rand__", "__rdiv__",
        "__rfloordiv__", "__rmatmul__", "__rmod__", "__rmul__", "__ror__",
        "__rpow__", "__rsub__", "__rtruediv__", "__rxor__",
    }
    method_capabilities: dict[int, tuple[str, ...]] = {}
    for owner in ast.walk(tree):
        if not isinstance(owner, ast.ClassDef):
            continue
        capabilities: set[str] = set()
        for item in owner.body:
            if (
                isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, bool)
            ):
                capabilities.add(item.targets[0].id)
            elif (
                isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, bool)
            ):
                capabilities.add(item.target.id)
        if not capabilities:
            continue
        for item in owner.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_capabilities[id(item)] = tuple(sorted(capabilities))

    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if function.name not in protocol_names and "combine" not in function.name.lower():
            continue
        parameter_names = {
            item.arg for item in (
                *function.args.posonlyargs, *function.args.args,
                *function.args.kwonlyargs,
            )
            if item.arg not in {"self", "cls"}
        }
        capabilities = method_capabilities.get(id(function), ())

        def capability_hint(operand: str) -> str:
            if not capabilities:
                return ""
            marker = capabilities[0]
            return (
                f" The owning class exposes boolean capability contract(s) "
                f"{capabilities!r}; test getattr({operand}, {marker!r}, False) "
                "instead of constructing a replacement operand."
            )

        for statement in ast.walk(function):
            if (
                isinstance(statement, ast.Call)
                and isinstance(statement.func, ast.Attribute)
                and statement.func.attr == "_combine"
                and isinstance(statement.func.value, ast.Name)
                and statement.func.value.id in parameter_names
            ):
                errors.add(
                    f"{function.name}: directly calls private _combine() on binary "
                    f"operand {statement.func.value.id!r}; do not simulate reflected "
                    "dispatch on an arbitrary protocol object; preserve the operand "
                    "and prefer an existing public capability contract. Return "
                    "NotImplemented only when a concrete reflected method is proven "
                    "to accept the operand."
                    + capability_hint(statement.func.value.id)
                )
            if (
                isinstance(statement, ast.Call)
                and len(statement.args) == 1
                and not statement.keywords
                and isinstance(statement.args[0], ast.Name)
                and statement.args[0].id in parameter_names
            ):
                constructor = _expression_path(statement.func)
                constructor_leaf = constructor.rsplit(".", 1)[-1]
                # Raising an exception with the offending operand is diagnostic,
                # not protocol coercion.  Line shifts caused by a new guard used to
                # make an unchanged ``TypeError(other)`` look like a newly added
                # wrapper because the validation key includes its line number.
                # Continue rejecting real value/object wrappers while excluding
                # conventional exception constructors.
                is_exception_constructor = (
                    constructor_leaf.endswith("Error")
                    or constructor_leaf.endswith("Exception")
                    or constructor_leaf in {
                        "Exception", "BaseException", "StopIteration",
                        "StopAsyncIteration", "GeneratorExit",
                        "KeyboardInterrupt", "SystemExit",
                    }
                )
                if constructor_leaf[:1].isupper() and not is_exception_constructor:
                    errors.add(
                        f"{function.name}:{getattr(statement, 'lineno', 0)}: wraps "
                        f"binary operand {statement.args[0].id!r} in constructor "
                        f"{constructor}() inside the protocol path; preserve operand "
                        "identity and use the published capability contract"
                        + capability_hint(statement.args[0].id)
                    )
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            targets = statement.targets if isinstance(statement, ast.Assign) else (statement.target,)
            value = statement.value
            if not isinstance(value, ast.Call) or len(value.args) != 1 or value.keywords:
                continue
            argument = value.args[0]
            if not isinstance(argument, ast.Name) or argument.id not in parameter_names:
                continue
            if not any(isinstance(target, ast.Name) and target.id == argument.id for target in targets):
                continue
            constructor = _expression_path(value.func) or ast.unparse(value.func)
            errors.add(
                f"{function.name}:{getattr(statement, 'lineno', 0)}: coerces binary operand "
                f"{argument.id!r} with {constructor}(); preserve the operand and honor forward/"
                "reverse dispatch. Prefer an existing capability check; use "
                "NotImplemented only when the reflected implementation is proven."
                + capability_hint(argument.id)
            )
    return errors


def _binary_capability_bypass_errors(before: ast.AST, after: ast.AST) -> set[str]:
    """Reject blanket ``NotImplemented`` when the receiver exposes a capability.

    Binary helper methods often reject a concrete peer type even though the owning
    class publishes a boolean protocol marker (for example, an expression being
    conditional). Replacing ``raise TypeError`` with ``return NotImplemented`` only
    defers the decision to a reflected method and doesn't make the helper accept all
    objects implementing that marker. Prefer the local capability contract when one
    is mechanically visible on the owning class.
    """

    errors: set[str] = set()

    def classes(tree: ast.AST) -> dict[str, ast.ClassDef]:
        return {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        }

    def methods(node: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
        return {
            item.name: item
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def boolean_capabilities(node: ast.ClassDef) -> tuple[str, ...]:
        names: set[str] = set()
        for item in node.body:
            if (
                isinstance(item, ast.Assign)
                and len(item.targets) == 1
                and isinstance(item.targets[0], ast.Name)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, bool)
            ):
                names.add(item.targets[0].id)
            elif (
                isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, bool)
            ):
                names.add(item.target.id)
        return tuple(sorted(names))

    def count_type_errors(function: ast.AST) -> int:
        return sum(
            1 for item in ast.walk(function)
            if isinstance(item, ast.Raise)
            and isinstance(item.exc, ast.Call)
            and _expression_path(item.exc.func) in {"TypeError", "builtins.TypeError"}
        )

    def count_notimplemented_returns(function: ast.AST) -> int:
        return sum(
            1 for item in ast.walk(function)
            if isinstance(item, ast.Return)
            and isinstance(item.value, ast.Name)
            and item.value.id == "NotImplemented"
        )

    def references_capability(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        capability: str,
    ) -> bool:
        parameters = {
            item.arg for item in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
            if item.arg not in {"self", "cls"}
        }
        for item in ast.walk(function):
            if (
                isinstance(item, ast.Attribute)
                and item.attr == capability
                and isinstance(item.value, ast.Name)
                and item.value.id in parameters
            ):
                return True
            if (
                isinstance(item, ast.Call)
                and _expression_path(item.func) == "getattr"
                and len(item.args) >= 2
                and isinstance(item.args[0], ast.Name)
                and item.args[0].id in parameters
                and isinstance(item.args[1], ast.Constant)
                and item.args[1].value == capability
            ):
                return True
        return False

    before_classes = classes(before)
    for class_name, after_class in classes(after).items():
        before_class = before_classes.get(class_name)
        if before_class is None:
            continue
        capabilities = boolean_capabilities(after_class)
        if not capabilities:
            continue
        before_methods = methods(before_class)
        for method_name, after_method in methods(after_class).items():
            if "combine" not in method_name.lower():
                continue
            before_method = before_methods.get(method_name)
            if before_method is None:
                continue
            if count_type_errors(before_method) <= count_type_errors(after_method):
                continue
            if (
                count_notimplemented_returns(after_method)
                <= count_notimplemented_returns(before_method)
            ):
                continue
            missing = tuple(
                name for name in capabilities
                if not references_capability(after_method, name)
            )
            if missing:
                errors.add(
                    f"{class_name}.{method_name} replaces a concrete TypeError with "
                    "blanket NotImplemented while ignoring existing binary capability "
                    f"contract(s) {missing!r}"
                )
    return errors


def _presentation_form_adapter_errors(tree: ast.AST) -> set[str]:
    """Reject input/form adapter factories newly used as output serializers.

    A display/render/format path receives runtime values. Constructing a form or
    input adapter there can select a different adapter based on choices or other
    configuration and broadens the path with setup side effects. Such paths should
    use the producer/model object's stable serialization contract directly.
    """

    errors: set[str] = set()
    presentation_tokens = ("display", "format", "render", "serializ")
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(token in function.name.lower() for token in presentation_tokens):
            continue
        for call in ast.walk(function):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if (
                call.func.attr == "prepare_value"
                and isinstance(call.func.value, ast.Call)
                and isinstance(call.func.value.func, ast.Attribute)
                and call.func.value.func.attr in {"formfield", "form_field", "input_adapter"}
            ):
                errors.add(
                    f"{function.name}:{getattr(call, 'lineno', 0)} constructs "
                    f"{call.func.value.func.attr}() inside a presentation path and uses "
                    "its prepare_value() input contract as an output serializer"
                )
            if call.func.attr in {
                "clean", "get_db_prep_value", "get_prep_value", "to_python",
            }:
                errors.add(
                    f"{function.name}:{getattr(call, 'lineno', 0)} uses "
                    f"{call.func.attr}() persistence/input coercion as an output "
                    "serializer in a presentation path"
                )
    return errors


def _unbounded_rectangular_index_errors(tree: ast.AST) -> set[str]:
    """Find 2-D row/column comprehensions whose column bound can exceed cols."""

    errors: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for comprehension in ast.walk(function):
            if not isinstance(comprehension, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
                continue
            element = comprehension.elt
            subscripts = [
                item for item in ast.walk(element)
                if isinstance(item, ast.Subscript)
            ]
            if not any(
                isinstance(item.slice, (ast.Tuple, ast.List))
                and len(item.slice.elts) == 2
                and isinstance(item.slice.elts[0], ast.Name)
                and isinstance(item.slice.elts[1], ast.Name)
                for item in subscripts
            ):
                continue
            generators = {
                generator.target.id: generator.iter
                for generator in comprehension.generators
                if isinstance(generator.target, ast.Name)
            }
            for item in subscripts:
                if not isinstance(item.slice, (ast.Tuple, ast.List)) or len(item.slice.elts) != 2:
                    continue
                row, column = item.slice.elts
                if not isinstance(row, ast.Name) or not isinstance(column, ast.Name):
                    continue
                row_iter = generators.get(row.id)
                column_iter = generators.get(column.id)
                if row_iter is None or column_iter is None:
                    continue
                row_text = ast.unparse(row_iter)
                column_text = ast.unparse(column_iter)
                if ".rows" not in row_text or row.id not in column_text:
                    continue
                if ".cols" in column_text:
                    continue
                errors.add(
                    f"{function.name}:{getattr(comprehension, 'lineno', 0)}: column index "
                    f"{column.id!r} is bounded by row index {row.id!r} but not by the "
                    "column dimension"
                )
    return errors


def _partial_rectangular_index_fix_errors(
    before: ast.AST,
    after: ast.AST,
) -> set[str]:
    """Require a boundary repair to cover its remaining unsafe sibling paths."""

    old_errors = _unbounded_rectangular_index_errors(before)
    new_errors = _unbounded_rectangular_index_errors(after)
    if len(new_errors) >= len(old_errors) or not new_errors:
        return set()
    return {
        "partial rectangular index repair leaves sibling path unsafe: " + item
        for item in new_errors
    }


def _state_transition_structure_errors(tree: ast.AST) -> set[str]:
    """Find producer-side attempts to hide an empty difference state."""

    errors: set[str] = set()

    def assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            return None
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if len(targets) != 1 or node.value is None:
            return None
        return ast.unparse(targets[0]), node.value

    def walk(statements: list[ast.stmt]) -> None:
        for index, statement in enumerate(statements):
            if isinstance(statement, ast.If) and index > 0:
                previous = assignment(statements[index - 1])
                if previous is not None:
                    difference_target, difference_value = previous
                    difference_text = ast.unparse(difference_value)
                    if (
                        ".difference(" in difference_text
                        and difference_target in ast.unparse(statement.test)
                    ):
                        calls = [
                            node for node in ast.walk(difference_value)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "difference"
                        ]
                        removed_inputs = {
                            name.id
                            for call in calls
                            for argument in call.args
                            for name in ast.walk(argument)
                            if isinstance(name, ast.Name)
                        }
                        branch_assignments = [
                            current
                            for branch in (statement.body, statement.orelse)
                            for child in branch
                            if (current := assignment(child)) is not None
                        ]
                        state_targets = {
                            target
                            for target, value in branch_assignments
                            if difference_target in {
                                name.id for name in ast.walk(value)
                                if isinstance(name, ast.Name)
                            }
                        } or {difference_target}
                        previous_mode = (
                            difference_value.elts[-1].value
                            if isinstance(difference_value, ast.Tuple)
                            and difference_value.elts
                            and isinstance(difference_value.elts[-1], ast.Constant)
                            and isinstance(difference_value.elts[-1].value, bool)
                            else None
                        )
                        for target, value in branch_assignments:
                            if target not in state_targets:
                                continue
                            value_text = ast.unparse(value)
                            names = {
                                name.id for name in ast.walk(value)
                                if isinstance(name, ast.Name)
                            }
                            if removed_inputs & names and ".difference(" not in value_text:
                                errors.add("reintroduces raw input after a difference operation")
                            replacement_mode = (
                                value.elts[-1].value
                                if isinstance(value, ast.Tuple)
                                and value.elts
                                and isinstance(value.elts[-1], ast.Constant)
                                and isinstance(value.elts[-1].value, bool)
                                else None
                            )
                            if (
                                previous_mode is not None
                                and replacement_mode is not None
                                and previous_mode != replacement_mode
                                and difference_target in value_text
                                and ".difference(" not in value_text
                            ):
                                errors.add("only flips the mode/tag of an empty difference state")
            for _field, value in ast.iter_fields(statement):
                if (
                    isinstance(value, list) and value
                    and all(isinstance(item, ast.stmt) for item in value)
                ):
                    walk(value)

    walk(list(getattr(tree, "body", ())))
    return errors


def _state_consumer_recovery_anchors(
    *,
    relative_path: str,
    source: str,
    tree: ast.AST,
    edits: Iterable["ProposedEdit"],
    max_anchors: int = 4,
) -> tuple[dict[str, Any], ...]:
    """Locate the consumer guard that can explain a rejected state rewrite.

    A common invalid repair changes a state *producer* so an empty normalized
    payload is replaced with the original input or with a different mode tag.
    The real defect can instead be a consumer which unpacks ``(payload, mode)``
    and returns on an empty payload before interpreting ``mode``.  This helper
    follows that local state relation in the actual file and returns exact,
    mechanically sourced edit anchors for the bounded recovery turn.

    The result is diagnostic evidence only: it is never staged automatically
    and it cannot trigger a controller revision.
    """

    edit_text = "\n".join(
        f"{edit.expected_source}\n{edit.replacement}" for edit in edits
    )
    mentioned_attributes = {
        f"{owner}.{attribute}"
        for owner, attribute in re.findall(
            r"\b(self|cls)\.([A-Za-z_]\w*)", edit_text,
        )
    }
    if not mentioned_attributes:
        return ()

    source_lines = source.splitlines()

    def expression_name(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except (AttributeError, ValueError):
            return ""

    def unpacked_names(target: ast.AST) -> tuple[str, ...]:
        if not isinstance(target, (ast.Tuple, ast.List)):
            return ()
        names: list[str] = []
        for item in target.elts:
            if isinstance(item, ast.Name):
                names.append(item.id)
            else:
                return ()
        return tuple(names)

    def names_in(node: ast.AST) -> set[str]:
        return {
            child.id for child in ast.walk(node) if isinstance(child, ast.Name)
        }

    def is_empty_value(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Constant) and node.value in {None, False, 0, ""}
        ) or (
            isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict))
            and not (
                node.keys if isinstance(node, ast.Dict) else node.elts
            )
        )

    def is_payload_empty_guard(test: ast.AST, payload: str) -> bool:
        if payload not in names_in(test):
            return False
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return payload in names_in(test.operand)
        if isinstance(test, ast.Compare):
            values = (test.left, *test.comparators)
            if any(is_empty_value(value) for value in values):
                return True
            for value in values:
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "len"
                    and payload in names_in(value)
                ):
                    return any(
                        isinstance(other, ast.Constant) and other.value == 0
                        for other in values
                    )
        return False

    def statement_blocks(node: ast.AST) -> Iterable[list[ast.stmt]]:
        for _field_name, value in ast.iter_fields(node):
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, ast.stmt) for item in value)
            ):
                yield value
                for statement in value:
                    yield from statement_blocks(statement)

    anchors: list[dict[str, Any]] = []
    functions = (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for function in functions:
        for block in statement_blocks(function):
            for index, statement in enumerate(block):
                if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else (statement.target,)
                )
                if len(targets) != 1 or statement.value is None:
                    continue
                state_attribute = expression_name(statement.value)
                if state_attribute not in mentioned_attributes:
                    continue
                names = unpacked_names(targets[0])
                if len(names) < 2:
                    continue
                payload = names[0]
                companion_names = names[1:]
                for guard_index in range(index + 1, len(block)):
                    guard = block[guard_index]
                    if not isinstance(guard, ast.If):
                        continue
                    guard_names = names_in(guard.test)
                    if any(name in guard_names for name in companion_names):
                        continue
                    if not is_payload_empty_guard(guard.test, payload):
                        continue
                    following = block[guard_index + 1:]
                    used_companions = tuple(
                        name for name in companion_names
                        if any(name in names_in(item) for item in following)
                    )
                    if not used_companions:
                        continue
                    guard_start = int(guard.lineno)
                    guard_end = int(getattr(guard, "end_lineno", guard_start))
                    context_start = int(statement.lineno)
                    context_end = min(
                        len(source_lines),
                        max(guard_end, context_start + 8),
                    )
                    anchors.append({
                        "kind": "STATE_CONSUMER_GUARD",
                        "relative_path": relative_path,
                        "symbol": function.name,
                        "state_attribute": state_attribute,
                        "payload_name": payload,
                        "companion_names": used_companions,
                        "guard_start_line": guard_start,
                        "guard_end_line": guard_end,
                        "guard_source": "\n".join(
                            source_lines[guard_start - 1:guard_end]
                        ),
                        "context_start_line": context_start,
                        "context_end_line": context_end,
                        "context_source": "\n".join(
                            source_lines[context_start - 1:context_end]
                        ),
                        "reason": (
                            "this consumer exits on payload emptiness before "
                            "interpreting the companion mode/tag"
                        ),
                    })
                    if len(anchors) >= max_anchors:
                        return tuple(anchors)
                    break
    return tuple(anchors)


def _duplicate_statement_block_errors(tree: ast.AST) -> set[str]:
    """Detect newly copied consecutive control-flow blocks in one scope."""

    errors: set[str] = set()
    for node in ast.walk(tree):
        for field_name, value in ast.iter_fields(node):
            if (
                not isinstance(value, list)
                or len(value) < 4
                or not all(isinstance(item, ast.stmt) for item in value)
            ):
                continue
            signatures = [ast.dump(item, include_attributes=False) for item in value]
            maximum = min(8, len(signatures) // 2)
            for width in range(2, maximum + 1):
                for start in range(0, len(signatures) - 2 * width + 1):
                    if (
                        signatures[start:start + width]
                        == signatures[start + width:start + 2 * width]
                    ):
                        errors.add(
                            f"repeats {width} consecutive statements in "
                            f"{type(node).__name__}.{field_name}"
                        )
    return errors


def _duplicate_scope_assignment_errors(tree: ast.AST) -> set[str]:
    """Find identical assignments copied into module/class scope.

    Reassigning a local variable inside a function is normal state evolution, so
    this check deliberately covers only module and class bodies.  Copying an
    existing class constant or module setting with the same value cannot repair a
    runtime path and can silently displace a class/module docstring.
    """

    errors: set[str] = set()

    def target_name(target: ast.AST) -> str:
        if isinstance(target, ast.Name):
            return target.id
        if isinstance(target, ast.Attribute):
            owner = _expression_path(target.value)
            return f"{owner}.{target.attr}" if owner else target.attr
        return ""

    def visit_scope(node: ast.Module | ast.ClassDef, owner: str) -> None:
        signatures: Counter[tuple[str, str]] = Counter()
        for statement in node.body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                name = target_name(statement.targets[0])
                value = statement.value
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                name = target_name(statement.target)
                value = statement.value
            else:
                name = ""
                value = None
            if name and value is not None:
                signatures[(name, ast.dump(value, include_attributes=False))] += 1
            if isinstance(statement, ast.ClassDef):
                nested_owner = f"{owner}.{statement.name}" if owner else statement.name
                visit_scope(statement, nested_owner)
        for (name, _value), count in signatures.items():
            if count > 1:
                qualified = f"{owner}.{name}" if owner else name
                errors.add(f"duplicate identical assignment {qualified}")

    visit_scope(tree, "")
    return errors


def _unguarded_nested_return_subscript_errors(tree: ast.AST) -> set[str]:
    """Find returns which introduce two unchecked literal mapping lookups.

    Nested mappings supplied directly by a caller commonly omit an inner key on
    a valid preservation path. Replacing a computed fallback with
    ``data['a']['b']`` broadens the public exception surface unless the function
    first proves membership. Object-owned mappings (``self.data['a']['b']``)
    are permitted because their producer establishes an object invariant; a
    blanket rejection there incorrectly forces stale derived fallbacks over the
    authoritative metadata stored by that producer.
    """

    errors: set[str] = set()

    def literal_chain(node: ast.AST) -> tuple[str, ...]:
        keys: list[str] = []
        current = node
        while isinstance(current, ast.Subscript):
            key = current.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.append(key.value)
            else:
                break
            current = current.value
        return tuple(reversed(keys))

    def chain_root(node: ast.AST) -> ast.AST:
        current = node
        while isinstance(current, ast.Subscript):
            current = current.value
        return current

    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        guarded_keys = {
            value.value
            for condition in ast.walk(function)
            if isinstance(condition, (ast.If, ast.Assert))
            for value in ast.walk(condition.test)
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        }
        for statement in ast.walk(function):
            if not isinstance(statement, ast.Return) or statement.value is None:
                continue
            keys = literal_chain(statement.value)
            if len(keys) < 2 or set(keys).issubset(guarded_keys):
                continue
            root = chain_root(statement.value)
            root_path = _expression_path(root)
            if root_path in {"self", "cls"} or root_path.startswith(("self.", "cls.")):
                continue
            errors.add(
                f"{function.name}:{getattr(statement, 'lineno', 0)} returns through "
                f"unchecked nested mapping keys {keys!r}"
            )
    return errors


def _placeholder_definition_errors(
    tree: ast.AST,
    *,
    definition_lines: set[int] | None = None,
) -> set[str]:
    """Find newly introduced functions that cannot change runtime behavior."""

    errors: set[str] = set()

    def visit(node: ast.AST, scope: tuple[str, ...] = ()) -> None:
        for child in ast.iter_child_nodes(node):
            child_scope = scope
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                child_scope = (*scope, child.name)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                line = int(getattr(child, "lineno", 0))
                decorators = {
                    getattr(decorator, "id", None)
                    or getattr(decorator, "attr", None)
                    for decorator in child.decorator_list
                }
                body = list(child.body)
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    body = body[1:]
                placeholder = len(body) == 1 and (
                    isinstance(body[0], ast.Pass)
                    or (
                        isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and body[0].value.value is Ellipsis
                    )
                )
                if (
                    placeholder
                    and not decorators & {"abstractmethod", "overload"}
                    and (definition_lines is None or line in definition_lines)
                ):
                    errors.add(
                        f"pass/ellipsis-only definition {'.'.join(child_scope)}"
                    )
            visit(child, child_scope)

    visit(tree)
    return errors


def _shadowing_definition_errors(tree: ast.AST) -> set[str]:
    """Return stable identities for definitions that shadow one another.

    Line numbers cannot be part of the identity: an unrelated insertion before
    an existing overload or protocol hook moves its line and would otherwise
    make a baseline definition look newly introduced.
    """

    errors: set[str] = set()

    def decorator_names(definitions: list[ast.AST]) -> set[str]:
        return {
            str(getattr(decorator, "id", None) or getattr(decorator, "attr", ""))
            for definition in definitions
            for decorator in getattr(definition, "decorator_list", ())
        }

    top_level: dict[str, list[ast.AST]] = {}
    for node in tree.body if isinstance(tree, ast.Module) else ():
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top_level.setdefault(node.name, []).append(node)
    for name, definitions in top_level.items():
        if len(definitions) > 1 and "overload" not in decorator_names(definitions):
            errors.add(f"duplicate top-level definition {name!r}")

    def visit_class(node: ast.ClassDef, scope: tuple[str, ...]) -> None:
        qualified = (*scope, node.name)
        members: dict[str, list[ast.AST]] = {}
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                members.setdefault(child.name, []).append(child)
        for name, definitions in members.items():
            if len(definitions) <= 1:
                continue
            decorators = decorator_names(definitions)
            legitimate_family = bool(
                decorators & {"property", "getter", "setter", "deleter", "overload"}
            )
            if not legitimate_family:
                errors.add(f"duplicate member {'.'.join(qualified)}.{name}")
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                visit_class(child, qualified)

    for node in tree.body if isinstance(tree, ast.Module) else ():
        if isinstance(node, ast.ClassDef):
            visit_class(node, ())
    return errors


def _unchanged_path_definition_extensions(
    expected_source: str,
    replacement: str,
) -> tuple[str, ...]:
    """Find definitions appended after an unchanged selected path.

    A new helper cannot repair behavior when the exact statements selected for
    replacement remain unchanged and nothing in those statements calls the
    helper. Detect that shape before source-range recovery so the generator is
    told about the causal defect instead of repeatedly adjusting line numbers.
    """

    def parse_fragment(source: str) -> ast.Module | None:
        try:
            return ast.parse(textwrap.dedent(source.rstrip("\n")))
        except SyntaxError:
            return None

    expected_text = expected_source.rstrip("\n")
    replacement_text = replacement.rstrip("\n")
    if not replacement_text.startswith(expected_text):
        return ()
    tail = replacement_text[len(expected_text):]
    if not tail.strip():
        return ()

    # Parse the added tail separately. This remains valid when expected_source
    # is a class-body or function-body fragment whose indentation differs from
    # a newly appended top-level definition.
    added = parse_fragment(tail)
    if added is None:
        return ()
    expected = parse_fragment(expected_source)
    existing_names = {
        node.name
        for node in (ast.walk(expected) if expected is not None else ())
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    placeholder_names = {
        node.name
        for node in added.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and _placeholder_definition_errors(
            ast.Module(body=[node], type_ignores=[])
        )
    }
    return tuple(
        node.name
        for node in added.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name not in existing_names
        and node.name not in placeholder_names
    )


def _reversed_set_operation_errors(
    tree: ast.AST,
    *,
    call_lines: set[int] | None = None,
) -> set[str]:
    """Reject set-only methods reversed onto an unnormalized input iterable."""

    errors: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = {
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
        }
        normalized: set[str] = set()
        calls: list[ast.Call] = []
        for node in ast.walk(function):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                value = node.value
                if (
                    len(targets) == 1
                    and isinstance(targets[0], ast.Name)
                    and targets[0].id in parameters
                    and isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id in {"set", "frozenset"}
                ):
                    normalized.add(targets[0].id)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"difference", "intersection", "union"}
            ):
                calls.append(node)
        for call in calls:
            receiver = call.func.value
            if not isinstance(receiver, ast.Name):
                continue
            parameter = receiver.id
            line = int(getattr(call, "lineno", 0))
            if (
                parameter not in parameters
                or parameter in normalized
                or (call_lines is not None and line not in call_lines)
            ):
                continue
            reverse_evidence = any(
                other is not call
                and isinstance(other.func.value, ast.Name)
                and other.func.value.id not in parameters
                and any(
                    parameter in {
                        child.id for child in ast.walk(argument)
                        if isinstance(child, ast.Name)
                    }
                    for argument in other.args
                )
                for other in calls
            )
            if reverse_evidence:
                errors.add(
                    f"set-only {call.func.attr}() is called on unnormalized "
                    f"input parameter {parameter!r} in {function.name}"
                )
    return errors


def _new_unresolved_name_errors(before: ast.AST, after: ast.AST) -> set[str]:
    """Find newly loaded direct names with no definition in the candidate."""

    def loaded_names(tree: ast.AST) -> set[str]:
        return {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }

    def defined_names(tree: ast.AST) -> set[str]:
        names = {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        }
        names.update(
            node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)
        )
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Import):
                names.update(
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                names.update(
                    alias.asname or alias.name for alias in node.names
                    if alias.name != "*"
                )
        return names

    builtins = set(dir(__import__("builtins"))) | {
        "basestring", "long", "raw_input", "StandardError", "unicode", "xrange",
    }
    introduced = loaded_names(after) - loaded_names(before)
    unresolved = introduced - defined_names(after) - builtins
    # Existing projects frequently expose module-level configuration constants
    # through settings/import side effects that a bounded AST cannot resolve.
    # Preserve that convention when the original file already contains an
    # unresolved all-caps constant (the new behavior still gets checked for
    # ordinary names such as ``router`` or ``numbers``).
    baseline_unresolved = loaded_names(before) - defined_names(before) - builtins
    if any(name.isupper() for name in baseline_unresolved):
        unresolved = {
            name for name in unresolved
            if not name.isupper()
        }
    return unresolved


def _new_unused_import_errors(before: ast.AST, after: ast.AST) -> set[str]:
    """Find newly bound imports that never enter an executable expression.

    A common false repair imports the API named by the issue and then changes an
    unrelated guard without ever calling or reading that API.  Such a diff can
    look keyword-complete to a textual review while leaving the selected runtime
    path unchanged.  Compare only imports introduced by the candidate and count
    every AST ``Load`` (including decorators, annotations, defaults, and nested
    functions) as a real use.  ``__future__`` features are compiler directives,
    not runtime bindings, and are excluded.
    """

    def imported_names(tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(
                    alias.asname or alias.name.split(".", 1)[0]
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                names.update(
                    alias.asname or alias.name
                    for alias in node.names if alias.name != "*"
                )
        return names

    loaded = {
        node.id for node in ast.walk(after)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    introduced = imported_names(after) - imported_names(before)
    return {
        name for name in introduced
        if name not in loaded and not name.startswith("_")
    }


def _import_candidates_for_names(
    repository_root: Path,
    repository_index: RepositoryIndex,
    relative_path: str,
    names: Iterable[str],
    *,
    max_paths_per_name: int = 40,
    max_candidates_per_name: int = 3,
) -> tuple[dict[str, Any], ...]:
    """Find bounded existing imports that bind newly introduced names.

    These candidates are recovery evidence only; ReachPatch never inserts one
    automatically. Using token postings keeps the search bounded and avoids
    guessing a module path from an identifier such as ``router`` or ``numbers``.
    """

    root = repository_root.resolve()
    token_index = getattr(repository_index, "token_index", {}) or {}
    current_parts = Path(relative_path).parts
    candidates: list[dict[str, Any]] = []
    for name in sorted(set(map(str, names))):
        statements: Counter[str] = Counter()
        sources: dict[str, set[str]] = defaultdict(set)
        for candidate_path in tuple(token_index.get(name, ()))[:max_paths_per_name]:
            candidate_path = str(candidate_path).replace("\\", "/")
            path_parts = Path(candidate_path).parts
            if (
                candidate_path == relative_path
                or "tests" in path_parts
                or "test" in path_parts
                or Path(candidate_path).name.startswith("test_")
            ):
                continue
            path = (root / candidate_path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            try:
                tree = ast.parse(
                    path.read_text(encoding="utf-8", errors="replace"),
                    filename=candidate_path,
                )
            except (OSError, SyntaxError):
                continue
            for node in tree.body:
                bound_names: set[str] = set()
                if isinstance(node, ast.Import):
                    bound_names.update(
                        alias.asname or alias.name.split(".", 1)[0]
                        for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom):
                    bound_names.update(
                        alias.asname or alias.name
                        for alias in node.names if alias.name != "*"
                    )
                if name not in bound_names:
                    continue
                statement = ast.unparse(node)
                statements[statement] += 1
                sources[statement].add(candidate_path)
        ranked = sorted(
            statements,
            key=lambda statement: (
                -int(any(
                    Path(source).parts[:1] == current_parts[:1]
                    for source in sources[statement]
                )),
                -statements[statement],
                len(statement),
                statement,
            ),
        )[:max_candidates_per_name]
        for statement in ranked:
            candidates.append({
                "name": name,
                "statement": statement,
                "source_paths": tuple(sorted(sources[statement]))[:4],
                "occurrences": statements[statement],
            })
    return tuple(candidates)


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
    current_tree_hash: str = ""
    public_check_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_search_calls: int = 2
    max_read_calls: int = 4
    max_public_checks: int = 3
    search_calls: int = 0
    read_calls: int = 0
    public_check_calls: int = 0
    result_cache: dict[tuple[str, str, int | None, int | None], dict] = field(
        default_factory=dict
    )
    blocker: dict[str, Any] | None = None
    mechanical_recovery_anchors: tuple[dict[str, Any], ...] = ()
    staged_edit_version: int = 0
    reviewed_staged_version: int = -1
    finished_staged_version: int = -1
    staged_quality_rejected: bool = False
    staged_quality_error: str | None = None
    staged_quality_rejected_version: int = -1
    prohibited_staged_paths: set[str] = field(default_factory=set)
    rejected_staged_paths: set[str] = field(default_factory=set)
    last_rejected_staged_diff: str = ""

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
        if self.search_calls >= self.max_search_calls:
            raise ValueError("search budget exhausted for this revision")
        self.search_calls += 1
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
        key = (self.current_tree_hash, path, start_line, end_line)
        if key in self.result_cache:
            return dict(self.result_cache[key])
        if self.read_calls >= self.max_read_calls:
            raise ValueError("read budget exhausted for this revision")
        self.read_calls += 1
        source = self._path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start_line or 1)
        end = min(len(source), end_line or min(len(source), start + 399))
        if end < start or end - start > 500:
            raise ValueError("read range must contain at most 501 lines")
        result = {"path": path, "start_line": start, "end_line": end,
                  "content": "\n".join(source[start - 1:end])}
        self.result_cache[key] = dict(result)
        return result

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

    def _staged_diff(self) -> str:
        """Render the proposed edit set against the persistent working tree."""

        by_path: dict[str, list[ProposedEdit]] = {}
        for edit in self.staged_edits:
            by_path.setdefault(edit.relative_path, []).append(edit)
        chunks: list[str] = []
        for relative_path, path_edits in sorted(by_path.items()):
            path = self._path(relative_path)
            original = path.read_text(encoding="utf-8", errors="replace")
            lines = original.splitlines()
            for edit in sorted(
                path_edits, key=lambda item: item.start_line, reverse=True,
            ):
                lines[edit.start_line - 1:edit.end_line] = (
                    edit.replacement.rstrip("\n").splitlines()
                )
            candidate = "\n".join(lines)
            if original.endswith("\n"):
                candidate += "\n"
            chunks.extend(difflib.unified_diff(
                original.splitlines(keepends=True),
                candidate.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            ))
        return "".join(chunks)

    def _staged_edit_set_changes_only_imports(self) -> bool:
        """Detect a complete replacement that changes imports but no behavior."""

        by_path: dict[str, list[ProposedEdit]] = {}
        for edit in self.staged_edits:
            if not edit.relative_path.endswith(".py"):
                return False
            by_path.setdefault(edit.relative_path, []).append(edit)
        if not by_path:
            return False

        class RemoveImports(ast.NodeTransformer):
            def visit_Import(self, node):  # noqa: N802 - ast visitor API
                return None

            def visit_ImportFrom(self, node):  # noqa: N802 - ast visitor API
                return None

        changed = False
        for relative_path, edits in by_path.items():
            original = self._path(relative_path).read_text(
                encoding="utf-8", errors="replace",
            )
            lines = original.splitlines()
            for edit in sorted(edits, key=lambda item: item.start_line, reverse=True):
                lines[edit.start_line - 1:edit.end_line] = (
                    edit.replacement.rstrip("\n").splitlines()
                )
            candidate = "\n".join(lines)
            if original.endswith("\n"):
                candidate += "\n"
            original_tree = ast.parse(original, filename=relative_path)
            candidate_tree = ast.parse(candidate, filename=relative_path)
            if ast.dump(original_tree, include_attributes=False) == ast.dump(
                candidate_tree, include_attributes=False,
            ):
                continue
            changed = True
            original_without_imports = RemoveImports().visit(original_tree)
            candidate_without_imports = RemoveImports().visit(candidate_tree)
            ast.fix_missing_locations(original_without_imports)
            ast.fix_missing_locations(candidate_without_imports)
            if ast.dump(
                original_without_imports, include_attributes=False,
            ) != ast.dump(candidate_without_imports, include_attributes=False):
                return False
        return changed

    def discard_rejected_import_only_stage(self) -> bool:
        """Clear a rejected import-only stage so root recovery can edit behavior."""

        if not (
            self.staged_quality_rejected
            and self.staged_edits
            and self._staged_edit_set_changes_only_imports()
        ):
            return False
        return self.discard_quality_rejected_stage()

    def discard_quality_rejected_stage(self) -> bool:
        """Discard an uncheckpointed stage while retaining its review evidence.

        A stage rejected by the mandatory first-patch review is not a valid
        checkpoint. Keeping its edits active forces every later root-recovery
        turn through ``replace_staged_edits`` and can anchor the generator on the
        same copied body or helper-only mechanism. Preserve the exact diff and
        affected paths for the recovery packet, then reopen the transaction on
        the unchanged repository source.
        """

        # The controller calls this only after a
        # STAGED_PATCH_REVIEW_REJECTED revision. Some generator adapters report
        # that decision on GeneratorRevision without mutating the executor's
        # duplicate quality flag, so the unfinished transaction is the local
        # invariant that matters here.
        if not (
            self.staged_edits
            and self.finished_staged_version != self.staged_edit_version
        ):
            return False
        self.last_rejected_staged_diff = self._staged_diff()
        self.rejected_staged_paths.update(
            edit.relative_path.replace("\\", "/") for edit in self.staged_edits
        )
        self.staged_edits.clear()
        self.staged_edit_version += 1
        self.reviewed_staged_version = -1
        self.finished_staged_version = -1
        return True

    def show_current_diff(self) -> dict:
        staged_diff = self._staged_diff()
        self.reviewed_staged_version = self.staged_edit_version
        return {
            "diff": self.current_diff,
            "working_diff": self.current_diff,
            "staged_diff": staged_diff,
            "staged_edit_version": self.staged_edit_version,
            "review_is_current": True,
        }

    def run_public_check(self, check_id: str) -> dict:
        if self.public_check_calls >= self.max_public_checks:
            raise ValueError("public check budget exhausted for this revision")
        self.public_check_calls += 1
        if check_id in self.public_check_results:
            return dict(self.public_check_results[check_id])
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
        unused_import_failures: list[tuple[str, tuple[str, ...]]] = []
        for edit in edits:
            if not edit.replacement.strip() and not edit.expected_source.strip():
                raise ValueError("empty edit is not a repair")
            if edit.replacement.rstrip("\n") == edit.expected_source.rstrip("\n"):
                raise ValueError(
                    f"no-op edit is not a repair: {edit.relative_path}:{edit.start_line}"
                )
            path = self._path(edit.relative_path, for_edit=True)
            normalized_path = edit.relative_path.replace("\\", "/")
            if normalized_path in self.prohibited_staged_paths:
                raise ValueError(
                    "the mandatory initial review prohibited resubmitting the "
                    f"unjustified shared path {normalized_path!r}; replace the "
                    "complete edit set with the scoped component/call-site repair"
                )
            unchanged_extensions = _unchanged_path_definition_extensions(
                edit.expected_source, edit.replacement,
            )
            if unchanged_extensions:
                try:
                    original_tree = ast.parse(path.read_text(
                        encoding="utf-8", errors="replace",
                    ))
                except SyntaxError:
                    original_tree = ast.Module(body=[], type_ignores=[])
                existing_definition_names = {
                    node.name
                    for node in ast.walk(original_tree)
                    if isinstance(node, (
                        ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                    ))
                }
                unchanged_extensions = tuple(
                    name for name in unchanged_extensions
                    if name not in existing_definition_names
                )
            if unchanged_extensions:
                raise ValueError(
                    "candidate leaves the selected execution path unchanged and "
                    "only appends definition(s) "
                    + ", ".join(repr(name) for name in unchanged_extensions)
                    + ". Modify the existing reachable path and call any necessary "
                    "helper from that path in the same edit. If the selected "
                    "function lacks state required by the issue, inspect and edit "
                    "the bounded caller that owns that state instead of adding an "
                    "unused alternate entry point."
                )
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            expected_lines = edit.expected_source.rstrip("\n").splitlines()
            expected_source = edit.expected_source
            replacement = edit.replacement
            start = edit.start_line
            end = edit.end_line
            actual = "\n".join(lines[start - 1:end])

            def uniquely_nearest(candidates: list[int]) -> int | None:
                """Use a stale line anchor only when it selects one nearby match."""

                if not candidates:
                    return None
                ranked = sorted(
                    (abs(candidate - edit.start_line), candidate)
                    for candidate in candidates
                )
                distance, selected = ranked[0]
                if len(ranked) > 1 and ranked[1][0] == distance:
                    return None
                # Line-number drift from imports/comments is expected, but a
                # distant match is not reliable source evidence.
                maximum_drift = max(12, len(expected_lines) * 4)
                return selected if distance <= maximum_drift else None

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
                    nearest = uniquely_nearest(matches)
                    if nearest is None:
                        raise ValueError(
                            "expected source is ambiguous: "
                            f"{edit.relative_path}:{edit.start_line}"
                        )
                    start = nearest
                    end = start + len(expected_lines) - 1
                    relocated.append({
                        "path": edit.relative_path,
                        "from_start_line": edit.start_line,
                        "to_start_line": start,
                        "match": "nearest_line_anchor",
                    })
                else:
                    normalized_expected = [line.strip() for line in expected_lines]
                    normalized_matches = [
                        index + 1
                        for index in range(len(lines) - len(expected_lines) + 1)
                        if [
                            line.strip()
                            for line in lines[index:index + len(expected_lines)]
                        ] == normalized_expected
                    ]
                    if len(normalized_matches) == 1:
                        start = normalized_matches[0]
                        end = start + len(expected_lines) - 1
                        actual_lines = lines[start - 1:end]

                        def indentation(source_line: str) -> int:
                            return len(source_line) - len(source_line.lstrip())

                        expected_anchor = next(
                            (line for line in expected_lines if line.strip()), "",
                        )
                        actual_anchor = next(
                            (line for line in actual_lines if line.strip()), "",
                        )
                        shift = indentation(actual_anchor) - indentation(
                            expected_anchor
                        )
                        if shift:
                            adjusted = []
                            for line in replacement.rstrip("\n").splitlines():
                                if not line.strip():
                                    adjusted.append("")
                                    continue
                                width = max(0, indentation(line) + shift)
                                adjusted.append(" " * width + line.lstrip())
                            replacement = "\n".join(adjusted)
                        expected_source = "\n".join(actual_lines)
                        relocated.append({
                            "path": edit.relative_path,
                            "from_start_line": edit.start_line,
                            "to_start_line": start,
                            "match": "normalized_whitespace",
                        })
                    elif len(normalized_matches) > 1:
                        nearest = uniquely_nearest(normalized_matches)
                        if nearest is None:
                            raise ValueError(
                                "whitespace-normalized expected source is ambiguous: "
                                f"{edit.relative_path}:{edit.start_line}"
                            )
                        start = nearest
                        end = start + len(expected_lines) - 1
                        actual_lines = lines[start - 1:end]

                        def indentation(source_line: str) -> int:
                            return len(source_line) - len(source_line.lstrip())

                        expected_anchor = next(
                            (line for line in expected_lines if line.strip()), "",
                        )
                        actual_anchor = next(
                            (line for line in actual_lines if line.strip()), "",
                        )
                        shift = indentation(actual_anchor) - indentation(
                            expected_anchor
                        )
                        if shift:
                            adjusted = []
                            for line in replacement.rstrip("\n").splitlines():
                                if not line.strip():
                                    adjusted.append("")
                                    continue
                                width = max(0, indentation(line) + shift)
                                adjusted.append(" " * width + line.lstrip())
                            replacement = "\n".join(adjusted)
                        expected_source = "\n".join(actual_lines)
                        relocated.append({
                            "path": edit.relative_path,
                            "from_start_line": edit.start_line,
                            "to_start_line": start,
                            "match": "nearest_normalized_line_anchor",
                        })
                    else:
                        # A complete definition can be safely relocated even
                        # when a stale docstring or nearby comment prevents a
                        # textual match. Require the expected and replacement
                        # to parse as the same single definition and require a
                        # unique definition name in the real file.
                        def single_definition(source: str) -> ast.AST | None:
                            try:
                                parsed = ast.parse(textwrap.dedent(source))
                            except SyntaxError:
                                return None
                            definitions = [
                                node for node in parsed.body
                                if isinstance(node, (
                                    ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef,
                                ))
                            ]
                            if len(definitions) != 1 or len(parsed.body) != 1:
                                return None
                            return definitions[0]

                        expected_definition = single_definition(
                            edit.expected_source.rstrip("\n")
                        )
                        replacement_definition = single_definition(
                            edit.replacement.rstrip("\n")
                        )
                        same_definition = (
                            expected_definition is not None
                            and replacement_definition is not None
                            and type(expected_definition) is type(replacement_definition)
                            and getattr(expected_definition, "name", None)
                            == getattr(replacement_definition, "name", None)
                            and len(expected_lines) >= 3
                        )
                        if same_definition:
                            try:
                                actual_tree = ast.parse("\n".join(lines))
                            except SyntaxError:
                                actual_tree = ast.Module(body=[], type_ignores=[])
                            definition_matches = [
                                node for node in ast.walk(actual_tree)
                                if type(node) is type(expected_definition)
                                and getattr(node, "name", None)
                                == getattr(expected_definition, "name", None)
                            ]
                            if len(definition_matches) == 1:
                                definition = definition_matches[0]
                                definition_line = int(
                                    getattr(definition, "lineno", start)
                                )
                                decorator_lines = [
                                    int(getattr(decorator, "lineno", definition_line))
                                    for decorator in getattr(
                                        definition, "decorator_list", ()
                                    )
                                ]
                                start = min((definition_line, *decorator_lines))
                                end = int(getattr(definition, "end_lineno", start))
                                actual_lines = lines[start - 1:end]

                                def indentation(source_line: str) -> int:
                                    return len(source_line) - len(source_line.lstrip())

                                replacement_lines = replacement.rstrip("\n").splitlines()
                                expected_anchor = next(
                                    (line for line in replacement_lines if line.strip()), "",
                                )
                                actual_anchor = next(
                                    (line for line in actual_lines if line.strip()), "",
                                )
                                shift = indentation(actual_anchor) - indentation(
                                    expected_anchor
                                )
                                if shift:
                                    replacement_lines = [
                                        "" if not line.strip() else
                                        " " * max(0, indentation(line) + shift) + line.lstrip()
                                        for line in replacement_lines
                                    ]
                                replacement = "\n".join(replacement_lines)
                                expected_source = "\n".join(actual_lines)
                                relocated.append({
                                    "path": edit.relative_path,
                                    "from_start_line": edit.start_line,
                                    "to_start_line": start,
                                    "match": "unique_definition",
                                })

            # Models sometimes select a multi-line expression without its
            # final delimiter, then include that already-existing delimiter in
            # the replacement. Consume the exact adjacent overlap so the edit
            # cannot duplicate a closing bracket or trailing statement.
            replacement_lines = replacement.rstrip("\n").splitlines()
            normalized_expected_lines = expected_source.rstrip("\n").splitlines()
            if (
                normalized_expected_lines
                and replacement_lines[:len(normalized_expected_lines)]
                == normalized_expected_lines
            ):
                added_tail = replacement_lines[len(normalized_expected_lines):]
                # Consume the full exact adjacent overlap. A short prefix cap
                # allowed a copied method tail to survive after its first few
                # lines were canonicalized into the selected source range.
                following = lines[end:end + len(added_tail)]
                overlap = 0
                for added, existing in zip(added_tail, following):
                    # A blank separator is formatting owned by neither
                    # definition. Consuming it makes the line slice end in an
                    # empty line, which cannot be represented consistently by
                    # the expected_source contract after newline trimming.
                    if not added.strip():
                        break
                    if added != existing:
                        break
                    overlap += 1
                if overlap:
                    end += overlap
                    expected_source = "\n".join(lines[start - 1:end])
                    relocated.append({
                        "path": edit.relative_path,
                        "from_start_line": edit.start_line,
                        "to_start_line": start,
                        "match": "trailing_source_overlap",
                    })
            if replacement.rstrip("\n") == expected_source.rstrip("\n"):
                raise ValueError(
                    f"no-op edit is not a repair: {edit.relative_path}:{start}"
                )
            if start <= len(lines) and end > len(lines):
                # Model-provided ranges commonly count the final newline as a
                # source line.  The textual source contract is still exact,
                # but carrying that virtual line into the transactional
                # applier makes a single EOF edit look like it overlaps
                # itself.  Canonicalize the range at staging time.
                relocated.append({
                    "path": edit.relative_path,
                    "from_start_line": edit.start_line,
                    "to_start_line": start,
                    "from_end_line": edit.end_line,
                    "to_end_line": len(lines),
                    "match": "eof_newline_clamp",
                })
                end = len(lines)
            candidate.append(ProposedEdit(
                relative_path=edit.relative_path,
                start_line=start,
                end_line=end,
                expected_source=expected_source,
                replacement=replacement,
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
                raise ValueError(
                    f"expected source mismatch: {edit.relative_path}:{edit.start_line}; "
                    f"actual source at requested range: {actual!r}"
                )
            ranges = occupied.setdefault(edit.relative_path, [])
            if any(not (edit.end_line < start or edit.start_line > end) for start, end in ranges):
                raise ValueError("overlapping edits in one revision")
            ranges.append((edit.start_line, edit.end_line))
        if not candidate:
            raise ValueError("apply_edits requires at least one non-empty edit")
        by_path: dict[str, list[ProposedEdit]] = {}
        for edit in candidate:
            if edit.relative_path.endswith(".py"):
                by_path.setdefault(edit.relative_path, []).append(edit)
        for relative_path, path_edits in by_path.items():
            path = self._path(relative_path, for_edit=True)
            original_source = path.read_text(
                encoding="utf-8", errors="replace",
            )
            source_lines = original_source.splitlines()
            for edit in sorted(path_edits, key=lambda item: item.start_line, reverse=True):
                source_lines[edit.start_line - 1:edit.end_line] = (
                    edit.replacement.rstrip("\n").splitlines()
                )
            candidate_source = "\n".join(source_lines) + "\n"
            try:
                tree = ast.parse(candidate_source, filename=relative_path, type_comments=True)
            except SyntaxError as exc:
                raise ValueError(
                    f"candidate Python source is invalid: {relative_path}: {exc}"
                ) from exc
            try:
                original_tree = ast.parse(
                    original_source, filename=relative_path, type_comments=True,
                )
            except SyntaxError:
                original_tree = ast.Module(body=[], type_ignores=[])
            if ast.dump(tree, include_attributes=False) == ast.dump(
                original_tree, include_attributes=False,
            ):
                raise ValueError(
                    f"candidate has no executable Python AST change in {relative_path}; "
                    "comments, whitespace, or an equivalent restatement cannot repair "
                    "runtime behavior. Modify the reachable guard, expression, state "
                    "write, dispatch, exception, or return which causes the issue."
                )
            new_state_errors = (
                _state_transition_structure_errors(tree)
                - _state_transition_structure_errors(original_tree)
            )
            if new_state_errors:
                self.mechanical_recovery_anchors = _state_consumer_recovery_anchors(
                    relative_path=relative_path,
                    source=original_source,
                    tree=original_tree,
                    edits=path_edits,
                )
                recovery_hint = (
                    f" Located {len(self.mechanical_recovery_anchors)} exact "
                    "state-consumer guard anchor(s) for bounded recovery."
                    if self.mechanical_recovery_anchors
                    else ""
                )
                raise ValueError(
                    f"candidate introduces invalid state transition in {relative_path}: "
                    + "; ".join(sorted(new_state_errors))
                    + ". Preserve normalized state by computing both outgoing "
                    "(existing - incoming) and incoming-only (incoming - existing) "
                    "residuals. A mode switch may carry only the incoming-only "
                    "residual. Audit every empty-state producer before changing a "
                    "consumer guard; do not rewrite the consumer body."
                    + recovery_hint
                )
            new_alias_mutations = sorted(
                _caller_owned_mutation_errors(tree)
                - _caller_owned_mutation_errors(original_tree)
            )
            if new_alias_mutations:
                raise ValueError(
                    "candidate mutates caller-owned state in " + relative_path + ": "
                    + "; ".join(new_alias_mutations)
                    + ". Preserve ownership by cloning/copying the selected input "
                    "before the state write, then keep the public input unchanged."
                )

            new_protocol_coercions = sorted(
                _binary_protocol_coercion_errors(tree)
                - _binary_protocol_coercion_errors(original_tree)
            )
            if new_protocol_coercions:
                raise ValueError(
                    "candidate bypasses binary protocol dispatch in " + relative_path + ": "
                    + "; ".join(new_protocol_coercions)
                    + ". Inspect the forward and reflected operator paths. Preserve "
                    "the operand object and prefer the existing capability contract. "
                    "Use NotImplemented only when a concrete reflected method is "
                    "proven to accept the operand."
                )

            capability_bypasses = sorted(
                _binary_capability_bypass_errors(original_tree, tree)
            )
            if capability_bypasses:
                raise ValueError(
                    "candidate ignores existing binary capability contract in "
                    + relative_path + ": " + "; ".join(capability_bypasses)
                    + ". Test the compatible operand's published boolean capability "
                    "and retain TypeError for operands without it; do not rely on "
                    "unproven reflected dispatch."
                )

            new_presentation_adapters = sorted(
                _presentation_form_adapter_errors(tree)
                - _presentation_form_adapter_errors(original_tree)
            )
            if new_presentation_adapters:
                raise ValueError(
                    "candidate constructs an input/form adapter inside a presentation "
                    "path in " + relative_path + ": "
                    + "; ".join(new_presentation_adapters)
                    + ". Runtime display values must use the producer/model object's "
                    "stable configured serialization contract directly. Do not route "
                    "persisted values through a form factory merely to cover invalid "
                    "input representations that cannot reach this path."
                )

            nested_mapping_returns = sorted(
                _unguarded_nested_return_subscript_errors(tree)
                - _unguarded_nested_return_subscript_errors(original_tree)
            )
            if nested_mapping_returns:
                raise ValueError(
                    "candidate introduces an unguarded nested mapping lookup in "
                    + relative_path + ": " + "; ".join(nested_mapping_returns)
                    + ". Preserve the valid missing-key path with an existing "
                    "fallback, a membership guard, or mapping.get(); do not broaden "
                    "the public KeyError surface without executable authority."
                )

            partial_index_repairs = sorted(
                _partial_rectangular_index_fix_errors(original_tree, tree)
            )
            if partial_index_repairs:
                raise ValueError(
                    "candidate repairs only one rectangular-index boundary in "
                    + relative_path + ": " + "; ".join(partial_index_repairs)
                    + ". Inspect and repair the structurally equivalent sibling "
                    "predicates in the same component."
                )
            duplicate_blocks = (
                _duplicate_statement_block_errors(tree)
                - _duplicate_statement_block_errors(original_tree)
            )
            if duplicate_blocks:
                raise ValueError(
                    f"candidate duplicates an existing statement block in {relative_path}: "
                    + "; ".join(sorted(duplicate_blocks))
                    + ". Modify the smallest existing guard or statement; do not copy "
                    "the consumer body, loops, callbacks, or sibling branch."
                )

            placeholder_errors = (
                _placeholder_definition_errors(tree)
                - _placeholder_definition_errors(original_tree)
            )
            if placeholder_errors:
                raise ValueError(
                    "candidate adds a placeholder definition with no executable "
                    "repair behavior in " + relative_path + ": "
                    + "; ".join(sorted(placeholder_errors))
                    + ". Modify the existing causal method instead of adding a "
                    "pass/ellipsis-only sibling hook."
                )

            reversed_set_errors = (
                _reversed_set_operation_errors(tree)
                - _reversed_set_operation_errors(original_tree)
            )
            if reversed_set_errors:
                raise ValueError(
                    "candidate reverses a set operation onto an input iterable: "
                    + "; ".join(sorted(reversed_set_errors))
                    + ". Normalize the incoming iterable with set/frozenset "
                    "before computing the incoming-minus-existing residual."
                )

            # Validate literal command dispatch at staging time.  Waiting for
            # post-checkpoint mechanical execution would allow an invented
            # ``**options`` keyword to become the saved first patch even though
            # the public command never declares it.
            added_lines: set[int] = set()
            line_offset = 0
            for edit in sorted(path_edits, key=lambda item: item.start_line):
                adjusted_start = edit.start_line + line_offset
                replacement_count = len(edit.replacement.rstrip("\n").splitlines())
                original_count = edit.end_line - edit.start_line + 1
                added_lines.update(range(
                    adjusted_start,
                    adjusted_start + max(1, replacement_count),
                ))
                line_offset += replacement_count - original_count
            from reachpatch.execution.mechanical import (
                _unsupported_literal_command_options,
            )
            command_option_errors = _unsupported_literal_command_options(
                self.repository_root, tree, relative_path, added_lines,
            )
            if command_option_errors:
                raise ValueError(
                    "candidate dispatches a literal command with an undeclared "
                    "option: " + "; ".join(command_option_errors)
                )

            unresolved_names = sorted(
                _new_unresolved_name_errors(original_tree, tree)
            )
            if unresolved_names:
                import_candidates = _import_candidates_for_names(
                    self.repository_root,
                    self.repository_index,
                    relative_path,
                    unresolved_names,
                )
                self.mechanical_recovery_anchors = ({
                    "kind": "UNRESOLVED_DIRECT_NAME",
                    "relative_path": relative_path,
                    "unresolved_names": tuple(unresolved_names),
                    "import_candidates": import_candidates,
                    "rejected_behavior_edits": tuple({
                        "relative_path": edit.relative_path,
                        "start_line": edit.start_line,
                        "end_line": edit.end_line,
                        "expected_source": edit.expected_source,
                        "replacement": edit.replacement,
                    } for edit in candidate),
                },)
                candidate_hint = ""
                if import_candidates:
                    candidate_hint = " Import candidates: " + ", ".join(
                        repr(item["statement"])
                        for item in import_candidates
                    ) + "."
                raise ValueError(
                    "candidate introduces unresolved direct name(s) in "
                    f"{relative_path}: {', '.join(unresolved_names)}. Add the "
                    "required import, assignment, or parameter in the same complete "
                    "edit set as the behavior change; do not submit a separate "
                    "import-only replacement."
                    + candidate_hint
                )

            unused_imports = sorted(
                _new_unused_import_errors(original_tree, tree)
            )
            if unused_imports:
                unused_import_failures.append((
                    relative_path, tuple(unused_imports),
                ))

            duplicate_errors = sorted(
                _shadowing_definition_errors(tree)
                - _shadowing_definition_errors(original_tree)
            )
            if duplicate_errors:
                raise ValueError(
                    f"candidate introduces shadowing definitions in {relative_path}: "
                    + "; ".join(duplicate_errors)
                    + ". Edit the existing definition instead of adding another one."
                )
            duplicate_assignments = sorted(
                _duplicate_scope_assignment_errors(tree)
                - _duplicate_scope_assignment_errors(original_tree)
            )
            if duplicate_assignments:
                raise ValueError(
                    f"candidate introduces duplicate assignment in {relative_path}: "
                    + "; ".join(duplicate_assignments)
                    + ". Edit the existing executable guard, expression, call, "
                    "state write, exception, return, or assignment instead of "
                    "copying an unchanged module/class value."
                )
        previous_length = len(self.staged_edits)
        self.staged_edits.extend(candidate)
        if self._staged_edit_set_changes_only_imports():
            del self.staged_edits[previous_length:]
            raise ValueError(
                "apply_edits must include a reachable behavior change; an "
                "import-only edit cannot implement the issue. Submit the import "
                "and the existing guard/expression/state/exception/return change "
                "that uses it in the same complete edit set."
            )
        if unused_import_failures:
            del self.staged_edits[previous_length:]
            rejected = tuple({
                "relative_path": edit.relative_path,
                "start_line": edit.start_line,
                "end_line": edit.end_line,
                "expected_source": edit.expected_source,
                "replacement": edit.replacement,
            } for edit in candidate)
            self.mechanical_recovery_anchors = tuple({
                "kind": "UNUSED_DIRECT_IMPORT",
                "relative_path": relative_path,
                "unused_names": names,
                "rejected_behavior_edits": rejected,
            } for relative_path, names in unused_import_failures)
            details = "; ".join(
                f"{relative_path}: {', '.join(names)}"
                for relative_path, names in unused_import_failures
            )
            raise ValueError(
                "candidate adds unused direct import(s) in "
                + details
                + ". Importing the API named by the issue is not a repair unless "
                "an existing reachable guard, call, expression, state write, "
                "exception, or return uses it in the same complete edit set. "
                "Remove the unnecessary import or connect it to the causal "
                "execution path."
            )
        self.staged_edit_version += 1
        self.reviewed_staged_version = -1
        self.finished_staged_version = -1
        return {"accepted": True, "edit_count": len(candidate),
                "paths": sorted({item.relative_path for item in candidate}),
                "relocated": relocated,
                "staged_edit_version": self.staged_edit_version}

    def complete_unresolved_name_edits(
        self,
        *,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """Complete a rejected behavior edit with a mechanically sourced import.

        ``apply_edits`` deliberately rejects a new direct name that isn't bound in
        the candidate module.  The rejected executable edit and bounded repository
        import candidates are retained in ``mechanical_recovery_anchors``.  This
        method closes that mechanical gap without asking the model to reconstruct
        either source span: it selects the smallest existing import that binds each
        missing name, inserts it after any module docstring/``__future__`` imports,
        and revalidates the complete behavior-plus-import edit set through the same
        AST and policy gates as every other patch.

        No module path is guessed.  Completion is refused unless every unresolved
        name has a real import occurrence in the repository index and a non-
        overlapping insertion point exists.  The resulting staged diff still goes
        through the normal mandatory review before it can become the first patch.
        """

        anchors = tuple(
            anchor for anchor in self.mechanical_recovery_anchors
            if str(anchor.get("kind", "")) == "UNRESOLVED_DIRECT_NAME"
        )
        if not anchors:
            raise ValueError("no unresolved-name recovery anchor is available")
        anchor = anchors[-1]
        relative_path = str(anchor.get("relative_path", ""))
        unresolved_names = tuple(map(str, anchor.get("unresolved_names", ())))
        raw_candidates = tuple(anchor.get("import_candidates", ()))
        raw_behavior_edits = tuple(anchor.get("rejected_behavior_edits", ()))
        if not relative_path or not unresolved_names or not raw_behavior_edits:
            raise ValueError("unresolved-name recovery anchor is incomplete")

        def parsed_import(statement: str) -> tuple[ast.stmt, set[str]] | None:
            try:
                parsed = ast.parse(statement)
            except SyntaxError:
                return None
            if len(parsed.body) != 1 or not isinstance(
                parsed.body[0], (ast.Import, ast.ImportFrom),
            ):
                return None
            node = parsed.body[0]
            bound = {
                alias.asname or (
                    alias.name.split(".", 1)[0]
                    if isinstance(node, ast.Import) else alias.name
                )
                for alias in node.names
                if alias.name != "*"
            }
            return node, bound

        chosen_statements: list[str] = []
        covered_names: set[str] = set()
        for name in unresolved_names:
            viable: list[tuple[int, int, int, str, set[str]]] = []
            for item in raw_candidates:
                if str(item.get("name", "")) != name:
                    continue
                statement = str(item.get("statement", "")).strip()
                parsed = parsed_import(statement)
                if parsed is None or name not in parsed[1]:
                    continue
                viable.append((
                    len(parsed[1]),
                    -int(item.get("occurrences", 0) or 0),
                    len(statement),
                    statement,
                    parsed[1],
                ))
            if not viable:
                raise ValueError(
                    f"no mechanically sourced import binds unresolved name {name!r}"
                )
            _width, _frequency, _length, statement, bound = min(viable)
            if statement not in chosen_statements:
                chosen_statements.append(statement)
            covered_names.update(bound)
        missing_names = sorted(set(unresolved_names) - covered_names)
        if missing_names:
            raise ValueError(
                "mechanical import completion does not bind: "
                + ", ".join(missing_names)
            )

        behavior_edits = tuple(ProposedEdit(**dict(item)) for item in raw_behavior_edits)
        path = self._path(relative_path, for_edit=True)
        source = path.read_text(encoding="utf-8", errors="replace")
        source_lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=relative_path, type_comments=True)
        except SyntaxError as exc:
            raise ValueError(
                f"cannot place recovery import in invalid Python source: {relative_path}"
            ) from exc

        existing_imports = {
            ast.unparse(node)
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        statements_to_add = tuple(
            statement for statement in chosen_statements
            if statement not in existing_imports
        )
        if not statements_to_add:
            raise ValueError(
                "mechanically selected imports already exist but names remain unresolved"
            )

        occupied = {
            line
            for edit in behavior_edits
            if edit.relative_path == relative_path
            for line in range(edit.start_line, edit.end_line + 1)
        }
        body = list(tree.body)
        body_index = 0
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body_index = 1
        while (
            body_index < len(body)
            and isinstance(body[body_index], ast.ImportFrom)
            and body[body_index].module == "__future__"
        ):
            body_index += 1

        candidate_lines = [
            int(getattr(node, "lineno", 0))
            for node in body[body_index:]
            if int(getattr(node, "lineno", 0)) > 0
        ]
        insertion_line = next(
            (line for line in candidate_lines if line not in occupied),
            0,
        )
        import_block = "\n".join(statements_to_add)
        if insertion_line:
            expected = source_lines[insertion_line - 1]
            import_edit = ProposedEdit(
                relative_path=relative_path,
                start_line=insertion_line,
                end_line=insertion_line,
                expected_source=expected,
                replacement=import_block + "\n" + expected,
            )
        else:
            # A module containing only a docstring and/or future imports has no
            # following statement to prepend. Append after the last such node by
            # replacing its final source line, provided the line isn't part of the
            # rejected behavior edit.
            anchor_node = body[-1] if body else None
            append_line = int(getattr(
                anchor_node, "end_lineno", getattr(anchor_node, "lineno", 0),
            ))
            if append_line <= 0 or append_line in occupied:
                raise ValueError(
                    "no non-overlapping import insertion point is available"
                )
            expected = source_lines[append_line - 1]
            import_edit = ProposedEdit(
                relative_path=relative_path,
                start_line=append_line,
                end_line=append_line,
                expected_source=expected,
                replacement=expected + "\n\n" + import_block,
            )

        complete_edits = (import_edit, *behavior_edits)
        result = (
            self.replace_staged_edits(complete_edits)
            if replace_existing else self.apply_edits(complete_edits)
        )
        result["mechanical_completion"] = "UNRESOLVED_DIRECT_NAME"
        result["resolved_names"] = list(unresolved_names)
        result["inserted_imports"] = list(statements_to_add)
        return result

    def replace_staged_edits(self, edits: Iterable[ProposedEdit]) -> dict:
        """Replace, rather than append to, the uncheckpointed first-patch edit set."""

        proposed = tuple(edits)
        effective = tuple(
            edit for edit in proposed
            if edit.replacement.rstrip("\n") != edit.expected_source.rstrip("\n")
        )
        dropped_noop_count = len(proposed) - len(effective)
        if not effective:
            raise ValueError(
                "replace_staged_edits contains no executable source change"
            )
        previous = list(self.staged_edits)
        previous_version = self.staged_edit_version
        previous_reviewed_version = self.reviewed_staged_version
        previous_finished_version = self.finished_staged_version
        self.staged_edits.clear()
        try:
            result = self.apply_edits(effective)
            if self._staged_edit_set_changes_only_imports():
                raise ValueError(
                    "replace_staged_edits is a complete edit-set operation; an "
                    "import-only replacement has no reachable repair behavior. "
                    "Include the existing method/guard/state/exception/return "
                    "change that uses the import."
                )
        except Exception as exc:
            self.staged_edits[:] = previous
            self.staged_edit_version = previous_version
            self.reviewed_staged_version = previous_reviewed_version
            self.finished_staged_version = previous_finished_version
            if (
                isinstance(exc, ValueError)
                and "import-only edit" in str(exc)
            ):
                raise ValueError(
                    "replace_staged_edits is a complete edit-set operation; an "
                    "import-only replacement has no reachable repair behavior. "
                    "Include the existing method/guard/state/exception/return "
                    "change that uses the import."
                ) from exc
            raise
        result["replaced_edit_count"] = len(previous)
        result["dropped_noop_edit_count"] = dropped_noop_count
        result["replacement_is_complete_edit_set"] = True
        return result

    def finish_revision(self, summary: str) -> dict:
        if not self.staged_edits:
            raise ValueError("revision has no staged edits")
        if self.reviewed_staged_version != self.staged_edit_version:
            raise ValueError(
                "the latest staged edit set has not been reviewed; call "
                "show_current_diff after the most recent apply_edits or "
                "replace_staged_edits, then finish the revision"
            )
        self.finished_staged_version = self.staged_edit_version
        # Quality rejection belongs to this transactional staged edit set, not
        # to one model invocation. Bounded initial recovery reuses the executor,
        # so clear the rejection only after a reviewed edit set is finished.
        self.staged_quality_rejected = False
        self.staged_quality_error = None
        self.staged_quality_rejected_version = -1
        self.prohibited_staged_paths.clear()
        self.rejected_staged_paths.clear()
        return {"finished": True, "summary": summary,
                "edit_count": len(self.staged_edits),
                "reviewed_staged_version": self.reviewed_staged_version}

    def declare_blocker(self, reason: str, missing_evidence: Iterable[str] = ()) -> dict:
        if self.staged_edits:
            raise ValueError("cannot declare a blocker after staging edits")
        self.blocker = {
            "reason": str(reason),
            "missing_evidence": tuple(map(str, missing_evidence)),
        }
        return {"blocked": True, **self.blocker}
