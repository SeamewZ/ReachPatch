from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable

from reachpatch.binding_graph.models import BindingGraph, BindingUnit
from reachpatch.challenge_graph.models import ScenarioProposal
from reachpatch.challenge_graph.recipes import (
    CandidateGenerator,
    InputRecipe,
    TraceSpec,
    recipe_from_scenario,
)
from reachpatch.models.base import stable_id
from reachpatch.oracle.models import ExecutableScenario
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.requirement_graph.models import RequirementGraph


def _proposal(
    operator_id: str,
    axis: str,
    unit: BindingUnit,
    scenario: ExecutableScenario,
    recipe: InputRecipe,
    *,
    graph_witness_ids: Iterable[str],
    partition_id: str,
    route_id: str,
    origin: str = "BASELINE_GRAPH",
    hard: bool = True,
    unresolved_fields: Iterable[str] = (),
) -> ScenarioProposal:
    proposal_id = stable_id(
        "scenario-proposal", operator_id, unit.unit_id, recipe.recipe_id,
        partition_id, route_id, scenario.oracle.oracle_id,
    )
    return ScenarioProposal(
        proposal_id=proposal_id,
        binding_unit_id=unit.unit_id,
        operator_id=operator_id,
        graph_witness_ids=tuple(graph_witness_ids),
        public_trigger_id=unit.trigger_id,
        partition_id=partition_id,
        route_id=route_id,
        recipe=recipe,
        scenario=scenario,
        locked_oracle_id=scenario.oracle.oracle_id,
        changed_dimension=axis,
        expected_observation_class=scenario.observe.channels,
        unresolved_fields=tuple(unresolved_fields),
        origin=origin,
        hard=hard,
    )


class BaselineOperator:
    operator_id = "baseline_exact_path"
    axis = "baseline"

    def applicable(self, unit, requirement_graph, program_graph, binding_graph) -> bool:
        return bool(unit.scenario_ids)

    def propose(self, unit, requirement_graph, program_graph, binding_graph):
        scenario = binding_graph.scenarios[unit.scenario_ids[0]]
        obligation = requirement_graph.path_obligations[unit.path_obligation_id]
        return [_proposal(
            self.operator_id,
            self.axis,
            unit,
            scenario,
            recipe_from_scenario(scenario),
            graph_witness_ids=unit.interaction_path_ids,
            partition_id=obligation.scenario_partition_id,
            route_id=unit.path_class_id,
        )]


class InputPartitionOperator:
    operator_id = "input_partition"
    axis = "input_partition"

    def applicable(self, unit, requirement_graph, program_graph, binding_graph) -> bool:
        if not unit.scenario_ids:
            return False
        leaf = requirement_graph.leaves[unit.leaf_id]
        return bool(leaf.quantified_variables) and leaf.authority_class.value != "PRESERVATION"

    def propose(self, unit, requirement_graph, program_graph, binding_graph):
        scenario = binding_graph.scenarios[unit.scenario_ids[0]]
        leaf = requirement_graph.leaves[unit.leaf_id]
        obligation = requirement_graph.path_obligations[unit.path_obligation_id]
        proposals: list[ScenarioProposal] = []
        for bindings in CandidateGenerator().generate(
            leaf, obligation.partition, limit=32
        ):
            base_recipe = recipe_from_scenario(scenario)
            stimulus = list(base_recipe.stimulus)
            if not stimulus or stimulus[0].get("op") != "call":
                continue
            stimulus[0] = {
                **stimulus[0],
                "args": list(bindings.values()),
                "kwargs": {},
            }
            recipe = InputRecipe.create(
                imports=base_recipe.imports,
                setup=base_recipe.setup,
                stimulus=stimulus,
                observations=base_recipe.observations,
                teardown=base_recipe.teardown,
                environment=base_recipe.environment,
                resource_limits=base_recipe.resource_limits,
                provenance_ids=base_recipe.provenance_ids,
            )
            proposals.append(_proposal(
                self.operator_id,
                self.axis,
                unit,
                scenario,
                recipe,
                graph_witness_ids=obligation.path_edge_ids,
                partition_id=stable_id("candidate-partition", obligation.scenario_partition_id, bindings),
                route_id=unit.path_class_id,
            ))
        return proposals


class RouteOperator:
    operator_id = "alternate_route"
    axis = "route"

    def applicable(self, unit, requirement_graph, program_graph, binding_graph) -> bool:
        return bool(unit.scenario_ids and unit.bypass_path_ids)

    def propose(self, unit, requirement_graph, program_graph, binding_graph):
        scenario = binding_graph.scenarios[unit.scenario_ids[0]]
        recipe = recipe_from_scenario(scenario)
        obligation = requirement_graph.path_obligations[unit.path_obligation_id]
        return [
            _proposal(
                self.operator_id,
                self.axis,
                unit,
                scenario,
                recipe,
                graph_witness_ids=(bypass_id,),
                partition_id=obligation.scenario_partition_id,
                route_id=bypass_id,
            )
            for bypass_id in unit.bypass_path_ids
        ]


