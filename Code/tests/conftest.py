from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from reachpatch.models.base import content_hash
from reachpatch.execution.worktree import diff_between
from reachpatch.models.evidence import (
    ExecutableOracle, LockedCheckSet, ObservationBundle, ObservationContract,
    OutcomeStatus,
)
from reachpatch.models.graphs import (
    BindingGraph, BindingStatus, BindingUnit, ChallengeCell, ChallengeGraph,
    ChallengeStatus, ExecutableScenario, GraphStack, InputRecipe, PathClass,
    ProgramGraph, ProgramNode, ProgramNodeKind, RequirementGraph,
    RequirementLeaf,
)
from reachpatch.models.reach_avoid import (
    CheckpointEvidence, GeneratorSession, ReachAvoidPhase, ReachAvoidState,
    StateCheckpoint,
)


def make_stack(
    *,
    patch_hash: str = "patch-hash",
    target_status: ChallengeStatus = ChallengeStatus.PENDING,
    preservation_status: ChallengeStatus | None = None,
    stability_runs: int = 0,
) -> GraphStack:
    contract = ObservationContract("calc must return 2", 2, comparator="equals")
    target = RequirementLeaf(
        "req-target", "RETURN_CONTRACT", "FOR_ALL", (), (), (), "calc",
        contract, None, False, "A", ("evidence-target",), (),
        OutcomeStatus.PASS if target_status is ChallengeStatus.PASS else OutcomeStatus.UNKNOWN,
        True,
    )
    leaves = {target.requirement_id: target}
    if preservation_status is not None:
        leaves["req-preservation"] = RequirementLeaf(
            "req-preservation", "PRESERVATION", "CONTRACT", (), (), (), "calc",
            ObservationContract("existing callers remain successful", 0), None,
            True, "A", ("evidence-preservation",), (),
            OutcomeStatus.PASS if preservation_status is ChallengeStatus.PASS else OutcomeStatus.UNKNOWN,
            True,
        )
    requirement = RequirementGraph(leaves)
    node = ProgramNode("symbol-calc", ProgramNodeKind.FUNCTION, "calc.py", "calc", 1, 3, True)
    path = PathClass("path-calc", "calc", (), "DIRECT", "RETURN", "RETURN_VALUE", node_ids=(node.node_id,))
    program = ProgramGraph(patch_hash, "base", {node.node_id: node}, {}, {path.path_class_id: path}, {"calc.py": "file"})
    target_binding = BindingUnit(
        "binding-target", target.requirement_id, path.path_class_id, (node.node_id,),
        (), ("hunk-calc",), (), (), ("check-target",), (), (), (), (),
        "A", BindingStatus.TARGET_PASSING if target_status is ChallengeStatus.PASS else BindingStatus.STATIC_ACTIONABLE,
        target.evidence_ids,
    )
    units = {target_binding.binding_id: target_binding}
    if preservation_status is not None:
        units["binding-preservation"] = BindingUnit(
            "binding-preservation", "req-preservation", path.path_class_id,
            (node.node_id,), (), ("hunk-calc",), (), (), (),
            ("check-preservation",), (), (), (), "A",
            BindingStatus.TARGET_PASSING if preservation_status is ChallengeStatus.PASS else BindingStatus.STATIC_ACTIONABLE,
            ("evidence-preservation",),
        )
    binding = BindingGraph(patch_hash, requirement.graph_hash(), program.graph_hash(), units)
    recipe = InputRecipe("recipe-target", "PUBLIC_REPLAY", None, ("public input",), ("python", "check.py"), trace_symbols=(node.node_id,))
    scenario = ExecutableScenario("scenario-target", recipe.command, ".", (), 10.0)
    oracle = ExecutableOracle("oracle-target", "A", contract.relation, 2, True, ("evidence-target",))
    target_cell = ChallengeCell(
        "challenge-target", patch_hash, target.requirement_id, target_binding.binding_id,
        path.path_class_id, ("hunk-calc",), "TARGET", recipe, scenario,
        contract, oracle, "A", None,
        OutcomeStatus.PASS if target_status is ChallengeStatus.PASS else None,
        "paired-target" if stability_runs else None, stability_runs, target_status,
        True, "PUBLIC_CHECK",
    )
    cells = {target_cell.challenge_id: target_cell}
    units[target_binding.binding_id] = replace(target_binding, challenge_ids=(target_cell.challenge_id,))
    if preservation_status is not None:
        preservation = leaves["req-preservation"]
        preservation_recipe = replace(recipe, recipe_id="recipe-preservation")
        preservation_cell = ChallengeCell(
            "challenge-preservation", patch_hash, preservation.requirement_id,
            "binding-preservation", path.path_class_id, ("hunk-calc",),
            "PRESERVATION", preservation_recipe, scenario,
            preservation.expected_observation,
            replace(oracle, oracle_id="oracle-preservation", relation=preservation.expected_observation.relation),
            "A", None,
            OutcomeStatus.PASS if preservation_status is ChallengeStatus.PASS else None,
            "paired-preservation" if stability_runs else None,
            stability_runs, preservation_status, True, "IMPACT_CALLER",
        )
        cells[preservation_cell.challenge_id] = preservation_cell
        units["binding-preservation"] = replace(
            units["binding-preservation"], challenge_ids=(preservation_cell.challenge_id,),
        )
    binding = replace(binding, units=units)
    challenge = ChallengeGraph(patch_hash, binding.graph_hash(), cells)
    stack = GraphStack(patch_hash, 0, requirement, program, binding, challenge)
    stack.validate()
    return stack


def make_state(tmp_path: Path, **stack_options) -> ReachAvoidState:
    base = tmp_path / "base"
    tree = tmp_path / "tree"
    base.mkdir()
    tree.mkdir()
    (base / "calc.py").write_text("def calc():\n    return 1\n", encoding="utf-8")
    (tree / "calc.py").write_text("def calc():\n    return 2\n", encoding="utf-8")
    actual = diff_between(base, tree)
    stack_options.setdefault("patch_hash", actual.patch_hash)
    stack = make_stack(**stack_options)
    checkpoint_evidence = CheckpointEvidence(True, True, 0, 0, 0, 0, 1, 0)
    checkpoint = StateCheckpoint(
        "checkpoint-working", None, str(tree), stack.patch_hash,
        actual.canonical_diff, stack.graph_hashes(), "",
        checkpoint_evidence, (), (), ("challenge-target",), "WORKING", 0,
    )
    return ReachAvoidState(
        "instance", "run", base, "base", tmp_path / "run", stack,
        checkpoint, checkpoint, None, {checkpoint.checkpoint_id: checkpoint},
        ObservationBundle(), [], LockedCheckSet(), [], {},
        GeneratorSession("session"), None, 0, 0, 0, 0, {},
        ReachAvoidPhase.CHALLENGE, None, 100, 100,
    )


@pytest.fixture
def state_factory(tmp_path):
    return lambda **options: make_state(tmp_path, **options)
