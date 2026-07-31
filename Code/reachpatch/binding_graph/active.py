from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Iterable

from reachpatch.models.base import SerializableRecord, content_hash, stable_id

if TYPE_CHECKING:
    from reachpatch.execution.reconcile import ActualDiff


class ActiveBindingStatus(StrEnum):
    UNBOUND = "UNBOUND"
    BOUND_STATIC = "BOUND_STATIC"
    BOUND_EXECUTABLE = "BOUND_EXECUTABLE"
    FAILING = "FAILING"
    PASSING = "PASSING"
    PRESERVATION_RISK = "PRESERVATION_RISK"
    COUNTEREXAMPLE_OPEN = "COUNTEREXAMPLE_OPEN"
    ORACLE_UNAVAILABLE = "ORACLE_UNAVAILABLE"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ActiveBindingUnit(SerializableRecord):
    binding_id: str
    requirement_id: str
    requirement_text: str
    requirement_authority: str
    program_symbol_ids: tuple[str, ...] = ()
    path_class_ids: tuple[str, ...] = ()
    branch_partition_ids: tuple[str, ...] = ()
    protocol_edge_ids: tuple[str, ...] = ()
    changed_hunk_ids: tuple[str, ...] = ()
    causal_cut_ids: tuple[str, ...] = ()
    impact_cone_ids: tuple[str, ...] = ()
    target_check_ids: tuple[str, ...] = ()
    preservation_check_ids: tuple[str, ...] = ()
    challenge_check_ids: tuple[str, ...] = ()
    counterexample_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    status: str = ActiveBindingStatus.UNKNOWN.value
    confidence: float = 0.0
    unresolved_reason: str | None = None
    historical_hunk_ids: tuple[str, ...] = ()
    closed_counterexample_ids: tuple[str, ...] = ()

    @property
    def unit_id(self) -> str:
        return self.binding_id

    @property
    def leaf_id(self) -> str:
        return self.requirement_id

    @property
    def repair_cut_node_ids(self) -> tuple[str, ...]:
        return self.causal_cut_ids

    @property
    def impact_cone_node_ids(self) -> tuple[str, ...]:
        return self.impact_cone_ids

    @property
    def path_obligation_id(self) -> str:
        return self.path_class_ids[0] if self.path_class_ids else self.requirement_id

    @property
    def interaction_path_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.program_symbol_ids, *self.path_class_ids)))

    @property
    def preservation_node_ids(self) -> tuple[str, ...]:
        return self.impact_cone_ids

    @property
    def trigger_id(self) -> None:
        return None

    @property
    def entrypoint_id(self) -> str | None:
        return self.program_symbol_ids[0] if self.program_symbol_ids else None

    @property
    def exit_kind(self) -> str:
        return "process_exit"

    @property
    def oracle_id(self) -> None:
        return None

    @property
    def scenario_ids(self) -> tuple[str, ...]:
        return ()

    @property
    def frontier_ids(self) -> tuple[str, ...]:
        return ()

    @property
    def cut_status(self) -> str:
        """Compatibility view derived from the active unit status."""
        return (
            "CUT_RESOLVED" if self.status == ActiveBindingStatus.PASSING.value
            else "CUT_OPEN"
        )


@dataclass(frozen=True, slots=True)
class BindingEdge(SerializableRecord):
    source_id: str
    target_id: str
    edge_type: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BindingGap(SerializableRecord):
    requirement_id: str
    gap_type: str
    reason: str
    attempted_bindings: tuple[str, ...] = ()
    next_recovery_actions: tuple[str, ...] = ()

    @property
    def frontier_id(self) -> str:
        return stable_id("active-binding-gap", self.requirement_id, self.gap_type)

    @property
    def hard(self) -> bool:
        return False

    @property
    def status(self) -> str:
        return "OPEN"

    @property
    def kind(self) -> str:
        return self.gap_type


