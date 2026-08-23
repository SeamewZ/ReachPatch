# Reach--Avoid architecture and 51-case validation report

## Default production flow

The production controller maintains one accepted working tree. It applies the
DeepSeek p0, synchronizes Requirement/Program/Binding/Challenge graphs, derives
content-addressed `RepairFrontier` records, and selects `RUN_CHALLENGE`,
`RECOVER_EVIDENCE`, `REPAIR`, or `SEAL` using the strict Reach--Avoid priority.
A revision is materialized in a trial tree and evaluated through mechanical,
public-target, preservation, and challenge evidence. The transition can
`COMMIT_WORKING`, `KEEP_PROVISIONAL`, or `ROLLBACK_TRIAL`; rollback only discards
the trial. Final output is always the current accepted cumulative diff.

`RepairFrontier` covers mechanical, behavior, preservation, localization,
requirement-coverage, reproduction, observation, and impact-risk gaps.
`ValidationObligation` carries command, cwd, environment, timeout, backend,
input, oracle, requirement, binding, and challenge atomically. Challenge retry
identity includes challenge, patch, recipe, oracle, and graph revision. Initial
generator empty responses are bounded to two calls; a second empty response
seals the unchanged bootstrap as `GENERATOR_BLOCKED_EXTERNAL` without a third
call.

## Verification

- `python3 -m compileall -q reachpatch experiments/reachavoid_51/runner.py`
- `PYTHONPATH=. .venv/bin/pytest -q` → **189 passed**
- DeepSeek model: `deepseek-chat`; generation budget: max 8 real non-empty
  revisions, with retries recorded in the public-only generation runner.
- 51/51 generation results are present and sealed under
  `experiments/reachavoid_51_20260823c`. All 51 final patches are non-empty.

Aggregate generation evidence:

| Metric | Value |
|---|---:|
| Cases | 51 |
| p0/final patch hash changed | 0 |
| Transition count distribution | 0: 43, 1: 5, 2: 1, 3: 1, 4: 1 |
| Decisions | ROLLBACK 14; COMMIT 0; KEEP_PROVISIONAL 0 |
| Frontier records | 441 (ACTIONABLE 310, CLOSED 131) |
| Frontier kinds | REPRODUCTION_GAP 221; IMPACT_RISK 104; REQUIREMENT_COVERAGE_GAP 75; PRESERVATION_REGRESSION 21; LOCALIZATION_FAILURE 16; MECHANICAL_FAILURE 3; BEHAVIOR_FAILURE 1 |
| Graph participation (Requirement/Program/Binding/Challenge/Reach--Avoid) | 8/32/30/37/41 cases |

The zero p0-to-final hash changes are an observed limitation of this sealed
run: revision trials were generated in 8 cases but all were rejected by the
transition evidence gate. This is not reported as an improvement, and no
official harness result was used as feedback to DeepSeek.

## Official harness

The harness was run independently after sealing both prediction files:
`harness/sealed_p0_predictions.jsonl` and
`harness/sealed_final_predictions.jsonl`.

| Stage | Submitted | Completed | Resolved | Errors |
|---|---:|---:|---:|---:|
| p0 | 51 | 50 | 4 | 1 |
| final | 51 | 50 | 4 | 1 |

The one harness error is `django__django-15819`; it is retained as an error,
not treated as a resolution. The p0→final comparison is: improved 0,
regressed 0, unchanged 51. Harness reports and logs are under
`experiments/reachavoid_51_20260823c/harness/`.

## Scope and isolation

Generation reads only public instance data and the DeepSeek key supplied at
`/home/slt/ReachPatch/ds_pwd.txt`. The official harness is a post-seal step;
its results are not available to the controller, graph builder, objective
compiler, or generator. No case ID, gold patch, hidden test, or harness result
is used by production decisions.

The two post-run robustness fixes (legacy component-evidence field defaults
and bounded initial-empty termination) are covered by the 189-test suite. The
sealed artifacts remain labeled with implementation hash
`b233fab54ae79032b0c29baf1e2c6f5c50ea7d4120a3a96553cd8ca94f33811c`; the
post-run fixes were not retroactively inserted into that sealed evidence.
