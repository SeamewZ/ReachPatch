from __future__ import annotations

import ast
import copy
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from reachpatch.binding_graph.models import BindingGraph, BindingUnit
from reachpatch.challenge_graph.models import (
    ChallengeCell,
    ChallengeGraph,
    ChangedEdgeLedgerRecord,
    DiffChallengePlan,
    DiffClosureCertificate,
    DiffObligation,
    DICCCertificate,
    DICCStatus,
    ExecutableChallengeEvidence,
)
from reachpatch.binding_graph.models import BindingStatus
from reachpatch.execution.models import CheckClassification, CheckStatus
from reachpatch.challenge_graph.recipes import InputRecipe, recipe_from_scenario
from reachpatch.execution.reconcile import ActualDiff, ChangedRelation
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.core import Frontier
from reachpatch.models.enums import ChallengeTerminalStatus
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.requirement_graph.compiler import compile_requirement_paths
from reachpatch.requirement_graph.domains import ConstraintCompiler, default_domain_values
from reachpatch.requirement_graph.models import RequirementGraph, RequirementPathObligation


def evaluate_dicc(
    executable_targets,
    comparisons,
    actual_diff,
    impact_slice,
    challenge_results,
    *,
    path_obligation_count: int = 0,
    active_binding_count: int = 0,
) -> DICCCertificate:
    """Close diff risk only from real target and preservation executions."""

    targets = tuple(executable_targets)
    paired = tuple(comparisons)
    target_ids = {
        str(getattr(item, "check_id", "")) for item in targets
        if getattr(item, "check_id", None)
    }
    target_pairs = tuple(item for item in paired if item.check_id in target_ids)
    stable_failures = tuple(sorted(
        item.check_id for item in target_pairs
        if item.classification in {
            CheckClassification.TARGET_STILL_FAILING,
            CheckClassification.TARGET_REGRESSED,
        }
    ))
    regressions = tuple(sorted(
        item.check_id for item in paired
        if item.classification == CheckClassification.PRESERVATION_REGRESSION
    ))
    environment = tuple(sorted(
        item.check_id for item in paired
        if item.classification in {
            CheckClassification.SAME_INFRA_FAILURE,
            CheckClassification.NEW_INFRA_FAILURE,
            CheckClassification.UNSUPPORTED_CHECK,
            CheckClassification.FLAKY_RESULT,
        }
    ))
    uncovered = tuple(sorted(
        getattr(impact_slice, "uncovered_branch_partition_ids", ()) or ()
    ))
    real_challenge_count = int(
        getattr(challenge_results, "real_execution_count", 0) or 0
    )
    executed_challenge_ids = tuple(sorted(set(map(
        str, getattr(challenge_results, "executed_challenge_ids", ())
        or getattr(challenge_results, "challenge_ids", ()) or (),
    ))))
    if not targets:
        status = DICCStatus.NOT_EVALUABLE
        reason = "no executable target"
    elif path_obligation_count <= 0:
        status = DICCStatus.NOT_EVALUABLE
        reason = "no executable requirement path obligation"
    elif active_binding_count <= 0:
        status = DICCStatus.NOT_EVALUABLE
        reason = "no active executable binding"
    elif not target_pairs:
        status = DICCStatus.NOT_EVALUABLE
        reason = "no real target execution"
    elif environment:
        status = DICCStatus.BLOCKED_EXTERNAL
        reason = "required check environment is unavailable or unstable"
    elif stable_failures:
        status = DICCStatus.OPEN
        reason = "stable target failure remains"
    elif regressions:
        status = DICCStatus.OPEN
        reason = "preservation regression remains"
    elif uncovered:
        status = DICCStatus.OPEN
        reason = "touched branch partition is uncovered"
    elif any(
        item.classification != CheckClassification.TARGET_FIXED
        for item in target_pairs
    ):
        status = DICCStatus.OPEN
        reason = "not every target is fixed"
    elif real_challenge_count == 0:
        status = DICCStatus.NOT_EVALUABLE
        reason = "no real challenge execution"
    else:
        status = DICCStatus.CLOSED
        reason = "all executable targets and required impact checks passed"
    return DICCCertificate(
        certificate_id=stable_id(
            "dicc-certificate", status.value, sorted(target_ids),
            [item.comparison_id for item in paired],
            getattr(actual_diff, "canonical_diff_hash", ""), uncovered,
            path_obligation_count, active_binding_count,
            executed_challenge_ids,
        ),
        status=status,
        executable_target_count=len(targets),
        real_target_execution_count=len(target_pairs),
        real_challenge_execution_count=real_challenge_count,
        stable_target_failure_ids=stable_failures,
        preservation_regression_ids=regressions,
        environment_blocked_check_ids=environment,
        uncovered_touched_branch_partition_ids=uncovered,
        reason=reason,
        path_obligation_count=path_obligation_count,
        active_binding_count=active_binding_count,
        executed_challenge_ids=executed_challenge_ids,
    )


