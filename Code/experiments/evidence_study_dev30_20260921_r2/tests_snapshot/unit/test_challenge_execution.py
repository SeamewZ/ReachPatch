from __future__ import annotations

from dataclasses import replace
import sys

from reachpatch.challenge_graph.execute import execute_challenge_round
from reachpatch.execution.paired import clear_execution_hot_cache, execute_paired
from reachpatch.execution.trace import run_trace
from reachpatch.execution.worktree import diff_between
from reachpatch.models.evidence import (
    ExecutableOracle, OutcomeStatus, PairClassification, PairedTraceBundle,
    RunObservation, TraceBundle,
)
from reachpatch.models.graphs import (
    BindingStatus, ChallengeStatus, ExecutableScenario, InputRecipe,
    ProgramGraphDelta, ProgramNode, ProgramNodeKind,
)
from reachpatch.models.reach_avoid import ChallengeSelection
from reachpatch.reach_avoid.challenge_player import select_challenge_batch
from reachpatch.reach_avoid.graph_stack import apply_execution_to_graph_stack
from reachpatch.reach_avoid.persistence import record_locked_passes


def paired(state, classification):
    before_status = (
        OutcomeStatus.PASS
        if classification in {
            PairClassification.PRESERVATION_REGRESSION,
            PairClassification.PASS_PRESERVED,
        }
        else OutcomeStatus.FAIL
    )
    after_status = (
        OutcomeStatus.PASS
        if classification in {
            PairClassification.TARGET_FIXED,
            PairClassification.PASS_PRESERVED,
        }
        else OutcomeStatus.FAIL
    )
    before = TraceBundle(
        "trace-before", "base", ("python",),
        RunObservation(before_status, 0 if before_status is OutcomeStatus.PASS else 1, "before", "", 0),
        ("symbol-calc",), ("calc.py:1", "calc.py:2"),
        executed_line_ids=("calc.py:1", "calc.py:2"), stable_runs=2,
    )
    after = TraceBundle(
        "trace-after", "working", ("python",),
        RunObservation(after_status, 0 if after_status is OutcomeStatus.PASS else 1, "after", "", 0),
        ("symbol-calc",), ("calc.py:1", "calc.py:2"),
        executed_line_ids=("calc.py:1", "calc.py:2"), stable_runs=2,
    )
    return PairedTraceBundle(
        "paired", "check-target", "challenge-target",
        state.graph_stack.patch_hash, before, after, classification,
        "oracle-target", "A", "calc must return 2", 2,
    )


def test_no_confirmed_failure_runs_challenge(state_factory):
    state = state_factory()
    selection = select_challenge_batch(state)
    assert selection.challenge_ids == ("challenge-target",)
    assert state.confirmed_failures == []


def test_challenge_batch_covers_distinct_binding_families_first(state_factory):
    state = state_factory(preservation_status=ChallengeStatus.PENDING)
    target = state.graph_stack.challenge_graph.cells["challenge-target"]
    preservation = state.graph_stack.challenge_graph.cells["challenge-preservation"]
    state.graph_stack.challenge_graph.cells = {
        "challenge-a": replace(target, challenge_id="challenge-a"),
        "challenge-b": replace(target, challenge_id="challenge-b"),
        preservation.challenge_id: preservation,
    }
    selection = select_challenge_batch(state, max_batch=2)
    assert len(selection.challenge_ids) == 2
    assert "challenge-preservation" in selection.challenge_ids
    assert len(set(selection.challenge_ids) & {"challenge-a", "challenge-b"}) == 1


def test_challenge_execution_updates_all_four_graphs(state_factory, monkeypatch):
    state = state_factory()
    before = state.graph_stack.graph_hashes()
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired",
        lambda **kwargs: (paired(state, PairClassification.TARGET_FIXED), False),
    )
    result = execute_challenge_round(
        state, ChallengeSelection(("challenge-target",)),
        state.base_repository, state.working_checkpoint.snapshot_tree,
    )
    result.updated_graph_stack.validate()
    after = result.updated_graph_stack.graph_hashes()
    assert all(after[name] != before[name] for name in after)
    assert result.updated_graph_stack.binding_graph.units["binding-target"].status is BindingStatus.TARGET_PASSING
    assert result.updated_graph_stack.challenge_graph.cells["challenge-target"].terminal_status is ChallengeStatus.PASS


