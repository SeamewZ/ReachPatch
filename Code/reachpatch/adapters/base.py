from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.graph import GraphNode


@dataclass(frozen=True, slots=True)
class AdapterObservation(SerializableRecord):
    adapter: str
    marker_paths: tuple[str, ...]
    mechanical_command_hints: tuple[tuple[str, ...], ...]
    graph_hints: dict[str, tuple[str, ...]]
    status: str = "OBSERVED_NOT_CORRECTNESS"


class ProjectAdapter:
    name = "python"
    markers: tuple[str, ...] = ()
    command_hints: tuple[tuple[str, ...], ...] = ()
    graph_hints: dict[str, tuple[str, ...]] = {}

    def matches(self, repository: Path) -> bool:
        return any((repository / marker).exists() for marker in self.markers)

    def observe(self, repository: str | Path) -> AdapterObservation:
        root = Path(repository).resolve()
        markers = tuple(sorted(
            marker for marker in self.markers if (root / marker).exists()
        ))
        return AdapterObservation(
            adapter=self.name,
            marker_paths=markers,
            mechanical_command_hints=self.command_hints,
            graph_hints=dict(self.graph_hints),
        )

    def semantic_facts(self, repository: Path) -> tuple[dict[str, str], ...]:
        """Return additive facts; facts never become correctness oracles."""

        return ()

    def blocked_surfaces(self, repository: Path) -> tuple[str, ...]:
        return ()

    def augment_program_graph(self, graph, observation: AdapterObservation, repository: str | Path | None = None) -> None:
        root = Path(repository).resolve() if repository is not None else None
        facts = self.semantic_facts(root) if root is not None else ()
        blocked = self.blocked_surfaces(root) if root is not None else ()
        for fact in facts:
            label = str(fact.get("label", self.name))
            node = GraphNode.create(
                "external_interface",
                label,
                identity=(self.name, fact),
                attributes={
                    "qualified_name": f"adapter.{self.name}.{stable_id(fact)}",
                    "adapter": self.name,
                    "semantic_fact": fact,
                    "observed": True,
                    "correctness_authority": "NONE",
                },
            )
            graph.index_node(node)
        for surface in blocked:
            graph.create_frontier(
                "ADAPTER_EXTERNAL_SURFACE",
                stable_id("adapter-surface", self.name, surface),
                f"{self.name} surface {surface} requires external/framework execution",
                "run an isolated adapter-aware check or retain UNKNOWN",
                hard=True,
            )


def select_adapter(repository: str | Path) -> ProjectAdapter:
    from .python import DjangoAdapter, NumPyAdapter, PythonAdapter, RequestsAdapter, SymPyAdapter

    root = Path(repository).resolve()
    adapters: Iterable[ProjectAdapter] = (
        DjangoAdapter(), SymPyAdapter(), NumPyAdapter(), RequestsAdapter()
    )
    return next((item for item in adapters if item.matches(root)), PythonAdapter())
