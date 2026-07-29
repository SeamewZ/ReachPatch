# Generator Budget Audit

## Limits

The production defaults are:

```text
max revisions: 6
max tool turns per revision: 6
max searches per revision: 2
max reads per revision: 4
max public-check requests per revision: 3
```

The old experiment defaults allowed 10 revisions and 12 turns, an upper bound
of 120 model turns. The new upper bound is 36 turns, with separate hard tool
budgets.

## Persistent working patch

`GeneratorConversation` persists the accepted cumulative diff, inspected files
and line ranges, attempted mechanisms, mechanism-to-failure-signature mapping,
rolled-back diffs, eliminated/unresolved counterexamples, and passed
preservation checks. `RepairToolExecutor` is rooted at the current checkpoint
snapshot. After COMMIT, the next edit's expected source is read from that
incumbent, not from the base tree.

The full conversation and new execution evidence are serialized and restored by
`rebuild()`. A rollback records the rejected diff and failure signatures without
changing `current_working_diff`.

## Focused context

`build_repair_context()` prioritizes the issue, cumulative diff, current target,
baseline/patched output, failure signature, first project frame, exact
reproduction command, source snippets, causal cuts, previous failure reason,
preservation checks, semantic ambiguity, and remaining budget. It summarizes
the local slice rather than dumping all graph nodes/frontiers.

## Loop termination

The final tool turn advertises edit, finish, exact slice request, and blocker
actions. It does not force `apply_edits`. When the evidence fingerprint is
unchanged, the agent returns `NO_NEW_REPAIR_EVIDENCE` before another transport
request. A declared blocker terminates as `GENERATOR_NONPROGRESS`; external API
failure terminates as `GENERATOR_BLOCKED_EXTERNAL`.

No recovered target means the first Generator call is skipped entirely.

## Cache

Read results are cached by `tree_hash + path + start_line + end_line`. A cached
read does not consume another read call. New revisions receive new executor
counters while conversation memory persists.

## Verification

- `test_repair_tool_budgets_and_tree_range_read_cache`: exact 2/4/3 limits and
  cache behavior.
- `test_generator_final_turn_limits_browsing_without_forcing_an_edit`: final
  schema excludes browse/check tools and includes blocker.
- `test_production_final_turn_does_not_force_apply_edits`: transport forced
  choices remain empty.
- `test_contextless_generator_stops_before_revision_budget_is_exhausted`: one
  initial transport call, then `NO_NEW_REPAIR_EVIDENCE`.
- `test_single_working_patch_is_repaired_in_one_persistent_conversation`: two
  accepted cumulative patches and one rolled-back trial in one conversation.
- `test_target_recovery_blocked_does_not_call_generator`: zero calls when no
  stable target exists.

Final suite result: `140 passed in 288.45s`.
