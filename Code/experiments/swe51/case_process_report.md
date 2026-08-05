# SWE51 Case Process Report

- Cases observed: `51`
- Every row records generation phases, all five graph timings, DeepSeek calls, transitions, component outcomes, and isolated harness results.

| Case | Generation | Harness | Semantic | Index | Requirement | Program | Binding | Challenge | Initial patch | Commit/Rollback |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `astropy__astropy-14182` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.005 | 5.130 | 0.387 | 4.471 | 0.022 | 0.001 | 36.736 | 1/0 |
| `astropy__astropy-14365` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.105 | 5.132 | 0.270 | 3.368 | 0.073 | 0.004 | 36.963 | 1/0 |
| `astropy__astropy-7746` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.002 | 3.371 | 0.334 | 4.231 | 0.021 | 0.001 | 52.947 | 1/0 |
| `django__django-10924` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.382 | 5.742 | 0.067 | 1.411 | 0.004 | 0.001 | 94.402 | 1/0 |
| `django__django-11019` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.984 | 5.717 | 0.197 | 2.868 | 0.042 | 0.002 | 78.055 | 1/0 |
| `django__django-11564` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_PRESERVATION_REGRESSION` | 0.321 | 5.854 | 2.511 | 2.054 | 0.007 | 0.001 | 235.557 | 0/0 |
| `django__django-11742` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.003 | 5.431 | 0.000 | 2.355 | 0.000 | 0.000 | 132.377 | 0/0 |
| `django__django-11905` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.390 | 5.451 | 0.258 | 3.080 | 0.088 | 0.003 | 62.703 | 1/0 |
| `django__django-12308` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.012 | 6.013 | 0.152 | 1.872 | 0.006 | 0.001 | 95.219 | 1/0 |
| `django__django-12747` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.003 | 5.704 | 0.059 | 2.015 | 0.004 | 0.001 | 42.291 | 1/0 |
| `django__django-12908` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.016 | 6.268 | 0.283 | 3.245 | 0.110 | 0.002 | 40.178 | 1/0 |
| `django__django-13220` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.018 | 6.531 | 0.101 | 2.438 | 0.006 | 0.001 | 64.835 | 1/0 |
| `django__django-13265` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.031 | 6.176 | 2.826 | 2.814 | 0.038 | 0.001 | 85.679 | 1/0 |
| `django__django-13321` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.087 | 6.082 | 0.068 | 2.264 | 0.024 | 0.001 | 261.227 | 1/0 |
| `django__django-13448` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.085 | 6.348 | 0.289 | 3.407 | 0.017 | 0.001 | 54.471 | 1/0 |
| `django__django-13660` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_PRESERVATION_REGRESSION` | 0.018 | 6.973 | 0.144 | 1.819 | 0.022 | 0.000 | 9.634 | 1/0 |
| `django__django-13768` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.026 | 6.445 | 0.087 | 1.835 | 0.019 | 0.002 | 36.517 | 1/0 |
| `django__django-13925` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.518 | 6.102 | 0.416 | 3.445 | 0.091 | 0.003 | 72.655 | 1/0 |
| `django__django-13964` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.047 | 6.673 | 0.151 | 2.205 | 0.031 | 0.001 | 50.554 | 1/0 |
| `django__django-14017` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.006 | 6.464 | 0.144 | 1.808 | 0.028 | 0.001 | 58.421 | 1/0 |
| `django__django-14155` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.006 | 6.552 | 0.259 | 3.280 | 0.013 | 0.000 | 42.277 | 1/0 |
| `django__django-14534` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.059 | 6.198 | 0.251 | 2.950 | 0.011 | 0.001 | 72.018 | 1/0 |
| `django__django-14667` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.205 | 7.068 | 2.827 | 3.901 | 0.051 | 0.004 | 267.092 | 1/0 |
| `django__django-14730` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_PRESERVATION_REGRESSION` | 0.035 | 5.911 | 0.192 | 3.366 | 0.030 | 0.001 | 48.123 | 1/0 |
| `django__django-14997` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.008 | 6.646 | 0.151 | 2.112 | 0.009 | 0.001 | 36.615 | 1/0 |
| `django__django-15061` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.016 | 7.124 | 0.205 | 3.744 | 0.051 | 0.001 | 35.370 | 1/0 |
| `django__django-15202` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.002 | 6.653 | 0.133 | 2.044 | 0.029 | 0.001 | 75.901 | 1/0 |
| `django__django-15252` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.774 | 7.132 | 2.645 | 3.337 | 0.166 | 0.002 | 218.805 | 1/0 |
| `django__django-15320` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.018 | 6.779 | 0.140 | 2.288 | 0.014 | 0.000 | 75.641 | 1/0 |
| `django__django-15400` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.038 | 6.547 | 0.219 | 3.470 | 0.018 | 0.001 | 29.931 | 1/0 |
| `django__django-15695` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.060 | 6.750 | 2.889 | 4.664 | 0.016 | 0.001 | 108.609 | 1/0 |
| `django__django-15738` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_PRESERVATION_REGRESSION` | 0.043 | 6.681 | 2.970 | 2.827 | 0.138 | 0.004 | 158.787 | 1/0 |
| `django__django-15781` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.086 | 6.762 | 0.060 | 1.111 | 0.004 | 0.001 | 15.725 | 1/0 |
| `django__django-15819` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_PRESERVATION_REGRESSION` | 0.013 | 7.046 | 0.127 | 2.166 | 0.033 | 0.001 | 89.442 | 1/0 |
| `matplotlib__matplotlib-18869` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.173 | 2.581 | 0.209 | 2.096 | 0.023 | 0.001 | 53.365 | 1/0 |
| `psf__requests-2148` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_PRESERVATION_REGRESSION` | 0.016 | 0.277 | 0.053 | 1.363 | 0.004 | 0.001 | 36.970 | 1/0 |
| `pytest-dev__pytest-5413` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.185 | 0.690 | 0.206 | 3.857 | 0.023 | 0.002 | 37.094 | 1/0 |
| `pytest-dev__pytest-5692` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.013 | 0.623 | 0.184 | 3.903 | 0.032 | 0.002 | 39.467 | 1/0 |
| `pytest-dev__pytest-7220` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_PRESERVATION_REGRESSION` | 0.021 | 0.723 | 0.628 | 3.087 | 0.047 | 0.002 | 64.896 | 1/0 |
| `scikit-learn__scikit-learn-11040` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.152 | 2.515 | 0.400 | 5.797 | 0.072 | 0.001 | 86.020 | 1/0 |
| `scikit-learn__scikit-learn-14092` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.392 | 2.627 | 0.855 | 5.466 | 0.352 | 0.002 | 136.821 | 1/0 |
| `sphinx-doc__sphinx-8282` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.038 | 1.156 | 0.154 | 1.854 | 0.013 | 0.001 | 46.429 | 1/0 |
| `sphinx-doc__sphinx-8721` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.029 | 1.275 | 0.221 | 2.962 | 0.017 | 0.001 | 35.008 | 1/0 |
| `sympy__sympy-11870` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.057 | 12.140 | 0.242 | 5.356 | 0.011 | 0.001 | 62.856 | 1/0 |
| `sympy__sympy-12454` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.040 | 7.104 | 0.249 | 5.068 | 0.013 | 0.001 | 45.230 | 1/0 |
| `sympy__sympy-13437` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.003 | 12.874 | 0.194 | 3.814 | 0.046 | 0.001 | 51.351 | 1/0 |
| `sympy__sympy-18199` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.011 | 19.154 | 0.444 | 5.366 | 0.100 | 0.001 | 40.275 | 1/0 |
| `sympy__sympy-18835` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.030 | 19.102 | 0.277 | 3.379 | 0.055 | 0.001 | 81.821 | 1/0 |
| `sympy__sympy-20049` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.110 | 19.550 | 0.896 | 5.661 | 0.165 | 0.004 | 69.239 | 1/0 |
| `sympy__sympy-21171` | `EVIDENCE_LIMITED_COMPLETE` | `FAIL_TARGET` | 0.058 | 20.880 | 0.303 | 3.832 | 0.063 | 0.001 | 42.337 | 1/0 |
| `sympy__sympy-22005` | `EVIDENCE_LIMITED_COMPLETE` | `PASS` | 0.028 | 19.951 | 0.307 | 5.471 | 0.018 | 0.002 | 104.852 | 2/0 |

## Per-case process

### `astropy__astropy-14182`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T14:53:40.527898+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-1e0e721c5825748e770a92cc'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T14:53:40.527953+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-944bbabf32f067b65d617cf3'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T14:53:40.527998+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-5696d902ad009c67d4da793c'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T14:55:10.370694+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-d121aaf33330c329b5940d9e'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T14:55:33.871566+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-3e6518e00b8403947fb196fb'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T14:55:35.262814+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-054ccff2aac4dcb09b8cf7cf'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T14:55:36.344571+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-2d36051a3ea569183af4c878'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T14:55:52.541622+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-1b3796b80b2148e5dcfa13b8'}`
- Graph timings: `{"binding_graph": 0.02211534301750362, "challenge_graph": 0.0009037819691002369, "initial_generation": 36.73593725007959, "program_graph": 4.471466219052672, "repository_index": 5.130326967919245, "requirement_graph": 0.3868063109694049, "semantic_graph": 0.005420492962002754}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `19`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-020578cf18d55e316c76a111"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-14182/final_patch.diff`
- Full structured process: `case_process_report.json` entry `astropy__astropy-14182`

