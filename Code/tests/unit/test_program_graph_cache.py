from __future__ import annotations

from pathlib import Path

from reachpatch.execution.worktree import diff_between
from reachpatch.models.base import content_hash
from reachpatch.models.evidence import (
    ExecutableCheck, ObservationContract, OutcomeStatus, PublicEvidence,
    RunObservation, TraceBundle,
)
from reachpatch.models.graphs import (
    BindingGraph, ChallengeGraph, ContextRequest, GraphBudget, GraphStack,
    ProgramEdgeKind, ProgramGraph, ProgramNode, ProgramNodeKind, RequirementGraph,
    RequirementLeaf,
)
from reachpatch.program_graph import (
    RepositoryIndex,
    build_initial_program_graph,
    match_trace_nodes,
    update_program_graph_after_diff,
)
from reachpatch.reach_avoid.graph_stack import update_graph_stack_after_diff
from reachpatch.requirement_graph.update import promote_diff_partitions


def _repository(root: Path) -> Path:
    root.mkdir()
    (root / "changed.py").write_text(
        "def changed(value):\n    return value + 1\n", encoding="utf-8",
    )
    (root / "stable.py").write_text(
        "def stable(value):\n    return value * 2\n", encoding="utf-8",
    )
    return root


def test_repository_index_reuses_symbol_tokens_for_same_content(tmp_path):
    repository = _repository(tmp_path / "repo")
    first = RepositoryIndex.build(repository, "commit-a", ("changed",), 10)
    second = RepositoryIndex.build(repository, "commit-a", ("changed",), 10)
    assert first.cache_hits == 0
    assert second.cache_hits == 2
    assert second.symbol_files["changed"] == ("changed.py",)


def test_repository_index_expands_symbol_beyond_bounded_initial_window(tmp_path):
    repository = tmp_path / "repo-expand"
    repository.mkdir()
    for index in range(25):
        (repository / f"module_{index:02d}.py").write_text(
            f"VALUE_{index} = {index}\n", encoding="utf-8",
        )
    target = repository / "nested" / "implementation.py"
    target.parent.mkdir()
    target.write_text(
        "class Expressions:\n"
        "    def rename_table_references(self, old, new):\n"
        "        return new\n",
        encoding="utf-8",
    )
    index = RepositoryIndex.build(
        repository, "commit-a", ("rename_table_references",), max_files=1,
    )
    assert not index.symbol_files["rename_table_references"]

    expanded = index.expand_symbol("rename_table_references")

    assert expanded == ("nested/implementation.py",)


def test_trace_node_matching_indexes_paths_and_collapses_repeated_lines():
    nodes = {
        "class": ProgramNode(
            "class", ProgramNodeKind.CLASS, "module.py", "Container", 1, 100, True,
        ),
        "function": ProgramNode(
            "function", ProgramNodeKind.FUNCTION, "module.py",
            "Container.run", 10, 20, True,
        ),
        "block": ProgramNode(
            "block", ProgramNodeKind.BASIC_BLOCK, "module.py",
            "Container.run:block", 12, 12, True,
        ),
    }
    graph = ProgramGraph("patch", "base", nodes, {}, {}, {"module.py": "hash"})
    trace = TraceBundle(
        "trace", "tree", ("python", "module.py"),
        RunObservation(OutcomeStatus.PASS, 0, "", "", 0.1),
        (), (), tuple("module.py:12" for _ in range(2000)),
    )

    ordered, hit = match_trace_nodes(graph, trace)

    assert ordered == ("block",)
    assert hit == {"class", "function", "block"}


