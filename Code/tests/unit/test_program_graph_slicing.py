from __future__ import annotations

from reachpatch.models.evidence import (
    DiffHunk, OutcomeStatus, RunObservation, TraceBundle,
)
from reachpatch.models.graphs import (
    ProgramEdge, ProgramEdgeKind, ProgramGraph, ProgramNode, ProgramNodeKind,
)
from reachpatch.program_graph.slicing import compute_causal_repair_cuts


def test_causal_cut_excludes_static_may_call_outside_executed_trace():
    changed = ProgramNode(
        "changed", ProgramNodeKind.BRANCH, "module.py", "target.branch",
        2, 2, True,
    )
    observed = ProgramNode(
        "observed", ProgramNodeKind.RETURN, "module.py", "target.Return",
        3, 3, True,
    )
    unrelated = ProgramNode(
        "unrelated", ProgramNodeKind.CALL_SITE, "other.py", "other.target",
        10, 10, True,
    )
    graph = ProgramGraph(
        "patch", "base",
        {node.node_id: node for node in (changed, observed, unrelated)},
        {
            "causal": ProgramEdge(
                "causal", changed.node_id, observed.node_id,
                ProgramEdgeKind.DATA_FLOW, False,
            ),
            "static": ProgramEdge(
                "static", unrelated.node_id, observed.node_id,
                ProgramEdgeKind.MAY_CALL, False,
            ),
        },
        {}, {"module.py": "changed", "other.py": "other"},
    )
    trace = TraceBundle(
        "trace", "tree", ("python", "check.py"),
        RunObservation(OutcomeStatus.FAIL, 1, "", "failure", 0.1),
        (), (changed.node_id, observed.node_id),
    )
    hunk = DiffHunk(
        "hunk", "module.py", 2, 1, 2, 1, "@@ -2 +2 @@",
        ("-old", "+new"),
    )

    cut = compute_causal_repair_cuts(graph, trace, (hunk,))[0]

    assert cut.earliest_editable_node_id == changed.node_id
    assert cut.responsible_node_ids == (observed.node_id, changed.node_id)
    assert unrelated.node_id not in cut.responsible_node_ids


def test_causal_cut_follows_execution_confirmed_call_edge():
    caller = ProgramNode(
        "caller", ProgramNodeKind.CALL_SITE, "module.py", "caller.target",
        2, 2, True,
    )
    observed = ProgramNode(
        "observed", ProgramNodeKind.RAISE, "module.py", "target.Raise",
        5, 5, True,
    )
    graph = ProgramGraph(
        "patch", "base", {caller.node_id: caller, observed.node_id: observed},
        {
            "call": ProgramEdge(
                "call", caller.node_id, observed.node_id,
                ProgramEdgeKind.CALLS, True, ("trace",),
            ),
        },
        {}, {"module.py": "changed"},
    )
    trace = TraceBundle(
        "trace", "tree", ("python", "check.py"),
        RunObservation(OutcomeStatus.FAIL, 1, "", "failure", 0.1),
        (), (caller.node_id, observed.node_id),
    )
    hunk = DiffHunk(
        "hunk", "module.py", 2, 1, 2, 1, "@@ -2 +2 @@",
        ("-old", "+new"),
    )

    cut = compute_causal_repair_cuts(graph, trace, (hunk,))[0]

    assert cut.responsible_node_ids == (observed.node_id, caller.node_id)
