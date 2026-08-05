from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from reachpatch.artifacts.store import ArtifactStore
from reachpatch.binding_graph import build_legacy_active_binding_product
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
from reachpatch.reach_avoid.controller import (
    ReachPatchConfig,
    ReachPatchController,
    _inferred_public_test_paths,
)
from reachpatch.reach_avoid.transition import (
    _apply_revision_edits, _mechanical_packet, evaluate_patch_revision,
)
from reachpatch.repair.deepseek_agent import (
    ActionConversionStatus, GeneratorRevision, PersistentDeepSeekAgent,
    convert_revision_action,
)
from reachpatch.repair.context import _issue_text
from reachpatch.repair.policy import next_untried_repair_intent
from reachpatch.repair.tools import ProposedEdit, RepairToolExecutor
from reachpatch.requirement_graph import (
    compile_assignment_overlay, compile_requirement_core,
    compile_requirement_paths, promote_domains_from_diff,
    refresh_requirement_paths,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "simple_repo"


def test_transactional_applier_clamps_virtual_eof_newline_range(tmp_path):
    (tmp_path / "module.py").write_text(
        "def value():\n    return 1\n", encoding="utf-8",
    )
    revision = GeneratorRevision(
        revision_id="eof-range", mechanism="initial_issue_repair",
        edits=(ProposedEdit(
            relative_path="module.py", start_line=2, end_line=4,
            expected_source="    return 1\n",
            replacement="    return 2\n",
        ),),
        summary="reviewed EOF repair", context_requests=(),
        requested_public_checks=(), tool_turns=1, status="PROPOSED",
    )

    _apply_revision_edits(tmp_path, revision)

    assert (tmp_path / "module.py").read_text(encoding="utf-8") == (
        "def value():\n    return 2\n"
    )


def test_public_test_inference_prioritizes_references_to_issue_symbols(tmp_path):
    index = SimpleNamespace(
        symbols={"uniq": (object(),), "unrelated": (object(),)},
        test_references={
            "bin/test_alpha.py": ("list", "argument"),
            "pkg/tests/test_iterables.py": ("uniq", "list"),
            "pkg/tests/test_other.py": ("unrelated",),
        },
    )

    inferred = _inferred_public_test_paths(
        "uniq modifies a list argument", index, tmp_path, limit=10,
    )

    assert inferred == (str(tmp_path / "pkg/tests/test_iterables.py"),)


def test_public_test_inference_selects_symbol_referencing_test_function(tmp_path):
    test_path = tmp_path / "pkg" / "tests" / "test_solver.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "def test_unrelated():\n"
        "    assert helper()\n\n"
        "def test_existing_solver_contract():\n"
        "    assert solve_system([1]) == [1]\n",
        encoding="utf-8",
    )
    index = SimpleNamespace(
        symbols={"solve_system": (object(),), "helper": (object(),)},
        test_references={
            "pkg/tests/test_solver.py": ("solve_system", "helper"),
        },
    )

    inferred = _inferred_public_test_paths(
        "solve_system should reject infinite input", index, tmp_path, limit=10,
    )

    assert inferred == (
        f"{test_path}::test_existing_solver_contract",
    )


