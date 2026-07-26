from __future__ import annotations

from reachpatch.evidence import build_semantic_graph, freeze_assignment
from reachpatch.models.enums import OracleLifecycle, OutcomeStatus
from reachpatch.oracle.authority import resolve_oracle
from reachpatch.oracle.classifier import classify_pair
from reachpatch.oracle.models import ExecutableScenario, RunObservation
from reachpatch.requirement_graph import compile_assignment_overlay


def leaf_for(issue: str):
    semantic = build_semantic_graph(issue).graph
    assignment = freeze_assignment(semantic, selection_mode="certified")
    requirement = compile_assignment_overlay(semantic, assignment)
    return next(iter(requirement.leaves.values()))


def test_oracle_requires_executable_relation_not_normative_vagueness():
    vague = resolve_oracle(leaf_for("api.normalize(x) must return a normalized value."))
    exact = resolve_oracle(leaf_for("api.normalize(x) must return 2."))

    assert vague.lifecycle == OracleLifecycle.DOWNGRADED
    assert vague.executable is False
    assert exact.lifecycle == OracleLifecycle.ACTIVE
    assert exact.active_and_trusted
    assert exact.relation["expected"] == 2


def test_paired_classifier_separates_patch_failure_and_shared_setup_unknown():
    leaf = leaf_for("api.normalize(x) must return 2.")
    oracle = resolve_oracle(leaf)
    from reachpatch.oracle.authority import observation_contract_from_leaf

    scenario = ExecutableScenario(
        scenario_id="s",
        binding_unit_id="u",
        assignment_scope="ALL",
        setup=(),
        stimulus=(),
        observe=observation_contract_from_leaf(leaf),
        oracle=oracle,
        evidence_ids=leaf.supporting_evidence,
        isolation={},
        timeout_seconds=1,
        kind="TARGET",
        source_hashes={},
        evidence_cluster_id=oracle.evidence_cluster_id,
    )
    base = RunObservation(
        "base", "env", "observe", True, False, False, False, False,
        OutcomeStatus.FAIL, {"return": 1}, ("return",), "", "", 0, "base-hash",
    )
    patch = RunObservation(
        "patch", "env", "observe", True, False, False, False, False,
        OutcomeStatus.PASS, {"return": 2}, ("return",), "", "", 0, "patch-hash",
    )
    assert classify_pair(base, patch, scenario).status == OutcomeStatus.PASS

    shared_base = RunObservation(
        "base-shared", "env", "setup", False, False, True, False, False,
        OutcomeStatus.UNKNOWN, {}, (), "", "dependency", 0, "base-hash",
    )
    shared_patch = RunObservation(
        "patch-shared", "env", "setup", False, False, True, False, False,
        OutcomeStatus.UNKNOWN, {}, (), "", "dependency", 0, "patch-hash",
    )
    classified = classify_pair(shared_base, shared_patch, scenario)
    assert classified.status == OutcomeStatus.UNKNOWN
    assert classified.failure_origin == "SHARED_SETUP_OR_DEPENDENCY"


def test_target_classifier_accepts_patch_that_restores_a_missing_return_channel():
    leaf = leaf_for("api.normalize(x) must return 2.")
    oracle = resolve_oracle(leaf)
    from reachpatch.oracle.authority import observation_contract_from_leaf

    scenario = ExecutableScenario(
        scenario_id="missing-return",
        binding_unit_id="u",
        assignment_scope="ALL",
        setup=(),
        stimulus=(),
        observe=observation_contract_from_leaf(leaf),
        oracle=oracle,
        evidence_ids=leaf.supporting_evidence,
        isolation={},
        timeout_seconds=1,
        kind="TARGET",
        source_hashes={},
        evidence_cluster_id=oracle.evidence_cluster_id,
    )
    base = RunObservation(
        "base-exception", "env", "stimulus", True, False, False, False, False,
        OutcomeStatus.FAIL, {"exception": {"type": "ValueError"}}, ("exception",),
        "", "", 0, "base-hash",
    )
    patch = RunObservation(
        "patch-return", "env", "observe", True, False, False, False, False,
        OutcomeStatus.PASS, {"return": 2}, ("return",), "", "", 0, "patch-hash",
    )

    classified = classify_pair(base, patch, scenario)
    assert classified.status == OutcomeStatus.PASS
    assert classified.base_evaluation.reason == "required_channel_absent_on_base"
