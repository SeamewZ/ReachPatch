from __future__ import annotations

import re

from reachpatch.models.base import stable_id
from reachpatch.models.evidence import ActualDiff, ExecutableCheck
from reachpatch.models.graphs import (
    BindingGap, BindingGraph, BindingRecoveryAction, BindingStatus, BindingUnit,
    ProgramEdgeKind, ProgramGraph, ProgramNodeKind, RequirementGraph,
)


def _terms(value: str) -> set[str]:
    return {
        item.lower() for item in re.findall(r"[A-Za-z_]\w*", value)
        if len(item) > 2
    }


def _matching_symbols(
    operation: str,
    graph: ProgramGraph,
    touched_node_ids: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    terms = _terms(operation)
    terminal = operation.rsplit(".", 1)[-1].casefold()
    qualified = operation.casefold() if "." in operation else None
    if qualified is not None:
        # A dotted requirement identifies an operation in context. Matching
        # only its final segment (notably ``__init__``) creates false static
        # bindings to every same-named method in the local graph. A graph
        # node does not include module prefixes, so first accept qualified
        # suffixes down to class/function scope. A non-dunder terminal may
        # then bind only inside the real diff/impact locus.
        parts = qualified.split(".")
        scoped_suffixes = tuple(
            ".".join(parts[index:])
            for index in range(len(parts) - 1)
        )
        contextual = tuple(
            node.node_id for node in graph.nodes.values()
            if node.kind in {
                ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD,
                ProgramNodeKind.CLASS, ProgramNodeKind.CALL_SITE,
            }
            and any(
                node.symbol.casefold() == suffix
                or node.symbol.casefold().endswith(f".{suffix}")
                for suffix in scoped_suffixes
            )
        )
        if contextual:
            return contextual
        if terminal.startswith("__") and terminal.endswith("__"):
            return ()
        impact_ids = frozenset(
            graph.impact_cone.all_risk_ids()
            if graph.impact_cone is not None else ()
        )
        local_ids = touched_node_ids | impact_ids
        terminal_matches = tuple(
            node for node in graph.nodes.values()
            if node.node_id in local_ids
            and node.kind in {
                ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD,
                ProgramNodeKind.CLASS,
            }
            and (
                node.symbol.casefold() == terminal
                or node.symbol.casefold().endswith(f".{terminal}")
            )
        )
        expanded: list[str] = [node.node_id for node in terminal_matches]
        for matched in terminal_matches:
            if matched.kind is not ProgramNodeKind.CLASS:
                continue
            prefix = f"{matched.symbol}.".casefold()
            expanded.extend(
                node.node_id for node in graph.nodes.values()
                if node.node_id in local_ids
                and node.kind in {ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD}
                and node.symbol.casefold().startswith(prefix)
            )
        return tuple(dict.fromkeys(expanded))
    strong = tuple(
        node.node_id for node in graph.nodes.values()
        if node.kind in {
            ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD,
            ProgramNodeKind.CLASS, ProgramNodeKind.CALL_SITE,
        }
        and (
            node.symbol.casefold() == terminal
            or node.symbol.casefold().endswith(f".{terminal}")
        )
    )
    if strong:
        local_ids = touched_node_ids | frozenset(
            graph.impact_cone.all_risk_ids()
            if graph.impact_cone is not None else ()
        )
        expanded = list(strong)
        for node_id in strong:
            matched = graph.nodes[node_id]
            if matched.kind is not ProgramNodeKind.CLASS:
                continue
            prefix = f"{matched.symbol}.".casefold()
            expanded.extend(
                node.node_id for node in graph.nodes.values()
                if node.node_id in local_ids
                and node.kind in {ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD}
                and node.symbol.casefold().startswith(prefix)
            )
        return tuple(dict.fromkeys(expanded))
    return tuple(
        node.node_id for node in graph.nodes.values()
        if node.kind in {
            ProgramNodeKind.FUNCTION, ProgramNodeKind.METHOD,
            ProgramNodeKind.CLASS, ProgramNodeKind.CALL_SITE,
        }
        and terms.intersection(_terms(node.symbol))
    )


def _touched_hunks_by_node(
    actual_diff: ActualDiff,
    graph: ProgramGraph,
) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for node in graph.nodes.values():
        matching = tuple(
            hunk.hunk_id for hunk in actual_diff.hunks
            if node.path == hunk.path
            and any(
                node.start_line <= line <= node.end_line
                for line in hunk.changed_new_lines or (hunk.new_start,)
            )
        )
        if matching:
            result[node.node_id] = matching
    return result


def _checks_for(
    requirement,
    symbols: tuple[str, ...],
    graph: ProgramGraph,
    checks: tuple[ExecutableCheck, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names = {
        graph.nodes[item].symbol.split(".")[-1]
        for item in symbols if item in graph.nodes
    }
    target: list[str] = []
    preservation: list[str] = []
    for check in checks:
        explicit = requirement.requirement_id in check.requirement_ids
        evidence_bound = bool(
            set(requirement.evidence_ids).intersection(check.source_evidence_ids)
        )
        referenced = bool(names.intersection(check.symbol_references))
        if requirement.preservation:
            bound = explicit or evidence_bound
        else:
            bound = explicit or evidence_bound or referenced
        if not bound:
            continue
        if check.role == "PRESERVATION":
            preservation.append(check.check_id)
        elif check.role == "TARGET":
            target.append(check.check_id)
    return tuple(target), tuple(preservation)


def _recovery_actions(
    graph: ProgramGraph,
    attempted_symbols: tuple[str, ...],
) -> tuple[BindingRecoveryAction, ...]:
    actions = [BindingRecoveryAction.EXPAND_DIRECT_CALLER]
    kinds = {
        graph.nodes[item].kind for item in attempted_symbols if item in graph.nodes
    }
    if ProgramNodeKind.RETURN in kinds:
        actions.append(BindingRecoveryAction.EXPAND_RETURN_CONSUMER)
    if ProgramNodeKind.RAISE in kinds:
        actions.append(BindingRecoveryAction.EXPAND_EXCEPTION_HANDLER)
    if any(
        graph.nodes[item].metadata.get("protocol")
        for item in attempted_symbols if item in graph.nodes
    ):
        actions.append(BindingRecoveryAction.EXPAND_PROTOCOL_DISPATCH)
    if any(
        graph.nodes[item].kind is ProgramNodeKind.BRANCH
        for item in attempted_symbols if item in graph.nodes
    ):
        actions.append(BindingRecoveryAction.MATERIALIZE_BRANCH_PARTITION)
    actions.extend((
        BindingRecoveryAction.TRACE_PUBLIC_CHECK,
        BindingRecoveryAction.TRACE_ISSUE_WITNESS,
    ))
    return tuple(dict.fromkeys(actions))


def build_binding_graph(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    actual_diff: ActualDiff,
    public_checks: tuple[ExecutableCheck, ...],
) -> BindingGraph:
    """Build sparse static links; textual overlap never certifies execution."""

    units: dict[str, BindingUnit] = {}
    gaps: list[BindingGap] = []
    touched_hunks = _touched_hunks_by_node(actual_diff, program_graph)
    touched_node_ids = frozenset(touched_hunks)
    connected_to_touched: dict[str, set[str]] = {}
    for edge in program_graph.edges.values():
        if edge.kind not in {
            ProgramEdgeKind.CALLS, ProgramEdgeKind.MAY_CALL,
            ProgramEdgeKind.EXECUTED_CALL, ProgramEdgeKind.CONTAINS,
            ProgramEdgeKind.RETURN_FLOW, ProgramEdgeKind.EXCEPTION_FLOW,
            ProgramEdgeKind.STATE_READ, ProgramEdgeKind.STATE_WRITE,
            ProgramEdgeKind.DISPATCH, ProgramEdgeKind.REFLECTED_DISPATCH,
            ProgramEdgeKind.CONSUMER,
        }:
            continue
        if edge.target_id in touched_hunks:
            connected_to_touched.setdefault(edge.source_id, set()).add(edge.target_id)
        if edge.source_id in touched_hunks:
            connected_to_touched.setdefault(edge.target_id, set()).add(edge.source_id)
    for requirement in requirement_graph.leaves.values():
        symbols = _matching_symbols(
            requirement.operation, program_graph, touched_node_ids,
        )
        path_classes = tuple(
            path for path in program_graph.path_classes.values()
            if set(path.node_ids).intersection(symbols)
            and (
                set(path.node_ids).intersection(touched_hunks)
                or any(
                    node_id in connected_to_touched
                    for node_id in path.node_ids
                )
            )
        )
        if not path_classes and symbols:
            path_classes = tuple(
                path for path in program_graph.path_classes.values()
                if any(
                    program_graph.nodes[item].path
                    == program_graph.nodes[symbols[0]].path
                    for item in path.node_ids if item in program_graph.nodes
                )
            )[:1]
        if not symbols or not path_classes:
            gaps.append(BindingGap(
                requirement_id=requirement.requirement_id,
                gap_type="NO_EXECUTABLE_PATH" if symbols else "NO_PROGRAM_SYMBOL",
                hard=requirement.hard,
                attempted_symbols=symbols,
                next_recovery_actions=_recovery_actions(program_graph, symbols),
            ))
            continue
        if requirement.preservation:
            path_classes = tuple(sorted(
                path_classes,
                key=lambda path: (
                    not bool(set(path.node_ids).intersection(touched_hunks)),
                    -len(set(path.node_ids).intersection(symbols)),
                    path.path_class_id,
                ),
            )[:1])
        for path in path_classes:
            path_symbols = tuple(
                item for item in path.node_ids if item in program_graph.nodes
            )
            matched_path_symbols = tuple(
                item for item in path_symbols if item in symbols
            )
            causal_neighbors = tuple(dict.fromkeys(
                neighbor
                for item in path_symbols
                for neighbor in connected_to_touched.get(item, ())
            ))
            all_symbols = tuple(dict.fromkeys(
                matched_path_symbols + path_symbols + causal_neighbors
            ))
            hunks = tuple(dict.fromkeys(
                hunk_id for symbol_id in all_symbols
                for hunk_id in touched_hunks.get(symbol_id, ())
            ))
            if not matched_path_symbols or not hunks:
                continue
            partitions = tuple(
                item.partition_id
                for item in requirement_graph.challenge_partitions.values()
                if item.requirement_id == requirement.requirement_id
                and item.path_class_id == path.path_class_id
                and item.source_hunk_id in hunks
            )
            target_checks, preservation_checks = _checks_for(
                requirement, all_symbols, program_graph, public_checks,
            )
            binding_id = stable_id(
                "binding", requirement.requirement_id, path.path_class_id,
                all_symbols, hunks, target_checks, preservation_checks,
            )
            units[binding_id] = BindingUnit(
                binding_id=binding_id,
                requirement_id=requirement.requirement_id,
                path_class_id=path.path_class_id,
                program_symbol_ids=all_symbols,
                branch_partition_ids=partitions,
                changed_hunk_ids=hunks,
                causal_cut_ids=tuple(program_graph.causal_cuts),
                impact_cone_ids=(program_graph.impact_cone.cone_id,) if program_graph.impact_cone else (),
                target_check_ids=target_checks,
                preservation_check_ids=preservation_checks,
                challenge_ids=(),
                trace_bundle_ids=(),
                counterexample_ids=(),
                authority=requirement.authority,
                status=BindingStatus.STATIC_ACTIONABLE,
                evidence_ids=requirement.evidence_ids,
            )
            if not target_checks and not preservation_checks:
                gaps.append(BindingGap(
                    requirement.requirement_id,
                    "NO_EXECUTABLE_CHECK",
                    requirement.hard,
                    all_symbols,
                    _recovery_actions(program_graph, all_symbols),
                ))
        if not any(
            unit.requirement_id == requirement.requirement_id
            for unit in units.values()
        ):
            gaps.append(BindingGap(
                requirement.requirement_id,
                "NO_DIFF_REFERENCED_PATH",
                requirement.hard,
                symbols,
                _recovery_actions(program_graph, symbols),
            ))
    return BindingGraph(
        patch_hash=actual_diff.patch_hash,
        requirement_hash=requirement_graph.graph_hash(),
        program_hash=program_graph.graph_hash(),
        units=units,
        gaps=tuple(gaps),
    )
