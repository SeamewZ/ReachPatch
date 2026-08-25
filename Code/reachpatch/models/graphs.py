from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .base import SerializableRecord, content_hash
from .evidence import (
    ActualDiff, ExceptionContract, ExecutableOracle, ObservationContract,
    OutcomeStatus,
)


class GraphConsistencyError(RuntimeError):
    """Raised when any graph points at a stale integrated dependency."""


@dataclass(frozen=True, slots=True)
class RequirementVariable(SerializableRecord):
    name: str
    type_hint: str | None = None
    role: str = "input"


@dataclass(frozen=True, slots=True)
class ChallengePartition(SerializableRecord):
    partition_id: str
    requirement_id: str
    kind: str
    predicate: str
    source_branch_id: str
    source_hunk_id: str
    path_class_id: str
    status: OutcomeStatus = OutcomeStatus.UNKNOWN


@dataclass(frozen=True, slots=True)
class RequirementLeaf(SerializableRecord):
    requirement_id: str
    kind: str
    quantifier: str
    variables: tuple[RequirementVariable, ...]
    domain_constraints: tuple[str, ...]
    preconditions: tuple[str, ...]
    operation: str
    expected_observation: ObservationContract
    exception_contract: ExceptionContract | None
    preservation: bool
    authority: str
    evidence_ids: tuple[str, ...]
    witness_ids: tuple[str, ...]
    status: OutcomeStatus
    hard: bool = True
    domain_partitions: tuple[str, ...] = ()
    executable: bool = True
    issue_evidence_spans: tuple[dict[str, Any], ...] = ()


@dataclass(slots=True)
class RequirementGraph(SerializableRecord):
    leaves: dict[str, RequirementLeaf]
    challenge_partitions: dict[str, ChallengePartition] = field(default_factory=dict)
    evidence_hash: str = ""

    def graph_hash(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True, slots=True)
class RequirementDelta(SerializableRecord):
    graph: RequirementGraph
    added_partition_ids: tuple[str, ...]
    update_seconds: float


class ProgramNodeKind(StrEnum):
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    BASIC_BLOCK = "BASIC_BLOCK"
    BRANCH = "BRANCH"
    RETURN = "RETURN"
    RAISE = "RAISE"
    CALL_SITE = "CALL_SITE"
    PARAMETER = "PARAMETER"
    LOCAL_VALUE = "LOCAL_VALUE"
    ATTRIBUTE = "ATTRIBUTE"
    STATE_READ = "STATE_READ"
    STATE_WRITE = "STATE_WRITE"
    PROTOCOL_DISPATCH = "PROTOCOL_DISPATCH"
    EXTERNAL_EFFECT = "EXTERNAL_EFFECT"


class ProgramEdgeKind(StrEnum):
    CONTAINS = "CONTAINS"
    CALLS = "CALLS"
    MAY_CALL = "MAY_CALL"
    EXECUTED_CALL = "EXECUTED_CALL"
    CONTROL_TRUE = "CONTROL_TRUE"
    CONTROL_FALSE = "CONTROL_FALSE"
    DATA_FLOW = "DATA_FLOW"
    RETURN_FLOW = "RETURN_FLOW"
    EXCEPTION_FLOW = "EXCEPTION_FLOW"
    STATE_READ = "STATE_READ"
    STATE_WRITE = "STATE_WRITE"
    DISPATCH = "DISPATCH"
    REFLECTED_DISPATCH = "REFLECTED_DISPATCH"
    ALIAS = "ALIAS"
    CONSUMER = "CONSUMER"


@dataclass(frozen=True, slots=True)
class ProgramNode(SerializableRecord):
    node_id: str
    kind: ProgramNodeKind
    path: str
    symbol: str
    start_line: int
    end_line: int
    editable: bool
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProgramEdge(SerializableRecord):
    edge_id: str
    source_id: str
    target_id: str
    kind: ProgramEdgeKind
    dynamic_confirmed: bool
    trace_bundle_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PathClass(SerializableRecord):
    path_class_id: str
    entrypoint: str
    ordered_guard_outcomes: tuple[str, ...]
    dispatch_route: str
    exit_kind: str
    observed_effect_kind: str
    loop_class: str = "0"
    recursion_class: str = "NONE"
    node_ids: tuple[str, ...] = ()

    def key(self) -> tuple[str, tuple[str, ...], str, str, str]:
        return (
            self.entrypoint, self.ordered_guard_outcomes, self.dispatch_route,
            self.exit_kind, self.observed_effect_kind,
        )


