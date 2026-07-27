# ReachPatch Implementation Report

## Scope

This report describes the executable implementation in `Code/reachpatch`.
The system is a generic Python repository controller: it derives requirements
from public evidence, constructs four linked graphs, executes paired
challenges, applies one structured repair action at a time, and seals one
working patch. It does not read hidden tests, gold patches, selector scores, or
pre-recorded repair trajectories.

## End-to-End Path

`ReachPatchController.analyze()` performs semantic extraction and assignment,
builds the Python Program Graph, compiles Requirement paths, builds the
Requirement x Program Binding Graph, materializes Challenge Cells, initializes
an immutable Worktree checkpoint, and executes the baseline paired challenges.
`_drive()` selects one losing core and one intent per turn. The generator
session produces a `RepairAction`; `apply_registered_operator()` validates AST
preconditions, edits only the causal cut, and reconciles the actual diff.
`evaluate_single_update()` then runs mechanical checks, graph/DICC closure,
paired execution, counterexample construction, progress, and the Reach-Avoid
gate before atomically committing or rolling back that one trial.

At terminal reach, `seal()` runs edit-retention ablation on the same checkpoint
lineage. Every removed edit group is tried in a fresh transaction and retained
unless a rebuilt graph, paired execution, safety check, and DICC closure all
remain closed.

## Module Map

| Concern | Production symbols | Concrete algorithm | Persistent evidence |
|---|---|---|---|
| Evidence | `evidence.extract`, `build_semantic_graph`, `freeze_assignment` | typed extraction, authority adjudication, coherent assignment enumeration | evidence, semantic hypothesis graph, episode assignment |
| Requirements | `compile_assignment_overlay`, `compile_requirement_paths`, `RequirementGraph` | quantified leaves, preservation expansion, symbolic partitions, path/edge ledger | requirement graph |
| Program | `PythonProgramGraphBuilder`, `CFGBuilder`, `DefUseAnalyzer`, `ProtocolAnalyzer` | AST/scope/import/points-to/CFG/def-use/dispatch/protocol passes | program graph |
| Binding | `build_binding_graph`, compatibility and entrypoint recovery | constrained product of leaf, entrypoint, path class, partition, observation and oracle | binding graph |
| Oracle | `resolve_oracle`, `classify_pair`, `HypothesisDiscriminator` | authority-ranked executable relations; unknown/provisional states are preserved | oracle/scenario and discriminator records |
| Challenges | `materialize_challenges`, `CandidateGenerator`, `diff_induced_challenge_plan` | finite constrained witness generation plus graph and actual-diff overlays | challenge graph, recipes, DICC certificate |
| Execution | `TraceExecutor`, `WorktreeManager`, `reconcile_actual_diff` | isolated worker, paired baseline/patched replay, stability replay, canonical diff | trace bundles, receipts, working patch |
| Repair | `select_losing_core`, `next_untried_repair_intent`, `apply_registered_operator` | causal-cut selection, mechanism rotation, AST-checked structured edits | repair action, mechanism memory, transition certificate |
| Reach-Avoid | `in_target_set`, `raw_avoid_reasons`, `evaluate_single_update` | strict progress, preservation and frontier gates, commit/rollback transaction | ReachAvoidState, transition certificate |
| Recovery | `rebuild`, `recover_run_storage`, `ArtifactStore.recover` | replay graphs/traces from latest checkpoint; rebuild index by timestamp | recovery audit, verified index |
| Reporting | `verify_artifacts`, `build_run_report`, `export_patch` | hash/lineage replay and pure diff export | terminal certificate and reports |

## Graph Linkage

Requirement leaves carry quantified variables, finite/open-world domains,
preconditions, trigger hypotheses, trace/exception/state/preservation
contracts, authority, evidence and coverage. `compile_requirement_paths()`
joins these leaves with feasible Program Graph paths and records every program
edge in an edge ledger. `build_binding_graph()` projects domain constraints
onto path guards and observation channels, locks each executable unit to one
oracle, and indexes units both by path and program node. Challenge materializer
consumes those units and creates deduplicated executable cells.

After a real diff, DICC compares baseline and trial path keys, adds removed/new
path obligations and operator-specific adjacent partitions (guards, calls,
fallbacks, dispatch, bypass, preservation and external effects), executes each
admitted cell, and finalizes a recomputable closure certificate. Accepted
transitions rebuild Program, Requirement, Binding and Challenge graphs from the
committed tree; no stale graph object is used as the incumbent.

## Observation and Repair Contracts

`InputRecipe` is validated without executing source and supports imports,
containers, construction, field writes, calls, pure operators, state
snapshots, sequences, observations, multi-traces and resource limits. The
worker normalizes return values, object shapes, exceptions, state snapshots,
stdout/stderr, calls and protocol events. `TraceExecutor` repeats each role
and classifies disagreements as `FLAKY`/`UNKNOWN` rather than PASS.

`CounterexamplePacket` is generated from a real Challenge Cell and paired
bundle. It includes the minimized recipe, baseline/patched observations,
first divergence, path/protocol slices, repair cuts, preservation siblings,
source/diff hashes and uncertainty. A repair action is accepted only when its
intent, checkpoint, component, causal cut, operator, read/write set and AST
spans agree; the registered operator then changes the trial tree and produces
the canonical actual diff.