### `astropy__astropy-14365`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:31:59.454905+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-0969ac5b8a7b02a9f34c0955'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:31:59.454955+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-33ff91729834be03077a8591'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:31:59.454995+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-c0a601611d4d1bb889636e90'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:33:20.668751+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-3edb1d966136b51877ab88ee'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:33:43.615199+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-5647c62d029bb53f7921724b'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:33:45.361107+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-c4488e4d398fc4575cf989ba'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:33:46.465571+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-27afef76236fca5bc5f8914b'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:33:56.607997+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-4e25f83433c1d1997b610c14'}`
- Graph timings: `{"binding_graph": 0.07284112204797566, "challenge_graph": 0.003715591039508581, "initial_generation": 36.96272452000994, "program_graph": 3.367616320727393, "repository_index": 5.132355878013186, "requirement_graph": 0.2700711640063673, "semantic_graph": 0.104911326081492}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `19`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-bb016a4b6b84bc36aee88f65"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-14365/final_patch.diff`
- Full structured process: `case_process_report.json` entry `astropy__astropy-14365`

### `astropy__astropy-7746`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T14:53:38.492818+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-ae81574372aa1ff997c0c2d5'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T14:53:38.492877+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-50dd0b9678363f669295d378'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T14:53:38.492922+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-0ebaf48ac1226d2496222806'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T14:55:20.612958+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-b2b1f701c76b33bcc3278944'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T14:55:57.314914+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-cce6f6874888954f07878be9'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T14:55:58.232672+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-fac4d62f21226517b07d93a6'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T14:55:59.332501+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-5baa8fc5da6e5e7157fbb9c1'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T14:56:05.622716+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-11363c47cc3162afdf7173b6'}`
- Graph timings: `{"binding_graph": 0.020693240920081735, "challenge_graph": 0.0008241409668698907, "initial_generation": 52.946630944963545, "program_graph": 4.2306494830409065, "repository_index": 3.370584537042305, "requirement_graph": 0.33384394098538905, "semantic_graph": 0.002420860924758017}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `5`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-c3cfe482648e0199d36ad5e1"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-7746/final_patch.diff`
- Full structured process: `case_process_report.json` entry `astropy__astropy-7746`

### `django__django-10924`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T14:53:43.943329+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-43fdd83b9afed76aef1b61a4'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T14:53:43.943379+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-87680a14fa9b8cdbd540acfa'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T14:53:43.943417+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-bad9827f4d696a0de9954d7d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T14:57:13.361139+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-72c4ecb7eef9ad99b734ea76'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T14:57:26.591930+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-eb4517d581aed08e797af254'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T14:57:27.885562+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-8f0ecf1162d62d463a4e319f'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T14:57:32.901564+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-7b554a92d14b1c76215a6b33'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T14:57:51.951512+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-957177e2745c979a23a7a50e'}`
- Graph timings: `{"binding_graph": 0.0043333040084689856, "challenge_graph": 0.0012006089091300964, "initial_generation": 94.40238218894228, "program_graph": 1.411203968920745, "repository_index": 5.741539018927142, "requirement_graph": 0.06686636712402105, "semantic_graph": 0.3818688690662384}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `30`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-b41c4fe7a1ca6525662f525e"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-10924/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-10924`

### `django__django-11019`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T14:53:45.484728+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-09c0592b1bcd40496cadd353'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T14:53:45.484796+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-a52799f501795da4c35297aa'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T14:53:45.484843+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-0bcce4746238b20b7bc76c7d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T14:56:39.036386+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-0b9af27a16f674a073f6d3f4'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T14:57:05.954892+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-6864281bbb9d9a961fe9d659'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T14:57:07.167084+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-ecd974da6d7c68557cbf8f26'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T14:57:10.577011+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-567906e21bbdbec30a5a7068'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T14:57:17.581509+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-adbf810955b6c34e9746b878'}`
- Graph timings: `{"binding_graph": 0.042229883023537695, "challenge_graph": 0.0016239950200542808, "initial_generation": 78.05467038194183, "program_graph": 2.868034583167173, "repository_index": 5.716976986033842, "requirement_graph": 0.19673857500310987, "semantic_graph": 0.984465211047791}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `11`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-8b6cd946a4bcef421df2333e"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11019/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-11019`

### `django__django-11564`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T14:56:15.696536+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-e1dd945ed5351698cd745011'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T14:56:15.696594+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-7c54b57489c77d054cab151f'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T14:56:15.696640+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-a8f01df3995027f90d0cb635'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:01:19.076140+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-49892ad45504495f7ccadff1'} -> {'artifact_ids': [], 'event': 'mechanical_rollback', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:01:41.820584+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-530fed934095a569545e0506'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-08-05T15:02:02.828859+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-134b20903f52ae51fafbb770'}`
- Graph timings: `{"binding_graph": 0.0072365859523415565, "challenge_graph": 0.0006678190547972918, "initial_generation": 235.55743696191348, "program_graph": 2.0542070808587596, "repository_index": 5.8543747660005465, "requirement_graph": 2.5107870479114354, "semantic_graph": 0.32117185601964593}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `29`
- Transitions: `1`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11564/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-11564`