def compile_executable_challenge_evidence(
    executable_bindings,
    comparisons,
    actual_diff,
    impact_slice,
    trace_results=(),
    *,
    checks=(),
    repository_index=None,
) -> ExecutableChallengeEvidence:
    """Collect real graph and preservation executions for the current diff.

    Target checks establish reach progress and are deliberately excluded from
    the preservation side. A preservation check becomes a challenge only after
    a non-empty project diff exists, an executable preservation binding exists,
    and both sides of its paired execution are stable project results. Trace
    challenges count only when ``execute_challenges`` reports their IDs.
    """

    changed_files = tuple(getattr(actual_diff, "changed_files", ()) or ())
    diff_hash = str(getattr(actual_diff, "canonical_diff_hash", ""))
    impact_files = tuple(getattr(impact_slice, "changed_files", ()) or ())
    if not diff_hash or not changed_files or not impact_files:
        return ExecutableChallengeEvidence((), (), 0)

    if hasattr(trace_results, "executed_challenge_ids"):
        trace_results = (trace_results,)
    trace_challenge_ids = {
        str(challenge_id)
        for result in trace_results
        for challenge_id in (
            getattr(result, "executed_challenge_ids", ()) or ()
        )
    }

    preservation_check_ids = {
        str(unit.check_id)
        for unit in getattr(executable_bindings, "units", ())
        if unit.kind == BindingStatus.EXECUTABLE_PRESERVATION and unit.check_id
    }
    checks_by_id = {
        str(check.check_id): check for check in checks
        if getattr(check, "check_id", None)
    }
    changed_symbol_leaves = {
        part.lower()
        for symbol in (
            getattr(impact_slice, "changed_symbol_names", ()) or ()
        )
        for part in str(symbol).split(".")
        if len(part) >= 4 and not part.startswith("__")
    }

    def impact_related(check_id: str) -> bool:
        check = checks_by_id.get(check_id)
        if check is None or repository_index is None or not changed_symbol_leaves:
            return False
        paths = []
        for evidence in getattr(check, "source_evidence_ids", ()):
            if str(evidence).startswith("selector:"):
                paths.append(str(evidence).split(":", 1)[1])
        selector = str(getattr(check, "selector", ""))
        if selector.endswith(".py"):
            paths.append(selector)
        root = Path(str(getattr(check, "cwd", "") or ".")).resolve()
        for raw_path in paths:
            candidate = Path(raw_path)
            if candidate.is_absolute():
                try:
                    relative = str(candidate.resolve().relative_to(root))
                except ValueError:
                    parts = candidate.parts
                    if "tests" not in parts:
                        continue
                    relative = str(Path(*parts[parts.index("tests"):]))
            else:
                relative = str(candidate)
            relative = relative.replace("\\", "/")
            references = {
                str(item).lower() for item in (
                    repository_index.test_references.get(relative, ())
                )
            }
            if references & changed_symbol_leaves:
                return True
        return False
    valid_statuses = {CheckStatus.PASS, CheckStatus.FAIL}
    selected = []
    for comparison in comparisons:
        if comparison.check_id not in preservation_check_ids:
            continue
        if not impact_related(comparison.check_id):
            continue
        if comparison.classification not in {
            CheckClassification.PASS_PRESERVED,
            CheckClassification.PRESERVATION_REGRESSION,
        }:
            continue
        if (
            not comparison.baseline.stable
            or not comparison.patched.stable
            or comparison.baseline.status not in valid_statuses
            or comparison.patched.status not in valid_statuses
            or comparison.baseline.execution_id == comparison.patched.execution_id
            or comparison.baseline.tree_hash == comparison.patched.tree_hash
        ):
            continue
        selected.append(comparison)

    check_ids = tuple(sorted({item.check_id for item in selected}))
    public_challenge_ids = {
        stable_id("executable-impact-challenge", diff_hash, check_id)
        for check_id in check_ids
    }
    challenge_ids = tuple(sorted(trace_challenge_ids | public_challenge_ids))
    return ExecutableChallengeEvidence(
        challenge_ids=challenge_ids,
        executed_check_ids=check_ids,
        real_execution_count=len(challenge_ids),
    )


