from pathlib import Path
from types import SimpleNamespace

from reachpatch.execution.models import (
    CheckExecution, CheckRole, CheckStatus, ExecutableCheck,
)
from reachpatch.models.controller import (
    ConfirmedFailure, ExecutableOracle, LockedCheck, LockedCheckSet,
)
from reachpatch.models.base import stable_id
from reachpatch.models.graph import GraphEdge, GraphNode
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.binding_graph.active import (
    ActiveBindingGraph, ActiveBindingUnit, BindingEdge, _causal_cuts,
    _check_projection, recover_direct_callers,
)
from reachpatch.execution.target_recovery import TargetCandidate, certify_target
from reachpatch.models.enums import Authority, ChallengeTerminalStatus
from reachpatch.oracle.models import ObservationContract
from reachpatch.repair.policy import accept_edit_scope
from reachpatch.repair.counterexamples import challenge_is_confirmed
from reachpatch.reach_avoid.trajectory import (
    compare_observations, decide_transition, evaluate_trial_against_checkpoint,
    is_confirmed_failure, record_transition, refresh_confirmed_failures,
)


def _execution(check_id, status, *, stable=True, signature=None, tree="tree"):
    return CheckExecution(
        execution_id=stable_id("execution", check_id, status, tree),
        check_id=check_id,
        tree_hash=tree,
        status=CheckStatus(status),
        return_code=0 if status == "PASS" else 1,
        stdout="",
        stderr="",
        duration_seconds=0.01,
        stable=stable,
        failure_signature=signature,
        first_project_frame=None,
    )


def _locked(check_id="target", role="TARGET", baseline="FAIL"):
    check = ExecutableCheck(
        check_id=check_id,
        role=CheckRole(role),
        authority="PUBLIC",
        command=("python", "-c", "raise SystemExit(1)"),
        cwd=".",
        environment={},
        timeout_seconds=5.0,
        source_evidence_ids=("public-test",),
        target_requirement_ids=("req",),
        temporary_artifact_paths=(),
    )
    return LockedCheck(
        check_id=check.check_id,
        role=check.role.value,
        command=check.command,
        observation_contract=SimpleNamespace(),
        oracle=ExecutableOracle(
            oracle_id="oracle", authority="A",
            relation="baseline_failure_must_become_pass",
        ),
        authority="A",
        requirement_ids=("req",),
        cwd=check.cwd,
        timeout_seconds=check.timeout_seconds,
        source_evidence_ids=check.source_evidence_ids,
        baseline_observation=_execution(check_id, baseline, tree="base"),
    )


def test_unknown_locked_execution_cannot_promote():
    locked = LockedCheckSet(lock_id="lock", target_checks=(_locked(),))
    comparison = compare_observations(
        before_results=(_execution("target", "FAIL", tree="before"),),
        after_results=(_execution("target", "PASS", stable=False, tree="after"),),
        locked_checks=locked,
        before_patch_hash="before",
        after_patch_hash="after",
    )
    assert not comparison.comparable
    assert decide_transition(comparison)[0] == "ROLLBACK"


def test_target_improvement_promotes_only_when_locked_checks_are_comparable():
    locked = LockedCheckSet(lock_id="lock", target_checks=(_locked(),))
    comparison = compare_observations(
        before_results=(_execution("target", "FAIL", tree="before"),),
        after_results=(_execution("target", "PASS", tree="after"),),
        locked_checks=locked,
        before_patch_hash="before",
        after_patch_hash="after",
    )
    assert comparison.comparable
    assert decide_transition(comparison) == (
        "PROMOTE", "CONFIRMED_EXECUTION_IMPROVEMENT",
    )


def test_target_fix_with_regression_is_kept_but_not_promoted():
    locked = LockedCheckSet(
        lock_id="lock",
        target_checks=(_locked(),),
        preservation_checks=(_locked("preserve", "PRESERVATION", "PASS"),),
    )
    comparison = compare_observations(
        before_results=(
            _execution("target", "FAIL", tree="before"),
            _execution("preserve", "PASS", tree="before"),
        ),
        after_results=(
            _execution("target", "PASS", tree="after"),
            _execution("preserve", "FAIL", tree="after"),
        ),
        locked_checks=locked,
        before_patch_hash="before",
        after_patch_hash="after",
    )
    assert decide_transition(comparison)[0] == "KEEP_TRIAL_FOR_REGRESSION_REPAIR"


