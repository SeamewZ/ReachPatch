# SWE51 Experiment Report

- Cases: `51`
- Generation counts: `{"BUDGET_EXHAUSTED": 4, "ENVIRONMENT_BLOCKED": 1, "GENERATOR_NONPROGRESS": 4, "NO_NEW_REPAIR_EVIDENCE": 9, "REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE": 2, "REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH": 1, "TARGET_RECOVERY_BLOCKED": 30}`
- Harness counts: `{"BLOCKED_GENERATION": 10, "FAIL_PRESERVATION_REGRESSION": 4, "FAIL_TARGET": 23, "PASS": 14}`

| Case | Generation | Harness | F2P | P2P | Patch apply | Graphs | Components effective | Transitions | Graph reached |
|---|---|---|---|---|---|---:|---:|---:|---|
| `astropy__astropy-14182` | `GENERATOR_NONPROGRESS` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `1` | `False` |
| `astropy__astropy-14365` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `astropy__astropy-7746` | `ENVIRONMENT_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `2` | `False` |
| `django__django-10924` | `REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `5` | `False` |
| `django__django-11019` | `REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `6` | `False` |
| `django__django-11564` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_PRESERVATION_REGRESSION` | `FAIL` | `FAIL` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-11742` | `REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `6` | `False` |
| `django__django-11905` | `GENERATOR_NONPROGRESS` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-12308` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-12747` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-12908` | `GENERATOR_NONPROGRESS` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-13220` | `GENERATOR_NONPROGRESS` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-13265` | `NO_NEW_REPAIR_EVIDENCE` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `django__django-13321` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-13448` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-13660` | `TARGET_RECOVERY_BLOCKED` | `FAIL_PRESERVATION_REGRESSION` | `PASS` | `FAIL` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-13768` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-13925` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `django__django-13964` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-14017` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-14155` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-14534` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-14667` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-14730` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `django__django-14997` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15061` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15202` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15252` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15320` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15400` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15695` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15738` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15781` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `django__django-15819` | `TARGET_RECOVERY_BLOCKED` | `FAIL_PRESERVATION_REGRESSION` | `FAIL` | `FAIL` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `matplotlib__matplotlib-18869` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `psf__requests-2148` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_PRESERVATION_REGRESSION` | `FAIL` | `FAIL` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `pytest-dev__pytest-5413` | `NO_NEW_REPAIR_EVIDENCE` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `pytest-dev__pytest-5692` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `pytest-dev__pytest-7220` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `scikit-learn__scikit-learn-11040` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `scikit-learn__scikit-learn-14092` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `sphinx-doc__sphinx-8282` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `sphinx-doc__sphinx-8721` | `TARGET_RECOVERY_BLOCKED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `sympy__sympy-11870` | `NO_NEW_REPAIR_EVIDENCE` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `sympy__sympy-12454` | `NO_NEW_REPAIR_EVIDENCE` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `sympy__sympy-13437` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `sympy__sympy-18199` | `BUDGET_EXHAUSTED` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `sympy__sympy-18835` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `0` | `False` |
| `sympy__sympy-20049` | `BUDGET_EXHAUSTED` | `BLOCKED_GENERATION` | `` | `` | `` | `5/5` | `0/0` | `0` | `False` |
| `sympy__sympy-21171` | `BUDGET_EXHAUSTED` | `FAIL_TARGET` | `FAIL` | `PASS` | `PASS` | `5/5` | `0/0` | `1` | `False` |
| `sympy__sympy-22005` | `BUDGET_EXHAUSTED` | `PASS` | `PASS` | `PASS` | `PASS` | `5/5` | `0/1` | `3` | `False` |

## Graph Timing Summary

| Stage | Cases | Mean seconds | Max seconds | Total seconds |
|---|---:|---:|---:|---:|
| `active_program_slice_seconds` | 51 | 1.198 | 6.097 | 61.119 |
| `analysis_total_seconds` | 51 | 209.984 | 534.092 | 10709.208 |
| `binding_graph_incremental_seconds` | 9 | 0.096 | 0.824 | 0.864 |
| `binding_graph_initial_seconds` | 51 | 0.000 | 0.000 | 0.000 |
| `challenge_graph_incremental_seconds` | 9 | 0.008 | 0.039 | 0.070 |
| `challenge_graph_initial_seconds` | 51 | 0.000 | 0.000 | 0.000 |
| `first_patch_generation_seconds` | 51 | 6.201 | 39.599 | 316.234 |
| `initial_localization_seconds` | 51 | 0.022 | 0.190 | 1.130 |
| `initial_revision_validation_seconds` | 6 | 214.474 | 385.164 | 1286.844 |
| `program_graph_incremental_seconds` | 51 | 2.494 | 35.614 | 127.200 |
| `public_test_recovery_seconds` | 51 | 0.000 | 0.000 | 0.000 |
| `repository_index_seconds` | 51 | 18.515 | 60.238 | 944.282 |
| `requirement_core_seconds` | 51 | 0.005 | 0.031 | 0.266 |
| `requirement_graph_incremental_seconds` | 9 | 2.205 | 12.608 | 19.842 |
| `requirement_graph_initial_seconds` | 51 | 0.000 | 0.000 | 0.000 |
| `semantic_analysis_seconds` | 51 | 0.107 | 0.964 | 5.454 |
| `target_recovery_seconds` | 47 | 115.880 | 396.942 | 5446.347 |

## Graph Memory Summary

| Stage | Cases | Mean peak RSS MiB | Max peak RSS MiB |
|---|---:|---:|---:|
| `active_program_slice` | 51 | 109.8 | 204.1 |

Detailed failure rows and reasons: `failure_report.md` and `failure_report.json`.
