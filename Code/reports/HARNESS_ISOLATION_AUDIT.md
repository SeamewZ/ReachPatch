# Harness Isolation Audit

## Boundary

Generation and official evaluation are separate commands and data types:

```text
generation_public_instances.jsonl
  -> GenerationInstance
  -> ReachPatchController / PersistentDeepSeekAgent
  -> sealed final_patch.diff

official_instances.jsonl
  -> HarnessEvaluationInstance
  -> independent patched tree and official tests
  -> experiments/swe51/harness/<case>/...
```

`generate` and `case` read only `PUBLIC_PATH`. `harness` is the only command that reads `OFFICIAL_PATH`, and it runs after a patch file already exists. Harness results are never passed to `ReachPatchController`, `GeneratorConversation`, `RepairContext`, `ArtifactStore` or `resume()`.

## Type-level controls

`GenerationInstance.from_public_record` selects an allowlist:

```text
instance_id
repo
base_commit
problem_statement
hints_text
version
environment_setup_commit
visible_tests
```

It recursively rejects official-only keys including `test_patch`, `patch`, `gold_patch`, `hidden_tests`, `harness_logs`, `FAIL_TO_PASS` and `PASS_TO_PASS` in nested public metadata.

`HarnessEvaluationInstance.from_official_record` is a separate type whose official test identifiers and sealed patch path are used only by `_harness_one`. The Generator accepts no `HarnessEvaluationInstance` parameter.

## Tool-level controls

The model cannot run arbitrary shell. `RepairToolExecutor` is rooted at the incumbent repository snapshot and:

- rejects path escape and metadata directories;
- rejects test_patch, gold_patch, hidden-test and harness-result/log paths;
- permits test reads only for Controller-declared visible/public paths;
- excludes forbidden evidence from default or explicit search;
- rejects every test edit;
- runs only Controller-registered public commands with `shell=False`.

`convert_revision_action` independently rechecks absolute/parent escapes, tests, `.git`, gold, hidden and test_patch edit targets before a trial is created. Diff reconciliation additionally marks forbidden and Oracle-contamination paths.

## Storage controls

Generation artifacts live under each generation `run_root/artifacts`. They contain public evidence, active graphs, recipes, public executions, revisions, working patches and certificates.

Harness state lives under `experiments/swe51/harness/<instance_id>`. `harness_evaluation_instance.json`, patch application output and official test output are written there and to harness/experiment reports, never through `RunArtifacts` or `ArtifactStore`.

`ReachPatchController.rebuild` reads only generation run manifest, worktree checkpoints and ArtifactStore objects. It has no harness directory or official-record argument. `resume()` rejects a sealed run and therefore cannot use post-seal harness feedback for another Generator revision.

## Evaluation behavior

The harness copies the base tree into its own directory, applies the sealed pure patch with `patch -p1`, then invokes the project runner on official fail-to-pass and pass-to-pass identifiers. It records patch application, target and preservation outcomes independently. Those outcomes may be used for experiment reporting only; they cannot alter the generated patch or conversation.

## Verification

- Unit test constructs an allowed public test and a forbidden `test_patch.py`; the public read succeeds, forbidden read/search fails to expose content, and test edit is rejected.
- GenerationInstance tests reject official-only fields.
- Static call audit finds `OFFICIAL_PATH` reads only in `harness()` and `HarnessEvaluationInstance` construction only in `_harness_one`.
- DeepSeek system prompt explicitly forbids hidden tests, gold patches, test_patch and harness outcomes; enforcement does not rely on the prompt because path/type checks are mechanical.
- No production source reads a gold patch or official harness result during generation.

## Residual considerations

Repository maintainers may have ordinary public tests whose filenames include broad words such as “golden”. The hard path filter targets explicit official/gold-patch/hidden/harness evidence names; Controller-declared public tests remain readable unless they match an official-only path. Project network/database tests remain public evidence but may be `BLOCKED_EXTERNAL`; blocked status is not converted to PASS.

## Conclusion

No harness/gold feedback path into the Generator was found. The sealed patch is evaluated in an independent tree and result namespace, and the persistent Generator cannot resume from those results.