### `django__django-11742`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T14:56:25.954068+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-1cc67d982ed03ea497106c8f'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T14:56:25.954130+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-4fa51b474c5f64a4c59c1772'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T14:56:25.954180+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-e4159497da76616341ca386d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T14:59:32.662869+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-0d0f5f44d7d1c38df0d8c80f'} -> {'artifact_ids': [], 'event': 'mechanical_rollback', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:00:00.673449+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-125ebff862d7d0efafafae6a'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-08-05T15:00:06.136463+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-d83ab689b59c4bebf4044ea9'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 132.3770029109437, "program_graph": 2.3546828019898385, "repository_index": 5.43114348500967, "requirement_graph": 0.0004940310027450323, "semantic_graph": 0.002852309960871935}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `20`
- Transitions: `1`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11742/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-11742`

### `django__django-11905`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T14:58:03.896129+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-1d6ce419007054795ee17ed9'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T14:58:03.896187+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-48e875b2aaa22d0a9f5006c6'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T14:58:03.896249+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-32650fc8626c93816301b352'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:00:18.544541+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-6634578755f69c1b9878715a'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:00:55.411119+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-9ccf47d9612e84be4888ecd7'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:00:57.545769+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-2c37b842e241a4c7e5bf62d5'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:01:01.448394+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-d617e4bf9ebc57738ebc6f75'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:01:31.814991+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-d40a5d49cecdda51807fe8b2'}`
- Graph timings: `{"binding_graph": 0.08845627307891846, "challenge_graph": 0.002677005948498845, "initial_generation": 62.70253764500376, "program_graph": 3.0795166050083935, "repository_index": 5.451136604999192, "requirement_graph": 0.2575467270798981, "semantic_graph": 0.3900563250062987}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `17`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-a017ba420ee618c8fd9e2226"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11905/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-11905`

### `django__django-12308`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T14:58:10.717210+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-2b3b02bcb8065b784654d15e'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T14:58:10.717266+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-881c456ea33afa658037faa0'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T14:58:10.717312+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-e75e127117316e532c49726a'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:01:03.907451+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-0b19d6d28a8c416f9c45d6d2'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:01:23.549348+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-aac1dd08229aa8d58b4b0b96'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:01:24.433113+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-37181a7ff4ba7f08f1720961'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:01:28.775121+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-2f0a9f1934c589a5a40bbb36'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:02:27.202854+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-36c57a9bc282c0a463270e0f'}`
- Graph timings: `{"binding_graph": 0.005525170010514557, "challenge_graph": 0.0008477489463984966, "initial_generation": 95.21913147205487, "program_graph": 1.8715519228717312, "repository_index": 6.013048553955741, "requirement_graph": 0.15222356002777815, "semantic_graph": 0.012141723069362342}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `25`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-dcb1c2c576237f31ce39c498"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12308/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-12308`

### `django__django-12747`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:00:25.520968+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-1d1213274402099a25761d0c'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:00:25.521028+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-0ff7cf12a651b144f24bfef4'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:00:25.521074+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-f73a40a6b32762fbc83871de'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:02:06.362854+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-a3ca1cb27b7db30ceb89de3f'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:02:16.584234+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-48350655e65d433aa1a643e5'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:02:17.376260+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-c94a44f399d0610ad5dd0788'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:02:31.221300+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-ec722ee6b77e5bbe0dabc138'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:02:56.026455+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-044c41cca0a963cd24af5dee'}`
- Graph timings: `{"binding_graph": 0.0039292649598792195, "challenge_graph": 0.0006949090166017413, "initial_generation": 42.29093016800471, "program_graph": 2.014835662790574, "repository_index": 5.704420856083743, "requirement_graph": 0.058908773004077375, "semantic_graph": 0.003062017960473895}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `18`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-b1327e159b8b2c0cfb8393f3"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12747/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-12747`

### `django__django-12908`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:03:09.302280+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-98c3b82e2eb1453954aa5855'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:03:09.302331+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-bbfd63947f13294958a6abf7'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:03:09.302373+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-2933c14c85a15a7f3ac36a28'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:04:34.044153+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-a7383836b4d55d1a28c9882a'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:05:01.276492+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-5418662ab5f6b89c81123898'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:05:04.356593+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-f80e8309d647efb6e257aaee'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:05:08.072623+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-9b0961e58e1a4e551c4e03a6'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:05:15.562576+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-9774fe4b45e6921de2187961'}`
- Graph timings: `{"binding_graph": 0.10990459995809942, "challenge_graph": 0.0017672110116109252, "initial_generation": 40.17750603496097, "program_graph": 3.2446781271137297, "repository_index": 6.2676071169553325, "requirement_graph": 0.28285693388897926, "semantic_graph": 0.016351782018318772}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `20`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-56c472d3f6b45aef9c24e463"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12908/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-12908`

### `django__django-13220`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:03:09.515709+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-aeca3c1343c64a5c8dcc1912'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:03:09.515766+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-a6b23ed8f7b8ea76fd1fffee'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:03:09.515812+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-b35683f27e332e0cc909083d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:05:09.471818+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-47239c557b1eae757ae7ab5d'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:05:18.460012+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-63f16a8e07ee9b7550f49849'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:05:19.557170+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-3160fb8f3f31b9def528bab8'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:05:23.465369+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-6ccd25ab5b5b5791dab161f2'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:05:37.595297+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-7d9c9d90325270209bebc46a'}`
- Graph timings: `{"binding_graph": 0.005957875982858241, "challenge_graph": 0.0006272769533097744, "initial_generation": 64.83523916499689, "program_graph": 2.4383022860856727, "repository_index": 6.530865365988575, "requirement_graph": 0.10114040900953114, "semantic_graph": 0.01849854493048042}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `5`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-49d1ca58f8a34f6f9565dd11"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13220/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13220`

### `django__django-13265`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:03:09.406125+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-4b1591049679080aa4f4305a'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:03:09.406177+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-d587db8574b2bd75f5bb24a6'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:03:09.406218+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-9daed027d8c2dbd42bdb98c7'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:05:13.058985+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-0e698f57fc1f739ac117401a'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:05:41.838850+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-c5fbdea10d231639dfe9cb90'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:05:43.277958+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-a451ef50ec3f3117752056b1'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:05:48.331528+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-4fa3a3324dd7782c8f7ee460'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:06:08.546384+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-27ed48c057030d2d700c2214'}`
- Graph timings: `{"binding_graph": 0.038351272931322455, "challenge_graph": 0.0010228860192000866, "initial_generation": 85.67858377401717, "program_graph": 2.814267424866557, "repository_index": 6.176256614970043, "requirement_graph": 2.8262326589319855, "semantic_graph": 0.031066303956322372}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `29`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-e84f3a03beea03ebad008fd4"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13265/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13265`

### `django__django-13321`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:03:22.846647+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-24354a54d2414b8430f3617b'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:03:22.846756+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-6cf228c89137bc76e749f7e1'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:03:22.846826+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-54ccda9a95fcbb5600a8b252'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:08:16.866453+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-c0814e66fe611d5c11e9a769'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:09:34.886230+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-6e73ac3470fac123b1ba0141'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:09:35.904452+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-1be15dd2db474078602f128c'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:09:40.066346+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-aec2e7c28b6300238ea58cf1'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:09:46.773125+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-a7db25d53f06322191b24e54'}`
- Graph timings: `{"binding_graph": 0.024150584009476006, "challenge_graph": 0.0008314700098708272, "initial_generation": 261.22684279398527, "program_graph": 2.264097682898864, "repository_index": 6.081570517970249, "requirement_graph": 0.06828424998093396, "semantic_graph": 0.08669929695315659}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `15`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-a65fbf85dfc358866e5cf65a"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13321/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13321`

