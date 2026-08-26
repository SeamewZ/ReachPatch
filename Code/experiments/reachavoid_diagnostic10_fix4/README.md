# Reach-Avoid Diagnostic 10 (fix4)

This directory contains the sealed ten-case Reach-Avoid experiment and its
official SWE-bench harness results. Official evaluation was run only after
both p0 and final predictions were sealed; harness outcomes were not fed back
to DeepSeek or the controller.

## Official result

- p0 resolved: 1/10
- final resolved: 1/10
- p0 to final improvements: 0
- p0 to final regressions: 0
- resolved instance: django__django-13448
- harness errors: 0
- empty patch: sphinx-doc__sphinx-8282 (generation did not complete)

The official harness submitted all ten predictions, executed the nine nonempty
patches, and recorded the unfinished Sphinx prediction as an empty patch.

## Published artifacts

- sealed_generation.json: immutable prediction hashes and empty-patch note
- harness/harness_summary.json: p0/final official summary
- harness/p0/ and harness/final/: official reports and execution logs
- runs/<instance>/p0.patch and final.patch: sealed patches
- runs/<instance>/transitions/: transition decisions and before/after evidence
- results/: generation result metadata
- official_evaluation_manifest.json: post-seal evaluation record

Temporary source-tree copies, checkpoint working trees, staging directories,
bytecode caches, and failed retry sandboxes are intentionally excluded from
the repository. They are execution scratch data rather than experiment output.
