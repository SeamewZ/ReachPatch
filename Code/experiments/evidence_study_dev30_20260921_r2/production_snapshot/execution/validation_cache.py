"""Explicitly opted-in stable execution reuse within one case environment."""
from __future__ import annotations

import os
import sys
import shutil
from pathlib import Path
from typing import Any

from reachpatch.models.base import content_hash
from .worktree import tree_hash


def validation_cache_key(tree: Path, clean: Path, check: Any) -> str | None:
    recipe = check.input_recipe if isinstance(check.input_recipe, dict) else {}
    policy = recipe.get("cache_policy", {})
    if not isinstance(policy, dict) or policy.get("isolated_deterministic") is not True:
        return None  # NO_EXPLICIT_DETERMINISM_ATTESTATION
    if policy.get("external_state") is not False or not policy.get("environment_fingerprint"):
        return None  # EXTERNAL_STATE_OR_DEPENDENCIES_UNIDENTIFIED
    interpreter = shutil.which(check.command[0]) if check.command else None
    if interpreter is None:
        return None  # EXECUTION_BACKEND_UNRESOLVED
    binary = Path(interpreter).resolve()
    stat = binary.stat()
    # Only hashes leave this function: the inherited environment can contain
    # credentials and must never be written to a graph artifact in clear text.
    environment_hash = content_hash(dict(os.environ))
    return content_hash({"tree": tree_hash(tree), "clean": tree_hash(clean), "check": check.to_dict(),
        "environment": environment_hash, "backend": (str(binary), stat.st_size, stat.st_mtime_ns, sys.version),
        "trace_adapter": content_hash(Path(__file__).with_name("trace.py").read_text()),
        "observation_adapter": content_hash(Path(__file__).with_name("checks.py").read_text()),
        "stability_runs": 2})
