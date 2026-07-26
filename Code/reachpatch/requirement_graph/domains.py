from __future__ import annotations

import ast
import itertools
import re
from dataclasses import dataclass
from typing import Any, Iterable

from reachpatch.models.base import SerializableRecord, content_hash, stable_id
from reachpatch.models.enums import Authority
from reachpatch.requirement_graph.models import (
    DomainPartition,
    DomainSpec,
    QuantifiedVariable,
    RequirementLeaf,
)

_SAFE_CALLS = {"len", "bool", "isinstance", "type", "abs", "all", "any"}
_SAFE_TYPES = {
    "int": int,
    "float": float,
    "str": str,
    "bytes": bytes,
    "list": list,
    "tuple": tuple,
    "dict": dict,
    "set": set,
    "bool": bool,
    "object": object,
}
_CONSTRAINT_RESULT_CACHE: dict[str, ConstraintResult] = {}
_CONSTRAINT_RESULT_CACHE_LIMIT = 8192


@dataclass(frozen=True, slots=True)
class ConstraintResult(SerializableRecord):
    satisfiable: bool
    witness: dict[str, Any] | None
    tried: int
    reason: str
    complete: bool = True


class ConstraintCompiler:
    """Safe evaluator for the finite, repository-derived partition language."""

    ALLOWED_NODES = {
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.USub,
        ast.UAdd,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
        ast.Name,
        ast.Load,
        ast.Constant,
        ast.Call,
        ast.Tuple,
        ast.List,
        ast.Set,
        ast.Dict,
        ast.Subscript,
        ast.Slice,
        ast.Attribute,
    }

    def compile(self, expression: str):
        tree = ast.parse(expression or "True", mode="eval")
        for node in ast.walk(tree):
            if type(node) not in self.ALLOWED_NODES:
                raise ValueError(f"unsupported constraint syntax: {type(node).__name__}")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_CALLS:
                    raise ValueError("constraint calls are limited to safe pure builtins")
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise ValueError("dunder attributes are forbidden in constraints")
        code = compile(tree, "<reachpatch-constraint>", "eval")

        def evaluate(bindings: dict[str, Any]) -> bool:
            namespace = {**_SAFE_TYPES, **{name: globals()[name] for name in ()}, **bindings}
            builtins = {name: __builtins__[name] for name in _SAFE_CALLS} if isinstance(__builtins__, dict) else {
                name: getattr(__builtins__, name) for name in _SAFE_CALLS
            }
            try:
                return bool(eval(code, {"__builtins__": builtins}, namespace))
            except (AttributeError, IndexError, KeyError, NameError, TypeError, ValueError, ZeroDivisionError):
                return False

        return evaluate


def _deduplicate_values(values: Iterable[Any]) -> tuple[Any, ...]:
    unique: dict[str, Any] = {}
    for value in values:
        key = repr((type(value).__qualname__, value))
        unique.setdefault(key, value)
    return tuple(unique.values())


def default_domain_values(domain: DomainSpec) -> tuple[Any, ...]:
    values: list[Any] = list(domain.literal_values)
    for type_name in domain.type_names:
        values.extend({
            "NoneType": [None],
            "bool": [False, True],
            "int": [-1, 0, 1, 2],
            "float": [-1.0, 0.0, 0.5, 1.0],
            "str": ["", "x", "0"],
            "bytes": [b"", b"x"],
            "list": [[], [0], [0, 1]],
            "tuple": [(), (0,), (0, 1)],
            "dict": [{}, {"k": 1}],
            "set": [set(), {0}],
            "Any": [None, False, True, -1, 0, 1, "", "x", [], [0], {}, {"k": 1}],
        }.get(type_name, []))
    if domain.lower_bound is not None:
        values.extend([domain.lower_bound, domain.lower_bound + 1])
    if domain.upper_bound is not None:
        values.extend([domain.upper_bound, domain.upper_bound - 1])
    return _deduplicate_values(values)


