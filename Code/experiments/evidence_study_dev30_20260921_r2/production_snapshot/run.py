from __future__ import annotations

import json
from pathlib import Path

from reachpatch.models.core import Instance
from reachpatch.reach_avoid.controller import ReachAvoidConfig


def load_instance(path: str | Path) -> Instance:
    source = Path(path).resolve()
    raw = json.loads(source.read_text(encoding="utf-8"))
    repository = Path(str(raw["repository"]))
    if not repository.is_absolute():
        repository = (source.parent / repository).resolve()
    return Instance(
        instance_id=str(raw["instance_id"]),
        repository=str(repository),
        base_commit=str(raw.get("base_commit", "UNKNOWN")),
        issue=str(raw["issue"]),
        visible_tests=tuple(map(str, raw.get("visible_tests", ()))),
        public_metadata=dict(raw.get("public_metadata", {})),
        environment=dict(raw.get("environment", {})),
    )


def config_from_manifest(manifest: dict) -> ReachAvoidConfig:
    raw = dict(manifest.get("config", {}))
    raw.pop("graph_budget", None)
    return ReachAvoidConfig(**raw)
