from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from reachpatch.binding_graph import build_active_binding_graph
from reachpatch.challenge_graph.materialize import (
    execute_challenges, materialize_active_challenges,
)
from reachpatch.challenge_graph.models import ChallengeCell, ChallengeGraph
from reachpatch.evidence import build_hypothesis_set, build_semantic_graph
from reachpatch.evidence.hypotheses import enumerate_assignments
from reachpatch.execution.reconcile import reconcile_actual_diff
from reachpatch.execution.mechanical import (
    run_mechanical_checks, run_public_checks_paired,
)
from reachpatch.models.core import Instance
from reachpatch.models.base import content_hash
from reachpatch.models.enums import (
    ChallengeTerminalStatus, Decision, OutcomeStatus,
)
from reachpatch.models.isolation import GenerationInstance, assert_generation_payload
from reachpatch.oracle.discriminator import HypothesisDiscriminator
from reachpatch.program_graph import (
    Deadline, GraphBudget, build_active_program_slice, build_repository_index,
    recover_repair_slice_seeds, update_active_program_slice,
    update_repository_index,
)
from reachpatch.program_graph.builder import PythonProgramGraphBuilder
from reachpatch.program_graph.slice import ContextRequest, RepairSliceSeed
from reachpatch.reach_avoid.controller import ReachPatchConfig, ReachPatchController
from reachpatch.reach_avoid.transition import evaluate_patch_revision
from reachpatch.repair.deepseek_agent import (
    ActionConversionStatus, GeneratorRevision, PersistentDeepSeekAgent,
    convert_revision_action,
)
from reachpatch.repair.tools import ProposedEdit, RepairToolExecutor
from reachpatch.requirement_graph import (
    compile_assignment_overlay, compile_requirement_core,
    compile_requirement_paths, promote_domains_from_diff,
    refresh_requirement_paths,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "simple_repo"


def _budget(*, files: int = 8, functions: int = 16) -> GraphBudget:
    return GraphBudget.from_limits(
        seconds=10.0, max_nodes=5_000, max_edges=15_000,
        max_files=files, max_functions=functions, max_rss_mib=2_048,
        max_protocol_candidates_per_operation=4,
    )


def test_repository_index_keeps_unrelated_expression_growth_out_of_precise_graph(tmp_path):
    root = tmp_path / "repo"
    package = root / "pkg"
    package.mkdir(parents=True)
    (package / "target.py").write_text(
        "def public(value):\n    if not value:\n        return []\n    return list(value)\n",
        encoding="utf-8",
    )
    for number in range(40):
        expressions = "\n".join(f"VALUE_{index} = {index} + {number}" for index in range(200))
        (package / f"unrelated_{number}.py").write_text(expressions + "\n", encoding="utf-8")
    index = build_repository_index(root, max_files=100, deadline=Deadline.after(10))
    seeds = recover_repair_slice_seeds(
        "pkg.target.public must return [] for empty values", (), index,
    )
    result = build_active_program_slice(
        root, index, seeds, previous=None, budget=_budget(files=4, functions=4),
    )

    assert index.scanned_files == 41
    assert "pkg.unrelated_39" in index.modules
    assert "pkg/target.py" in result.analyzed_files
    assert all("unrelated" not in path for path in result.analyzed_files)
    assert len(result.graph.nodes) < 200
    assert result.peak_rss_mib < 2_048
    assert result.elapsed_seconds < 10


def test_precise_builder_does_not_walk_repository_when_include_files_are_given(
    tmp_path, monkeypatch,
):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "target.py").write_text(
        "def target(value):\n    return value\n", encoding="utf-8"
    )
    (root / "pkg" / "unrelated.py").write_text(
        "def unrelated(value):\n    return value\n", encoding="utf-8"
    )

    def fail_full_walk(*args, **kwargs):
        raise AssertionError("precise include build walked the repository")

    monkeypatch.setattr("reachpatch.program_graph.builder._iter_python_files", fail_full_walk)
    graph = PythonProgramGraphBuilder(
        root, include_files=("pkg/target.py",), budget=_budget(files=1, functions=4)
    ).build()
    assert graph.build_stats["precise_file_count"] == 1
    assert set(graph.file_index) <= {"pkg/target.py"}
    assert len(graph.cfgs) == 1


def test_semantic_ambiguity_retains_hypotheses_and_plans_discriminator():
    semantic = build_semantic_graph(
        "The result could preserve identity? The result could create a copy?"
    ).graph
    decisions, _ = enumerate_assignments(semantic)
    hypotheses = build_hypothesis_set(semantic)
    probes = HypothesisDiscriminator().plan(decisions, hypotheses.alternatives)

    assert len(hypotheses.alternatives) >= 2
    assert hypotheses.preferred_assignment_id in hypotheses.active_assignment_ids
    assert hypotheses.unresolved_decision_ids
    assert set(hypotheses.common_hard_node_ids) <= set(
        hypotheses.alternatives[0].common_hard_node_ids
    )
    assert {probe.decision_id for probe in probes} == set(
        hypotheses.unresolved_decision_ids
    )