def test_ongoing_regression_cannot_be_misreported_as_fixed():
    locked = LockedCheckSet(
        lock_id="lock",
        target_checks=(_locked(),),
        preservation_checks=(_locked("preserve", "PRESERVATION", "PASS"),),
    )
    still_broken = compare_observations(
        before_results=(
            _execution("target", "PASS", tree="before"),
            _execution("preserve", "FAIL", tree="before"),
        ),
        after_results=(
            _execution("target", "PASS", tree="after"),
            _execution("preserve", "FAIL", tree="after"),
        ),
        locked_checks=locked,
        before_patch_hash="before",
        after_patch_hash="after",
    )
    assert still_broken.preservation_regressions_after == ("preserve",)
    assert decide_transition(still_broken)[0] == "ROLLBACK"

    repaired = compare_observations(
        before_results=still_broken.before_results,
        after_results=(
            _execution("target", "PASS", tree="fixed"),
            _execution("preserve", "PASS", tree="fixed"),
        ),
        locked_checks=locked,
        before_patch_hash="before",
        after_patch_hash="fixed",
    )
    assert decide_transition(repaired)[0] == "PROMOTE"


def test_llm_authority_failure_is_not_confirmed():
    before = _execution("target", "FAIL", signature="same", tree="before")
    failure = ConfirmedFailure(
        failure_id="failure",
        kind="CONFIRMED_TARGET_FAILURE",
        check_id="target",
        oracle_authority="E",
        requirement_id="req",
        binding_unit_id="unit",
        baseline_observation=before,
        before_patch_observation=before,
        expected_relation=ExecutableOracle(
            oracle_id="oracle", authority="E",
            relation="baseline_failure_must_become_pass",
        ),
        stable_runs=2,
        failure_signature="same",
        failure_location=None,
        causal_cut_ids=(),
        impact_risk_ids=(),
    )
    assert not is_confirmed_failure(failure)


def test_unknown_or_unbound_graph_does_not_make_confirmed_failure():
    comparison = SimpleNamespace(
        check_id="target",
        baseline=_execution("target", "FAIL", tree="base"),
        patched=_execution("target", "FAIL", tree="patch"),
        classification="TARGET_STILL_FAILING",
    )
    state = SimpleNamespace(
        target_recovery=SimpleNamespace(
            targets=(ExecutableCheck(
                check_id="target", role=CheckRole.TARGET,
                authority="PUBLIC", command=("python",), cwd=".",
                environment={}, timeout_seconds=5.0,
                source_evidence_ids=(), target_requirement_ids=(),
                temporary_artifact_paths=(),
            ),),
            preservation_checks=(), baseline_executions=(comparison.baseline,),
            candidates=(),
        ),
        check_comparisons=(comparison,),
        active_binding_graph=SimpleNamespace(units={}),
        confirmed_failures=[], patch_trajectory=None,
        checkpoint=SimpleNamespace(patch=SimpleNamespace(canonical_diff_hash="hash")),
    )
    assert refresh_confirmed_failures(state) == ()


def test_direct_caller_depth_expands_real_reverse_call_edges():
    graph = ProgramGraph(repository_root=".", source_hash="source")
    caller = graph.add_node(GraphNode.create("function", "caller"))
    middle = graph.add_node(GraphNode.create("function", "middle"))
    target = graph.add_node(GraphNode.create("function", "target"))
    graph.add_edge(GraphEdge.create("calls", (caller.node_id,), (middle.node_id,)))
    graph.add_edge(GraphEdge.create("calls", (middle.node_id,), (target.node_id,)))

    assert recover_direct_callers(graph, {target.node_id}, 1) == (middle.node_id,)
    assert recover_direct_callers(graph, {target.node_id}, 2) == (
        middle.node_id, caller.node_id,
    )


def test_check_projection_does_not_bind_unrelated_text_overlap():
    leaf = SimpleNamespace(
        leaf_id="req", formula="public values must be returned",
        supporting_evidence=(), entrypoint_hypotheses=("pkg.api.public",),
    )
    unrelated = ExecutableCheck(
        check_id="unrelated", role=CheckRole.TARGET, authority="PUBLIC",
        command=("python",), cwd=".", environment={}, timeout_seconds=5.0,
        source_evidence_ids=("mentions-public-values",),
        target_requirement_ids=(), temporary_artifact_paths=(),
    )
    assert _check_projection(
        leaf,
        SimpleNamespace(targets=(unrelated,), preservation_checks=()),
        (),
    ) == ((), (), ())


