"""Reach--Avoid public API.

The controller is loaded lazily so model modules can use the independent
frontier records without creating an import cycle during package bootstrap.
"""

__all__ = ["ReachAvoidConfig", "ReachAvoidController"]

def __getattr__(name):
    if name in __all__:
        from .controller import ReachAvoidConfig, ReachAvoidController
        return {"ReachAvoidConfig": ReachAvoidConfig, "ReachAvoidController": ReachAvoidController}[name]
    raise AttributeError(name)
