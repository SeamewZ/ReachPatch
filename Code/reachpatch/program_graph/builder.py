from __future__ import annotations

import ast
import gc
import hashlib
import os
import time
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.graph import GraphNode
from reachpatch.program_graph.analysis import (
    CFGBuilder,
    DefUseAnalyzer,
    DefinitionScopeAnalyzer,
    ModuleAnalysis,
)
from reachpatch.program_graph.models import ProgramGraph
from reachpatch.program_graph.protocols import ProtocolAnalyzer, ProtocolFact

_DEFAULT_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
}
_REFLECTION_CALLS = {"getattr", "setattr", "delattr", "hasattr", "eval", "exec", "__import__"}
_EXTERNAL_CALL_PREFIXES = {
    "open": "filesystem",
    "subprocess": "process",
    "socket": "network",
    "requests": "network",
    "urllib": "network",
    "http": "network",
    "sqlite3": "database",
    "psycopg": "database",
}
_REGISTRATION_NAMES = {"register", "connect", "subscribe", "route", "add_handler", "add_callback"}
_PRE_CALL_FLOW_RELATIONS = {
    "containment", "defines", "decorates", "imports", "exports",
    "control_flow", "exception_flow", "def_use", "data_flow", "alias",
    "state_read", "state_write", "field_flow", "return_flow", "raises",
    "calls", "may_call", "parameter_flow", "test_coverage",
}


