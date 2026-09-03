"""Reach--Avoid public API.

The controller is loaded lazily so model modules can use the independent
frontier records without creating an import cycle during package bootstrap.
"""

__all__ = ["ReachAvoidConfig", "ReachAvoidController", "ActiveFailureKind", "select_active_failure", "DynamicFailureGraph", "DynamicFailureGraphBudget", "build_dynamic_failure_graph"]

def __getattr__(name):
    if name in {"ReachAvoidConfig", "ReachAvoidController"}:
        from .controller import ReachAvoidConfig, ReachAvoidController
        return {"ReachAvoidConfig": ReachAvoidConfig, "ReachAvoidController": ReachAvoidController}[name]
    if name in {"ActiveFailureKind", "select_active_failure"}:
        from .active_failure import ActiveFailureKind, select_active_failure
        return {"ActiveFailureKind": ActiveFailureKind, "select_active_failure": select_active_failure}[name]
    if name in {"DynamicFailureGraph", "DynamicFailureGraphBudget", "build_dynamic_failure_graph"}:
        from .dynamic_failure_graph import DynamicFailureGraph, DynamicFailureGraphBudget, build_dynamic_failure_graph
        return {"DynamicFailureGraph": DynamicFailureGraph, "DynamicFailureGraphBudget": DynamicFailureGraphBudget, "build_dynamic_failure_graph": build_dynamic_failure_graph}[name]
    raise AttributeError(name)