class DiffOperatorRegistry:
    """Finite transfer/exit registry over actual typed program deltas."""

    def __init__(self) -> None:
        self._handlers: list[tuple[re.Pattern[str], str, Callable[[ChangedRelation], list[dict[str, Any]]]]] = []

    def register(
        self,
        pattern: str,
        handler_id: str,
        handler: Callable[[ChangedRelation], list[dict[str, Any]]],
    ) -> None:
        if any(identifier == handler_id for _, identifier, _ in self._handlers):
            raise ValueError(f"diff handler already registered: {handler_id}")
        self._handlers.append((re.compile(pattern), handler_id, handler))

    def resolve(self, relation_kind: str):
        for pattern, identifier, handler in self._handlers:
            if pattern.fullmatch(relation_kind):
                return identifier, handler
        return None

    @classmethod
    def default(cls) -> "DiffOperatorRegistry":
        registry = cls()
        registry.register(r"guard_(?:added|modified|deleted)", "changed_guard", _guard_exits)
        registry.register(r"call_(?:added|modified|deleted)", "call_exits", _call_exits)
        registry.register(r"fallback_deleted|return_(?:added|modified|deleted)", "fallback_return", _fallback_exits)
        registry.register(r"state_(?:added|modified|deleted)", "state_lifecycle", _state_exits)
        registry.register(r"dispatch_(?:added|modified|deleted)", "protocol_dispatch", _dispatch_exits)
        registry.register(r"resource_(?:added|modified|deleted)", "resource_lifetime", _resource_exits)
        registry.register(r"external_effect_(?:added|modified)|external_surface_modified", "external_effect", _external_exits)
        registry.register(r"assignment_(?:added|modified|deleted)", "alias_flow", _alias_exits)
        return registry


def _guard_exits(relation: ChangedRelation) -> list[dict[str, Any]]:
    new = relation.attributes.get("new") or {}
    old = relation.attributes.get("old") or {}
    predicate = str(new.get("predicate") or old.get("predicate") or relation.new_source or relation.old_source or "True")
    return [
        {"kind": "guard", "partition": "true", "constraints": (predicate,), "expected_exit": "true"},
        {"kind": "guard", "partition": "false", "constraints": (f"not ({predicate})",), "expected_exit": "false"},
        {"kind": "guard", "partition": "boundary", "constraints": (), "expected_exit": "boundary"},
    ]


def _call_exits(relation: ChangedRelation) -> list[dict[str, Any]]:
    return [
        {"kind": "call_exit", "partition": "normal", "constraints": (), "expected_exit": "normal"},
        {"kind": "call_exit", "partition": "exception", "constraints": (), "expected_exit": "exception"},
    ]


def _fallback_exits(relation: ChangedRelation) -> list[dict[str, Any]]:
    return [{
        "kind": "fallback",
        "partition": "predecessor",
        "constraints": (),
        "expected_exit": "former_fallback",
    }]


def _state_exits(relation: ChangedRelation) -> list[dict[str, Any]]:
    return [
        {"kind": "state", "partition": name, "constraints": (), "expected_exit": name}
        for name in ("predecessor", "successor", "repeated", "recovery")
    ]


def _dispatch_exits(relation: ChangedRelation) -> list[dict[str, Any]]:
    return [
        {"kind": "dispatch", "partition": name, "constraints": (), "expected_exit": name}
        for name in (
            "left_right", "right_left", "not_implemented_fallback",
            "truthy", "falsy", "empty", "nonempty",
        )
    ]


def _resource_exits(relation: ChangedRelation) -> list[dict[str, Any]]:
    return [
        {"kind": "resource", "partition": "normal_release", "constraints": (), "expected_exit": "normal_release"},
        {"kind": "resource", "partition": "exception_release", "constraints": (), "expected_exit": "exception_release"},
    ]


def _external_exits(relation: ChangedRelation) -> list[dict[str, Any]]:
    return [
        {"kind": "external_effect", "partition": "before", "constraints": (), "expected_exit": "before"},
        {"kind": "external_effect", "partition": "after", "constraints": (), "expected_exit": "after"},
    ]


def _alias_exits(relation: ChangedRelation) -> list[dict[str, Any]]:
    return [
        {"kind": "alias_flow", "partition": "direct", "constraints": (), "expected_exit": "consumer"},
        {"kind": "alias_flow", "partition": "aliased", "constraints": (), "expected_exit": "consumer"},
    ]


def _semantic_clone(requirement_graph: RequirementGraph) -> RequirementGraph:
    clone = copy.deepcopy(requirement_graph)
    clone.path_obligations.clear()
    clone.edge_ledger.clear()
    clone.frontiers.clear()
    clone.partitions = {
        partition_id: partition
        for partition_id, partition in clone.partitions.items()
        if partition.source == "requirement_and_program_guards"
    }
    return clone


def _path_key(obligation: RequirementPathObligation, graph: ProgramGraph) -> tuple[Any, ...]:
    entry = graph.nodes.get(obligation.entrypoint_id) if obligation.entrypoint_id else None
    observation = graph.nodes.get(obligation.observation_id)
    path = graph.path_classes.get(obligation.path_class_id)
    return (
        obligation.leaf_id,
        tuple(sorted(obligation.partition.constraints)),
        entry.attributes.get("qualified_name") if entry else None,
        path.critical_predicates if path else obligation.accumulated_guard,
        path.protocol_selections if path else (),
        obligation.exit_kind,
        observation.kind if observation else None,
    )


