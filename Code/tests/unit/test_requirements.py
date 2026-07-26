from __future__ import annotations

from pathlib import Path

from reachpatch.evidence import build_semantic_graph, freeze_assignment
from reachpatch.models.enums import Authority, RequirementAuthorityClass
from reachpatch.program_graph import PythonProgramGraphBuilder
from reachpatch.requirement_graph import compile_assignment_overlay, compile_requirement_paths
from reachpatch.requirement_graph.authority import apply_authority_change
from reachpatch.requirement_graph.closure import requirement_path_closure
from reachpatch.requirement_graph.domains import promote_program_predicates


FIXTURE = Path(__file__).parents[1] / "fixtures" / "simple_repo"


def test_six_stage_requirement_expansion_and_reverse_domain_promotion():
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return a normalized value and must preserve state."
    ).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)
    assert len(requirement.leaves) == 2
    return_leaf = next(
        leaf for leaf in requirement.leaves.values()
        if leaf.required_trace_relation["kind"] == "equality"
    )
    promoted = promote_program_predicates(return_leaf, ["not x"])
    constraints = {constraint for partition in promoted for constraint in partition.constraints}
    assert "len(x) == 0" in constraints
    assert "len(x) > 0" in constraints
    assert all(partition.scope == "REQUIREMENT" for partition in promoted)


def test_invalid_program_predicate_is_retained_as_challenge_frontier():
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return a normalized value."
    ).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)
    leaf = next(iter(requirement.leaves.values()))

    promoted = promote_program_predicates(leaf, ["x === invalid"])

    assert promoted
    assert all(partition.scope == "CHALLENGE_ONLY" for partition in promoted)
    assert all(not partition.satisfiable for partition in promoted)
    assert all(
        str(partition.proof["reason"]).startswith("unsupported_constraint")
        for partition in promoted
    )


def test_domain_partitions_remain_owned_by_their_requirement_leaf():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return [] and must preserve state."
    ).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)
    compile_requirement_paths(requirement, graph)

    assert len(requirement.leaves) == 2
    assert all(
        partition.leaf_id in requirement.leaves
        for partition in requirement.partitions.values()
    )
    assert all(
        obligation.partition.leaf_id == obligation.leaf_id
        for obligation in requirement.path_obligations.values()
    )


def test_requirement_paths_are_path_preserving_and_edge_accounted():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return a normalized value."
    ).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)
    compile_requirement_paths(requirement, graph)

    assert requirement.path_obligations
    assert all(item.path_edge_ids for item in requirement.path_obligations.values())
    assert requirement.edge_ledger
    ledger_pairs = {
        (item.path_state_id, item.program_edge_id) for item in requirement.edge_ledger.values()
    }
    assert len(ledger_pairs) == len(requirement.edge_ledger)
    closure = requirement_path_closure(requirement)
    assert 0.0 <= closure.path_coverage <= 1.0
    assert closure.missing_leaf_ids == ()


def test_visible_concrete_assertions_do_not_become_open_world_quantifiers():
    visible_test = FIXTURE / "tests" / "test_api.py"
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return [].",
        visible_test_paths=(visible_test,),
    ).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)

    preservation = [
        leaf for leaf in requirement.leaves.values()
        if leaf.authority_class.value == "PRESERVATION"
    ]
    hard = [
        leaf for leaf in requirement.leaves.values()
        if leaf.authority_class.value == "HARD"
    ]
    assert preservation
    assert all(not leaf.quantified_variables for leaf in preservation)
    assert len(hard) == 1
    assert [item.name for item in hard[0].quantified_variables] == ["x"]


def test_exact_public_symbol_closes_paths_without_module_or_post_observation_frontiers():
    graph = PythonProgramGraphBuilder(FIXTURE).build()
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return []."
    ).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)
    compile_requirement_paths(requirement, graph)

    leaf = next(iter(requirement.leaves.values()))
    closure = requirement_path_closure(requirement)
    assert leaf.entrypoint_hypotheses == ("pkg.api.public",)
    assert closure.closed
    assert not [item for item in graph.frontiers.values() if item.hard]


def test_authority_change_conservatively_invalidates_hashed_path_ledger():
    program = PythonProgramGraphBuilder(FIXTURE).build()
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return []."
    ).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)
    compile_requirement_paths(requirement, program)
    leaf = next(iter(requirement.leaves.values()))
    owned_paths = tuple(sorted(
        item.path_obligation_id
        for item in requirement.path_obligations.values()
        if item.leaf_id == leaf.leaf_id
    ))

    change = apply_authority_change(
        requirement,
        leaf.leaf_id,
        authority=Authority.B,
        authority_class=RequirementAuthorityClass.DERIVED,
        reason="independent contract accepted",
    )

    assert change.invalidated_path_obligation_ids == owned_paths
    assert change.invalidated_ledger_ids == tuple(sorted(requirement.edge_ledger))
    assert requirement.leaves[leaf.leaf_id].coverage_status == (
        "AUTHORITY_CHANGED_RECOMPILE_REQUIRED"
    )
