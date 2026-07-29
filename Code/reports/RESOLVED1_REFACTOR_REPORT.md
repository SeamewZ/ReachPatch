# Resolved@1 Production Refactor Report

## Scope and baseline

- Repository: `https://github.com/SeamewZ/ReachPatch`
- Verified local and remote commit before modification:
  `bea89e8a3eb47ad57ca88205dbbc8f94da09d2d4`
- All implementation and verification work was performed under
  `/home/slt/ReachPatch/Code`.
- No implementation was read from either prohibited AAAI2027 directory.

## 1. Original production failures

The previous production path could generate before it had a stable executable
target, treated graph/mechanical changes as progress, defaulted checkpoints to
safe with zero target deficit, and used one pytest-shaped public-check path for
projects with incompatible native runners. Infrastructure failures could enter
repair feedback, DICC could appear closed without real obligations, and the
generator could spend 10 revisions times 12 turns while repeatedly browsing.
The experiment harness also mixed host-side test execution terminology with
official SWE-bench results.

## 2. Production files and functions changed

- `reachpatch/execution/models.py`: executable check, execution, health,
  comparison, and paired classification semantics.
- `reachpatch/execution/runners/`: project-native selector normalization,
  isolated execution, health checks, and baseline caching.
- `reachpatch/execution/target_recovery.py`:
  `recover_executable_targets()`.
- `reachpatch/requirement_graph/compiler.py`:
  `compile_requirement_core()` and
  `compile_executable_requirement_overlay()`.
- `reachpatch/program_graph/execution_slice.py`: L0 target, L1 causal, and L2
  impact slices.
- `reachpatch/program_graph/causal_cut.py`: `causal_repair_cut()` returning real
  source paths and line ranges.
- `reachpatch/binding_graph/executable.py`:
  `build_executable_bindings()`.
- `reachpatch/challenge_graph/dicc.py`: execution-backed `evaluate_dicc()`.
- `reachpatch/reach_avoid/controller.py`: Target Recovery before generation,
  persistent incumbent control, resume restoration, terminal statuses, and
  execution-backed sealing.
- `reachpatch/reach_avoid/transition.py`: real paired execution,
  COMMIT/ROLLBACK/KEEP_UNCERTIFIED, and DICC evaluation.
- `reachpatch/reach_avoid/gates.py` and `metrics.py`: non-vacuous Reach and
  execution-only progress.
- `reachpatch/reach_avoid/machine.py`: legal transition from the execution gate
  into post-Reach incumbent close/ablation validation.
- `reachpatch/repair/context.py`, `deepseek_agent.py`, and `tools.py`: focused
  evidence context, 6x6 revision/turn limits, per-tool limits, cache, blocker,
  and no-new-evidence termination.
- `reachpatch/execution/official_harness.py` and
  `experiments/swe51/runner.py`: post-seal official SWE-bench Docker adapter and
  generation/harness isolation.
- `reachpatch/reach_avoid/restore.py`: full restoration of executable evidence,
  slices, bindings, comparisons, DICC, and generator memory.
- `reachpatch/reporting.py`: execution, DICC, transition, and root-cause fields.

## 3. Old and new call chains

Old effective path:

```text
issue -> broad graph products -> generator -> mechanical/graph deltas
      -> generic public pytest execution -> graph closure -> host harness label
```

New production path:

```text
public issue/repository
  -> select_project_runner
  -> baseline health_check and cached real executions
  -> recover_executable_targets (stable FAIL only)
  -> executable requirement overlay
  -> L0 target + L1 causal slice
  -> executable bindings
  -> persistent DeepSeek revision on incumbent snapshot
  -> mechanical checks
  -> native runner baseline/trial CheckComparison
  -> behavior CounterexamplePacket (environment failures excluded)
  -> L2 diff impact + DICC
  -> COMMIT / ROLLBACK / KEEP_UNCERTIFIED
  -> repeat on accepted incumbent
  -> REACHED seal
  -> separate official SWE-bench Docker harness
```

Every new production function in this report is called by the controller,
transition evaluator, or post-seal experiment runner. None is a detached model
or placeholder.

## 4. Invariants and tests