def test_check_projection_resolves_executed_symbol_to_qualified_definition():
    graph = ProgramGraph(repository_root=".", source_hash="source")
    definition = graph.add_node(GraphNode.create(
        "function", "public",
        attributes={"file": "pkg/api.py", "line": 4, "end_line": 5,
                    "qualified_name": "pkg.api.public"},
    ))
    leaf = SimpleNamespace(
        leaf_id="req", formula="public behavior must be preserved",
        supporting_evidence=(), entrypoint_hypotheses=("pkg.api.public",),
    )
    public_test = ExecutableCheck(
        check_id="public-test", role=CheckRole.PRESERVATION,
        authority="PUBLIC", command=("python", "-m", "pytest"), cwd=".",
        environment={}, timeout_seconds=5.0,
        source_evidence_ids=("public-test",), target_requirement_ids=(),
        temporary_artifact_paths=(), executed_symbol_ids=("public",),
    )

    assert _check_projection(
        leaf,
        SimpleNamespace(targets=(), preservation_checks=(public_test,),
                        candidates=()),
        (), symbol_ids=(definition.node_id,), program=graph,
    ) == ((), ("public-test",), ())


def test_edit_scope_rejects_unexplained_public_utility_change():
    relation = SimpleNamespace(
        relation_id="signature-change", kind="signature_changed",
        qualified_scope="shared.util.parse",
    )
    decision = accept_edit_scope(
        {
            "files_to_modify": ("pkg/target.py",),
            "symbols_to_modify": ("pkg.target.run",),
            "causal_cut_ids": ("target-cut",),
        },
        SimpleNamespace(
            changed_files=("pkg/target.py", "shared/util.py"),
            changed_relations=(relation,),
        ),
        SimpleNamespace(),
    )
    assert not decision.allowed
    assert decision.unexplained_files == ("shared/util.py",)
    assert decision.signature_changes == ("signature-change",)


def _candidate(check_id="target"):
    return TargetCandidate(
        target_id=check_id,
        strategy="related_public_test",
        input_source="PUBLIC_REPOSITORY",
        oracle_authority="A",
        setup_commands=(),
        command=("python", "-c", "raise SystemExit(1)"),
        observation_contract=ObservationContract(
            contract_id="contract", channels=("process_status",),
        ),
        oracle=ExecutableOracle(
            oracle_id="oracle", authority="A",
            relation="baseline_failure_must_become_pass",
        ),
        target_requirement_ids=("req",),
        source_evidence_ids=("public-test",),
        executed_symbol_ids=("pkg.api.public",),
        stability_runs=2,
        status="MECHANICALLY_VERIFIED",
    )


def _active_graph(*, execution_edge=False):
    unit = ActiveBindingUnit(
        binding_id="unit", requirement_id="req", requirement_text="requirement",
        requirement_authority="A", program_symbol_ids=("symbol",),
        changed_hunk_ids=("hunk",), target_check_ids=("target",),
    )
    edges = [BindingEdge("unit", "target", "UNIT_EXECUTABLE_CHECK")]
    if execution_edge:
        edges.append(BindingEdge("target", "symbol", "CHECK_EXECUTED_SYMBOL"))
    return ActiveBindingGraph(
        instance_id="case", revision=1, diff_hash="diff",
        program_slice_hash="program", requirement_graph_hash="requirements",
        units={"unit": unit}, edges=edges, target_check_ids=("target",),
    )


def test_target_certification_requires_a_real_executed_symbol_binding():
    baseline = _execution("target", "FAIL", signature="bug", tree="base")
    rejected = certify_target(_candidate(), (baseline,), _active_graph())
    accepted = certify_target(
        _candidate(), (baseline,), _active_graph(execution_edge=True),
    )
    assert not rejected.certified
    assert not rejected.reaches_related_code
    assert accepted.certified


def test_environment_failure_cannot_certify_as_target_failure():
    environment_failure = _execution(
        "target", "INVALID_ENVIRONMENT", signature="dependency", tree="base",
    )
    certification = certify_target(
        _candidate(), (environment_failure,), _active_graph(execution_edge=True),
    )
    assert not certification.certified
    assert not certification.exposes_issue


def test_dicc_confirmation_rejects_unstable_or_untrusted_execution():
    cell = SimpleNamespace(
        terminal_status=ChallengeTerminalStatus.FAIL,
        baseline_outcome="PASS", patched_outcome="FAIL",
    )
    scenario = SimpleNamespace(oracle=SimpleNamespace(
        authority=Authority.A, executable=True,
    ))
    stable_bundle = SimpleNamespace(
        stability_status="STABLE",
        base_bundle=SimpleNamespace(runs=(object(), object())),
        patch_bundle=SimpleNamespace(runs=(object(), object())),
    )
    unit = SimpleNamespace(changed_hunk_ids=("hunk",), program_symbol_ids=())
    assert challenge_is_confirmed(cell, scenario, stable_bundle, unit)
    unstable = SimpleNamespace(
        stability_status="FLAKY",
        base_bundle=stable_bundle.base_bundle,
        patch_bundle=stable_bundle.patch_bundle,
    )
    assert not challenge_is_confirmed(cell, scenario, unstable, unit)
    scenario.oracle.authority = SimpleNamespace(value="E")
    assert not challenge_is_confirmed(cell, scenario, stable_bundle, unit)