### `django__django-13448`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:05:51.418210+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-f3cb4df62c92e8edeade5706'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:05:51.418273+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-41167b74054ab552c888ae11'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:05:51.418325+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-3498d0dd0df67e69e1c56d6d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:08:18.298390+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-53e69bc098e3df903e324cbd'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:08:28.221165+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-8589af701d77f2a4644982c6'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:08:29.457756+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-69009d005498705366142324'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:08:36.921583+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-c562eba2cd767faa8f6c0b62'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:09:14.053853+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-8c61133c9a88c8c886b62b7c'}`
- Graph timings: `{"binding_graph": 0.01693054591305554, "challenge_graph": 0.0006684541003778577, "initial_generation": 54.471390971913934, "program_graph": 3.4071135381236672, "repository_index": 6.347806622972712, "requirement_graph": 0.2885750540299341, "semantic_graph": 0.08476805407553911}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `5`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-9e8debcfbcb813905e1b3d35"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13448/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13448`

### `django__django-13660`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:06:06.517633+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-42e7d6adbdde81a67d7faee0'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:06:06.517700+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-94c88f7b2bdd9aa0f9e1aa1b'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:06:06.517751+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-a4cc0d9ab72e5708badf7455'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:07:12.186485+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-ba98a801f5d5de78755c68cd'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:07:38.603138+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-e420816210d790d0037dda4c'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:07:39.463268+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-658afa98d312b7bec56930d6'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:07:43.233756+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-9d04c3f89f2bcd073f75d710'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:07:55.316392+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-16c2020e75bd7b2eee055c34'}`
- Graph timings: `{"binding_graph": 0.021907032933086157, "challenge_graph": 0.0003475039266049862, "initial_generation": 9.63441093498841, "program_graph": 1.8189272860763595, "repository_index": 6.973271421040408, "requirement_graph": 0.14374583796598017, "semantic_graph": 0.017554685939103365}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `4`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-50811bc58d9a4a5d07586391"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13660/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13660`

### `django__django-13768`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:06:58.255392+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-84c675553a3bbaae9bd093aa'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:06:58.255452+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-5b7d1a0f78b1544af9a73918'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:06:58.255498+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-f52ab3f542f7ebf65af07630'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:08:15.280774+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-5edd8ad1b142a8b7e172ac05'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:09:32.739180+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-9bd4278f53b52d67aa520d91'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:09:33.790107+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-43883f6c5f1e22aaa1c0ff53'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:09:37.602102+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-3911d22e7cbe4576f9d7ba94'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:09:44.768396+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-125d25516b89e993d955f06b'}`
- Graph timings: `{"binding_graph": 0.018609474995173514, "challenge_graph": 0.001726721995510161, "initial_generation": 36.51657756394707, "program_graph": 1.8352329201297835, "repository_index": 6.444510958041064, "requirement_graph": 0.08686747902538627, "semantic_graph": 0.026049928041175008}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `21`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-b90d611abf17feceba52e188"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13768/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13768`

### `django__django-13925`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:08:21.823668+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-fda40414b93e46438fb9ffab'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:08:21.823723+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-e330c6cdc6bc9b30a2468c0b'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:08:21.823766+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-aa8c25eb0078afb04f69c334'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:11:26.153245+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-9ff0a03b5f08d4cf30b266d8'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:11:59.644417+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-4e82fbf5dfba3c1ea400fd05'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:12:01.686961+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-5aebe32d73096bfad006c988'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:12:06.821507+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-53c9a6984f2d1b5c2c04f817'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:12:39.594222+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-12c48ab39da1e9c83031bfe9'}`
- Graph timings: `{"binding_graph": 0.09105234395246953, "challenge_graph": 0.002501609968021512, "initial_generation": 72.65507103293203, "program_graph": 3.444863806129433, "repository_index": 6.101656469982117, "requirement_graph": 0.41555106500163674, "semantic_graph": 0.5184250810416415}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `16`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-9c854eefa5ad03e86d31f9a1"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13925/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13925`

### `django__django-13964`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:09:42.053310+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-25bef59e84ac4a1c25819d54'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:09:42.053373+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-05b77dcfeacb88a2bb80d1df'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:09:42.053421+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-b9f96c8d08dae069aae489bf'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:11:29.503288+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-a0aa61a951cd8910afff6080'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:12:06.456802+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-eb4245099ce852b3d52af355'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:12:07.823211+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-58ba3b3e926a68080ee24125'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:12:11.614787+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-72f474801d9d19673d14d075'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:12:34.611209+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-5acf2651ce80c8288a4a8c73'}`
- Graph timings: `{"binding_graph": 0.030872620991431177, "challenge_graph": 0.0006809810874983668, "initial_generation": 50.55356859101448, "program_graph": 2.2050613248720765, "repository_index": 6.673026800039224, "requirement_graph": 0.15142710006330162, "semantic_graph": 0.04687472293153405}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `20`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-5479f414de9bd4c03ec9de2c"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13964/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13964`

### `django__django-14017`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:30:29.607209+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-8ddb9b86975548d2ad95c9b4'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:30:29.607263+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-85661f92f7c0becaf031a3f2'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:30:29.607307+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-b6f5a1d7853fc7a84da90612'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:32:04.350929+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-ab2f1b5ee10c48b241112378'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:32:36.515120+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-11477fa5892fb761202c7cbf'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:32:37.817817+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-11d634e78dc421e4d40882af'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:32:41.837685+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-9697de0a69517d3205b2ec81'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:32:49.077225+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-22093ebe53ff170e31acf03b'}`
- Graph timings: `{"binding_graph": 0.028252379037439823, "challenge_graph": 0.0007543000392615795, "initial_generation": 58.42144771409221, "program_graph": 1.808207044028677, "repository_index": 6.464253564947285, "requirement_graph": 0.14357960398774594, "semantic_graph": 0.005596703034825623}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `8`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-e293705de0c4c959aed32062"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14017/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14017`

### `django__django-14155`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:10:40.513614+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-af6289db4ce7c0ab96aa3080'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:10:40.513687+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-137a267ffd9611dc690b6415'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:10:40.513738+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-2ed368fa2a83c6bb7e65e5ec'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:12:04.160978+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-d145da1e95f48ae8496f8423'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:12:45.071248+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-d0f7ecede29876a6d792d2b8'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:12:46.727933+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-ab49d29dcdc0b90505463f34'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:12:53.225071+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-62a34c7e803f518a1e7d7762'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:14:11.300107+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-1c5dc8edda172a06f3002d1c'}`
- Graph timings: `{"binding_graph": 0.01311967906076461, "challenge_graph": 0.0003685660194605589, "initial_generation": 42.27734089503065, "program_graph": 3.2798461047932506, "repository_index": 6.551814057980664, "requirement_graph": 0.2590597129892558, "semantic_graph": 0.006476148962974548}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `18`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-e793c005e89d1e311d35039d"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14155/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14155`

### `django__django-14534`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:10:39.653698+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-6d4185440a34173e5f6c4893'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:10:39.653756+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-83f52adee627fbc8f859450c'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:10:39.653802+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-292d8b5ef4817fc308ce2a3d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:12:57.899019+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-840adb51b2da4dbdadb8ba14'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:13:22.924303+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-0c7d8e5cfc302be8df595436'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:13:24.764707+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-906175f475b7ef5f8063fac1'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:13:49.453000+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-6bf62c35da771d873f178f3a'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:14:29.623006+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-43672b25b9d2694efec07169'}`
- Graph timings: `{"binding_graph": 0.010804646066389978, "challenge_graph": 0.0007855350850149989, "initial_generation": 72.01780388399493, "program_graph": 2.9496042439714074, "repository_index": 6.198032600921579, "requirement_graph": 0.25090016098693013, "semantic_graph": 0.058957890956662595}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `12`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-b4731c4b35abac7b79ee3136"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14534/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14534`

