from __future__ import annotations

from typing import Any

from reachpatch.binding_graph.models import (
    BindingGraph, BindingStatus, BindingUnit, ExecutableBindingGraph,
    ExecutableBindingUnit, OracleFrontier, ProjectionWitness, RepairComponent,
)
from reachpatch.binding_graph import active_binding_graph_from_dict
from reachpatch.challenge_graph.models import (
    ChallengeCell, ChallengeGraph, ChallengePriority, DICCCertificate, DICCStatus,
)
from reachpatch.execution.models import (
    CheckClassification, CheckComparison, CheckExecution, CheckRole, CheckStatus,
    EnvironmentFrontier, EnvironmentHealth, EnvironmentHealthStatus,
    ExecutableCheck, RejectedCheck,
)
from reachpatch.execution.target_recovery import (
    TargetCandidate,
    TargetCertification,
    TargetRecoveryResult,
)
from reachpatch.challenge_graph.recipes import (
    InputRecipe, ResourceLimits, TraceSpec,
)
from reachpatch.evidence.hypotheses import HypothesisAssignment, HypothesisSet
from reachpatch.models.controller import ExecutableOracle, UnitOutcome
from reachpatch.models.core import Frontier
from reachpatch.models.enums import (
    Authority, ChallengeTerminalStatus, LedgerStatus, OracleLifecycle,
    OutcomeStatus, RequirementAuthorityClass,
)
from reachpatch.models.graph import GraphEdge, GraphNode
from reachpatch.oracle.models import ExecutableScenario, ObservationContract, Oracle
from reachpatch.program_graph.index import ModuleSummary, RepositoryIndex, SymbolLocation
from reachpatch.program_graph.models import (
    CFGRecord, CausalSlice, ImpactSlice, PathClass, ProgramGraph,
    ProtocolOperation, TargetSlice,
)
from reachpatch.program_graph.slice import ContextRequest
from reachpatch.repair.deepseek_agent import GeneratorConversation
from reachpatch.requirement_graph.models import (
    DomainPartition, DomainSpec, PathEdgeLedgerRecord, QuantifiedVariable,
    ExecutableRequirement, ExecutableRequirementOverlay, NormativeRequirement,
    RequirementGraph, RequirementHyperEdge, RequirementLeaf, RequirementPathObligation,
)