def test_initial_program_graph_reuses_ast_and_cfg_cache(tmp_path):
    repository = _repository(tmp_path / "repo")
    empty = tmp_path / "empty"
    empty.mkdir()
    actual = diff_between(empty, repository)
    checks = (ExecutableCheck(
        "check-changed", ("python", "changed.py"), "TARGET", "A",
        symbol_references=("changed", "stable"),
    ),)
    budget = GraphBudget(max_files=10)
    first = build_initial_program_graph(
        repository, "`changed` must return a value", actual, checks, budget,
        base_commit="commit-a",
    )
    second = build_initial_program_graph(
        repository, "`changed` must return a value", actual, checks, budget,
        base_commit="commit-a",
    )
    assert first.files_reparsed >= 1
    assert second.files_reparsed == 0
    assert second.cache_hits >= first.cache_hits + 1
    assert second.graph_hash() == first.graph_hash()


def test_initial_graph_prioritizes_changed_function_at_end_of_large_file(tmp_path):
    base = tmp_path / "base-late"
    repository = tmp_path / "repo-late"
    base.mkdir()
    repository.mkdir()
    prefix = "".join(
        f"def filler_{index}(value):\n    return value + {index}\n\n"
        for index in range(120)
    )
    old_target = (
        "class FilePathField:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n"
    )
    new_target = old_target.replace("self.path = path", "self.path = str(path)")
    (base / "fields.py").write_text(prefix + old_target, encoding="utf-8")
    (repository / "fields.py").write_text(prefix + new_target, encoding="utf-8")
    actual = diff_between(base, repository)
    budget = GraphBudget(max_files=8, max_nodes=96, max_edges=300)

    graph = build_initial_program_graph(
        repository,
        "`models.FilePathField` must accept every documented path value.",
        actual,
        (),
        budget,
        base_commit="commit-late",
    )

    symbols = {node.symbol for node in graph.nodes.values()}
    assert "FilePathField" in symbols
    assert "FilePathField.__init__" in symbols
    assert any(
        path.entrypoint == "FilePathField.__init__"
        for path in graph.path_classes.values()
    )
    assert len(graph.nodes) <= budget.max_nodes


def test_conditional_expression_builds_branch_paths_and_diff_partitions(tmp_path):
    base = tmp_path / "base-ifexp"
    repository = tmp_path / "repo-ifexp"
    base.mkdir()
    repository.mkdir()
    (base / "choice.py").write_text(
        "def choose(value):\n    return value\n", encoding="utf-8",
    )
    (repository / "choice.py").write_text(
        "def choose(value):\n"
        "    return value() if callable(value) else value\n",
        encoding="utf-8",
    )
    actual = diff_between(base, repository)
    graph = build_initial_program_graph(
        repository, "`choose` must accept a deferred value", actual, (),
        GraphBudget(max_files=4, max_nodes=128, max_edges=400),
        base_commit="commit-ifexp",
    )

    branch = next(
        node for node in graph.nodes.values()
        if node.kind is ProgramNodeKind.BRANCH
        and node.metadata.get("predicate") == "callable(value)"
    )
    outcomes = {
        guard.rsplit(":", 1)[-1]
        for path in graph.path_classes.values()
        if branch.node_id in path.node_ids
        for guard in path.ordered_guard_outcomes
        if guard.startswith(f"{branch.node_id}:")
    }
    edge_kinds = {
        edge.kind for edge in graph.edges.values()
        if edge.source_id == branch.node_id
    }
    assert outcomes == {"TRUE", "FALSE"}
    assert {ProgramEdgeKind.CONTROL_TRUE, ProgramEdgeKind.CONTROL_FALSE} <= edge_kinds

    requirement = RequirementGraph({
        "req": RequirementLeaf(
            "req", "TARGET_BEHAVIOR", "FOR_ALL", (), (), (), "choose",
            ObservationContract("choose resolves deferred values", "resolved"),
            None, False, "B", ("issue-evidence",), (),
            OutcomeStatus.UNKNOWN, True,
        ),
    })
    delta = promote_diff_partitions(requirement, graph, actual)
    kinds = {
        partition.kind for partition in delta.graph.challenge_partitions.values()
    }
    assert {"BRANCH_TRUE", "BRANCH_FALSE"} <= kinds
    assert {"WRAPPER_TRUTHY", "WRAPPER_FALSY"} <= kinds