def test_non_executable_oracles_are_aggregated_without_unknown_cells():
    program_root = FIXTURE.resolve()
    index = build_repository_index(
        program_root, max_files=20, deadline=Deadline.after(10)
    )
    seeds = recover_repair_slice_seeds(
        "For every x, pkg.api.public(x) must return a normalized value.", (), index
    )
    program = build_active_program_slice(
        program_root, index, seeds, previous=None, budget=_budget(),
    ).graph
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return a normalized value."
    ).graph
    assignment = build_hypothesis_set(semantic).alternatives[0]
    requirement = compile_assignment_overlay(semantic, assignment)
    compile_requirement_paths(requirement, program)
    binding = build_active_binding_graph(
        requirement, program, previous=None,
        affected_leaf_ids=set(requirement.leaves),
        affected_path_ids=set(requirement.path_obligations),
        max_target_units=20, max_preservation_units=20,
        deadline=time.monotonic() + 10,
    )
    challenges = materialize_active_challenges(
        requirement, program, binding, actual_diff=None,
        previous_outcomes={}, max_challenges=40,
        deadline=time.monotonic() + 10,
    )

    assert binding.oracle_frontiers
    assert all(unit.status == "DEFERRED" for unit in binding.units.values())
    assert challenges.cells == {}


def test_active_challenges_execute_in_bounded_parallel_workers():
    graph = ChallengeGraph(
        requirement_graph_hash="requirement",
        program_graph_hash="program",
        binding_graph_hash="binding",
    )
    for number in range(4):
        recipe = SimpleNamespace(recipe_id=f"recipe-{number}")
        scenario = SimpleNamespace(scenario_id=f"scenario-{number}")
        graph.add_cell(
            ChallengeCell(
                challenge_id=f"challenge-{number}",
                binding_unit_id=f"binding-{number}",
                quantified_partition={"partition_id": f"partition-{number}"},
                path_class_id=f"path-{number}",
                trigger_recipe_id=recipe.recipe_id,
                input_constraints=(), observation_contract_id="observe",
                oracle_id="oracle", baseline_outcome=None,
                patched_outcome=None, diff_dependency={},
                stability_status="PENDING",
                terminal_status=ChallengeTerminalStatus.PENDING,
                evidence=(), scenario_id=scenario.scenario_id,
                operator_id="test", changed_dimension="test",
                origin="GRAPH", hard=False, graph_hashes={},
            ),
            recipe=recipe,
            scenario=scenario,
        )

    class ConcurrentExecutor:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.lock = threading.Lock()

        def execute_paired(self, recipe, base, patch, scenario):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
                trace = SimpleNamespace(
                    stable_status=OutcomeStatus.PASS,
                    stability_status="STABLE",
                    runs=(),
                )
            return SimpleNamespace(
                paired_bundle_id=f"bundle-{recipe.recipe_id}",
                base_bundle=trace, patch_bundle=trace,
                status=OutcomeStatus.PASS,
                stability_status="STABLE",
            )

    executor = ConcurrentExecutor()
    result = execute_challenges(
        graph, executor, "/base", "/patch", max_workers=2,
    )

    assert executor.peak == 2
    assert result.real_execution_count == 4
    assert result.executed_challenge_ids == tuple(sorted(graph.cells))
    assert all(
        cell.terminal_status == ChallengeTerminalStatus.PASS
        for cell in graph.cells.values()
    )


def _revision(mechanism: str, edits, requests=()) -> GeneratorRevision:
    return GeneratorRevision(
        revision_id=f"revision-{mechanism}-{len(edits)}", mechanism=mechanism,
        edits=tuple(edits), summary="test revision",
        context_requests=tuple(requests), requested_public_checks=(),
        tool_turns=1, status="PROPOSED",
    )


def test_deepseek_action_conversion_reports_precise_statuses(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__"))
    index = build_repository_index(root, max_files=20, deadline=Deadline.after(10))
    seed = RepairSliceSeed(
        symbol_names=("pkg.api.public",), file_paths=("pkg/api.py",),
        test_paths=(), traceback_locations=(), issue_tokens=("public",),
        diff_locations=(), trace_locations=(), requested_context=(),
    )
    program = build_active_program_slice(
        root, index, seed, previous=None, budget=_budget(files=2),
    ).graph
    state = SimpleNamespace(
        checkpoint=SimpleNamespace(snapshot_tree=str(root)), program_graph=program,
    )
    single = ProposedEdit("pkg/api.py", 40, 40, "    return normalize(value)", "    return []")
    second = ProposedEdit("pkg/api.py", 45, 45, "        return public(value)", "        return []")
    outside = ProposedEdit(
        "pkg/__init__.py", 1, 1,
        "from .api import Box, dispatch, public",
        "from .api import Box, dispatch, public, safe_public",
    )

    assert convert_revision_action(
        state, _revision("initial_issue_repair", (single,))
    ).status == ActionConversionStatus.ACCEPTED
    assert convert_revision_action(
        state, _revision("cross_function_propagation", (single, second))
    ).status == ActionConversionStatus.ACCEPTED
    assert convert_revision_action(
        state,
        _revision(
            "cross_function_propagation", (outside,),
            (ContextRequest(file_paths=("pkg/__init__.py",)),),
        ),
    ).status == ActionConversionStatus.NEEDS_SLICE_EXPANSION
    assert convert_revision_action(
        state, _revision("invented_operator", (single,))
    ).status == ActionConversionStatus.INVALID_OPERATOR
    normalized = convert_revision_action(
        state, _revision("Add required import", (single,))
    )
    assert normalized.status == ActionConversionStatus.ACCEPTED
    assert normalized.revision.mechanism == "cross_function_propagation"
    forbidden = ProposedEdit(
        "tests/test_api.py", 1, 1,
        "from pkg.api import Box, dispatch, public", "# forbidden",
    )
    assert convert_revision_action(
        state, _revision("initial_issue_repair", (forbidden,))
    ).status == ActionConversionStatus.FORBIDDEN_PATH


def test_repair_tool_relocates_unique_expected_source_anchor(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("__pycache__"))
    index = build_repository_index(root, max_files=20, deadline=Deadline.after(10))
    executor = RepairToolExecutor(
        repository_root=root, repository_index=index,
    )
    result = executor.apply_edits((ProposedEdit(
        "pkg/api.py", 1, 1,
        "    return normalize(value)", "    return []",
    ),))

    assert result["accepted"]
    assert result["relocated"]
    assert executor.staged_edits[0].start_line == 40


class _TwoRevisionTransport:
    def __init__(self) -> None:
        self.turn = 0

    def __call__(self, messages, schemas):
        self.turn += 1
        if self.turn in {1, 3, 5}:
            initial = self.turn == 1
            invalid = self.turn == 3
            edit = {
                "relative_path": "pkg/api.py", "start_line": 40, "end_line": 40,
                "expected_source": "    return normalize(value)" if initial else "    return [1]",
                "replacement": (
                    "    return [1]" if initial
                    else "    return (" if invalid
                    else "    return []"
                ),
            }
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": f"apply-{self.turn}", "type": "function",
                    "function": {
                        "name": "apply_edits",
                        "arguments": json.dumps({
                            "mechanism": (
                                "initial_issue_repair" if initial
                                else "causal_slice_rewrite"
                            ),
                            "edits": [edit],
                        }),
                    },
                }],
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"finish-{self.turn}", "type": "function",
                "function": {
                    "name": "finish_revision",
                    "arguments": json.dumps({"summary": "one lineage repair"}),
                },
            }],
        }


