# Implementation and verification — 2026-09-15

Overall research acceptance: **NOT COMPLETED**. This report covers the new
evidence-guided implementation, not a completed Diagnostic 10 / 51-case study.
No hidden harness output was consulted and no paid model experiment was run in
this implementation turn. No Resolved@1 uplift is claimed.

## Implemented production behavior

- Unified graph-derived validation now precedes and controls execution. Each
  executed result records command, environment, phase, patch hash and all run
  observations. P0 validation is included in the final observation artifact.
- Localization uses requirement-bound scopes and current-observation evidence;
  same-file unrelated functions and stale source nodes are excluded. Qualified
  class method identities do not collide. Parent backtracking refreshes source
  and static dependencies within the same graph.
- Hypotheses include distinct mechanisms, input partitions, path predictions
  and falsifiers. Paired feedback differentiates regressions/bypasses, measured
  improvement, unaltered behavior and inconclusive evidence. Observation-only
  progress is not falsely attributed to an unobserved path change.
- AST boundaries use the predicate's actual literals. Adjacent executable
  contracts require their own authority and evidence; they do not inherit the
  original input's Oracle. Bounded sibling probes execute across clean and
  sibling snapshots; disagreements influence localization and model context,
  never certification by majority vote.
- Process-local replacement-return interventions expose insufficient return
  oracles. A surviving incompatible return blocks certification and directs
  recovery toward missing evidence. Source and tests are not mutated.
- Expansion potential and final patch quality are separate. Descendant evidence
  updates ancestors' expansion metadata without transferring PASS. Locked-target
  preservation repair wins over revisiting an ancestor. Empty/duplicate attempts
  exhaust hypotheses rather than repeatedly reopening the same search loop.
- Final P0/revision comparisons use executed evidence; full cumulative diffs
  remain immutable checkpoint content. P0 observations refresh its score too.

## Files changed in this implementation turn

Production:

- `reachpatch/reach_avoid/dynamic_reach_avoid_graph.py`
- `reachpatch/reach_avoid/controller.py`
- `reachpatch/reach_avoid/execution_checkpoint.py`
- `reachpatch/execution/oracle_audit.py` (new)
- `reachpatch/execution/discriminating_probes.py` (new)
- `reachpatch/execution/checks.py`
- `reachpatch/execution/target_recovery.py`
- `reachpatch/execution/trace.py`
- `reachpatch/models/execution.py`
- `reachpatch/repair/execution_objective.py`
- `reachpatch/repair/deepseek_agent.py`

Tests/docs/tooling:

- `tests/reach_avoid/test_evidence_guided_policy.py` (17 new tests)
- `tests/reach_avoid/test_dynamic_reach_avoid_graph.py`
- `tests/reach_avoid/test_graph_guided_challenges.py`
- `experiments/audit_reachpatch_production.py` (file selection/output options)
- `docs/evidence_guided_search_design.md`

Existing dirty-worktree changes outside this list were preserved.

## Test commands and results

Run from `Code`, with `PATH=/home/slt/miniconda3/bin:$PATH PYTHONPATH=.`:

```text
/home/slt/miniconda3/bin/python -m pytest -q --junitxml=experiments/evidence_guided_tests.xml -o faulthandler_timeout=45
189 passed, 165 deselected in 91.35s

/home/slt/miniconda3/bin/python -m pytest tests/reach_avoid/test_evidence_guided_policy.py tests/reach_avoid/test_dynamic_reach_avoid_graph.py tests/reach_avoid/test_graph_guided_challenges.py tests/integration/test_integrated_reach_avoid.py tests/reach_avoid/test_patch_checkpoint_search.py -q --junitxml=experiments/evidence_guided_targeted_tests.xml
32 passed in 9.09s

git diff --check
exit 0
```

The 165 exclusions are the existing `legacy_graph` pytest marker, not newly
excluded failures. The prior graph localization fixture was given its missing
requirement binding; the prior challenge fixture now asserts that branch-level
authority alone does not certify an adjacent input.

The toy tests include parent backtracking, target-progress preservation repair,
and rejection of an apparent P0 success using a weak exit-code-only Oracle.
They use scripted generators/public checks and do not establish SWE performance.

## Static audit

`experiments/evidence_guided_audit.json` preserves all 27 matches in the selected
production files. There are no `pass`, `NotImplementedError`, ellipsis-body or
prohibited implementation-token hits. The matches are empty/None returns:

- Graph: unsupported AST-name lookup; no requirement-bound scope; no selectable
  checkpoint; missing/malformed backtrack parent.
- Controller: no source terms or no usable graph-selected snapshot.
- Oracle audit: no supported incompatible return; unsupported command adapter.
- Sibling probes: unsupported/unparseable/ambiguous calls or parameters, bounded
  length overflow, and no pair of siblings. Each adapter refusal has a local
  reason comment; challenge artifacts distinguish missing adapters from missing
  exact-input oracles.
- Existing check/objective/agent functions: absent observation, absent mechanical
  result, and unsupported tool-call decoding.

The auditor's `blocking_count=0` does **not** independently verify every original
structural requirement; its empty-return findings remain visible for review.

## Remaining acceptance work

- Current-code sealed Diagnostic 10 and its per-case checkpoint/backtrack report.
- Matched A/B/C/D budgets and generation-versus-selection ablations.
- The gated 51-case official Resolved@1 comparison and preservation-regression
  accounting.
- Broader probe adapters and return-contract coverage; exact same-line branch
  tracing remains a limitation described in the design document.

Candidate coverage, conditional selection accuracy, and cost effectiveness of
oracle audits/sibling probes remain empirical questions, not results of these
unit tests.