def test_target_pass_preserved_closes_obligation_without_claiming_fixed(
    state_factory, monkeypatch,
):
    state = state_factory()
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired",
        lambda **kwargs: (paired(state, PairClassification.PASS_PRESERVED), False),
    )

    result = execute_challenge_round(
        state, ChallengeSelection(("challenge-target",)),
        state.base_repository, state.working_checkpoint.snapshot_tree,
    )

    binding = result.updated_graph_stack.binding_graph.units["binding-target"]
    cell = result.updated_graph_stack.challenge_graph.cells["challenge-target"]
    assert binding.status is BindingStatus.EXECUTION_CONFIRMED
    assert cell.terminal_status is ChallengeStatus.PASS
    assert cell.patched_outcome is OutcomeStatus.PASS
    assert result.executions[0].classification is PairClassification.PASS_PRESERVED
    assert not result.counterexamples


def test_execution_updates_every_equivalent_challenge_cell(
    state_factory, monkeypatch,
):
    state = state_factory()
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    duplicate = replace(
        cell,
        challenge_id="challenge-target-equivalent",
        input_recipe=replace(
            cell.input_recipe,
            recipe_id="recipe-target-equivalent",
        ),
    )
    state.graph_stack.challenge_graph.cells[duplicate.challenge_id] = duplicate
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired",
        lambda **kwargs: (paired(state, PairClassification.PASS_PRESERVED), False),
    )

    result = execute_challenge_round(
        state, ChallengeSelection((cell.challenge_id,)),
        state.base_repository, state.working_checkpoint.snapshot_tree,
    )

    equivalent = result.updated_graph_stack.challenge_graph.cells[
        duplicate.challenge_id
    ]
    assert equivalent.terminal_status is ChallengeStatus.PASS
    assert equivalent.stability_runs == 2
    assert equivalent.trace_bundle_id == "paired"
    assert duplicate.challenge_id not in result.frontiers


def test_locking_records_only_the_concrete_executed_check(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    state.graph_stack.challenge_graph.cells[cell.challenge_id] = replace(
        cell,
        input_recipe=replace(
            cell.input_recipe,
            source_check_id="check-target",
        ),
    )
    binding = state.graph_stack.binding_graph.units[cell.binding_id]
    state.graph_stack.binding_graph.units[binding.binding_id] = replace(
        binding,
        target_check_ids=("check-target", "check-never-executed"),
    )
    state.graph_stack.challenge_graph.binding_hash = (
        state.graph_stack.binding_graph.graph_hash()
    )
    execution = paired(state, PairClassification.TARGET_FIXED)

    record_locked_passes(state, state.graph_stack, (execution,))

    assert state.locked_checks.target_ids == {"check-target"}
    assert "check-never-executed" not in state.locked_checks.target_ids


def test_challenge_failure_becomes_confirmed_failure(state_factory, monkeypatch):
    state = state_factory()
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired",
        lambda **kwargs: (paired(state, PairClassification.TARGET_STILL_FAILING), False),
    )
    result = execute_challenge_round(
        state, ChallengeSelection(("challenge-target",)),
        state.base_repository, state.working_checkpoint.snapshot_tree,
    )
    assert len(result.counterexamples) == len(result.confirmed_failures) == 1
    packet = result.counterexamples[0]
    assert packet.reproduction_command
    assert packet.failure_signature
    assert packet.binding_id == "binding-target"
    assert packet.expected_observation is not None
    assert packet.trial_observation == packet.patched_observation
    assert packet.comparator