def test_mechanical_structural_check_rejects_shadowed_class_and_keyword_collision(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text(
        "class ExceptionInfo:\n    pass\n\n"
        "class ExceptionInfo:\n    pass\n\n"
        "def build(**kwargs):\n"
        "    kwargs.setdefault('formatter_class', object)\n"
        "    return dict(formatter_class=object, **kwargs)\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "module.py").write_text("class ExceptionInfo:\n    pass\n", encoding="utf-8")
    actual = reconcile_actual_diff(baseline, root)
    checks = run_mechanical_checks(root, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")
    assert structural.status == OutcomeStatus.FAIL
    assert "duplicate top-level definition" in structural.stderr
    assert "setdefault" in structural.stderr


def test_guard_narrowing_is_preservation_advisory_not_mechanical_failure(tmp_path):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "module.py").write_text(
        "def choose(values, dimensions):\n"
        "    if len(values) == 1:\n"
        "        return values[0]\n"
        "    return None\n",
        encoding="utf-8",
    )
    trial = tmp_path / "trial"
    shutil.copytree(baseline, trial)
    (trial / "module.py").write_text(
        "def choose(values, dimensions):\n"
        "    if len(values) == 1 and len(dimensions) == 1:\n"
        "        return values[0]\n"
        "    return None\n",
        encoding="utf-8",
    )
    actual = reconcile_actual_diff(baseline, trial)

    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")

    assert structural.status == OutcomeStatus.PASS
    assert "guard narrowed by new conjunct" in structural.stdout
    assert structural.stderr == ""


def test_mechanical_structural_check_ignores_preexisting_keyword_conflict(tmp_path):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    source = (
        "def existing(**kwargs):\n"
        "    kwargs.setdefault('strip', True)\n"
        "    return dict(strip=False, **kwargs)\n\n"
        "def public(path):\n"
        "    return path\n"
    )
    (baseline / "module.py").write_text(source, encoding="utf-8")
    trial = tmp_path / "trial"
    trial.mkdir()
    (trial / "module.py").write_text(
        source.replace(
            "def public(path):\n    return path\n",
            "def public(path):\n"
            "    if callable(path):\n"
            "        path = path()\n"
            "    return path\n",
        ),
        encoding="utf-8",
    )

    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")

    assert structural.status == OutcomeStatus.PASS
    assert "setdefault" not in structural.stderr


def test_repair_tool_rejects_duplicate_member_before_staging(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text(
        "class Query:\n"
        "    def update(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"module.py": "hash"}),
    )
    with pytest.raises(ValueError, match="duplicate member Query.update"):
        tools.apply_edits((ProposedEdit(
            relative_path="module.py",
            start_line=3,
            end_line=3,
            expected_source="        return 1",
            replacement=(
                "        return 1\n\n"
                "    def update(self):\n"
                "        return 2"
            ),
        ),))
    assert tools.staged_edits == []


def test_repair_tool_rejects_new_pass_only_method_before_staging(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "query.py").write_text(
        "class Query:\n"
        "    def execute(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"query.py": "hash"}),
    )

    with pytest.raises(ValueError, match="placeholder definition"):
        tools.apply_edits((ProposedEdit(
            relative_path="query.py", start_line=3, end_line=3,
            expected_source="        return 1",
            replacement=(
                "        return 1\n\n"
                "    def deferred_behavior(self):\n"
                "        pass"
            ),
        ),))

    assert tools.staged_edits == []


def test_repair_tool_allows_existing_placeholder_to_shift_lines(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "fields.py").write_text(
        "class Field:\n"
        "    def normalize(self, value):\n"
        "        return value\n"
        "\n"
        "    def protocol_hook(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"fields.py": "hash"}),
    )

    result = tools.apply_edits((ProposedEdit(
        relative_path="fields.py", start_line=3, end_line=3,
        expected_source="        return value",
        replacement="        value = str(value)\n        return value",
    ),))

    assert result["accepted"]
    assert len(tools.staged_edits) == 1


def test_existing_overload_family_is_not_shadowing_regression(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = (
        "class Traceback:\n"
        "    @overload\n"
        "    def __getitem__(self, key: int): ...\n"
        "    @overload\n"
        "    def __getitem__(self, key: slice): ...\n"
        "    def __getitem__(self, key):\n"
        "        return self.items[key]\n"
        "\n"
        "    def render(self):\n"
        "        return self.items\n"
    )
    (root / "code.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"code.py": "hash"}),
    )

    result = tools.apply_edits((ProposedEdit(
        relative_path="code.py", start_line=10, end_line=10,
        expected_source="        return self.items",
        replacement="        return list(self.items)",
    ),))

    assert result["accepted"]
    assert len(tools.staged_edits) == 1


def test_repair_tool_rejects_state_reintroduction_before_staging(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    before = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        self.state = existing.difference(field_names), False\n"
    )
    (root / "query.py").write_text(before, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"query.py": "hash"}),
    )
    with pytest.raises(ValueError, match="invalid state transition"):
        tools.apply_edits((ProposedEdit(
            relative_path="query.py",
            start_line=4,
            end_line=4,
            expected_source="        self.state = existing.difference(field_names), False",
            replacement=(
                "        remaining = existing.difference(field_names)\n"
                "        if remaining:\n"
                "            self.state = remaining, False\n"
                "        else:\n"
                "            self.state = frozenset(field_names), True"
            ),
        ),))
    assert tools.staged_edits == []


def test_repair_tool_accepts_normalized_incoming_only_mode_switch(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    before = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        self.state = existing.difference(field_names), False\n"
    )
    (root / "query.py").write_text(before, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"query.py": "hash"}),
    )

    result = tools.apply_edits((ProposedEdit(
        relative_path="query.py",
        start_line=4,
        end_line=4,
        expected_source="        self.state = existing.difference(field_names), False",
        replacement=(
            "        remaining = existing.difference(field_names)\n"
            "        if remaining:\n"
            "            self.state = remaining, False\n"
            "        else:\n"
            "            self.state = frozenset(field_names).difference(existing), True"
        ),
    ),))

    assert result["accepted"]
    assert len(tools.staged_edits) == 1


def test_repair_tool_rejects_set_method_on_unnormalized_input_parameter(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    before = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        self.state = existing.difference(field_names), False\n"
    )
    (root / "query.py").write_text(before, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"query.py": "hash"}),
    )

    with pytest.raises(ValueError, match="unnormalized input parameter"):
        tools.apply_edits((ProposedEdit(
            relative_path="query.py", start_line=4, end_line=4,
            expected_source="        self.state = existing.difference(field_names), False",
            replacement=(
                "        remaining = existing.difference(field_names)\n"
                "        if remaining:\n"
                "            self.state = remaining, False\n"
                "        else:\n"
                "            self.state = field_names.difference(existing), True"
            ),
        ),))

    assert tools.staged_edits == []


def test_state_rejection_locates_exact_companion_mode_consumer_guard(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    before = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        self.state = existing.difference(field_names), False\n"
        "\n"
        "    def materialize(self):\n"
        "        field_names, defer = self.state\n"
        "        if not field_names:\n"
        "            return []\n"
        "        if defer:\n"
        "            return exclude(field_names)\n"
        "        return include(field_names)\n"
    )
    (root / "query.py").write_text(before, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"query.py": "hash"}),
    )

    with pytest.raises(ValueError, match="state-consumer guard anchor"):
        tools.apply_edits((ProposedEdit(
            relative_path="query.py",
            start_line=4,
            end_line=4,
            expected_source="        self.state = existing.difference(field_names), False",
            replacement=(
                "        remaining = existing.difference(field_names)\n"
                "        if remaining:\n"
                "            self.state = remaining, False\n"
                "        else:\n"
                "            self.state = frozenset(field_names), True"
            ),
        ),))

    assert tools.staged_edits == []
    assert len(tools.mechanical_recovery_anchors) == 1
    anchor = tools.mechanical_recovery_anchors[0]
    assert anchor["kind"] == "STATE_CONSUMER_GUARD"
    assert anchor["symbol"] == "materialize"
    assert anchor["state_attribute"] == "self.state"
    assert anchor["guard_start_line"] == 8
    assert anchor["guard_source"] == "        if not field_names:\n            return []"
    assert anchor["companion_names"] == ("defer",)


def test_repair_tool_rejects_copied_statement_block_before_staging(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    before = (
        "def consume(items):\n"
        "    for item in items:\n"
        "        visit(item)\n"
        "    for item in items:\n"
        "        emit(item)\n"
        "    return items\n"
    )
    (root / "consumer.py").write_text(before, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"consumer.py": "hash"}),
    )
    with pytest.raises(ValueError, match="duplicates an existing statement block"):
        tools.apply_edits((ProposedEdit(
            relative_path="consumer.py",
            start_line=2,
            end_line=5,
            expected_source=(
                "    for item in items:\n"
                "        visit(item)\n"
                "    for item in items:\n"
                "        emit(item)"
            ),
            replacement=(
                "    for item in items:\n"
                "        visit(item)\n"
                "    for item in items:\n"
                "        emit(item)\n"
                "    for item in items:\n"
                "        visit(item)\n"
                "    for item in items:\n"
                "        emit(item)"
            ),
        ),))
    assert tools.staged_edits == []


