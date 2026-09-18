# Search-graph policy revision — 2026-09-15

Overall acceptance: **NOT COMPLETED**. No Resolved@1 gain is claimed.
This report supersedes the earlier 189-test implementation snapshot.

## Implemented and checked

- One `DynamicReachAvoidGraph`; global action views instead of a second search
  tree. Untried hypotheses, exhausted actions and bounded expansion are graph
  state. Parent snapshots remain exact immutable base-to-checkpoint diffs.
- Exact-input contract obligations prevent one probe from cancelling another
  probe's oracle gap. Child-specific executable challenges precede certification.
- Failure loci no longer contain ephemeral execution roots. Local exhaustion,
  repeated empty patches and weak oracles terminate their actions without looping.
- Typed traces retain AST anchors, source versions and caller files. Unknown
  successors are not falsely marked not-taken. Predictions are input/cut scoped;
  a nearby unrelated branch change cannot support the hypothesis.
- Candidate disagreement can compare checkpoints with different parents.
- Shared case call/token/execution budgets, final-validation reserve, conservative
  opt-in validation caching and source/environment/contract invalidation.
- Removed the unused controller checkpoint selector and the experiment runner's
  old four-graph decoding/report path. Old canonical-only P0 records are rejected.
  Target runs are no longer miscounted as challenge runs. New experiment manifests
  include the actual case budget configuration.
- Historical experiment files and unrelated worktree changes were retained.

## Files

New production modules:

- `reachpatch/reach_avoid/graph_policy.py`
- `reachpatch/reach_avoid/mechanism_predictions.py`
- `reachpatch/execution/case_budget.py`
- `reachpatch/execution/validation_cache.py`

Updated production: `controller.py`, `dynamic_reach_avoid_graph.py`,
`execution_checkpoint.py`, `oracle_audit.py`, `trace.py`, `models/evidence.py`,
and `experiments/reachavoid_51/runner.py`.

Updated/new tests: `test_graph_policy_lifecycle.py`,
`test_execution_serialization.py`, `test_reachavoid_51_runner.py`.

## Verification artifacts

Run from `Code` with `PATH=/home/slt/miniconda3/bin:$PATH PYTHONPATH=.`:

```text
python -m pytest -q --tb=short --junitxml=experiments/graph_policy_tests.xml
212 passed, 165 deselected in 99.08s

python -m pytest tests/reach_avoid/test_graph_policy_lifecycle.py tests/reach_avoid/test_patch_checkpoint_search.py tests/integration/test_integrated_reach_avoid.py tests/execution/test_target_recovery_semantics.py tests/requirement_graph/test_semantic_compiler.py tests/unit/test_reachavoid_51_runner.py -q --tb=short --junitxml=experiments/graph_policy_targeted_tests.xml
61 passed in 28.92s

git diff --check
exit 0
```

Both runs include the recovery-discovery and scoped-tracing corrections.
The 165 exclusions are existing legacy markers, not new exclusions.
Toy runs cover rejected-child parent backtracking, partial target progress with
preservation repair, weak-oracle recovery, and bounded duplicate-patch exhaustion.
They use scripted generators and public probes, not hidden SWE tests.

`experiments/graph_policy_audit.json` contains all 39 AST matches in the thirteen
selected production modules: conditional empty/None returns for missing lookups,
empty frontiers, unsupported adapters and non-cacheable execution. There are no
pass/NotImplementedError/ellipsis-body/prohibited-token matches. This audit does
not prove every original structural requirement.
The extended audit includes compiler/recovery/evidence optional lookup and
unsupported-witness paths; these conditional None/empty results remain visible
for review rather than being suppressed by the audit tool.

## Diagnostic and remaining acceptance

Fresh generation directory:
`experiments/reachavoid_graph_policy_diag10_20260915`.

