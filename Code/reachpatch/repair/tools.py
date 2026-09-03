"""Compatibility import for the execution-driven repair tools.

The production repair protocol lives in :mod:`execution_tools`. This module
is intentionally graph-free so historical callers importing the old path do
not reintroduce GraphStack, binding, or challenge state into the controller.
"""

from reachpatch.execution.worktree import (
    apply_patch_action,
    apply_unified_diff,
    diff_between,
)

from .execution_tools import RepairToolExecutor, TOOL_SCHEMAS

__all__ = [
    "RepairToolExecutor",
    "TOOL_SCHEMAS",
    "apply_patch_action",
    "apply_unified_diff",
    "diff_between",
]
