from __future__ import annotations

import resource
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Deadline:
    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> "Deadline":
        if seconds <= 0:
            raise ValueError("deadline seconds must be positive")
        return cls(time.monotonic() + seconds)

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())


def current_rss_mib() -> float:
    try:
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024.0 * 1024.0)
    except (OSError, ValueError, IndexError):
        usage = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return usage / (1024.0 * 1024.0) if sys.platform == "darwin" else usage / 1024.0


@dataclass(slots=True)
class GraphBudget:
    deadline: float
    max_nodes: int
    max_edges: int
    max_files: int
    max_functions: int
    max_rss_mib: int
    max_protocol_candidates_per_operation: int = 8
    files: int = 0
    functions: int = 0
    truncated_reason: str | None = None

    @classmethod
    def from_limits(
        cls, *, seconds: float, max_nodes: int, max_edges: int,
        max_files: int, max_functions: int, max_rss_mib: int,
        max_protocol_candidates_per_operation: int = 8,
    ) -> "GraphBudget":
        return cls(
            deadline=time.monotonic() + seconds,
            max_nodes=max_nodes,
            max_edges=max_edges,
            max_files=max_files,
            max_functions=max_functions,
            max_rss_mib=max_rss_mib,
            max_protocol_candidates_per_operation=max_protocol_candidates_per_operation,
        )

    def check(self, *, nodes: int = 0, edges: int = 0) -> bool:
        reason = None
        if time.monotonic() >= self.deadline:
            reason = "DEADLINE"
        elif nodes >= self.max_nodes:
            reason = "NODE_LIMIT"
        elif edges >= self.max_edges:
            reason = "EDGE_LIMIT"
        elif current_rss_mib() >= self.max_rss_mib:
            reason = "RSS_LIMIT"
        if reason is not None:
            self.truncated_reason = self.truncated_reason or reason
            return False
        return True

    def consume_file(self, *, nodes: int = 0, edges: int = 0) -> bool:
        if self.files >= self.max_files:
            self.truncated_reason = self.truncated_reason or "FILE_LIMIT"
            return False
        if not self.check(nodes=nodes, edges=edges):
            return False
        self.files += 1
        return True

    def consume_function(self, *, nodes: int = 0, edges: int = 0) -> bool:
        if self.functions >= self.max_functions:
            self.truncated_reason = self.truncated_reason or "FUNCTION_LIMIT"
            return False
        if not self.check(nodes=nodes, edges=edges):
            return False
        self.functions += 1
        return True
