# SWE51 Experiment Report

- Cases: `12`
- Generation counts: `{"BUDGET_EXHAUSTED": 1, "ERROR": 5, "SEMANTIC_BLOCKED": 6}`
- Harness counts: `{"PENDING": 11, "UNKNOWN_EXECUTION": 1}`

| Case | Generation | Harness | F2P | P2P | Patch apply | Graphs | Components effective | Transitions | Graph reached |
|---|---|---|---|---|---|---:|---:|---:|---|
| `astropy__astropy-14182` | `ERROR` | `PENDING` | `` | `` | `` | `0/5` | `0/0` | `0` | `None` |
| `django__django-11905` | `SEMANTIC_BLOCKED` | `PENDING` | `` | `` | `` | `1/5` | `0/0` | `0` | `None` |
| `django__django-12308` | `SEMANTIC_BLOCKED` | `PENDING` | `` | `` | `` | `1/5` | `0/0` | `0` | `None` |
| `psf__requests-2148` | `BUDGET_EXHAUSTED` | `UNKNOWN_EXECUTION` | `BLOCKED_EXTERNAL` | `BLOCKED_EXTERNAL` | `PASS` | `5/5` | `0/0` | `1` | `False` |
| `pytest-dev__pytest-5413` | `SEMANTIC_BLOCKED` | `PENDING` | `` | `` | `` | `1/5` | `0/0` | `0` | `None` |
| `pytest-dev__pytest-5692` | `SEMANTIC_BLOCKED` | `PENDING` | `` | `` | `` | `1/5` | `0/0` | `0` | `None` |
| `pytest-dev__pytest-7220` | `ERROR` | `PENDING` | `` | `` | `` | `0/5` | `0/0` | `0` | `None` |
| `scikit-learn__scikit-learn-14092` | `ERROR` | `PENDING` | `` | `` | `` | `0/5` | `0/0` | `0` | `None` |
| `sphinx-doc__sphinx-8282` | `SEMANTIC_BLOCKED` | `PENDING` | `` | `` | `` | `1/5` | `0/0` | `0` | `None` |
| `sphinx-doc__sphinx-8721` | `SEMANTIC_BLOCKED` | `PENDING` | `` | `` | `` | `1/5` | `0/0` | `0` | `None` |
| `sympy__sympy-11870` | `ERROR` | `PENDING` | `` | `` | `` | `0/5` | `0/0` | `0` | `None` |
| `sympy__sympy-12454` | `ERROR` | `PENDING` | `` | `` | `` | `0/5` | `0/0` | `0` | `None` |

## Graph Timing Summary

| Stage | Cases | Mean seconds | Max seconds | Total seconds |
|---|---:|---:|---:|---:|
| `active_program_slice_seconds` | 1 | 3.467 | 3.467 | 3.467 |
| `analysis_total_seconds` | 7 | 12.470 | 61.261 | 87.293 |
| `binding_graph_incremental_seconds` | 1 | 0.003 | 0.003 | 0.003 |
| `binding_graph_initial_seconds` | 1 | 0.007 | 0.007 | 0.007 |
| `challenge_graph_incremental_seconds` | 1 | 0.001 | 0.001 | 0.001 |
| `challenge_graph_initial_seconds` | 1 | 0.002 | 0.002 | 0.002 |
| `first_patch_generation_seconds` | 1 | 30.466 | 30.466 | 30.466 |
| `initial_localization_seconds` | 1 | 0.001 | 0.001 | 0.001 |
| `initial_revision_validation_seconds` | 1 | 4.195 | 4.195 | 4.195 |
| `program_graph_definition_index_seconds` | 3 | 33.908 | 62.571 | 101.723 |
| `program_graph_incremental_seconds` | 1 | 0.427 | 0.427 | 0.427 |
| `program_graph_initial_seconds` | 2 | 1403.396 | 2726.916 | 2806.792 |
| `public_test_recovery_seconds` | 1 | 0.000 | 0.000 | 0.000 |
| `repository_index_seconds` | 1 | 0.283 | 0.283 | 0.283 |
| `requirement_core_seconds` | 1 | 0.002 | 0.002 | 0.002 |
| `requirement_graph_incremental_seconds` | 1 | 1.647 | 1.647 | 1.647 |
| `requirement_graph_initial_seconds` | 1 | 0.933 | 0.933 | 0.933 |
| `semantic_analysis_seconds` | 12 | 2.993 | 28.841 | 35.921 |

## Graph Memory Summary

| Stage | Cases | Mean peak RSS MiB | Max peak RSS MiB |
|---|---:|---:|---:|
| `active_program_slice` | 1 | 69.5 | 69.5 |
| `program_graph_initial` | 5 | 2288.9 | 9838.0 |
| `requirement_graph_initial` | 2 | 5428.4 | 9838.0 |
| `semantic_analysis` | 5 | 130.0 | 518.2 |

Detailed failure rows and reasons: `failure_report.md` and `failure_report.json`.