### `django__django-14667`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:13:02.750684+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-4ae8f1dd825b0d45f7013b83'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:13:02.750755+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-73f0d5d1603066cddedacece'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:13:02.750811+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-ac4ac38f314395296b8957a6'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:19:58.926392+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-3c344d6a94075fa2aae873c5'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:20:26.386497+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-0b66db0a119953bc2f7390e2'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:20:28.133063+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-3b74b5bf75b15b0bb487c607'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:20:32.161673+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-8172d143e92adb7907efe612'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:21:11.065625+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-ee3fb23221be28bcae9a518a'}`
- Graph timings: `{"binding_graph": 0.051251811906695366, "challenge_graph": 0.003665463998913765, "initial_generation": 267.09208430792205, "program_graph": 3.901263466104865, "repository_index": 7.068411372019909, "requirement_graph": 2.8273156989598647, "semantic_graph": 0.2051076190546155}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `33`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-ba21918a2d481929f0fe4004"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14667/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14667`

### `django__django-14730`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:44:42.354686+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-a62492e22dd0a29348650855'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:44:42.354780+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-dfce6242ce2aeaa25e3414e8'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:44:42.354843+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-037c484c6706b1a4c9f4d4c0'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:46:08.457742+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-6b765c9d1119da2c5499343a'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:46:41.456249+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-5d289f5f36d10df7f798799b'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:46:43.621344+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-42b35e64c91b943832da49c4'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:46:47.718848+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-64423325132fd7038d5416f5'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:46:54.984581+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-c16f0fa575de06ddfafee6ef'}`
- Graph timings: `{"binding_graph": 0.030311706941574812, "challenge_graph": 0.0006791830528527498, "initial_generation": 48.12307227693964, "program_graph": 3.3663030547322705, "repository_index": 5.9113143580034375, "requirement_graph": 0.19220420194324106, "semantic_graph": 0.034762872965075076}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `7`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-cb1e5bf229b9aae0e812500b"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14730/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14730`

### `django__django-14997`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:14:53.125031+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-d1d531e8303e1cc4fd717151'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:14:53.125098+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-d32db91933998d07a9352533'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:14:53.125144+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-96df71061a8880f04eef07d2'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:16:27.396521+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-aa020e1ff5ad54d5f4c0eb92'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:16:37.891053+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-68fa7e0a0d9f982ddd1413c7'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:16:39.014331+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-cb8cc818ed0db91e9f35edee'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:16:42.678429+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-ea3ac9da3736f4e8166bcc9b'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:16:51.688119+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-093889516297de028fb89701'}`
- Graph timings: `{"binding_graph": 0.009110523969866335, "challenge_graph": 0.0007686299504712224, "initial_generation": 36.614828255958855, "program_graph": 2.1124369270401075, "repository_index": 6.645970460027456, "requirement_graph": 0.15109771396964788, "semantic_graph": 0.008334298967383802}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `10`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-7e02c317dc4c8e1d891e199a"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14997/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14997`

### `django__django-15061`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:14:57.155054+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-5c97bd009b29b84711b61bf2'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:14:57.155107+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-8aa1a7aaa0959a21ade58499'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:14:57.155148+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-4d09c5c98b9a8669b01dfded'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:16:08.356326+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-045483423f177a7fae492ce6'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:16:32.391721+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-d3213e4fa7ca81e2d836e2b2'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:16:34.263075+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-724aa10e0aeeaf1d72196f39'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:16:38.059825+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-8409d366191460cdfa0191e8'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:16:45.859030+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-5d73c3cdac23d65ff7c8a153'}`
- Graph timings: `{"binding_graph": 0.051478111068718135, "challenge_graph": 0.0007835249416530132, "initial_generation": 35.370473736082204, "program_graph": 3.7442207388812676, "repository_index": 7.124060684000142, "requirement_graph": 0.20511619304306805, "semantic_graph": 0.016152014955878258}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `20`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-91acc4b0b8b5700974bf3133"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15061/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15061`

### `django__django-15202`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:17:13.891232+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-de6af873bb0587d3b031cc59'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:17:13.891295+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-72ef362ed4817f81d887f183'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:17:13.891340+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-0b66dfcbb5687be075ca7b22'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:19:04.263439+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-d877634e2ee5cac023a2e6b7'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:19:34.086986+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-031ccc6b246de796169b9a46'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:19:35.090131+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-a7a1e62d606c1da9c05789f4'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:19:39.132334+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-be9c563afec47cdd7b45cfc0'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:19:55.325720+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-6707df0fdef0a6fd7dd7bc82'}`
- Graph timings: `{"binding_graph": 0.029386878944933414, "challenge_graph": 0.000724851037375629, "initial_generation": 75.90079069300555, "program_graph": 2.0435762740671635, "repository_index": 6.652757618925534, "requirement_graph": 0.13347196800168604, "semantic_graph": 0.002026248024776578}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `10`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-0f4dbc23aa5ee3fdeaa2d4db"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15202/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15202`

### `django__django-15252`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:17:15.136032+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-50a6fbc24c48185f810c6de7'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:17:15.136083+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-c7e91e2376effbcdde3046b5'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:17:15.136125+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-61412773f88df7ece6218159'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:21:55.676755+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-455d684ccc4c8853208166d9'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:22:18.205044+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-92ab32ba9fc37c6b7bf04ea8'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:22:19.855530+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-3b8cafe3082556e5adcc307d'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:22:23.860983+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-288a9c6deb53a7b851675b69'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:22:34.479190+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-af69bf4e4532324fce963d13'}`
- Graph timings: `{"binding_graph": 0.1655138770584017, "challenge_graph": 0.001638429006561637, "initial_generation": 218.80546105699614, "program_graph": 3.3365634938236326, "repository_index": 7.131918946048245, "requirement_graph": 2.6446997129824013, "semantic_graph": 0.774090314982459}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `29`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-0c944c89ebfc9712054c65ec"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15252/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15252`

### `django__django-15320`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:18:08.887981+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-cd330c6d25c91ba6733ab190'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:18:08.888035+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-daca438e1874b08c0f9abd86'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:18:08.888078+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-1b25847adcf6f2390d86e2fc'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:20:04.824750+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-572a12ab9eb6255c594fa4f7'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:21:25.257579+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-74bef5bae4395d1722d04a5d'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:21:26.829190+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-311b2aca99833cab0b71b13b'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:21:30.629769+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-1323e2e5fd4aeffbda5cde3e'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:21:37.519218+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-e97417108a48004d351fcb8b'}`
- Graph timings: `{"binding_graph": 0.01384718797635287, "challenge_graph": 0.000418263953179121, "initial_generation": 75.6412386019947, "program_graph": 2.288216104847379, "repository_index": 6.778565490967594, "requirement_graph": 0.14026831707451493, "semantic_graph": 0.017547623021528125}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `22`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-07c9badf49328fba38582003"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15320/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15320`

### `django__django-15400`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:20:32.670009+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-59a36eb2440d90054cad7e7e'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:20:32.670063+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-38af9bd3aa16401d25749019'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:20:32.670106+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-c5b9e614baa3d8cf0560ec47'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:22:32.563177+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-1eb95a65f08261a2d9dc1743'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:22:42.297311+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-636b18fb0a9a82f33995a586'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:22:43.621867+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-348574729b9e1745ae9c45c8'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:22:49.961338+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-57926f7d8d32ec97c4125250'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:23:16.148042+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-9826754b07102f8d4097b55f'}`
- Graph timings: `{"binding_graph": 0.018180255079641938, "challenge_graph": 0.0010697579709812999, "initial_generation": 29.931418251944706, "program_graph": 3.470493695815094, "repository_index": 6.5467481239466, "requirement_graph": 0.2189724480267614, "semantic_graph": 0.03826663305517286}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `14`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-819d8afeb4d536efe6fe5ef2"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15400/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15400`