Public-source preflight passed for ten cases. Bubblewrap and Python startup passed;
the key file is present without displaying its contents. A new Diagnostic 10
generation was started with deepseek-chat, 8 revisions, 160 model calls,
1,000,000 tokens and 3,600 wall seconds per case; final validation reserve 30s.
Case retries are disabled (one attempt) to avoid silently increasing budgets.
That generation was interrupted before sealing: the first case had a correctly
compiled target but selected unrelated FITS empty-list checks during recovery.
The subsequent correction ranks tests by hard-goal symbols before generic hints,
requires actual AST loaded references (not comments/strings/test names), and
executes authoritative issue witnesses before broad public-check discovery.
The interrupted artifacts are retained and must not count as Diagnostic 10.
All generated patches must be sealed before official P0/final evaluation.
No official harness output has been read during this revision.

Still unaccepted:

- Current Diagnostic 10 final results, per-case graph provenance and P0/final
  official comparison; generation starting is not a passed diagnostic.
- Complete equal-budget A/B/C/D graph-off/generation/selection ablations. The
  current production controller has no complete graph-off experimental mode;
  the shared budget infrastructure alone does not constitute that comparison.
- The gated 51-case evaluation and all requested empirical metrics.
- Broader exact-input oracle/probe adapters, observable same-line conditional
  outcomes, cost-calibrated action ordering, and conservative source correspondence
  when AST statement structure changes. These boundaries are not patched around
  with invented observations or permissions.

Do not label this revision completed or infer Resolved@1 improvement from tests.

## Recovery correction and rerun

The hard-goal-first discovery correction was exercised on the real public
astropy source: all six highest-ranked checks reference `wcs_pix2world` in AST
loads; unrelated FITS tests no longer win from generic empty-list prose.
Recovery now receives the unified graph instance and its source context, and
bounded expansion is used for graph-backed source search. Compiler tool failures
are retained in `goal_compilation_tool.json` even when fallback writes a separate
requirement compilation result.

The related recovery/compiler/graph-policy/integration set passed 52 tests in
24.04s. A fresh generation rerun was started under
`experiments/reachavoid_graph_policy_diag10_r2_20260915`, with the same budgets,
one attempt per case, and all-ten seal required before official evaluation.
The interrupted first directory is not a completed diagnostic.

The R2 generation was also interrupted before sealing when a public trace-on/off
diagnostic demonstrated instrumentation-induced timeout. The raw measured summary
is `experiments/graph_policy_trace_diagnostic.json`: untraced FAIL at 5.28s,
unscoped traced timeout at 15.42s, then target/two-callee-scoped traced FAIL at
6.73s with 33 events. ASTs are cached per file. Neither interrupted run is a
completed or successful Diagnostic 10.

The paired public diagnostic after scoped tracing completed with two FAILs,
`stable=True`, `entered_target_code=True` in 11.48s. The manually instantiated
probe is only a diagnostic, not a substituted production recovery oracle.
The active fresh generation directory is now
`experiments/reachavoid_graph_policy_diag10_r3_20260915`; neither earlier attempt
is eligible for benchmark accounting. All source mutations were finished before
starting this directory's implementation-hash seal lifecycle.

R3 was interrupted after public validation exposed two false target promotions:
rotation tests failed on a dependency `DeprecationWarning` treated as an error,
after target code had already returned. The ordinary issue witness was stable
PASS on P0. Dependency warning-as-error observations now remain BLOCKED even
when a target frame was previously entered. A real rerun of the public rotation
test returned `BLOCKED`, stable, no failure stage, in 30.29s. Tests and warning
filters were not modified. The fresh active generation directory is now
`experiments/reachavoid_graph_policy_diag10_r4_20260915`.

Evaluation-only `experiments/graph_policy_acceptance.py` gates official reads on
the ten-case generation seal, verifies patch hashes, and emits per-case graph
provenance/transition/final selection records. Its two seal-gate tests passed.
The current `experiments/graph_policy_acceptance_status.json` reports
`NOT_COMPLETED: ALL_TEN_PATCHES_NOT_SEALED`; it has not read official results.