def _module_name(relative_path: str) -> str:
    without_suffix = relative_path[:-3] if relative_path.endswith(".py") else relative_path
    parts = list(Path(without_suffix).parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__root__"


def _is_test_path(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    name = Path(relative_path).name
    return any(part in {"test", "tests"} for part in parts) or name.startswith("test_") or name.endswith("_test.py")


def _iter_python_files(root: Path, excludes: set[str]) -> list[Path]:
    files: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root):
        directory_names[:] = sorted(name for name in directory_names if name not in excludes)
        current = Path(current_root)
        for file_name in sorted(file_names):
            if file_name.endswith(".py"):
                files.append(current / file_name)
    return files


def _repository_source_hash(root: Path, paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = str(path.relative_to(root)).replace(os.sep, "/")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class PythonProgramGraphBuilder:
    """Multi-pass, conservative Python behavioral graph construction."""

    def __init__(
        self,
        repository_root: str | Path,
        *,
        exclude_directories: Iterable[str] = (),
        max_files: int = 10000,
    ) -> None:
        self.root = Path(repository_root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(self.root)
        self.excludes = _DEFAULT_EXCLUDES | set(exclude_directories)
        self.max_files = max_files
        self.analyses: list[ModuleAnalysis] = []
        self.import_aliases: dict[str, dict[str, str]] = defaultdict(dict)
        self.points_to: dict[str, set[str]] = defaultdict(set)
        self.module_node_by_name: dict[str, str] = {}
        self.call_facts: list[
            tuple[str, tuple[str, ...], tuple[str | None, ...], bool, str | None]
        ] = []
        self._candidate_parameter_cache: dict[str, tuple[str, ...]] = {}
        self._candidate_return_cache: dict[str, tuple[str, ...]] = {}
        self._stream_timings: dict[str, float] = defaultdict(float)
        self.inheritance_facts: list[tuple[str, str, str, float]] = []
        self.registration_facts: list[dict[str, object]] = []
        self.protocol_facts: list[ProtocolFact] = []

    def build(
        self,
        *,
        progress_callback: Callable[[str, str, float | None], None] | None = None,
    ) -> ProgramGraph:
        timings: dict[str, float] = {}

        def measured(name: str, operation) -> None:
            started = time.perf_counter()
            callback_name = name.removesuffix("_seconds")
            if progress_callback is not None:
                progress_callback(callback_name, "in_progress", None)
            try:
                operation()
            except Exception:
                if progress_callback is not None:
                    progress_callback(
                        callback_name, "error", time.perf_counter() - started
                    )
                raise
            elapsed = time.perf_counter() - started
            timings[name] = elapsed
            if progress_callback is not None:
                progress_callback(callback_name, "complete", elapsed)

        discovery_started = time.perf_counter()
        paths = _iter_python_files(self.root, self.excludes)
        if len(paths) > self.max_files:
            paths = paths[: self.max_files]
            capped = True
        else:
            capped = False
        timings["file_discovery_seconds"] = time.perf_counter() - discovery_started
        hash_started = time.perf_counter()
        source_hash = _repository_source_hash(self.root, paths)
        timings["source_hash_seconds"] = time.perf_counter() - hash_started
        graph = ProgramGraph(repository_root=str(self.root), source_hash=source_hash)
        measured(
            "definition_index_seconds",
            lambda: self._definition_index_pass(graph, paths),
        )
        measured(
            "behavior_stream_seconds",
            lambda: self._behavior_stream_pass(
                graph, paths, progress_callback=progress_callback
            ),
        )
        timings.update(self._stream_timings)
        measured("call_flow_seconds", lambda: self._materialize_call_flows(graph))
        measured(
            "inheritance_dispatch_seconds",
            lambda: self._materialize_inheritance_facts(graph),
        )
        measured(
            "registration_external_seconds",
            lambda: self._materialize_registration_facts(graph),
        )
        measured("protocol_ir_seconds", lambda: self._materialize_protocol_facts(graph))
        measured("property_descriptor_seconds", lambda: self._property_descriptor_pass(graph))
        measured("test_observation_seconds", lambda: self._mark_test_observations(graph))
        measured("package_entrypoint_seconds", lambda: self._load_package_entrypoints(graph))
        if capped:
            owner = next(iter(graph.nodes), stable_id("repository", self.root))
            graph.create_frontier(
                "ANALYSIS_FILE_CAP",
                owner,
                f"Python file count exceeded cap {self.max_files}",
                "raise the file cap or provide demand-driven seed paths",
                hard=True,
            )
        graph.build_timings = timings
        graph.build_stats = {
            "python_file_count": len(paths),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "cfg_count": len(graph.cfgs),
            "protocol_operation_count": len(graph.protocol_operations),
            "test_node_count": len(graph.test_node_ids),
            "observation_node_count": len(graph.observation_node_ids),
            "frontier_count": len(graph.frontiers),
        }
        return graph

    def _analyze_path(
        self,
        graph: ProgramGraph,
        path: Path,
        *,
        declarations_only: bool,
    ) -> ModuleAnalysis | None:
        relative = str(path.relative_to(self.root)).replace(os.sep, "/")
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path), type_comments=True)
        except SyntaxError as exc:
            node = GraphNode.create(
                "file",
                relative,
                identity=relative,
                attributes={
                    "qualified_name": _module_name(relative),
                    "file": relative,
                    "parse_error": str(exc),
                },
            )
            graph.index_node(node)
            graph.create_frontier(
                "PYTHON_PARSE_ERROR",
                node.node_id,
                str(exc),
                "repair syntax or exclude a non-source generated file",
                hard=True,
            )
            return None
        analyzer = DefinitionScopeAnalyzer(
            graph,
            relative_path=relative,
            module_name=_module_name(relative),
            source=source,
            tree=tree,
            is_test=_is_test_path(relative),
            declarations_only=declarations_only,
        )
        analyzer.visit(tree)
        analysis = analyzer.result()
        self.module_node_by_name.setdefault(
            analysis.module_name, analysis.module_node_id
        )
        return analysis

    def _definition_index_pass(self, graph: ProgramGraph, paths: list[Path]) -> None:
        self.analyses.clear()
        for path in paths:
            self._analyze_path(graph, path, declarations_only=True)

    def _behavior_stream_pass(
        self,
        graph: ProgramGraph,
        paths: list[Path],
        *,
        progress_callback: Callable[[str, str, float | None], None] | None,
    ) -> None:
        operations = (
            ("import_export_seconds", self._import_export_pass),
            ("points_to_seconds", self._points_to_pass),
        )
        stream_started = time.perf_counter()
        for index, path in enumerate(paths, start=1):
            parse_started = time.perf_counter()
            analysis = self._analyze_path(graph, path, declarations_only=False)
            self._stream_timings["behavior_reparse_seconds"] += (
                time.perf_counter() - parse_started
            )
            if analysis is None:
                continue
            for timing_name, operation in operations:
                started = time.perf_counter()
                operation(graph, (analysis,))
                self._stream_timings[timing_name] += time.perf_counter() - started
            started = time.perf_counter()
            CFGBuilder(graph, analysis).build()
            DefUseAnalyzer(graph, analysis).run()
            self._stream_timings["cfg_def_use_seconds"] += time.perf_counter() - started
            started = time.perf_counter()
            self._collect_call_facts(graph, analysis)
            self._stream_timings["call_fact_collection_seconds"] += time.perf_counter() - started
            started = time.perf_counter()
            self._collect_inheritance_facts(graph, analysis)
            self._stream_timings["inheritance_fact_collection_seconds"] += time.perf_counter() - started
            started = time.perf_counter()
            self._collect_registration_facts(graph, analysis)
            self._stream_timings["registration_fact_collection_seconds"] += time.perf_counter() - started
            started = time.perf_counter()
            analyzer = ProtocolAnalyzer(
                graph, analysis, defer_materialization=True
            )
            analyzer.run()
            self.protocol_facts.extend(analyzer.facts)
            self._stream_timings["protocol_fact_collection_seconds"] += time.perf_counter() - started
            if index % 64 == 0:
                gc.collect()
                if progress_callback is not None:
                    progress_callback(
                        "behavior_stream",
                        "progress",
                        time.perf_counter() - stream_started,
                    )

    def _import_export_pass(
        self,
        graph: ProgramGraph,
        analyses: Iterable[ModuleAnalysis] | None = None,
    ) -> None:
        module_by_name = self.module_node_by_name
        for analysis in analyses if analyses is not None else self.analyses:
            explicit_all: set[str] = set()
            for node in ast.walk(analysis.tree):
                source_id = analysis.ast_node_ids.get(id(node), analysis.module_node_id)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        local = alias.asname or alias.name.split(".")[0]
                        self.import_aliases[analysis.module_name][local] = alias.name
                        target = module_by_name.get(alias.name)
                        if target:
                            graph.add_relation("imports", [source_id], [target])
                        else:
                            unknown = self._unknown_target(graph, f"module:{alias.name}", source_id)
                            graph.add_relation("imports", [source_id], [unknown], confidence=0.4)
                elif isinstance(node, ast.ImportFrom):
                    prefix = "." * node.level + (node.module or "")
                    for alias in node.names:
                        local = alias.asname or alias.name
                        full = f"{prefix}.{alias.name}".strip(".")
                        self.import_aliases[analysis.module_name][local] = full
                        targets = graph.resolve_symbol(full)
                        if targets:
                            for target in targets:
                                graph.add_relation("imports", [source_id], [target])
                        else:
                            unknown = self._unknown_target(graph, f"symbol:{full}", source_id)
                            graph.add_relation("imports", [source_id], [unknown], confidence=0.4)
                elif (
                    isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
                    and isinstance(node.value, (ast.List, ast.Tuple, ast.Set))
                ):
                    explicit_all.update(
                        value.value
                        for value in node.value.elts
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    )
            for node_id in graph.file_index.get(analysis.relative_path, ()):
                qualified_name = str(
                    graph.nodes[node_id].attributes.get("qualified_name", "")
                )
                if not qualified_name.startswith(f"{analysis.module_name}."):
                    continue
                remainder = qualified_name.removeprefix(f"{analysis.module_name}.")
                if "." in remainder:
                    continue
                if (explicit_all and remainder in explicit_all) or (not explicit_all and not remainder.startswith("_")):
                    if node_id == analysis.module_node_id:
                        continue
                    graph.add_relation("exports", [analysis.module_node_id], [node_id])
                    graph.external_surface_ids.add(node_id)
                    attributes = dict(graph.nodes[node_id].attributes)
                    attributes["externally_controllable"] = True
                    graph.update_node_attributes(node_id, attributes)

    def _unknown_target(self, graph: ProgramGraph, label: str, owner_id: str) -> str:
        node = GraphNode.create(
            "unknown_dynamic_target",
            label,
            identity=(label, owner_id),
            attributes={"qualified_name": label, "file": "<unresolved>"},
        )
        graph.index_node(node)
        graph.create_frontier(
            "UNRESOLVED_DYNAMIC_TARGET",
            owner_id,
            f"unresolved target {label}",
            "resolve import/dispatch statically or run a targeted trace",
            hard=False,
        )
        return node.node_id

    def _points_to_pass(
        self,
        graph: ProgramGraph,
        analyses: Iterable[ModuleAnalysis] | None = None,
    ) -> None:
        for analysis in analyses if analyses is not None else self.analyses:
            for node in ast.walk(analysis.tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if isinstance(value, ast.Call):
                    callee = ast.unparse(value.func)
                    resolved = graph.resolve_symbol(callee)
                    for target in targets:
                        if isinstance(target, ast.Name):
                            qualified = f"{analysis.module_name}.{target.id}"
                            for resolved_id in resolved:
                                if graph.nodes[resolved_id].kind == "class":
                                    self.points_to[qualified].add(resolved_id)
                                    target_id = analysis.ast_node_ids.get(id(target))
                                    if target_id:
                                        object_node = GraphNode.create(
                                            "abstract_object",
                                            f"instance:{callee}",
                                            identity=(qualified, callee, graph.source_hash),
                                            attributes={
                                                "qualified_name": f"{qualified}::<object>",
                                                "file": analysis.relative_path,
                                                "possible_class_ids": [resolved_id],
                                                "construction_complete": True,
                                            },
                                        )
                                        graph.index_node(object_node)
                                        graph.add_relation("data_flow", [resolved_id], [object_node.node_id])
                                        graph.add_relation("data_flow", [object_node.node_id], [target_id])
                if isinstance(value, ast.Name):
                    for target in targets:
                        if isinstance(target, ast.Name):
                            source_id = analysis.ast_node_ids.get(id(value))
                            target_id = analysis.ast_node_ids.get(id(target))
                            if source_id and target_id:
                                graph.add_relation("alias", [source_id], [target_id])

    def _resolve_callee(self, graph: ProgramGraph, analysis: ModuleAnalysis, call: ast.Call) -> list[str]:
        expression = ast.unparse(call.func)
        direct = graph.resolve_symbol(expression)
        if direct:
            return direct
        if isinstance(call.func, ast.Name):
            imported = self.import_aliases[analysis.module_name].get(call.func.id)
            if imported:
                direct = graph.resolve_symbol(imported)
        elif isinstance(call.func, ast.Attribute):
            base = ast.unparse(call.func.value)
            imported_base = self.import_aliases[analysis.module_name].get(base, base)
            direct = graph.resolve_symbol(f"{imported_base}.{call.func.attr}")
            if not direct:
                points_to_ids = self.points_to.get(f"{analysis.module_name}.{base}", set())
                for class_id in points_to_ids:
                    class_name = str(graph.nodes[class_id].attributes.get("qualified_name", ""))
                    direct.extend(graph.resolve_symbol(f"{class_name}.{call.func.attr}"))
        return sorted(set(direct))

    def _collect_call_facts(
        self,
        graph: ProgramGraph,
        analysis: ModuleAnalysis,
    ) -> None:
        for call in (node for node in ast.walk(analysis.tree) if isinstance(node, ast.Call)):
            call_id = analysis.ast_node_ids.get(id(call))
            if call_id is None:
                continue
            candidates = tuple(self._resolve_callee(graph, analysis, call))
            if not candidates:
                self.call_facts.append((
                    call_id,
                    (),
                    (),
                    False,
                    f"call:{ast.unparse(call.func)}",
                ))
                continue
            argument_ids = tuple(
                analysis.ast_node_ids.get(id(argument)) for argument in call.args
            )
            is_test = (
                analysis.module_node_id in graph.test_node_ids
                or graph.nodes[call_id].kind == "test"
            )
            self.call_facts.append((call_id, candidates, argument_ids, is_test, None))

    def _materialize_call_flows(self, graph: ProgramGraph) -> None:
        def candidate_parameters(candidate: str) -> tuple[str, ...]:
            cached = self._candidate_parameter_cache.get(candidate)
            if cached is not None:
                return cached
            parameters = tuple(
                node_id
                for node_id in graph.successors(candidate, {"defines"})
                if graph.nodes[node_id].kind == "parameter"
            )
            self._candidate_parameter_cache[candidate] = parameters
            return parameters

        def candidate_returns(candidate: str) -> tuple[str, ...]:
            cached = self._candidate_return_cache.get(candidate)
            if cached is not None:
                return cached
            # Preserve the original conservative bounded reachability domain,
            # but evaluate it once per candidate instead of once per call site.
            returns = tuple(
                node_id
                for node_id in graph.reachable(
                    [candidate],
                    max_nodes=500,
                    edge_predicate=lambda edge: (
                        edge.kind in _PRE_CALL_FLOW_RELATIONS
                        and not (
                            edge.kind == "containment"
                            and any(
                                graph.nodes[target_id].kind == "protocol_operation"
                                for target_id in edge.target_ids
                            )
                        )
                    ),
                )
                if graph.nodes[node_id].kind == "return"
            )
            self._candidate_return_cache[candidate] = returns
            return returns

        for call_id, candidates, argument_ids, is_test, unresolved_label in self.call_facts:
            if unresolved_label is not None:
                unknown_id = self._unknown_target(graph, unresolved_label, call_id)
                graph.add_relation("may_call", [call_id], [unknown_id], confidence=0.3)
                continue
            relation = "calls" if len(candidates) == 1 else "may_call"
            graph.add_relation(
                relation,
                [call_id],
                candidates,
                confidence=1.0 if relation == "calls" else 0.6,
            )
            parameters_by_position: dict[int, set[str]] = defaultdict(set)
            return_ids: set[str] = set()
            for candidate in candidates:
                parameters = candidate_parameters(candidate)
                for position, parameter_id in enumerate(parameters[:len(argument_ids)]):
                    parameters_by_position[position].add(parameter_id)
                return_ids.update(candidate_returns(candidate))
            for position, parameter_ids in sorted(parameters_by_position.items()):
                argument_id = argument_ids[position]
                if argument_id and parameter_ids:
                    graph.add_relation(
                        "parameter_flow", [argument_id], sorted(parameter_ids)
                    )
            if return_ids:
                graph.add_relation("return_flow", sorted(return_ids), [call_id])
            if is_test:
                graph.add_relation("test_coverage", [call_id], candidates)
        self.call_facts.clear()
        self._candidate_parameter_cache.clear()
        self._candidate_return_cache.clear()

    def _inheritance_dispatch_pass(
        self,
        graph: ProgramGraph,
        analyses: Iterable[ModuleAnalysis] | None = None,
    ) -> None:
        class_ast_by_id: dict[str, tuple[ModuleAnalysis, ast.ClassDef]] = {}
        for analysis in analyses if analyses is not None else self.analyses:
            for node in ast.walk(analysis.tree):
                if isinstance(node, ast.ClassDef):
                    class_id = analysis.ast_node_ids.get(id(node))
                    if class_id:
                        class_ast_by_id[class_id] = (analysis, node)
        for class_id, (analysis, class_ast) in class_ast_by_id.items():
            class_name = str(graph.nodes[class_id].attributes.get("qualified_name", class_ast.name))
            for base in class_ast.bases:
                base_name = ast.unparse(base)
                targets = graph.resolve_symbol(base_name)
                if not targets:
                    targets = graph.resolve_symbol(self.import_aliases[analysis.module_name].get(base_name, base_name))
                for target in targets:
                    if graph.nodes[target].kind == "class":
                        graph.add_relation("inheritance", [class_id], [target])
                        base_qualified = str(graph.nodes[target].attributes.get("qualified_name", ""))
                        for child in class_ast.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                child_ids = graph.resolve_symbol(f"{class_name}.{child.name}")
                                base_methods = graph.resolve_symbol(f"{base_qualified}.{child.name}")
                                for child_id in child_ids:
                                    for base_method in base_methods:
                                        graph.add_relation("override", [child_id], [base_method])
                                        graph.add_relation("dispatch", [base_method], [child_id], confidence=0.8)

    def _collect_inheritance_facts(
        self,
        graph: ProgramGraph,
        analysis: ModuleAnalysis,
    ) -> None:
        for node in ast.walk(analysis.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            class_id = analysis.ast_node_ids.get(id(node))
            if class_id is None:
                continue
            class_name = str(
                graph.nodes[class_id].attributes.get("qualified_name", node.name)
            )
            for base in node.bases:
                base_name = ast.unparse(base)
                targets = graph.resolve_symbol(base_name)
                if not targets:
                    imported = self.import_aliases[analysis.module_name].get(
                        base_name, base_name
                    )
                    targets = graph.resolve_symbol(imported)
                for target in targets:
                    if graph.nodes[target].kind != "class":
                        continue
                    self.inheritance_facts.append(
                        ("inheritance", class_id, target, 1.0)
                    )
                    base_qualified = str(
                        graph.nodes[target].attributes.get("qualified_name", "")
                    )
                    for child in node.body:
                        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            continue
                        child_ids = graph.resolve_symbol(f"{class_name}.{child.name}")
                        base_methods = graph.resolve_symbol(
                            f"{base_qualified}.{child.name}"
                        )
                        for child_id in child_ids:
                            for base_method in base_methods:
                                self.inheritance_facts.extend((
                                    ("override", child_id, base_method, 1.0),
                                    ("dispatch", base_method, child_id, 0.8),
                                ))

    def _materialize_inheritance_facts(self, graph: ProgramGraph) -> None:
        for kind, source_id, target_id, confidence in self.inheritance_facts:
            graph.add_relation(
                kind, [source_id], [target_id], confidence=confidence
            )
        self.inheritance_facts.clear()

    def _registration_external_pass(
        self,
        graph: ProgramGraph,
        analyses: Iterable[ModuleAnalysis] | None = None,
    ) -> None:
        for analysis in analyses if analyses is not None else self.analyses:
            for call in (node for node in ast.walk(analysis.tree) if isinstance(node, ast.Call)):
                call_id = analysis.ast_node_ids.get(id(call))
                if call_id is None:
                    continue
                callee = ast.unparse(call.func)
                final_name = callee.rsplit(".", 1)[-1]
                if final_name in _REGISTRATION_NAMES:
                    for argument in call.args:
                        if isinstance(argument, (ast.Name, ast.Attribute)):
                            for callback_id in graph.resolve_symbol(ast.unparse(argument)):
                                graph.add_relation("registers", [call_id], [callback_id])
                                graph.external_surface_ids.add(callback_id)
                if final_name in _REFLECTION_CALLS or callee.endswith(".import_module"):
                    graph.add_relation("reflection", [call_id], [call_id], confidence=0.5)
                    graph.add_relation("dynamic_lookup", [call_id], [call_id], confidence=0.5)
                    graph.create_frontier(
                        "DYNAMIC_REFLECTION",
                        call_id,
                        f"dynamic operation {callee}",
                        "trace resolved name and target under the relevant input partition",
                        hard=False,
                    )
                external_kind = next(
                    (kind for prefix, kind in _EXTERNAL_CALL_PREFIXES.items() if callee == prefix or callee.startswith(f"{prefix}.")),
                    None,
                )
                if external_kind:
                    external = GraphNode.create(
                        "external_interface",
                        callee,
                        identity=(external_kind, callee),
                        attributes={
                            "qualified_name": f"external.{external_kind}.{callee}",
                            "file": "<external>",
                            "effect_kind": external_kind,
                        },
                    )
                    graph.index_node(external)
                    graph.add_relation("external_effect", [call_id], [external.node_id])

    def _collect_registration_facts(
        self,
        graph: ProgramGraph,
        analysis: ModuleAnalysis,
    ) -> None:
        for call in (node for node in ast.walk(analysis.tree) if isinstance(node, ast.Call)):
            call_id = analysis.ast_node_ids.get(id(call))
            if call_id is None:
                continue
            callee = ast.unparse(call.func)
            final_name = callee.rsplit(".", 1)[-1]
            if final_name in _REGISTRATION_NAMES:
                target_names = tuple(
                    ast.unparse(argument)
                    for argument in call.args
                    if isinstance(argument, (ast.Name, ast.Attribute))
                )
                if target_names:
                    self.registration_facts.append({
                        "kind": "registers",
                        "call_id": call_id,
                        "target_names": target_names,
                    })
            if final_name in _REFLECTION_CALLS or callee.endswith(".import_module"):
                self.registration_facts.append({
                    "kind": "reflection",
                    "call_id": call_id,
                    "callee": callee,
                })
            external_kind = next(
                (
                    kind
                    for prefix, kind in _EXTERNAL_CALL_PREFIXES.items()
                    if callee == prefix or callee.startswith(f"{prefix}.")
                ),
                None,
            )
            if external_kind:
                self.registration_facts.append({
                    "kind": "external_effect",
                    "call_id": call_id,
                    "callee": callee,
                    "external_kind": external_kind,
                })

    def _materialize_registration_facts(self, graph: ProgramGraph) -> None:
        for fact in self.registration_facts:
            kind = str(fact["kind"])
            call_id = str(fact["call_id"])
            if kind == "registers":
                for target_name in fact["target_names"]:
                    for callback_id in graph.resolve_symbol(str(target_name)):
                        graph.add_relation("registers", [call_id], [callback_id])
                        graph.external_surface_ids.add(callback_id)
            elif kind == "reflection":
                callee = str(fact["callee"])
                graph.add_relation("reflection", [call_id], [call_id], confidence=0.5)
                graph.add_relation("dynamic_lookup", [call_id], [call_id], confidence=0.5)
                graph.create_frontier(
                    "DYNAMIC_REFLECTION",
                    call_id,
                    f"dynamic operation {callee}",
                    "trace resolved name and target under the relevant input partition",
                    hard=False,
                )
            elif kind == "external_effect":
                callee = str(fact["callee"])
                external_kind = str(fact["external_kind"])
                external = GraphNode.create(
                    "external_interface",
                    callee,
                    identity=(external_kind, callee),
                    attributes={
                        "qualified_name": f"external.{external_kind}.{callee}",
                        "file": "<external>",
                        "effect_kind": external_kind,
                    },
                )
                graph.index_node(external)
                graph.add_relation(
                    "external_effect", [call_id], [external.node_id]
                )
        self.registration_facts.clear()

    def _materialize_protocol_facts(self, graph: ProgramGraph) -> None:
        for fact in self.protocol_facts:
            ProtocolAnalyzer.materialize_fact(graph, fact)
        self.protocol_facts.clear()

    def _property_descriptor_pass(self, graph: ProgramGraph) -> None:
        properties = [node for node in graph if node.kind == "property"]
        fields_by_name: dict[str, list[GraphNode]] = defaultdict(list)
        for node in graph:
            if node.kind == "field" and node.attributes.get("field") is not None:
                fields_by_name[str(node.attributes["field"])].append(node)
        for property_node in properties:
            for field in fields_by_name.get(property_node.label, ()):
                graph.add_relation("descriptor", [field.node_id], [property_node.node_id], confidence=0.8)
                graph.add_relation("property", [field.node_id], [property_node.node_id], confidence=0.8)

    def _mark_test_observations(self, graph: ProgramGraph) -> None:
        for node_id in sorted(graph.test_node_ids):
            reachable = graph.reachable([node_id], max_nodes=2000)
            targets = sorted(
                target_id
                for target_id in reachable
                if target_id != node_id
                and graph.nodes[target_id].kind
                in {"assertion", "return", "exception", "external_effect"}
            )
            graph.observation_node_ids.update(targets)
            if targets:
                graph.add_relation("observes", [node_id], targets)

    def _load_package_entrypoints(self, graph: ProgramGraph) -> None:
        pyproject = self.root / "pyproject.toml"
        if not pyproject.is_file():
            return
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            owner = next(iter(graph.nodes), stable_id("pyproject", self.root))
            graph.create_frontier(
                "PACKAGE_METADATA_PARSE",
                owner,
                str(exc),
                "repair or inspect pyproject.toml",
                hard=False,
            )
            return
        scripts = data.get("project", {}).get("scripts", {})
        for command, target_name in sorted(scripts.items()):
            symbol = str(target_name).replace(":", ".")
            targets = graph.resolve_symbol(symbol)
            for target in targets:
                external = GraphNode.create(
                    "external_interface",
                    command,
                    identity=("console-script", command),
                    attributes={
                        "qualified_name": f"console.{command}",
                        "file": "pyproject.toml",
                        "externally_controllable": True,
                    },
                )
                graph.index_node(external)
                graph.external_surface_ids.add(external.node_id)
                graph.add_relation("triggers", [external.node_id], [target])

    def incremental_update(
        self,
        previous: ProgramGraph,
        changed_files: Iterable[str],
    ) -> tuple[ProgramGraph, dict[str, list[str]]]:
        changed = {str(Path(path).as_posix()) for path in changed_files}
        rebuilt = self.build()
        old_nodes = {
            node_id: node
            for node_id, node in previous.nodes.items()
            if str(node.attributes.get("file", "")) in changed
        }
        new_nodes = {
            node_id: node
            for node_id, node in rebuilt.nodes.items()
            if str(node.attributes.get("file", "")) in changed
        }
        old_edges = {
            edge_id: edge
            for edge_id, edge in previous.edges.items()
            if set(edge.source_ids + edge.target_ids) & old_nodes.keys()
        }
        new_edges = {
            edge_id: edge
            for edge_id, edge in rebuilt.edges.items()
            if set(edge.source_ids + edge.target_ids) & new_nodes.keys()
        }
        delta = {
            "added_nodes": sorted(new_nodes.keys() - old_nodes.keys()),
            "deleted_nodes": sorted(old_nodes.keys() - new_nodes.keys()),
            "retained_nodes": sorted(old_nodes.keys() & new_nodes.keys()),
            "added_edges": sorted(new_edges.keys() - old_edges.keys()),
            "deleted_edges": sorted(old_edges.keys() - new_edges.keys()),
        }
        rebuilt.version = previous.version + 1
        return rebuilt, delta


def build_augmented_program_graph(
    repository_root: str | Path,
    *,
    progress_callback: Callable[[str, str, float | None], None] | None = None,
) -> ProgramGraph:
    """Build the generic graph and add only adapter-observed facts/frontiers."""

    from reachpatch.adapters import select_adapter

    root = Path(repository_root).resolve()
    graph = PythonProgramGraphBuilder(root).build(
        progress_callback=progress_callback
    )
    adapter = select_adapter(root)
    started = time.perf_counter()
    if progress_callback is not None:
        progress_callback("adapter_augmentation", "in_progress", None)
    adapter.augment_program_graph(graph, adapter.observe(root))
    elapsed = time.perf_counter() - started
    graph.build_timings["adapter_augmentation_seconds"] = elapsed
    graph.build_stats.update({
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "frontier_count": len(graph.frontiers),
    })
    if progress_callback is not None:
        progress_callback("adapter_augmentation", "complete", elapsed)
        progress_callback("semantic_hash", "in_progress", None)
    started = time.perf_counter()
    graph.program_hash()
    elapsed = time.perf_counter() - started
    graph.build_timings["semantic_hash_seconds"] = elapsed
    if progress_callback is not None:
        progress_callback("semantic_hash", "complete", elapsed)
    return graph