class _UnknownToolThenEditTransport:
    def __init__(self) -> None:
        self.turn = 0

    def __call__(self, messages, schemas):
        self.turn += 1
        if self.turn == 1:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "unknown-tool", "type": "function",
                    "function": {
                        "name": "grep",
                        "arguments": json.dumps({"query": "public"}),
                    },
                }],
            }
        if self.turn == 2:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "valid-edit", "type": "function",
                    "function": {
                        "name": "apply_edits",
                        "arguments": json.dumps({
                            "mechanism": "initial_issue_repair",
                            "edits": [{
                                "relative_path": "pkg/api.py",
                                "start_line": 40,
                                "end_line": 40,
                                "expected_source": "    return normalize(value)",
                                "replacement": "    return []",
                            }],
                        }),
                    },
                }],
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "finish-after-invalid", "type": "function",
                "function": {
                    "name": "finish_revision",
                    "arguments": json.dumps({"summary": "used registered edit tool"}),
                },
            }],
        }


class _BrowseUntilFinalTurnTransport:
    def __init__(self) -> None:
        self.available_tools: list[set[str]] = []

    def __call__(self, messages, schemas):
        available = {item["function"]["name"] for item in schemas}
        self.available_tools.append(available)
        if "search_code" in available:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": f"browse-{len(self.available_tools)}", "type": "function",
                    "function": {
                        "name": "search_code",
                        "arguments": json.dumps({"query": "normalize"}),
                    },
                }],
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "final-edit", "type": "function",
                "function": {
                    "name": "apply_edits",
                    "arguments": json.dumps({
                        "mechanism": "initial_issue_repair",
                        "edits": [{
                            "relative_path": "pkg/api.py",
                            "start_line": 40,
                            "end_line": 40,
                            "expected_source": "    return normalize(value)",
                            "replacement": "    return []",
                        }],
                    }),
                },
            }],
        }


class _ForcedChoiceTransport:
    def __init__(self) -> None:
        self.turn = 0
        self.forced_choices: list[dict] = []

    def __call__(self, messages, schemas):
        self.turn += 1
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"browse-{self.turn}", "type": "function",
                "function": {
                    "name": "search_code",
                    "arguments": json.dumps({"query": "normalize"}),
                },
            }],
        }

    def call_with_tool_choice(self, messages, schemas, tool_choice):
        self.forced_choices.append(tool_choice)
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "forced-final-edit", "type": "function",
                "function": {
                    "name": "apply_edits",
                    "arguments": json.dumps({
                        "mechanism": "initial_issue_repair",
                        "edits": [{
                            "relative_path": "pkg/api.py",
                            "start_line": 40,
                            "end_line": 40,
                            "expected_source": "    return normalize(value)",
                            "replacement": "    return []",
                        }],
                    }),
                },
            }],
        }


class _ContextlessTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages, schemas):
        self.calls += 1
        return {"role": "assistant", "content": "insufficient evidence"}


