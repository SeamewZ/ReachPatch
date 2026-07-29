from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from reachpatch.binding_graph.models import BindingStatus
from reachpatch.challenge_graph.dicc import (
    compile_executable_challenge_evidence,
    evaluate_dicc,
)
from reachpatch.challenge_graph.models import DICCStatus
from reachpatch.execution.models import (
    CheckClassification,
    CheckComparison,
    CheckExecution,
    CheckRole,
    CheckStatus,
    ExecutableCheck,
    classify_check_pair,
)
from reachpatch.reach_avoid.gates import in_target_set
from reachpatch.reach_avoid.metrics import (
    RevisionEvidence,
    progress_vector_from_comparisons,
    should_commit,
)
from reachpatch.reach_avoid.transition import decide_execution_transition
from reachpatch.repair.counterexamples import counterexample_from_check_comparison
from reachpatch.models.controller import ReachAvoidState
from reachpatch.models.enums import Decision
from reachpatch.program_graph import (
    Deadline,
    GraphBudget,
    build_active_program_slice,
    build_repository_index,
    prioritize_target_repair_seeds,
    recover_causal_slice,
    recover_repair_slice_seeds,
)


def _execution(
    check_id: str,
    status: CheckStatus,
    tree_hash: str,
    *,
    signature: str | None = None,
    stable: bool = True,
) -> CheckExecution:
    return CheckExecution(
        execution_id=f"execution-{check_id}-{tree_hash}-{status.value}",
        check_id=check_id,
        tree_hash=tree_hash,
        status=status,
        return_code=0 if status == CheckStatus.PASS else 1,
        stdout="",
        stderr="failure" if status != CheckStatus.PASS else "",
        duration_seconds=0.01,
        stable=stable,
        failure_signature=signature,
        first_project_frame=None,
    )


def _check(check_id: str, role: CheckRole) -> ExecutableCheck:
    return ExecutableCheck(
        check_id=check_id,
        role=role,
        authority="PUBLIC",
        command=("python", "check.py"),
        cwd=".",
        environment={},
        timeout_seconds=10.0,
        source_evidence_ids=("public",),
        target_requirement_ids=(),
        temporary_artifact_paths=(),
        selector="check.py",
    )


def test_stable_target_frame_is_compiled_into_l0_causal_slice(tmp_path):
    repository = tmp_path / "repo"
    module = repository / "pkg" / "fields.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "class FilePathField:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n"
        "        list(path)\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    check = _check("target", CheckRole.TARGET)
    execution = replace(
        _execution(check.check_id, CheckStatus.FAIL, "base"),
        first_project_frame={
            "relative_path": "pkg/fields.py",
            "line": 4,
            "symbol": "__init__",
        },
    )
    recovery = SimpleNamespace(
        targets=(check,), baseline_executions=(execution,),
    )
    seeds = prioritize_target_repair_seeds(
        recover_repair_slice_seeds("unrelated issue words", (), index),
        recovery,
        index,
    )
    budget = GraphBudget.from_limits(
        seconds=10, max_nodes=1000, max_edges=3000,
        max_files=1, max_functions=1, max_rss_mib=512,
        max_protocol_candidates_per_operation=4,
    )
    graph = build_active_program_slice(
        repository, index, seeds, previous=None, budget=budget,
    ).graph
    causal = recover_causal_slice(
        execution,
        index,
        graph,
        GraphBudget.from_limits(
            seconds=10, max_nodes=1000, max_edges=3000,
            max_files=1, max_functions=1, max_rss_mib=512,
            max_protocol_candidates_per_operation=4,
        ),
        check,
    )

    assert "pkg/fields.py" in graph.file_index
    assert causal.enclosing_callable == "pkg.fields.FilePathField.__init__"
    assert causal.candidate_cut_node_ids


