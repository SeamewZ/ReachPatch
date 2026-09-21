from __future__ import annotations

import time
import re

from reachpatch.models.base import stable_id
from reachpatch.models.evidence import ActualDiff
from reachpatch.models.graphs import (
    ChallengePartition, ProgramNodeKind, ProgramGraph, RequirementDelta,
    RequirementGraph,
)


def _added_lines(hunk) -> tuple[tuple[int, str], ...]:
    current = hunk.new_start
    result: list[tuple[int, str]] = []
    for line in hunk.lines:
        if line.startswith("+"):
            result.append((current, line[1:]))
            current += 1
        elif line.startswith("-"):
            continue
        else:
            current += 1
    return tuple(result)


def _partition_kinds(
    source: str,
    branch_predicate: str | None,
    *,
    state_write: bool,
) -> tuple[str, ...]:
    lowered = source.lower()
    predicate = (branch_predicate or "").lower()
    result: list[str] = []
    if branch_predicate:
        result.extend(("BRANCH_TRUE", "BRANCH_FALSE"))
    if any(token in predicate for token in ("len(", "if not ", "== []", "empty")):
        result.extend(("EMPTY", "NONEMPTY"))
    if branch_predicate and not any(
        token in predicate for token in ("==", "!=", "<", ">", " is ", " in ")
    ):
        result.extend(("WRAPPER_TRUTHY", "WRAPPER_FALSY"))
    if "none" in predicate or re.search(r"\b[A-Za-z_]\w*\s*=\s*None\b", source):
        result.extend(("NONE", "NON_NONE"))
    if branch_predicate and any(token in predicate for token in ("<", ">", "<=", ">=")):
        result.extend(("BOUNDARY_BEFORE", "BOUNDARY_AT", "BOUNDARY_AFTER"))
    if branch_predicate and any(token in lowered for token in ("raise", "except", "try:")):
        result.extend(("EXCEPTION", "NON_EXCEPTION"))
    if any(token in lowered for token in ("__r", "reflected", "notimplemented")):
        result.extend(("FORWARD_DISPATCH", "REVERSE_DISPATCH"))
    if state_write:
        result.extend(("STATE_BEFORE", "STATE_AFTER"))
    return tuple(dict.fromkeys(result))


def promote_diff_partitions(
    requirement_graph: RequirementGraph,
    program_graph: ProgramGraph,
    actual_diff: ActualDiff,
) -> RequirementDelta:
    """Add diff-derived challenge partitions without changing any leaf."""

    started = time.monotonic()
    added: list[str] = []
    current_hunk_ids = {hunk.hunk_id for hunk in actual_diff.hunks}
    requirement_graph.challenge_partitions = {
        partition_id: partition
        for partition_id, partition in requirement_graph.challenge_partitions.items()
        if partition.source_hunk_id in current_hunk_ids
    }
    branches = [
        node for node in program_graph.nodes.values()
        if node.kind is ProgramNodeKind.BRANCH
    ]
    state_writes = [
        node for node in program_graph.nodes.values()
        if node.kind is ProgramNodeKind.STATE_WRITE
    ]
    for hunk in actual_diff.hunks:
        additions = _added_lines(hunk)
        source = "\n".join(line for _, line in additions)
        none_assignment = next((
            (match.group(1), line_number)
            for line_number, line in additions
            for match in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*None\b", line)
        ), None)
        adjacent = [
            node for node in branches
            if node.path == hunk.path
            and node.start_line <= hunk.new_start + max(hunk.new_count, 1)
            and node.end_line >= hunk.new_start
        ]
        if not adjacent:
            adjacent = [None]
        hunk_state_write = any(
            node.path == hunk.path
            and node.start_line <= hunk.new_start + max(hunk.new_count, 1)
            and node.end_line >= hunk.new_start
            for node in state_writes
        )
        for leaf in requirement_graph.leaves.values():
            if leaf.preservation:
                # A preservation check protects the behavior it actually
                # executes. It does not authorize widening one concrete test
                # input into a universal adjacent-input contract.
                continue
            for branch in adjacent:
                branch_id = branch.node_id if branch is not None else stable_id(
                    "diff-branch", hunk.hunk_id,
                )
                path_ids = tuple(
                    path.path_class_id
                    for path in program_graph.path_classes.values()
                    if (
                        branch is not None and branch.node_id in path.node_ids
                    ) or (
                        branch is None and any(
                            program_graph.nodes[node_id].path == hunk.path
                            and any(
                                program_graph.nodes[node_id].start_line <= line
                                <= program_graph.nodes[node_id].end_line
                                for line in (
                                    (none_assignment[1],)
                                    if none_assignment is not None
                                    else hunk.changed_new_lines or (hunk.new_start,)
                                )
                            )
                            for node_id in path.node_ids
                            if node_id in program_graph.nodes
                        )
                    )
                ) or (stable_id("path-frontier", branch_id),)
                for path_id in path_ids:
                    predicate = (
                        str(branch.metadata.get("predicate", ""))
                        if branch is not None else None
                    )
                    for kind in _partition_kinds(
                        source, predicate, state_write=hunk_state_write,
                    ):
                        partition_id = stable_id(
                            "partition", leaf.requirement_id, hunk.hunk_id,
                            branch_id, path_id, kind,
                        )
                        if partition_id in requirement_graph.challenge_partitions:
                            continue
                        requirement_graph.challenge_partitions[partition_id] = ChallengePartition(
                            partition_id=partition_id,
                            requirement_id=leaf.requirement_id,
                            kind=kind,
                            predicate=(
                                predicate
                                or (none_assignment[0] if none_assignment else kind)
                            ),
                            source_branch_id=branch_id,
                            source_hunk_id=hunk.hunk_id,
                            path_class_id=path_id,
                        )
                        added.append(partition_id)
    return RequirementDelta(
        graph=requirement_graph,
        added_partition_ids=tuple(added),
        update_seconds=time.monotonic() - started,
    )