def test_generator_final_turn_forces_revision_synthesis(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    transport = _BrowseUntilFinalTurnTransport()
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=1),
        generator_agent=PersistentDeepSeekAgent(transport, max_tool_turns=3),
        implementation_root=tmp_path,
    )

    state = controller.analyze(
        Instance(
            "final-turn-synthesis", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert len(transport.available_tools) == 3
    assert "search_code" in transport.available_tools[0]
    assert "search_code" not in transport.available_tools[-1]
    assert transport.available_tools[-1] == {
        "apply_edits", "request_program_slice", "run_public_check",
        "finish_revision",
    }
    assert state.transition_index == 1
    assert state.checkpoint.patch.canonical_diff


def test_production_final_turn_requests_apply_edits_explicitly(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    transport = _ForcedChoiceTransport()
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=1),
        generator_agent=PersistentDeepSeekAgent(transport, max_tool_turns=3),
        implementation_root=tmp_path,
    )

    state = controller.analyze(
        Instance(
            "forced-final-choice", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert transport.forced_choices == [{
        "type": "function", "function": {"name": "apply_edits"},
    }]
    assert state.transition_index == 1
    assert state.checkpoint.patch.canonical_diff


def test_contextless_generator_stops_before_revision_budget_is_exhausted(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    transport = _ContextlessTransport()
    controller = ReachPatchController(
        config=ReachPatchConfig(
            max_submitted_revisions=10,
            nonprogress_before_root_recovery=3,
        ),
        generator_agent=PersistentDeepSeekAgent(transport, max_tool_turns=2),
        implementation_root=tmp_path,
    )

    state, certificate = controller.run(
        Instance(
            "contextless-stop", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert certificate.status == "GENERATOR_NONPROGRESS"
    assert state.termination_status == "GENERATOR_NONPROGRESS"
    assert state.runtime_metrics["generator_contextless_revisions"] == 3
    assert state.runtime_metrics["submitted_generator_revisions"] == 4
    assert transport.calls == 4


def test_unknown_generator_tool_is_reported_without_crashing_or_executing_shell(
    tmp_path,
):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=1),
        generator_agent=PersistentDeepSeekAgent(
            _UnknownToolThenEditTransport(), max_tool_turns=4,
        ),
        implementation_root=tmp_path,
    )

    state = controller.analyze(
        Instance(
            "invalid-tool-recovery", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert state.transition_index == 1
    assert state.checkpoint.patch.canonical_diff
    tool_messages = [
        json.loads(message["content"])
        for message in state.generator_conversation.messages
        if message.get("role") == "tool"
    ]
    assert any(item.get("error") == "INVALID_TOOL" for item in tool_messages)


def test_single_working_patch_is_repaired_in_one_persistent_conversation(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    agent = PersistentDeepSeekAgent(_TwoRevisionTransport(), max_tool_turns=3)
    controller = ReachPatchController(
        config=ReachPatchConfig(
            max_submitted_revisions=3, max_active_challenges=8,
            max_active_target_bindings=4, max_active_preservation_bindings=4,
        ),
        generator_agent=agent,
        implementation_root=tmp_path,
    )
    def full_graph_must_not_run(*args, **kwargs):
        raise AssertionError("patch-first production called full Program Graph builder")

    monkeypatch.setattr(
        "reachpatch.program_graph.builder.build_augmented_program_graph",
        full_graph_must_not_run,
    )
    state, certificate = controller.run(
        Instance(
            "continuous", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert certificate.status == "GRAPH_REACHED"
    assert state.transition_index == 3
    assert [item.decision for item in state.repair_history] == [
        Decision.COMMIT, Decision.ROLLBACK, Decision.COMMIT,
    ]
    assert state.checkpoint.patch.version == 2
    assert state.runtime_metrics["deepseek_initial_generation_count"] == 1
    assert state.runtime_metrics["deepseek_repair_count"] == 2
    assert len(state.generator_conversation.accepted_patch_hashes) == 2
    assert len(state.generator_conversation.rejected_patch_hashes) == 1
    assert "return []" in state.checkpoint.patch.canonical_diff
    assert "return [1]" not in state.checkpoint.patch.canonical_diff
    records = state.runtime_metrics["graph_build_records"]
    assert records[0]["kind"] == "initial_localization"
    assert records[0]["products_materialized"] is False
    assert any(item["kind"] == "incremental_transition" for item in records)
    assert all(
        item["program_graph_seconds"] >= 0
        and item["requirement_graph_seconds"] >= 0
        and item["binding_graph_seconds"] >= 0
        and item["challenge_graph_seconds"] >= 0
        for item in records
    )


def test_initial_generator_precedes_requirement_path_and_product_build(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    events: list[str] = []
    transport = _RegressingTransport()

    def logged_transport(messages, schemas):
        events.append("generator")
        return transport(messages, schemas)
    from reachpatch.reach_avoid import transition as transition_module

    original_compile = transition_module.compile_requirement_paths

    def logged_compile(*args, **kwargs):
        events.append("requirement_paths")
        return original_compile(*args, **kwargs)

    monkeypatch.setattr(transition_module, "compile_requirement_paths", logged_compile)
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=1),
        generator_agent=PersistentDeepSeekAgent(
            logged_transport, max_tool_turns=3
        ),
        implementation_root=tmp_path,
    )

    state = controller.analyze(
        Instance(
            "patch-before-products", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert events[0] == "generator"
    assert events.index("generator") < events.index("requirement_paths")
    assert state.runtime_metrics["graph_build_records"][0][
        "products_materialized"
    ] is False


def test_incremental_program_update_retains_untouched_nodes(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    (base / "pkg").mkdir(parents=True)
    (base / "pkg" / "target.py").write_text(
        "def target(x):\n    return x + 1\n", encoding="utf-8"
    )
    (base / "pkg" / "helper.py").write_text(
        "def helper(x):\n    return x * 2\n", encoding="utf-8"
    )
    shutil.copytree(base, trial)
    index = build_repository_index(base, max_files=10, deadline=Deadline.after(10))
    seeds = RepairSliceSeed(
        symbol_names=("pkg.target.target", "pkg.helper.helper"),
        file_paths=("pkg/target.py", "pkg/helper.py"), test_paths=(),
        traceback_locations=(), issue_tokens=("target",), diff_locations=(),
        trace_locations=(), requested_context=(),
    )
    previous = build_active_program_slice(
        base, index, seeds, previous=None, budget=_budget(),
    ).graph
    (trial / "pkg" / "target.py").write_text(
        "def target(x):\n    return x + 2\n", encoding="utf-8"
    )
    actual = reconcile_actual_diff(base, trial)
    updated_index = update_repository_index(
        index, trial, tuple(actual.changed_files), deadline=Deadline.after(10)
    )
    delta = update_active_program_slice(
        previous, updated_index, trial, actual, None, (), _budget(),
    )
    helper_ids = set(previous.file_index["pkg/helper.py"])

    assert "pkg/target.py" in delta.rebuilt_files
    assert helper_ids <= set(delta.graph.nodes)
    assert all(delta.graph.nodes[node_id] is previous.nodes[node_id] for node_id in helper_ids)
    assert not helper_ids & set(delta.removed_node_ids)
    assert delta.added_node_ids or delta.removed_node_ids or delta.modified_node_ids
    assert updated_index.source_hashes["pkg/target.py"] != index.source_hashes["pkg/target.py"]
    assert delta.graph.source_hash == content_hash({
        path: updated_index.source_hashes[path]
        for path in sorted(delta.graph.file_index)
    })


def test_incremental_program_update_bounds_cumulative_active_slice(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    (base / "pkg").mkdir(parents=True)
    for number in range(8):
        (base / "pkg" / f"module_{number}.py").write_text(
            f"def function_{number}(value):\n    return value + {number}\n",
            encoding="utf-8",
        )
    shutil.copytree(base, trial)
    index = build_repository_index(base, max_files=20, deadline=Deadline.after(10))
    seed_paths = tuple(f"pkg/module_{number}.py" for number in range(4))
    seeds = RepairSliceSeed(
        symbol_names=tuple(f"function_{number}" for number in range(4)),
        file_paths=seed_paths, test_paths=(), traceback_locations=(),
        issue_tokens=(), diff_locations=(), trace_locations=(),
        requested_context=(),
    )
    previous = build_active_program_slice(
        base, index, seeds, previous=None, budget=_budget(files=4, functions=4),
    ).graph
    assert len(previous.file_index) == 4

    changed = trial / "pkg" / "module_4.py"
    changed.write_text(
        "def function_4(value):\n    return value + 40\n", encoding="utf-8"
    )
    actual = reconcile_actual_diff(base, trial)
    updated_index = update_repository_index(
        index, trial, tuple(actual.changed_files), deadline=Deadline.after(10)
    )
    delta = update_active_program_slice(
        previous, updated_index, trial, actual, None, (),
        _budget(files=4, functions=4),
    )

    assert "pkg/module_4.py" in delta.graph.file_index
    assert len(delta.graph.file_index) <= 4
    assert len(delta.graph.cfgs) <= 4
    assert len(delta.graph.nodes) <= 5_000
    assert len(delta.graph.edges) <= 15_000
    assert any(
        frontier.kind == "ANALYSIS_TRUNCATED" and not frontier.hard
        for frontier in delta.graph.frontiers.values()
    )


def test_incremental_context_replaces_precise_functions_within_one_file(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    (base / "pkg").mkdir(parents=True)
    (base / "pkg" / "module.py").write_text(
        "def first(value):\n    return value + 1\n\n"
        "def second(value):\n    return value + 2\n",
        encoding="utf-8",
    )
    shutil.copytree(base, trial)
    index = build_repository_index(base, max_files=10, deadline=Deadline.after(10))
    previous = build_active_program_slice(
        base, index,
        RepairSliceSeed(
            symbol_names=("pkg.module.first",), file_paths=("pkg/module.py",),
            test_paths=(), traceback_locations=(), issue_tokens=(),
            diff_locations=(), trace_locations=(), requested_context=(),
        ),
        previous=None, budget=_budget(files=1, functions=1),
    ).graph
    previous_precise = {
        previous.nodes[cfg.callable_id].attributes.get("qualified_name")
        for cfg in previous.cfgs.values()
    }
    assert previous_precise == {"pkg.module.first"}

    delta = update_active_program_slice(
        previous, index, trial, reconcile_actual_diff(base, trial), None,
        (ContextRequest(symbols=("pkg.module.second",), file_paths=("pkg/module.py",)),),
        _budget(files=1, functions=1),
    )
    updated_callables = {
        delta.graph.nodes[cfg.callable_id].attributes.get("qualified_name")
        for cfg in delta.graph.cfgs.values()
    }

    assert "pkg.module.second" in updated_callables
    assert "pkg.module.first" not in updated_callables
    assert len(delta.graph.cfgs) <= 1


def test_incremental_update_invalidates_derived_paths_from_touched_file(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    shutil.copytree(FIXTURE, base)
    shutil.copytree(FIXTURE, trial)
    index = build_repository_index(base, max_files=20, deadline=Deadline.after(10))
    seeds = recover_repair_slice_seeds(
        "For every x, pkg.api.public(x) must return a normalized value.", (), index,
    )
    previous = build_active_program_slice(
        base, index, seeds, previous=None, budget=_budget(),
    ).graph
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return a normalized value."
    ).graph
    assignment = build_hypothesis_set(semantic).alternatives[0]
    requirements = compile_assignment_overlay(semantic, assignment)
    compile_requirement_paths(requirements, previous)
    touched_path_ids = {
        path_id
        for path_id, path_class in previous.path_classes.items()
        if any(
            previous.nodes[node_id].attributes.get("file") == "pkg/api.py"
            for node_id in path_class.node_ids
        )
    }
    assert touched_path_ids

    api_path = trial / "pkg" / "api.py"
    source = api_path.read_text(encoding="utf-8")
    api_path.write_text(source.replace("return normalize(value)", "return normalize(value) or []"), encoding="utf-8")
    actual = reconcile_actual_diff(base, trial)
    updated_index = update_repository_index(
        index, trial, tuple(actual.changed_files), deadline=Deadline.after(10)
    )
    delta = update_active_program_slice(
        previous, updated_index, trial, actual, None, (), _budget(),
    )

    assert not touched_path_ids & set(delta.graph.path_classes)
    # Path classes are rebuilt later by requirement-path recovery, but the
    # touched callable's precise CFG must already be present in this delta.
    assert any(
        delta.graph.nodes[cfg.callable_id].attributes.get("file") == "pkg/api.py"
        for cfg in delta.graph.cfgs.values()
    )
    binding = build_active_binding_graph(
        requirements, delta.graph, previous=None,
        affected_leaf_ids=set(requirements.leaves),
        affected_path_ids=set(requirements.path_obligations),
        max_target_units=20, max_preservation_units=20,
        deadline=time.monotonic() + 10,
    )
    assert any(
        frontier.kind == "STALE_PATH_OBLIGATION"
        for frontier in binding.frontiers.values()
    )


def test_bounded_requirement_refresh_can_retry_after_all_paths_are_invalidated():
    program_root = FIXTURE.resolve()
    index = build_repository_index(
        program_root, max_files=20, deadline=Deadline.after(10)
    )
    seeds = recover_repair_slice_seeds(
        "For every x, pkg.api.public(x) must return a normalized value.", (), index,
    )
    program = build_active_program_slice(
        program_root, index, seeds, previous=None, budget=_budget(),
    ).graph
    semantic = build_semantic_graph(
        "For every x, pkg.api.public(x) must return a normalized value."
    ).graph
    assignment = build_hypothesis_set(semantic).alternatives[0]
    requirements = compile_assignment_overlay(semantic, assignment)
    compile_requirement_paths(requirements, program)
    assert requirements.path_obligations
    assert requirements.edge_ledger

    refreshed, _, _ = refresh_requirement_paths(
        requirements,
        program,
        affected_leaf_ids=set(requirements.leaves),
        deadline=time.monotonic() - 1,
    )
    assert not refreshed.path_obligations
    assert not refreshed.edge_ledger

    # A later targeted context expansion must be able to retry the same leaf
    # instead of failing because stale ledger records survived the deadline.
    compile_requirement_paths(refreshed, program)
    assert refreshed.path_obligations


def test_incremental_repository_index_removes_deleted_symbols(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    (base / "pkg").mkdir(parents=True)
    (base / "pkg" / "kept.py").write_text("def kept():\n    return 1\n", encoding="utf-8")
    (base / "pkg" / "deleted.py").write_text(
        "def removed_symbol():\n    return 2\n", encoding="utf-8"
    )
    shutil.copytree(base, trial)
    index = build_repository_index(base, max_files=10, deadline=Deadline.after(10))
    (trial / "pkg" / "deleted.py").unlink()
    actual = reconcile_actual_diff(base, trial)
    updated = update_repository_index(
        index, trial, tuple(actual.changed_files), deadline=Deadline.after(10)
    )

    assert "pkg/deleted.py" not in updated.source_hashes
    assert "pkg.deleted" not in updated.modules
    assert "removed_symbol" not in updated.symbols
    assert updated.source_hashes["pkg/kept.py"] == index.source_hashes["pkg/kept.py"]


def test_import_check_treats_baseline_environment_failure_as_non_regression(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    (base / "pkg").mkdir(parents=True)
    (base / "pkg" / "mod.py").write_text(
        "import dependency_that_is_not_installed\nVALUE = 1\n", encoding="utf-8"
    )
    shutil.copytree(base, trial)
    (trial / "pkg" / "mod.py").write_text(
        "import dependency_that_is_not_installed\nVALUE = 2\n", encoding="utf-8"
    )
    actual = reconcile_actual_diff(base, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=base)
    import_check = next(item for item in checks if item.kind.startswith("IMPORT"))

    assert import_check.kind == "IMPORT_BASELINE_BLOCKED"
    assert import_check.status.value == "PASS"
    assert "no confirmed import regression" in import_check.stderr


@pytest.mark.parametrize(
    ("baseline_value", "patched_value", "classification"),
    [
        ("pass", "pass", "PASS_PRESERVED"),
        ("fail", "pass", "TARGET_FIXED"),
        ("pass", "fail", "PRESERVATION_REGRESSION"),
        ("fail", "fail", "STABLE_FAIL"),
    ],
)
def test_public_checks_compare_incumbent_and_trial(
    tmp_path, baseline_value, patched_value, classification,
):
    baseline = tmp_path / "baseline"
    patched = tmp_path / "patched"
    baseline.mkdir()
    patched.mkdir()
    (baseline / "result.txt").write_text(baseline_value, encoding="utf-8")
    (patched / "result.txt").write_text(patched_value, encoding="utf-8")
    command = (
        sys.executable, "-c",
        "import pathlib,sys; sys.exit(pathlib.Path('result.txt').read_text() != 'pass')",
    )

    comparison = run_public_checks_paired(
        baseline, patched, (command,), timeout_seconds=10,
    )[0]

    assert comparison.classification == classification
    assert comparison.preservation_regression == (
        classification == "PRESERVATION_REGRESSION"
    )


@pytest.mark.parametrize("diagnostic", [
    "ModuleNotFoundError: No module named 'asgiref'",
    "ImportError while loading conftest '/repo/tests/conftest.py'",
    "fixture 'path' not found",
    "ImportError: cannot import name '_c_internal_utils'",
    "collected 0 items",
])
def test_public_checks_classify_execution_infrastructure_as_blocked(
    tmp_path, diagnostic,
):
    baseline = tmp_path / "baseline"
    patched = tmp_path / "patched"
    baseline.mkdir()
    patched.mkdir()
    command = (
        sys.executable, "-c",
        f"import sys; print({diagnostic!r}, file=sys.stderr); sys.exit(2)",
    )

    comparison = run_public_checks_paired(
        baseline, patched, (command,), timeout_seconds=10,
    )[0]

    assert comparison.classification == "BLOCKED_EXTERNAL"
    assert not comparison.preservation_regression


def test_repair_tools_enforce_public_evidence_boundary(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pkg" / "api.py").write_text("def api():\n    return 1\n", encoding="utf-8")
    (root / "tests" / "test_public.py").write_text("PUBLIC_WITNESS = 1\n", encoding="utf-8")
    (root / "tests" / "test_patch.py").write_text("GOLD_SECRET = 2\n", encoding="utf-8")
    index = build_repository_index(root, max_files=10, deadline=Deadline.after(10))
    tools = RepairToolExecutor(
        repository_root=root, repository_index=index,
        allowed_test_paths={"tests/test_public.py"},
    )

    assert "PUBLIC_WITNESS" in tools.read_file("tests/test_public.py")["content"]
    with pytest.raises(ValueError, match="official harness or gold evidence"):
        tools.read_file("tests/test_patch.py")
    search = tools.search_code("GOLD_SECRET")
    assert search["matches"] == []
    with pytest.raises(ValueError, match="test edits are forbidden"):
        tools.apply_edits((ProposedEdit(
            "tests/test_public.py", 1, 1, "PUBLIC_WITNESS = 1", "PUBLIC_WITNESS = 2"
        ),))


def test_generation_instance_rejects_official_fields():
    import reachpatch.models as exported_models

    assert {"ReachAvoidState", "WorkingPatch", "GenerationInstance"} <= set(
        exported_models.__all__
    )
    public = {
        "instance_id": "public-only", "repo": "owner/repo",
        "base_commit": "base", "problem_statement": "fix public behavior",
    }
    assert GenerationInstance.from_public_record(public).issue == "fix public behavior"
    with pytest.raises(ValueError, match="official-only field"):
        GenerationInstance(
            "leak", "owner/repo", "base", "issue",
            public_metadata={"nested": {"FAIL_TO_PASS": ["secret"]}},
        )
    with pytest.raises(ValueError, match="official-only field"):
        assert_generation_payload({"environment": {"harness_logs": "secret"}})


class _FailingTransport:
    def __call__(self, messages, schemas):
        raise OSError("model endpoint unavailable")


class _CorrectInitialTransport:
    def __init__(self):
        self.turn = 0

    def __call__(self, messages, schemas):
        self.turn += 1
        if self.turn == 1:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "apply-correct", "type": "function",
                    "function": {
                        "name": "apply_edits",
                        "arguments": json.dumps({
                            "mechanism": "initial_issue_repair",
                            "edits": [{
                                "relative_path": "pkg/api.py",
                                "start_line": 40, "end_line": 40,
                                "expected_source": "    return normalize(value)",
                                "replacement": "    return list(normalize(value))",
                            }],
                        }),
                    },
                }],
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "finish-correct", "type": "function",
                "function": {
                    "name": "finish_revision",
                    "arguments": json.dumps({"summary": "implement shared hard behavior"}),
                },
            }],
        }


class _RegressingTransport:
    def __init__(self):
        self.turn = 0

    def __call__(self, messages, schemas):
        self.turn += 1
        if self.turn == 1:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "apply-regression", "type": "function",
                    "function": {
                        "name": "apply_edits",
                        "arguments": json.dumps({
                            "mechanism": "initial_issue_repair",
                            "edits": [{
                                "relative_path": "pkg/api.py",
                                "start_line": 40, "end_line": 40,
                                "expected_source": "    return normalize(value)",
                                "replacement": "    return []",
                            }],
                        }),
                    },
                }],
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "finish-regression", "type": "function",
                "function": {
                    "name": "finish_revision",
                    "arguments": json.dumps({"summary": "regressing revision"}),
                },
            }],
        }


def test_public_preservation_regression_rolls_back_only_trial(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=1),
        generator_agent=PersistentDeepSeekAgent(
            _RegressingTransport(), max_tool_turns=3,
        ),
        implementation_root=tmp_path,
    )

    state = controller.analyze(
        Instance(
            "public-regression", str(repository), "base",
            "For every x, pkg.api.public(x) must return a list.",
        ),
        run_root=tmp_path / "run",
    )

    assert state.transition_index == 1
    assert state.checkpoint.patch.canonical_diff == ""
    assert state.repair_history[-1].decision == Decision.ROLLBACK
    assert state.repair_history[-1].avoid
    assert any(
        item.failure_origin == "PUBLIC_PRESERVATION_REGRESSION"
        for item in state.counterexamples
    )
    assert state.artifact_ids["public_check_comparison"]


def test_public_target_fix_contributes_transition_progress(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    target_check = (
        sys.executable, "-c",
        "import sys; from pkg.api import public; sys.exit(public([1]) != [])",
    )
    controller = ReachPatchController(
        config=ReachPatchConfig(
            max_submitted_revisions=1,
            mechanical_commands=(target_check,),
        ),
        generator_agent=PersistentDeepSeekAgent(
            _RegressingTransport(), max_tool_turns=3,
        ),
        implementation_root=tmp_path,
    )

    state = controller.analyze(
        Instance(
            "public-target", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert state.checkpoint.patch.canonical_diff
    assert state.repair_history[-1].decision == Decision.COMMIT
    assert state.repair_history[-1].progress
    comparisons = state.repair_history[-1].graph_delta["public_check_comparisons"]
    assert {item["classification"] for item in comparisons} == {"TARGET_FIXED"}
    assert state.runtime_metrics["public_target_fixed_commands"]
    assert state.runtime_metrics["public_target_evidence_unit_ids"]


def test_semantic_ambiguity_still_reaches_initial_generator(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=2),
        generator_agent=PersistentDeepSeekAgent(
            _CorrectInitialTransport(), max_tool_turns=3,
        ),
        implementation_root=tmp_path,
    )
    state = controller.analyze(
        Instance(
            "ambiguous-generation", str(repository), "base",
            "For every x, pkg.api.public(x) must return a list. "
            "The result could preserve identity? The result could create a copy?",
        ),
        run_root=tmp_path / "run",
    )

    assert len(state.hypothesis_set.alternatives) >= 2
    assert state.runtime_metrics["deepseek_initial_generation_count"] == 1
    assert state.transition_index == 1
    assert state.checkpoint.patch.canonical_diff
    probe_ids = {
        item["probe_id"]
        for item in state.runtime_metrics["discriminator_probes"]
    }
    executed = set(state.runtime_metrics["executed_discriminator_probe_ids"])
    assert executed <= probe_ids
    if executed:
        mapping = state.runtime_metrics["discriminator_probe_challenges"]
        assert all(mapping[probe_id] for probe_id in executed)
        assert all(
            state.challenge_graph.cells[challenge_id].execution_bundle_id
            for probe_id in executed
            for challenge_id in mapping[probe_id]
        )
    else:
        assert any(
            frontier.kind == "DISCRIMINATOR_DEFERRED"
            for frontier in state.challenge_graph.frontiers.values()
        )


def test_generator_external_failure_is_safely_sealed(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=2),
        generator_agent=PersistentDeepSeekAgent(_FailingTransport(), max_tool_turns=2),
        implementation_root=tmp_path,
    )
    state, certificate = controller.run(
        Instance(
            "external-block", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert certificate.status == "GENERATOR_BLOCKED_EXTERNAL"
    assert state.termination_status == "GENERATOR_BLOCKED_EXTERNAL"
    assert state.transition_index == 0
    assert state.checkpoint.patch.canonical_diff == ""
    assert state.artifact_ids["generator_failure"]
    assert (tmp_path / "run" / "final_patch.diff").read_text(encoding="utf-8") == ""


def test_graph_budget_returns_partial_slice_with_soft_frontier(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "target.py").write_text(
        "def target(value):\n"
        + "\n".join(f"    value = value + {index}" for index in range(100))
        + "\n    return value\n",
        encoding="utf-8",
    )
    index = build_repository_index(root, max_files=10, deadline=Deadline.after(10))
    seeds = recover_repair_slice_seeds("target(value) must return a value", (), index)
    budget = GraphBudget.from_limits(
        seconds=10, max_nodes=20, max_edges=30, max_files=2,
        max_functions=2, max_rss_mib=2_048,
    )
    result = build_active_program_slice(
        root, index, seeds, previous=None, budget=budget,
    )

    assert result.truncated_reason in {"NODE_LIMIT", "EDGE_LIMIT"}
    assert result.graph.nodes
    assert any(
        frontier.kind == "ANALYSIS_TRUNCATED" and not frontier.hard
        for frontier in result.graph.frontiers.values()
    )


def test_iterative_cfg_handles_long_sequential_function(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "long.py").write_text(
        "def long(value):\n"
        + "\n".join("    value += 1" for _ in range(1500))
        + "\n    return value\n",
        encoding="utf-8",
    )
    index = build_repository_index(root, max_files=10, deadline=Deadline.after(10))
    seeds = recover_repair_slice_seeds("long(value) must return a value", (), index)
    result = build_active_program_slice(
        root, index, seeds, previous=None,
        budget=GraphBudget.from_limits(
            seconds=20, max_nodes=10_000, max_edges=20_000,
            max_files=2, max_functions=2, max_rss_mib=2_048,
        ),
    )

    assert len(result.graph.cfgs) == 1
    cfg = next(iter(result.graph.cfgs.values()))
    assert len(cfg.statement_node_ids) == 1501


def test_diff_domain_promotion_only_adds_touched_guard_neighbours(tmp_path):
    base = tmp_path / "base"
    trial = tmp_path / "trial"
    base.mkdir()
    trial.mkdir()
    (base / "api.py").write_text(
        "def public(x):\n    if x:\n        return [x]\n    return []\n",
        encoding="utf-8",
    )
    (trial / "api.py").write_text(
        "def public(x):\n    if not x:\n        return []\n    return [x]\n",
        encoding="utf-8",
    )
    index = build_repository_index(trial, max_files=10, deadline=Deadline.after(10))
    semantic = build_semantic_graph(
        "For every x, api.public(x) must return a list."
    ).graph
    hypotheses = build_hypothesis_set(semantic)
    requirements = compile_requirement_core(semantic, hypotheses, index)
    before = set(requirements.partitions)
    actual = reconcile_actual_diff(base, trial)
    delta = promote_domains_from_diff(
        requirements, SimpleNamespace(), actual, None,
        deadline=time.monotonic() + 10,
    )
    constraints = {
        constraint
        for partition_id in delta.added_partition_ids
        for constraint in requirements.partitions[partition_id].constraints
    }

    assert delta.affected_leaf_ids
    assert set(delta.added_partition_ids).isdisjoint(before)
    assert {"not x", "not (not x)", "len(x) == 0", "len(x) > 0"} <= constraints
    assert {"bool(x) is False", "bool(x) is True"} <= constraints
