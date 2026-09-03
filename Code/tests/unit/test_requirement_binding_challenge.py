from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.legacy_graph

from reachpatch.binding_graph import build_binding_graph, confirm_bindings_from_execution
from reachpatch.challenge_graph.execute import execute_challenge_round
from reachpatch.challenge_graph.materialize import (
    materialize_challenge_graph, update_challenge_graph_after_diff,
)
from reachpatch.challenge_graph.input_recipes import compile_input_recipe
from reachpatch.execution.worktree import diff_between
from reachpatch.models.base import stable_id
from reachpatch.models.evidence import (
    ActualDiff, DiffHunk, EvidenceRecord, ExecutableCheck, ExecutableOracle,
    LockedCheckSet, ObservationBundle, ObservationContract, OutcomeStatus,
    PairClassification, PairedTraceBundle, PublicEvidence,
    RunObservation, TraceBundle, discover_diff_public_checks,
    public_evidence_from_instance,
)
from reachpatch.models.graphs import (
    BindingStatus, ChallengeStatus, ContextRequest, GraphBudget, GraphStack,
    PathClass, ProgramGraph, ProgramNode, ProgramNodeKind, RequirementGraph,
    RequirementLeaf, RequirementVariable,
)
from reachpatch.models.reach_avoid import (
    CheckpointEvidence, ChallengeSelection, GeneratorSession, ReachAvoidPhase,
    ReachAvoidState, StateCheckpoint,
)
from reachpatch.program_graph import (
    build_initial_program_graph, update_program_graph_after_diff,
)
from reachpatch.oracle.resolve import resolve_oracle
from reachpatch.requirement_graph import build_requirement_graph
from reachpatch.requirement_graph.update import promote_diff_partitions
from reachpatch.repair.objective import compile_repair_objective


def test_issue_example_is_witness_not_narrowed_requirement():
    issue = "`combine` must accept every public operand. For example, combine([], 1) returns 1."
    graph = build_requirement_graph(issue, PublicEvidence())
    assert len(graph.leaves) == 1
    leaf = next(iter(graph.leaves.values()))
    assert leaf.quantifier == "FOR_ALL"
    assert leaf.witness_ids
    assert all("[]" not in item for item in leaf.domain_constraints)


def test_explicit_accept_contract_has_executable_issue_oracle(tmp_path):
    issue = "`Handler` must accept a callable callback."
    repository = tmp_path / "oracle-repository"
    repository.mkdir()
    evidence = public_evidence_from_instance(issue, (), {}, repository)
    graph = build_requirement_graph(issue, evidence)
    leaf = next(iter(graph.leaves.values()))

    resolution = resolve_oracle(leaf, evidence, None)

    assert leaf.expected_observation.comparator == "succeeds"
    assert leaf.expected_observation.expected == {"exit_code": 0}
    assert resolution.oracle is not None
    assert resolution.oracle.authority == "B"
    assert resolution.oracle.executable


def test_qualified_requirement_does_not_bind_every_same_named_method(tmp_path):
    repository = tmp_path / "repository"
    base = tmp_path / "base"
    repository.mkdir()
    base.mkdir()
    (repository / "api.py").write_text(
        "class Target:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n"
        "\n"
        "class Unrelated:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n",
        encoding="utf-8",
    )
    leaf = RequirementLeaf(
        requirement_id="target-init",
        kind="TARGET_BEHAVIOR",
        quantifier="FOR_ALL",
        variables=(),
        domain_constraints=(),
        preconditions=(),
        operation="Target.__init__",
        expected_observation=ObservationContract("accepts value", None),
        exception_contract=None,
        preservation=False,
        authority="B",
        evidence_ids=("issue",),
        witness_ids=(),
        status=OutcomeStatus.UNKNOWN,
        hard=True,
    )
    requirement = RequirementGraph(
        leaves={leaf.requirement_id: leaf},
        challenge_partitions={},
        evidence_hash="evidence",
    )
    actual = diff_between(base, repository)
    program = build_initial_program_graph(
        repository, "Target.__init__ must accept a value.", actual, (),
        GraphBudget(max_files=10),
    )
    binding = build_binding_graph(requirement, program, actual, ())

    assert len(binding.units) == 1
    symbols = {
        program.nodes[node_id].symbol
        for unit in binding.units.values()
        for node_id in unit.program_symbol_ids
        if node_id in program.nodes
    }
    assert "Target.__init__" in symbols
    assert "Unrelated.__init__" not in symbols


def test_dotted_class_operation_binds_real_changed_class_and_method(tmp_path):
    base = tmp_path / "base-dotted-class"
    repository = tmp_path / "repo-dotted-class"
    base.mkdir()
    repository.mkdir()
    old = (
        "class FilePathField:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n"
    )
    new = old.replace("self.path = path", "self.path = str(path)")
    (base / "fields.py").write_text(old, encoding="utf-8")
    (repository / "fields.py").write_text(new, encoding="utf-8")
    actual = diff_between(base, repository)
    leaf = RequirementLeaf(
        "req-field", "TARGET_BEHAVIOR", "FOR_ALL", (), (), (),
        "models.FilePathField", ObservationContract("accepts paths", None),
        None, False, "B", ("issue",), (), OutcomeStatus.UNKNOWN, True,
    )
    requirement = RequirementGraph({leaf.requirement_id: leaf})
    program = build_initial_program_graph(
        repository, "models.FilePathField must accept paths", actual, (),
        GraphBudget(max_files=8, max_nodes=96),
    )

    binding = build_binding_graph(requirement, program, actual, ())

    assert binding.units
    bound_symbols = {
        program.nodes[node_id].symbol
        for unit in binding.units.values()
        for node_id in unit.program_symbol_ids
        if node_id in program.nodes
    }
    assert {"FilePathField", "FilePathField.__init__"} <= bound_symbols