The built-in `PersistentGeneratorSession` can synthesize concrete
return/identity/exception edits from a trusted relation. Guard, protocol,
state-order, wrapper, and representation rewrites remain available through the
validated `action_provider` hook; the default CLI does not claim a universal
source-to-source generator for those mechanisms.

## Persistence and Certificates

`ArtifactStore` writes content-addressed envelopes atomically, validates schema
and parent links, maintains an index and journal, and can rebuild the index.
Latest selection is ordered by `(created_at, artifact_id)`, so recovery is
independent of filesystem object order. `WorktreeManager` uses one active lease,
immutable checkpoints, atomic pending directories, and exact rollback hashes.
`TransitionCertificate` is recomputed from graph hashes, outcomes, closure,
mechanical checks and receipts. `TerminalCertificate` records final checkpoint,
patch hash, graph hashes, unresolved paths/frontiers, budget and artifact
verification hash.

## Adapter and CLI Surface

Adapters for Python, Django, SymPy, NumPy and requests only add marker,
mechanical-command and graph hints. Their status is explicitly
`OBSERVED_NOT_CORRECTNESS`; they never assign PASS. The CLI exposes
`analyze`, `build-requirements`, `build-program-graph`, `bind`,
`generate-challenges`, `run`/`repair`, `resume`, `status`/`inspect`,
`verify`/`verify-artifacts`, `recover`, `report`, `export`/`export-patch`, and
`artifacts`. Stage commands use the same complete production analyzer and
return the selected graph artifact rather than a separate implementation.

## Audit Update

The post-audit pass added explicit open-world coverage tracking: finite
witness enumeration is never treated as universal proof, and mandatory units
retain a `UNIVERSAL_DOMAIN_COVERAGE` hard frontier until an exhaustive
partition or closed-domain proof exists. Stable worker traces now include
branch outcomes, calls, effects and output channels and are merged into the
trial Program Graph before rebinding. Actual diff symbols are used to compute
the guarded Impact Cone, and stable failing packets are minimized by replaying
candidate recipes. Method/property scenarios, protocol fallback/infeasible
relations, explicit schemas for every emitted artifact type, recovery schema
validation, stored DICC certificate replay, and worktree lease-boundary checks
are also enforced.

The generic adapters remain additive semantic observations and explicit
external frontiers; they do not create correctness oracles. Full Django,
SymPy, NumPy and requests semantics still require framework-specific runtime
dependencies and are therefore represented as adapter facts plus
`UNKNOWN`/`BLOCKED_EXTERNAL` frontiers where the core Python executor cannot
prove behavior.

## Evidence

- Unit, integration and conformance suite: **42 passed** with
  `.venv/bin/pytest -q --basetemp=.pytest-tmp`.
- The permitted dataset copy at
  `dataset/patchpsro_55_unique51` was inventoried as 55 files, 1,404,216
  bytes, with recursive per-file-SHA256 digest
  `e7425927ef3348c64625b167014ed1f2aeeecce303c9326ab8fd00c634c955e4`.
- `runs/audit-run-final`: terminal `BUDGET_EXHAUSTED`, one committed repair
  transition, two immutable checkpoints, 99 verified artifacts, and a hard
  `UNIVERSAL_DOMAIN_COVERAGE` frontier. The controller correctly refused
  `GRAPH_REACHED` for the open-world `For every x` obligation.
- CLI `verify-artifacts` returned `valid: true`; the stored DICC closure and
  terminal certificate were independently replayed from their persisted
  payloads.
- Exported patch is byte-identical to `final_patch.diff`; its canonical diff
  changes only `pkg/api.py` and has hash
  `b64a444000cd963af7a1d6abaacb05ad383f847d4e5c87ab4010b5437f09d2bf`.

## Intentional Boundaries

Dynamic reflection, external databases/network, unsupported protocol handlers,
ambiguous semantics and unstable execution become explicit frontier or
UNKNOWN/BLOCKED states. Finite candidate generation is deliberately bounded by
the declared domain and limit; witness-only open-world partitions create a
hard universal-coverage frontier and cannot enter Reach. The adapters and
discriminator are evidence-only by contract. These are conservative terminal
conditions, not silent success.

## Patch-first Production Addendum (2026-07-27)

The production SWE route is now `SemanticGraph/HypothesisSet -> RepositoryIndex
-> RequirementCore -> ActiveProgramSlice -> initial DeepSeek revision ->
paired public validation -> incremental active graphs -> persistent repair
conversation -> Reach-Avoid commit/rollback`. The legacy full-graph narrative
above is historical matrix context; normal `run`, `resume`, and SWE runner
calls use the patch-first route. Legacy full-graph constructors are explicitly
gated by `REACHPATCH_ENABLE_LEGACY_FULL_GRAPH=1`.

The current behavior suite is **105 passed**. The sealed requests smoke
produced a real 918-byte patch and one COMMIT, then stopped at
`BUDGET_EXHAUSTED` with four deferred bindings and no executable challenges.
Its independent harness result was `UNKNOWN_EXECUTION` because both command
groups were `BLOCKED_EXTERNAL` (pytest unavailable); this evidence was not
returned to the Generator.
