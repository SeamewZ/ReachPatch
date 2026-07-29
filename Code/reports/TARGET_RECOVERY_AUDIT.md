# Target Recovery Audit

## Production entry

`ReachPatchController.analyze()` selects a project runner and calls
`recover_executable_targets()` before the first Generator request. The recovery
result is persisted and projected into the executable requirement overlay,
target slice, causal slices, and executable bindings.

## Candidate authority order

The implementation considers public visible tests, repository tests related by
issue symbols, explicit public commands, executable issue code blocks, and a
bounded public-API behavior reproduction. Official-only paths and fields are
filtered before candidate construction.

Every candidate is run on the baseline through `health_check()`:

- stable `FAIL` becomes `TARGET`;
- stable `PASS` becomes `PRESERVATION`;
- invalid selectors and environment failures become rejected checks and
  `EnvironmentFrontier` records;
- flaky, timeout, or unsupported results do not become patch targets.

Temporary reproductions are written under the run execution artifacts, outside
the repaired repository, and are absent from the final diff.

## Non-vacuity behavior

If no stable failing target is recovered, the controller sets
`TARGET_RECOVERY_BLOCKED`, performs no patch transition, and does not call the
Generator. Bounded public behavior synthesis counts as at most one directed
reproduction request.

## Baseline caching

Baseline executions are cached by
`base_commit + environment_hash + check_id`. Trial execution rebases cwd and
`PYTHONPATH` onto the trial tree; the baseline repository is removed from the
front of `PYTHONPATH` so patched code is actually imported.

## Verification

- `test_target_recovery_blocked_does_not_call_generator`: zero targets, zero
  model calls, zero transitions, exact blocked status.
- `test_baseline_health_cache_reuses_base_commit_environment_and_check`: a
  changed local test is not rerun under the same baseline cache key.
- `test_health_check_distinguishes_missing_dependency_and_invalid_selector`:
  correct frontier classifications.
- `test_public_target_fix_contributes_transition_progress`: a stable baseline
  failure is recovered, executed against the trial tree, classified
  `TARGET_FIXED`, and restored correctly after `rebuild()`.

## Observed result

The production target-fix fixture recovered a stable failing public behavior
reproduction, produced a resolved causal binding, and later classified the
trial as `TARGET_FIXED`. The no-target fixture terminated before any DeepSeek
transport request. Environment failures produced zero counterexamples.
