from __future__ import annotations

import re
import itertools
from dataclasses import dataclass, field
from typing import Any, Iterable

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.requirement_graph.domains import ConstraintCompiler, default_domain_values

_REFERENCE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_MODULE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_ALLOWED_OPERATORS = {
    "add", "sub", "mul", "matmul", "truediv", "floordiv", "mod", "pow",
    "lshift", "rshift", "or", "xor", "and", "lt", "le", "eq", "ne", "gt", "ge",
    "contains", "getitem", "truth", "length", "iterate",
}
_ALLOWED_STEPS = {
    "import", "construct", "container", "set_field", "call", "operator",
    "state_snapshot", "delete", "sequence", "observe",
}


@dataclass(frozen=True, slots=True)
class ResourceLimits(SerializableRecord):
    timeout_seconds: float = 120.0
    cpu_seconds: int = 120
    memory_bytes: int = 1_073_741_824
    output_bytes: int = 4_194_304
    open_files: int = 256
    process_count: int = 32

    def __post_init__(self) -> None:
        if any(value <= 0 for value in self.to_dict().values()):
            raise ValueError("resource limits must be positive")


@dataclass(frozen=True, slots=True)
class TraceSpec(SerializableRecord):
    trace_id: str
    steps: tuple[dict[str, Any], ...]
    reset_before: bool
    relation_role: str


@dataclass(frozen=True, slots=True)
class InputRecipe(SerializableRecord):
    recipe_id: str
    imports: tuple[dict[str, Any], ...]
    setup: tuple[dict[str, Any], ...]
    stimulus: tuple[dict[str, Any], ...]
    observations: tuple[dict[str, Any], ...]
    teardown: tuple[dict[str, Any], ...]
    traces: tuple[TraceSpec, ...]
    environment: dict[str, str]
    resource_limits: ResourceLimits
    allow_network: bool
    allow_subprocess: bool
    max_iteration_items: int
    provenance_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        imports: Iterable[dict[str, Any]] = (),
        setup: Iterable[dict[str, Any]] = (),
        stimulus: Iterable[dict[str, Any]] = (),
        observations: Iterable[dict[str, Any]] = (),
        teardown: Iterable[dict[str, Any]] = (),
        traces: Iterable[TraceSpec] = (),
        environment: dict[str, str] | None = None,
        resource_limits: ResourceLimits | None = None,
        allow_network: bool = False,
        allow_subprocess: bool = False,
        max_iteration_items: int = 1000,
        provenance_ids: Iterable[str] = (),
    ) -> "InputRecipe":
        fields = {
            "imports": tuple(dict(item) for item in imports),
            "setup": tuple(dict(item) for item in setup),
            "stimulus": tuple(dict(item) for item in stimulus),
            "observations": tuple(dict(item) for item in observations),
            "teardown": tuple(dict(item) for item in teardown),
            "traces": tuple(traces),
            "environment": dict(environment or {}),
            "resource_limits": resource_limits or ResourceLimits(),
            "allow_network": allow_network,
            "allow_subprocess": allow_subprocess,
            "max_iteration_items": max_iteration_items,
            "provenance_ids": tuple(provenance_ids),
        }
        recipe_id = stable_id("input-recipe", fields)
        recipe = cls(recipe_id=recipe_id, **fields)
        RecipeCompiler().validate(recipe)
        return recipe


@dataclass(frozen=True, slots=True)
class RecipeValidation(SerializableRecord):
    valid: bool
    errors: tuple[str, ...]
    unresolved_references: tuple[str, ...]
    source_mutation_attempts: tuple[str, ...]