def test_mechanical_structural_check_rejects_state_input_reintroduction(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    baseline.mkdir()
    trial.mkdir()
    before = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        self.state = existing.difference(field_names), False\n"
    )
    after = before + (
        "        if not self.state[0]:\n"
        "            self.state = frozenset(field_names), True\n"
    )
    (baseline / "query.py").write_text(before, encoding="utf-8")
    (trial / "query.py").write_text(after, encoding="utf-8")
    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")
    assert structural.status == OutcomeStatus.FAIL
    assert "reintroduces the raw input" in structural.stderr
    assert "incoming-minus-existing" in structural.stderr


def test_caller_owned_getattr_alias_must_be_cloned_before_state_write(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    baseline.mkdir()
    trial.mkdir()
    before = (
        "class Wrapper:\n"
        "    def __init__(self, value):\n"
        "        self.value = getattr(value, 'value', value)\n"
    )
    after = before + "        self.value.active = True\n"
    (baseline / "wrapper.py").write_text(before, encoding="utf-8")
    (trial / "wrapper.py").write_text(after, encoding="utf-8")

    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")

    assert structural.status == OutcomeStatus.FAIL
    assert "caller-owned alias" in structural.stderr
    assert "self.value.active" in structural.stderr


def test_caller_owned_alias_clone_terminates_alias_before_state_write(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    baseline.mkdir()
    trial.mkdir()
    before = (
        "class Wrapper:\n"
        "    def __init__(self, value):\n"
        "        self.value = getattr(value, 'value', value)\n"
    )
    after = before + (
        "        self.value = self.value.clone()\n"
        "        self.value.active = True\n"
    )
    (baseline / "wrapper.py").write_text(before, encoding="utf-8")
    (trial / "wrapper.py").write_text(after, encoding="utf-8")

    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")

    assert structural.status == OutcomeStatus.PASS


def test_new_local_dict_and_kwargs_are_not_caller_owned_aliases(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    baseline.mkdir()
    trial.mkdir()
    before = (
        "def render(value, **extra):\n"
        "    params = {**extra, 'value': value}\n"
        "    return params\n"
    )
    after = (
        "def render(value, **extra):\n"
        "    params = {**extra, 'value': value}\n"
        "    params['rendered'] = True\n"
        "    extra['consumed'] = True\n"
        "    return params\n"
    )
    (baseline / "render.py").write_text(before, encoding="utf-8")
    (trial / "render.py").write_text(after, encoding="utf-8")

    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")

    assert structural.status == OutcomeStatus.PASS


def test_existing_alias_write_is_stable_when_earlier_lines_shift(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    baseline.mkdir()
    trial.mkdir()
    before = (
        "def cache(data, field_name):\n"
        "    data[field_name] = 1\n"
    )
    after = (
        "def cache(data, field_name):\n"
        "    normalized = field_name.strip()\n"
        "    data[field_name] = 1\n"
    )
    (baseline / "cache.py").write_text(before, encoding="utf-8")
    (trial / "cache.py").write_text(after, encoding="utf-8")

    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")

    assert structural.status == OutcomeStatus.PASS


def test_repair_tool_rejects_caller_owned_alias_mutation_before_staging(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = (
        "class Wrapper:\n"
        "    def __init__(self, value):\n"
        "        self.value = getattr(value, 'value', value)\n"
    )
    (root / "wrapper.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"wrapper.py": "hash"}),
    )

    with pytest.raises(ValueError, match="mutates caller-owned state"):
        tools.apply_edits((ProposedEdit(
            relative_path="wrapper.py",
            start_line=3,
            end_line=3,
            expected_source="        self.value = getattr(value, 'value', value)",
            replacement=(
                "        self.value = getattr(value, 'value', value)\n"
                "        self.value.active = True"
            ),
        ),))

    assert tools.staged_edits == []


def test_repair_tool_rejects_copied_full_method_tail_as_noop(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    body = "\n".join(f"    value_{index} = {index}" for index in range(12))
    source = f"def compute():\n{body}\n    return value_11\n"
    (root / "module.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"module.py": "hash"}),
    )
    lines = source.splitlines()
    copied_tail = "\n".join(lines[1:])

    with pytest.raises(ValueError, match="no-op edit"):
        tools.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=2, end_line=2,
            expected_source=lines[1], replacement=copied_tail,
        ),))

    assert tools.staged_edits == []


def test_complete_replacement_rejects_import_only_edit_and_restores_prior_stage(
    tmp_path,
):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"module.py": "hash"}),
    )
    original_stage = ProposedEdit(
        relative_path="module.py", start_line=1, end_line=1,
        expected_source="VALUE = 1", replacement="VALUE = 2",
    )
    tools.apply_edits((original_stage,))

    with pytest.raises(ValueError, match="import-only replacement"):
        tools.replace_staged_edits((ProposedEdit(
            relative_path="module.py", start_line=1, end_line=1,
            expected_source="VALUE = 1",
            replacement="import os\n\nVALUE = 1",
        ),))

    assert tools.staged_edits == [original_stage]


def test_apply_edits_rejects_import_only_stage_before_review(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"module.py": "hash"}),
    )

    with pytest.raises(ValueError, match="import-only edit"):
        tools.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=1, end_line=1,
            expected_source="VALUE = 1", replacement="import os\n\nVALUE = 1",
        ),))

    assert tools.staged_edits == []


def test_complete_replacement_drops_noop_member_but_keeps_real_edits(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text(
        "VALUE = 1\nOTHER = 2\n", encoding="utf-8",
    )
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"module.py": "hash"}),
    )
    tools.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=1, end_line=1,
        expected_source="VALUE = 1", replacement="VALUE = 3",
    ),))

    result = tools.replace_staged_edits((
        ProposedEdit(
            relative_path="module.py", start_line=1, end_line=1,
            expected_source="VALUE = 1", replacement="VALUE = 4",
        ),
        ProposedEdit(
            relative_path="module.py", start_line=2, end_line=2,
            expected_source="OTHER = 2", replacement="OTHER = 2",
        ),
    ))

    assert result["dropped_noop_edit_count"] == 1
    assert len(tools.staged_edits) == 1
    assert tools.staged_edits[0].replacement == "VALUE = 4"