def test_frame_less_reproduction_keeps_domain_symbol_ahead_of_setup(tmp_path):
    repository = tmp_path / "repo"
    settings = repository / "pkg" / "settings.py"
    models = repository / "pkg" / "models.py"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        "def configure(**options):\n"
        "    return options\n",
        encoding="utf-8",
    )
    models.write_text(
        "class CharField:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        self.args = args\n"
        "    def check(self, value):\n"
        "        return value\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    check = replace(
        _check("target", CheckRole.TARGET),
        source_evidence_ids=(
            "issue-behavior:pkg.settings.configure",
            "issue-behavior:pkg.models.CharField",
        ),
    )
    execution = replace(
        _execution(check.check_id, CheckStatus.FAIL, "base"),
        # A temporary reproduction reports its own assertion, so there is no
        # project traceback frame to seed localization.
        first_project_frame=None,
    )
    recovery = SimpleNamespace(
        targets=(check,), baseline_executions=(execution,),
    )
    seeds = prioritize_target_repair_seeds(
        recover_repair_slice_seeds("unrelated issue words", (), index),
        recovery,
        index,
    )
    budget = GraphBudget.from_limits(
        seconds=10, max_nodes=1000, max_edges=3000,
        max_files=2, max_functions=10, max_rss_mib=512,
        max_protocol_candidates_per_operation=4,
    )
    graph = build_active_program_slice(
        repository, index, seeds, previous=None, budget=budget,
    ).graph
    causal = recover_causal_slice(execution, index, graph, budget, check)

    assert causal.failure_location["relative_path"] == "pkg/models.py"
    assert causal.enclosing_callable == "pkg.models.CharField.check"
    candidate_files = {
        str(graph.nodes[node_id].attributes.get("file", ""))
        for node_id in causal.candidate_cut_node_ids
    }
    assert "pkg/models.py" in candidate_files
    assert (
        str(graph.nodes[causal.candidate_cut_node_ids[0]].attributes.get("file", ""))
        == "pkg/models.py"
    )


def test_empty_execution_sets_cannot_reach_close_dicc_or_make_progress():
    empty_diff = SimpleNamespace(canonical_diff_hash="empty")
    dicc = evaluate_dicc((), (), empty_diff, None, ())
    vector = progress_vector_from_comparisons((), ())
    evidence = RevisionEvidence(vector, safe=True, real_execution_count=0)
    state = SimpleNamespace(
        checkpoint=SimpleNamespace(
            patch=SimpleNamespace(canonical_diff="nonempty"), safe=True,
        ),
        target_recovery=SimpleNamespace(targets=()),
        check_comparisons=(),
        counterexamples=[],
        dicc_certificate=dicc,
    )

    assert not in_target_set(state)
    assert dicc.status == DICCStatus.NOT_EVALUABLE
    assert not vector.meaningful
    assert not should_commit(None, evidence)


def test_same_infrastructure_failure_never_becomes_repair_counterexample():
    check = _check("dependency", CheckRole.TARGET)
    baseline = _execution(
        check.check_id, CheckStatus.INVALID_ENVIRONMENT, "base",
        signature="missing-dependency",
    )
    patched = _execution(
        check.check_id, CheckStatus.INVALID_ENVIRONMENT, "trial",
        signature="missing-dependency",
    )
    comparison = CheckComparison.create(baseline, patched, check.role)

    packet = counterexample_from_check_comparison(
        None, check, comparison, None, transition_id="transition",
    )

    assert classify_check_pair(baseline, patched, check.role) == (
        CheckClassification.SAME_INFRA_FAILURE
    )
    assert comparison.classification == CheckClassification.SAME_INFRA_FAILURE
    assert packet is None


def test_real_target_fix_and_preservation_pass_commit_close_and_reach():
    target = _check("target", CheckRole.TARGET)
    preservation = _check("preservation", CheckRole.PRESERVATION)
    target_base = _execution(
        target.check_id, CheckStatus.FAIL, "base", signature="target-failure",
    )
    preservation_base = _execution(
        preservation.check_id, CheckStatus.PASS, "base",
    )
    previous = (
        CheckComparison.create(target_base, target_base, target.role),
        CheckComparison.create(
            preservation_base, preservation_base, preservation.role,
        ),
    )
    current = (
        CheckComparison.create(
            target_base,
            _execution(target.check_id, CheckStatus.PASS, "trial"),
            target.role,
        ),
        CheckComparison.create(
            preservation_base,
            _execution(preservation.check_id, CheckStatus.PASS, "trial"),
            preservation.role,
        ),
    )
    vector = progress_vector_from_comparisons(previous, current)
    evidence = RevisionEvidence(vector, safe=True, real_execution_count=len(current))
    diff = SimpleNamespace(
        canonical_diff_hash="patched", changed_files=("project/module.py",),
    )
    binding_target = SimpleNamespace(check_id=target.check_id)
    executable_bindings = SimpleNamespace(units=(
        SimpleNamespace(
            check_id=target.check_id, kind=BindingStatus.EXECUTABLE_TARGET,
        ),
        SimpleNamespace(
            check_id=preservation.check_id,
            kind=BindingStatus.EXECUTABLE_PRESERVATION,
        ),
    ))
    impact = SimpleNamespace(
        changed_files=("project/module.py",),
        changed_symbol_names=("project.module.public",),
        uncovered_branch_partition_ids=(),
    )
    challenges = compile_executable_challenge_evidence(
        executable_bindings, current, diff, impact,
        checks=(target, preservation),
        repository_index=SimpleNamespace(
            test_references={"check.py": ("public",)},
        ),
    )
    dicc = evaluate_dicc(
        (binding_target,), current, diff, impact, challenges,
        path_obligation_count=2, active_binding_count=2,
    )
    state = SimpleNamespace(
        checkpoint=SimpleNamespace(
            patch=SimpleNamespace(canonical_diff="diff --git a/x b/x"), safe=True,
        ),
        target_recovery=SimpleNamespace(targets=(target,)),
        check_comparisons=current,
        counterexamples=[],
        dicc_certificate=dicc,
    )

    assert decide_execution_transition(None, evidence) == Decision.COMMIT
    assert dicc.status == DICCStatus.CLOSED
    assert dicc.real_challenge_execution_count == 1
    assert dicc.executed_challenge_ids
    assert in_target_set(state)


