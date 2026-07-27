# SWE51 Failure Report

- Cases observed: `51`
- Failure/unknown rows: `13`

| Case | Stage | Status | Reason | Run root |
|---|---|---|---|---|
| `astropy__astropy-14182` | `generation` | `ERROR` | RecursionError: maximum recursion depth exceeded | `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-14182` |
| `django__django-11905` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11905` |
| `django__django-12308` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12308` |
| `psf__requests-2148` | `generation` | `BUDGET_EXHAUSTED` | revision budget exhausted before Reach: submitted=10, deferred_bindings=4, active_challenges=0, real_challenge_executions=0 | `/home/slt/ReachPatch/Code/experiments/swe51/runs/psf__requests-2148` |
| `psf__requests-2148` | `official_harness` | `UNKNOWN_EXECUTION` | official harness isolated result: target=BLOCKED_EXTERNAL, preservation=BLOCKED_EXTERNAL | `` |
| `pytest-dev__pytest-5413` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5413` |
| `pytest-dev__pytest-5692` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5692` |
| `pytest-dev__pytest-7220` | `generation` | `ERROR` | SyntaxError: invalid syntax (<unknown>, line 1) | `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-7220` |
| `scikit-learn__scikit-learn-14092` | `generation` | `ERROR` | ValueError: node id collision: program-node-fc84bcb1989968b0c4ee7faa | `/home/slt/ReachPatch/Code/experiments/swe51/runs/scikit-learn__scikit-learn-14092` |
| `sphinx-doc__sphinx-8282` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8282` |
| `sphinx-doc__sphinx-8721` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8721` |
| `sympy__sympy-11870` | `generation` | `ERROR` | ValueError: node id collision: program-node-9e27ac03dffc7d00d0680ca8 | `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-11870` |
| `sympy__sympy-12454` | `generation` | `ERROR` | ValueError: node id collision: program-node-85e4b2992d85d28ffda26ab2 | `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-12454` |

## Per-case diagnostics

### `astropy__astropy-14182`

- Failure point: `requirement_graph_initial`
- Status: `ERROR`
- Reason: RecursionError: maximum recursion depth exceeded
- Graph stack: `0` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"program_graph_initial_seconds": 2726.916206283, "semantic_analysis_seconds": 0.008541046001482755}`
- Stage memory: `{"program_graph_initial": {"complete_peak_rss_mib": 9837.96484375, "in_progress_peak_rss_mib": 30.9921875}, "requirement_graph_initial": {"in_progress_peak_rss_mib": 9838.03515625}, "semantic_analysis": {"complete_peak_rss_mib": 30.9921875}}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/astropy__astropy-14182.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-14182/run_manifest.json`

### `django__django-11905`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph stack: `1` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 2.1784658739343286, "semantic_analysis_seconds": 2.1784644071012735}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/django__django-11905.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11905/run_manifest.json`

### `django__django-12308`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph stack: `1` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 4.170931367203593, "semantic_analysis_seconds": 4.1709298035129905}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/django__django-12308.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12308/run_manifest.json`

### `psf__requests-2148`

- Failure point: `repair_generation`
- Status: `BUDGET_EXHAUSTED`
- Reason: revision budget exhausted before Reach: submitted=10, deferred_bindings=4, active_challenges=0, real_challenge_executions=0
- Graph stack: `5` graphs; full closure `False`
- Transitions: `1`
- Reach-Avoid: phase `SEALED`, hard frontier `1`, PASS/FAIL/UNKNOWN `0/0/0`
- Stage timings: `{"active_program_slice_seconds": 3.4674210580124054, "analysis_total_seconds": 61.26112899999134, "binding_graph_incremental_seconds": 0.0033648060052655637, "binding_graph_initial_seconds": 0.007063386001391336, "challenge_graph_incremental_seconds": 0.000934204988880083, "challenge_graph_initial_seconds": 0.0015758189983898774, "first_patch_generation_seconds": 30.466096478994587, "initial_localization_seconds": 0.0006849759956821799, "initial_revision_validation_seconds": 4.195282111002598, "program_graph_incremental_seconds": 0.4268237709911773, "public_test_recovery_seconds": 0.0, "repository_index_seconds": 0.2833162919996539, "requirement_core_seconds": 0.0019731880020117387, "requirement_graph_incremental_seconds": 1.6471682979899924, "requirement_graph_initial_seconds": 0.9333903410006315, "semantic_analysis_seconds": 0.006309773001703434}`
- Stage memory: `{"active_program_slice": {"peak_rss_mib": 69.484375, "precise_files": 40, "precise_functions": 195}, "graph_build_records": [{"active_binding_units": 0, "binding_graph_seconds": 0.007063386001391336, "binding_units": 4, "challenge_cells": 0, "challenge_graph_seconds": 0.0015758189983898774, "deferred_binding_units": 4, "frontier_count": 478, "kind": "initial_active", "program_edges": 21891, "program_graph_seconds": 3.4674210580124054, "program_nodes": 10414, "requirement_graph_seconds": 0.9333903410006315, "requirement_leaves": 2, "requirement_path_obligations": 4, "truncated": true}, {"active_binding_units": 0, "binding_graph_seconds": 0.0033648060052655637, "binding_units": 4, "challenge_cells": 0, "challenge_graph_seconds": 0.000934204988880083, "deferred_binding_units": 4, "kind": "incremental_transition", "peak_rss_mib": 86.6171875, "program_edges": 17234, "program_graph_seconds": 0.4268237709911773, "program_nodes": 8699, "repository_index_seconds": 0.02628323899989482, "requirement_graph_seconds": 1.6471682979899924, "requirement_leaves": 2, "requirement_path_obligations": 4, "total_seconds": 2.10457431897521, "transition_id": "patch-revision-transition-07e6a94bb8ab52834ca583ba", "truncated": true}], "peak_rss_mib": 86.6171875, "repository_index": {"file_count": 83}}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/psf__requests-2148.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/psf__requests-2148/run_manifest.json`