def test_incremental_graph_reparses_only_changed_file(tmp_path):
    repository = _repository(tmp_path / "repo")
    base = tmp_path / "base"
    base.mkdir()
    for name in ("changed.py", "stable.py"):
        (base / name).write_text((repository / name).read_text(encoding="utf-8"), encoding="utf-8")
    initial = diff_between(base, repository)
    checks = (ExecutableCheck(
        "check-changed", ("python", "changed.py"), "TARGET", "A",
        symbol_references=("changed",),
    ),)
    budget = GraphBudget(max_files=10)
    previous = build_initial_program_graph(
        repository, "`changed` and `stable`", initial, checks, budget,
        base_commit="commit-a",
    )
    (repository / "changed.py").write_text(
        "def changed(value):\n    return value + 2\n", encoding="utf-8",
    )
    revision = diff_between(base, repository)
    delta = update_program_graph_after_diff(
        previous, repository, revision, (), (), budget, checks,
    )
    assert delta.files_reparsed == 1
    assert delta.graph.file_hashes["stable.py"] == previous.file_hashes["stable.py"]
    assert delta.graph.nodes


def test_saturated_local_graph_retains_changed_function_on_incremental_update(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "changed.py").write_text(
        "def changed(value):\n    return value + 1\n", encoding="utf-8",
    )
    (repository / "large.py").write_text(
        "def large(value):\n"
        + "".join(f"    value_{index} = value + {index}\n" for index in range(200))
        + "    return value\n",
        encoding="utf-8",
    )
    base = tmp_path / "base"
    base.mkdir()
    (base / "changed.py").write_text(
        "def changed(value):\n    return value\n", encoding="utf-8",
    )
    (base / "large.py").write_text(
        (repository / "large.py").read_text(encoding="utf-8"), encoding="utf-8",
    )
    budget = GraphBudget(max_files=10, max_nodes=60, max_edges=200)
    check = ExecutableCheck(
        "check", ("python", "changed.py"), "TARGET", "A",
        symbol_references=("changed", "large"),
    )
    previous = build_initial_program_graph(
        repository, "`changed` must work with `large`", diff_between(base, repository),
        (check,), budget, base_commit="commit-a",
    )
    (repository / "changed.py").write_text(
        "def changed(value):\n"
        "    if value is None:\n"
        "        return 0\n"
        "    return value + 2\n",
        encoding="utf-8",
    )
    delta = update_program_graph_after_diff(
        previous, repository, diff_between(base, repository), (), (), budget, (check,),
    )
    changed_nodes = [
        node for node in delta.graph.nodes.values()
        if node.path == "changed.py"
    ]
    assert any(
        node.kind is ProgramNodeKind.FUNCTION
        and node.symbol.split(".")[-1] == "changed"
        for node in changed_nodes
    )
    assert any(node.kind is ProgramNodeKind.BRANCH for node in changed_nodes)
    assert len(delta.graph.nodes) <= budget.max_nodes