def test_rejected_import_only_stage_is_discarded_for_root_recovery(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"module.py": "hash"}),
    )
    # Compatibility cleanup can still encounter an import-only staged set
    # persisted by an older generation run. Construct that legacy state
    # directly; current apply_edits rejects it before review.
    tools.staged_edits.append(ProposedEdit(
        relative_path="module.py", start_line=1, end_line=1,
        expected_source="VALUE = 1", replacement="import os\n\nVALUE = 1",
    ))
    tools.staged_quality_rejected = True
    tools.staged_quality_error = "STAGED_PATCH_IMPORT_ONLY_WITHOUT_REACHABLE_BEHAVIOR"

    assert tools.discard_rejected_import_only_stage()
    assert tools.staged_edits == []
    assert tools.staged_quality_rejected
    assert tools.rejected_staged_paths == {"module.py"}


def test_quality_review_prohibited_path_cannot_be_resubmitted(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg" / "utils").mkdir(parents=True)
    path = root / "pkg" / "utils" / "validation.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(
            source_hashes={"pkg/utils/validation.py": "hash"},
        ),
    )
    original_stage = ProposedEdit(
        relative_path="pkg/utils/validation.py", start_line=1, end_line=1,
        expected_source="VALUE = 1", replacement="VALUE = 2",
    )
    tools.apply_edits((original_stage,))
    tools.staged_quality_rejected = True
    tools.prohibited_staged_paths.add("pkg/utils/validation.py")

    with pytest.raises(ValueError, match="prohibited resubmitting"):
        tools.replace_staged_edits((ProposedEdit(
            relative_path="pkg/utils/validation.py", start_line=1, end_line=1,
            expected_source="VALUE = 1", replacement="VALUE = 3",
        ),))

    assert tools.staged_edits == [original_stage]


def test_repair_tool_requires_new_direct_name_import_in_same_edit_set(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = "def ensure():\n    return True\n"
    (root / "module.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"module.py": "hash"}),
    )

    with pytest.raises(ValueError, match="unresolved direct name.*router"):
        tools.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=2, end_line=2,
            expected_source="    return True",
            replacement="    return router.allow()",
        ),))

    accepted = tools.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=1, end_line=2,
        expected_source=source.rstrip("\n"),
        replacement=(
            "from framework import router\n\n"
            "def ensure():\n"
            "    return router.allow()"
        ),
    ),))

    assert accepted["accepted"]


def test_unresolved_name_recovery_preserves_behavior_edit_and_import_candidate(
    tmp_path,
):
    root = tmp_path / "repo"
    (root / "framework").mkdir(parents=True)
    (root / "framework" / "db.py").write_text(
        "router = object()\n", encoding="utf-8",
    )
    (root / "consumer.py").write_text(
        "def ensure():\n    return True\n", encoding="utf-8",
    )
    (root / "existing.py").write_text(
        "from framework.db import router\n\n"
        "def route():\n    return router\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        root, max_files=10, deadline=Deadline.after(10),
    )
    tools = RepairToolExecutor(
        repository_root=root, repository_index=index,
    )

    with pytest.raises(ValueError, match="Import candidates"):
        tools.apply_edits((ProposedEdit(
            relative_path="consumer.py", start_line=2, end_line=2,
            expected_source="    return True",
            replacement="    return router.allow()",
        ),))

    assert len(tools.mechanical_recovery_anchors) == 1
    anchor = tools.mechanical_recovery_anchors[0]
    assert anchor["kind"] == "UNRESOLVED_DIRECT_NAME"
    assert anchor["unresolved_names"] == ("router",)
    assert anchor["rejected_behavior_edits"][0]["replacement"] == (
        "    return router.allow()"
    )
    assert any(
        item["statement"] == "from framework.db import router"
        for item in anchor["import_candidates"]
    )

    completed = tools.complete_unresolved_name_edits()

    assert completed["accepted"]
    assert completed["mechanical_completion"] == "UNRESOLVED_DIRECT_NAME"
    assert completed["resolved_names"] == ["router"]
    assert completed["inserted_imports"] == ["from framework.db import router"]
    staged_diff = tools.show_current_diff()["staged_diff"]
    assert "from framework.db import router" in staged_diff
    assert "return router.allow()" in staged_diff


def test_repair_tool_rejects_import_named_by_issue_when_execution_never_uses_it(
    tmp_path,
):
    root = tmp_path / "repo"
    root.mkdir()
    source = "def record():\n    return True\n"
    (root / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        root, max_files=10, deadline=Deadline.after(10),
    )
    tools = RepairToolExecutor(
        repository_root=root, repository_index=index,
    )

    with pytest.raises(ValueError, match="unused direct import.*router"):
        tools.apply_edits((
            ProposedEdit(
                relative_path="module.py", start_line=1, end_line=1,
                expected_source="def record():",
                replacement="from framework import router\n\ndef record():",
            ),
            ProposedEdit(
                relative_path="module.py", start_line=2, end_line=2,
                expected_source="    return True",
                replacement="    return False",
            ),
        ))

    assert not tools.staged_edits
    assert tools.mechanical_recovery_anchors[0]["kind"] == "UNUSED_DIRECT_IMPORT"
    assert tools.mechanical_recovery_anchors[0]["unused_names"] == ("router",)


