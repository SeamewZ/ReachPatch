# Reach-Avoid Diagnostic 10 Audit

This directory contains the sealed diagnostic-10 generation output and the official harness reports for the Reach-Avoid rewrite at implementation commit bae8c483491908c48a6e3988cd895e9365496ad1.

## Outcome

- Submitted: 10 cases
- Harness completed: 8 cases
- P0 resolved: 3/10
- Final resolved: 3/10
- P0 -> final improvement: 0
- P0 pass -> final fail: 0
- Resolved in both submissions: django__django-12747, django__django-13448, scikit-learn__scikit-learn-14092
- Incomplete generation cases sealed with an empty patch: django__django-13321, sympy__sympy-20049

The rewrite therefore did not meet the diagnostic acceptance criterion. This bundle is evidence for analysis, not a claim of effectiveness.

## Where to look

- generation_manifest.json and sealed_generation.json: case list and sealing decision.
- generation_summary.json and results/*.json: per-case checkpoints, patch hashes, component participation, and terminal status.
- harness/harness_summary.json: immutable P0/final harness counts and prediction/report hashes.
- harness/sealed_*_predictions.jsonl: exact sealed submissions.
- harness/p0/ and harness/final/: harness reports and logs.
- runs/<case>/run.json, terminal.json, p0.patch, final.patch: controller-level outcome and complete base-to-patch diffs.
- runs/<case>/repair_objectives/: objectives supplied to the repair player.
- runs/<case>/transitions/: transition certificates and evidence. The Django 14534 transitions are especially relevant: revisions were KEEP_REPAIRING, but the selected ISSUE_DIFF_MISMATCH frontier had no executable obligations or selected scenario keys, so no selected progress was recognized and terminal output remained the P0/safe-best checkpoint.

## Excluded from this audit commit

Mutable source copies and high-volume runtime data are intentionally excluded: checkpoint_store/, working_tree/, generator_staging/, trace_tmp/, trials/, and the external official dataset. The source code is in the repository commit referenced above; the included summaries, objectives, patches, and transition files preserve the decision trail without duplicating those trees.