def _tuple_fields(raw: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    value = dict(raw)
    for name in names:
        value[name] = tuple(value.get(name, ()))
    return value


def hypothesis_assignment_from_dict(raw: dict[str, Any]) -> HypothesisAssignment:
    return HypothesisAssignment(**_tuple_fields(raw, (
        "common_hard_node_ids", "assignment_node_ids", "preservation_node_ids",
        "contradiction_ids",
    )))


def hypothesis_set_from_dict(raw: dict[str, Any]) -> HypothesisSet:
    return HypothesisSet(
        common_hard_node_ids=tuple(raw.get("common_hard_node_ids", ())),
        alternatives=tuple(
            hypothesis_assignment_from_dict(item)
            for item in raw.get("alternatives", ())
        ),
        unresolved_decision_ids=tuple(raw.get("unresolved_decision_ids", ())),
        active_assignment_ids=tuple(raw.get("active_assignment_ids", ())),
        preferred_assignment_id=raw.get("preferred_assignment_id"),
    )


def repository_index_from_dict(raw: dict[str, Any]) -> RepositoryIndex:
    return RepositoryIndex(
        repository_root=str(raw["repository_root"]),
        source_hashes=dict(raw.get("source_hashes", {})),
        modules={
            key: ModuleSummary(**_tuple_fields(value, (
                "classes", "callables", "imports", "bases", "decorators",
                "public_symbols",
            )))
            for key, value in raw.get("modules", {}).items()
        },
        symbols={
            key: tuple(SymbolLocation(**item) for item in values)
            for key, values in raw.get("symbols", {}).items()
        },
        imports={key: tuple(value) for key, value in raw.get("imports", {}).items()},
        inheritance={
            key: tuple(value) for key, value in raw.get("inheritance", {}).items()
        },
        test_references={
            key: tuple(value) for key, value in raw.get("test_references", {}).items()
        },
        token_index={
            key: tuple(value) for key, value in raw.get("token_index", {}).items()
        },
        parse_frontiers=tuple(Frontier(**_tuple_fields(item, ("evidence_ids",))) for item in raw.get("parse_frontiers", ())),
        build_seconds=float(raw.get("build_seconds", 0.0)),
        scanned_files=int(raw.get("scanned_files", 0)),
    )


def program_graph_from_dict(raw: dict[str, Any]) -> ProgramGraph:
    graph = ProgramGraph(
        repository_root=str(raw["repository_root"]),
        source_hash=str(raw["source_hash"]),
        version=int(raw.get("version", 1)),
    )
    for item in raw.get("nodes", ()):
        graph.index_node(GraphNode(**_tuple_fields(item, ("provenance_ids",))))
    for item in raw.get("edges", ()):
        graph.add_edge(GraphEdge(**_tuple_fields(
            item, ("source_ids", "target_ids", "provenance_ids")
        )))
    for item in raw.get("cfgs", ()):
        record = CFGRecord(**_tuple_fields(
            item, ("exit_node_ids", "statement_node_ids", "edge_ids")
        ))
        graph.add_cfg(record)
    for item in raw.get("protocol_operations", ()):
        record = ProtocolOperation(**_tuple_fields(item, (
            "candidate_method_names", "candidate_target_ids", "fallback_order",
            "conditions",
        )))
        graph.add_protocol_operation(record)
    for item in raw.get("path_classes", ()):
        record = PathClass(**_tuple_fields(item, (
            "node_ids", "edge_ids", "critical_predicates", "protocol_selections",
            "observation_ids", "state_effect_ids", "loop_summaries",
        )))
        graph.add_path_class(record)
    for item in raw.get("frontiers", ()):
        graph.add_frontier(Frontier(**_tuple_fields(item, ("evidence_ids",))))
    # Some observation/external/test membership is promoted after node
    # construction and cannot be inferred from node kind alone. Restore the
    # persisted semantic indexes exactly so graph hashes survive a resume.
    graph.external_surface_ids = set(raw.get("external_surface_ids", ()))
    graph.test_node_ids = set(raw.get("test_node_ids", ()))
    graph.observation_node_ids = set(raw.get("observation_node_ids", ()))
    graph.invalidate_hash()
    expected = raw.get("graph_hash")
    if expected and graph.program_hash() != expected:
        raise ValueError("persisted active Program Graph hash mismatch")
    return graph


def _domain(raw: dict[str, Any]) -> DomainSpec:
    return DomainSpec(**_tuple_fields(raw, (
        "type_names", "literal_values", "container_shapes",
    )))


def _partition(raw: dict[str, Any]) -> DomainPartition:
    return DomainPartition(**_tuple_fields(raw, (
        "variable_names", "constraints", "candidate_bindings", "witness_ids",
    )))


def requirement_graph_from_dict(raw: dict[str, Any]) -> RequirementGraph:
    graph = RequirementGraph(
        assignment_id=str(raw["assignment_id"]), version=int(raw.get("version", 1))
    )
    for item in raw.get("nodes", ()):
        graph.add_node(GraphNode(**_tuple_fields(item, ("provenance_ids",))))
    for item in raw.get("edges", ()):
        graph.add_edge(GraphEdge(**_tuple_fields(
            item, ("source_ids", "target_ids", "provenance_ids")
        )))
    for item in raw.get("leaves", ()):
        values = _tuple_fields(item, (
            "entrypoint_hypotheses", "witnesses", "supporting_evidence",
        ))
        values["quantified_variables"] = tuple(
            QuantifiedVariable(**_tuple_fields(value, ("type_hints",)))
            for value in item.get("quantified_variables", ())
        )
        values["domains"] = tuple(_domain(value) for value in item.get("domains", ()))
        values["authority"] = Authority(values["authority"])
        values["authority_class"] = RequirementAuthorityClass(values["authority_class"])
        leaf = RequirementLeaf(**values)
        graph.leaves[leaf.leaf_id] = leaf
    graph.domains = {
        item["domain_id"]: _domain(item) for item in raw.get("domains", ())
    }
    graph.partitions = {
        item["partition_id"]: _partition(item) for item in raw.get("partitions", ())
    }
    graph.hyperedges = {
        item["requirement_edge_id"]: RequirementHyperEdge(**_tuple_fields(
            item, ("source_ids", "target_ids", "evidence_ids")
        ))
        for item in raw.get("hyperedges", ())
    }
    for item in raw.get("path_obligations", ()):
        values = _tuple_fields(item, (
            "path_edge_ids", "preservation_caller_ids", "dependence_slice_ids",
            "frontier_ids",
        ))
        values["partition"] = _partition(item["partition"])
        record = RequirementPathObligation(**values)
        graph.path_obligations[record.path_obligation_id] = record
    for item in raw.get("edge_ledger", ()):
        values = dict(item)
        values["status"] = LedgerStatus(values["status"])
        record = PathEdgeLedgerRecord(**values)
        graph.edge_ledger[record.ledger_id] = record
    graph.frontiers = {
        item["frontier_id"]: Frontier(**_tuple_fields(item, ("evidence_ids",)))
        for item in raw.get("frontiers", ())
    }
    graph.authority_snapshot_hash = str(raw.get("authority_snapshot_hash", ""))
    expected = raw.get("graph_hash")
    if expected and graph.to_dict()["graph_hash"] != expected:
        raise ValueError("persisted active Requirement Graph hash mismatch")
    return graph


def _observation(raw: dict[str, Any]) -> ObservationContract:
    return ObservationContract(**_tuple_fields(raw, (
        "channels", "object_fields", "visible_state_keys",
    )))


def _oracle(raw: dict[str, Any]) -> Oracle:
    values = _tuple_fields(raw, ("observation_channels", "evidence_ids"))
    values["authority"] = Authority(values["authority"])
    values["lifecycle"] = OracleLifecycle(values["lifecycle"])
    return Oracle(**values)


def _scenario(raw: dict[str, Any]) -> ExecutableScenario:
    values = _tuple_fields(raw, ("setup", "stimulus", "evidence_ids"))
    values["observe"] = _observation(raw["observe"])
    values["oracle"] = _oracle(raw["oracle"])
    return ExecutableScenario(**values)


def binding_graph_from_dict(raw: dict[str, Any]) -> BindingGraph:
    graph = BindingGraph(
        requirement_graph_hash=str(raw["requirement_graph_hash"]),
        program_graph_hash=str(raw["program_graph_hash"]),
        assignment_id=str(raw["assignment_id"]), version=int(raw.get("version", 1)),
    )
    for item in raw.get("units", ()):
        values = _tuple_fields(item, (
            "interaction_path_ids", "repair_cut_node_ids", "observation_node_ids",
            "bypass_path_ids", "preservation_node_ids", "scenario_ids",
            "frontier_ids", "impact_cone_node_ids",
        ))
        values["projection_witness"] = ProjectionWitness(**item["projection_witness"])
        graph.add_unit(BindingUnit(**values))
    graph.components = {
        item["component_id"]: RepairComponent(**_tuple_fields(item, (
            "unit_ids", "common_dominator_ids", "state_owner_ids",
            "dispatch_boundary_ids", "legal_repair_cut_ids",
            "preservation_node_ids", "interaction_witnesses",
        )))
        for item in raw.get("components", ())
    }
    graph.oracles = {
        item["oracle_id"]: _oracle(item) for item in raw.get("oracles", ())
    }
    graph.scenarios = {
        item["scenario_id"]: _scenario(item) for item in raw.get("scenarios", ())
    }
    graph.frontiers = {
        item["frontier_id"]: Frontier(**_tuple_fields(item, ("evidence_ids",)))
        for item in raw.get("frontiers", ())
    }
    graph.oracle_frontiers = {
        item["frontier_id"]: OracleFrontier(**_tuple_fields(
            item, ("leaf_ids", "unit_ids", "observation_channels")
        ))
        for item in raw.get("oracle_frontiers", ())
    }
    expected = raw.get("graph_hash")
    if expected and graph.graph_hash() != expected:
        raise ValueError("persisted active Binding Graph hash mismatch")
    return graph


def _recipe(raw: dict[str, Any]) -> InputRecipe:
    values = _tuple_fields(raw, (
        "imports", "setup", "stimulus", "observations", "teardown",
        "provenance_ids",
    ))
    values["traces"] = tuple(
        TraceSpec(**_tuple_fields(item, ("steps",))) for item in raw.get("traces", ())
    )
    values["resource_limits"] = ResourceLimits(**raw["resource_limits"])
    return InputRecipe(**values)


def challenge_graph_from_dict(raw: dict[str, Any]) -> ChallengeGraph:
    graph = ChallengeGraph(
        requirement_graph_hash=str(raw["requirement_graph_hash"]),
        program_graph_hash=str(raw["program_graph_hash"]),
        binding_graph_hash=str(raw["binding_graph_hash"]),
        diff_hash=str(raw.get("diff_hash", "BASELINE")),
        version=int(raw.get("version", 1)),
    )
    recipes = {item["recipe_id"]: _recipe(item) for item in raw.get("recipes", ())}
    scenarios = {
        item["scenario_id"]: _scenario(item) for item in raw.get("scenarios", ())
    }
    for item in raw.get("cells", ()):
        values = _tuple_fields(item, ("input_constraints", "evidence"))
        values["terminal_status"] = ChallengeTerminalStatus(values["terminal_status"])
        cell = ChallengeCell(**values)
        graph.add_cell(
            cell,
            recipe=recipes.get(cell.trigger_recipe_id or ""),
            scenario=scenarios.get(cell.scenario_id or ""),
        )
    graph.frontiers = {
        item["frontier_id"]: Frontier(**_tuple_fields(item, ("evidence_ids",)))
        for item in raw.get("frontiers", ())
    }
    graph.priorities = {
        key: ChallengePriority(**value)
        for key, value in raw.get("priorities", {}).items()
    }
    graph.build_timings = {
        str(key): float(value) for key, value in raw.get("build_timings", {}).items()
    }
    graph.build_stats = {
        str(key): int(value) for key, value in raw.get("build_stats", {}).items()
    }
    expected = raw.get("graph_hash")
    if expected and graph.graph_hash() != expected:
        raise ValueError("persisted active Challenge Graph hash mismatch")
    return graph


def conversation_from_dict(raw: dict[str, Any]) -> GeneratorConversation:
    return GeneratorConversation(
        conversation_id=str(raw["conversation_id"]),
        messages=list(raw.get("messages", ())),
        inspected_files=set(raw.get("inspected_files", ())),
        inspected_symbols=set(raw.get("inspected_symbols", ())),
        attempted_mechanisms=list(raw.get("attempted_mechanisms", ())),
        accepted_patch_hashes=list(raw.get("accepted_patch_hashes", ())),
        rejected_patch_hashes=list(raw.get("rejected_patch_hashes", ())),
        delivered_counterexamples=set(raw.get("delivered_counterexamples", ())),
        pending_context_requests=[
            ContextRequest(**_tuple_fields(item, (
                "symbols", "file_paths", "relation_kinds",
            )))
            for item in raw.get("pending_context_requests", ())
        ],
        current_working_diff=str(raw.get("current_working_diff", "")),
        inspected_line_ranges=set(raw.get("inspected_line_ranges", ())),
        mechanism_failure_signatures={
            str(key): list(map(str, values))
            for key, values in raw.get("mechanism_failure_signatures", {}).items()
        },
        rolled_back_diffs=list(map(str, raw.get("rolled_back_diffs", ()))),
        eliminated_counterexamples=set(raw.get("eliminated_counterexamples", ())),
        unresolved_counterexamples=set(raw.get("unresolved_counterexamples", ())),
        passed_preservation_checks=set(raw.get("passed_preservation_checks", ())),
        last_evidence_fingerprint=raw.get("last_evidence_fingerprint"),
        revision_count=int(raw.get("revision_count", 0)),
    )


def executable_check_from_dict(raw: dict[str, Any]) -> ExecutableCheck:
    values = _tuple_fields(raw, (
        "command", "source_evidence_ids", "target_requirement_ids",
        "temporary_artifact_paths", "executed_symbol_ids",
    ))
    values["role"] = CheckRole(values["role"])
    values["environment"] = dict(values.get("environment", {}))
    return ExecutableCheck(**values)


def check_execution_from_dict(raw: dict[str, Any]) -> CheckExecution:
    values = dict(raw)
    values["status"] = CheckStatus(values["status"])
    values["executed_symbol_ids"] = tuple(values.get("executed_symbol_ids", ()))
    frame = values.get("first_project_frame")
    values["first_project_frame"] = dict(frame) if frame is not None else None
    return CheckExecution(**values)


def check_comparison_from_dict(raw: dict[str, Any]) -> CheckComparison:
    values = dict(raw)
    values["baseline"] = check_execution_from_dict(values["baseline"])
    values["patched"] = check_execution_from_dict(values["patched"])
    values["classification"] = CheckClassification(values["classification"])
    return CheckComparison(**values)


def environment_health_from_dict(raw: dict[str, Any]) -> EnvironmentHealth:
    values = dict(raw)
    values["status"] = EnvironmentHealthStatus(values["status"])
    execution = values.get("execution")
    values["execution"] = (
        check_execution_from_dict(execution) if execution is not None else None
    )
    return EnvironmentHealth(**values)


def environment_frontier_from_dict(raw: dict[str, Any]) -> EnvironmentFrontier:
    values = dict(raw)
    values["health_status"] = EnvironmentHealthStatus(values["health_status"])
    return EnvironmentFrontier(**values)


def target_recovery_from_dict(raw: dict[str, Any]) -> TargetRecoveryResult:
    def _candidate(item: dict[str, Any]) -> TargetCandidate:
        values = dict(item)
        old_authority = str(values.pop("authority", ""))
        old_relation = values.pop("expected_relation", None)
        old_evidence = values.pop("source_evidence", ())
        values.setdefault("input_source", "LEGACY_ARTIFACT")
        values.setdefault("oracle_authority", old_authority or "E")
        values["setup_commands"] = tuple(
            tuple(command) if not isinstance(command, str) else (command,)
            for command in values.get("setup_commands", ())
        )
        command = values.get("command", ())
        values["command"] = (
            tuple(command) if not isinstance(command, str)
            else tuple(command.split())
        )
        values["source_evidence_ids"] = tuple(
            values.get("source_evidence_ids", old_evidence)
        )
        values["target_requirement_ids"] = tuple(
            values.get("target_requirement_ids", ())
        )
        values["executed_symbol_ids"] = tuple(
            values.get("executed_symbol_ids", ())
        )
        oracle = values.get("oracle")
        if isinstance(oracle, dict):
            values["oracle"] = ExecutableOracle(**oracle)
        elif oracle is None and isinstance(old_relation, dict):
            relation = str(
                old_relation.get("relation", old_relation.get("kind", ""))
            )
            values["oracle"] = (
                ExecutableOracle(
                    oracle_id=str(
                        old_relation.get("oracle_id", values["target_id"])
                    ),
                    authority=values["oracle_authority"],
                    relation=relation,
                    requirement_id=old_relation.get("requirement_id"),
                ) if relation else None
            )
        contract = values.get("observation_contract")
        if isinstance(contract, dict):
            values["observation_contract"] = _observation(contract)
        return TargetCandidate(**values)

    return TargetRecoveryResult(
        targets=tuple(executable_check_from_dict(item) for item in raw.get("targets", ())),
        preservation_checks=tuple(
            executable_check_from_dict(item)
            for item in raw.get("preservation_checks", ())
        ),
        rejected_checks=tuple(
            RejectedCheck(**item) for item in raw.get("rejected_checks", ())
        ),
        environment_frontiers=tuple(
            environment_frontier_from_dict(item)
            for item in raw.get("environment_frontiers", ())
        ),
        baseline_executions=tuple(
            check_execution_from_dict(item)
            for item in raw.get("baseline_executions", ())
        ),
        health_checks=tuple(
            environment_health_from_dict(item)
            for item in raw.get("health_checks", ())
        ),
        directed_reproduction_requests=int(raw.get("directed_reproduction_requests", 0)),
        status=str(raw.get("status", "TARGET_UNAVAILABLE")),
        candidates=tuple(_candidate(item) for item in raw.get("candidates", ())),
        exploration_candidates=tuple(
            _candidate(item) for item in raw.get("exploration_candidates", ())
        ),
        elapsed_seconds=float(raw.get("elapsed_seconds", 0.0)),
        timed_out=bool(raw.get("timed_out", False)),
        certifications=tuple(
            TargetCertification(**item)
            for item in raw.get("certifications", ())
        ),
    )


def executable_overlay_from_dict(raw: dict[str, Any]) -> ExecutableRequirementOverlay:
    normative = []
    for item in raw.get("normative_requirements", ()):
        values = _tuple_fields(item, ("witnesses",))
        values["quantified_variables"] = tuple(
            QuantifiedVariable(**_tuple_fields(value, ("type_hints",)))
            for value in item.get("quantified_variables", ())
        )
        values["expected_relation"] = dict(values.get("expected_relation", {}))
        values["observation_contract"] = dict(values.get("observation_contract", {}))
        normative.append(NormativeRequirement(**values))
    return ExecutableRequirementOverlay(
        normative_requirements=tuple(normative),
        executable_requirements=tuple(
            ExecutableRequirement(**item)
            for item in raw.get("executable_requirements", ())
        ),
        unresolved_normative_requirement_ids=tuple(
            raw.get("unresolved_normative_requirement_ids", ())
        ),
        hypothesis_assignment_ids=tuple(raw.get("hypothesis_assignment_ids", ())),
        overlay_hash=str(raw["overlay_hash"]),
    )


def target_slice_from_dict(raw: dict[str, Any]) -> TargetSlice:
    values = _tuple_fields(raw, (
        "check_ids", "file_paths", "symbol_names", "node_ids", "source_locations",
    ))
    values["source_locations"] = tuple(map(dict, values["source_locations"]))
    return TargetSlice(**values)


def causal_slice_from_dict(raw: dict[str, Any]) -> CausalSlice:
    values = _tuple_fields(raw, (
        "node_ids", "edge_ids", "branch_predicate_ids", "dispatch_edge_ids",
        "exception_edge_ids", "candidate_cut_node_ids",
    ))
    location = values.get("failure_location")
    values["failure_location"] = dict(location) if location is not None else None
    return CausalSlice(**values)


def impact_slice_from_dict(raw: dict[str, Any]) -> ImpactSlice:
    return ImpactSlice(**_tuple_fields(raw, (
        "changed_files", "changed_symbol_names", "node_ids", "direct_caller_ids",
        "sibling_path_ids", "state_consumer_ids", "dispatch_alternative_ids",
        "exception_consumer_ids", "uncovered_branch_partition_ids",
    )))


def executable_binding_graph_from_dict(raw: dict[str, Any]) -> ExecutableBindingGraph:
    units = []
    for item in raw.get("units", ()):
        values = _tuple_fields(item, (
            "repair_cut_node_ids", "candidate_repair_cut_ids", "impact_node_ids",
        ))
        values["kind"] = BindingStatus(values["kind"])
        location = values.get("failure_location")
        values["failure_location"] = dict(location) if location is not None else None
        units.append(ExecutableBindingUnit(**values))
    return ExecutableBindingGraph(
        units=tuple(units),
        executable_requirement_overlay_hash=str(raw["executable_requirement_overlay_hash"]),
        target_slice_id=str(raw["target_slice_id"]),
        impact_slice_id=raw.get("impact_slice_id"),
        graph_hash=str(raw["graph_hash"]),
    )


def dicc_certificate_from_dict(raw: dict[str, Any]) -> DICCCertificate:
    values = _tuple_fields(raw, (
        "stable_target_failure_ids", "preservation_regression_ids",
        "environment_blocked_check_ids", "uncovered_touched_branch_partition_ids",
        "executed_challenge_ids",
    ))
    values["status"] = DICCStatus(values["status"])
    return DICCCertificate(**values)


def outcome_from_dict(raw: dict[str, Any]) -> UnitOutcome:
    values = dict(raw)
    values["status"] = OutcomeStatus(values["status"])
    return UnitOutcome(**values)