def test_wrong_file_diff_retains_disjoint_requirement_path(tmp_path):
    """A candidate route remains available when p0 edits an unrelated file.

    The ProgramGraph receives the Requirement operation as a bounded lookup
    seed.  It therefore parses the target file alongside the changed file,
    rather than treating the absence of a diff overlap as absence of a route.
    """
    base = tmp_path / "base-disjoint"
    working = tmp_path / "working-disjoint"
    base.mkdir()
    working.mkdir()
    target = "def combine(left, right):\n    return left + right\n"
    unrelated_before = "def unrelated(value):\n    return value\n"
    unrelated_after = "def unrelated(value):\n    return str(value)\n"
    for directory in (base, working):
        (directory / "target.py").write_text(target, encoding="utf-8")
    (base / "other.py").write_text(unrelated_before, encoding="utf-8")
    (working / "other.py").write_text(unrelated_after, encoding="utf-8")

    actual = diff_between(base, working)
    leaf = RequirementLeaf(
        "req-combine", "TARGET_BEHAVIOR", "FOR_ALL", (), (), (),
        "combine", ObservationContract("equals 2", 2, comparator="equals"),
        None, False, "B", ("issue",), (), OutcomeStatus.UNKNOWN, True,
    )
    requirement = RequirementGraph({leaf.requirement_id: leaf})
    program = build_initial_program_graph(
        working, "combine must return the sum.", actual, (),
        GraphBudget(max_files=8, max_nodes=96),
        relevant_symbols=(leaf.operation,),
    )

    binding = build_binding_graph(requirement, program, actual, ())

    assert any(node.symbol == "combine" for node in program.nodes.values())
    assert len(binding.units) == 1
    unit = next(iter(binding.units.values()))
    assert unit.changed_hunk_ids == ()
    assert unit.alignment_status == "DISJOINT"
    assert any(gap.gap_type == "DIFF_DISJOINT" for gap in binding.gaps)


def test_changed_callable_predicate_materializes_executable_class_recipe(tmp_path):
    base = tmp_path / "base-callable-recipe"
    working = tmp_path / "working-callable-recipe"
    base.mkdir()
    working.mkdir()
    old = (
        "class Handler:\n"
        "    def __init__(self, callback):\n"
        "        self.callback = callback\n\n"
        "    def snapshot(self):\n"
        "        return self.callback\n"
    )
    new = (
        "class Handler:\n"
        "    def __init__(self, callback):\n"
        "        if callable(callback):\n"
        "            callback = callback()\n"
        "        self.callback = callback\n\n"
        "    def snapshot(self):\n"
        "        return self.callback\n"
    )
    (base / "api.py").write_text(old, encoding="utf-8")
    (working / "api.py").write_text(new, encoding="utf-8")
    issue = "`api.Handler` must accept a callable callback."
    evidence = public_evidence_from_instance(issue, (), {}, base)
    requirement = build_requirement_graph(issue, evidence)
    actual = diff_between(base, working)
    program = build_initial_program_graph(
        working, issue, actual, (), GraphBudget(max_files=8),
    )
    promote_diff_partitions(requirement, program, actual)
    binding = build_binding_graph(requirement, program, actual, ())
    binding, challenge, _ = materialize_challenge_graph(
        requirement, program, binding, evidence,
    )

    branch = next(
        cell for cell in challenge.active_cells()
        if cell.input_recipe.kind == "BRANCH_TRUE"
    )
    impact = tuple(
        cell for cell in challenge.active_cells()
        if cell.origin == "IMPACT_CONE"
    )
    completed = __import__("subprocess").run(
        branch.execution_scenario.command,
        cwd=working,
        capture_output=True,
        text=True,
        check=False,
    )
    assert branch.oracle.authority == "B"
    assert branch.oracle.executable
    assert branch.input_recipe.concrete_input["__kwargs__"]["callback"][
        "__reachpatch_factory__"
    ] == "CALLABLE"
    # No public input exercises Handler.snapshot, so an impact replay would be
    # invented evidence.  The branch itself is executable, while the consumer
    # remains an explicit recovery frontier.
    assert not impact
    assert any(
        gap.gap_type.startswith("INPUT_RECIPE_FRONTIER:STATE_READER:")
        for gap in binding.gaps
    )
    assert completed.returncode == 0, completed.stderr