def test_repair_tool_rejects_copied_class_constant_with_identical_value(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = (
        "class Storage:\n"
        "    \"\"\"Public storage contract.\"\"\"\n"
        "    FLAGS = 1\n\n"
        "    def url(self):\n"
        "        return '/static/'\n"
    )
    (root / "storage.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"storage.py": "hash"}),
    )

    with pytest.raises(ValueError, match="duplicate assignment.*Storage.FLAGS"):
        tools.apply_edits((ProposedEdit(
            relative_path="storage.py", start_line=1, end_line=1,
            expected_source="class Storage:",
            replacement="class Storage:\n    FLAGS = 1",
        ),))

    assert not tools.staged_edits


def test_quality_rejected_stage_is_discarded_but_retained_as_recovery_evidence(
    tmp_path,
):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text(
        "def normalize(value):\n    return value\n",
        encoding="utf-8",
    )
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"module.py": "hash"}),
    )
    tools.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=2, end_line=2,
        expected_source="    return value",
        replacement="    return None",
    ),))
    tools.show_current_diff()
    tools.staged_quality_rejected = True
    tools.staged_quality_error = "STAGED_PATCH_SELF_REJECTED"
    tools.staged_quality_rejected_version = tools.staged_edit_version

    assert tools.discard_quality_rejected_stage() is True
    assert tools.staged_edits == []
    assert "-    return value" in tools.last_rejected_staged_diff
    assert "+    return None" in tools.last_rejected_staged_diff
    assert tools.rejected_staged_paths == {"module.py"}
    assert tools.staged_quality_error == "STAGED_PATCH_SELF_REJECTED"


