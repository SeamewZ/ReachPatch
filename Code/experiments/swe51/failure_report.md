# SWE51 Failure Report

- Cases observed: `51`
- Failure/unknown rows: `9`

| Case | Stage | Status | Reason | Run root |
|---|---|---|---|---|
| `astropy__astropy-14182` | `generation` | `ERROR` | RecursionError: maximum recursion depth exceeded | `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-14182` |
| `django__django-11905` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11905` |
| `django__django-12308` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12308` |
| `psf__requests-2148` | `generation` | `NO_LEGAL_ACTION` | see stage details | `/home/slt/ReachPatch/Code/experiments/swe51/runs/psf__requests-2148` |
| `pytest-dev__pytest-5413` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5413` |
| `pytest-dev__pytest-5692` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5692` |
| `pytest-dev__pytest-7220` | `generation` | `ERROR` | SyntaxError: invalid syntax (<unknown>, line 1) | `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-7220` |
| `sphinx-doc__sphinx-8282` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8282` |
| `sphinx-doc__sphinx-8721` | `generation` | `SEMANTIC_BLOCKED` | public evidence leaves multiple mutually exclusive semantic assignments | `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8721` |

## Per-case diagnostics

### `astropy__astropy-14182`

- Failure point: `requirement_graph_initial`
- Status: `ERROR`
- Reason: RecursionError: maximum recursion depth exceeded
- Graph closure: `0/5`
- Transitions: `0`
- Stage timings: `{"program_graph_initial_seconds": 2726.916206283, "semantic_analysis_seconds": 0.008541046001482755}`
- Stage memory: `{"program_graph_initial": {"complete_peak_rss_mib": 9837.96484375, "in_progress_peak_rss_mib": 30.9921875}, "requirement_graph_initial": {"in_progress_peak_rss_mib": 9838.03515625}, "semantic_analysis": {"complete_peak_rss_mib": 30.9921875}}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/astropy__astropy-14182.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-14182/run_manifest.json`

### `django__django-11905`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph closure: `1/5`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 2.1784658739343286, "semantic_analysis_seconds": 2.1784644071012735}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/django__django-11905.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11905/run_manifest.json`

### `django__django-12308`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph closure: `1/5`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 4.170931367203593, "semantic_analysis_seconds": 4.1709298035129905}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/django__django-12308.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12308/run_manifest.json`

### `psf__requests-2148`

- Failure point: `repair_action_selection`
- Status: `NO_LEGAL_ACTION`
- Reason: no legal repair transition was available
- Graph closure: `5/5`
- Transitions: `0`
- Repair components: `0/184` effective; outcomes `{"UNKNOWN_ORACLE": 1107}`
- Reach-Avoid: phase `SEALED`, hard frontier `1107`, PASS/FAIL/UNKNOWN `0/0/1107`
- Stage timings: `{"analysis_total_seconds": 397.54618411231786, "baseline_execution_initial_seconds": 0.00038877222687005997, "baseline_execution_replay_seconds": 0.000327051617205143, "binding_graph_dynamic_rebuild_seconds": 29.31760357040912, "binding_graph_initial_seconds": 29.191360705532134, "challenge_graph_dynamic_rebuild_seconds": 22.408939834684134, "challenge_graph_initial_seconds": 22.832106362096965, "program_graph_dynamic_merge_seconds": 5.566515028476715e-06, "program_graph_initial_seconds": 15.09840840101242, "requirement_graph_dynamic_rebuild_seconds": 114.79989553336054, "requirement_graph_initial_seconds": 117.13769526965916, "semantic_analysis_seconds": 0.004490036517381668}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/psf__requests-2148.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/psf__requests-2148/run_manifest.json`

### `pytest-dev__pytest-5413`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph closure: `1/5`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 6.669762681238353, "semantic_analysis_seconds": 0.18335729464888573}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/pytest-dev__pytest-5413.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5413/run_manifest.json`

### `pytest-dev__pytest-5692`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph closure: `1/5`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 4.587007123976946, "semantic_analysis_seconds": 0.008636957965791225}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/pytest-dev__pytest-5692.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5692/run_manifest.json`

### `pytest-dev__pytest-7220`

- Failure point: `requirement_graph_initial`
- Status: `ERROR`
- Reason: SyntaxError: invalid syntax (<unknown>, line 1)
- Graph closure: `0/5`
- Transitions: `0`
- Stage timings: `{"program_graph_initial_seconds": 79.87587464199896, "semantic_analysis_seconds": 0.015431990001161466}`
- Stage memory: `{"program_graph_initial": {"complete_peak_rss_mib": 1018.81640625, "in_progress_peak_rss_mib": 31.1015625}, "requirement_graph_initial": {"in_progress_peak_rss_mib": 1018.81640625}, "semantic_analysis": {"complete_peak_rss_mib": 31.1015625}}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/pytest-dev__pytest-7220.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-7220/run_manifest.json`

### `sphinx-doc__sphinx-8282`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph closure: `1/5`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 4.416122045367956, "semantic_analysis_seconds": 0.3807439021766186}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/sphinx-doc__sphinx-8282.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8282/run_manifest.json`

### `sphinx-doc__sphinx-8721`

- Failure point: `semantic_analysis`
- Status: `SEMANTIC_BLOCKED`
- Reason: public evidence leaves multiple mutually exclusive semantic assignments
- Graph closure: `1/5`
- Transitions: `0`
- Stage timings: `{"analysis_total_seconds": 4.009572719223797, "semantic_analysis_seconds": 0.09983614273369312}`
- Result JSON: `/home/slt/ReachPatch/Code/experiments/swe51/results/sphinx-doc__sphinx-8721.json`
- Run manifest: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8721/run_manifest.json`

Captured traceback/stdout/stderr, patch application results, component outcomes, and DeepSeek call records are in `failure_report.json`; older workers may not have captured a traceback.
