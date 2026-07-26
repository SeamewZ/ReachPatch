"""Universal requirement graph and symbolic path obligations."""

from typing import Any

from .domains import promote_program_predicates, symbolic_scenario_partitions
from .models import RequirementGraph, RequirementLeaf, RequirementPathObligation


def compile_assignment_overlay(*args: Any, **kwargs: Any) -> RequirementGraph:
    from .compiler import compile_assignment_overlay as implementation

    return implementation(*args, **kwargs)


def compile_requirement_paths(*args: Any, **kwargs: Any) -> RequirementGraph:
    from .compiler import compile_requirement_paths as implementation

    return implementation(*args, **kwargs)


def compile_requirement_core(*args: Any, **kwargs: Any) -> RequirementGraph:
    from .compiler import compile_requirement_core as implementation
    return implementation(*args, **kwargs)


def refresh_requirement_paths(*args: Any, **kwargs: Any):
    from .compiler import refresh_requirement_paths as implementation
    return implementation(*args, **kwargs)


def promote_domains_from_diff(*args: Any, **kwargs: Any):
    from .compiler import promote_domains_from_diff as implementation
    return implementation(*args, **kwargs)

__all__ = [
    "RequirementGraph",
    "RequirementLeaf",
    "RequirementPathObligation",
    "compile_assignment_overlay",
    "compile_requirement_core",
    "compile_requirement_paths",
    "refresh_requirement_paths",
    "promote_domains_from_diff",
    "promote_program_predicates",
    "symbolic_scenario_partitions",
]