class ActiveBindingGraph(SerializableRecord):
    """The only mutable-revision binding truth used by production repair."""

    def __init__(
        self,
        *,
        instance_id: str,
        revision: int,
        diff_hash: str,
        program_slice_hash: str,
        requirement_graph_hash: str,
        units: dict[str, ActiveBindingUnit] | None = None,
        edges: Iterable[BindingEdge] = (),
        unresolved_gaps: Iterable[BindingGap] = (),
        target_check_ids: Iterable[str] = (),
        preservation_check_ids: Iterable[str] = (),
        challenge_check_ids: Iterable[str] = (),
        history: Iterable[dict[str, Any]] = (),
        build_stats: dict[str, int] | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.revision = revision
        self.diff_hash = diff_hash
        self.program_slice_hash = program_slice_hash
        self.requirement_graph_hash = requirement_graph_hash
        self.units = dict(units or {})
        self.edges = list(edges)
        self.unresolved_gaps = list(unresolved_gaps)
        self.target_check_ids = tuple(dict.fromkeys(map(str, target_check_ids)))
        self.preservation_check_ids = tuple(
            dict.fromkeys(map(str, preservation_check_ids))
        )
        self.challenge_check_ids = tuple(dict.fromkeys(map(str, challenge_check_ids)))
        self.history = list(history)
        self.build_stats = dict(build_stats or {})
        # These empty registries are compatibility-only executor inputs. They
        # never carry a second binding truth and production decisions read the
        # active units/check ids above.
        self.oracles: dict[str, Any] = {}
        self.scenarios: dict[str, Any] = {}
        self.oracle_frontiers: dict[str, Any] = {}
        self.components: dict[str, Any] = {}

    @property
    def executable_unit_count(self) -> int:
        return sum(
            bool(unit.target_check_ids or unit.preservation_check_ids or unit.challenge_check_ids)
            for unit in self.units.values()
        )

    @property
    def unbound_requirement_count(self) -> int:
        return len({gap.requirement_id for gap in self.unresolved_gaps})

    @property
    def frontiers(self) -> dict[str, BindingGap]:
        return {
            stable_id("active-binding-gap", item.requirement_id, item.gap_type): item
            for item in self.unresolved_gaps
        }

    @property
    def assignment_id(self) -> str:
        return self.instance_id

    @property
    def version(self) -> int:
        return self.revision

    @property
    def program_graph_hash(self) -> str:
        return self.program_slice_hash

    @property
    def by_path_obligation(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for unit in self.units.values():
            result.setdefault(unit.path_obligation_id, set()).add(unit.binding_id)
        return result

    def unit_ids_for_nodes(self, node_ids: Iterable[str]) -> set[str]:
        requested = set(map(str, node_ids))
        return {
            unit.binding_id for unit in self.units.values()
            if requested & set(unit.interaction_path_ids + unit.causal_cut_ids)
        }

    def to_dict(self) -> dict[str, Any]:
        body = {
            "instance_id": self.instance_id,
            "revision": self.revision,
            "diff_hash": self.diff_hash,
            "program_slice_hash": self.program_slice_hash,
            "requirement_graph_hash": self.requirement_graph_hash,
            "units": [self.units[key].to_dict() for key in sorted(self.units)],
            "edges": [item.to_dict() for item in sorted(
                self.edges, key=lambda item: (item.source_id, item.target_id, item.edge_type)
            )],
            "unresolved_gaps": [item.to_dict() for item in sorted(
                self.unresolved_gaps, key=lambda item: (item.requirement_id, item.gap_type)
            )],
            "target_check_ids": list(self.target_check_ids),
            "preservation_check_ids": list(self.preservation_check_ids),
            "challenge_check_ids": list(self.challenge_check_ids),
            "executable_unit_count": self.executable_unit_count,
            "unbound_requirement_count": self.unbound_requirement_count,
            "history": self.history,
            "build_stats": self.build_stats,
        }
        body["graph_hash"] = content_hash(body)
        return body

    def graph_hash(self) -> str:
        return self.to_dict()["graph_hash"]

    def relevant_units(self, *, include_passing: bool = False) -> tuple[ActiveBindingUnit, ...]:
        priority = {
            ActiveBindingStatus.FAILING.value: 0,
            ActiveBindingStatus.PRESERVATION_RISK.value: 1,
            ActiveBindingStatus.COUNTEREXAMPLE_OPEN.value: 2,
            ActiveBindingStatus.UNBOUND.value: 3,
            ActiveBindingStatus.UNKNOWN.value: 4,
            ActiveBindingStatus.ORACLE_UNAVAILABLE.value: 5,
            ActiveBindingStatus.ENVIRONMENT_BLOCKED.value: 6,
            ActiveBindingStatus.BOUND_EXECUTABLE.value: 7,
            ActiveBindingStatus.BOUND_STATIC.value: 8,
            ActiveBindingStatus.PASSING.value: 9,
        }
        return tuple(sorted(
            (
                unit for unit in self.units.values()
                if include_passing or unit.status != ActiveBindingStatus.PASSING.value
            ),
            key=lambda unit: (priority.get(unit.status, 99), unit.binding_id),
        ))

    @property
    def executable_targets(self) -> tuple[ActiveBindingUnit, ...]:
        """Derived target units for legacy readers; no second graph is stored."""
        return tuple(unit for unit in self.units.values() if unit.target_check_ids)

    @property
    def executable_preservation(self) -> tuple[ActiveBindingUnit, ...]:
        return tuple(
            unit for unit in self.units.values() if unit.preservation_check_ids
        )


def empty_active_binding_graph(
    *, instance_id: str, requirement_graph: Any, program_slice: Any
) -> ActiveBindingGraph:
    requirement_hash = _requirement_hash(requirement_graph)
    program_hash = _program_hash(program_slice)
    units: dict[str, ActiveBindingUnit] = {}
    gaps: list[BindingGap] = []
    for leaf in _requirement_leaves(requirement_graph):
        requirement_id = _leaf_id(leaf)
        binding_id = stable_id("active-binding", instance_id, requirement_id)
        units[binding_id] = ActiveBindingUnit(
            binding_id=binding_id,
            requirement_id=requirement_id,
            requirement_text=_leaf_text(leaf),
            requirement_authority=_leaf_authority(leaf),
            evidence_ids=_leaf_evidence(leaf),
            status=ActiveBindingStatus.UNBOUND.value,
            unresolved_reason="the first working diff has not been generated",
        )
        gaps.append(BindingGap(
            requirement_id=requirement_id,
            gap_type="DIFF_NOT_AVAILABLE",
            reason="active binding is intentionally deferred until the first patch exists",
            next_recovery_actions=("generate_first_complete_patch", "analyze_actual_diff"),
        ))
    return ActiveBindingGraph(
        instance_id=instance_id,
        revision=0,
        diff_hash=content_hash(""),
        program_slice_hash=program_hash,
        requirement_graph_hash=requirement_hash,
        units=units,
        unresolved_gaps=gaps,
    )


def active_binding_graph_from_dict(raw: dict[str, Any]) -> ActiveBindingGraph:
    units = {
        str(item["binding_id"]): ActiveBindingUnit(**{
            **item,
            **{
                name: tuple(item.get(name, ()))
                for name in (
                    "program_symbol_ids", "path_class_ids", "branch_partition_ids",
                    "protocol_edge_ids", "changed_hunk_ids", "causal_cut_ids",
                    "impact_cone_ids", "target_check_ids",
                    "preservation_check_ids", "challenge_check_ids",
                    "counterexample_ids", "evidence_ids", "historical_hunk_ids",
                    "closed_counterexample_ids",
                )
            },
        })
        for item in raw.get("units", ())
    }
    edges = tuple(BindingEdge(
        source_id=str(item["source_id"]),
        target_id=str(item["target_id"]),
        edge_type=str(item["edge_type"]),
        evidence_ids=tuple(item.get("evidence_ids", ())),
    ) for item in raw.get("edges", ()))
    gaps = tuple(BindingGap(
        requirement_id=str(item["requirement_id"]),
        gap_type=str(item["gap_type"]),
        reason=str(item["reason"]),
        attempted_bindings=tuple(item.get("attempted_bindings", ())),
        next_recovery_actions=tuple(item.get("next_recovery_actions", ())),
    ) for item in raw.get("unresolved_gaps", ()))
    return ActiveBindingGraph(
        instance_id=str(raw["instance_id"]),
        revision=int(raw.get("revision", 0)),
        diff_hash=str(raw.get("diff_hash", content_hash(""))),
        program_slice_hash=str(raw.get("program_slice_hash", "")),
        requirement_graph_hash=str(raw.get("requirement_graph_hash", "")),
        units=units,
        edges=edges,
        unresolved_gaps=gaps,
        target_check_ids=raw.get("target_check_ids", ()),
        preservation_check_ids=raw.get("preservation_check_ids", ()),
        challenge_check_ids=raw.get("challenge_check_ids", ()),
        history=raw.get("history", ()),
        build_stats=dict(raw.get("build_stats", {})),
    )


def _requirement_leaves(requirement_graph: Any) -> tuple[Any, ...]:
    leaves = getattr(requirement_graph, "leaves", {})
    if isinstance(leaves, dict):
        return tuple(leaves[key] for key in sorted(leaves))
    return tuple(leaves)


def _leaf_id(leaf: Any) -> str:
    return str(getattr(leaf, "leaf_id", getattr(leaf, "requirement_id", "")))


def _leaf_text(leaf: Any) -> str:
    return str(getattr(leaf, "formula", getattr(leaf, "normalized_requirement", leaf)))


def _leaf_authority(leaf: Any) -> str:
    authority = getattr(leaf, "authority", "C")
    return str(getattr(authority, "value", authority))


def _leaf_evidence(leaf: Any) -> tuple[str, ...]:
    return tuple(map(str, getattr(leaf, "supporting_evidence", ())))


def _requirement_hash(graph: Any) -> str:
    method = getattr(graph, "semantic_layer_hash", None)
    return str(method()) if callable(method) else content_hash(graph)


def _program_hash(graph: Any) -> str:
    method = getattr(graph, "program_hash", None)
    return str(method()) if callable(method) else content_hash(graph)


def _candidate_symbols(leaf: Any, program: Any, changed_files: set[str]) -> tuple[str, ...]:
    names = list(map(str, getattr(leaf, "entrypoint_hypotheses", ())))
    text_tokens = {
        token.strip("`()[]{}.,:;'")
        for token in _leaf_text(leaf).replace("/", " ").split()
        if any(char.isalpha() for char in token)
    }
    resolver = getattr(program, "resolve_symbol", None)
    candidates: list[str] = []
    for name in (*names, *sorted(text_tokens, key=lambda item: (-len(item), item))):
        if callable(resolver):
            candidates.extend(map(str, resolver(name)))
        if len(candidates) >= 40:
            break
    for relative in sorted(changed_files):
        candidates.extend(map(str, getattr(program, "file_index", {}).get(relative, ())))
    return tuple(dict.fromkeys(candidates))[:40]


def _path_projection(program: Any, symbol_ids: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    selected_paths: list[str] = []
    branch_ids: list[str] = []
    symbols = set(symbol_ids)
    for path_id, path in sorted(getattr(program, "path_classes", {}).items()):
        if symbols & set(getattr(path, "node_ids", ())):
            selected_paths.append(str(path_id))
            branch_ids.extend(map(str, getattr(path, "critical_predicates", ())))
        if len(selected_paths) >= 40:
            break
    return tuple(selected_paths), tuple(dict.fromkeys(branch_ids))


def _protocol_projection(program: Any, symbol_ids: tuple[str, ...]) -> tuple[str, ...]:
    symbols = set(symbol_ids)
    return tuple(
        str(operation_id)
        for operation_id, operation in sorted(
            getattr(program, "protocol_operations", {}).items()
        )
        if (
            getattr(operation, "source_node_id", None) in symbols
            or symbols & set(getattr(operation, "candidate_target_ids", ()))
        )
    )[:40]


def _hunks_for_symbols(
    diff: ActualDiff, program: Any, symbol_ids: tuple[str, ...]
) -> tuple[str, ...]:
    files = {
        str(getattr(program, "nodes", {}).get(symbol_id).attributes.get("file", ""))
        for symbol_id in symbol_ids
        if getattr(program, "nodes", {}).get(symbol_id) is not None
    }
    scopes = {
        str(getattr(program, "nodes", {}).get(symbol_id).attributes.get("qualified_name", ""))
        for symbol_id in symbol_ids
        if getattr(program, "nodes", {}).get(symbol_id) is not None
    }
    relation_files = {
        relation.file for relation in diff.changed_relations
        if relation.qualified_scope in scopes
        or any(scope.endswith("." + relation.qualified_scope) for scope in scopes)
    }
    return tuple(
        hunk.hunk_id for hunk in diff.hunks if hunk.file in files | relation_files
    )


def _check_projection(
    leaf: Any, target_recovery: Any, public_tests: Iterable[Any]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    text = _leaf_text(leaf).lower()
    evidence = set(_leaf_evidence(leaf))
    target: list[str] = []
    preservation: list[str] = []
    challenge: list[str] = []
    checks = (
        *getattr(target_recovery, "targets", ()),
        *getattr(target_recovery, "preservation_checks", ()),
        *tuple(public_tests),
    )
    for check in checks:
        check_id = str(getattr(check, "check_id", check))
        source = set(map(str, getattr(check, "source_evidence_ids", ())))
        requirement_ids = set(map(str, getattr(check, "target_requirement_ids", ())))
        relevant = (
            _leaf_id(leaf) in requirement_ids
            or bool(evidence & source)
            or any(token in str(check).lower() for token in text.split() if len(token) >= 5)
        )
        if not relevant and (target or preservation):
            continue
        role = getattr(getattr(check, "role", None), "value", getattr(check, "role", ""))
        if role == "TARGET":
            target.append(check_id)
        elif role == "PRESERVATION":
            preservation.append(check_id)
        else:
            challenge.append(check_id)
    return tuple(target), tuple(preservation), tuple(challenge)


def _causal_cuts(
    diff: ActualDiff, symbol_ids: tuple[str, ...], target_checks: tuple[str, ...]
) -> tuple[str, ...]:
    scopes = {relation.qualified_scope for relation in diff.changed_relations}
    priority = {
        "return": 0,
        "guard": 1,
        "dispatch": 2,
        "state": 3,
        "exception": 4,
        "call": 5,
        "assignment": 6,
    }
    related = sorted(
        diff.changed_relations,
        key=lambda relation: (
            min((rank for kind, rank in priority.items() if kind in relation.kind), default=9),
            relation.relation_id,
        ),
    )
    cuts = [relation.relation_id for relation in related[:8]]
    if not cuts and target_checks:
        cuts.extend(stable_id("causal-cut", check_id, symbol_ids) for check_id in target_checks)
    del scopes
    return tuple(cuts)


def _impact_cone(
    diff: ActualDiff, program: Any, symbol_ids: tuple[str, ...], depth: int = 2
) -> tuple[str, ...]:
    visited = set(symbol_ids)
    frontier = set(symbol_ids)
    edges = getattr(program, "edges", {})
    for _ in range(max(0, depth)):
        adjacent: set[str] = set()
        for edge in edges.values():
            sources = set(map(str, getattr(edge, "source_ids", ())))
            targets = set(map(str, getattr(edge, "target_ids", ())))
            if frontier & sources:
                adjacent.update(targets)
            if frontier & targets and getattr(edge, "kind", "") in {
                "dispatch", "exception_flow", "return_flow", "field_flow", "calls",
            }:
                adjacent.update(sources)
        adjacent -= visited
        visited.update(adjacent)
        frontier = adjacent
    relation_ids = {relation.relation_id for relation in diff.changed_relations}
    return tuple(sorted(visited | relation_ids))[:200]


def build_active_binding_graph(
    requirement_graph: Any,
    program_slice: Any,
    diff_analysis: ActualDiff,
    target_recovery: Any,
    public_tests: Iterable[Any],
    previous_graph: ActiveBindingGraph | None = None,
    *,
    instance_id: str | None = None,
    revision: int | None = None,
    active_slice_max_files: int = 12,
    active_slice_max_symbols: int = 40,
    direct_caller_depth: int = 2,
    impact_cone_depth: int = 2,
) -> ActiveBindingGraph:
    """Project requirements, the actual diff, bounded paths, and real checks."""

    del direct_caller_depth
    if active_slice_max_files < 1 or active_slice_max_symbols < 1:
        raise ValueError("active slice bounds must be positive")
    selected_files = set(diff_analysis.changed_files[:active_slice_max_files])
    resolved_instance_id = instance_id or getattr(
        previous_graph, "instance_id", getattr(requirement_graph, "assignment_id", "unknown")
    )
    units: dict[str, ActiveBindingUnit] = {}
    edges: list[BindingEdge] = []
    gaps: list[BindingGap] = []
    all_targets: list[str] = []
    all_preservation: list[str] = []
    all_challenges: list[str] = []
    for leaf in _requirement_leaves(requirement_graph):
        requirement_id = _leaf_id(leaf)
        binding_id = stable_id("active-binding", resolved_instance_id, requirement_id)
        symbols = _candidate_symbols(leaf, program_slice, selected_files)[:active_slice_max_symbols]
        path_ids, branch_ids = _path_projection(program_slice, symbols)
        protocol_ids = _protocol_projection(program_slice, symbols)
        hunk_ids = _hunks_for_symbols(diff_analysis, program_slice, symbols)
        if not hunk_ids and len(_requirement_leaves(requirement_graph)) == 1:
            hunk_ids = tuple(hunk.hunk_id for hunk in diff_analysis.hunks)
        target_ids, preservation_ids, challenge_ids = _check_projection(
            leaf, target_recovery, public_tests
        )
        cuts = _causal_cuts(diff_analysis, symbols, target_ids)
        impacts = _impact_cone(
            diff_analysis, program_slice, symbols, depth=impact_cone_depth
        )
        previous = previous_graph.units.get(binding_id) if previous_graph else None
        if not symbols:
            status = ActiveBindingStatus.UNBOUND.value
            reason = "no bounded program symbol could be projected from the requirement or diff"
            gaps.append(BindingGap(
                requirement_id=requirement_id,
                gap_type="PROGRAM_PATH_UNBOUND",
                reason=reason,
                attempted_bindings=tuple(diff_analysis.changed_files),
                next_recovery_actions=(
                    "search_explicit_issue_symbols", "inspect_changed_symbol_callers",
                    "inspect_relevant_public_tests",
                ),
            ))
        elif target_ids or preservation_ids or challenge_ids:
            status = ActiveBindingStatus.BOUND_EXECUTABLE.value
            reason = None
        else:
            status = ActiveBindingStatus.ORACLE_UNAVAILABLE.value
            reason = "static requirement/diff binding exists but no trusted executable oracle is available"
            gaps.append(BindingGap(
                requirement_id=requirement_id,
                gap_type="ORACLE_UNAVAILABLE",
                reason=reason,
                attempted_bindings=tuple(hunk_ids),
                next_recovery_actions=(
                    "derive_issue_witness", "derive_baseline_relation", "run_preservation_replay",
                ),
            ))
        historical = tuple(dict.fromkeys((
            *(previous.historical_hunk_ids if previous else ()),
            *(previous.changed_hunk_ids if previous else ()),
        )))
        unit = ActiveBindingUnit(
            binding_id=binding_id,
            requirement_id=requirement_id,
            requirement_text=_leaf_text(leaf),
            requirement_authority=_leaf_authority(leaf),
            program_symbol_ids=symbols,
            path_class_ids=path_ids,
            branch_partition_ids=branch_ids,
            protocol_edge_ids=protocol_ids,
            changed_hunk_ids=hunk_ids,
            causal_cut_ids=cuts,
            impact_cone_ids=impacts,
            target_check_ids=target_ids,
            preservation_check_ids=preservation_ids,
            challenge_check_ids=challenge_ids,
            counterexample_ids=previous.counterexample_ids if previous else (),
            evidence_ids=tuple(dict.fromkeys((
                *_leaf_evidence(leaf), *target_ids, *preservation_ids, *challenge_ids,
            ))),
            status=status,
            confidence=min(1.0, 0.2 + 0.15 * bool(symbols) + 0.2 * bool(hunk_ids)
                           + 0.25 * bool(target_ids or preservation_ids or challenge_ids)
                           + 0.2 * bool(path_ids)),
            unresolved_reason=reason,
            historical_hunk_ids=historical,
            closed_counterexample_ids=previous.closed_counterexample_ids if previous else (),
        )
        units[binding_id] = unit
        for symbol_id in symbols:
            edges.append(BindingEdge(requirement_id, symbol_id, "REQUIREMENT_PROGRAM"))
        for hunk_id in hunk_ids:
            edges.append(BindingEdge(binding_id, hunk_id, "UNIT_CHANGED_HUNK"))
        for path_id in path_ids:
            edges.append(BindingEdge(binding_id, path_id, "UNIT_PATH_CLASS"))
        for check_id in (*target_ids, *preservation_ids, *challenge_ids):
            edges.append(BindingEdge(binding_id, check_id, "UNIT_EXECUTABLE_CHECK", (check_id,)))
        all_targets.extend(target_ids)
        all_preservation.extend(preservation_ids)
        all_challenges.extend(challenge_ids)
    history = list(previous_graph.history) if previous_graph else []
    if previous_graph is not None:
        history.append({
            "revision": previous_graph.revision,
            "diff_hash": previous_graph.diff_hash,
            "unit_statuses": {
                key: value.status for key, value in sorted(previous_graph.units.items())
            },
        })
    graph = ActiveBindingGraph(
        instance_id=resolved_instance_id,
        revision=revision if revision is not None else (previous_graph.revision + 1 if previous_graph else 1),
        diff_hash=diff_analysis.canonical_diff_hash,
        program_slice_hash=_program_hash(program_slice),
        requirement_graph_hash=_requirement_hash(requirement_graph),
        units=units,
        edges=edges,
        unresolved_gaps=gaps,
        target_check_ids=all_targets,
        preservation_check_ids=all_preservation,
        challenge_check_ids=all_challenges,
        history=history,
        build_stats={
            "changed_file_count": len(selected_files),
            "changed_hunk_count": len(diff_analysis.hunks),
            "projected_symbol_count": len({symbol for unit in units.values() for symbol in unit.program_symbol_ids}),
            "reused_unit_count": sum(
                previous_graph is not None and unit.binding_id in previous_graph.units
                for unit in units.values()
            ),
            "candidate_count": len(units),
            "active_count": sum(
                unit.status == ActiveBindingStatus.BOUND_EXECUTABLE.value
                for unit in units.values()
            ),
            "deferred_count": sum(
                unit.status in {
                    ActiveBindingStatus.UNBOUND.value,
                    ActiveBindingStatus.ORACLE_UNAVAILABLE.value,
                    ActiveBindingStatus.UNKNOWN.value,
                }
                for unit in units.values()
            ),
        },
    )
    return graph


def _comparison_status(unit: ActiveBindingUnit, observations: Any) -> str | None:
    comparisons = tuple(getattr(observations, "check_comparisons", observations or ()))
    relevant = [
        item for item in comparisons
        if getattr(item, "check_id", None) in {
            *unit.target_check_ids, *unit.preservation_check_ids, *unit.challenge_check_ids,
        }
    ]
    if not relevant:
        return None
    classifications = {
        getattr(getattr(item, "classification", None), "value", getattr(item, "classification", ""))
        for item in relevant
    }
    if "PRESERVATION_REGRESSION" in classifications:
        return ActiveBindingStatus.PRESERVATION_RISK.value
    if classifications & {
        "TARGET_STILL_FAILING", "TARGET_REGRESSED",
    }:
        return ActiveBindingStatus.FAILING.value
    target_ids = set(unit.target_check_ids)
    target_rows = [item for item in relevant if getattr(item, "check_id", None) in target_ids]
    if target_rows and all(
        getattr(getattr(item, "classification", None), "value", item.classification)
        == "TARGET_FIXED"
        for item in target_rows
    ):
        return ActiveBindingStatus.PASSING.value
    if all(
        getattr(getattr(item, "classification", None), "value", item.classification)
        == "PASS_PRESERVED"
        for item in relevant
    ):
        return ActiveBindingStatus.PASSING.value
    return ActiveBindingStatus.UNKNOWN.value


def update_active_binding_graph(
    previous_graph: ActiveBindingGraph,
    new_diff: ActualDiff,
    new_observations: Any,
    new_counterexamples: Iterable[Any],
    new_requirements: Any | None = None,
    *,
    program_slice: Any | None = None,
    target_recovery: Any | None = None,
    public_tests: Iterable[Any] = (),
) -> ActiveBindingGraph:
    """Increment only units touched by the new diff or new evidence."""

    counterexamples = tuple(new_counterexamples)
    touched_hunks = {hunk.hunk_id for hunk in new_diff.hunks}
    touched_files = set(new_diff.changed_files)
    if new_requirements is not None and program_slice is not None:
        candidate = build_active_binding_graph(
            new_requirements,
            program_slice,
            new_diff,
            target_recovery,
            public_tests,
            previous_graph=previous_graph,
            instance_id=previous_graph.instance_id,
            revision=previous_graph.revision + 1,
        )
    else:
        candidate = ActiveBindingGraph(
            instance_id=previous_graph.instance_id,
            revision=previous_graph.revision + 1,
            diff_hash=new_diff.canonical_diff_hash,
            program_slice_hash=(
                _program_hash(program_slice) if program_slice is not None
                else previous_graph.program_slice_hash
            ),
            requirement_graph_hash=previous_graph.requirement_graph_hash,
            units=previous_graph.units,
            edges=previous_graph.edges,
            unresolved_gaps=previous_graph.unresolved_gaps,
            target_check_ids=previous_graph.target_check_ids,
            preservation_check_ids=previous_graph.preservation_check_ids,
            challenge_check_ids=previous_graph.challenge_check_ids,
            history=previous_graph.history,
        )
    rebuilt = 0
    reused = 0
    updated_units: dict[str, ActiveBindingUnit] = {}
    active_hunks = {hunk.hunk_id for hunk in new_diff.hunks}
    for binding_id, unit in candidate.units.items():
        unit_files = {
            str(getattr(program_slice, "nodes", {}).get(symbol_id).attributes.get("file", ""))
            for symbol_id in unit.program_symbol_ids
            if program_slice is not None
            and getattr(program_slice, "nodes", {}).get(symbol_id) is not None
        }
        packets = tuple(
            packet for packet in counterexamples
            if getattr(packet, "binding_unit_id", None) in {None, binding_id}
            and (
                getattr(packet, "binding_unit_id", None) == binding_id
                or set(getattr(packet, "candidate_repair_cut_ids", ())) & set(unit.causal_cut_ids)
            )
        )
        affected = bool(
            set(unit.changed_hunk_ids) & touched_hunks
            or unit_files & touched_files
            or packets
            or binding_id not in previous_graph.units
        )
        if not affected:
            previous = previous_graph.units[binding_id]
            updated_units[binding_id] = replace(
                previous,
                changed_hunk_ids=tuple(
                    hunk_id for hunk_id in previous.changed_hunk_ids if hunk_id in active_hunks
                ),
                historical_hunk_ids=tuple(dict.fromkeys((
                    *previous.historical_hunk_ids,
                    *(hunk_id for hunk_id in previous.changed_hunk_ids if hunk_id not in active_hunks),
                ))),
            )
            reused += 1
            continue
        status = _comparison_status(unit, new_observations) or unit.status
        packet_ids = tuple(dict.fromkeys((
            *unit.counterexample_ids,
            *(str(getattr(packet, "counterexample_id", "")) for packet in packets),
        )))
        if packets and status not in {
            ActiveBindingStatus.FAILING.value,
            ActiveBindingStatus.PRESERVATION_RISK.value,
        }:
            status = ActiveBindingStatus.COUNTEREXAMPLE_OPEN.value
        current_signatures = {
            getattr(getattr(item, "patched", None), "failure_signature", None)
            for item in tuple(getattr(new_observations, "check_comparisons", ()))
        }
        closed = tuple(dict.fromkeys((
            *unit.closed_counterexample_ids,
            *(
                str(getattr(packet, "counterexample_id", ""))
                for packet in packets
                if getattr(packet, "failure_signature", None) not in current_signatures
            ),
        )))
        updated_units[binding_id] = replace(
            unit,
            status=status,
            counterexample_ids=packet_ids,
            closed_counterexample_ids=closed,
            unresolved_reason=(None if status in {
                ActiveBindingStatus.PASSING.value,
                ActiveBindingStatus.FAILING.value,
                ActiveBindingStatus.PRESERVATION_RISK.value,
                ActiveBindingStatus.COUNTEREXAMPLE_OPEN.value,
            } else unit.unresolved_reason),
        )
        rebuilt += 1
    candidate.units = updated_units
    candidate.build_stats.update({
        "incrementally_rebuilt_unit_count": rebuilt,
        "incrementally_reused_unit_count": reused,
    })
    return candidate