def test_changed_hunk_import_regression_becomes_counterexample(
    state_factory, monkeypatch,
):
    state = state_factory(preservation_status=ChallengeStatus.PENDING)
    cell = state.graph_stack.challenge_graph.cells["challenge-preservation"]
    actual_hunk_id = diff_between(
        state.base_repository, state.working_checkpoint.snapshot_tree,
    ).hunks[0].hunk_id
    cell = replace(cell, changed_hunk_ids=(actual_hunk_id,))
    state.graph_stack.challenge_graph.cells[cell.challenge_id] = cell
    execution = replace(
        paired(state, PairClassification.PRESERVATION_REGRESSION),
        challenge_id=cell.challenge_id,
        check_id="check-preservation",
        patched=replace(
            paired(state, PairClassification.PRESERVATION_REGRESSION).patched,
            first_project_frame="calc.py:1",
            executed_symbol_ids=("module-import",),
            executed_path_ids=("calc.py:1",),
            executed_line_ids=("calc.py:1",),
        ),
    )
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired",
        lambda **kwargs: (execution, False),
    )
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute._executed_node_ids",
        lambda graph, trace: ("unbound-import-node",),
    )
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.materialize_execution_path_class",
        lambda graph, trace, anchors: (graph, None),
    )

    result = execute_challenge_round(
        state, ChallengeSelection((cell.challenge_id,)),
        state.base_repository, state.working_checkpoint.snapshot_tree,
    )

    assert len(result.counterexamples) == len(result.confirmed_failures) == 1
    assert result.counterexamples[0].challenge_id == cell.challenge_id
    assert result.updated_graph_stack.challenge_graph.cells[
        cell.challenge_id
    ].terminal_status is ChallengeStatus.FAIL


def test_real_trace_rebinds_stale_static_path_before_confirming_failure(
    state_factory, monkeypatch,
):
    state = state_factory()
    program = state.graph_stack.program_graph
    target_class = ProgramNode(
        "symbol-target-class", ProgramNodeKind.CLASS, "calc.py", "Calculator",
        4, 10, True,
    )
    executed_method = ProgramNode(
        "symbol-executed-method", ProgramNodeKind.METHOD, "calc.py",
        "Calculator.run", 5, 8, True,
    )
    program.nodes = {
        **program.nodes,
        target_class.node_id: target_class,
        executed_method.node_id: executed_method,
    }
    unit = state.graph_stack.binding_graph.units["binding-target"]
    state.graph_stack.binding_graph.units[unit.binding_id] = replace(
        unit,
        program_symbol_ids=unit.program_symbol_ids + (target_class.node_id,),
    )
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    state.graph_stack.challenge_graph.cells[cell.challenge_id] = replace(
        cell,
        input_recipe=replace(
            cell.input_recipe,
            trace_symbols=cell.input_recipe.trace_symbols + (target_class.node_id,),
        ),
    )
    state.graph_stack.binding_graph.program_hash = program.graph_hash()
    state.graph_stack.challenge_graph.binding_hash = (
        state.graph_stack.binding_graph.graph_hash()
    )
    state.graph_stack.validate()

    execution = paired(state, PairClassification.TARGET_STILL_FAILING)
    actual_trace = replace(
        execution.patched,
        executed_symbol_ids=("run",),
        executed_path_ids=("calc.py:5", "calc.py:8"),
        executed_line_ids=("calc.py:5", "calc.py:8"),
    )
    execution = replace(execution, patched=actual_trace)
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired",
        lambda **kwargs: (execution, False),
    )
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.update_program_graph_after_diff",
        lambda *args, **kwargs: ProgramGraphDelta(
            program, (), (), 0, 0, 0, 0.0,
        ),
    )

    result = execute_challenge_round(
        state, ChallengeSelection(("challenge-target",)),
        state.base_repository, state.working_checkpoint.snapshot_tree,
    )

    updated = result.updated_graph_stack
    rebound = updated.binding_graph.units["binding-target"]
    rebound_cell = updated.challenge_graph.cells["challenge-target"]
    path = updated.program_graph.path_classes[rebound.path_class_id]
    assert rebound.path_class_id.startswith("execution-path-class-")
    assert rebound_cell.path_class_id == rebound.path_class_id
    assert path.entrypoint == "Calculator.run"
    assert executed_method.node_id in path.node_ids
    assert len(rebound.program_symbol_ids) <= 128
    assert len(result.counterexamples) == len(result.confirmed_failures) == 1
    assert result.counterexamples[0].executed_path_ids == (path.path_class_id,)