def test_incremental_graph_refocuses_late_changed_function(tmp_path):
    base = tmp_path / "base-late-revision"
    repository = tmp_path / "repo-late-revision"
    base.mkdir()
    repository.mkdir()
    prefix = "".join(
        f"def filler_{index}(value):\n    return value + {index}\n\n"
        for index in range(120)
    )
    original = (
        "class FilePathField:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n"
    )
    initial = original.replace("self.path = path", "self.path = str(path)")
    revised = (
        "class FilePathField:\n"
        "    def __init__(self, path):\n"
        "        if path is None:\n"
        "            path = ''\n"
        "        self.path = path\n"
    )
    (base / "fields.py").write_text(prefix + original, encoding="utf-8")
    (repository / "fields.py").write_text(prefix + initial, encoding="utf-8")
    budget = GraphBudget(max_files=8, max_nodes=96, max_edges=300)
    previous = build_initial_program_graph(
        repository, "`models.FilePathField` must accept paths.",
        diff_between(base, repository), (), budget, base_commit="commit-late",
    )
    (repository / "fields.py").write_text(prefix + revised, encoding="utf-8")

    delta = update_program_graph_after_diff(
        previous, repository, diff_between(base, repository), (), (), budget, (),
    )

    late_nodes = tuple(
        node for node in delta.graph.nodes.values()
        if node.symbol.startswith("FilePathField")
    )
    assert any(node.symbol == "FilePathField.__init__" for node in late_nodes)
    assert any(node.kind is ProgramNodeKind.BRANCH for node in late_nodes)
    assert any(
        path.entrypoint == "FilePathField.__init__"
        for path in delta.graph.path_classes.values()
    )
    assert delta.files_reparsed == 1


def test_repeated_frontier_request_is_retained_without_reparse(tmp_path):
    repository = _repository(tmp_path / "repo")
    empty = tmp_path / "empty"
    empty.mkdir()
    actual = diff_between(empty, repository)
    previous = build_initial_program_graph(
        repository, "`changed` must return a value", actual, (),
        GraphBudget(max_files=10), base_commit="commit-a",
    )
    symbol_id = next(
        node.node_id for node in previous.nodes.values()
        if node.kind is ProgramNodeKind.FUNCTION
        and node.symbol.split(".")[-1] == "changed"
    )
    request = ContextRequest(
        "request", "EXPAND_DIRECT_CALLER", symbol_id, 1,
    )
    first = update_program_graph_after_diff(
        previous, repository, actual, (), (request,), GraphBudget(max_files=10), (),
    )
    second = update_program_graph_after_diff(
        first.graph, repository, actual, (), (request,), GraphBudget(max_files=10), (),
    )
    assert second.graph.frontier_requests == (request,)
    assert second.files_reparsed == 0
    assert not second.added_node_ids


def test_revision_graph_update_never_calls_initial_builder(tmp_path, monkeypatch):
    repository = _repository(tmp_path / "repo")
    base = tmp_path / "base"
    base.mkdir()
    for name in ("changed.py", "stable.py"):
        (base / name).write_text(
            (repository / name).read_text(encoding="utf-8"), encoding="utf-8",
        )
    actual = diff_between(base, repository)
    previous_program = build_initial_program_graph(
        repository, "`changed` must preserve `stable`", actual, (),
        GraphBudget(max_files=10), base_commit="commit-a",
    )
    requirement = RequirementGraph({})
    binding = BindingGraph(
        previous_program.patch_hash, requirement.graph_hash(),
        previous_program.graph_hash(), {}, (),
    )
    challenge = ChallengeGraph(
        previous_program.patch_hash, binding.graph_hash(), {},
    )
    previous = GraphStack(
        previous_program.patch_hash, 0, requirement, previous_program,
        binding, challenge,
    )
    (repository / "changed.py").write_text(
        "def changed(value):\n    return value + 3\n", encoding="utf-8",
    )
    revision = diff_between(base, repository)
    monkeypatch.setattr(
        "reachpatch.reach_avoid.graph_stack.build_initial_program_graph",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("initial builder called during revision")
        ),
    )
    updated = update_graph_stack_after_diff(
        previous, revision, repository, base,
        "`changed` must return a value", PublicEvidence(),
        GraphBudget(max_files=10),
    )
    assert updated.program_graph.files_reparsed == 1
    assert (
        updated.program_graph.file_hashes["stable.py"]
        == previous_program.file_hashes["stable.py"]
    )