def test_changed_ifexp_materializes_and_executes_both_callable_partitions(tmp_path):
    base = tmp_path / "base-ifexp"
    working = tmp_path / "working-ifexp"
    run_root = tmp_path / "run-ifexp"
    base.mkdir()
    working.mkdir()
    run_root.mkdir()
    old = (
        "class Resolver:\n"
        "    def __init__(self, callback):\n"
        "        self.callback = callback\n\n"
        "    def resolve(self):\n"
        "        return self.callback\n"
    )
    new = old.replace(
        "return self.callback",
        "return self.callback() if callable(self.callback) else self.callback",
    )
    (base / "api.py").write_text(old, encoding="utf-8")
    (working / "api.py").write_text(new, encoding="utf-8")
    issue = "`Resolver.resolve` must accept a callable callback."
    evidence = public_evidence_from_instance(issue, (), {}, base)
    requirement = build_requirement_graph(issue, evidence)
    actual = diff_between(base, working)
    program = build_initial_program_graph(
        working, issue, actual, (), GraphBudget(max_files=8),
    )
    promote_diff_partitions(requirement, program, actual)
    binding = build_binding_graph(requirement, program, actual, ())
    binding, challenge, _ = materialize_challenge_graph(
        requirement, program, binding, evidence,
    )
    stack = GraphStack(
        actual.patch_hash, 0, requirement, program, binding, challenge,
    )
    stack.validate()

    required_kinds = {
        "BRANCH_TRUE", "BRANCH_FALSE", "WRAPPER_TRUTHY", "WRAPPER_FALSY",
    }
    cells_by_kind = {
        cell.input_recipe.kind: cell
        for cell in challenge.active_cells()
        if cell.input_recipe.kind in required_kinds
    }
    assert not cells_by_kind
    frontier_kinds = {
        kind for kind in required_kinds
        if any(
            gap.gap_type.startswith(f"INPUT_RECIPE_FRONTIER:{kind}:")
            for gap in binding.gaps
        )
    }
    assert frontier_kinds == required_kinds
    assert any(
        action.value == "MATERIALIZE_BRANCH_PARTITION"
        for gap in binding.gaps
        for action in gap.next_recovery_actions
    )


def test_dotted_dunder_never_uses_terminal_only_fallback(tmp_path):
    base = tmp_path / "base-dotted-dunder"
    repository = tmp_path / "repo-dotted-dunder"
    base.mkdir()
    repository.mkdir()
    old = "class Unrelated:\n    def __init__(self, value):\n        self.value = value\n"
    new = old.replace("self.value = value", "self.value = str(value)")
    (base / "api.py").write_text(old, encoding="utf-8")
    (repository / "api.py").write_text(new, encoding="utf-8")
    actual = diff_between(base, repository)
    leaf = RequirementLeaf(
        "req-package-init", "TARGET_BEHAVIOR", "FOR_ALL", (), (), (),
        "package.__init__", ObservationContract("initializes package", None),
        None, False, "B", ("issue",), (), OutcomeStatus.UNKNOWN, True,
    )
    requirement = RequirementGraph({leaf.requirement_id: leaf})
    program = build_initial_program_graph(
        repository, "package.__init__ must initialize the package", actual, (),
        GraphBudget(max_files=8),
    )

    binding = build_binding_graph(requirement, program, actual, ())

    assert not binding.units
    assert binding.gaps[0].gap_type == "NO_PROGRAM_SYMBOL"


def test_binding_gap_recovery_materializes_real_diff_referenced_unit(tmp_path):
    base = tmp_path / "base-gap-recovery"
    repository = tmp_path / "repo-gap-recovery"
    base.mkdir()
    repository.mkdir()
    old = "class FilePathField:\n    def __init__(self, path):\n        self.path = path\n"
    new = old.replace("self.path = path", "self.path = str(path)")
    (base / "fields.py").write_text(old, encoding="utf-8")
    (repository / "fields.py").write_text(new, encoding="utf-8")
    actual = diff_between(base, repository)
    leaf = RequirementLeaf(
        "req-recovery", "TARGET_BEHAVIOR", "FOR_ALL", (), (), (),
        "models.FilePathField", ObservationContract("accepts paths", None),
        None, False, "B", ("issue",), (), OutcomeStatus.UNKNOWN, True,
    )
    requirement = RequirementGraph({leaf.requirement_id: leaf})
    check = ExecutableCheck(
        "check-field", ("python", "check.py"), "TARGET", "B",
        requirement_ids=(leaf.requirement_id,),
        symbol_references=("FilePathField",),
    )
    empty_program = ProgramGraph(actual.patch_hash, "base", {}, {}, {}, {})
    before = build_binding_graph(requirement, empty_program, actual, (check,))
    action = before.gaps[0].next_recovery_actions[0]
    request = ContextRequest("recover-field", action.value, "FilePathField", 1)

    delta = update_program_graph_after_diff(
        empty_program, repository, actual, (), (request,),
        GraphBudget(max_files=8, max_nodes=96), (check,),
    )
    after = build_binding_graph(requirement, delta.graph, actual, (check,))

    assert before.gaps[0].gap_type == "NO_PROGRAM_SYMBOL"
    assert after.units
    assert not any(gap.gap_type == "NO_PROGRAM_SYMBOL" for gap in after.gaps)