def test_batch_counterexamples_keep_their_executed_challenge_ids(state_factory, monkeypatch):
    state = state_factory(preservation_status=ChallengeStatus.PENDING)
    target = state.graph_stack.challenge_graph.cells["challenge-target"]
    preservation = state.graph_stack.challenge_graph.cells["challenge-preservation"]

    def execute(**kwargs):
        selected = kwargs["challenge_id"]
        classification = (
            PairClassification.TARGET_STILL_FAILING
            if selected == target.challenge_id
            else PairClassification.PRESERVATION_REGRESSION
        )
        result = paired(state, classification)
        return replace(
            result,
            challenge_id=selected,
            check_id=(
                "check-target" if selected == target.challenge_id
                else "check-preservation"
            ),
        ), False

    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired", execute,
    )
    result = execute_challenge_round(
        state,
        ChallengeSelection((target.challenge_id, preservation.challenge_id)),
        state.base_repository,
        state.working_checkpoint.snapshot_tree,
    )
    assert {item.challenge_id for item in result.counterexamples} == {
        target.challenge_id, preservation.challenge_id,
    }
    assert {item.challenge_id for item in result.confirmed_failures} == {
        target.challenge_id, preservation.challenge_id,
    }


def test_impact_consumer_uses_stable_baseline_preservation_oracle(
    state_factory, monkeypatch,
):
    state = state_factory(preservation_status=ChallengeStatus.PENDING)
    preservation = state.graph_stack.challenge_graph.cells["challenge-preservation"]
    impact = replace(
        preservation,
        challenge_id="challenge-impact",
        kind="PRESERVATION",
        input_recipe=replace(
            preservation.input_recipe,
            recipe_id="recipe-impact",
            kind="RETURN_CONSUMER",
        ),
        oracle=replace(
            preservation.oracle,
            oracle_id="oracle-provisional-impact",
            authority="PROVISIONAL",
            executable=False,
        ),
        authority="PROVISIONAL",
        origin="IMPACT_CONE",
    )
    state.graph_stack.challenge_graph.cells[impact.challenge_id] = impact
    unit = state.graph_stack.binding_graph.units[impact.binding_id]
    state.graph_stack.binding_graph.units[impact.binding_id] = replace(
        unit,
        challenge_ids=tuple(dict.fromkeys(unit.challenge_ids + (impact.challenge_id,))),
    )
    state.graph_stack.challenge_graph.binding_hash = (
        state.graph_stack.binding_graph.graph_hash()
    )

    def execute(**kwargs):
        result = paired(state, PairClassification.PRESERVATION_REGRESSION)
        return replace(
            result,
            challenge_id=impact.challenge_id,
            check_id="check-preservation",
            oracle_id="oracle-stable-baseline",
            oracle_authority="C",
            expected_relation="patched observation preserves stable baseline observation",
        ), False

    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired", execute,
    )
    result = execute_challenge_round(
        state, ChallengeSelection((impact.challenge_id,)),
        state.base_repository, state.working_checkpoint.snapshot_tree,
    )
    assert result.executions[0].classification is PairClassification.PRESERVATION_REGRESSION
    assert result.updated_graph_stack.challenge_graph.cells[
        impact.challenge_id
    ].authority == "C"
    assert result.counterexamples[0].challenge_id == impact.challenge_id


def test_challenge_round_does_not_consume_revision(state_factory, monkeypatch):
    state = state_factory()
    monkeypatch.setattr(
        "reachpatch.challenge_graph.execute.execute_paired",
        lambda **kwargs: (paired(state, PairClassification.TARGET_FIXED), False),
    )
    before = state.revision_count
    execute_challenge_round(
        state, ChallengeSelection(("challenge-target",)),
        state.base_repository, state.working_checkpoint.snapshot_tree,
    )
    assert state.revision_count == before