def test_locked_check_set_is_identical_for_before_and_after_execution(tmp_path):
    locked = LockedCheckSet(lock_id="lock", target_checks=(_locked(),))
    calls = []

    class Runner:
        def run_check(self, check, *, repository, tree_hash):
            calls.append((check.check_id, str(repository), tree_hash))
            status = "PASS" if Path(repository).name == "after" else "FAIL"
            return _execution(check.check_id, status, tree=tree_hash)

    comparison = evaluate_trial_against_checkpoint(
        before=SimpleNamespace(patch_hash="before"),
        after_patch_hash="after",
        before_repository=tmp_path / "before",
        after_repository=tmp_path / "after",
        locked_checks=locked,
        project_runner=Runner(),
        before_tree_hash="before-tree",
        after_tree_hash="after-tree",
    )
    assert comparison.executed_check_ids == ("target",)
    assert [item[0] for item in calls] == ["target", "target"]


def test_failure_history_changes_policy_only_after_confirmed_repeats():
    trajectory = SimpleNamespace(
        working_patch=SimpleNamespace(checkpoint_id="working"),
        best_evidence_patch=SimpleNamespace(checkpoint_id="best"),
        trial_patch=None, checkpoint_archive={}, revision_history=[],
        regression_repair_attempts=0,
    )
    state = SimpleNamespace(
        patch_trajectory=trajectory,
        failure_histories={}, prohibited_mechanisms=set(), runtime_metrics={},
        active_binding_graph=SimpleNamespace(units={
            "unit": SimpleNamespace(program_symbol_ids=("symbol",)),
        }),
    )
    failure = SimpleNamespace(
        failure_id="failure", failure_signature="same",
        kind="CONFIRMED_TARGET_FAILURE", binding_unit_id="unit",
        causal_cut_ids=("cut",),
    )
    comparison = SimpleNamespace(
        comparison_id="comparison", lock_id="lock",
        executed_check_ids=("target",),
    )
    for index in range(3):
        record_transition(
            state, failure=failure, comparison=comparison,
            trial=SimpleNamespace(checkpoint_id=f"trial-{index}"),
            action_id=f"action-{index}", mechanism_id="guard_tighten",
            decision="ROLLBACK", reason="NO_CONFIRMED_IMPROVEMENT",
        )
        if index == 0:
            assert "guard_tighten" not in state.prohibited_mechanisms
        if index == 1:
            assert "guard_tighten" in state.prohibited_mechanisms
    assert state.runtime_metrics["root_recovery_required"]


def test_causal_cut_uses_failure_backward_data_slice():
    graph = ProgramGraph(repository_root=".", source_hash="source")
    producer = graph.add_node(GraphNode.create(
        "assignment", "producer",
        attributes={"file": "pkg/api.py", "line": 2, "end_line": 2,
                    "qualified_name": "pkg.api.public"},
    ))
    sink = graph.add_node(GraphNode.create(
        "return", "sink",
        attributes={"file": "pkg/api.py", "line": 3, "end_line": 3,
                    "qualified_name": "pkg.api.public"},
    ))
    graph.add_edge(GraphEdge.create(
        "data_flow", (producer.node_id,), (sink.node_id,),
    ))
    diff = SimpleNamespace(
        changed_files=("pkg/api.py",), changed_relations=(),
    )
    cuts = _causal_cuts(
        diff, graph, (producer.node_id, sink.node_id), ("target",),
        failure_locations={"target": {
            "relative_path": "pkg/api.py", "line": 3,
            "symbol": "pkg.api.public",
        }},
    )
    assert sink.node_id in cuts
    assert producer.node_id in cuts


def test_static_causal_localization_prioritizes_changed_symbol():
    graph = ProgramGraph(repository_root=".", source_hash="source")
    module = graph.add_node(GraphNode.create(
        "module", "pkg.api",
        attributes={"file": "pkg/api.py", "line": 1, "end_line": 50,
                    "qualified_name": "pkg.api"},
    ))
    changed_return = graph.add_node(GraphNode.create(
        "return", "public return",
        attributes={"file": "pkg/api.py", "line": 40, "end_line": 40,
                    "qualified_name": "pkg.api.public"},
    ))
    relation = SimpleNamespace(
        relation_id="changed-relation", qualified_scope="public",
    )
    diff = SimpleNamespace(
        changed_files=("pkg/api.py",), changed_relations=(relation,),
    )

    cuts = _causal_cuts(
        diff, graph, (module.node_id, changed_return.node_id), ("target",),
    )

    assert cuts[0] == changed_return.node_id