def test_same_patch_context_expansion_preserves_challenge_execution(tmp_path):
    repository = tmp_path / "repository"
    base = tmp_path / "base"
    repository.mkdir()
    base.mkdir()
    (repository / "api.py").write_text(
        "def combine(left, right):\n"
        "    return left + right\n",
        encoding="utf-8",
    )
    issue = (
        "`combine` must support all public operands. "
        "For example, combine([], [1]) returns [1]."
    )
    evidence = PublicEvidence(records=(
        EvidenceRecord("issue-evidence", "issue", "B", issue),
    ))
    requirement = build_requirement_graph(issue, evidence)
    actual = diff_between(base, repository)
    program = build_initial_program_graph(
        repository, issue, actual, (), GraphBudget(max_files=10),
    )
    binding = build_binding_graph(requirement, program, actual, ())
    binding, challenge, _ = materialize_challenge_graph(
        requirement, program, binding, evidence,
    )
    selected = next(iter(challenge.cells.values()))
    executed = replace(
        selected,
        baseline_outcome=OutcomeStatus.FAIL,
        patched_outcome=OutcomeStatus.PASS,
        trace_bundle_id="paired-trace",
        stability_runs=2,
        terminal_status=ChallengeStatus.PASS,
    )
    challenge = replace(
        challenge,
        cells={**challenge.cells, selected.challenge_id: executed},
    )

    _, updated, _ = update_challenge_graph_after_diff(
        challenge,
        binding,
        requirement,
        program,
        binding,
        evidence,
        (selected.binding_id,),
    )

    retained = updated.cells[selected.challenge_id]
    assert retained.terminal_status is ChallengeStatus.PASS
    assert retained.stability_runs == 2
    assert retained.trace_bundle_id == "paired-trace"


def test_issue_witness_materializes_executable_challenge_without_narrowing(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "api.py").write_text(
        "def combine(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    issue = (
        "`combine` must support all public operands. "
        "For example, combine([], [1]) returns [1]."
    )
    evidence = PublicEvidence(records=(
        EvidenceRecord("issue-evidence", "issue", "B", issue),
    ))
    requirement = build_requirement_graph(issue, evidence)
    leaf = next(iter(requirement.leaves.values()))
    actual = diff_between(empty, repository)
    program = build_initial_program_graph(
        repository, issue, actual, (), GraphBudget(max_files=10),
    )
    binding = build_binding_graph(requirement, program, actual, ())
    updated_binding, challenge, _ = materialize_challenge_graph(
        requirement, program, binding, evidence,
    )
    witnesses = [
        cell for cell in challenge.active_cells()
        if cell.input_recipe.kind == "ISSUE_WITNESS"
    ]
    assert leaf.quantifier == "FOR_ALL"
    assert leaf.witness_ids
    assert witnesses[0].input_recipe.concrete_input == {
        "__args__": [[], [1]], "__kwargs__": {},
    }
    assert witnesses[0].oracle.authority == "B"
    assert not any(
        gap.gap_type == "NO_EXECUTABLE_CHECK"
        or gap.gap_type.startswith("INPUT_RECIPE_FRONTIER:PUBLIC_REPLAY:")
        for gap in updated_binding.gaps
    )


def test_multiline_repl_witness_excludes_output_and_traceback(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "api.py").write_text(
        "class Writer:\n"
        "    def write(self, value, *, header=False):\n"
        "        if not header:\n"
        "            raise TypeError('header unsupported')\n"
        "        return value\n",
        encoding="utf-8",
    )
    base = tmp_path / "base"
    base.mkdir()
    issue = """Please support header output.

```Python
>>> from api import Writer
>>> writer = Writer()
>>> writer.write('row', header=True)
row
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
TypeError: old behavior
```
"""
    evidence = public_evidence_from_instance(issue, (), {}, repository)
    record = evidence.records[0]
    witnesses = record.metadata["issue_witnesses"]
    assert len(witnesses) == 1
    assert "writer.write('row', header=True)" in witnesses[0]["script"]
    assert "__reachpatch_trace_reset__" in witnesses[0]["script"]
    assert "Traceback" not in witnesses[0]["script"]
    assert "\nrow\n" not in witnesses[0]["script"]
    assert witnesses[0]["expected"] == {"exit_code": 0, "stdout": "row\n"}

    requirement = build_requirement_graph(issue, evidence)
    leaf = next(iter(requirement.leaves.values()))
    assert leaf.operation == "write"
    assert leaf.expected_observation.expected == "Please support header output."
    actual = diff_between(base, repository)
    program = build_initial_program_graph(
        repository, issue, actual, (), GraphBudget(max_files=10),
    )
    binding = build_binding_graph(requirement, program, actual, ())
    updated_binding, challenge, _ = materialize_challenge_graph(
        requirement, program, binding, evidence,
    )
    cells = [
        cell for cell in challenge.active_cells()
        if cell.input_recipe.kind == "ISSUE_WITNESS"
    ]
    assert cells
    assert cells[0].execution_scenario.command[:2] == ("python", "-c")
    assert cells[0].oracle.authority == "B"
    assert cells[0].oracle.expected == {"exit_code": 0, "stdout": "row\n"}
    assert not any(
        gap.gap_type == "NO_EXECUTABLE_CHECK"
        for gap in updated_binding.gaps
    )


def test_free_form_readme_is_not_a_normative_requirement():
    evidence = PublicEvidence(api_contracts=(EvidenceRecord(
        "readme", "documentation:README.md", "B",
        "Project home is https://example.invalid. Contributions are welcome.",
    ),))
    graph = build_requirement_graph("`render` must support headers.", evidence)
    assert len(graph.leaves) == 1
    assert next(iter(graph.leaves.values())).operation == "render"