### `django__django-15695`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:21:37.892683+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-9ffbef4137fd5624c93e7ec3'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:21:37.892745+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-65d6ecafed25718a391eb370'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:21:37.892810+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-9e871bed6b9a4825fa04c671'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:24:29.556060+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-cca92e37b212e5cac6d10250'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:24:42.469205+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-9fcf70aafc4f43dc47405ebc'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:24:44.231469+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-41062d7ca4d569478cbc7e8f'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:24:48.141013+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-2029b8eef50cf46c7b06cfce'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:24:55.949779+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-3c44916780ec35006a2828bb'}`
- Graph timings: `{"binding_graph": 0.01648958504665643, "challenge_graph": 0.0014962200075387955, "initial_generation": 108.60879297100473, "program_graph": 4.664066567202099, "repository_index": 6.7496241088956594, "requirement_graph": 2.8890585000626743, "semantic_graph": 0.06039834697730839}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `37`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-9d4fab8eb614365b06799442"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15695/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15695`

### `django__django-15738`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:22:25.489642+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-a8acf4f2f221ec2ad583f91f'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:22:25.489727+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-361bf31d13cd9c0dd9697cca'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:22:25.489782+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-5b346b7c225532f62654e53f'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:26:05.212203+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-11d10d7784f7c5d5a5fc5c79'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:26:34.094072+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-0466e3d4ac77e18919780c4f'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:26:35.568139+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-c565a4f49b6e9a0e5364fb01'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:26:39.732992+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-02dd860201f490db7e3089c9'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:26:54.047486+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-b3595fec98bef656a7ea240d'}`
- Graph timings: `{"binding_graph": 0.13777680695056915, "challenge_graph": 0.003897513961419463, "initial_generation": 158.7865590340225, "program_graph": 2.82654872816056, "repository_index": 6.681138699059375, "requirement_graph": 2.969589699059725, "semantic_graph": 0.04250700306147337}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `56`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-75bd44ebf6c300eeaef608eb"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15738/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15738`

### `django__django-15781`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:23:30.671741+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-f02b7b69bac40ff45d65eb93'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:23:30.671796+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-ac6a03311fbba289988f7574'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:23:30.671839+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-4f7f30dcdf2d97836b21c11e'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:24:40.197775+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-e4ac823704a0daa06c88f238'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:24:50.539134+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-d90db4e410ab71fee3d6fdd5'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:24:51.261549+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-db54d414f0a2203f37c3a75b'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:24:55.233801+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-42813ebcc4762302afc48610'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:25:05.704291+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-b1b6894c8c0b619c33fe7cc4'}`
- Graph timings: `{"binding_graph": 0.0037496600998565555, "challenge_graph": 0.0006348289316520095, "initial_generation": 15.725396994967014, "program_graph": 1.110582409077324, "repository_index": 6.761579383048229, "requirement_graph": 0.05978304997552186, "semantic_graph": 0.08575491106603295}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `8`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-11d05d3b99faaa64c21826cf"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15781/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15781`

### `django__django-15819`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:32:46.715449+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-444fa6bcd9581efe75d314d1'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:32:46.715505+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-8c151ae4e692d1fb11f06d60'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:32:46.715548+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-e4fa419ffd7e1ec01052c49d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:35:03.352185+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-b24fe3d3b8a4b95aad4be5ea'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:35:26.773126+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-2b4e24983ebe1b002831cf67'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:35:27.977047+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-83b068ad27f4c53ba7a7e2c0'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:35:32.137699+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-38ee0b64b020b79015551187'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:35:41.689162+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-cbab683b8614b579cdf223a3'}`
- Graph timings: `{"binding_graph": 0.032817456987686455, "challenge_graph": 0.0006619329797104001, "initial_generation": 89.44207980297506, "program_graph": 2.16601430112496, "repository_index": 7.046077664010227, "requirement_graph": 0.12745401193387806, "semantic_graph": 0.013161127921193838}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `15`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-a6b84b7b936f9ed5f92c6f3f"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15819/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15819`

### `matplotlib__matplotlib-18869`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:34:23.967977+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-0ebcb0818e4d3c562db413ca'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:34:23.968029+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-6c6365b5310e6f3560dcdc94'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:34:23.968069+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-a9b71951a04cd80a6d5a62fa'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:36:00.427239+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-d8edf1b349ea485553d2347e'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:36:35.662451+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-69a8827b537523195cbd1c5b'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:36:37.187880+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-91491d6713f992779c3d1f9f'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:36:39.486798+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-f95b0157dc8f0366a04e0cd1'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:36:44.361963+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-706144682473a971f4becd43'}`
- Graph timings: `{"binding_graph": 0.02332353696692735, "challenge_graph": 0.0011259979801252484, "initial_generation": 53.365228712093085, "program_graph": 2.0960210588527843, "repository_index": 2.580674451077357, "requirement_graph": 0.2085115199442953, "semantic_graph": 0.172663125093095}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `7`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-4afcce18cff9538a1a689310"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/matplotlib__matplotlib-18869/final_patch.diff`
- Full structured process: `case_process_report.json` entry `matplotlib__matplotlib-18869`

### `psf__requests-2148`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:23:22.105245+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-af4839edb4c83ac3f9580853'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:23:22.105300+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-3ab77ac1945aeb629a9be86c'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:23:22.105343+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-40d8a890c7bf267ab0ff5315'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:24:48.204545+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-c08fe2031ce372840f277dd3'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:24:50.013428+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-f6166a295d390f0b89bfaece'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:24:50.423564+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-0108ec716af53a27409cbb66'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:24:50.528489+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-103f7f08ba9ee28bd5773a91'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:24:53.785184+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-935e327137061d4ae4ff4818'}`
- Graph timings: `{"binding_graph": 0.00433051201980561, "challenge_graph": 0.000673139002174139, "initial_generation": 36.970407909015194, "program_graph": 1.3633353059412912, "repository_index": 0.2771334759891033, "requirement_graph": 0.05323367298115045, "semantic_graph": 0.015880316961556673}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `22`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-cfc2cf09be1d643ce3999b33"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/psf__requests-2148/final_patch.diff`
- Full structured process: `case_process_report.json` entry `psf__requests-2148`