def test_target_fix_commits_but_zero_graph_or_execution_evidence_cannot_reach():
    target = _check("target", CheckRole.TARGET)
    baseline = _execution(
        target.check_id, CheckStatus.FAIL, "base", signature="target-failure",
    )
    fixed = CheckComparison.create(
        baseline,
        _execution(target.check_id, CheckStatus.PASS, "trial"),
        target.role,
    )
    vector = progress_vector_from_comparisons((), (fixed,))
    evidence = RevisionEvidence(vector, safe=True, real_execution_count=1)
    dicc = evaluate_dicc(
        (SimpleNamespace(check_id=target.check_id),),
        (fixed,),
        SimpleNamespace(canonical_diff_hash="patched"),
        SimpleNamespace(uncovered_branch_partition_ids=()),
        SimpleNamespace(real_execution_count=0, executed_challenge_ids=()),
        path_obligation_count=0,
        active_binding_count=0,
    )
    state = SimpleNamespace(
        checkpoint=SimpleNamespace(
            patch=SimpleNamespace(canonical_diff="diff --git a/x b/x"), safe=True,
        ),
        target_recovery=SimpleNamespace(targets=(target,)),
        check_comparisons=(fixed,),
        counterexamples=[],
        dicc_certificate=dicc,
    )

    assert decide_execution_transition(None, evidence) == Decision.COMMIT
    assert dicc.status == DICCStatus.NOT_EVALUABLE
    assert not in_target_set(state)


def test_comparisons_alone_never_count_as_challenge_executions():
    target = _check("target", CheckRole.TARGET)
    baseline = _execution(target.check_id, CheckStatus.FAIL, "base")
    fixed = CheckComparison.create(
        baseline, _execution(target.check_id, CheckStatus.PASS, "trial"), target.role,
    )
    dicc = evaluate_dicc(
        (SimpleNamespace(check_id=target.check_id),),
        (fixed,),
        SimpleNamespace(canonical_diff_hash="patched"),
        SimpleNamespace(uncovered_branch_partition_ids=()),
        (fixed,),
        path_obligation_count=1,
        active_binding_count=1,
    )

    assert dicc.real_challenge_execution_count == 0
    assert dicc.status == DICCStatus.NOT_EVALUABLE


def test_state_target_deficit_uses_real_comparisons_not_graph_products():
    target = _check("target", CheckRole.TARGET)
    baseline = _execution(
        target.check_id, CheckStatus.FAIL, "base", signature="target-failure",
    )
    fixed = CheckComparison.create(
        baseline,
        _execution(target.check_id, CheckStatus.PASS, "trial"),
        target.role,
    )
    state = SimpleNamespace(
        target_recovery=SimpleNamespace(targets=(target,)),
        check_comparisons=(fixed,),
        binding_graph=SimpleNamespace(
            units=SimpleNamespace(values=lambda: (_ for _ in ()).throw(
                AssertionError("graph deficit must not be evaluated")
            )),
        ),
    )

    assert ReachAvoidState.target_deficit(state) == 0.0


def test_environment_blocked_improvement_is_kept_uncertified_not_committed():
    target = _check("target", CheckRole.TARGET)
    baseline = _execution(
        target.check_id, CheckStatus.FAIL, "base", signature="old-failure",
    )
    current = CheckComparison.create(
        baseline, _execution(target.check_id, CheckStatus.PASS, "trial"), target.role,
    )
    vector = progress_vector_from_comparisons((), (current,))
    evidence = RevisionEvidence(
        vector, safe=True, real_execution_count=1, environment_blocked=True,
    )

    assert decide_execution_transition(None, evidence) == Decision.KEEP_UNCERTIFIED