def test_binary_protocol_operand_wrapper_is_rejected_before_staging(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = (
        "class Predicate:\n"
        "    def _combine(self, other, connector):\n"
        "        if not getattr(other, 'conditional', False):\n"
        "            return NotImplemented\n"
        "        return connector(self, other)\n"
    )
    (root / "predicate.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"predicate.py": "hash"}),
    )

    with pytest.raises(ValueError, match="bypasses binary protocol dispatch"):
        tools.apply_edits((ProposedEdit(
            relative_path="predicate.py",
            start_line=2,
            end_line=5,
            expected_source=(
                "    def _combine(self, other, connector):\n"
                "        if not getattr(other, 'conditional', False):\n"
                "            return NotImplemented\n"
                "        return connector(self, other)"
            ),
            replacement=(
                "    def _combine(self, other, connector):\n"
                "        other = Predicate(other)\n"
                "        if not getattr(other, 'conditional', False):\n"
                "            return NotImplemented\n"
                "        return connector(self, other)"
            ),
        ),))


def test_binary_protocol_private_reverse_combine_is_rejected(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = (
        "class Predicate:\n"
        "    def _combine(self, other, connector):\n"
        "        if not isinstance(other, Predicate):\n"
        "            raise TypeError(other)\n"
        "        return connector(self, other)\n"
    )
    (root / "predicate.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"predicate.py": "hash"}),
    )

    with pytest.raises(ValueError, match="private _combine"):
        tools.apply_edits((ProposedEdit(
            relative_path="predicate.py",
            start_line=3,
            end_line=4,
            expected_source=(
                "        if not isinstance(other, Predicate):\n"
                "            raise TypeError(other)"
            ),
            replacement=(
                "        if not isinstance(other, Predicate):\n"
                "            if getattr(other, 'conditional', False):\n"
                "                return other._combine(self, connector)\n"
                "            raise TypeError(other)"
            ),
        ),))


def test_partial_rectangular_index_repair_requires_unsafe_sibling_fix(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = (
        "class Matrix:\n"
        "    def is_upper(self):\n"
        "        return all(self[i, j] for i in range(1, self.rows) for j in range(i))\n"
        "    def is_upper_band(self):\n"
        "        return all(self[i, j] for i in range(2, self.rows) for j in range(i - 1))\n"
    )
    (root / "matrix.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"matrix.py": "hash"}),
    )

    with pytest.raises(ValueError, match="repairs only one rectangular-index boundary"):
        tools.apply_edits((ProposedEdit(
            relative_path="matrix.py",
            start_line=3,
            end_line=3,
            expected_source=(
                "        return all(self[i, j] for i in range(1, self.rows) "
                "for j in range(i))"
            ),
            replacement=(
                "        return all(self[i, j] for i in range(1, self.rows) "
                "for j in range(min(i, self.cols)))"
            ),
        ),))


def test_complete_rectangular_index_sibling_repair_is_accepted(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source = (
        "class Matrix:\n"
        "    def is_upper(self):\n"
        "        return all(self[i, j] for i in range(1, self.rows) for j in range(i))\n"
        "    def is_upper_band(self):\n"
        "        return all(self[i, j] for i in range(2, self.rows) for j in range(i - 1))\n"
    )
    (root / "matrix.py").write_text(source, encoding="utf-8")
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={"matrix.py": "hash"}),
    )

    result = tools.apply_edits((
        ProposedEdit(
            relative_path="matrix.py", start_line=3, end_line=3,
            expected_source=(
                "        return all(self[i, j] for i in range(1, self.rows) "
                "for j in range(i))"
            ),
            replacement=(
                "        return all(self[i, j] for i in range(1, self.rows) "
                "for j in range(min(i, self.cols)))"
            ),
        ),
        ProposedEdit(
            relative_path="matrix.py", start_line=5, end_line=5,
            expected_source=(
                "        return all(self[i, j] for i in range(2, self.rows) "
                "for j in range(i - 1))"
            ),
            replacement=(
                "        return all(self[i, j] for i in range(2, self.rows) "
                "for j in range(min(i - 1, self.cols)))"
            ),
        ),
    ))

    assert result["accepted"] is True
    assert result["edit_count"] == 2


def test_mechanical_structural_check_tracks_difference_alias_across_branches(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    baseline.mkdir()
    trial.mkdir()
    before = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        self.state = existing.difference(field_names), False\n"
    )
    after = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        remaining = existing.difference(field_names)\n"
        "        if remaining:\n"
        "            self.state = remaining, False\n"
        "        else:\n"
        "            self.state = frozenset(field_names), True\n"
    )
    (baseline / "query.py").write_text(before, encoding="utf-8")
    (trial / "query.py").write_text(after, encoding="utf-8")
    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")
    assert structural.status == OutcomeStatus.FAIL
    assert "reintroduces the raw input" in structural.stderr


def test_mechanical_structural_check_rejects_empty_difference_mode_only_flip(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    baseline.mkdir()
    trial.mkdir()
    before = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        self.state = existing.difference(field_names), False\n"
    )
    after = before + (
        "        if not self.state[0]:\n"
        "            self.state = self.state[0], True\n"
    )
    (baseline / "query.py").write_text(before, encoding="utf-8")
    (trial / "query.py").write_text(after, encoding="utf-8")
    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")
    assert structural.status == OutcomeStatus.FAIL
    assert "only flips its companion mode/tag" in structural.stderr
    assert "incoming-only residual" in structural.stderr


def test_mechanical_structural_check_rejects_undeclared_literal_command_flag(tmp_path):
    baseline = tmp_path / "baseline"
    trial = tmp_path / "trial"
    for root in (baseline, trial):
        (root / "commands").mkdir(parents=True)
        (root / "commands" / "sync.py").write_text(
            "def configure(parser):\n"
            "    parser.add_argument('--database')\n"
            "def handle(**options):\n"
            "    return options['database']\n",
            encoding="utf-8",
        )
    (baseline / "caller.py").write_text("def create():\n    return None\n", encoding="utf-8")
    (trial / "caller.py").write_text(
        "def create():\n"
        "    return call_command('sync', database='default', sync=False)\n",
        encoding="utf-8",
    )
    actual = reconcile_actual_diff(baseline, trial)
    checks = run_mechanical_checks(trial, actual, baseline_root=baseline)
    structural = next(item for item in checks if item.kind == "STRUCTURAL")
    assert structural.status == OutcomeStatus.FAIL
    assert "does not declare option 'sync'" in structural.stderr


def test_repair_tool_rejects_any_undeclared_literal_command_option(tmp_path):
    root = tmp_path / "repo"
    (root / "commands").mkdir(parents=True)
    (root / "commands" / "sync.py").write_text(
        "def configure(parser):\n"
        "    parser.add_argument('--database')\n"
        "def handle(**options):\n"
        "    return options['database']\n",
        encoding="utf-8",
    )
    (root / "caller.py").write_text(
        "def create():\n"
        "    return None\n",
        encoding="utf-8",
    )
    tools = RepairToolExecutor(
        repository_root=root,
        repository_index=SimpleNamespace(source_hashes={
            "caller.py": "caller", "commands/sync.py": "sync",
        }),
    )

    with pytest.raises(ValueError, match="does not declare option 'invented'"):
        tools.apply_edits((ProposedEdit(
            relative_path="caller.py", start_line=2, end_line=2,
            expected_source="    return None",
            replacement=(
                "    return call_command(\n"
                "        'sync', database='default', invented=False,\n"
                "    )"
            ),
        ),))

    assert tools.staged_edits == []


def test_repair_context_keeps_primary_issue_separate_from_public_discussion() -> None:
    state = SimpleNamespace(
        runtime_config={
            "primary_issue": "PRIMARY CONTRACT",
            "generation_hints": "DISCUSSION DETAIL",
        },
        semantic_graph=SimpleNamespace(evidence={
            "witness": SimpleNamespace(
                content="SHUFFLED WITNESS",
                kind=SimpleNamespace(value="ISSUE_WITNESS"),
            ),
        }),
    )

    assert _issue_text(state) == "PRIMARY CONTRACT"


def test_mechanical_counterexample_preserves_failed_check_diagnostics() -> None:
    state = SimpleNamespace(
        outcomes={},
        checkpoint=SimpleNamespace(
            patch=SimpleNamespace(working_tree_hash="tree-hash"),
        ),
    )
    actual_diff = SimpleNamespace(
        diff_id="diff-id",
        canonical_diff_hash="diff-hash",
        fingerprint={"hash": "fingerprint"},
        changed_files=("pkg/module.py",),
        hunks=(SimpleNamespace(hunk_id="changed-hunk"),),
    )
    check = SimpleNamespace(
        check_id="import-check",
        kind="IMPORT",
        status=OutcomeStatus.FAIL,
        command=(sys.executable, "-c", "import pkg.module"),
        return_code=1,
        stdout="observed stdout",
        stderr="ImportError: circular import",
    )

    packet = _mechanical_packet(
        state, "transition-id", actual_diff, "MECHANICAL_FAILURE", (check,),
    )

    assert packet.actual_observation["reason"] == "MECHANICAL_FAILURE"
    assert packet.actual_observation["failed_checks"] == ({
        "check_id": "import-check",
        "kind": "IMPORT",
        "status": "FAIL",
        "command": (sys.executable, "-c", "import pkg.module"),
        "return_code": 1,
        "stdout": "observed stdout",
        "stderr": "ImportError: circular import",
    },)
    assert packet.failure_signature
    assert packet.causal_cut_ids == ("changed-hunk",)
    assert packet.suggested_action_families[:2] == (
        "move_import_inside_call_site", "lazy_local_import",
    )

    repeated = _mechanical_packet(
        state,
        "different-transition-id",
        SimpleNamespace(
            diff_id="different-diff-id",
            canonical_diff_hash="different-diff-hash",
            fingerprint={"hash": "different-fingerprint"},
            changed_files=("pkg/module.py",),
            hunks=(SimpleNamespace(hunk_id="different-hunk"),),
        ),
        "MECHANICAL_FAILURE",
        (check,),
    )
    assert repeated.failure_signature == packet.failure_signature
    assert repeated.counterexample_id == packet.counterexample_id


def test_circular_import_counterexample_drives_unbound_lazy_import_intent() -> None:
    state = SimpleNamespace(
        instance_id="fixture-instance",
        outcomes={},
        checkpoint=SimpleNamespace(
            checkpoint_id="checkpoint",
            patch=SimpleNamespace(working_tree_hash="tree-hash"),
        ),
    )
    packet = _mechanical_packet(
        state,
        "transition-id",
        SimpleNamespace(
            diff_id="diff-id",
            canonical_diff_hash="diff-hash",
            fingerprint={"hash": "fingerprint"},
            changed_files=("pkg/module.py",),
            hunks=(SimpleNamespace(hunk_id="changed-hunk"),),
        ),
        "MECHANICAL_FAILURE",
        (SimpleNamespace(
            check_id="import-check",
            kind="IMPORT",
            status=OutcomeStatus.FAIL,
            command=(sys.executable, "-c", "import pkg.module"),
            return_code=1,
            stdout="",
            stderr=(
                "ImportError: cannot import name 'value' from partially "
                "initialized module 'pkg.module' (most likely due to a "
                "circular import)"
            ),
        ),),
    )
    state.active_binding_graph = SimpleNamespace(
        diff_hash="working-diff", units={}, unresolved_gaps=(),
    )
    state.counterexamples = [packet]
    state.prohibited_mechanisms = set()
    state.requirement_coverage = None

    intent = next_untried_repair_intent(state)

    assert intent is not None
    assert intent.mechanism_id == "move_import_inside_call_site"
    assert intent.files_to_modify == ("pkg/module.py",)
    assert intent.counterexample_ids == (packet.counterexample_id,)


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
    binding = build_legacy_active_binding_product(
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


def test_staged_edit_set_must_be_previewed_and_can_be_replaced(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        root, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=root, repository_index=index,
    )
    executor.apply_edits((ProposedEdit(
        "module.py", 1, 1, "VALUE = 1", "VALUE = 2",
    ),))

    with pytest.raises(ValueError, match="has not been reviewed"):
        executor.finish_revision("premature")

    first_preview = executor.show_current_diff()
    assert "-VALUE = 1" in first_preview["staged_diff"]
    assert "+VALUE = 2" in first_preview["staged_diff"]
    executor.replace_staged_edits((ProposedEdit(
        "module.py", 1, 1, "VALUE = 1", "VALUE = 3",
    ),))

    with pytest.raises(ValueError, match="has not been reviewed"):
        executor.finish_revision("stale review")

    final_preview = executor.show_current_diff()
    assert "+VALUE = 3" in final_preview["staged_diff"]
    assert "+VALUE = 2" not in final_preview["staged_diff"]
    assert executor.finish_revision("reviewed final edit set")["finished"]


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
        if "finish_revision" in available and "apply_edits" not in available:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "final-review", "type": "function",
                    "function": {
                        "name": "finish_revision",
                        "arguments": json.dumps({
                            "summary": "reviewed the exact staged diff",
                        }),
                    },
                }],
            }
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
        self.forced_choices: list[str | dict] = []

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
                "id": "allowed-final-blocker", "type": "function",
                "function": {
                    "name": "declare_blocker",
                    "arguments": json.dumps({
                        "reason": "the available evidence does not ground an edit",
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


class _TransientThenEditTransport:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages, schemas):
        self.calls += 1
        if self.calls == 1:
            raise OSError("temporary DNS failure")
        if self.calls == 2:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "retry-edit", "type": "function",
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
                "id": f"retry-finish-{self.calls}", "type": "function",
                "function": {
                    "name": "finish_revision",
                    "arguments": json.dumps({"summary": "retry produced patch"}),
                },
            }],
        }