def _changed_program_nodes(graph: ProgramGraph, actual_diff: ActualDiff) -> set[str]:
    changed: set[str] = set()
    for hunk in actual_diff.hunks:
        lower = hunk.new_start
        upper = hunk.new_start + max(hunk.new_count, 1) - 1
        for node_id in graph.file_index.get(hunk.file, []):
            line = int(graph.nodes[node_id].attributes.get("line", -1))
            end = int(graph.nodes[node_id].attributes.get("end_line", line))
            if line <= upper and end >= lower:
                changed.add(node_id)
    return changed


def _candidate_units(
    relation: ChangedRelation,
    binding_graph: BindingGraph,
    graph: ProgramGraph,
    changed_nodes: set[str],
) -> list[BindingUnit]:
    direct_nodes = {
        node_id
        for node_id in changed_nodes
        if graph.nodes[node_id].attributes.get("file") == relation.file
        and (
            relation.qualified_scope == "<module>"
            or str(graph.nodes[node_id].attributes.get("qualified_name", "")).endswith(relation.qualified_scope)
            or relation.qualified_scope in str(graph.nodes[node_id].attributes.get("qualified_name", ""))
        )
    }
    unit_ids = binding_graph.unit_ids_for_nodes(direct_nodes)
    if not unit_ids:
        unit_ids = binding_graph.unit_ids_for_nodes(changed_nodes)
    return [binding_graph.units[unit_id] for unit_id in sorted(unit_ids)]


class _RenameConstraint(ast.NodeTransformer):
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def visit_Name(self, node: ast.Name):
        if node.id in self.mapping:
            return ast.copy_location(ast.Name(id=self.mapping[node.id], ctx=node.ctx), node)
        return node


def _project_constraints(
    constraints: Iterable[str],
    unit: BindingUnit,
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
) -> tuple[str, ...]:
    leaf = requirement_graph.leaves[unit.leaf_id]
    if not constraints:
        return ()
    parameters = [
        node
        for node_id in unit.interaction_path_ids
        if (node := program_graph.nodes[node_id]).kind == "parameter"
    ]
    parameters.sort(key=lambda node: (
        int(node.attributes.get("line", 0)), int(node.attributes.get("column", 0))
    ))
    mapping = {
        parameter.label: variable.name
        for parameter, variable in zip(parameters, leaf.quantified_variables, strict=False)
    }
    projected: list[str] = []
    for expression in constraints:
        try:
            tree = ast.parse(expression, mode="eval")
            renamed = _RenameConstraint(mapping).visit(tree)
            ast.fix_missing_locations(renamed)
            projected.append(ast.unparse(renamed))
        except SyntaxError:
            projected.append(expression)
    return tuple(projected)


def _recipe_for_constraints(
    unit: BindingUnit,
    constraints: tuple[str, ...],
    requirement_graph: RequirementGraph,
    binding_graph: BindingGraph,
) -> InputRecipe | None:
    if not unit.scenario_ids:
        return None
    scenario = binding_graph.scenarios[unit.scenario_ids[0]]
    base = recipe_from_scenario(scenario)
    leaf = requirement_graph.leaves[unit.leaf_id]
    try:
        predicates = [ConstraintCompiler().compile(item) for item in constraints]
    except (SyntaxError, ValueError):
        return None
    candidate_sets = [default_domain_values(domain) for domain in leaf.domains]
    selected: dict[str, Any] | None = None
    import itertools

    for values in itertools.islice(itertools.product(*candidate_sets), 4096):
        bindings = {
            variable.name: value
            for variable, value in zip(leaf.quantified_variables, values, strict=True)
        }
        if all(predicate(bindings) for predicate in predicates):
            selected = bindings
            break
    if constraints and selected is None:
        return None
    selected = selected or {}
    stimulus = list(base.stimulus)
    if selected and stimulus and stimulus[0].get("op") == "call":
        stimulus[0] = {**stimulus[0], "args": list(selected.values()), "kwargs": {}}
    return InputRecipe.create(
        imports=base.imports,
        setup=base.setup,
        stimulus=stimulus,
        observations=base.observations,
        teardown=base.teardown,
        traces=base.traces,
        environment=base.environment,
        resource_limits=base.resource_limits,
        provenance_ids=base.provenance_ids,
    )


