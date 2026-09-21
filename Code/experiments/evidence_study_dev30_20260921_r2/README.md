# Evidence-efficiency development cohort (2026-09-21)

This directory contains the frozen protocol, sealed cell summaries, official
harness outcomes, and accounting artifacts for the 30-issue development
cohort. It is not a held-out confirmation study.

All four arms used DeepSeek Flash with the same case budget and one registered
attempt per cell. The cohort was sealed before official evaluation. The
container harness reports were reconciled from their immutable prediction
hashes; no model request or container evaluation was rerun during
reconciliation.

| Metric | F00 | F11 | Change |
|---|---:|---:|---:|
| Resolved@1 | 7/30 (23.3%) | 8/30 (26.7%) | +1 case |
| Model calls | 323 | 282 | -12.7% |
| Reported provider tokens | 3,419,018 | 2,961,716 | at least -13.4% |
| Calls per resolved | 46.1 | 35.3 | approximately -23.6% |
| Reported tokens per resolved | 488,431 | 370,215 | at least -24.2% |

The token figures are lower bounds: the request journals contain 7 unknown
usage records for F00 and F11 (and 8 for each of F10 and F01). Consequently,
the cohort supports a descriptive development observation, not a claim of
complete cost reduction, statistical non-inferiority, or generalisation.

The complete production tree and raw Docker/worktree outputs are intentionally
not committed. They are large mutable execution artifacts; the sealed hashes,
per-cell summaries, protocol, official reports, and source snapshots provide
the reproducibility boundary for this tag.