### `psf__requests-2148`

- Failure point: `official_harness`
- Status: `UNKNOWN_EXECUTION`
- Reason: official harness isolated result: target=BLOCKED_EXTERNAL, preservation=BLOCKED_EXTERNAL
- Graph stack: `0` graphs; full closure `False`
- Transitions: `0`
- Result JSON: ``
- Run manifest: ``

### `pytest-dev__pytest-5413`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph stack: `1` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 6.669762681238353, "semantic_analysis_seconds": 0.18335729464888573}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/pytest-dev__pytest-5413.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5413/run_manifest.json`

### `pytest-dev__pytest-5692`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph stack: `1` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 4.587007123976946, "semantic_analysis_seconds": 0.008636957965791225}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/pytest-dev__pytest-5692.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5692/run_manifest.json`

### `pytest-dev__pytest-7220`

- Failure point: `requirement_graph_initial`
- Status: `ERROR`
- Reason: SyntaxError: invalid syntax (<unknown>, line 1)
- Graph stack: `0` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"program_graph_initial_seconds": 79.87587464199896, "semantic_analysis_seconds": 0.015431990001161466}`
- Stage memory: `{"program_graph_initial": {"complete_peak_rss_mib": 1018.81640625, "in_progress_peak_rss_mib": 31.1015625}, "requirement_graph_initial": {"in_progress_peak_rss_mib": 1018.81640625}, "semantic_analysis": {"complete_peak_rss_mib": 31.1015625}}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/pytest-dev__pytest-7220.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-7220/run_manifest.json`

### `scikit-learn__scikit-learn-14092`

- Failure point: `program_graph_initial`
- Status: `ERROR`
- Reason: ValueError: node id collision: program-node-fc84bcb1989968b0c4ee7faa
- Graph stack: `0` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"program_graph_definition_index_seconds": 9.981938669006922, "semantic_analysis_seconds": 28.840674693004985}`
- Stage memory: `{"program_graph_initial": {"in_progress_peak_rss_mib": 518.25}, "semantic_analysis": {"complete_peak_rss_mib": 518.25}}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/scikit-learn__scikit-learn-14092.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/scikit-learn__scikit-learn-14092/run_manifest.json`

### `sphinx-doc__sphinx-8282`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph stack: `1` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 4.416122045367956, "semantic_analysis_seconds": 0.3807439021766186}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/sphinx-doc__sphinx-8282.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8282/run_manifest.json`

### `sphinx-doc__sphinx-8721`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph stack: `1` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 4.009572719223797, "semantic_analysis_seconds": 0.09983614273369312}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/sphinx-doc__sphinx-8721.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8721/run_manifest.json`

### `sympy__sympy-11870`

- Failure point: `program_graph_initial`
- Status: `ERROR`
- Reason: ValueError: node id collision: program-node-9e27ac03dffc7d00d0680ca8
- Graph stack: `0` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"program_graph_definition_index_seconds": 62.571062274000724, "semantic_analysis_seconds": 0.01846719899913296}`
- Stage memory: `{"program_graph_initial": {"in_progress_peak_rss_mib": 34.82421875}, "semantic_analysis": {"complete_peak_rss_mib": 34.82421875}}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/sympy__sympy-11870.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-11870/run_manifest.json`

### `sympy__sympy-12454`

- Failure point: `program_graph_initial`
- Status: `ERROR`
- Reason: ValueError: node id collision: program-node-85e4b2992d85d28ffda26ab2
- Graph stack: `0` graphs; full closure `False`
- Transitions: `0`
- Stage timings: `{"program_graph_definition_index_seconds": 29.17022263800027, "semantic_analysis_seconds": 0.009774099999049213}`
- Stage memory: `{"program_graph_initial": {"in_progress_peak_rss_mib": 34.82421875}, "semantic_analysis": {"complete_peak_rss_mib": 34.82421875}}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/sympy__sympy-12454.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-12454/run_manifest.json`

Captured traceback/stdout/stderr, patch application results, component outcomes, and DeepSeek call records are in `failure_report.json`; older workers may not have captured a traceback.