def _add_diff_challenge(
    challenge_graph: ChallengeGraph,
    obligation: DiffObligation,
    unit: BindingUnit,
    requirement_graph: RequirementGraph,
    binding_graph: BindingGraph,
) -> str | None:
    if not unit.scenario_ids or unit.oracle_id is None:
        return None
    scenario = binding_graph.scenarios[unit.scenario_ids[0]]
    recipe = _recipe_for_constraints(
        unit, obligation.input_constraints, requirement_graph, binding_graph
    )
    if recipe is None:
        return None
    challenge_id = stable_id(
        "diff-challenge", obligation.obligation_id, recipe.recipe_id,
        challenge_graph.diff_hash,
    )
    path = requirement_graph.path_obligations[unit.path_obligation_id]
    cell = ChallengeCell(
        challenge_id=challenge_id,
        binding_unit_id=unit.unit_id,
        quantified_partition={
            **path.partition.to_dict(),
            "diff_constraints": list(obligation.input_constraints),
            "partition_id": obligation.partition_id,
        },
        path_class_id=unit.path_class_id,
        trigger_recipe_id=recipe.recipe_id,
        input_constraints=obligation.input_constraints,
        observation_contract_id=scenario.observe.contract_id,
        oracle_id=unit.oracle_id,
        baseline_outcome=None,
        patched_outcome=None,
        diff_dependency={
            "obligation_id": obligation.obligation_id,
            "changed_relation_id": obligation.changed_relation_id,
            "relation_witness_ids": list(obligation.relation_witness_ids),
            "expected_exit": obligation.expected_exit,
        },
        stability_status="NOT_EXECUTED",
        terminal_status=ChallengeTerminalStatus.PENDING,
        evidence=scenario.evidence_ids,
        scenario_id=scenario.scenario_id,
        operator_id=f"diff:{obligation.kind}",
        changed_dimension=obligation.kind,
        origin="ACTUAL_DIFF",
        hard=obligation.safety_relevant,
        graph_hashes={
            "requirement": challenge_graph.requirement_graph_hash,
            "program": challenge_graph.program_graph_hash,
            "binding": challenge_graph.binding_graph_hash,
            "diff": challenge_graph.diff_hash,
        },
    )
    challenge_graph.add_cell(cell, recipe=recipe, scenario=scenario)
    return challenge_id


