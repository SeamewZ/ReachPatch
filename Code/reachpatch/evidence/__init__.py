"""Evidence extraction and semantic-hypothesis graph construction."""

from .hypotheses import (
    HypothesisAssignment,
    HypothesisSet,
    build_hypothesis_set,
    enumerate_assignments,
    freeze_assignment,
)
from .semantic_graph import SemanticGraph, build_semantic_graph

__all__ = [
    "HypothesisAssignment",
    "HypothesisSet",
    "SemanticGraph",
    "build_hypothesis_set",
    "build_semantic_graph",
    "enumerate_assignments",
    "freeze_assignment",
]
