# SWE51 Case Process Report

- Cases observed: `12`
- Every row records generation phases, all five graph timings, DeepSeek calls, transitions, component outcomes, and isolated harness results.

| Case | Generation | Harness | Semantic | Index | Requirement | Program | Binding | Challenge | Initial patch | Commit/Rollback |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `astropy__astropy-14182` | `ERROR` | `PENDING` | 0.009 | 0.000 | 0.000 | 2726.916 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-11905` | `SEMANTIC_BLOCKED` | `PENDING` | 2.178 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-12308` | `SEMANTIC_BLOCKED` | `PENDING` | 4.171 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0/0 |
| `psf__requests-2148` | `BUDGET_EXHAUSTED` | `UNKNOWN_EXECUTION` | 0.006 | 0.283 | 2.583 | 3.894 | 0.010 | 0.003 | 30.466 | 1/0 |
| `pytest-dev__pytest-5413` | `SEMANTIC_BLOCKED` | `PENDING` | 0.183 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0/0 |
| `pytest-dev__pytest-5692` | `SEMANTIC_BLOCKED` | `PENDING` | 0.009 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0/0 |
| `pytest-dev__pytest-7220` | `ERROR` | `PENDING` | 0.015 | 0.000 | 0.000 | 79.876 | 0.000 | 0.000 | 0.000 | 0/0 |
| `scikit-learn__scikit-learn-14092` | `ERROR` | `PENDING` | 28.841 | 0.000 | 0.000 | 9.982 | 0.000 | 0.000 | 0.000 | 0/0 |
| `sphinx-doc__sphinx-8282` | `SEMANTIC_BLOCKED` | `PENDING` | 0.381 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0/0 |
| `sphinx-doc__sphinx-8721` | `SEMANTIC_BLOCKED` | `PENDING` | 0.100 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0/0 |
| `sympy__sympy-11870` | `ERROR` | `PENDING` | 0.018 | 0.000 | 0.000 | 62.571 | 0.000 | 0.000 | 0.000 | 0/0 |
| `sympy__sympy-12454` | `ERROR` | `PENDING` | 0.010 | 0.000 | 0.000 | 29.170 | 0.000 | 0.000 | 0.000 | 0/0 |

## Per-case process

### `astropy__astropy-14182`

- Generation/Harness: `ERROR` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 2726.916206283, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 0.008541046001482755}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `RecursionError: maximum recursion depth exceeded`
- Patch: ``
- Full structured process: `case_process_report.json` entry `astropy__astropy-14182`

### `django__django-11905`

- Generation/Harness: `SEMANTIC_BLOCKED` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 0, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 2.1784644071012735}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `public evidence leaves multiple mutually exclusive semantic assignments`
- Patch: ``
- Full structured process: `case_process_report.json` entry `django__django-11905`

### `django__django-12308`

- Generation/Harness: `SEMANTIC_BLOCKED` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 0, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 4.1709298035129905}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `public evidence leaves multiple mutually exclusive semantic assignments`
- Patch: ``
- Full structured process: `case_process_report.json` entry `django__django-12308`

### `psf__requests-2148`

- Generation/Harness: `BUDGET_EXHAUSTED` / `UNKNOWN_EXECUTION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T03:04:30.762169+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-98a97f4f0f7578908a6ac788'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T03:04:30.762225+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-cef10d14691af90acaea3e8f'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T03:04:30.762269+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-9520a9c6958c1fe55435b18e'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T03:05:10.246165+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-1c76602c8b54d1cdeb4a8724'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-27T03:05:11.597982+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-53adef092e874e74200fb762'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-27T03:05:13.702674+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-2d50a59d88f77b1d760346a8'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-27T03:05:14.433526+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-2e53c0b8a7c40b29226f306e'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-27T03:05:28.015239+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-8d414515d22e26b35b0841ea'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-27T03:05:28.015288+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-7f3181ecee00953eb7749836'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T03:06:38.965671+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-f3272d343485574c002f92bc'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T03:07:01.344873+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-3ab09424d45821eb4d776bd8'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T03:07:45.253707+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-01178a17dac34de29c440a2b'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T03:08:09.556915+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-2c94d2b5c7555f99eec3a142'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T03:08:48.793465+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-3d14d9b8dade2618e4537f42'}`
- Graph timings: `{"binding_graph": 0.0104281920066569, "challenge_graph": 0.0025100239872699603, "initial_generation": 30.466096478994587, "program_graph": 3.8942448290035827, "repository_index": 0.2833162919996539, "requirement_graph": 2.5825318269926356, "semantic_graph": 0.006309773001703434}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `117`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": ["proposed-edit-6b480e7dd576fcd88604be5a", "proposed-edit-59bb71b2687f06961cfe3c01", "proposed-edit-6f5cc9da8db0f983897cf0c8"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-07e6a94bb8ab52834ca583ba"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/psf__requests-2148/final_patch.diff`
- Full structured process: `case_process_report.json` entry `psf__requests-2148`

### `pytest-dev__pytest-5413`

- Generation/Harness: `SEMANTIC_BLOCKED` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 0, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 0.18335729464888573}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `public evidence leaves multiple mutually exclusive semantic assignments`
- Patch: ``
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-5413`

### `pytest-dev__pytest-5692`

- Generation/Harness: `SEMANTIC_BLOCKED` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 0, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 0.008636957965791225}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `public evidence leaves multiple mutually exclusive semantic assignments`
- Patch: ``
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-5692`

### `pytest-dev__pytest-7220`

- Generation/Harness: `ERROR` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 79.87587464199896, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 0.015431990001161466}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `SyntaxError: invalid syntax (<unknown>, line 1)`
- Patch: ``
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-7220`

### `scikit-learn__scikit-learn-14092`

- Generation/Harness: `ERROR` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 9.981938669006922, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 28.840674693004985}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `ValueError: node id collision: program-node-fc84bcb1989968b0c4ee7faa`
- Patch: ``
- Full structured process: `case_process_report.json` entry `scikit-learn__scikit-learn-14092`

### `sphinx-doc__sphinx-8282`

- Generation/Harness: `SEMANTIC_BLOCKED` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 0, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 0.3807439021766186}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `public evidence leaves multiple mutually exclusive semantic assignments`
- Patch: ``
- Full structured process: `case_process_report.json` entry `sphinx-doc__sphinx-8282`

### `sphinx-doc__sphinx-8721`

- Generation/Harness: `SEMANTIC_BLOCKED` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 0, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 0.09983614273369312}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `public evidence leaves multiple mutually exclusive semantic assignments`
- Patch: ``
- Full structured process: `case_process_report.json` entry `sphinx-doc__sphinx-8721`

### `sympy__sympy-11870`

- Generation/Harness: `ERROR` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 62.571062274000724, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 0.01846719899913296}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `ValueError: node id collision: program-node-9e27ac03dffc7d00d0680ca8`
- Patch: ``
- Full structured process: `case_process_report.json` entry `sympy__sympy-11870`

### `sympy__sympy-12454`

- Generation/Harness: `ERROR` / `PENDING`
- Phase path: `not recorded`
- Graph timings: `{"binding_graph": 0, "challenge_graph": 0, "initial_generation": 0, "program_graph": 29.17022263800027, "repository_index": 0, "requirement_graph": 0, "semantic_graph": 0.009774099999049213}`
- Graph build records: `0` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `ValueError: node id collision: program-node-85e4b2992d85d28ffda26ab2`
- Patch: ``
- Full structured process: `case_process_report.json` entry `sympy__sympy-12454`