def diff_induced_challenge_plan(
    requirement_graph: RequirementGraph,
    base_program_graph: ProgramGraph,
    trial_program_graph: ProgramGraph,
    binding_graph: BindingGraph,
    challenge_graph: ChallengeGraph,
    actual_diff: ActualDiff,
    *,
    update_id: str,
    registry: DiffOperatorRegistry | None = None,
    trial_requirement_graph: RequirementGraph | None = None,
) -> DiffChallengePlan:
    if challenge_graph.diff_hash not in {"BASELINE", actual_diff.canonical_diff_hash}:
        raise ValueError("challenge graph is tied to a different actual diff")
    challenge_graph.diff_hash = actual_diff.canonical_diff_hash
    registry = registry or DiffOperatorRegistry.default()
    if trial_requirement_graph is None:
        trial_requirements = _semantic_clone(requirement_graph)
        compile_requirement_paths(trial_requirements, trial_program_graph)
    else:
        trial_requirements = trial_requirement_graph
    baseline_by_key = {
        _path_key(item, base_program_graph): item
        for item in requirement_graph.feasible_path_obligations()
    }
    trial_by_key = {
        _path_key(item, trial_program_graph): item
        for item in trial_requirements.feasible_path_obligations()
    }
    changed_nodes = _changed_program_nodes(trial_program_graph, actual_diff)
    obligations: list[DiffObligation] = []
    frontiers: list[Frontier] = []
    ledger: list[ChangedEdgeLedgerRecord] = []
    overlay_check_ids: list[str] = []

    removed_keys = baseline_by_key.keys() - trial_by_key.keys()
    for key in sorted(removed_keys, key=repr):
        baseline = baseline_by_key[key]
        unit_ids = binding_graph.by_path_obligation.get(baseline.path_obligation_id, set())
        for unit_id in sorted(unit_ids):
            unit = binding_graph.units[unit_id]
            obligation = DiffObligation(
                obligation_id=stable_id("diff-obligation", update_id, "path_removed", baseline.path_obligation_id),
                origin_update_id=update_id,
                kind="path_removed",
                baseline_path_obligation_id=baseline.path_obligation_id,
                binding_unit_id=unit_id,
                public_trigger_id=unit.trigger_id,
                partition_id=baseline.scenario_partition_id,
                route_id=baseline.path_class_id,
                observation_id=baseline.observation_id,
                oracle_id=unit.oracle_id,
                evidence_cluster_id=(
                    binding_graph.oracles[unit.oracle_id].evidence_cluster_id if unit.oracle_id else None
                ),
                relation_witness_ids=baseline.path_edge_ids,
                closure_status="EXECUTED_FAIL",
                proof_or_frontier_id="PATH_REMOVED",
                safety_relevant=True,
                changed_relation_id="baseline_trial_path_comparison",
                input_constraints=baseline.partition.constraints,
                expected_exit="PATH_REMOVED",
            )
            obligations.append(obligation)

    new_keys = trial_by_key.keys() - baseline_by_key.keys()
    for key in sorted(new_keys, key=repr):
        trial_path = trial_by_key[key]
        candidate_units = [
            unit
            for unit in binding_graph.units.values()
            if unit.leaf_id == trial_path.leaf_id
        ]
        for unit in sorted(candidate_units, key=lambda item: item.unit_id):
            obligation = DiffObligation(
                obligation_id=stable_id("diff-obligation", update_id, "new_path", trial_path.path_obligation_id, unit.unit_id),
                origin_update_id=update_id,
                kind="new_path",
                baseline_path_obligation_id=None,
                binding_unit_id=unit.unit_id,
                public_trigger_id=trial_path.public_trigger_id,
                partition_id=trial_path.scenario_partition_id,
                route_id=trial_path.path_class_id,
                observation_id=trial_path.observation_id,
                oracle_id=unit.oracle_id,
                evidence_cluster_id=(binding_graph.oracles[unit.oracle_id].evidence_cluster_id if unit.oracle_id else None),
                relation_witness_ids=trial_path.path_edge_ids,
                closure_status="FRONTIER",
                proof_or_frontier_id=None,
                safety_relevant=True,
                changed_relation_id="baseline_trial_path_comparison",
                input_constraints=trial_path.partition.constraints,
                expected_exit=trial_path.exit_kind,
            )
            obligations.append(obligation)

    for relation in actual_diff.changed_relations:
        resolved = registry.resolve(relation.kind)
        candidate_units = _candidate_units(
            relation, binding_graph, trial_program_graph, changed_nodes
        )
        if resolved is None:
            frontier = Frontier(
                frontier_id=stable_id("dicc-frontier", update_id, relation.relation_id, "unsupported"),
                kind="UNSUPPORTED_CHANGED_RELATION",
                owner_id=relation.relation_id,
                reason=f"no DICC handler for {relation.kind}",
                resolution_action="register a transfer/exit handler for this relation kind",
                hard=bool(candidate_units),
                evidence_ids=(relation.relation_id,),
                non_reachability_proof_id=(
                    None if candidate_units else stable_id("nonreach", relation.relation_id, binding_graph.graph_hash())
                ),
            )
            frontiers.append(frontier)
            ledger.append(ChangedEdgeLedgerRecord(
                ledger_id=stable_id("changed-edge-ledger", update_id, relation.relation_id),
                changed_relation_id=relation.relation_id,
                handler_id=None,
                obligation_ids=(),
                status="FRONTIER" if candidate_units else "RESIDUAL_NONREACHABLE",
                frontier_id=frontier.frontier_id,
            ))
            continue
        handler_id, handler = resolved
        candidate_specs = handler(relation)
        relation_obligations: list[str] = []
        if not candidate_units:
            ledger.append(ChangedEdgeLedgerRecord(
                ledger_id=stable_id("changed-edge-ledger", update_id, relation.relation_id),
                changed_relation_id=relation.relation_id,
                handler_id=handler_id,
                obligation_ids=(),
                status="RESIDUAL_NONREACHABLE",
                frontier_id=None,
            ))
            continue
        for unit in candidate_units:
            path = requirement_graph.path_obligations[unit.path_obligation_id]
            oracle = binding_graph.oracles.get(unit.oracle_id or "")
            for spec in candidate_specs:
                projected = _project_constraints(
                    spec["constraints"], unit, requirement_graph, trial_program_graph
                )
                obligation_id = stable_id(
                    "diff-obligation", update_id, relation.relation_id,
                    unit.unit_id, spec["kind"], spec["partition"], projected,
                )
                frontier_id = None
                status = "FRONTIER"
                if oracle is None or not oracle.active_and_trusted:
                    frontier = Frontier(
                        frontier_id=stable_id("dicc-frontier", obligation_id, "oracle"),
                        kind="ORACLE_FRONTIER",
                        owner_id=unit.unit_id,
                        reason="diff-induced candidate has no active A/B/C predicate",
                        resolution_action="retain as discriminator or obtain independent authority",
                        hard=True,
                        evidence_ids=(relation.relation_id,),
                    )
                    frontiers.append(frontier)
                    frontier_id = frontier.frontier_id
                obligation = DiffObligation(
                    obligation_id=obligation_id,
                    origin_update_id=update_id,
                    kind=spec["kind"],
                    baseline_path_obligation_id=path.path_obligation_id,
                    binding_unit_id=unit.unit_id,
                    public_trigger_id=unit.trigger_id,
                    partition_id=stable_id("diff-partition", path.scenario_partition_id, spec["partition"], projected),
                    route_id=unit.path_class_id,
                    observation_id=unit.observation_node_ids[0],
                    oracle_id=unit.oracle_id,
                    evidence_cluster_id=oracle.evidence_cluster_id if oracle else None,
                    relation_witness_ids=(relation.relation_id,) + unit.interaction_path_ids,
                    closure_status=status,
                    proof_or_frontier_id=frontier_id,
                    safety_relevant=True,
                    changed_relation_id=relation.relation_id,
                    input_constraints=projected,
                    expected_exit=str(spec["expected_exit"]),
                )
                obligations.append(obligation)
                relation_obligations.append(obligation_id)
                challenge_id = _add_diff_challenge(
                    challenge_graph, obligation, unit, requirement_graph, binding_graph
                )
                if challenge_id:
                    overlay_check_ids.append(challenge_id)
                elif frontier_id is None:
                    frontier = Frontier(
                        frontier_id=stable_id("dicc-frontier", obligation_id, "recipe"),
                        kind="DIFF_RECIPE_FRONTIER",
                        owner_id=unit.unit_id,
                        reason="no controlled InputRecipe satisfies the diff partition",
                        resolution_action="solve the path constraints or materialize state/setup",
                        hard=True,
                        evidence_ids=(relation.relation_id,),
                    )
                    frontiers.append(frontier)
                    obligations[-1] = replace(
                        obligation,
                        proof_or_frontier_id=frontier.frontier_id,
                    )
        ledger.append(ChangedEdgeLedgerRecord(
            ledger_id=stable_id("changed-edge-ledger", update_id, relation.relation_id),
            changed_relation_id=relation.relation_id,
            handler_id=handler_id,
            obligation_ids=tuple(sorted(relation_obligations)),
            status="ACCOUNTED" if relation_obligations else "RESIDUAL_NONREACHABLE",
            frontier_id=None,
        ))
    for frontier in frontiers:
        challenge_graph.add_frontier(frontier)
    hard = tuple(sorted(
        frontier.frontier_id for frontier in frontiers if frontier.hard
    ))
    residual = tuple(sorted(
        frontier.frontier_id for frontier in frontiers if not frontier.hard
    ))
    plan_body = {
        "update_id": update_id,
        "baseline": sorted(requirement_graph.path_obligations),
        "obligations": [item.to_dict() for item in obligations],
        "ledger": [item.to_dict() for item in ledger],
        "hard": hard,
        "residual": residual,
    }
    return DiffChallengePlan(
        update_id=update_id,
        baseline_path_obligation_ids=tuple(sorted(requirement_graph.path_obligations)),
        overlay_obligation_ids=tuple(sorted(item.obligation_id for item in obligations)),
        overlay_check_ids=tuple(sorted(overlay_check_ids)),
        structural_discharges={},
        changed_edge_ledger_ids=tuple(sorted(item.ledger_id for item in ledger)),
        hard_frontier_ids=hard,
        residual_risk_frontier_ids=residual,
        obligations=tuple(obligations),
        changed_edge_ledger=tuple(ledger),
        challenge_graph_hash=content_hash(plan_body),
    )