| Invariant | Test evidence |
| --- | --- |
| 0 target cannot Reach or close DICC | `test_empty_execution_sets_cannot_reach_close_dicc_or_make_progress` |
| 0 execution cannot produce progress | same test plus `should_commit()` assertion |
| Same infrastructure failure creates no counterexample | `test_same_infrastructure_failure_never_becomes_repair_counterexample` |
| Stable target fix plus preserved behavior commits and reaches | `test_real_target_fix_and_preservation_pass_commit_close_and_reach` and controller integration test |
| Partial patch continues in one conversation/incumbent | `test_single_working_patch_is_repaired_in_one_persistent_conversation` |
| Preservation regression rolls back without checkpoint replacement | `test_public_preservation_regression_rolls_back_only_trial` |
| No target means no Generator calls | `test_target_recovery_blocked_does_not_call_generator` |
| Native Django/SymPy/pytest runners execute | `tests/unit/test_project_runners.py` |
| Generator budgets and read cache are enforced | `test_repair_tool_budgets_and_tree_range_read_cache` |
| Final turn is not forced to edit | `test_production_final_turn_does_not_force_apply_edits` |
| Resume retains execution state | assertions in `test_public_target_fix_contributes_transition_progress` |
| Deficit and certificate use real paired checks | `test_state_target_deficit_uses_real_comparisons_not_graph_products` and the target-fix certificate assertions |
| Post-Reach ablation cannot reuse stale graph closure/comparisons | `test_edit_ablation_reexecutes_targets_before_retaining_removal` |
| Generation cannot access official-only fields | `test_generation_instance_rejects_official_fields` |
| Missing official image is HARNESS_NOT_RUN | `test_official_harness_reports_missing_exact_image` |

## 5. Environment handling fixed

Each check now receives independent writable `HOME`, `XDG_CACHE_HOME`,
`MPLCONFIGDIR`, and `TMPDIR`, with deterministic Python environment settings.
Health states distinguish missing dependencies, collection failure, invalid
selectors, unsupported runtime, and external services. Baseline environment
failures are rejected into `EnvironmentFrontier`; they neither change patch
payoff nor form repair counterexamples. Trial-caused infrastructure failures
remain regressions.

The current development environment does not have the official Docker/SWE-bench
runtime ready. Its correct official status is therefore `HARNESS_NOT_RUN`; no
host pytest result is reported as official.

## 6. Generator budget change

- Previous effective upper bound: 10 revisions x 12 tool turns = 120 model
  turns, with browse-loop risk.
- New default upper bound: 6 revisions x 6 tool turns = 36 model turns.
- Per revision: 2 searches, 4 reads, and 3 public-check requests.
- Repeated evidence fingerprint: immediate `NO_NEW_REPAIR_EVIDENCE` with zero
  additional model calls.
- Final turn: `apply_edits`, `finish_revision`, `request_program_slice`, or
  `declare_blocker`; no forced `apply_edits` tool choice.

## 7. Graph scope change

The Program Graph defaults changed from broad 40-file/200-function/
50,000-node/150,000-edge limits to 8 files, 40 functions, 8,000 nodes, and
24,000 edges. Initial generation occurs after Target Recovery and the local
target/causal slice, before global path-product materialization. Incremental
updates rebuild touched/requested files. L2 impact construction starts only
after a real diff and only true branch-bearing paths can create uncovered
branch partitions.

## 8. Remaining real limitations

- Natural-language reproduction synthesis is intentionally bounded; issues
  without an executable public observation terminate as
  `TARGET_RECOVERY_BLOCKED`.
- A traceback-free return-value reproduction relies on a public API symbol to
  seed the causal slice. Issues without a frame or resolvable public symbol can
  remain `CUT_UNRESOLVED`/`LOCALIZATION_BLOCKED`.
- External databases, networks, and unavailable historical runtimes remain
  environment frontiers rather than repair targets.
- Official Resolved@1 is not claimed until the upstream Docker harness is
  installed, images are available, and post-seal evaluation completes.

## 9. Executed commands and results

```text
git status --short
python -m compileall -q reachpatch experiments/swe51/runner.py tests/unit/...
git diff --check
python -m pytest -q tests/unit/test_project_runners.py tests/unit/test_worktree.py
  10 passed
python -m pytest -q tests/unit/test_execution_semantics.py
  4 passed
python -m pytest -q tests/unit/test_official_harness.py tests/unit/test_swe51_runner.py
  12 passed
python -m pytest -q
  140 passed in 288.45s
```

## Production execution proof

The persistent controller integration run recorded:

```text
baseline stable target FAIL
-> first trial TARGET_STILL_FAILING with a new concrete failure packet
-> COMMIT because one prior stable counterexample signature was eliminated
-> invalid mechanical trial
-> ROLLBACK; incumbent unchanged
-> next edit uses the accepted working diff in the same conversation
-> TARGET_FIXED, target deficit reduction 1.0, no regression
-> COMMIT
-> DICC CLOSED
-> terminal certificate REACHED
```

The terminal certificate from that run had `graph_reached=true`,
`target_complete=true`, `preservation_complete=true`,
`closure_complete=true`, and no unresolved executable target IDs. A separate
regression run produced two `TARGET_FIXED` comparisons and two
`PRESERVATION_REGRESSION` comparisons, selected `ROLLBACK`, and created no
result checkpoint.

The optional post-seal edit-retention path was also executed against the same
fixture. Removing the accepted behavior caused a fresh public target
`TARGET_STILL_FAILING` comparison and `DICC OPEN`; the removal was retained,
and the incumbent patch was not replaced. This closes the remaining path where
legacy graph closure could otherwise have influenced the Reach-Avoid result.