class RecipeCompiler:
    """Validate the declarative recipe language without executing source."""

    def validate(self, recipe: InputRecipe) -> RecipeValidation:
        errors: list[str] = []
        unresolved: list[str] = []
        mutation: list[str] = []
        aliases: set[str] = set()
        for step in recipe.imports + recipe.setup + recipe.stimulus + recipe.observations + recipe.teardown:
            operation = step.get("op")
            if operation not in _ALLOWED_STEPS:
                errors.append(f"unsupported operation {operation!r}")
                continue
            if operation == "import":
                module = str(step.get("module", ""))
                alias = str(step.get("as", module.rsplit(".", 1)[-1]))
                if not _MODULE.fullmatch(module):
                    errors.append(f"invalid module {module!r}")
                if not alias.isidentifier():
                    errors.append(f"invalid import alias {alias!r}")
                aliases.add(alias)
            if operation in {"construct", "call"}:
                target = str(step.get("target", ""))
                if not _REFERENCE.fullmatch(target):
                    errors.append(f"invalid target reference {target!r}")
                elif target.split(".", 1)[0] not in aliases and "." in target:
                    unresolved.append(target)
            if operation == "operator" and step.get("operator") not in _ALLOWED_OPERATORS:
                errors.append(f"unsupported operator {step.get('operator')!r}")
            if operation == "set_field":
                target = str(step.get("target", ""))
                if target.split(".", 1)[0] not in aliases:
                    mutation.append(target)
            if operation == "delete" and step.get("kind") in {"file", "directory", "source"}:
                mutation.append(str(step))
            save_as = step.get("save_as")
            if save_as is not None:
                if not isinstance(save_as, str) or not save_as.isidentifier():
                    errors.append(f"invalid save_as {save_as!r}")
                else:
                    aliases.add(save_as)
        for trace in recipe.traces:
            if not trace.trace_id or not trace.steps:
                errors.append("multi-trace entries require an id and steps")
            for step in trace.steps:
                if step.get("op") not in _ALLOWED_STEPS:
                    errors.append(f"unsupported trace operation {step.get('op')!r}")
        if recipe.allow_network:
            errors.append("external network recipes are not admitted as correctness checks")
        if recipe.max_iteration_items < 1:
            errors.append("max_iteration_items must be positive")
        validation = RecipeValidation(
            valid=not errors and not unresolved and not mutation,
            errors=tuple(errors),
            unresolved_references=tuple(unresolved),
            source_mutation_attempts=tuple(mutation),
        )
        if not validation.valid:
            raise ValueError(f"invalid InputRecipe: {validation.to_dict()}")
        return validation


class CandidateGenerator:
    """Enumerate deterministic finite witnesses for one quantified leaf partition."""

    def generate(self, leaf, partition, *, limit: int = 32) -> tuple[dict[str, Any], ...]:
        if limit < 1:
            raise ValueError("candidate limit must be positive")
        try:
            predicates = [
                ConstraintCompiler().compile(item) for item in partition.constraints
            ]
        except (SyntaxError, ValueError):
            return ()
        domain_by_id = {item.domain_id: item for item in leaf.domains}
        candidate_sets = []
        for variable in leaf.quantified_variables:
            domain = domain_by_id.get(variable.domain_id)
            if domain is None:
                return ()
            candidate_sets.append(default_domain_values(domain))
        if not candidate_sets:
            return ({},) if all(predicate({}) for predicate in predicates) else ()
        emitted = []
        for values in itertools.product(*candidate_sets):
            bindings = {
                variable.name: value
                for variable, value in zip(leaf.quantified_variables, values, strict=True)
            }
            if all(predicate(bindings) for predicate in predicates):
                emitted.append(bindings)
                if len(emitted) >= limit:
                    break
        return tuple(emitted)


def recipe_from_scenario(scenario) -> InputRecipe:
    observations = tuple(
        {"op": "observe", "channel": channel, "source": "result"}
        for channel in scenario.observe.channels
    )
    return InputRecipe.create(
        imports=scenario.setup,
        stimulus=scenario.stimulus,
        observations=observations,
        environment={},
        resource_limits=ResourceLimits(timeout_seconds=scenario.timeout_seconds),
        allow_network=False,
        provenance_ids=(scenario.scenario_id,) + tuple(scenario.evidence_ids),
    )
