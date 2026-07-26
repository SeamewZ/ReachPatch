# SWE51 Experiment Report

- Cases: `12`
- Generation counts: `{"ERROR": 5, "NO_LEGAL_ACTION": 1, "SEMANTIC_BLOCKED": 6}`
- Harness counts: `{"PENDING": 12}`

| Case | Generation | Harness | F2P | P2P | Patch apply | Graphs | Components effective | Transitions | Graph reached |
|---|---|---|---|---|---|---:|---:|---:|---|
| `astropy__astropy-14182` | `ERROR` | `PENDING` | `` | `` | `` | `0/5` | `0/0` | `0` | `None` |
| `django__django-11905` | `SEMANTIC_BLOCKED` | `PENDING` | `` | `` | `` | `1/5` | `0/0` | `0` | `None` |
| `django__django-12308` | `SEMANTIC_BLOCKED` | `PENDING` | `` | `` | `` | `1/5` | `0/0` | `0` | `None` |
| `psf__requests-2148` | `NO_LEGAL_ACTION` | `PENDING` | `` | `` | `` | `5/5` | `0/184` | `0` | `False` |
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
| `analysis_total_seconds` | 7 | 60.511 | 397.546 | 423.578 |
| `baseline_execution_initial_seconds` | 1 | 0.000 | 0.000 | 0.000 |
| `baseline_execution_replay_seconds` | 1 | 0.000 | 0.000 | 0.000 |
| `binding_graph_dynamic_rebuild_seconds` | 1 | 29.318 | 29.318 | 29.318 |
| `binding_graph_initial_seconds` | 1 | 29.191 | 29.191 | 29.191 |
| `challenge_graph_dynamic_rebuild_seconds` | 1 | 22.409 | 22.409 | 22.409 |
| `challenge_graph_initial_seconds` | 1 | 22.832 | 22.832 | 22.832 |
| `program_graph_definition_index_seconds` | 3 | 33.908 | 62.571 | 101.723 |
| `program_graph_dynamic_merge_seconds` | 1 | 0.000 | 0.000 | 0.000 |
| `program_graph_initial_seconds` | 3 | 940.630 | 2726.916 | 2821.890 |
| `requirement_graph_dynamic_rebuild_seconds` | 1 | 114.800 | 114.800 | 114.800 |
| `requirement_graph_initial_seconds` | 1 | 117.138 | 117.138 | 117.138 |
| `semantic_analysis_seconds` | 12 | 2.993 | 28.841 | 35.919 |

## Graph Memory Summary

| Stage | Cases | Mean peak RSS MiB | Max peak RSS MiB |
|---|---:|---:|---:|
| `program_graph_initial` | 5 | 2288.9 | 9838.0 |
| `requirement_graph_initial` | 2 | 5428.4 | 9838.0 |
| `semantic_analysis` | 5 | 130.0 | 518.2 |

Detailed failure rows and reasons: `failure_report.md` and `failure_report.json`.
