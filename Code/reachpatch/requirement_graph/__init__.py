from .compiler import (
    ClaimRole, CompiledRequirementClaim, EvidenceSpan, GoalContract,
    RequirementCompilation, compile_goal_contracts,
    compile_requirement_contract, validate_compiled_claim,
)

__all__ = [
    "build_requirement_graph", "promote_diff_partitions", "GoalContract",
    "compile_goal_contracts", "ClaimRole", "CompiledRequirementClaim",
    "EvidenceSpan", "RequirementCompilation", "compile_requirement_contract",
    "validate_compiled_claim",
]

def __getattr__(name):
    # The graph builder is a historical artifact reader only.  Keep it lazy so
    # importing the execution-driven compiler cannot pull the retired
    # RequirementGraph production path into the controller.
    if name == "build_requirement_graph":
        from .builder import build_requirement_graph
        return build_requirement_graph
    if name == "promote_diff_partitions":
        from .update import promote_diff_partitions
        return promote_diff_partitions
    raise AttributeError(name)