def test_incremental_graph_removes_reverted_local_file(tmp_path):
    base = _repository(tmp_path / "base")
    repository = tmp_path / "repo"
    repository.mkdir()
    for name in ("changed.py", "stable.py"):
        (repository / name).write_text(
            (base / name).read_text(encoding="utf-8"), encoding="utf-8",
        )
    (repository / "changed.py").write_text(
        "def changed(value):\n    return value + 2\n", encoding="utf-8",
    )
    first_diff = diff_between(base, repository)
    initial = build_initial_program_graph(
        repository, "`changed` must return a value", first_diff, (),
        GraphBudget(max_files=10), base_commit="commit-a",
    )
    (repository / "changed.py").write_text(
        (base / "changed.py").read_text(encoding="utf-8"), encoding="utf-8",
    )
    reverted = diff_between(base, repository)
    delta = update_program_graph_after_diff(
        initial, repository, reverted, (), (), GraphBudget(max_files=10), (),
    )
    assert delta.graph.file_hashes["changed.py"] == content_hash(
        (base / "changed.py").read_bytes().hex()
    )
    assert delta.graph.file_hashes["changed.py"] != initial.file_hashes["changed.py"]
    assert any(node.path == "changed.py" for node in delta.graph.nodes.values())


def test_local_graph_emits_alias_external_effect_and_resolved_static_call(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def callee(value):\n    return value\n\n"
        "def caller(value):\n    alias = callee\n    print(alias(value))\n    return alias(value)\n",
        encoding="utf-8",
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    graph = build_initial_program_graph(
        repository, "`caller` must return a value", diff_between(empty, repository),
        (), GraphBudget(max_files=10), base_commit="commit-a",
    )
    assert any(node.kind is ProgramNodeKind.EXTERNAL_EFFECT for node in graph.nodes.values())
    assert any(edge.kind is ProgramEdgeKind.ALIAS for edge in graph.edges.values())
    assert any(edge.kind is ProgramEdgeKind.CALLS for edge in graph.edges.values())


def test_impact_cone_excludes_ambiguous_may_call_and_name_overlap(tmp_path):
    repository = tmp_path / "repo-impact"
    repository.mkdir()
    (repository / "changed.py").write_text(
        "def write(value):\n"
        "    return value\n",
        encoding="utf-8",
    )
    (repository / "unrelated.py").write_text(
        "class Other:\n"
        "    def write(self, value):\n"
        "        return value\n\n"
        "def unrelated(obj):\n"
        "    return obj.write(1)\n",
        encoding="utf-8",
    )
    base = tmp_path / "base-impact"
    base.mkdir()
    (base / "changed.py").write_text(
        "def write(value):\n"
        "    return value + 1\n",
        encoding="utf-8",
    )
    (base / "unrelated.py").write_text(
        (repository / "unrelated.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    actual = diff_between(base, repository)
    graph = build_initial_program_graph(
        repository, "`write` must work", actual, (),
        GraphBudget(max_files=10), base_commit="commit-a",
    )
    assert graph.impact_cone is not None
    direct = {
        graph.nodes[node_id].symbol
        for node_id in graph.impact_cone.direct_caller_ids
        if node_id in graph.nodes
    }
    assert "unrelated.obj.write" not in direct
    assert not graph.impact_cone.state_reader_ids


def test_impact_cone_links_changed_state_write_to_downstream_reader(tmp_path):
    base = tmp_path / "base-state-impact"
    repository = tmp_path / "repo-state-impact"
    base.mkdir()
    repository.mkdir()
    old = (
        "class Handler:\n"
        "    def __init__(self, callback):\n"
        "        self.callback = callback\n\n"
        "    def snapshot(self):\n"
        "        return self.callback\n"
    )
    new = old.replace(
        "        self.callback = callback\n",
        "        if callable(callback):\n"
        "            callback = callback()\n"
        "        self.callback = callback\n",
    )
    (base / "api.py").write_text(old, encoding="utf-8")
    (repository / "api.py").write_text(new, encoding="utf-8")
    actual = diff_between(base, repository)

    graph = build_initial_program_graph(
        repository, "`Handler` must accept a callable callback", actual, (),
        GraphBudget(max_files=8), base_commit="commit-state",
    )

    assert graph.impact_cone is not None
    reader_symbols = {
        graph.nodes[node_id].symbol
        for node_id in graph.impact_cone.state_reader_ids
    }
    assert "Handler.snapshot.self.callback" in reader_symbols
    assert not any(symbol.startswith("Handler.__init__") for symbol in reader_symbols)


def test_unique_dynamic_attribute_name_is_not_a_confirmed_static_call(tmp_path):
    repository = tmp_path / "repo-dynamic-call"
    repository.mkdir()
    (repository / "api.py").write_text(
        "def write(value):\n"
        "    return value\n\n"
        "def consumer(output):\n"
        "    return output.write('x')\n",
        encoding="utf-8",
    )
    empty = tmp_path / "empty-dynamic-call"
    empty.mkdir()
    graph = build_initial_program_graph(
        repository, "`write` must work", diff_between(empty, repository),
        (), GraphBudget(max_files=10), base_commit="commit-a",
    )
    dynamic_call = next(
        node for node in graph.nodes.values()
        if node.kind is ProgramNodeKind.CALL_SITE
        and node.symbol.endswith("output.write")
    )
    write_function = next(
        node for node in graph.nodes.values()
        if node.kind is ProgramNodeKind.FUNCTION and node.symbol == "write"
    )
    assert any(
        edge.source_id == dynamic_call.node_id
        and edge.target_id == write_function.node_id
        and edge.kind is ProgramEdgeKind.MAY_CALL
        for edge in graph.edges.values()
    )
    assert not any(
        edge.source_id == dynamic_call.node_id
        and edge.target_id == write_function.node_id
        and edge.kind is ProgramEdgeKind.CALLS
        for edge in graph.edges.values()
    )


def test_self_method_call_is_statically_resolved_within_its_class(tmp_path):
    repository = tmp_path / "repo-self-call"
    repository.mkdir()
    (repository / "api.py").write_text(
        "class First:\n"
        "    def render(self):\n"
        "        return 1\n"
        "    def call(self):\n"
        "        return self.render()\n\n"
        "class Second:\n"
        "    def render(self):\n"
        "        return 2\n",
        encoding="utf-8",
    )
    empty = tmp_path / "empty-self-call"
    empty.mkdir()
    graph = build_initial_program_graph(
        repository, "`First.render` must work", diff_between(empty, repository),
        (), GraphBudget(max_files=10), base_commit="commit-a",
    )
    call = next(
        node for node in graph.nodes.values()
        if node.kind is ProgramNodeKind.CALL_SITE
        and node.symbol.endswith("self.render")
    )
    targets = {
        graph.nodes[edge.target_id].symbol
        for edge in graph.edges.values()
        if edge.source_id == call.node_id
        and edge.kind is ProgramEdgeKind.CALLS
        and edge.target_id in graph.nodes
    }
    assert targets == {"First.render"}


def test_init_is_not_treated_as_binary_protocol(tmp_path):
    repository = tmp_path / "repo-protocol"
    repository.mkdir()
    (repository / "value.py").write_text(
        "class Value:\n"
        "    def __init__(self, value):\n"
        "        self.value = value\n"
        "    def __add__(self, other):\n"
        "        return self.value + other\n"
        "    def __radd__(self, other):\n"
        "        return other + self.value\n",
        encoding="utf-8",
    )
    empty = tmp_path / "empty-protocol"
    empty.mkdir()
    graph = build_initial_program_graph(
        repository, "`Value` arithmetic must work", diff_between(empty, repository),
        (), GraphBudget(max_files=10), base_commit="commit-a",
    )
    methods = {
        node.symbol.split(".")[-1]: node.metadata.get("protocol")
        for node in graph.nodes.values()
        if node.kind is ProgramNodeKind.METHOD
    }
    assert methods["__init__"] is None
    assert methods["__add__"] == "FORWARD"
    assert methods["__radd__"] == "REFLECTED"
