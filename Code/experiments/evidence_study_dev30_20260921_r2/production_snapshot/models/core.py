from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import SerializableRecord


@dataclass(frozen=True, slots=True)
class Instance(SerializableRecord):
    instance_id: str
    repository: str
    base_commit: str
    issue: str
    visible_tests: tuple[str, ...] = ()
    public_metadata: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)

    def repository_path(self) -> Path:
        path = Path(self.repository).resolve()
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path
