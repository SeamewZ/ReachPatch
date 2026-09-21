"""Reach--Avoid public API.

The controller is loaded lazily so model modules can use the independent
frontier records without creating an import cycle during package bootstrap.
"""

__all__ = ["ReachAvoidConfig", "ReachAvoidController", "ActiveFailureKind", "select_active_failure", "DynamicReachAvoidGraph", "DynamicGraphBudget", "GraphNodeKind", "GraphEdgeKind", "seed_dynamic_graph", "update_graph_from_execution", "update_graph_from_recovery", "expand_dynamic_graph_frontier", "rank_causal_cuts", "derive_validation_obligations", "materialize_graph_guided_challenges", "SearchBudget", "SearchScore", "RepairHypothesis", "CheckpointState", "build_distinct_repair_hypotheses", "select_open_checkpoint_from_graph", "create_trial_checkpoint", "select_best_evaluated_checkpoint", "backtrack_checkpoint"]

def __getattr__(name):
    if name in {"ReachAvoidConfig", "ReachAvoidController"}:
        from .controller import ReachAvoidConfig, ReachAvoidController
        return {"ReachAvoidConfig": ReachAvoidConfig, "ReachAvoidController": ReachAvoidController}[name]
    if name in {"ActiveFailureKind", "select_active_failure"}:
        from .active_failure import ActiveFailureKind, select_active_failure
        return {"ActiveFailureKind": ActiveFailureKind, "select_active_failure": select_active_failure}[name]
    if name in {"DynamicReachAvoidGraph", "DynamicGraphBudget", "GraphNodeKind", "GraphEdgeKind", "seed_dynamic_graph", "update_graph_from_execution", "update_graph_from_recovery", "expand_dynamic_graph_frontier", "rank_causal_cuts", "derive_validation_obligations", "materialize_graph_guided_challenges", "SearchBudget", "SearchScore", "RepairHypothesis", "CheckpointState", "build_distinct_repair_hypotheses", "select_open_checkpoint_from_graph", "create_trial_checkpoint", "select_best_evaluated_checkpoint", "backtrack_checkpoint"}:
        from .dynamic_reach_avoid_graph import (
            DynamicReachAvoidGraph, DynamicGraphBudget, GraphNodeKind,
            GraphEdgeKind, seed_dynamic_graph, update_graph_from_execution,
            update_graph_from_recovery, expand_dynamic_graph_frontier,
            rank_causal_cuts, derive_validation_obligations,
            materialize_graph_guided_challenges, SearchBudget, SearchScore,
            RepairHypothesis, CheckpointState, build_distinct_repair_hypotheses,
            select_open_checkpoint_from_graph, create_trial_checkpoint,
            select_best_evaluated_checkpoint, backtrack_checkpoint,
        )
        return locals()[name]
    raise AttributeError(name)
