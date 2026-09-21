# Prospective evidence-policy experiments

This runner measures an intervention; it does not assume that the intervention
saves tokens or preserves correctness. Current supported cohorts are public
integration smoke and exposed development issues, **not confirmatory data**.

Run from `Code/` with the project Python directory prepended to the existing
PATH. Keep `rg`, Git, Docker and bubblewrap available. Protocol freezing records
the interpreter and executable hashes. Do not replace PATH with a minimal path
that loses source-discovery tools.

```
python -m experiments.evidence_study.run init --root experiments/STUDY \
  --scope public_smoke --repetitions 3
python -m experiments.evidence_study.run run --root experiments/STUDY \
  --key-path /path/to/private/key
python -m experiments.evidence_study.run analyze --root experiments/STUDY
python -m experiments.audit_study_trajectories experiments/STUDY
```

F00/F10/F01/F11 form the scheduling × reuse factorial. All arms share compiler,
oracle rules, model settings, validation tools and budget ceilings. Runs are
independent, with randomized arm order within each issue/repetition block.
Two blocks may execute concurrently; a cell has exactly one whole-case attempt.
Transport failures within that attempt remain in the accounting.

`request_journal.jsonl` is fsynced before provider dispatch and after return/error.
An interrupted or unreported request has unknown provider cost, never measured
zero. Reservations and reported tokens are separate. The ledger does not store
prompts, source contents, inherited environment values or credentials.

`sealed_study.json` covers every registered cell, including generation failures
with empty patches. Official evaluation cannot start before this cohort seal.
Only identical sealed patches may receive infrastructure retries. The runner
checks code/data/tool hashes before starting or resuming generation.

The supplementary trajectory audit also includes interrupted attempts without
terminal reports. Its raw-argument duplicate counters do **not** measure
state-conditioned unnecessary work. Public smoke success is distinct from
controller certification and official SWE-bench resolution. Repeats of one
fixture do not support an issue-level confidence interval.

Historical and interrupted versions must stay separate from each new freeze.
Current limitations and the remaining confirmatory study are documented in
`Paper/fse2027/experiment_protocol_zh.md`.