@dataclass(frozen=True, slots=True)
class CausalRepairCut(SerializableRecord):
    cut_id: str
    observation_node_id: str
    responsible_node_ids: tuple[str, ...]
    earliest_editable_node_id: str
    changed_hunk_ids: tuple[str, ...]
    preservation_consumer_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ImpactCone(SerializableRecord):
    cone_id: str
    changed_hunk_ids: tuple[str, ...]
    direct_caller_ids: tuple[str, ...]
    return_consumer_ids: tuple[str, ...]
    exception_handler_ids: tuple[str, ...]
    state_reader_ids: tuple[str, ...]
    reverse_dispatch_ids: tuple[str, ...]
    rendering_consumer_ids: tuple[str, ...]
    public_check_ids: tuple[str, ...]

    def all_risk_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            self.direct_caller_ids + self.return_consumer_ids
            + self.exception_handler_ids + self.state_reader_ids
            + self.reverse_dispatch_ids + self.rendering_consumer_ids
            + self.public_check_ids
        ))


@dataclass(frozen=True, slots=True)
class GraphBudget(SerializableRecord):
    max_files: int = 40
    max_nodes: int = 4000
    max_edges: int = 12000
    direct_caller_depth: int = 1


@dataclass(frozen=True, slots=True)
class ContextRequest(SerializableRecord):
    request_id: str
    action: str
    symbol_id: str
    depth: int = 1


@dataclass(slots=True)
class ProgramGraph(SerializableRecord):
    patch_hash: str
    base_commit: str
    nodes: dict[str, ProgramNode]
    edges: dict[str, ProgramEdge]
    path_classes: dict[str, PathClass]
    file_hashes: dict[str, str]
    symbol_index: dict[str, tuple[str, ...]] = field(default_factory=dict)
    causal_cuts: dict[str, CausalRepairCut] = field(default_factory=dict)
    impact_cone: ImpactCone | None = None
    frontier_requests: tuple[ContextRequest, ...] = ()
    files_reparsed: int = 0
    symbols_expanded: int = 0
    cache_hits: int = 0

    def graph_hash(self) -> str:
        value = self.to_dict()
        for metric in ("files_reparsed", "symbols_expanded", "cache_hits"):
            value.pop(metric, None)
        return content_hash(value)


@dataclass(frozen=True, slots=True)
class ProgramGraphDelta(SerializableRecord):
    graph: ProgramGraph
    added_node_ids: tuple[str, ...]
    removed_node_ids: tuple[str, ...]
    files_reparsed: int
    symbols_expanded: int
    cache_hits: int
    update_seconds: float


class BindingStatus(StrEnum):
    UNBOUND = "UNBOUND"
    STATIC_ACTIONABLE = "STATIC_ACTIONABLE"
    EXECUTION_CONFIRMED = "EXECUTION_CONFIRMED"
    TARGET_FAILING = "TARGET_FAILING"
    TARGET_PASSING = "TARGET_PASSING"
    PRESERVATION_RISK = "PRESERVATION_RISK"
    COUNTEREXAMPLE_OPEN = "COUNTEREXAMPLE_OPEN"
    ORACLE_UNAVAILABLE = "ORACLE_UNAVAILABLE"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"

    @property
    def execution_confirmed(self) -> bool:
        return self in {
            BindingStatus.EXECUTION_CONFIRMED,
            BindingStatus.TARGET_FAILING,
            BindingStatus.TARGET_PASSING,
            BindingStatus.PRESERVATION_RISK,
            BindingStatus.COUNTEREXAMPLE_OPEN,
        }


class BindingRecoveryAction(StrEnum):
    EXPAND_DIRECT_CALLER = "EXPAND_DIRECT_CALLER"
    TRACE_PUBLIC_CHECK = "TRACE_PUBLIC_CHECK"
    TRACE_ISSUE_WITNESS = "TRACE_ISSUE_WITNESS"
    EXPAND_RETURN_CONSUMER = "EXPAND_RETURN_CONSUMER"
    EXPAND_EXCEPTION_HANDLER = "EXPAND_EXCEPTION_HANDLER"
    EXPAND_PROTOCOL_DISPATCH = "EXPAND_PROTOCOL_DISPATCH"
    MATERIALIZE_BRANCH_PARTITION = "MATERIALIZE_BRANCH_PARTITION"


@dataclass(frozen=True, slots=True)
class BindingUnit(SerializableRecord):
    binding_id: str
    requirement_id: str
    path_class_id: str
    program_symbol_ids: tuple[str, ...]
    branch_partition_ids: tuple[str, ...]
    changed_hunk_ids: tuple[str, ...]
    causal_cut_ids: tuple[str, ...]
    impact_cone_ids: tuple[str, ...]
    target_check_ids: tuple[str, ...]
    preservation_check_ids: tuple[str, ...]
    challenge_ids: tuple[str, ...]
    trace_bundle_ids: tuple[str, ...]
    counterexample_ids: tuple[str, ...]
    authority: str
    status: BindingStatus
    evidence_ids: tuple[str, ...]
    # Requirement-to-path binding is independent of the current patch.  The
    # overlay records whether this particular working diff touches its causal
    # route, so an incorrect edit remains visible as DISJOINT evidence.
    alignment_status: str = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class BindingGap(SerializableRecord):
    requirement_id: str
    gap_type: str
    hard: bool
    attempted_symbols: tuple[str, ...]
    next_recovery_actions: tuple[BindingRecoveryAction, ...]
    gap_id: str | None = None

    def __post_init__(self) -> None:
        from .base import stable_id
        if self.gap_id is None:
            object.__setattr__(self, "gap_id", stable_id(
                "binding-gap", self.requirement_id, self.gap_type,
                tuple(self.attempted_symbols), tuple(self.next_recovery_actions),
            ))


