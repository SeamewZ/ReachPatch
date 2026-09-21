from .mechanical import run_mechanical_checks
from .target_recovery import (
    RejectedTargetCandidate, TargetRecoveryConfig, TargetRecoveryResult,
    TargetRecoveryAgent, TargetRecoveryToolExecutor,
    TARGET_RECOVERY_TOOL_SCHEMAS, recover_target_checks,
)
from .trace import run_trace
from .checks import (
    CheckExecution, ExecutionStatus, execute_check, observation_matches_check,
    semantic_observation_signature,
)
from .worktree import (
    apply_generator_result, apply_patch_action, apply_unified_diff, copy_source_tree,
    create_trial_tree, diff_between, discard_bootstrap_tree, discard_ephemeral_tree,
    register_runtime_root, tree_hash,
)

__all__ = [name for name in globals() if not name.startswith("_")]


def __getattr__(name):
    """Load historical graph-backed execution adapters only on explicit use."""
    if name == "recover_target_scenarios":
        from .target_recovery import recover_target_scenarios
        return recover_target_scenarios
    if name in {
        "clear_execution_hot_cache", "execute_paired",
        "execute_transition_triplet",
    }:
        from .paired import (
            clear_execution_hot_cache, execute_paired,
            execute_transition_triplet,
        )
        return {
            "clear_execution_hot_cache": clear_execution_hot_cache,
            "execute_paired": execute_paired,
            "execute_transition_triplet": execute_transition_triplet,
        }[name]
    raise AttributeError(name)