### `pytest-dev__pytest-5413`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:25:08.136590+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-09bc4e3131a59b9c64f7961d'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:25:08.136644+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-f3712eef79131393ca24940b'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:25:08.136686+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-bf7f4200fdcc3d777654550b'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:26:21.128058+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-e95e246e12e9c9c67dff2316'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:26:34.102355+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-444954f0961bc6afea95ac8a'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:26:35.689678+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-fd009da39ba11e7e62f5de21'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:26:35.987307+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-827351d1aa03ba998b96dc86'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:26:43.552426+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-61436e181076e1e788202d5f'}`
- Graph timings: `{"binding_graph": 0.022721941000781953, "challenge_graph": 0.0015545040369033813, "initial_generation": 37.094452394056134, "program_graph": 3.8565178689314052, "repository_index": 0.6898735070135444, "requirement_graph": 0.20555293909274042, "semantic_graph": 0.1851871469989419}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `17`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-1d7a6a7cf12d42ffc2e291db"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5413/final_patch.diff`
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-5413`

### `pytest-dev__pytest-5692`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:25:07.908021+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-8cb5be083f9651b170f14b45'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:25:07.908082+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-ede13f4f752c7051bee7a2fe'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:25:07.908121+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-482877f8a739ac556acf0a9f'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:26:16.944370+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-cf82ba84f5b0cc2d4a86641f'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:26:26.759446+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-eeac296c1ffa4e56065709e5'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:26:28.291055+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-b2eb74adc1e373286f6d48e6'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:26:28.568970+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-66e472847641a73e13c6bf0d'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:26:35.227145+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-ebad20203fd138ac2cade1d2'}`
- Graph timings: `{"binding_graph": 0.03208488610107452, "challenge_graph": 0.0016171600436791778, "initial_generation": 39.46695845096838, "program_graph": 3.9027449981076643, "repository_index": 0.6225738789653406, "requirement_graph": 0.1840124250156805, "semantic_graph": 0.012506130035035312}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `21`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-ef317dc8d44e231675c948bc"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5692/final_patch.diff`
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-5692`

### `pytest-dev__pytest-7220`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:25:17.895185+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-2b2ae09ea7709de4cf40902a'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:25:17.895238+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-d5f098266388881b4699289a'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:25:17.895279+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-50f9020d254752bab63d9f35'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:27:01.849041+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-3252cc9b19b6d9edba735faf'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:27:10.017694+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-717cf0c7791f3e7916d3c557'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:27:10.997815+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-c2587ebaef41195927d342cf'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:27:11.288354+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-03ee62b09970544208481873'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:27:17.439567+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-c3166cbdf79366403d5315c7'}`
- Graph timings: `{"binding_graph": 0.047400156036019325, "challenge_graph": 0.0017070549074560404, "initial_generation": 64.8962644119747, "program_graph": 3.0869170849910006, "repository_index": 0.7226206430932507, "requirement_graph": 0.6280179331079125, "semantic_graph": 0.021246520103886724}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `34`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-d90ccd46c471ebcc664d1a9e"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-7220/final_patch.diff`
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-7220`

### `scikit-learn__scikit-learn-11040`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:27:11.596168+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-12492ad4638ad29ac6cfb803'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:27:11.596227+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-b9925151cdb5ba120e29dfe4'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:27:11.596274+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-e6e4764698005ff2852623aa'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:29:33.511834+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-27fde1a558ef1e41b3e1bd02'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:29:55.295297+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-8cb969673999240b0d50d336'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:29:57.245404+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-bfad58f8d188aad2537e1fcf'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:29:57.944181+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-39ce4d87a65598b059f31826'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:30:06.037122+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-652270769b184629e4493fdf'}`
- Graph timings: `{"binding_graph": 0.07217663899064064, "challenge_graph": 0.0009339459938928485, "initial_generation": 86.01999730698299, "program_graph": 5.796864502946846, "repository_index": 2.5146130949724466, "requirement_graph": 0.40031775494571775, "semantic_graph": 0.15189537801779807}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `16`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-9bfe2618d2a05e54d9ce1037"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/scikit-learn__scikit-learn-11040/final_patch.diff`
- Full structured process: `case_process_report.json` entry `scikit-learn__scikit-learn-11040`

### `scikit-learn__scikit-learn-14092`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:30:30.551914+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-7f218bcf57cc73103dcd6e84'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:30:30.551980+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-1fba2064d76dab9df2f21056'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:30:30.552024+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-99aaef44a89d45175c67edd1'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:33:29.995124+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-b003d082018ca76d6ade1cc3'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:34:01.851203+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-705f7e30f4b06fd44b9f737e'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:34:03.238707+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-36b7c5b9fe2291930fde498b'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:34:04.031984+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-00f5e7e2be1e7c6e442a8c5a'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:34:08.898614+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-4457de9589317445a1301f5d'}`
- Graph timings: `{"binding_graph": 0.3524967550765723, "challenge_graph": 0.0022758219856768847, "initial_generation": 136.82092002604622, "program_graph": 5.466433206107467, "repository_index": 2.6272518389159814, "requirement_graph": 0.8552702000597492, "semantic_graph": 0.3923402330838144}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `39`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-56cf1681053c5ceb1e344461"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/scikit-learn__scikit-learn-14092/final_patch.diff`
- Full structured process: `case_process_report.json` entry `scikit-learn__scikit-learn-14092`

### `sphinx-doc__sphinx-8282`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:26:49.259367+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-26d432d55c937b3897d28d61'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:26:49.259427+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-b4dfce4ef90aff6673a275af'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:26:49.259475+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-99a9a413d5cc9a9c0d361027'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:28:24.366590+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-fc5fe5a6fcb5a8aba5bc4be9'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:28:59.875381+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-1d39f2071be0ca43b64bcf65'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:29:01.425710+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-e9f007816f8cb5c6ca55cff9'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:29:02.397960+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-8d565e3b9d3b5c4d7246430e'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:29:18.598595+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-4d897195273144215f1b65b5'}`
- Graph timings: `{"binding_graph": 0.013268506969325244, "challenge_graph": 0.0006769669707864523, "initial_generation": 46.42889164807275, "program_graph": 1.8544505318859592, "repository_index": 1.1561226269695908, "requirement_graph": 0.15433203696738929, "semantic_graph": 0.03784208500292152}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `21`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-67be854fed45b4f487bd70c4"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8282/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sphinx-doc__sphinx-8282`

### `sphinx-doc__sphinx-8721`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:26:57.158530+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-94727984bec5f4855140752f'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:26:57.158585+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-27d3487f75a971fd68e30ca7'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:26:57.158628+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-a22cb7da575af9b5acd4156d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:28:08.685050+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-f93d94be8b9fa71b91ebeddd'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:28:30.008258+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-0d53bec8351e31bff13ab401'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:28:31.343730+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-b5362de0fb8893f5b86cc083'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:28:32.241928+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-2c4e113670301af0a488f9b5'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:28:37.785089+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-c26c64fc2689343f046c7114'}`
- Graph timings: `{"binding_graph": 0.016697590006515384, "challenge_graph": 0.0010210869368165731, "initial_generation": 35.0075266220374, "program_graph": 2.962442471063696, "repository_index": 1.27477395394817, "requirement_graph": 0.2208781159715727, "semantic_graph": 0.02886735601350665}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `19`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-59c6205616f595c29dae273d"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8721/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sphinx-doc__sphinx-8721`

### `sympy__sympy-11870`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:27:45.619449+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-385130e9fd6cef64e23d7f65'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:27:45.619505+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-e2164ddfbed774486465d487'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:27:45.619549+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-43036bb6d815bb7bf27b1d4d'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:29:44.178275+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-70ccce4da6e55a86dbbda6d6'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:29:53.975058+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-2f11477d3839e21ba0564ae5'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:29:56.125618+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-6e9585e1c94525435199fd48'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:29:56.959088+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-1825ebe968a4c90aad08e998'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:30:05.540081+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-cb1388115475a9adbe6fae9a'}`
- Graph timings: `{"binding_graph": 0.010727850953117013, "challenge_graph": 0.0007584440754726529, "initial_generation": 62.855581539915875, "program_graph": 5.356011745985597, "repository_index": 12.140335403033532, "requirement_graph": 0.24189798487350345, "semantic_graph": 0.057263790047727525}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `20`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-531a7abeb1011b339a3b35a7"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-11870/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-11870`

