# Generator Loop Audit

## Production ownership

Generator logic now lives in `reachpatch/repair/deepseek_agent.py`, `tools.py` and `context.py`. `experiments/swe51/runner.py` only reads a public generation record, creates `DeepSeekHTTPTransport` and `PersistentDeepSeekAgent`, and passes the agent into `ReachPatchController`.

There is no runner-local AST-node selector, patch population, candidate mixture, LLM-only selector or `NO_ACTION` collapse.

## Persistent conversation

Each case creates exactly one `GeneratorConversation` with:

```text
conversation_id
messages
inspected_files / inspected_symbols
attempted_mechanisms
accepted_patch_hashes / rejected_patch_hashes
delivered_counterexamples
pending_context_requests
```

The same object is used by initial generation, counterexample repair and root recovery. A commit appends the cumulative incumbent hash. A rollback appends the actual rejected incremental hash, including edit-apply and mechanical-check early exits. Rollback changes only the trial worktree; messages, inspected context, failed mechanisms, packets and the previous incumbent remain.

Artifact restore reconstructs the conversation, not a new chat. The DeepSeek system message explicitly requires preservation of validated edits, executable checks, targeted context requests and no hidden/gold/harness access.

## Tool loop

The agent may call only:

```text
search_code
read_file
inspect_symbol
find_callers
find_references
show_current_diff
run_public_check
request_program_slice
apply_edits
finish_revision
```

`RepairToolExecutor` resolves paths under the incumbent snapshot, uses RepositoryIndex for search/symbol operations, runs commands without a shell, and stages edits rather than mutating the incumbent. Overlapping edits are rejected across the entire revision. `apply_edits` supports multiple files and locations but the revision declares one registered mechanism.

Official/gold/harness paths are rejected in code. Test reads are allowed only when the Controller marked the path as visible/public for this instance. Test edits are always rejected. Search silently excludes non-public test and official-only paths so their content cannot enter model messages.

The Controller clamps agent tool turns to `max_internal_tool_turns_per_revision`; the outer loop counts context-only, invalid and edited revisions against `max_submitted_revisions`.

## Context compilation

`build_repair_context` sends a compact view, not graph JSON:

- complete issue evidence;
- current cumulative working diff;
- Requirement coverage and active binding counts;
- real failed outcomes and Counterexample Packets;
- first trace divergences;
- active files/symbols/node-edge counts and graph frontiers;
- causal repair cuts and Impact Cone risks;
- confirmed preservation PASS observations;
- failed Generator mechanisms;
- remaining budget.

Initial mode emphasizes issue/localization. Counterexample mode adds newly delivered packets. Root recovery uses the same evidence and conversation after repeated nonprogress.

## Action conversion

`convert_revision_action` validates registered mechanism, repository boundary, forbidden paths, current expected source and active-slice membership. Results are:

| Status | Controller behavior |
|---|---|
| `ACCEPTED` | validate transactionally |
| `NEEDS_SLICE_EXPANSION` | requested file is admitted into affected slice during revision update |
| `INVALID_OPERATOR` | persist exact rejection; continue within revision budget |
| `INVALID_SOURCE` | persist missing/stale/out-of-slice reason; continue |
| `FORBIDDEN_PATH` | persist path reason; never apply |

A legal edit is not rejected merely because the original causal cut missed its node. A legal context request expands the slice and revalidates against real source.

## Revision transaction

`evaluate_patch_revision` performs:

1. conversion and trial checkout from the sole incumbent;
2. reverse-line-order application of all coordinated edits;
3. actual diff reconciliation and scope/gold checks;
4. syntax/import checks plus paired incumbent/trial public checks;
5. incremental RepositoryIndex and ActiveProgramSlice update;
6. diff-only Requirement promotion and affected path refresh;
7. affected Active Binding and bounded Challenge materialization;
8. real paired execution and one targeted expansion for pure UNKNOWN;
9. stable trace merge, packets, Progress/Avoid/Reach calculation;
10. atomic commit to a new incumbent or rollback of only this trial;
11. TransitionCertificate and conversation/state persistence.

The transition certificate embeds the structured actual incremental diff, graph node/edge delta, checks, outcomes, packets, diff adequacy and decision. An initial safe nonempty patch may become the incumbent before complete evidence; it cannot reach the target until active target, stable counterexample, preservation, diff adequacy and hash gates pass.

Public checks are not treated as patched-only mechanical commands. `PASS_PRESERVED` protects prior behavior, `TARGET_FIXED` adds target progress, `PRESERVATION_REGRESSION` rolls back only the revision, and `STABLE_FAIL` creates a real packet containing the command and both outputs. `UNKNOWN_EXECUTION` and `BLOCKED_EXTERNAL` remain explicit evidence states. Every comparison is persisted and appears in the TransitionCertificate; none is inferred from model text.

## Failure handling

HTTP, timeout, decoding and non-object response failures become `GeneratorBlockedExternal`. The Controller persists `generator_failure`, conversation and state, writes a terminal `GENERATOR_BLOCKED_EXTERNAL` certificate and preserves any previous incumbent. It does not report a generic success or restart with a fresh conversation.

Malformed tool arguments are returned to the same model conversation as structured tool errors. Invalid converted revisions are persisted as `generator_action_rejection` rather than becoming `NO_ACTION` or crashing the case.

## Behavioral evidence

- Single patch test: initial `[1]` revision commits, syntax-invalid revision rolls back, final `[]` revision commits and reaches; patch version is 2 while transition count is 3.
- Paired public-check tests execute real subprocesses for all four deterministic transitions: pass/pass, fail/pass, pass/fail and fail/fail.
- Conversation assertions: one ID, one message history, initial count 1, repair count 2, accepted hashes 2, rejected hashes 1.
- Conversion test covers legal single edit, coordinated edits, requested expansion, invalid operator and forbidden path.
- External failure test produces no transition or patch, persists a failure artifact and seals `GENERATOR_BLOCKED_EXTERNAL`.
- Production-path test makes the old full builder raise if invoked; the persistent patch-first run still completes.

## Audit conclusion

DeepSeek is both the initial Generator and later Repair Player. It maintains one conversation and one working patch lineage. All model-proposed success claims remain provisional until real diff reconciliation and execution evidence pass Reach-Avoid gates.