def finalize_diff_induced_challenge_closure(
    plan: DiffChallengePlan,
    challenge_graph: ChallengeGraph,
    *,
    checkpoint_id: str,
    transition_index: int,
    causal_touch_witnesses: dict[str, list[str]],
    stale_record_ids: Iterable[str] = (),
    oracle_change_ids: Iterable[str] = (),
) -> DiffClosureCertificate:
    stale = tuple(sorted(stale_record_ids))
    challenge_by_obligation = {
        str(cell.diff_dependency.get("obligation_id")): cell
        for cell in challenge_graph.cells.values()
        if cell.origin == "ACTUAL_DIFF" and cell.diff_dependency.get("obligation_id")
    }
    updated_obligations = []
    result_ids: list[str] = []
    for obligation in plan.obligations:
        cell = challenge_by_obligation.get(obligation.obligation_id)
        if obligation.kind == "path_removed":
            status = "EXECUTED_FAIL"
            result_id = "PATH_REMOVED"
        elif obligation.obligation_id in plan.structural_discharges:
            status = "PROVED_INFEASIBLE"
            result_id = plan.structural_discharges[obligation.obligation_id]
        elif cell is None:
            status = "FRONTIER"
            result_id = obligation.proof_or_frontier_id or "MISSING_CHALLENGE"
        elif cell.terminal_status == ChallengeTerminalStatus.PASS:
            status = "EXECUTED_PASS"
            result_id = cell.execution_bundle_id or cell.challenge_id
        elif cell.terminal_status == ChallengeTerminalStatus.FAIL:
            status = "EXECUTED_FAIL"
            result_id = cell.execution_bundle_id or cell.challenge_id
        else:
            status = "FRONTIER"
            result_id = cell.execution_bundle_id or cell.challenge_id
        updated_obligations.append(replace(
            obligation,
            closure_status=status,
            proof_or_frontier_id=result_id,
        ))
        result_ids.append(result_id)
    hard_frontiers = set(plan.hard_frontier_ids)
    hard_frontiers.update(
        obligation.proof_or_frontier_id or obligation.obligation_id
        for obligation in updated_obligations
        if obligation.safety_relevant and obligation.closure_status == "FRONTIER"
    )
    ledger_closed = all(item.status in {"ACCOUNTED", "RESIDUAL_NONREACHABLE"} for item in plan.changed_edge_ledger)
    safety_closed = (
        not hard_frontiers
        and not stale
        and ledger_closed
        and all(
            obligation.closure_status in {"EXECUTED_PASS", "PROVED_INFEASIBLE"}
            for obligation in updated_obligations
            if obligation.safety_relevant and obligation.kind != "path_removed"
        )
        and not any(obligation.kind == "path_removed" for obligation in updated_obligations)
    )
    baseline_cells = [
        cell
        for cell in challenge_graph.cells.values()
        if cell.origin == "BASELINE_GRAPH" and cell.hard
    ]
    full_closed = (
        safety_closed
        and all(cell.terminal_status == ChallengeTerminalStatus.PASS for cell in baseline_cells)
        and all(
            obligation.closure_status in {"EXECUTED_PASS", "PROVED_INFEASIBLE"}
            for obligation in updated_obligations
        )
    )
    categories = {
        "guard": [], "call_exit": [], "fallback": [], "state_dispatch": [],
        "bypass": [], "preservation": [],
    }
    for obligation in updated_obligations:
        target = (
            "state_dispatch" if obligation.kind in {"state", "dispatch", "alias_flow"}
            else "preservation" if obligation.kind == "preservation_caller"
            else "bypass" if obligation.kind in {"bypass", "new_path"}
            else obligation.kind
        )
        if target in categories:
            categories[target].append(obligation.obligation_id)
    source_hashes = {
        "requirement": challenge_graph.requirement_graph_hash,
        "program": challenge_graph.program_graph_hash,
        "binding": challenge_graph.binding_graph_hash,
        "challenge": challenge_graph.graph_hash(),
        "diff": challenge_graph.diff_hash,
    }
    recomputation = content_hash({
        "plan": plan.to_dict(),
        "obligations": [item.to_dict() for item in updated_obligations],
        "results": result_ids,
        "causal_touch": causal_touch_witnesses,
        "stale": stale,
        "oracle_changes": sorted(oracle_change_ids),
        "source_hashes": source_hashes,
        "safety_closed": safety_closed,
        "full_closed": full_closed,
    })
    return DiffClosureCertificate(
        closure_id=stable_id("diff-closure", plan.update_id, checkpoint_id, recomputation),
        update_id=plan.update_id,
        checkpoint_id=checkpoint_id,
        transition_index=transition_index,
        baseline_path_obligation_ids=plan.baseline_path_obligation_ids,
        overlay_obligation_ids=plan.overlay_obligation_ids,
        obligation_result_ids=tuple(result_ids),
        causal_touch_witnesses=causal_touch_witnesses,
        invalidated_node_ids=(),
        changed_guard_obligation_ids=tuple(categories["guard"]),
        call_exit_obligation_ids=tuple(categories["call_exit"]),
        fallback_obligation_ids=tuple(categories["fallback"]),
        state_dispatch_obligation_ids=tuple(categories["state_dispatch"]),
        bypass_obligation_ids=tuple(categories["bypass"]),
        preservation_caller_obligation_ids=tuple(categories["preservation"]),
        hard_frontier_ids=tuple(sorted(hard_frontiers)),
        residual_risk_frontier_ids=plan.residual_risk_frontier_ids,
        oracle_change_ids=tuple(sorted(oracle_change_ids)),
        stale_record_ids=stale,
        changed_edge_ledger_ids=plan.changed_edge_ledger_ids,
        commit_safety_closed=safety_closed,
        diff_challenge_closed=full_closed,
        source_graph_oracle_hashes=source_hashes,
        recomputation_hash=recomputation,
        plan_payload=plan.to_dict(),
        updated_obligations=tuple(item.to_dict() for item in updated_obligations),
    )