@dataclass(slots=True)
class BindingGraph(SerializableRecord):
    patch_hash: str
    requirement_hash: str
    program_hash: str
    units: dict[str, BindingUnit]
    gaps: tuple[BindingGap, ...] = ()

    def graph_hash(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True, slots=True)
class BindingGraphDelta(SerializableRecord):
    graph: BindingGraph
    confirmed_binding_ids: tuple[str, ...]
    changed_binding_ids: tuple[str, ...]
    update_seconds: float


@dataclass(frozen=True, slots=True)
class InputRecipe(SerializableRecord):
    recipe_id: str
    kind: str
    concrete_input: Any
    derivation: tuple[str, ...]
    command: tuple[str, ...]
    source_check_id: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    trace_symbols: tuple[str, ...] = ()
    # A direct graph invocation is a synthesized probe, not reporter-owned
    # witness code.  Execution uses this distinction to keep probe-construction
    # errors out of Requirement failures.
    call_mode: str = "PUBLIC_CHECK"


@dataclass(frozen=True, slots=True)
class InputRecipeResult(SerializableRecord):
    recipe: InputRecipe | None
    frontier: str | None
    unreachable: bool = False


@dataclass(frozen=True, slots=True)
class ExecutableScenario(SerializableRecord):
    scenario_id: str
    command: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    timeout_seconds: float


class ChallengeStatus(StrEnum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"
    UNREACHABLE = "UNREACHABLE"
    EXPLORATION_ONLY = "EXPLORATION_ONLY"
    INVALID_RECIPE = "INVALID_RECIPE"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class ChallengeCell(SerializableRecord):
    challenge_id: str
    patch_hash: str
    requirement_id: str
    binding_id: str
    path_class_id: str
    changed_hunk_ids: tuple[str, ...]
    kind: str
    input_recipe: InputRecipe
    execution_scenario: ExecutableScenario
    observation_contract: ObservationContract
    oracle: ExecutableOracle
    authority: str
    baseline_outcome: OutcomeStatus | None
    patched_outcome: OutcomeStatus | None
    trace_bundle_id: str | None
    stability_runs: int
    terminal_status: ChallengeStatus
    hard: bool
    origin: str


@dataclass(slots=True)
class ChallengeGraph(SerializableRecord):
    patch_hash: str
    binding_hash: str
    cells: dict[str, ChallengeCell]
    frontier_attempts: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def graph_hash(self) -> str:
        return content_hash(self.to_dict())

    def active_cells(self) -> tuple[ChallengeCell, ...]:
        return tuple(
            cell for cell in self.cells.values()
            if cell.patch_hash == self.patch_hash
            and cell.terminal_status is not ChallengeStatus.STALE
        )


@dataclass(slots=True)
class GraphStack(SerializableRecord):
    patch_hash: str
    revision: int
    requirement_graph: RequirementGraph
    program_graph: ProgramGraph
    binding_graph: BindingGraph
    challenge_graph: ChallengeGraph

    def graph_hashes(self) -> dict[str, str]:
        return {
            "requirement": self.requirement_graph.graph_hash(),
            "program": self.program_graph.graph_hash(),
            "binding": self.binding_graph.graph_hash(),
            "challenge": self.challenge_graph.graph_hash(),
        }

    def validate(self) -> None:
        if self.program_graph.patch_hash != self.patch_hash:
            raise GraphConsistencyError("program graph uses stale patch")
        if self.binding_graph.patch_hash != self.patch_hash:
            raise GraphConsistencyError("binding graph uses stale patch")
        if self.challenge_graph.patch_hash != self.patch_hash:
            raise GraphConsistencyError("challenge graph uses stale patch")
        if self.binding_graph.requirement_hash != self.requirement_graph.graph_hash():
            raise GraphConsistencyError("stale requirement binding")
        if self.binding_graph.program_hash != self.program_graph.graph_hash():
            raise GraphConsistencyError("stale program binding")
        if self.challenge_graph.binding_hash != self.binding_graph.graph_hash():
            raise GraphConsistencyError("stale challenge binding")


def empty_graph_stack(base_commit: str, patch_hash: str) -> GraphStack:
    requirement = RequirementGraph({})
    program = ProgramGraph(patch_hash, base_commit, {}, {}, {}, {})
    binding = BindingGraph(
        patch_hash, requirement.graph_hash(), program.graph_hash(), {}, (),
    )
    challenge = ChallengeGraph(patch_hash, binding.graph_hash(), {})
    result = GraphStack(patch_hash, 0, requirement, program, binding, challenge)
    result.validate()
    return result