class TraceRelationOperator:
    operator_id = "trace_relation"
    axis = "trace_relation"

    def applicable(self, unit, requirement_graph, program_graph, binding_graph) -> bool:
        if not unit.scenario_ids:
            return False
        scenario = binding_graph.scenarios[unit.scenario_ids[0]]
        return scenario.observe.multi_trace_relation is not None

    def propose(self, unit, requirement_graph, program_graph, binding_graph):
        scenario = binding_graph.scenarios[unit.scenario_ids[0]]
        base = recipe_from_scenario(scenario)
        relation = scenario.observe.multi_trace_relation or "relation"
        trace = TraceSpec(
            trace_id=stable_id("trace-spec", unit.unit_id, relation),
            steps=base.stimulus + base.observations,
            reset_before=False,
            relation_role=relation,
        )
        recipe = InputRecipe.create(
            imports=base.imports,
            setup=base.setup,
            stimulus=base.stimulus,
            observations=base.observations,
            teardown=base.teardown,
            traces=(trace,),
            environment=base.environment,
            resource_limits=base.resource_limits,
            provenance_ids=base.provenance_ids,
        )
        obligation = requirement_graph.path_obligations[unit.path_obligation_id]
        return [_proposal(
            self.operator_id,
            self.axis,
            unit,
            scenario,
            recipe,
            graph_witness_ids=unit.observation_node_ids,
            partition_id=obligation.scenario_partition_id,
            route_id=unit.path_class_id,
        )]


class PreservationOperator:
    operator_id = "preservation_impact"
    axis = "preservation_impact"

    def applicable(self, unit, requirement_graph, program_graph, binding_graph) -> bool:
        return bool(unit.scenario_ids) and (
            binding_graph.scenarios[unit.scenario_ids[0]].kind == "PRESERVATION"
        )

    def propose(self, unit, requirement_graph, program_graph, binding_graph):
        scenario = binding_graph.scenarios[unit.scenario_ids[0]]
        recipe = recipe_from_scenario(scenario)
        obligation = requirement_graph.path_obligations[unit.path_obligation_id]
        witnesses = unit.preservation_node_ids or unit.interaction_path_ids
        return [_proposal(
            self.operator_id,
            self.axis,
            unit,
            scenario,
            recipe,
            graph_witness_ids=witnesses,
            partition_id=obligation.scenario_partition_id,
            route_id=unit.path_class_id,
        )]


class ExceptionResourceOperator:
    operator_id = "exception_resource_schedule"
    axis = "exception_resource_schedule"

    def applicable(self, unit, requirement_graph, program_graph, binding_graph) -> bool:
        return bool(unit.scenario_ids) and (
            unit.exit_kind == "exception"
            or any(
                program_graph.nodes[node_id].kind in {"exception", "external_interface"}
                for node_id in unit.interaction_path_ids
            )
        )

    def propose(self, unit, requirement_graph, program_graph, binding_graph):
        scenario = binding_graph.scenarios[unit.scenario_ids[0]]
        recipe = recipe_from_scenario(scenario)
        obligation = requirement_graph.path_obligations[unit.path_obligation_id]
        witnesses = tuple(
            node_id
            for node_id in unit.interaction_path_ids
            if program_graph.nodes[node_id].kind in {"exception", "external_interface"}
        )
        return [_proposal(
            self.operator_id,
            self.axis,
            unit,
            scenario,
            recipe,
            graph_witness_ids=witnesses or unit.observation_node_ids,
            partition_id=obligation.scenario_partition_id,
            route_id=unit.path_class_id,
        )]


class ScenarioOperatorRegistry:
    def __init__(self) -> None:
        self._operators: dict[str, Any] = {}

    def register(self, operator: Any) -> None:
        if operator.operator_id in self._operators:
            raise ValueError(f"scenario operator already registered: {operator.operator_id}")
        self._operators[operator.operator_id] = operator

    def operators(self) -> list[Any]:
        return [self._operators[key] for key in sorted(self._operators)]

    @classmethod
    def default(cls) -> "ScenarioOperatorRegistry":
        registry = cls()
        for operator in (
            BaselineOperator(),
            InputPartitionOperator(),
            RouteOperator(),
            TraceRelationOperator(),
            PreservationOperator(),
            ExceptionResourceOperator(),
        ):
            registry.register(operator)
        return registry