def verify_diff_closure_certificate(
    certificate: DiffClosureCertificate,
    plan: DiffChallengePlan,
    challenge_graph: ChallengeGraph,
) -> bool:
    rebuilt = finalize_diff_induced_challenge_closure(
        plan,
        challenge_graph,
        checkpoint_id=certificate.checkpoint_id,
        transition_index=certificate.transition_index,
        causal_touch_witnesses=certificate.causal_touch_witnesses,
        stale_record_ids=certificate.stale_record_ids,
        oracle_change_ids=certificate.oracle_change_ids,
    )
    return rebuilt.recomputation_hash == certificate.recomputation_hash


def verify_stored_diff_closure_certificate(certificate: DiffClosureCertificate) -> bool:
    """Verify a persisted closure without trusting mutable in-memory graphs."""

    if not certificate.plan_payload:
        return False
    expected = content_hash({
        "plan": certificate.plan_payload,
        "obligations": list(certificate.updated_obligations),
        "results": list(certificate.obligation_result_ids),
        "causal_touch": certificate.causal_touch_witnesses,
        "stale": list(certificate.stale_record_ids),
        "oracle_changes": sorted(certificate.oracle_change_ids),
        "source_hashes": certificate.source_graph_oracle_hashes,
        "safety_closed": certificate.commit_safety_closed,
        "full_closed": certificate.diff_challenge_closed,
    })
    return expected == certificate.recomputation_hash
