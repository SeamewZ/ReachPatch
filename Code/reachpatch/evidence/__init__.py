"""Evidence extraction and semantic-hypothesis graph construction."""

from .hypotheses import HypothesisAssignment, enumerate_assignments, freeze_assignment
from .semantic_graph import SemanticGraph, build_semantic_graph

__all__ = [
    "HypothesisAssignment",
    "SemanticGraph",
    "build_semantic_graph",
    "enumerate_assignments",
    "freeze_assignment",
]