def test_unknown_execution_marks_oracle_unavailable(state_factory):
    state = state_factory()
    execution = paired(state, PairClassification.TARGET_STILL_FAILING)
    execution = replace(
        execution,
        classification=PairClassification.UNKNOWN,
        oracle_authority="PROVISIONAL",
    )
    updated = apply_execution_to_graph_stack(state.graph_stack, (execution,), ())
    assert updated.binding_graph.units["binding-target"].status is BindingStatus.ORACLE_UNAVAILABLE


def test_blocked_execution_marks_environment_blocked(state_factory):
    state = state_factory()
    execution = paired(state, PairClassification.TARGET_STILL_FAILING)
    blocked = replace(
        execution.patched,
        observation=replace(
            execution.patched.observation,
            status=OutcomeStatus.BLOCKED,
            exception="TIMEOUT",
        ),
    )
    execution = replace(
        execution,
        patched=blocked,
        classification=PairClassification.UNKNOWN,
    )
    updated = apply_execution_to_graph_stack(state.graph_stack, (execution,), ())
    assert updated.binding_graph.units["binding-target"].status is BindingStatus.ENVIRONMENT_BLOCKED


def test_stale_execution_does_not_update_current_four_graphs(state_factory):
    state = state_factory()
    execution = replace(
        paired(state, PairClassification.TARGET_FIXED),
        patch_hash="retired-patch",
    )
    updated = apply_execution_to_graph_stack(state.graph_stack, (execution,), ())
    assert updated.graph_hashes() == state.graph_stack.graph_hashes()
    assert updated.binding_graph.units["binding-target"].status is BindingStatus.STATIC_ACTIONABLE


def test_paired_execution_uses_oracle_observation(tmp_path):
    baseline = tmp_path / "baseline"
    patched = tmp_path / "patched"
    baseline.mkdir()
    patched.mkdir()
    (baseline / "check.py").write_text("print(1)\n", encoding="utf-8")
    (patched / "check.py").write_text("print(2)\n", encoding="utf-8")
    recipe = InputRecipe(
        "recipe", "PUBLIC_REPLAY", None, (), ("python", "check.py"),
    )
    scenario = ExecutableScenario("scenario", recipe.command, ".", (), 10)
    oracle = ExecutableOracle("oracle", "A", "result equals 2", 2, True)
    result, _ = execute_paired(
        baseline_tree=baseline,
        patched_tree=patched,
        recipe=recipe,
        scenario=scenario,
        oracle=oracle,
        check_id="check",
        challenge_id="challenge-oracle",
        patch_hash="patch",
        role="TARGET",
    )
    assert result.classification is PairClassification.TARGET_FIXED
    assert result.baseline.observation.status is OutcomeStatus.FAIL
    assert result.patched.observation.status is OutcomeStatus.PASS


