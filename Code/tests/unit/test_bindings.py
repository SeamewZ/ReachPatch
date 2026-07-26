from __future__ import annotations

from pathlib import Path

from reachpatch.binding_graph import build_binding_graph
from reachpatch.binding_graph.closure import compute_binding_path_closure
from reachpatch.challenge_graph.materialize import materialize_challenges
from reachpatch.evidence import build_semantic_graph, freeze_assignment
from reachpatch.program_graph import PythonProgramGraphBuilder
from reachpatch.program_graph.slicing import causal_repair_cut
from reachpatch.requirement_graph import compile_assignment_overlay, compile_requirement_paths


FIXTURE = Path(__file__).parents[1] / "fixtures" / "simple_repo"


def build_product(issue: str):
    program = PythonProgramGraphBuilder(FIXTURE).build()
    semantic = build_semantic_graph(issue).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)
    compile_requirement_paths(requirement, program)
    binding = build_binding_graph(requirement, program)
    return requirement, program, binding


def test_binding_is_exact_path_product_with_projection_cut_and_components():
    requirement, program, binding = build_product(
        "For every x, pkg.api.public(x) must return []."
    )
    assert len(binding.units) == len(requirement.feasible_path_obligations())
    assert all(
        len(binding.by_path_obligation[path.path_obligation_id]) == 1
        for path in requirement.feasible_path_obligations()
    )
    assert all(unit.projection_witness.domain_guard_projection for unit in binding.units.values())
    assert all(unit.repair_cut_node_ids for unit in binding.units.values())
    assert binding.components
    assert all(component.legal_repair_cut_ids for component in binding.components.values())
    assert all(unit.program_graph_hash == program.program_hash() for unit in binding.units.values())
    closure = compute_binding_path_closure(requirement, binding)
    assert not closure.missing_path_obligation_ids
    assert not closure.duplicate_path_obligation_ids


def test_vague_normative_output_remains_oracle_frontier():
    _, _, binding = build_product(
        "For every x, pkg.api.public(x) must return a normalized value."
    )
    assert binding.units
    assert all(unit.status == "BLOCKED" for unit in binding.units.values())
    assert any(frontier.kind == "ORACLE_FRONTIER" for frontier in binding.frontiers.values())


def test_open_world_witness_does_not_close_universal_reach():
    requirement, program, binding = build_product(
        "For every x, pkg.api.public(x) must return []."
    )
    challenges = materialize_challenges(requirement, program, binding)
    universal = [
        frontier for frontier in challenges.frontiers.values()
        if frontier.kind == "UNIVERSAL_DOMAIN_COVERAGE"
    ]
    assert universal and all(frontier.hard for frontier in universal)


def test_causal_cut_basis_and_node_metrics_are_reused_exactly():
    requirement, program, binding = build_product(
        "For every x, pkg.api.public(x) must return []."
    )
    unit = next(iter(binding.units.values()))
    basis_cache = {}
    metric_cache = {}
    first = causal_repair_cut(
        program,
        [unit.entrypoint_id],
        unit.observation_node_ids,
        unit_slices={"first": set(unit.interaction_path_ids)},
        basis_cache=basis_cache,
        node_metric_cache=metric_cache,
    )
    basis_count = len(basis_cache)
    metric_count = len(metric_cache)
    second = causal_repair_cut(
        program,
        [unit.entrypoint_id],
        unit.observation_node_ids,
        unit_slices={"second": set(unit.interaction_path_ids)},
        basis_cache=basis_cache,
        node_metric_cache=metric_cache,
    )

    assert len(basis_cache) == basis_count == 1
    assert len(metric_cache) == metric_count
    assert first.node_ids == second.node_ids
    assert first.insertion_boundary_ids == second.insertion_boundary_ids
    assert first.excluded_node_ids == second.excluded_node_ids
    assert [
        {key: value for key, value in item.items() if key != "covered_unit_ids"}
        for item in first.ranked_nodes
    ] == [
        {key: value for key, value in item.items() if key != "covered_unit_ids"}
        for item in second.ranked_nodes
    ]


def test_repair_component_witnesses_are_a_linear_connectivity_certificate():
    _, _, binding = build_product(
        "For every x, pkg.api.public(x) must return [] and must preserve state."
    )
    for component in binding.components.values():
        unit_ids = set(component.unit_ids)
        adjacency = {unit_id: set() for unit_id in unit_ids}
        for witness in component.interaction_witnesses:
            left = witness["left"]
            right = witness["right"]
            adjacency[left].add(right)
            adjacency[right].add(left)
        reached = set()
        pending = [next(iter(unit_ids))]
        while pending:
            current = pending.pop()
            if current in reached:
                continue
            reached.add(current)
            pending.extend(adjacency[current] - reached)
        assert reached == unit_ids
        assert len(component.interaction_witnesses) <= max(0, len(unit_ids) - 1)