### `sympy__sympy-12454`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:28:58.504970+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-8bc5a07f760be45097ee40be'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:28:58.505026+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-ea1e3624cf001a4ea1e5dc5a'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:28:58.505070+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-3f1f531dd7b415e0526651fb'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:30:47.778123+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-af962460b855531ed0a0dbae'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:31:33.519612+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-994c2aa4693b9d80895f8043'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:31:35.786870+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-b03618bce99a7630be4a3de7'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:31:36.738154+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-7234e7909fcfa6dfe0586b3e'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:31:44.074651+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-cb9c4bff00a08c0931c1eb8c'}`
- Graph timings: `{"binding_graph": 0.01319947000592947, "challenge_graph": 0.0007289669010788202, "initial_generation": 45.229907382978126, "program_graph": 5.067787157953717, "repository_index": 7.103567699086852, "requirement_graph": 0.24890606419648975, "semantic_graph": 0.040172533015720546}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `19`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-5da973361308c58a1eb0ddc1"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-12454/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-12454`

### `sympy__sympy-13437`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:30:03.091874+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-667350f4ff102637d94a08a0'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:30:03.091929+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-ba2f72630d7f07c52602d0ce'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:30:03.091971+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-cb5775121a632d5999abbd62'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:31:54.243012+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-0bb07043f99fb2aee9abd592'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:32:14.791189+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-27883c3a504c2e76deb07aee'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:32:16.358498+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-53c7200a3d5bf2b0ac183aa2'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:32:17.266929+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-5a2015366af58191cf770594'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:32:24.230360+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-7324492769915cf099ee1bde'}`
- Graph timings: `{"binding_graph": 0.0456906920298934, "challenge_graph": 0.0014486690051853657, "initial_generation": 51.35110127006192, "program_graph": 3.8140869467752054, "repository_index": 12.874266176950186, "requirement_graph": 0.19383933895733207, "semantic_graph": 0.0034616170451045036}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `20`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-aff9913637567357bcbe4676"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-13437/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-13437`

### `sympy__sympy-18199`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:33:25.109353+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-238f79dc8660a8e03d67daad'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:33:25.109402+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-28d39faaa7789304cd6133b0'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:33:25.109439+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-5489087f403801259f93d1ef'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:34:45.799085+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-18443c476d07ebe1750e2c98'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:35:08.777241+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-20e04c98dc754081d641da05'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:35:12.313591+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-a36396ac96e21d53183cfb58'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:35:13.466335+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-1605d33609c914871d7cbf7a'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:35:24.483596+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-6b778e49ae163f78a718bbb8'}`
- Graph timings: `{"binding_graph": 0.10033643001224846, "challenge_graph": 0.0010942419758066535, "initial_generation": 40.27478817803785, "program_graph": 5.36591650522314, "repository_index": 19.153777037048712, "requirement_graph": 0.4443746708566323, "semantic_graph": 0.01144667703192681}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `16`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-015efa54ac6c3e7fe220254b"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-18199/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-18199`

### `sympy__sympy-18835`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:34:27.444397+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-2aa15cadcce4c3770052948a'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:34:27.444448+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-a43c2084f557f6cf0e3537c7'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:34:27.444490+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-176cba5b7abd9eba5ddde17f'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:36:36.069728+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-ac70079fe8c7584bbf20174b'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:37:08.699896+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-d6dd436e65b7f4e57a19864d'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:37:10.448288+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-4b3fc8bf7c8345a502a5d70a'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:37:11.461976+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-e3588e5ecc9bedf0c3f8198d'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:37:19.403179+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-1d8019a56c3985178bca902b'}`
- Graph timings: `{"binding_graph": 0.054583007004112005, "challenge_graph": 0.00100009108427912, "initial_generation": 81.82123139197938, "program_graph": 3.3791593487840146, "repository_index": 19.10243274597451, "requirement_graph": 0.276624173973687, "semantic_graph": 0.02988348703365773}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `13`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-ecb3223b96ec8b0ffbccd875"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-18835/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-18835`

### `sympy__sympy-20049`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:35:59.417414+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-157ad1a638903d685b18b372'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:35:59.417467+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-3f66967f3fc603a19c168fa6'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:35:59.417510+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-d763498ba645fc008f6d3c7a'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:37:58.395942+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-b7ed204e529039e239319dd9'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:38:12.953998+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-b2988659bd443b8d6fce47d5'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:38:17.029476+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-2ab8b7744d6201fb99c696d2'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:38:18.092218+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-47948b516c2fd46a06c814e0'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:38:36.779340+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-d04d5e3faa7ca7047c54d63e'}`
- Graph timings: `{"binding_graph": 0.16538503905758262, "challenge_graph": 0.0036878170212730765, "initial_generation": 69.23888700106181, "program_graph": 5.660743931075558, "repository_index": 19.550382202025503, "requirement_graph": 0.895976830041036, "semantic_graph": 0.11039952305145562}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `10`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-cf5a534699db3a81e50ef2c3"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-20049/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-20049`

### `sympy__sympy-21171`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:36:14.710016+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-a11b57c7659c975d43138a34'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:36:14.710071+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-1540bf8123e15661380815fa'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:36:14.710113+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-31001b508732cd5492f0f0ac'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:37:35.817547+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-b49b08dc323cf5e046ca5d1c'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:38:35.408444+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-886f84a09d96c5b5b6a1f1dc'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:38:38.490771+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-10d298a937864926123394fd'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:38:39.873225+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-45c415c972b574b61086fc31'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:38:56.649993+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-17e911dfff5fa3045f57c46d'}`
- Graph timings: `{"binding_graph": 0.0629973360337317, "challenge_graph": 0.0006819450063630939, "initial_generation": 42.33653235703241, "program_graph": 3.8324298869119957, "repository_index": 20.880467887036502, "requirement_graph": 0.30302061210386455, "semantic_graph": 0.057828968041576445}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `19`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-e677c79a07e1c35db7256d24"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-21171/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-21171`

### `sympy__sympy-22005`

- Generation/Harness: `EVIDENCE_LIMITED_COMPLETE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-08-05T15:37:16.452750+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-eb6c117d297a133a59dc5ffc'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-08-05T15:37:16.452814+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-1f7a38360aa6cbb9b4b137d2'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-08-05T15:37:16.452863+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-f42d5664407ee8fc364363f0'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-08-05T15:39:47.827670+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-23ac6353f9b5cd6228596065'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:40:17.629906+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-6f171604ed95c9d0461b4129'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:40:19.497369+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-eb2621845bff6665baf1ccf8'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:40:20.589726+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-c4332225f646024624f15283'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:40:28.165503+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-0da48c6e27400a6baecbb42b'} -> {'artifact_ids': [], 'event': 'confirmed_counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-08-05T15:40:28.165562+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-403b7a9e820af9332c5c2095'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-08-05T15:41:28.270143+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-4c94bcb8bda906419fe58b6c'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-08-05T15:43:11.545102+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-0df395a4eef8512179a73d1e'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-08-05T15:43:13.377710+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-2efd67e910b5a1c0a0eacc95'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-08-05T15:43:14.550535+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-f89dbfa230a766c631f9cfbe'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-08-05T15:43:22.440205+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-65c00add1a8d4637b1cfac1c'}`
- Graph timings: `{"binding_graph": 0.018365628900937736, "challenge_graph": 0.002099130884744227, "initial_generation": 104.85243961494416, "program_graph": 5.470624798792414, "repository_index": 19.951301531982608, "requirement_graph": 0.30706252611707896, "semantic_graph": 0.028282444924116135}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `28`
- Transitions: `2`; accepted `2`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-fb2d5bec1a53020528194c10"}, {"edit_ids": [], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-8a266423f9dab036eafcad62"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-22005/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-22005`