def solve_constraints(
    variables: Iterable[QuantifiedVariable],
    domains: Iterable[DomainSpec],
    constraints: Iterable[str],
    *,
    max_combinations: int = 4096,
) -> ConstraintResult:
    variables = tuple(variables)
    domains = tuple(domains)
    expressions = tuple(expression for expression in constraints if expression.strip()) or ("True",)
    cache_key = content_hash({
        "variables": [item.to_dict() for item in variables],
        "domains": [item.to_dict() for item in domains],
        "constraints": expressions,
        "max_combinations": max_combinations,
    })
    cached = _CONSTRAINT_RESULT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    def remember(result: ConstraintResult) -> ConstraintResult:
        if len(_CONSTRAINT_RESULT_CACHE) >= _CONSTRAINT_RESULT_CACHE_LIMIT:
            _CONSTRAINT_RESULT_CACHE.pop(next(iter(_CONSTRAINT_RESULT_CACHE)))
        _CONSTRAINT_RESULT_CACHE[cache_key] = result
        return result

    domain_by_id = {domain.domain_id: domain for domain in domains}
    compiler = ConstraintCompiler()
    try:
        predicates = [compiler.compile(expression) for expression in expressions]
    except (SyntaxError, ValueError) as exc:
        return remember(ConstraintResult(False, None, 0, f"unsupported_constraint:{exc}"))
    candidate_sets = []
    for variable in variables:
        domain = domain_by_id.get(variable.domain_id)
        if domain is None:
            return remember(ConstraintResult(False, None, 0, f"missing_domain:{variable.domain_id}"))
        candidate_sets.append(default_domain_values(domain))
    if not variables:
        candidate_sets = [tuple([None])]
    tried = 0
    products = itertools.product(*candidate_sets)
    for values in products:
        tried += 1
        if tried > max_combinations:
            # A cap is an analysis boundary, never an UNSAT proof.  Callers
            # must retain this partition as an explicit frontier instead of
            # turning an open-world domain into PROVED_INFEASIBLE.
            return remember(ConstraintResult(
                False, None, tried - 1, "finite_domain_cap_exceeded", complete=False
            ))
        bindings = (
            {variable.name: value for variable, value in zip(variables, values, strict=True)}
            if variables
            else {}
        )
        if all(predicate(bindings) for predicate in predicates):
            return remember(ConstraintResult(
                True, bindings, tried, "finite_domain_witness", complete=True
            ))
    # Exhaustion is a proof only for a closed finite domain.  DomainSpec is
    # open-world by default, so preserve the unresolved status in the proof.
    complete = all(not domain.open_world for domain in domain_by_id.values())
    return remember(ConstraintResult(
        False,
        None,
        tried,
        "finite_domain_exhausted" if complete else "open_world_unresolved",
        complete=complete,
    ))