def test_generator_final_turn_limits_browsing_without_forcing_an_edit(tmp_path):
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

    assert len(transport.available_tools) >= 3
    assert "search_code" in transport.available_tools[0]
    synthesis_tools = [
        available for available in transport.available_tools
        if "apply_edits" in available
    ]
    assert synthesis_tools
    assert "search_code" not in synthesis_tools[-1]
    assert {
        "apply_edits", "replace_staged_edits",
        "finish_revision", "declare_blocker",
    } <= synthesis_tools[-1]
    assert "finish_revision" in transport.available_tools[-1]
    assert "search_code" not in transport.available_tools[-1]
    assert state.transition_index == 1
    assert state.checkpoint.patch.canonical_diff


def test_production_final_turn_does_not_force_apply_edits(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    transport = _ForcedChoiceTransport()
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=1),
        generator_agent=PersistentDeepSeekAgent(transport, max_tool_turns=4),
        implementation_root=tmp_path,
    )

    state = controller.analyze(
        Instance(
            "forced-final-choice", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert transport.forced_choices == ["required", "required", "required"]
    assert state.runtime_metrics["initial_nonprogress_recovery_count"] == 2
    assert all(choice != {
        "type": "function", "function": {"name": "apply_edits"},
    } for choice in transport.forced_choices)
    assert state.transition_index == 0
    assert state.checkpoint.patch.canonical_diff == ""
    assert not any(
        call.get("function", {}).get("name") == "apply_edits"
        for message in state.generator_conversation.messages
        for call in message.get("tool_calls", ())
    )
    assert any(
        call.get("function", {}).get("name") == "declare_blocker"
        for message in state.generator_conversation.messages
        for call in message.get("tool_calls", ())
    )


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
    assert state.runtime_metrics.get("submitted_generator_revisions", 0) <= 2
    assert transport.calls >= 1


def test_target_recovery_unavailable_still_calls_generator(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    transport = _ContextlessTransport()
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=10),
        generator_agent=PersistentDeepSeekAgent(transport, max_tool_turns=6),
        implementation_root=tmp_path,
    )

    state, certificate = controller.run(
        Instance(
            "target-recovery-blocked", str(repository), "base",
            "Improve the public API documentation wording.",
        ),
        run_root=tmp_path / "run",
    )

    assert certificate.status == "GENERATOR_NONPROGRESS"
    assert state.termination_status == "GENERATOR_NONPROGRESS"
    assert not state.target_recovery.targets
    assert state.target_recovery.directed_reproduction_requests <= 1
    assert transport.calls > 0
    assert state.transition_index == 0
    assert state.runtime_metrics.get("confirmed_revision_count", 0) == 0
    assert state.runtime_metrics.get("submitted_generator_revisions", 0) == 0


def test_transient_initial_generator_failure_retries_and_keeps_patch(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    transport = _TransientThenEditTransport()
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=1),
        generator_agent=PersistentDeepSeekAgent(transport, max_tool_turns=3),
        implementation_root=tmp_path,
    )

    state = controller.analyze(
        Instance(
            "transient-initial-generator", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert state.runtime_metrics["initial_generator_retry_count"] == 1
    assert state.transition_index == 1
    assert state.checkpoint.patch.canonical_diff
    assert "return []" in state.checkpoint.patch.canonical_diff


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


def test_untrusted_frontier_does_not_revise_single_working_patch(
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
            "pkg.api.public(x) has incorrect behavior, but the expected public "
            "observation is not specified.",
        ),
        run_root=tmp_path / "run",
    )

    assert certificate.status == "EVIDENCE_LIMITED_COMPLETE"
    assert state.transition_index == 1
    assert [item.decision for item in state.repair_history] == [Decision.COMMIT]
    assert state.checkpoint.patch.version == 1
    assert state.runtime_metrics["deepseek_initial_generation_count"] == 1
    assert state.runtime_metrics["deepseek_repair_count"] == 0
    assert state.runtime_metrics["confirmed_revision_count"] == 0
    assert len(state.generator_conversation.accepted_patch_hashes) == 1
    assert len(state.generator_conversation.rejected_patch_hashes) == 0
    assert "return [1]" in state.checkpoint.patch.canonical_diff
    assert "return []" not in state.checkpoint.patch.canonical_diff
    assert state.patch_trajectory.first_patch.patch_hash == (
        state.patch_trajectory.best_evidence_patch.patch_hash
    )
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
    binding = build_legacy_active_binding_product(
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
    generation = GenerationInstance.from_public_record(public)
    assert generation.issue == "fix public behavior"
    for field in (
        "FAIL_TO_PASS", "PASS_TO_PASS", "test_patch", "gold_patch",
        "official_harness_output",
    ):
        assert not hasattr(generation, field)
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
            "public-regression", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )

    assert state.transition_index == 1
    assert state.checkpoint.patch.canonical_diff
    assert state.repair_history[-1].decision == Decision.COMMIT
    assert state.checkpoint.patch.status == "TARGET_FIXED_REGRESSION_OPEN"
    assert not state.repair_history[-1].avoid
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
    assert state.repair_history[-1].old_target_deficit == float(
        len(state.target_recovery.targets)
    )
    assert state.repair_history[-1].new_target_deficit == 0.0
    assert state.target_deficit() == 0.0
    comparisons = state.repair_history[-1].graph_delta["public_check_comparisons"]
    assert {item["classification"] for item in comparisons} == {"TARGET_FIXED"}
    assert state.runtime_metrics["public_target_fixed_commands"]
    assert state.runtime_metrics["public_target_evidence_unit_ids"]
    assert any(item.candidate_cut_node_ids for item in state.causal_slices)
    assert any(
        unit.cut_status == "CUT_RESOLVED"
        for unit in state.active_binding_graph.executable_targets
    )
    assert state.runtime_metrics["dicc_status"] == state.dicc_certificate.status.value
    persisted_dicc = ArtifactStore(tmp_path / "run" / "artifacts").latest(
        "public-target", "dicc_certificate",
    )
    assert persisted_dicc is not None
    assert persisted_dicc.payload == state.dicc_certificate.to_dict()

    restored = controller.rebuild(tmp_path / "run")
    assert len(restored.target_recovery.targets) == len(state.target_recovery.targets)
    assert tuple(item.to_dict() for item in restored.check_comparisons) == tuple(
        item.to_dict() for item in state.check_comparisons
    )
    assert restored.dicc_certificate.status == state.dicc_certificate.status
    assert restored.dicc_certificate.executed_challenge_ids == (
        state.dicc_certificate.executed_challenge_ids
    )
    assert restored.checkpoint.executed_target_deficit == (
        state.checkpoint.executed_target_deficit
    )
    assert restored.generator_conversation.current_working_diff == (
        state.checkpoint.patch.canonical_diff
    )
    assert restored.patch_trajectory is not None
    assert restored.patch_trajectory.first_patch.patch_hash == (
        state.patch_trajectory.first_patch.patch_hash
    )
    assert restored.patch_trajectory.best_evidence_patch.patch_hash == (
        state.patch_trajectory.best_evidence_patch.patch_hash
    )


def test_revision_cannot_erase_nonempty_working_patch(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=1),
        generator_agent=PersistentDeepSeekAgent(
            _RegressingTransport(), max_tool_turns=3,
        ),
        implementation_root=tmp_path,
    )
    state = controller.analyze(
        Instance(
            "working-patch-erasure", str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=tmp_path / "run",
    )
    incumbent_diff = state.checkpoint.patch.canonical_diff

    result = evaluate_patch_revision(state, _revision(
        "causal_slice_rewrite",
        (ProposedEdit(
            relative_path="pkg/api.py", start_line=40, end_line=40,
            expected_source="    return []",
            replacement="    return normalize(value)",
        ),),
    ))

    assert result.decision == Decision.ROLLBACK
    assert not result.accepted
    assert "WORKING_PATCH_ERASURE" in result.reason
    assert state.checkpoint.patch.canonical_diff == incumbent_diff
    assert state.verified_safe_patch is not None
    assert state.verified_safe_patch.canonical_diff == incumbent_diff


def test_edit_ablation_reexecutes_targets_before_retaining_removal(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    target_check = (
        sys.executable, "-c",
        "import sys; from pkg.api import public; sys.exit(public([1]) != [])",
    )
    instance_id = "ablation-public-recheck"
    run_root = tmp_path / "run"
    controller = ReachPatchController(
        config=ReachPatchConfig(
            max_submitted_revisions=1,
            mechanical_commands=(target_check,),
            enable_ablation=True,
            max_ablation_groups=1,
        ),
        generator_agent=PersistentDeepSeekAgent(
            _RegressingTransport(), max_tool_turns=3,
        ),
        implementation_root=tmp_path,
    )

    state, certificate = controller.run(
        Instance(
            instance_id, str(repository), "base",
            "For every x, pkg.api.public(x) must return [].",
        ),
        run_root=run_root,
    )

    artifact = ArtifactStore(run_root / "artifacts").latest(
        instance_id, "edit_retention_ablation",
    )
    assert certificate.status == "REACHED"
    assert state.checkpoint.patch.status == "REACHED"
    assert artifact is None


def test_semantic_ambiguity_still_reaches_initial_generator(tmp_path):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(repository / "tests")
    controller = ReachPatchController(
        config=ReachPatchConfig(max_submitted_revisions=2),
        generator_agent=PersistentDeepSeekAgent(
            _RegressingTransport(), max_tool_turns=3,
        ),
        implementation_root=tmp_path,
    )
    state = controller.analyze(
        Instance(
            "ambiguous-generation", str(repository), "base",
            "For every x, pkg.api.public(x) must return []. "
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