def test_stability_uses_only_oracle_observed_fields(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline-stability"
    patched = tmp_path / "patched-stability"
    baseline.mkdir()
    patched.mkdir()
    calls = 0

    def noisy_trace(tree, command, **kwargs):
        nonlocal calls
        calls += 1
        return TraceBundle(
            f"trace-{calls}", str(tree), command,
            RunObservation(OutcomeStatus.FAIL, 1, "", f"unordered warning {calls}", 0),
            (), (),
        )

    monkeypatch.setattr("reachpatch.execution.paired.run_trace", noisy_trace)
    recipe = InputRecipe("recipe-stability", "ISSUE_WITNESS", None, (), ("python", "-c", "raise SystemExit(1)"))
    scenario = ExecutableScenario("scenario-stability", recipe.command, ".", (), 10)
    oracle = ExecutableOracle(
        "oracle-stability", "B", "command must succeed", {"exit_code": 0}, True,
    )
    result, _ = execute_paired(
        baseline_tree=baseline,
        patched_tree=patched,
        recipe=recipe,
        scenario=scenario,
        oracle=oracle,
        check_id="check-stability",
        challenge_id="challenge-stability",
        patch_hash="patch-stability",
        role="TARGET",
    )
    assert result.stable_runs == 2
    assert result.classification is PairClassification.TARGET_STILL_FAILING


def test_trace_profiler_ignores_non_string_code_paths(monkeypatch):
    import sys
    import threading
    from types import SimpleNamespace

    from reachpatch.execution.trace import _SITECUSTOMIZE

    namespace = {"__name__": "reachpatch_trace_test"}
    monkeypatch.delenv("REACHPATCH_TRACE_OUTPUT", raising=False)
    exec(_SITECUSTOMIZE, namespace)
    sys.setprofile(None)
    threading.setprofile(None)
    frame = SimpleNamespace(
        f_code=SimpleNamespace(co_filename=None, co_name="synthetic"),
        f_lineno=1,
    )

    namespace["_profile"](frame, "call", None)
    namespace["_root"] = None
    namespace["os"] = None
    outside = SimpleNamespace(
        f_code=SimpleNamespace(co_filename="/outside/module.py", co_name="synthetic"),
        f_lineno=1,
    )
    namespace["_profile"](outside, "return", None)


def test_executable_oracle_requires_exit_and_displayed_stdout(tmp_path):
    from reachpatch.oracle.observe import observe_oracle

    oracle = ExecutableOracle(
        "oracle-output", "B", "issue witness output",
        {"exit_code": 0, "stdout": "expected\n"}, True,
    )
    wrong = RunObservation(OutcomeStatus.PASS, 0, "wrong\n", "", 0)
    right = RunObservation(OutcomeStatus.PASS, 0, "expected\n", "", 0)
    assert observe_oracle(oracle, wrong) is OutcomeStatus.FAIL
    assert observe_oracle(oracle, right) is OutcomeStatus.PASS


def test_paired_preservation_uses_stable_baseline_relation(tmp_path):
    baseline = tmp_path / "baseline-preservation"
    patched = tmp_path / "patched-preservation"
    baseline.mkdir()
    patched.mkdir()
    for tree in (baseline, patched):
        (tree / "check.py").write_text("print('same')\n", encoding="utf-8")
    recipe = InputRecipe(
        "recipe-preservation", "PUBLIC_REPLAY", None, (),
        ("python", "check.py"),
    )
    scenario = ExecutableScenario(
        "scenario-preservation", recipe.command, ".", (), 10,
    )
    expected = RunObservation(OutcomeStatus.PASS, 0, "same\n", "", 99.0)
    oracle = ExecutableOracle(
        "oracle-preservation", "C",
        "patched observation preserves stable baseline observation",
        expected, True,
    )
    result, _ = execute_paired(
        baseline_tree=baseline,
        patched_tree=patched,
        recipe=recipe,
        scenario=scenario,
        oracle=oracle,
        check_id="check-preservation",
        challenge_id="challenge-preservation-oracle",
        patch_hash="patch-preservation",
        role="PRESERVATION",
    )
    assert result.classification is PairClassification.PASS_PRESERVED


def test_provisional_preservation_derives_stable_baseline_regression(tmp_path):
    baseline = tmp_path / "baseline-derived-preservation"
    patched = tmp_path / "patched-derived-preservation"
    baseline.mkdir()
    patched.mkdir()
    (baseline / "check.py").write_text("print('before')\n", encoding="utf-8")
    (patched / "check.py").write_text("print('after')\n", encoding="utf-8")
    recipe = InputRecipe(
        "recipe-derived-preservation", "STATE_READER", None, (),
        ("python", "check.py"),
    )
    scenario = ExecutableScenario(
        "scenario-derived-preservation", recipe.command, ".", (), 10,
    )
    oracle = ExecutableOracle(
        "oracle-derived-preservation", "PROVISIONAL",
        "explore an impacted state consumer", None, False,
    )

    result, _ = execute_paired(
        baseline_tree=baseline,
        patched_tree=patched,
        recipe=recipe,
        scenario=scenario,
        oracle=oracle,
        check_id="check-derived-preservation",
        challenge_id="challenge-derived-preservation",
        patch_hash="patch-derived-preservation",
        role="PRESERVATION",
    )

    assert result.oracle_authority == "C"
    assert result.stable_runs == 2
    assert result.classification is PairClassification.PRESERVATION_REGRESSION


def test_paired_execution_cache_is_persisted_per_case(tmp_path):
    baseline = tmp_path / "baseline-disk"
    patched = tmp_path / "patched-disk"
    cache = tmp_path / "run" / "execution_cache"
    baseline.mkdir()
    patched.mkdir()
    (baseline / "check.py").write_text("print(1)\n", encoding="utf-8")
    (patched / "check.py").write_text("print(2)\n", encoding="utf-8")
    recipe = InputRecipe(
        "recipe-disk", "PUBLIC_REPLAY", None, (), ("python", "check.py"),
    )
    scenario = ExecutableScenario("scenario-disk", recipe.command, ".", (), 10)
    oracle = ExecutableOracle("oracle-disk", "A", "must equal 2", 2, True)
    arguments = dict(
        baseline_tree=baseline, patched_tree=patched, recipe=recipe,
        scenario=scenario, oracle=oracle, check_id="check-disk",
        challenge_id="challenge-disk", patch_hash="patch-disk",
        role="TARGET", cache_dir=cache,
    )
    first, first_hit = execute_paired(**arguments)
    clear_execution_hot_cache()
    second, second_hit = execute_paired(**arguments)
    assert not first_hit
    assert second_hit
    assert second == first
    assert len(tuple(cache.glob("*.json"))) == 1


def test_dynamic_execution_does_not_relabel_static_edges(state_factory):
    from reachpatch.models.base import stable_id
    from reachpatch.models.graphs import ProgramEdge, ProgramEdgeKind

    state = state_factory()
    node = state.graph_stack.program_graph.nodes["symbol-calc"]
    edge_id = stable_id("contains", node.node_id)
    static = ProgramEdge(
        edge_id, node.node_id, node.node_id,
        ProgramEdgeKind.CONTAINS, False,
    )
    state.graph_stack.program_graph.edges[edge_id] = static
    state.graph_stack.binding_graph.program_hash = (
        state.graph_stack.program_graph.graph_hash()
    )
    state.graph_stack.challenge_graph.binding_hash = (
        state.graph_stack.binding_graph.graph_hash()
    )
    updated = apply_execution_to_graph_stack(
        state.graph_stack,
        (paired(state, PairClassification.TARGET_FIXED),),
        (),
    )
    assert updated.program_graph.edges[edge_id] == static
    assert any(
        edge.kind is ProgramEdgeKind.EXECUTED_CALL and edge.dynamic_confirmed
        for edge in updated.program_graph.edges.values()
    )


def test_equivalent_pending_cell_does_not_clear_requirement_pass(state_factory):
    state = state_factory()
    target = state.graph_stack.challenge_graph.cells["challenge-target"]
    duplicate = replace(
        target,
        challenge_id="challenge-equivalent-pending",
        input_recipe=replace(
            target.input_recipe, recipe_id="recipe-equivalent-pending",
        ),
    )
    state.graph_stack.challenge_graph.cells[duplicate.challenge_id] = duplicate
    execution = paired(state, PairClassification.TARGET_FIXED)
    updated = apply_execution_to_graph_stack(
        state.graph_stack, (execution,), (),
    )
    assert updated.requirement_graph.leaves["req-target"].status is OutcomeStatus.PASS


def test_trace_retains_changed_file_after_bounded_noise(tmp_path):
    (tmp_path / "target.py").write_text(
        "def target():\n    return 7\n", encoding="utf-8",
    )
    (tmp_path / "noise.py").write_text(
        "def tick():\n    return None\n\n"
        "def flood():\n    for _ in range(6000):\n        tick()\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text(
        "from target import target\n"
        "from noise import flood\n"
        "value = target()\n"
        "flood()\n"
        "assert value == 7\n",
        encoding="utf-8",
    )

    trace = run_trace(
        tmp_path, (sys.executable, "main.py"), timeout_seconds=10,
        overlay_paths=("target.py",),
    )

    assert trace.observation.status is OutcomeStatus.PASS
    assert "target" in trace.executed_symbol_ids
    assert any(item.startswith("target.py:") for item in trace.executed_line_ids)
    assert len(trace.executed_line_ids) <= 4096 + 2048 + 1
