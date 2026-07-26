# ReachPatch Completeness Audit

## Scope and Evidence

The audit read `/home/slt/ReachPatch/Paper/reachpatch_graph_grounded.tex` and
searched, compiled, tested, and executed only the implementation under
`/home/slt/ReachPatch/Code`. The forbidden legacy directories were not read or
scanned, and no gold patch or hidden harness was used.

Checks run:

| Check | Result |
|---|---|
| Python compilation | PASS |
| Unit/integration/conformance suite | 42 passed |
| Fresh closed-loop run | PASS as a run, terminal `BUDGET_EXHAUSTED` |
| Artifact/index/schema verification | PASS |
| DICC closure replay from stored plan and obligations | PASS |
| Terminal certificate digest and graph-hash replay | PASS |
| Working-tree rollback/commit tests | PASS |

Dataset copy evidence (the only legacy-path material permitted by the task):
`dataset/patchpsro_55_unique51` currently contains 55 files, occupies 1,404,216
bytes, and has recursive digest
`e7425927ef3348c64625b167014ed1f2aeeecce303c9326ab8fd00c634c955e4` (computed
from sorted per-file SHA-256 records under `Code`). This dataset was not used
as a source of gold patches or hidden-harness outcomes.

Reference runtime evidence is `runs/audit-run-final`. It contains one working
patch lineage, one accepted transition, 99 artifacts, a hard
`UNIVERSAL_DOMAIN_COVERAGE` frontier, and no false `GRAPH_REACHED` claim.

## Correctness-Critical Findings Fixed

1. Finite witness enumeration previously looked like open-world satisfiability
   or universal coverage. `ConstraintResult.complete`, partition proof fields,
   and the `UNIVERSAL_DOMAIN_COVERAGE` hard frontier now distinguish a witness,
   a closed-domain proof, and an unresolved open-world obligation.
2. Dynamic trace events were previously persisted without graph consumption.
   Stable calls, branch outcomes, protocol selections, object shapes, and
   effects are now merged into the trial Program Graph before accepted graph
   rebuilding.
3. Diff-specific Impact Cone computation is now called from the transition
   evaluator and recorded in the transition certificate graph delta.
4. Stable failing challenges now run deterministic recipe shrinking before a
   Counterexample Packet is emitted.
5. Progress includes target deficit, repaired paths, failure delta, path
   coverage, frontier count, and worst-unit deficit; non-progressing or
   regressing transitions cannot commit.
6. Artifact schemas are explicit for every artifact type emitted by the
   controller; unknown types and schema-invalid recovered objects are rejected.
7. Terminal certificates use a stable artifact digest excluding the certificate
   itself. `verify_run()` checks that digest, final graph hashes, final patch,
   and reach consistency. Stored DICC plans and updated obligations are
   replayed independently.
8. Method/property/class entrypoints are materialized as import/construct/call
   or property-observation recipes. Worktree recovery rejects leases outside
   the transaction root.
9. Protocol IR now records fallback relations and explicit infeasible protocol
   targets. Exception oracles check type, message category, and phase.

## Remaining Gaps (Do Not Claim Full Paper Completion)

| Severity | Gap | Impact |
|---|---|---|
| P1 | Open-world universal coverage has no general theorem prover. | Generic quantified instances remain at a named hard frontier until an exhaustive symbolic partition or closed-domain proof is supplied. This is conservative and prevents false reach, but is not universal semantic proof. |
| P1 | The Python frontend is conservative rather than a complete Python semantics engine. | Complex reflection, metaclasses, C extensions, dynamic imports, deep recursion, concurrency, databases, and live network behavior become explicit `UNKNOWN`, `UNSUPPORTED`, `FLAKY`, or `BLOCKED_EXTERNAL` states. |
| P1 | DICC certificate replay validates persisted plan/obligation/hash data, not a fresh repository execution. | Full semantic re-execution of every historical transition is not implemented. |
| P1 | The built-in repair generator is deliberately narrow. | The default session can synthesize concrete return/identity/exception edits; guard, protocol, state-order, wrapper, and representation rewrites require an injected `action_provider`. Their registered operators, AST guards, causal cuts, diff checks, and rollback semantics are implemented, but there is no general source-to-source generator for every mechanism in the default CLI. |
| P2 | Django, SymPy, NumPy, and requests adapters add detected facts, graph hints, and external frontiers; they do not implement complete framework-specific ORM, symbolic-equivalence, broadcasting, or HTTP transport oracles. | Those semantics cannot be certified by adapter markers alone. |
| P2 | Experimental methodology sections of the paper are not a large-scale SWE-bench driver. | Current stage intentionally implements the method and generic fixture loop; dataset-scale statistical tables and all baselines are not populated. |
| P2 | Branch tracing is bytecode-observational and does not prove symbolic predicate values for every Python construct. | It augments static CFG evidence; unresolved constructs remain frontiers. |

## Forbidden-Pattern Audit

Production search found no `pass` statement, `NotImplementedError`, TODO/FIXME,
mock/replay generator, selector, random patch choice, gold-patch read, or
hard-coded SWE instance. Empty tuples in production are guarded cases for
missing evidence, unsupported domains, or no applicable relation and are
followed by an explicit frontier or UNKNOWN status. Fixture references occur
only in tests.

## Closure Decision

The implementation is a runnable, integrated, conservative ReachPatch
prototype with a persistent working patch and verified certificates. It is
**not** a proof-complete implementation of every open-world Python/framework
semantic in the paper. The controller now demonstrates the required safety
behavior by refusing Reach when universal coverage is unproved.
