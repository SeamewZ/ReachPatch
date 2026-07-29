# Harness Isolation Audit

## Process and data boundary

Generation reads `generation_public_instances.jsonl` into `GenerationInstance`.
That type has no FAIL_TO_PASS, PASS_TO_PASS, test patch, gold patch, or official
harness output fields. Public metadata is recursively checked for official-only
keys. The controller, repair context, tool executor, artifact store, and resume
path accept only generation-side state.

Each production generation worker additionally runs inside a bubblewrap mount
namespace. The combined dataset directory is replaced with a temporary
filesystem containing only `generation_public_instances.jsonl`; raw per-case
records and `official_instances.jsonl` do not exist in that namespace. Git
mirrors, prior harness logs/results, combined reports that may contain harness
outcomes, `/run` (including Docker/containerd sockets), and the official harness
tree are also masked. The base case export and ReachPatch implementation are
read-only. Only generation run/result roots are writable. If bubblewrap is not
available, the case returns `ENVIRONMENT_BLOCKED` with
`HARNESS_ISOLATION_UNAVAILABLE`; it never falls back to an unisolated worker.
Direct invocation of the internal `case` command outside this namespace is
rejected.

After generation seals `final_patch.diff`, the separate `harness` command reads
`official_instances.jsonl` and creates `HarnessEvaluationInstance`. Official
records and results are stored under the experiment harness roots, outside the
generation `RunArtifacts` store. A sealed generation run cannot be resumed.

## Official runner

`run_official_swebench_instance()` calls the upstream SWE-bench test-spec and
`run_instance` Docker flow. The upstream test spec owns base-commit checkout,
candidate patch application, official test patch application, official
FAIL_TO_PASS/PASS_TO_PASS selection, per-instance image, and report production.
Each `_harness_one()` call has a case-specific result and official log path.

There is no host pytest fallback. Missing Docker SDK/daemon/image or SWE-bench
runtime results in `HARNESS_NOT_RUN` and root-cause label
`HARNESS_NOT_OFFICIAL`. Timeouts from a started official container remain an
official unknown execution, not a local validation result.

## Tool and path boundary

`RepairToolExecutor` rejects path traversal, repository metadata, test patch,
gold patch, hidden test, and harness result/log paths. Only controller-declared
public test paths can be read, all test edits are forbidden, and revision action
conversion repeats the forbidden-path check. The DeepSeek system prompt also
forbids official evidence, but enforcement does not depend on the prompt.

## Cache boundary

Harness cache entries are reused only when they identify the official
SWE-bench Docker engine (or carry equivalent official image/test-patch wiring)
and match the sealed patch hash. Historical host-harness cache entries are
ignored and re-evaluated through the official adapter.

## Verification

- `test_generation_instance_rejects_official_fields`: official-only fields are
  absent and nested leaks are rejected.
- `test_generation_sandbox_hides_official_inputs_and_harness_outputs`: a real
  bubblewrap worker can read the public JSONL but cannot see official/raw case
  records or harness logs, cannot read a prior harness summary, and has no
  Docker daemon socket.
- `test_direct_case_generation_requires_public_only_sandbox`: the internal
  worker entrypoint cannot be called without the isolation marker.
- `test_official_harness_parses_upstream_report`: official patch, target, and
  preservation report parsing.
- `test_official_harness_distinguishes_patch_apply_failure`: patch application
  failure is distinct from target failure.
- `test_official_harness_reports_missing_exact_image`: exact image missing gives
  `HARNESS_NOT_RUN`.
- `test_host_harness_cache_is_not_treated_as_official`: local cache cannot be
  promoted to an official result.
- `test_harness_uses_sealed_patch_with_official_adapter`: only sealed patch text
  crosses into the post-generation official call.

Focused runner, project-runner, and harness tests: `20 passed in 7.04s` before
the `/run` hardening; the two direct isolation probes pass after that hardening.

## Current official status

Docker and exact SWE-bench instance images are available. Official evaluation
will run only after the newly isolated generation batch has sealed its patches;
no result from the earlier unisolated batch is eligible for the final
Resolved@1 claim.