def test_ipython_witness_is_extracted_without_console_output(tmp_path):
    (tmp_path / "api.py").write_text("def calc(value): return value\n", encoding="utf-8")
    issue = """`calc` should accept an empty list.
```
In [1]: from api import calc
In [2]: calc([])
Out[2]: []
```
"""
    evidence = public_evidence_from_instance(issue, (), {}, tmp_path)
    witnesses = evidence.records[0].metadata["issue_witnesses"]
    assert len(witnesses) == 1
    assert witnesses[0]["operation"] == "calc"
    assert "Out[2]" not in witnesses[0]["script"]


def test_long_issue_builds_bounded_contract_and_executable_plain_witness(tmp_path):
    source = tmp_path / "package" / "assets.py"
    source.parent.mkdir()
    source.write_text(
        "class Media:\n"
        "    @staticmethod\n"
        "    def merge(*lists):\n"
        "        return [item for values in lists for item in values]\n",
        encoding="utf-8",
    )
    (tmp_path / "framework.py").write_text(
        "class Widget:\n"
        "    @property\n"
        "    def media(self):\n"
        "        values = getattr(self, 'Media').js\n"
        "        return values\n",
        encoding="utf-8",
    )
    issue = """Merging several media declarations loses dependency order
Description
from framework import Widget
class Basic(Widget):
    class Media:
        js = ['base.js']
class Rich(Widget):
    class Media:
        js = ['base.js', 'extra.js', 'color.js']

The implementation should resolve the files into the order base.js, extra.js, color.js. However, accessing Rich().media results in the wrong order.

Public maintainer hints:
One proposal says callers should sort by length.
Another proposal says Media.merge should return a set.
The implementation must use a solver.
"""
    evidence = public_evidence_from_instance(issue, (), {}, tmp_path)
    graph = build_requirement_graph(issue, evidence)
    targets = [leaf for leaf in graph.leaves.values() if not leaf.preservation]
    assert len(targets) == 1
    leaf = targets[0]
    assert leaf.operation == "Media.merge"
    assert leaf.expected_observation.relation.endswith(
        "the order base.js, extra.js, color.js."
    )
    assert not any("sort by length" in item.expected_observation.relation for item in targets)
    assert leaf.witness_ids
    witness = evidence.records[0].metadata["issue_witnesses"][0]
    assert "class Basic" in witness["script"]
    assert "_reachpatch_expected_order" in witness["script"]
    assert witness["expected"] == {"exit_code": 0}
    empty = tmp_path / "empty"
    empty.mkdir()
    actual = diff_between(empty, tmp_path)
    program = build_initial_program_graph(
        tmp_path, issue, actual, (), GraphBudget(max_files=10),
    )
    binding = build_binding_graph(graph, program, actual, ())
    _, challenges, _ = materialize_challenge_graph(
        graph, program, binding, evidence,
    )
    witness_challenge = next(
        cell for cell in challenges.active_cells()
        if cell.input_recipe.call_mode == "ISSUE_WITNESS_SCRIPT"
    )
    assert witness_challenge.binding_id in binding.units
    assert "Rich().media" in witness_challenge.execution_scenario.command[-1]
    assert "Media.merge(" not in witness_challenge.execution_scenario.command[-1]
    direct_completed = __import__("subprocess").run(
        witness_challenge.execution_scenario.command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct_completed.returncode == 0, direct_completed.stderr
    completed = __import__("subprocess").run(
        ("python", "-c", witness["script"]),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_ineffective_working_patch_witness_becomes_confirmed_failure(tmp_path):
    base = tmp_path / "base"
    working = tmp_path / "working"
    run_root = tmp_path / "run"
    base.mkdir()
    working.mkdir()
    run_root.mkdir()
    (base / "api.py").write_text(
        "class Dependency:\n"
        "    @staticmethod\n"
        "    def merge(left, right):\n"
        "        return left + [item for item in right if item not in left]\n",
        encoding="utf-8",
    )
    (working / "api.py").write_text(
        "class Dependency:\n"
        "    @staticmethod\n"
        "    def merge(*groups):\n"
        "        return sorted(set(item for group in groups for item in group))\n",
        encoding="utf-8",
    )
    issue = """Merging dependency groups must preserve declared order
from api import Dependency
class First:
    class Spec:
        values = ['last.js']
class Second:
    class Spec:
        values = ['first.js']
class Third:
    class Spec:
        values = ['first.js', 'middle.js', 'last.js']

The operation should produce the order first.js, middle.js, last.js. However, accessing Dependency.merge(First.Spec.values, Second.Spec.values, Third.Spec.values) results in the wrong order.
"""
    evidence = public_evidence_from_instance(issue, (), {}, base)
    requirement = build_requirement_graph(issue, evidence)
    actual = diff_between(base, working)
    program = build_initial_program_graph(
        working, issue, actual, (), GraphBudget(max_files=10),
    )
    promote_diff_partitions(requirement, program, actual)
    binding = build_binding_graph(requirement, program, actual, ())
    binding, challenge, _ = materialize_challenge_graph(
        requirement, program, binding, evidence,
    )
    stack = GraphStack(
        actual.patch_hash, 0, requirement, program, binding, challenge,
    )
    stack.validate()
    witness_challenge = next(
        cell for cell in challenge.active_cells()
        if cell.input_recipe.call_mode == "ISSUE_WITNESS_SCRIPT"
    )
    checkpoint = StateCheckpoint(
        "working", None, str(working), actual.patch_hash,
        actual.canonical_diff, stack.graph_hashes(), "",
        CheckpointEvidence(True, True, 0, 0, 0, 0, 1, 0),
        (), (), (witness_challenge.challenge_id,), "WORKING", 0,
    )
    state = ReachAvoidState(
        "instance", "run", base, "base", run_root, stack,
        checkpoint, None, {checkpoint.checkpoint_id: checkpoint},
        ObservationBundle(), [], LockedCheckSet(), [], {},
        GeneratorSession("session"), None, 0, 0, 0, 0, {},
        ReachAvoidPhase.CHALLENGE, None, 60, 60, GraphBudget(max_files=10),
    )
    before_hashes = stack.graph_hashes()
    result = execute_challenge_round(
        state, ChallengeSelection((witness_challenge.challenge_id,)), base, working,
    )
    assert result.executions[0].classification is PairClassification.TARGET_STILL_FAILING
    assert result.executions[0].stable_runs == 2
    assert result.counterexamples
    assert result.confirmed_failures
    assert result.counterexamples[0].causal_cut_ids
    assert result.confirmed_failures[0].binding_id == witness_challenge.binding_id
    assert all(
        result.updated_graph_stack.graph_hashes()[name] != digest
        for name, digest in before_hashes.items()
    )
    state.graph_stack = result.updated_graph_stack
    state.counterexamples.extend(result.counterexamples)
    state.confirmed_failures.extend(result.confirmed_failures)
    objective = compile_repair_objective(state, result.confirmed_failures[0])
    assert objective.primary_requirement["operation"] == "Dependency.merge"
    assert objective.counterexamples == result.counterexamples
    assert objective.bindings[0]["binding_id"] == witness_challenge.binding_id
    assert objective.actual_hunks
    assert objective.causal_cuts
    obligation = next(
        item for item in objective.validation_obligations
        if item.command == witness_challenge.execution_scenario.command
    )
    structured = obligation.concrete_input["__reachpatch_issue_witness__"]
    assert structured["target_expression"].startswith("Dependency.merge(")
    assert "direct_input" not in structured


def test_only_issue_related_docstring_contract_is_promoted():
    evidence = PublicEvidence(api_contracts=(
        EvidenceRecord(
            "render-contract", "source:api.py:1", "B",
            "`render` public signature is render(value). Public docstring: Must return text.",
            metadata={"symbol": "render", "kind": "docstring_and_type_signature"},
        ),
        EvidenceRecord(
            "unrelated-contract", "source:api.py:5", "B",
            "`delete` public signature is delete(). Public docstring: Must remove data.",
            metadata={"symbol": "delete", "kind": "docstring_and_type_signature"},
        ),
    ))
    graph = build_requirement_graph("`render` should support headers.", evidence)
    preservation = [leaf for leaf in graph.leaves.values() if leaf.preservation]
    assert preservation
    assert {leaf.operation for leaf in preservation} == {"render"}


def test_diff_public_check_discovery_requires_real_symbol_reference(tmp_path):
    (tmp_path / "api.py").write_text(
        "def render(value):\n    return value\n", encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_api.py").write_text(
        "from api import render\n\n"
        "def test_render_text():\n    assert render('x') == 'x'\n\n"
        "def test_unrelated():\n    assert True\n",
        encoding="utf-8",
    )
    diff = ActualDiff(
        "diff", "patch",
        (DiffHunk("h", "api.py", 1, 2, 1, 2, "", (
            " def render(value):", "+    return str(value)",
        )),),
        ("api.py",), (),
    )
    checks = discover_diff_public_checks(tmp_path, diff)
    assert len(checks) == 1
    assert checks[0].role == "PRESERVATION"
    assert checks[0].symbol_references == ("render",)
    assert checks[0].command[-1] == "tests/test_api.py::test_render_text"


def test_django_diff_check_uses_rootless_qualified_unittest_label(tmp_path):
    source = tmp_path / "django" / "forms" / "widgets.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class Media:\n    def merge(self, values):\n        return values\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    (tests / "forms_tests" / "tests").mkdir(parents=True)
    (tests / "runtests.py").write_text("", encoding="utf-8")
    (tests / "forms_tests" / "tests" / "test_media.py").write_text(
        "from django.forms.widgets import Media\n\n"
        "class MediaTests:\n"
        "    def test_merge(self):\n"
        "        assert Media().merge([1]) == [1]\n",
        encoding="utf-8",
    )
    diff = ActualDiff(
        "diff", "patch",
        (DiffHunk("h", "django/forms/widgets.py", 1, 3, 1, 3, "", (
            " class Media:", "     def merge(self, values):", "+        return list(values)",
        )),),
        ("django/forms/widgets.py",), ("Media", "merge"),
    )

    checks = discover_diff_public_checks(tmp_path, diff)

    assert len(checks) == 1
    assert checks[0].command == (
        "python", "tests/runtests.py",
        "forms_tests.tests.test_media.MediaTests.test_merge",
    )


def test_diff_symbols_include_every_changed_enclosing_function(tmp_path):
    base = tmp_path / "base"
    working = tmp_path / "working"
    base.mkdir()
    working.mkdir()
    old = (
        "class Writer:\n"
        "    def __init__(self):\n"
        "        self.rows = []\n\n"
        "    def write(self):\n"
        "        return self.rows[0]\n"
    )
    new = old.replace(
        "self.rows = []", "self.rows = ['header']",
    ).replace(
        "return self.rows[0]", "return self.rows[-1]",
    )
    (base / "writer.py").write_text(old, encoding="utf-8")
    (working / "writer.py").write_text(new, encoding="utf-8")
    actual = diff_between(base, working)
    assert set(actual.changed_symbols) >= {"Writer", "__init__", "write"}


def test_preservation_check_is_not_widened_to_adjacent_input(state_factory):
    state = state_factory(preservation_status=ChallengeStatus.PENDING)
    hunk = DiffHunk(
        "hunk", "calc.py", 1, 2, 1, 3, "",
        (" def calc():", "+    if value:", "     return 2"),
    )
    result = promote_diff_partitions(
        state.graph_stack.requirement_graph,
        state.graph_stack.program_graph,
        ActualDiff("diff", state.graph_stack.patch_hash, (hunk,), ("calc.py",), ("calc",)),
    )
    preservation_ids = {
        leaf.requirement_id
        for leaf in result.graph.leaves.values() if leaf.preservation
    }
    assert not any(
        partition.requirement_id in preservation_ids
        for partition in result.graph.challenge_partitions.values()
    )


def _binding_fixture(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def calc(value):\n    return value\n", encoding="utf-8")
    check = ExecutableCheck(
        "check-calc", ("python", "check.py"), "TARGET", "A",
        symbol_references=("calc",),
    )
    hunk = DiffHunk(
        "calc-hunk", "calc.py", 1, 0, 1, 2, "",
        ("+def calc(value):", "+    return value"), ("calc",),
    )
    actual = ActualDiff("diff", "patch", (hunk,), ("calc.py",), ("calc",))
    program = build_initial_program_graph(
        tmp_path, "`calc` must return its input", actual, (check,), GraphBudget(),
    )
    leaf = RequirementLeaf(
        "req", "RETURN_CONTRACT", "FOR_ALL", (), (), (), "calc",
        ObservationContract("calc must return its input", "input"), None,
        False, "A", ("evidence",), (), OutcomeStatus.UNKNOWN, True,
    )
    requirement = RequirementGraph({leaf.requirement_id: leaf})
    binding = build_binding_graph(requirement, program, actual, (check,))
    return requirement, program, binding


def test_static_overlap_is_not_execution_confirmed(tmp_path):
    _, _, binding = _binding_fixture(tmp_path)
    assert binding.units
    assert {item.status for item in binding.units.values()} == {BindingStatus.STATIC_ACTIONABLE}


def test_requirement_id_without_trace_does_not_confirm_binding(tmp_path):
    requirement, program, binding = _binding_fixture(tmp_path)
    unit = next(iter(binding.units.values()))
    observation = RunObservation(OutcomeStatus.PASS, 0, "", "", 0)
    empty_trace = TraceBundle("trace", "tree", ("python",), observation, (), ())
    execution = PairedTraceBundle(
        "pair", "check-calc", "challenge", "patch", empty_trace, empty_trace,
        PairClassification.PASS_PRESERVED, "oracle", "A",
        requirement.leaves["req"].expected_observation.relation, 2,
    )
    delta = confirm_bindings_from_execution(binding, program, requirement, (execution,))
    assert delta.graph.units[unit.binding_id].status is BindingStatus.STATIC_ACTIONABLE


def test_paired_trace_confirms_binding(tmp_path):
    requirement, program, binding = _binding_fixture(tmp_path)
    unit = next(iter(binding.units.values()))
    symbol = unit.program_symbol_ids[0]
    before = TraceBundle(
        "before", "base", ("python",),
        RunObservation(OutcomeStatus.FAIL, 1, "", "failure", 0),
        (symbol,), (symbol,), stable_runs=2,
    )
    after = TraceBundle(
        "after", "working", ("python",),
        RunObservation(OutcomeStatus.PASS, 0, "", "", 0),
        (symbol,), (symbol,), stable_runs=2,
    )
    execution = PairedTraceBundle(
        "pair", "check-calc", "challenge", "patch", before, after,
        PairClassification.TARGET_FIXED, "oracle", "A",
        "the public executable contract completed successfully", 2,
        oracle_contract_id=ObservationContract(
            "the public executable contract completed successfully",
            {"exit_code": 0}, observable="process", comparator="EQUALS",
        ).contract_id,
    )
    delta = confirm_bindings_from_execution(binding, program, requirement, (execution,))
    assert delta.graph.units[unit.binding_id].status is BindingStatus.TARGET_PASSING
    assert delta.confirmed_binding_ids == (unit.binding_id,)


def test_binding_id_is_consistent_across_modules(state_factory):
    state = state_factory()
    for cell in state.graph_stack.challenge_graph.cells.values():
        assert cell.binding_id in state.graph_stack.binding_graph.units


def test_binding_gap_generates_recovery_action(tmp_path):
    leaf = RequirementLeaf(
        "req", "TARGET_BEHAVIOR", "CONTRACT", (), (), (), "missing_symbol",
        ObservationContract("must succeed", True), None, False, "A", (), (),
        OutcomeStatus.UNKNOWN, True,
    )
    requirement = RequirementGraph({leaf.requirement_id: leaf})
    program = ProgramGraph("patch", "base", {}, {}, {}, {})
    actual = ActualDiff("diff", "patch", (), (), ())
    binding = build_binding_graph(requirement, program, actual, ())
    assert binding.gaps[0].next_recovery_actions


def test_branch_partition_materializes_recipe(state_factory):
    state = state_factory()
    requirement = state.graph_stack.requirement_graph.leaves["req-target"]
    binding = state.graph_stack.binding_graph.units["binding-target"]
    path = state.graph_stack.program_graph.path_classes["path-calc"]
    public = PublicEvidence(checks=(ExecutableCheck(
        "check-target", ("python", "check.py"), "TARGET", "A",
        requirement_ids=(requirement.requirement_id,), concrete_input=[],
    ),))
    result = compile_input_recipe(
        requirement, path, binding, state.graph_stack.program_graph, public,
        partition_kind="NONEMPTY",
    )
    assert result.recipe is not None
    assert result.recipe.concrete_input
    assert "one graph constraint" in result.recipe.derivation[-1]


def test_requirement_variables_drive_direct_call_argument_order(state_factory):
    state = state_factory()
    requirement = replace(
        state.graph_stack.requirement_graph.leaves["req-target"],
        variables=(RequirementVariable("left"), RequirementVariable("right")),
    )
    binding = state.graph_stack.binding_graph.units["binding-target"]
    path = state.graph_stack.program_graph.path_classes["path-calc"]
    public = PublicEvidence(checks=(ExecutableCheck(
        "check-target", ("python", "check.py"), "TARGET", "A",
        requirement_ids=(requirement.requirement_id,),
        concrete_input={"right": 2, "left": 1},
    ),))
    result = compile_input_recipe(
        requirement, path, binding, state.graph_stack.program_graph, public,
        partition_kind="DIRECT_CALLER",
    )
    assert result.recipe is not None
    assert result.recipe.concrete_input == {"__args__": [1, 2]}
    assert "left, right" in result.recipe.derivation[1]


def test_impact_cone_materializes_direct_caller_and_return_consumer(tmp_path):
    base = tmp_path / "base"
    working = tmp_path / "working"
    base.mkdir()
    working.mkdir()
    original = (
        "def target(value):\n"
        "    return value + 1\n\n"
        "def caller(value):\n"
        "    return target(value)\n"
    )
    revised = original.replace("value + 1", "value + 2")
    (base / "api.py").write_text(original, encoding="utf-8")
    (working / "api.py").write_text(revised, encoding="utf-8")
    actual = diff_between(base, working)
    check = ExecutableCheck(
        "check-preservation", ("python", "check.py"), "PRESERVATION", "A",
        symbol_references=("target",), concrete_input=1,
        source_evidence_ids=("public-preservation",),
    )
    program = build_initial_program_graph(
        working, "`target` must return 3", actual, (check,),
        GraphBudget(max_files=10),
    )
    leaf = RequirementLeaf(
        "req", "PRESERVATION", "FOR_ALL",
        (RequirementVariable("value"),), (), (), "target",
        ObservationContract("existing target callers remain stable", None), None,
        True, "A", ("public-preservation",), (), OutcomeStatus.UNKNOWN, True,
    )
    requirement = RequirementGraph({leaf.requirement_id: leaf})
    binding = build_binding_graph(requirement, program, actual, (check,))
    _, challenge, _ = materialize_challenge_graph(
        requirement, program, binding, PublicEvidence(checks=(check,)),
    )
    impact_cells = [
        cell for cell in challenge.active_cells() if cell.origin == "IMPACT_CONE"
    ]
    assert {cell.input_recipe.kind for cell in impact_cells}.issuperset({
        "DIRECT_CALLER", "RETURN_CONSUMER",
    })
    assert all("'caller'" in " ".join(cell.execution_scenario.command) for cell in impact_cells)


def test_target_only_binding_does_not_create_baseline_preservation_leaf(state_factory):
    state = state_factory()
    assert not any(
        any(item.startswith("impact-preservation:") for item in leaf.evidence_ids)
        for leaf in state.graph_stack.requirement_graph.leaves.values()
    )


def test_untrusted_oracle_is_exploration_only():
    oracle = ExecutableOracle("oracle", "PROVISIONAL", "guess", 1, False)
    assert not oracle.trusted and not oracle.executable


def test_untrusted_public_check_is_not_promoted_to_trusted():
    leaf = RequirementLeaf(
        "req", "RETURN_CONTRACT", "CONTRACT", (), (), (), "calc",
        ObservationContract("calc returns 2", 2), None, False,
        "B", ("issue",), (), OutcomeStatus.UNKNOWN, True,
    )
    evidence = PublicEvidence(checks=(ExecutableCheck(
        "probe", ("python", "probe.py"), "TARGET", "PROVISIONAL",
        symbol_references=("calc",),
    ),))
    result = resolve_oracle(leaf, evidence, None)
    assert result.exploration_only
    assert result.oracle.authority == "PROVISIONAL"


def test_stale_patch_challenge_is_not_active(state_factory):
    state = state_factory()
    cell = state.graph_stack.challenge_graph.cells["challenge-target"]
    state.graph_stack.challenge_graph.cells[cell.challenge_id] = replace(cell, patch_hash="old")
    assert state.graph_stack.challenge_graph.active_cells() == ()
