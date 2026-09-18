# Contract-gap and search-graph revision

Status: **NOT COMPLETED** (full research/experiment acceptance).

This revision changes the existing dirty worktree in place. It does not reset
earlier implementation work or overwrite historical Diagnostic10 runs. No
official hidden evaluation output was read and no paid experiment was started
during this revision.

## Production changes

- `requirement_graph/facets.py`: cited normative clauses can produce separate
  no-exception, return-container and return-length goals. Each retains its
  parent requirement and evidence IDs. Ambiguous plural return layouts remain
  explicit required unresolved facets, not guessed expected values.
- `execution/facet_probes.py`: a bounded AST adapter measures type/container
  membership and length of the original issue-witness call, without serializing
  full returned objects. It recognizes the issue extractor's literal interactive
  wrapper. It refuses indirect/multiple calls and unsupported execution adapters.
- `models/execution.py`, `models/evidence.py`, `execution/checks.py`, and
  `requirement_graph/compiler.py`: facet provenance and INSTANCE_OF semantics;
  a list subtype is not rejected merely for having a different class name.
- `execution/target_recovery.py`: proposed stronger checks bind to explicitly
  grounded facets in a named requirement family, not any goal with the same
  expected value. Missing facets trigger recovery even when a target exists.
  Agent probes receive strict executable target-entry verification. Recovery
  history distinguishes discovery from complete required-facet coverage.
- `reach_avoid/graph_policy.py`: closure runs before certification is possible;
  otherwise missing facets would never reach the action scheduler. Each gap
  becomes a requirement-bound recovery action. Action cost uses observed times,
  with unknown costs explicitly represented. Candidate comparison prioritizes
  execution disagreements and causal-cut diversity before node IDs.
- `reach_avoid/dynamic_reach_avoid_graph.py`: all facets, observations and
  checkpoints remain in the same graph. REACHES_CHECKPOINT connects a hypothesis
  to an existing exact patch state; CHILD_OF provenance is not rewritten.
- `reach_avoid/controller.py`: duplicate patches record graph transpositions;
  stale selected hypotheses are exhausted rather than silently substituted.
  Bounded exploratory challenge execution is available before a second patch
  exists, comparing clean and current state. It never certifies a target.
- `execution/discriminating_probes.py`: exploratory execution and candidate
  disagreement are recorded on challenge nodes. Re-materialization retains
  observations instead of resetting their lifecycle.
- `reach_avoid/mechanism_predictions.py`: observable branch predictions include
  a direction, exact input binding and source-version checks. Path-only change
  does not support contract correctness. Unspecified return-path predictions
  remain NOT_OBSERVABLE instead of claiming generic DIFFERENT as support.
- `execution/oracle_audit.py`: process-only facets do not duplicate the return
  audit; separately required return facets govern closure and remain mandatory.
- `reach_avoid/execution_checkpoint.py`: refreshed execution records and their
  reference hashes are persisted together. Sealing checks snapshot content,
  graph observations and exported result consistency.
- `experiments/reachavoid_51/runner.py`: verifies the evidence manifest against
  graph hash and checkpoint execution references before reporting metrics.
  Graph participation is explicitly distinguished from demonstrated benefit.

## Verification

Commands run from `Code`, with `/home/slt/miniconda3/bin` first in PATH and
`PYTHONPATH=.`:

```sh
python -m pytest -q --junitxml=experiments/contract_gap_tests.xml
python -m pytest -q tests/reach_avoid/test_contract_gap_revision.py tests/reach_avoid/test_execution_toy_e2e.py tests/reach_avoid/test_patch_checkpoint_search.py tests/reach_avoid/test_graph_policy_lifecycle.py tests/unit/test_reachavoid_51_runner.py --junitxml=experiments/contract_gap_targeted_tests.xml
git diff --check
```

The targeted run passed 50 tests. The full run passed 227 tests in 107.68 seconds,
recorded in `experiments/contract_gap_tests.xml`; 165 historical legacy-graph tests are
deselected by the repository's existing test configuration, not counted as passes.

`tests/reach_avoid/test_contract_gap_revision.py` includes a real-process,
issue-only E2E: clean raises on empty input, P0 returns None, recovered facets
expose the incorrect return, repair returns an empty list, final differs from
P0 and is REACHED. The generator is scripted; the executable target oracle is
not supplied by the fixture. Deliberately deleting persisted target executions
must fail evidence sealing/aggregation rather than report zero validation.

Static AST audit of the 15 touched production modules: 54 findings, zero
blocking findings. All matches, including conditional empty/None returns, are
retained in `experiments/contract_gap_audit.json`. This limited audit is not a
claim that every historical production module has been removed or audited.

## Remaining acceptance gaps

1. General automatic resolution of ambiguous return layouts from public
   documentation/call-site assertions is not implemented. The new adapter
   supports explicitly grounded container/length facets, not arbitrary shape,
   dtype, state or ordering contracts. The Astropy plural-layout case therefore
   is not claimed fixed or resolved by this change.
2. Graph action scheduling is still deterministic heuristic scheduling. Measured
   cost, gap binding and disagreement ordering do not establish optimal budget
   allocation or higher correct-candidate probability.
3. Exact patch transpositions preserve evidence and provenance, but do not enable
   unrestricted execution-cache reuse across different environments. Existing
   determinism/environment attestation remains required.
4. Return/value-flow mechanism predictions without a specific executable path
   predicate remain unobservable. General AST movement correspondence and full
   dynamic def-use are not established by these changes.
5. There is no completed fresh sealed Diagnostic10, A/B/C/D same-budget
   comparison or 51-case result for this implementation. No Resolved@1 gain,
   target-recovery-rate gain or selection-accuracy gain is claimed.

Next gate: resolve the remaining evidence-binding/adapter gaps using public
evidence, then rerun structural/toy acceptance before a fresh sealed Diagnostic10.
Do not infer completion from passing unit tests.
