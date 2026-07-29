# Reach-Avoid Transition Audit

## Decision inputs

`evaluate_patch_revision()` applies edits to a transaction tree, runs mechanical
checks, executes every recovered target and preservation check with the selected
project runner, builds `CheckComparison` values, generates only behavioral
counterexamples, updates the diff impact slice, and calls `evaluate_dicc()`.

Safety is no longer defaulted. It requires mechanical success, no forbidden or
official-only edit, no patch-caused infrastructure failure, no target
regression, and no preservation regression. Target deficit is the weight/count
of executable targets whose latest classification is not `TARGET_FIXED`.

## Progress

`ProgressVector` contains only execution-backed values:

- target fixed gain;
- target deficit reduction;
- eliminated stable counterexamples;
- retained preservation passes;
- reduced high-risk unknowns;
- new regressions.

An empty comparison set cannot progress. Diff existence, graph hashes,
mechanical success, frontier counts, and vacuous `all()` results do not commit.

## Decisions

- `COMMIT`: real target/counterexample/unknown improvement, safe, no regression.
- `ROLLBACK`: mechanical failure, target regression, preservation regression,
  forbidden/contaminated edit, or patch-caused environment failure.
- `KEEP_UNCERTIFIED`: potentially useful trial whose required validation is
  blocked by a reliable external/environment condition.

`KEEP_UNCERTIFIED` archives the trial under the run worktree's `uncertified`
area, closes the transaction lease, records `working_trial`, and leaves the
verified incumbent checkpoint untouched.

## Reach and DICC

Reach requires a nonempty patch, at least one executable target, a real paired
execution for every target, all targets fixed, no preservation regression, no
live stable counterexample signature, DICC `CLOSED`, valid required
environments, and a safe checkpoint.

DICC returns `NOT_EVALUABLE` for zero target, zero target execution, or zero real
challenge execution; `BLOCKED_EXTERNAL` for required environment blockage;
`OPEN` for stable target failure, preservation regression, or an uncovered true
touched branch partition; otherwise `CLOSED`.

## Execution evidence

Persistent repair integration:

```text
COMMIT: TARGET_STILL_FAILING, counterexamples_eliminated=1, DICC OPEN
ROLLBACK: mechanical failure, incumbent retained
COMMIT: TARGET_FIXED, target_deficit_reduction=1.0, DICC CLOSED
terminal: REACHED
```

Regression integration:

```text
TARGET_FIXED x2 + PRESERVATION_REGRESSION x2
-> safe=false
-> ROLLBACK
-> result_checkpoint_id=None
```

The terminal certificate reports target, preservation, and closure completion
from executable comparisons/DICC rather than legacy graph outcomes.

## Post-seal ablation invariant

Edit-retention ablation is also execution-backed. Each candidate deletion runs
mechanical checks and the recovered target/preservation checks again, rebuilds
the L2 impact slice and executable bindings, and evaluates a fresh DICC. The
legacy diff-closure certificate is retained as analysis evidence only; it no
longer authorizes a candidate deletion or replaces the incumbent comparison
set. `TRANSITION_GATE -> INCUMBENT_CLOSE` is an explicit legal transition for
this post-Reach validation phase.

The regression test
`test_edit_ablation_reexecutes_targets_before_retaining_removal` observed a
candidate `TARGET_STILL_FAILING` comparison and `DICC=OPEN`, so the deletion
was retained and the verified patch remained unchanged.

`ReachAvoidState.target_deficit()` and transition certificates now use the
execution comparison set in patch-first mode. The target-fix integration test
records the actual deficit change from the recovered baseline target count to
zero.

## Tests

- `tests/unit/test_execution_semantics.py`
- `test_single_working_patch_is_repaired_in_one_persistent_conversation`
- `test_public_preservation_regression_rolls_back_only_trial`
- `test_keep_uncertified_archives_trial_without_replacing_incumbent`
- `test_public_target_fix_contributes_transition_progress`

Final suite result: `140 passed in 288.45s`.