def infer_domains(formula: str, variable_names: Iterable[str]) -> tuple[DomainSpec, ...]:
    constants: list[Any] = []
    for token in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?|(['\"])(.*?)\1", formula):
        raw = token[0] or token[1]
        if raw in {"'", '"'}:
            raw = token[1]
        try:
            constants.append(ast.literal_eval(raw))
        except (SyntaxError, ValueError):
            if raw:
                constants.append(raw)
    domains: list[DomainSpec] = []
    lowered = formula.lower()
    for name in sorted(set(variable_names)):
        type_names: set[str] = set()
        if re.search(rf"isinstance\s*\(\s*{re.escape(name)}\s*,\s*int", formula):
            type_names.add("int")
        if any(word in lowered for word in ("empty", "nonempty", "container", "sequence", "iterable")):
            type_names.update({"list", "tuple", "str"})
        if re.search(rf"\b{re.escape(name)}\s+is\s+(?:not\s+)?none", lowered):
            type_names.add("NoneType")
        if not type_names:
            type_names.add("Any")
        domain_id = stable_id("domain", name, sorted(type_names), constants)
        domains.append(DomainSpec(
            domain_id=domain_id,
            variable=name,
            type_names=tuple(sorted(type_names)),
            literal_values=_deduplicate_values(constants),
            container_shapes=("empty", "nonempty") if type_names & {"list", "tuple", "str"} else (),
            open_world=True,
        ))
    return tuple(domains)


def branch_partition_constraints(predicate: str) -> list[tuple[str, ...]]:
    expression = predicate.strip() or "True"
    partitions: list[tuple[str, ...]] = [(expression,), (f"not ({expression})",)]
    truthiness = re.fullmatch(r"not\s+([A-Za-z_]\w*)", expression)
    direct_name = re.fullmatch(r"([A-Za-z_]\w*)", expression)
    name = truthiness.group(1) if truthiness else direct_name.group(1) if direct_name else None
    if name:
        partitions.extend([
            (f"len({name}) == 0",),
            (f"len({name}) > 0",),
            (f"bool({name}) is False",),
            (f"bool({name}) is True",),
        ])
    comparison = re.fullmatch(r"([A-Za-z_]\w*)\s*(<|<=|>|>=|==|!=)\s*(-?\d+(?:\.\d+)?)", expression)
    if comparison:
        variable, _, boundary = comparison.groups()
        partitions.extend([
            (f"{variable} < {boundary}",),
            (f"{variable} == {boundary}",),
            (f"{variable} > {boundary}",),
        ])
    unique: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for item in partitions:
        if item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def symbolic_scenario_partitions(
    leaf: RequirementLeaf,
    *,
    guard_predicates: Iterable[str] = (),
    max_combinations: int = 4096,
) -> tuple[DomainPartition, ...]:
    constraint_groups: list[tuple[str, ...]] = []
    if leaf.precondition and leaf.precondition != "True":
        constraint_groups.append((leaf.precondition,))
    for predicate in guard_predicates:
        constraint_groups.extend(branch_partition_constraints(predicate))
    if not constraint_groups:
        constraint_groups.append(("True",))
    partitions: list[DomainPartition] = []
    for constraints in constraint_groups:
        result = solve_constraints(
            leaf.quantified_variables,
            leaf.domains,
            constraints,
            max_combinations=max_combinations,
        )
        proof = result.to_dict()
        # A witness proves existence only.  Universal obligations over an
        # open-world domain need an additional finite partition/exhaustiveness
        # proof before they may enter Reach.
        proof["coverage_complete"] = bool(
            result.complete and all(not domain.open_world for domain in leaf.domains)
        )
        partition_id = stable_id("partition", leaf.leaf_id, constraints)
        partitions.append(DomainPartition(
            partition_id=partition_id,
            variable_names=tuple(variable.name for variable in leaf.quantified_variables),
            constraints=constraints,
            candidate_bindings=(result.witness,) if result.witness is not None else (),
            source="requirement_and_program_guards",
            scope="REQUIREMENT" if leaf.mandatory else "CHALLENGE_ONLY",
            satisfiable=result.satisfiable,
            proof=proof,
            witness_ids=leaf.witnesses,
            leaf_id=leaf.leaf_id,
        ))
    return tuple(sorted(partitions, key=lambda partition: partition.partition_id))


def promote_program_predicates(
    leaf: RequirementLeaf,
    predicates: Iterable[str],
) -> tuple[DomainPartition, ...]:
    variable_names = {variable.name for variable in leaf.quantified_variables}
    promoted: list[DomainPartition] = []
    for predicate in predicates:
        try:
            referenced = {
                node.id
                for node in ast.walk(ast.parse(predicate, mode="eval"))
                if isinstance(node, ast.Name)
            }
        except SyntaxError:
            # Conservative frontends may emit predicates that are meaningful
            # only in their source context. Keep both adjacent challenges and
            # let the constraint proof carry unsupported_constraint instead of
            # aborting the entire Requirement Graph.
            referenced = set()
        partitions = symbolic_scenario_partitions(leaf, guard_predicates=[predicate])
        for partition in partitions:
            authoritative_projection = bool(referenced & variable_names) and (
                leaf.authority.trusted or leaf.authority_class.value == "PRESERVATION"
            )
            promoted_proof = {
                **partition.proof,
                "coverage_complete": bool(
                    partition.proof.get("coverage_complete", False)
                    and all(not domain.open_world for domain in leaf.domains)
                ),
            }
            promoted.append(DomainPartition(
                partition_id=partition.partition_id,
                variable_names=partition.variable_names,
                constraints=partition.constraints,
                candidate_bindings=partition.candidate_bindings,
                source=f"reverse_domain_promotion:{predicate}",
                scope="REQUIREMENT" if authoritative_projection else "CHALLENGE_ONLY",
                satisfiable=partition.satisfiable,
                proof={**promoted_proof, "authority_projection": authoritative_projection},
                witness_ids=partition.witness_ids,
                leaf_id=leaf.leaf_id,
            ))
    return tuple(sorted({item.partition_id: item for item in promoted}.values(), key=lambda item: item.partition_id))
