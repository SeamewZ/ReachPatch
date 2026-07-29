# SWE51 Case Process Report

- Cases observed: `51`
- Every row records generation phases, all five graph timings, DeepSeek calls, transitions, component outcomes, and isolated harness results.

| Case | Generation | Harness | Semantic | Index | Requirement | Program | Binding | Challenge | Initial patch | Commit/Rollback |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `astropy__astropy-14182` | `GENERATOR_NONPROGRESS` | `FAIL_TARGET` | 0.004 | 5.775 | 0.208 | 3.290 | 0.000 | 0.000 | 6.677 | 0/1 |
| `astropy__astropy-14365` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.104 | 5.524 | 0.003 | 2.335 | 0.000 | 0.000 | 0.000 | 0/0 |
| `astropy__astropy-7746` | `ENVIRONMENT_BLOCKED` | `FAIL_TARGET` | 0.005 | 3.879 | 0.003 | 4.099 | 0.000 | 0.000 | 18.574 | 0/1 |
| `django__django-10924` | `REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH` | `FAIL_TARGET` | 0.372 | 6.899 | 0.517 | 4.669 | 0.003 | 0.003 | 10.417 | 1/4 |
| `django__django-11019` | `REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE` | `FAIL_TARGET` | 0.964 | 6.621 | 0.765 | 3.743 | 0.025 | 0.022 | 13.633 | 4/2 |
| `django__django-11564` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_PRESERVATION_REGRESSION` | 0.263 | 6.645 | 0.010 | 1.134 | 0.000 | 0.000 | 13.416 | 0/0 |
| `django__django-11742` | `REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE` | `BLOCKED_GENERATION` | 0.003 | 5.762 | 0.001 | 1.934 | 0.000 | 0.000 | 14.000 | 0/6 |
| `django__django-11905` | `GENERATOR_NONPROGRESS` | `FAIL_TARGET` | 0.326 | 34.549 | 0.027 | 1.641 | 0.000 | 0.000 | 10.131 | 0/0 |
| `django__django-12308` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.011 | 20.536 | 0.001 | 1.234 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-12747` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.003 | 10.478 | 0.001 | 1.179 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-12908` | `GENERATOR_NONPROGRESS` | `PASS` | 0.016 | 8.976 | 0.183 | 3.938 | 0.002 | 0.003 | 6.754 | 0/0 |
| `django__django-13220` | `GENERATOR_NONPROGRESS` | `FAIL_TARGET` | 0.017 | 6.248 | 0.144 | 2.177 | 0.000 | 0.000 | 8.131 | 0/0 |
| `django__django-13265` | `NO_NEW_REPAIR_EVIDENCE` | `BLOCKED_GENERATION` | 0.025 | 7.051 | 0.005 | 4.279 | 0.000 | 0.000 | 11.926 | 0/0 |
| `django__django-13321` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.084 | 16.257 | 0.002 | 1.131 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-13448` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.071 | 29.474 | 0.002 | 1.808 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-13660` | `TARGET_RECOVERY_BLOCKED` | `FAIL_PRESERVATION_REGRESSION` | 0.017 | 34.927 | 0.003 | 1.691 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-13768` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.030 | 16.177 | 0.002 | 1.335 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-13925` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | 0.521 | 7.734 | 0.003 | 2.436 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-13964` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.042 | 6.762 | 0.000 | 2.167 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-14017` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_TARGET` | 0.005 | 11.621 | 0.209 | 3.334 | 0.000 | 0.000 | 11.972 | 0/0 |
| `django__django-14155` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.006 | 6.307 | 0.002 | 2.330 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-14534` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.052 | 7.502 | 0.003 | 1.920 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-14667` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.130 | 8.845 | 0.015 | 2.283 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-14730` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | 0.038 | 15.310 | 0.000 | 1.862 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-14997` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.008 | 38.334 | 0.000 | 1.759 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15061` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.005 | 37.252 | 0.003 | 2.111 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15202` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.002 | 37.945 | 0.000 | 2.115 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15252` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.608 | 7.810 | 0.019 | 1.731 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15320` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.017 | 35.182 | 0.002 | 2.483 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15400` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.067 | 33.911 | 0.006 | 1.882 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15695` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | 0.063 | 35.002 | 0.002 | 2.207 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15738` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | 0.035 | 19.572 | 0.020 | 1.353 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15781` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.059 | 60.238 | 0.006 | 1.669 | 0.000 | 0.000 | 0.000 | 0/0 |
| `django__django-15819` | `TARGET_RECOVERY_BLOCKED` | `FAIL_PRESERVATION_REGRESSION` | 0.014 | 15.071 | 0.003 | 2.547 | 0.000 | 0.000 | 0.000 | 0/0 |
| `matplotlib__matplotlib-18869` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.187 | 15.975 | 0.000 | 0.684 | 0.000 | 0.000 | 0.000 | 0/0 |
| `psf__requests-2148` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_PRESERVATION_REGRESSION` | 0.006 | 4.409 | 0.352 | 2.404 | 0.009 | 0.002 | 11.231 | 0/0 |
| `pytest-dev__pytest-5413` | `NO_NEW_REPAIR_EVIDENCE` | `PASS` | 0.181 | 1.045 | 0.003 | 1.288 | 0.000 | 0.000 | 11.673 | 0/0 |
| `pytest-dev__pytest-5692` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_TARGET` | 0.013 | 0.896 | 0.001 | 1.185 | 0.000 | 0.000 | 13.041 | 0/0 |
| `pytest-dev__pytest-7220` | `TARGET_RECOVERY_BLOCKED` | `BLOCKED_GENERATION` | 0.021 | 0.898 | 0.003 | 1.252 | 0.000 | 0.000 | 0.000 | 0/0 |
| `scikit-learn__scikit-learn-11040` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.144 | 5.556 | 0.007 | 1.871 | 0.000 | 0.000 | 0.000 | 0/0 |
| `scikit-learn__scikit-learn-14092` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.378 | 3.250 | 0.006 | 1.866 | 0.000 | 0.000 | 0.000 | 0/0 |
| `sphinx-doc__sphinx-8282` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.042 | 1.486 | 0.002 | 1.836 | 0.000 | 0.000 | 0.000 | 0/0 |
| `sphinx-doc__sphinx-8721` | `TARGET_RECOVERY_BLOCKED` | `PASS` | 0.028 | 1.464 | 0.002 | 1.890 | 0.000 | 0.000 | 0.000 | 0/0 |
| `sympy__sympy-11870` | `NO_NEW_REPAIR_EVIDENCE` | `BLOCKED_GENERATION` | 0.054 | 37.012 | 0.003 | 1.802 | 0.000 | 0.000 | 11.634 | 0/0 |
| `sympy__sympy-12454` | `NO_NEW_REPAIR_EVIDENCE` | `PASS` | 0.067 | 34.899 | 0.001 | 0.481 | 0.000 | 0.000 | 9.261 | 0/0 |
| `sympy__sympy-13437` | `NO_NEW_REPAIR_EVIDENCE` | `FAIL_TARGET` | 0.003 | 52.081 | 0.007 | 0.528 | 0.000 | 0.000 | 10.764 | 0/0 |
| `sympy__sympy-18199` | `BUDGET_EXHAUSTED` | `BLOCKED_GENERATION` | 0.019 | 27.216 | 0.000 | 11.604 | 0.000 | 0.000 | 33.234 | 0/0 |
| `sympy__sympy-18835` | `TARGET_RECOVERY_BLOCKED` | `FAIL_TARGET` | 0.015 | 54.699 | 0.010 | 0.402 | 0.000 | 0.000 | 0.000 | 0/0 |
| `sympy__sympy-20049` | `BUDGET_EXHAUSTED` | `BLOCKED_GENERATION` | 0.169 | 32.570 | 0.005 | 12.194 | 0.000 | 0.000 | 22.388 | 0/0 |
| `sympy__sympy-21171` | `BUDGET_EXHAUSTED` | `FAIL_TARGET` | 0.119 | 31.185 | 4.921 | 30.918 | 0.000 | 0.000 | 27.777 | 1/0 |
| `sympy__sympy-22005` | `BUDGET_EXHAUSTED` | `PASS` | 0.018 | 29.464 | 12.614 | 38.309 | 0.824 | 0.039 | 39.599 | 3/0 |

## Per-case process

### `astropy__astropy-14182`

- Generation/Harness: `GENERATOR_NONPROGRESS` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:03:51.847528+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-54e2c85cb58eb1399a74508b'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:03:51.847589+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-a673d7e0b505503d93f624e3'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:03:51.847643+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-a283f648281a5db6b055439a'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:04:13.904431+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-c5f8a68ae89e4731a2964202'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-28T07:04:13.904499+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-0dec02e11262a5d3d9459c39'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:04:22.726492+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-0aed184f5635d9b10b34e21f'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:09:33.858637+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-237d537eb27de92dc1d2eca3'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:09:34.777973+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-7f506880347330872c58ea5f'} -> {'artifact_ids': [], 'event': 'revision_rolled_back', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:09:36.613464+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-9e47440a0b2b7b338773b5f5'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:09:41.809087+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-970d694806c4812b798c6962'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:09:41.809144+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-006af0657dc38451321ce3b2'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:10:16.359958+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-9b048f615a1aacdeaabf6057'}`
- Graph timings: `{"binding_graph": 7.826098590157926e-05, "challenge_graph": 0.0001782689942047, "initial_generation": 6.6771695939823985, "program_graph": 3.2901740079687443, "repository_index": 5.774660350987688, "requirement_graph": 0.2083123380143661, "semantic_graph": 0.004332748998422176}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `11`
- Transitions: `1`; accepted `0`, rolled back `1`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-14182/final_patch.diff`
- Full structured process: `case_process_report.json` entry `astropy__astropy-14182`

### `astropy__astropy-14365`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:58:00.452060+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-cda8810330da2c9d80c593de'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:58:00.452117+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-28ce2dd4249b587a03573f3b'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:58:00.452161+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-e7e21f0a6e9ca991f4804c46'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:58:37.448419+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-bd323ec63fff5c21e4f10089'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.334741236001719, "repository_index": 5.524054363006144, "requirement_graph": 0.002929889000370167, "semantic_graph": 0.10386643299716525}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-14365/final_patch.diff`
- Full structured process: `case_process_report.json` entry `astropy__astropy-14365`

### `astropy__astropy-7746`

- Generation/Harness: `ENVIRONMENT_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:01:45.246235+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-bef3b2734ad5daee15bb0943'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:01:45.246293+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-5746f53226108ec57b2d07da'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:01:45.246338+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-eaf5a5ea731fc2cc2f017da8'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:02:15.137209+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-1023a6c0411b2619f58413f6'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:05:41.036939+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-1a9e2ed4a1d3bea236eff4f6'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:05:43.047383+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-0ca0d184c8d6e1ea0cc812c4'} -> {'artifact_ids': [], 'event': 'revision_rolled_back', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:05:52.801625+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-d45e9552328a7bf45c17b356'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:06:02.912373+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-8f445a09b270e5ef2e6be73a'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:06:02.912487+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-cffd776aeb9d77e6bf3708aa'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:06:33.544010+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-3d5684d6a22d3d1da884ae83'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:10:06.141333+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-1fbe1f9eb7c942832773ea84'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:10:08.070744+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-50cb5033bd706b2eb9a2f76c'} -> {'artifact_ids': [], 'event': 'revision_kept_uncertified', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:10:17.789394+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-0372723f5da1b6a3e4b1bf03'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:10:34.377663+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-539b235ea794214301dad058'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 18.57435094099492, "program_graph": 4.098908548010513, "repository_index": 3.8794173229834996, "requirement_graph": 0.003263877995777875, "semantic_graph": 0.004626028006896377}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `12`
- Transitions: `2`; accepted `0`, rolled back `1`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/astropy__astropy-7746/final_patch.diff`
- Full structured process: `case_process_report.json` entry `astropy__astropy-7746`

### `django__django-10924`

- Generation/Harness: `REVISION_BUDGET_EXHAUSTED_WITH_UNCERTIFIED_PATCH` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:00:22.264130+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-1aea909528b5feb4d548d5b6'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:00:22.264193+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-fc3bafcdd41cff68a6d32abe'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:00:22.264243+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-647f70c6b4a99170815378b3'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:00:51.452978+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-e8c5511b7c9cca4646846afa'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:02:54.979900+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-f1932237d8823d221ee3c8d0'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:02:56.730456+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-2345bd662f55f8d22eee15ea'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:03:04.710401+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-baef52c086e5518296f5c84c'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:03:15.307993+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-7029d1246df4086b9271d41b'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:03:15.308054+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-18ca8ad05a7545a738a6e822'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:03:37.433325+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-6b08590e78f056af773d3448'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:05:36.378650+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-57c0ad543a2e92acca4d71fc'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:05:37.912115+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-e375796f8f6d28adda0c6add'} -> {'artifact_ids': [], 'event': 'revision_rolled_back', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:05:44.453015+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-600e15c9b9dd365d186f249e'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:06:00.450388+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-c27ee290c911c3d62a241807'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:06:00.450525+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-fa17ea14a39a09fa52fcb7fc'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:06:18.807252+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-40313adf990616a20340c2d7'} -> {'artifact_ids': [], 'event': 'mechanical_rollback', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:06:26.187374+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-152d36c69f10bf6cfeeede0f'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:06:27.407187+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-a640ccf8a939a25dd7159d32'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:06:42.606095+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-ef93aac57b53e32d015efc83'} -> {'artifact_ids': [], 'event': 'mechanical_rollback', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:06:50.429353+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-47584b81501dcd19c52479e8'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:06:52.059662+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-16f4ee6fdcd58829cc530db7'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-28T07:07:06.546043+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-21e27eeff60ec88d82a31717'} -> {'artifact_ids': [], 'event': 'mechanical_rollback', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:07:13.992053+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-616d29405e21c3595686d548'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:07:14.972348+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-8001f8529b9807d8b6593f28'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:07:20.416708+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-b8327e2a594ed51025655532'}`
- Graph timings: `{"binding_graph": 0.002762763004284352, "challenge_graph": 0.003043378033908084, "initial_generation": 10.417473158013308, "program_graph": 4.6690967610047664, "repository_index": 6.898833701008698, "requirement_graph": 0.5171250679704826, "semantic_graph": 0.37206542800413445}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `29`
- Transitions: `5`; accepted `1`, rolled back `4`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": ["proposed-edit-b922644e77b8ac54190f587c"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-47fb3543cd83424b1f0cf781"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-10924/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-10924`

### `django__django-11019`

- Generation/Harness: `REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:00:30.192355+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-df8a2b42e6dd45b0337a67f3'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:00:30.192419+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-bf22641e6526fd07a52d5835'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:00:30.192469+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-419afbbddc2c0603c3a0099f'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:01:26.845430+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-4dc5bb123ca2fa4dd191c0de'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:03:14.839145+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-2d442ac35a1d84e9d60566c8'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:03:15.932958+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-e9c9fcee74d9e16c9451543e'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:03:23.070448+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-a1aa410b9dec3bfa6fd9579c'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:03:36.822656+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-3f26532c286dbc196194deb5'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:03:36.822718+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-cbdc93781ba4f71c59cac4ea'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:03:58.441220+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-136645b374f3bb56474a106e'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:05:44.938772+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-8f7ace2811df933e49418a08'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:05:45.997608+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-9708b07a4e45dfcc73dca7e0'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:05:53.125805+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-11220bb34c4c95e004618324'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:06:09.096527+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-df67767d765a2114aba3ccd1'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:06:09.096586+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-340f2d64888f6601e39bffff'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:06:21.146331+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-7652a0742f0f7830add8113c'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:08:04.915131+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-85cea4881a0943b2f25b656f'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:08:05.990622+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-0b579afa034da73c99050a09'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:08:13.266577+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-d09121fdf4ec4ae91a2cbf12'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:08:25.393949+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-1e3d4eef0982e2edfc35cd2e'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:08:25.394004+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-e63445442d21162a34c9d409'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:08:40.229243+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-af9e99f740c456e112c26580'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:10:43.166884+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-f57186946548ede16da539df'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:10:44.279750+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-6721dc6708d123ab82349892'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:10:50.280199+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-86bfed89d83db0dc425c134d'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:11:16.894483+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-f663354bdedebaf601013d11'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:11:16.894610+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-41431d1ad582e8b4efe24aca'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:11:37.469434+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-6704abfff1de7a03ab7ec670'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:13:35.150429+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-d4efed11e19592f1c5a6a4a0'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:13:36.208627+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-20173702881d1440cf8b6a7f'} -> {'artifact_ids': [], 'event': 'revision_rolled_back', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:13:42.102961+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-03b8684a8960e3ce0ba25cac'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:13:48.384492+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-12d9f6de63cdc577ab596d20'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T07:13:48.384555+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-d9504903ed2df58fac4d5ab8'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:14:05.576088+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-7db8d16efc8651b94a1f69b0'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T07:15:56.115270+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-56d46e27036fee0531953a97'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T07:15:57.187995+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-d1167995bfecde5974f8f3f3'} -> {'artifact_ids': [], 'event': 'revision_rolled_back', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T07:16:03.571865+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-feecdb79867c2e1f24517f5f'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T07:16:09.942566+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-a0ef45fb2908d5ab8b6caa2c'}`
- Graph timings: `{"binding_graph": 0.02464087199768983, "challenge_graph": 0.02185610801097937, "initial_generation": 13.633458895987133, "program_graph": 3.7428238899155986, "repository_index": 6.621094122994691, "requirement_graph": 0.765192894032225, "semantic_graph": 0.9635639569896739}`
- Graph build records: `5` (initial and every incremental/context update)
- DeepSeek calls: `31`
- Transitions: `6`; accepted `4`, rolled back `2`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": ["proposed-edit-6d99626b74944ae481e057f3"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-8b0c3b972cf2d979a81058f1"}, {"edit_ids": ["proposed-edit-8d6cc4d32f56c16085c7f290"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-d9c99bac772aad9e40fdc5e8"}, {"edit_ids": ["proposed-edit-45c518aa4892b832b3e314aa"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-854eaa4c6ff77a0a5cd4b540"}, {"edit_ids": ["proposed-edit-aebf2c3f14959c1dd4d868a3"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-c60d098d276b5346058ee22e"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11019/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-11019`

### `django__django-11564`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:10:09.262940+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-c70a1c9fc61eee948d552485'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:10:09.262998+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-4c899ff763fac387834791fc'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:10:09.263043+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-bbb0703777dee16b82955a0b'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:10:49.429531+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-64cf87799d346c2d6fbdb527'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-28T07:10:49.429650+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-d2712a0ade37cb32fb83fed7'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:10:49.433816+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-e73872be1516fc858ef3082e'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 13.41563290101476, "program_graph": 1.1340089780278504, "repository_index": 6.6445678409945685, "requirement_graph": 0.010036029998445883, "semantic_graph": 0.26303295500110835}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `7`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11564/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-11564`

### `django__django-11742`

- Generation/Harness: `REVISION_BUDGET_EXHAUSTED_WITH_TARGET_FAILURE` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T10:31:42.347328+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-0a3b8d0fa9027f27c7c36ee0'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T10:31:42.347387+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-aaef4075bc79bbd59ee13dd5'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T10:31:42.347432+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-1e00aa7019e050625f3bf4ce'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T10:32:15.296988+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-4845471d763229b3c16a04ba'} -> {'artifact_ids': [], 'event': 'public_preservation_regression', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T10:33:51.705592+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-45b1e31e4328f79dff7b1f75'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T10:34:00.727534+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-9453d847609a7d823376eb71'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T10:34:14.433288+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-c163bc61b31d63a9edee2ee8'} -> {'artifact_ids': [], 'event': 'public_preservation_regression', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T10:35:52.494566+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-02fb1b3fbbc5b49ace24e1a1'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T10:36:01.041804+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-b939384d3fe91994b1ad33bd'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T10:36:17.642394+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-5e3cb9781ab922efcb79e8fb'} -> {'artifact_ids': [], 'event': 'public_preservation_regression', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T10:41:23.648232+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-415245e1211f80f1190b300a'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T10:41:29.713466+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-abd5f43375e8b7d8c8c5c922'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T10:41:50.533565+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-05599ee4ee6cf57bc9ff31d2'} -> {'artifact_ids': [], 'event': 'public_preservation_regression', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T10:43:26.624247+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-0c87968b6e1d2e0bda170c93'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T10:43:36.592827+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-1527a17ed3c8a4a55e1eb547'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-28T10:43:53.535452+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-4755192772c237a146a92f3f'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T10:48:57.084419+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-7a89466160608ab0e1c043e4'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-28T10:48:57.986940+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-a8d1a92bedf44e30057dcc77'} -> {'artifact_ids': [], 'event': 'revision_rolled_back', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-28T10:49:08.473830+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-51f9a4a2087cdda2302c7c29'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-28T10:49:13.976110+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-fa97d5e531f8062cb9013f23'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T10:49:13.976164+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-c317ca88b7ddf0160f41257c'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T10:49:44.131123+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-e2a823d3f6ba7cd76c40855f'} -> {'artifact_ids': [], 'event': 'public_preservation_regression', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-28T10:54:40.158872+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-4f63c8992aa7f8a956f725bb'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-28T10:54:47.460340+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-de1ecdc6c0cf446cc83e8f86'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 14.000247062009294, "program_graph": 1.9336596699431539, "repository_index": 5.762181808997411, "requirement_graph": 0.000903200008906424, "semantic_graph": 0.0028320730198174715}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `37`
- Transitions: `6`; accepted `0`, rolled back `6`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11742/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-11742`

### `django__django-11905`

- Generation/Harness: `GENERATOR_NONPROGRESS` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:17:59.748549+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-c35ef1e10ded56074c57e166'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:17:59.748604+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-941e952ac99515107c8de874'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:17:59.748647+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-8222a4579596da7f24175f01'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:18:35.118841+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-00ef29128d9306434dad708d'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 10.130614778987365, "program_graph": 1.64132106595207, "repository_index": 34.54908062299364, "requirement_graph": 0.02720085100736469, "semantic_graph": 0.32622830499894917}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `7`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-11905/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-11905`

### `django__django-12308`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:14:35.553808+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-51c73ffcb68b0447d48a9233'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:14:35.553870+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-9c2e845d421582b3f02d54f2'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:14:35.553917+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-9a065479b88fd1f13c92c14b'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:14:42.776652+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-01ce7ff69cc171ce9072551f'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.233540955989156, "repository_index": 20.536368086002767, "requirement_graph": 0.0009885230101644993, "semantic_graph": 0.010553685016930103}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12308/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-12308`

### `django__django-12747`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:17:39.873363+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-6e08c63248405049fdcb874a'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:17:39.873421+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-5df201ea16054646700263cf'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:17:39.873466+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-6e43b015957da7240f2c89b3'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:17:47.615257+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-26defada74ea72b624237b0b'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.178882939973846, "repository_index": 10.477993947977666, "requirement_graph": 0.0010204549762420356, "semantic_graph": 0.0033747400157153606}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12747/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-12747`

### `django__django-12908`

- Generation/Harness: `GENERATOR_NONPROGRESS` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:19:06.232254+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-fbce1d348ee9d3dd1a960835'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:19:06.232314+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-ed6689af856952176d6300f7'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:19:06.232361+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-a366992bde8d7057deff9036'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:19:28.057511+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-0cef18dc1a924f898298e1f8'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-28T07:19:28.057582+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-ea7391eaacc09486b8992bfb'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:19:39.426812+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-c373d6e37d73c22b5c2805f9'}`
- Graph timings: `{"binding_graph": 0.0022044340148568153, "challenge_graph": 0.0028849059890490025, "initial_generation": 6.753538302989909, "program_graph": 3.938179581979057, "repository_index": 8.975753417005762, "requirement_graph": 0.18260996698518284, "semantic_graph": 0.016040921007515863}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `11`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-12908/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-12908`

### `django__django-13220`

- Generation/Harness: `GENERATOR_NONPROGRESS` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:20:18.883516+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-6f7fbdbe13c383be3b7e2f94'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:20:18.883575+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-b1ff6c82e5d8f88b82b336aa'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:20:18.883618+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-216cdb2dfbcc8204bbefc1b0'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:20:41.227890+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-e10caf9ad567262b8204ac96'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-28T07:20:41.227980+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-906dfff5aa3690ecb0a0c797'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:20:52.805773+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-2c701c5355a135af233b1979'}`
- Graph timings: `{"binding_graph": 7.679799455218017e-05, "challenge_graph": 0.00018347002333030105, "initial_generation": 8.13131569098914, "program_graph": 2.1766479399520904, "repository_index": 6.248046916996827, "requirement_graph": 0.1441318439610768, "semantic_graph": 0.016959986998699605}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `11`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13220/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13220`

### `django__django-13265`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-28T07:21:20.878489+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-e93c30a49da45ea9a829bc8e'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-28T07:21:20.878548+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-e86c93523fa59425b9dc2440'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-28T07:21:20.878593+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-2ac325296f36e2ce5e165c1d'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-28T07:21:42.873180+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-ea5d096932d5570d97c48083'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-28T07:21:42.873306+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-28833e5007af5d838f69345e'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-28T07:21:42.879513+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-506407d51a394eb01de49055'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 11.926266596012283, "program_graph": 4.279109882016201, "repository_index": 7.050600308983121, "requirement_graph": 0.00469498400343582, "semantic_graph": 0.025127803994109854}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `5`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13265/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13265`

### `django__django-13321`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:32:03.086438+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-a1887339f94ffb136d34da85'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:32:03.086494+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-b45cbd025b8026bcc66c4918'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:32:03.086538+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-cd3ca44c420eea8524883463'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:33:23.838781+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-af80afda64ea2c1b547a97aa'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.1306017480092123, "repository_index": 16.25688606100448, "requirement_graph": 0.0022746269969502464, "semantic_graph": 0.08421196999552194}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13321/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13321`

### `django__django-13448`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:32:27.225011+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-ba1d4da9d3d5892d4f1bc917'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:32:27.225066+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-7ac71bb3d2a80eb7a41ce327'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:32:27.225108+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-7d8648de334d4685ad9160c1'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:33:32.132539+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-3d8456a187089602f87cdbad'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.807795258006081, "repository_index": 29.473749336000765, "requirement_graph": 0.0023708320077275857, "semantic_graph": 0.0712653280061204}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13448/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13448`

### `django__django-13660`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:32:39.156328+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-710325bd5f547fd6dbbcb412'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:32:39.156385+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-2b7d1202cb286b63a8a17951'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:32:39.156429+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-297f3ffa0edf584fabe79398'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:33:19.910084+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-4a5b8684cf983cab8d1c70ec'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.6910090279998258, "repository_index": 34.92660422800691, "requirement_graph": 0.0032505510025657713, "semantic_graph": 0.017314689001068473}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13660/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13660`

### `django__django-13768`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:34:47.836958+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-2903cd6f7cb133b5422b0528'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:34:47.837022+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-93a8ee458386f7663ea3f559'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:34:47.837072+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-bc9353f934398f47396b02e1'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:36:02.727975+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-bb36caf5af9ca3a5333112ad'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.3350259760045446, "repository_index": 16.17743346300267, "requirement_graph": 0.0017412769957445562, "semantic_graph": 0.030281434999778867}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13768/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13768`

### `django__django-13925`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:34:46.380916+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-99142332486a83e805f7f9b4'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:34:46.380981+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-eac43a396a46b490732e355f'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:34:46.381032+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-15d7f8ccffc7228e243d01b6'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:36:50.772285+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-d4a5a875100a0a40d2bf2fe1'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.4361024960235227, "repository_index": 7.734442323999247, "requirement_graph": 0.0029172920039854944, "semantic_graph": 0.5211057730048196}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13925/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13925`

### `django__django-13964`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:34:47.590164+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-bd8564ed20503076775434ca'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:34:47.590223+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-8275fc670cdf79d5bf048c1d'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:34:47.590264+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-769b4d4010964471947ed4ad'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:36:03.697423+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-57f7cd47fb4fc7456b58a5f2'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.1671354360005353, "repository_index": 6.761653531997581, "requirement_graph": 0.0002379449870204553, "semantic_graph": 0.042250655009411275}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-13964/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-13964`

### `django__django-14017`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:56:54.918480+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-1b1a88e7e8c98709eb8a81b6'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:56:54.918536+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-4850605a0560c7e8cf4c0b0f'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:56:54.918580+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-dd80d438db7301e9d6d3e9b8'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:58:10.026400+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-59ba9de5635739fc8dfee19d'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T17:58:10.026537+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-4375b06ffe29ea7060fdbcc9'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T17:58:10.028910+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-e6dfa7b5d8a1f65227fcb9c5'}`
- Graph timings: `{"binding_graph": 0.00029950099997222424, "challenge_graph": 0.00041243199666496366, "initial_generation": 11.972233690001303, "program_graph": 3.334066764989984, "repository_index": 11.621466062002582, "requirement_graph": 0.208965302008437, "semantic_graph": 0.004643260006560013}`
- Graph build records: `2` (initial and every incremental/context update)
- DeepSeek calls: `6`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14017/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14017`

### `django__django-14155`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:37:09.101245+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-b40d7812f71b0d4df82aed60'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:37:09.101297+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-71d6e7d801af339a277386e0'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:37:09.101337+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-f0f33e2ea6a19ea64cc09c02'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:37:57.639727+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-c9b7593382f4a14eb7f18bd5'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.3302630499820225, "repository_index": 6.307468941013212, "requirement_graph": 0.0017343549989163876, "semantic_graph": 0.005728673000703566}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14155/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14155`

### `django__django-14534`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:37:13.027188+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-eb8b947ccf400c772c8b1ac0'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:37:13.027246+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-706abc3d08e442dac588b797'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:37:13.027290+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-9fb7d9edefd26e9920740a89'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:38:09.542920+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-6dbae1481beae919b01f792e'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.9201553099846933, "repository_index": 7.502416818999336, "requirement_graph": 0.002899788989452645, "semantic_graph": 0.05208126499201171}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14534/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14534`

### `django__django-14667`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:39:01.375602+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-ec08d1711bdebd3b7bc9090d'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:39:01.375680+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-dec8ec3f57362f3acaef41ac'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:39:01.375730+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-4af30f61d2824413251abbaf'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:41:21.104198+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-02562fd07c92aa3661579ace'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.2828367599868216, "repository_index": 8.844808547990397, "requirement_graph": 0.01495892099046614, "semantic_graph": 0.12961677899875212}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14667/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14667`

### `django__django-14730`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:39:32.832438+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-0633d9c3ea283a0c654943c6'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:39:32.832492+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-63f1e44420f61bb30b2277db'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:39:32.832535+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-aa3ae64eac74087a078fbe3d'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:40:58.521250+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-a0fac947b6c1ff494fe4c341'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.8623464799893554, "repository_index": 15.309605088987155, "requirement_graph": 0.0002405119885224849, "semantic_graph": 0.03836057499574963}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14730/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14730`

### `django__django-14997`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:40:04.362165+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-1f9ab49fb5389c84eb45d0da'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:40:04.362219+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-820a7bc7790b8b7f2d7ccf0b'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:40:04.362261+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-da156dca2ea8cf34951112c9'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:40:58.450134+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-42b7075236bc0a76330ed5e7'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.7593011620047037, "repository_index": 38.33374622800329, "requirement_graph": 0.00024052700609900057, "semantic_graph": 0.008000985995749943}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-14997/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-14997`

### `django__django-15061`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:42:38.342976+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-dd09de5e049162fad5209511'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:42:38.343065+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-0a673f811075c004bae00d6d'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:42:38.343115+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-cfdedeb85fed3fb733609fb2'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:43:04.905785+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-7eda4397d8092a3d739d2ba2'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.1106513640261255, "repository_index": 37.25213168498885, "requirement_graph": 0.0030310849979287013, "semantic_graph": 0.005175549988052808}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15061/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15061`

### `django__django-15202`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:42:38.298710+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-6b4b65e68325d3f2ff053c69'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:42:38.298816+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-ca2a22e117c111891306c271'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:42:38.298890+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-36642c25164308c920e6d1d9'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:43:02.779198+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-3d53c21a678799d9b5d0b3f7'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.1147771419782657, "repository_index": 37.9450804250082, "requirement_graph": 0.0002058770041912794, "semantic_graph": 0.002378645003773272}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15202/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15202`

### `django__django-15252`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:42:27.409455+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-d088ad01129ba246e51cf9a1'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:42:27.409512+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-2e199b31cb768ce81f195017'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:42:27.409557+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-60e1d60b5961ffc21b38a7a2'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:43:36.852070+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-9025b58e23b29635c68aeee0'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.730500050005503, "repository_index": 7.809988168999553, "requirement_graph": 0.01851143001113087, "semantic_graph": 0.6077254130068468}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15252/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15252`

### `django__django-15320`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:44:53.333847+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-04d921784c9df5e9fe47fb3b'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:44:53.333906+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-bd0247221290b3a5aff6f098'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:44:53.333952+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-ffe2735f8d2dfb5e1924445a'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:46:12.185042+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-54ec90b99f141ef3927f85ad'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.482559336000122, "repository_index": 35.18242093999288, "requirement_graph": 0.0016896970046218485, "semantic_graph": 0.01689158900990151}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15320/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15320`

### `django__django-15400`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:44:53.932292+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-a1cba935454f88dd58833906'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:44:53.932346+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-a437ba40a6ae39ed672bf551'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:44:53.932387+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-db0fb34faa39e3c0f9366c33'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:46:13.803853+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-8a2ba1eb8cb4f8a9bbc4833e'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.882261329999892, "repository_index": 33.91148146199703, "requirement_graph": 0.005722472997149453, "semantic_graph": 0.06666490799398161}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15400/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15400`

### `django__django-15695`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:45:34.932157+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-00758966d4c1406cd685a29c'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:45:34.932207+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-384ceaa5ab4fc3921967fa69'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:45:34.932246+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-4b7bad53e3c5ac6056edce1e'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:46:52.667768+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-7d6286da6b59ec30b8753f19'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.207065781985875, "repository_index": 35.00206938700285, "requirement_graph": 0.00228141900151968, "semantic_graph": 0.06327020200842526}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15695/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15695`

### `django__django-15738`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:48:27.497339+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-7f1f76f279e15a08021201ab'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:48:27.497393+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-f8610e474c56e2ad4553ebd1'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:48:27.497435+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-f2587db4625689083ac3f96c'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:49:57.032357+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-8a8495f38bf18acc3d5a0ef5'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.352620093995938, "repository_index": 19.571509970002808, "requirement_graph": 0.02001355700485874, "semantic_graph": 0.03504270300618373}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15738/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15738`

### `django__django-15781`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:48:57.769295+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-30efa4d1c91cd883ca28c242'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:48:57.769366+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-dc5451e6e4b7e9cbf5b98234'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:48:57.769417+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-fa4e86c764e3eff59870aa4d'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:50:46.954506+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-f3d24ecfad2837aa937c18a9'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.6686861280177254, "repository_index": 60.23816746100783, "requirement_graph": 0.006159880998893641, "semantic_graph": 0.059341321990359575}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15781/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15781`

### `django__django-15819`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:59:05.402755+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-d8e30d279d994d568223260d'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:59:05.402821+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-745596a2b97dd1d66e4a0540'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:59:05.402872+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-ae8fc14053fb425c15eb5a53'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T18:00:08.886373+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-f72e0962ed5079a6edf7de65'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 2.5470521520182956, "repository_index": 15.071160291001434, "requirement_graph": 0.0034215790074085817, "semantic_graph": 0.01423960700049065}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/django__django-15819/final_patch.diff`
- Full structured process: `case_process_report.json` entry `django__django-15819`

### `matplotlib__matplotlib-18869`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T18:01:30.405325+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-80d4d7bf7867e1249e60ea6c'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T18:01:30.405380+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-a9931d20710dd10ec1efca52'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T18:01:30.405423+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-f00ed2879256baf9b0168b3e'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T18:02:07.316239+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-cc465f9518edafe78b3a3289'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 0.6840887059806846, "repository_index": 15.974531897998531, "requirement_graph": 0.0002707339881453663, "semantic_graph": 0.18696467600238975}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/matplotlib__matplotlib-18869/final_patch.diff`
- Full structured process: `case_process_report.json` entry `matplotlib__matplotlib-18869`

### `psf__requests-2148`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `FAIL_PRESERVATION_REGRESSION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:47:07.612566+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-cea6340eea8db749fc01d0ce'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:47:07.612621+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-f935ec0038bcfcf146533378'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:47:07.612669+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-25b68fbc030c65b6e8f463f9'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:48:27.653982+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-058498d615316a3a4a777eec'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T17:48:27.654120+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-f956cd68fec7e806f7cdf49f'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T17:49:08.512499+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-5af04d68e727f2c7c9cb3c8d'}`
- Graph timings: `{"binding_graph": 0.009371573003591038, "challenge_graph": 0.0024432280042674392, "initial_generation": 11.231388323998544, "program_graph": 2.4043686959776096, "repository_index": 4.409064819992636, "requirement_graph": 0.3523250839934917, "semantic_graph": 0.0062305460014613345}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `18`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/psf__requests-2148/final_patch.diff`
- Full structured process: `case_process_report.json` entry `psf__requests-2148`

### `pytest-dev__pytest-5413`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:50:59.774078+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-d42bbbef3310b8cbd692ba1f'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:50:59.774145+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-2a9e1ee021ea8b47b425056e'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:50:59.774197+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-019b6d3fd911ec3d03b99bb4'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:51:58.450780+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-da2231ac897f4178f3a895df'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T17:51:58.450899+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-2bc2c00ba83f3cd01cc24328'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T17:51:58.452688+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-b9ea368cce478fce9cd9a837'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 11.673006130004069, "program_graph": 1.2881301619927399, "repository_index": 1.045108275997336, "requirement_graph": 0.003280044998973608, "semantic_graph": 0.18109048399492167}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `6`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5413/final_patch.diff`
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-5413`

### `pytest-dev__pytest-5692`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:51:55.324348+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-9cdc6bf3f12bc6227ccbd593'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:51:55.324410+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-18df86ed50cc114aba2d71e6'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:51:55.324460+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-0a297913e86b4793a38bdae1'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:52:35.058145+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-1dc5a39c3b4fb01defad78ab'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T17:52:35.058260+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-27a9eb0e52c392baefdc12bf'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T17:52:35.059824+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-c15103512fb519046585eb0e'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 13.040831677004462, "program_graph": 1.185170995973749, "repository_index": 0.8958013500086963, "requirement_graph": 0.0006162369973026216, "semantic_graph": 0.012771108988090418}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `6`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-5692/final_patch.diff`
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-5692`

### `pytest-dev__pytest-7220`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:52:25.874773+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-83bff5ffb71b4b8c66bdf06c'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:52:25.874829+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-ac3dc8aa21d95f53c03c3b85'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:52:25.874872+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-bd3fcc273ffa71ca95c82050'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:52:49.010433+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-95d2a1c13c680dbac0c77177'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.2523950860195328, "repository_index": 0.8983773539948743, "requirement_graph": 0.0028484310023486614, "semantic_graph": 0.020801007995032705}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/pytest-dev__pytest-7220/final_patch.diff`
- Full structured process: `case_process_report.json` entry `pytest-dev__pytest-7220`

### `scikit-learn__scikit-learn-11040`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:55:04.860323+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-ecad88e870cf5ef28145752e'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:55:04.860379+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-6e8b6e65f61b82a73652ec70'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:55:04.860423+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-2a1c3b29832b6753155a2002'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:56:07.125313+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-6344b9bf4098aedf7b3321a7'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.8713190339913126, "repository_index": 5.555735757996445, "requirement_graph": 0.007312274989089929, "semantic_graph": 0.14400812699750531}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/scikit-learn__scikit-learn-11040/final_patch.diff`
- Full structured process: `case_process_report.json` entry `scikit-learn__scikit-learn-11040`

### `scikit-learn__scikit-learn-14092`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:57:57.279740+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-7f51ba7aa596032a4a1a0ee9'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:57:57.279797+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-75644a4759ab1642ce452895'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:57:57.279842+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-6f57a18c8d99104ecd9c64ba'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:58:47.535441+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-8f1389441e9ef3604ca4eab3'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.8662769740039948, "repository_index": 3.250304770001094, "requirement_graph": 0.0064051899971673265, "semantic_graph": 0.3782655760005582}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/scikit-learn__scikit-learn-14092/final_patch.diff`
- Full structured process: `case_process_report.json` entry `scikit-learn__scikit-learn-14092`

### `sphinx-doc__sphinx-8282`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:53:07.035036+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-117675a68e0728c54b407220'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:53:07.035093+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-1847f99b3bd505be302ad34f'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:53:07.035137+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-383844eb3f18184263df69d9'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:54:10.208797+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-b0fa79958700a9dc3b168da4'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.835509219992673, "repository_index": 1.486260977006168, "requirement_graph": 0.0017234630067832768, "semantic_graph": 0.042365300992969424}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8282/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sphinx-doc__sphinx-8282`

### `sphinx-doc__sphinx-8721`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:53:45.987470+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-6c2912b4d45df8fc0c27035f'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:53:45.987532+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-f36ce92fd6e6620f4a71c1ca'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:53:45.987593+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-c7721194e0c7d1c369ce1125'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:54:26.462969+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-606ca5315d6b223b58f827d9'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 1.890091727982508, "repository_index": 1.464435964007862, "requirement_graph": 0.0019547730043996125, "semantic_graph": 0.028024111001286656}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sphinx-doc__sphinx-8721/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sphinx-doc__sphinx-8721`

### `sympy__sympy-11870`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:54:05.756811+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-3c1e1e9ced8bd1477425a8d4'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:54:05.756869+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-9e3a83a06606250662a99e6c'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:54:05.756915+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-137b71ca3b15646202d1755f'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:54:57.029642+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-92126f88426775f834d5e86e'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T17:54:57.029775+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-d5536ffde1e002554e38ba9e'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T17:54:57.032874+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-e43457a4f006c74a8c6dfe2a'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 11.634147663004114, "program_graph": 1.8017543500172906, "repository_index": 37.01193963699916, "requirement_graph": 0.00280536399804987, "semantic_graph": 0.05443632999958936}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `6`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-11870/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-11870`

### `sympy__sympy-12454`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:55:15.605348+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-a68be4cb61ef6edc343bfdbd'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:55:15.605415+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-c635d8e39909f68a2035c223'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:55:15.605467+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-978a10ddac78d82a25fc4cd2'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:56:10.642523+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-b509929b46d4103f1a4d1c21'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T17:56:10.642649+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-a4cae265d2e4531f22ec5cf0'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T17:56:10.644466+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-6c363488ce0baab813fcfdcc'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 9.261257992999163, "program_graph": 0.4808320580050349, "repository_index": 34.899112355007674, "requirement_graph": 0.001015696005197242, "semantic_graph": 0.06716447600047104}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `6`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-12454/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-12454`

### `sympy__sympy-13437`

- Generation/Harness: `NO_NEW_REPAIR_EVIDENCE` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T17:55:54.128838+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-f3735a19abb99839231ab090'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T17:55:54.128896+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-066548f0a612402e59ad98f6'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T17:55:54.128938+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-77e76b072fc6771a31506222'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T17:56:22.751097+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-03870a76ca7151b824c51015'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T17:56:22.751177+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-7888751f1dfd3dc891a51e80'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T17:56:22.752266+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-4c553cc97fd53cf72c33d70f'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 10.763830913987476, "program_graph": 0.5276092160202097, "repository_index": 52.081258054007776, "requirement_graph": 0.00700161000713706, "semantic_graph": 0.0034820330038201064}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `6`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-13437/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-13437`

### `sympy__sympy-18199`

- Generation/Harness: `BUDGET_EXHAUSTED` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T06:32:04.640955+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-e9bed6b2441645c998fa295a'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T06:32:04.641022+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-93a4497f13d8224220601b9c'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T06:32:04.641074+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-d15704512f4553642ed4970f'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T06:32:49.869668+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-518ee52e8a8ae182a86eabe8'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T06:32:49.869752+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-2b2f2abbcdc8a93a3fb64252'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:34:17.717553+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-8021f00e0e8f1e65e23bc91f'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T06:34:43.367176+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-fdab650c51b2758ae51d3386'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:35:44.706830+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-d02071ab3788fefe6cc25b00'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T06:36:22.140525+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-6004bbc55a7f9beea7b8a4ef'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:37:11.383129+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-e572a544b9ccb9a73b8e4ee3'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 33.233652037000866, "program_graph": 11.604078622011002, "repository_index": 27.215541240002494, "requirement_graph": 0.00017419899813830853, "semantic_graph": 0.018676299994695}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `120`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-18199/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-18199`

### `sympy__sympy-18835`

- Generation/Harness: `TARGET_RECOVERY_BLOCKED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T18:04:46.762928+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-4aacd9fc9d00d6d0f13e0543'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T18:04:46.762982+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-e283d9f3cbba4480b5af4c3a'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T18:04:46.763024+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-0bb4c83cf774bcf4e0c972be'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T18:05:18.241686+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-d1cd1dfa6f4b0b0c1061e2db'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 0.0, "program_graph": 0.40167472200118937, "repository_index": 54.69877889499185, "requirement_graph": 0.009715740990941413, "semantic_graph": 0.014600275011616759}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `0`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-18835/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-18835`

### `sympy__sympy-20049`

- Generation/Harness: `BUDGET_EXHAUSTED` / `BLOCKED_GENERATION`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T06:47:30.011298+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-4f4bccf82b8063e74faa10c8'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T06:47:30.011406+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-1bb0e8d69bca61d3f3119895'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T06:47:30.011499+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-658b3b18e40ef05fcfc74db6'} -> {'artifact_ids': [], 'event': 'initial_generation_requested_context', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T06:48:10.947803+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-1d99839843e5794133bf85d4'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T06:48:10.947911+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-b99eb7d9b191fd4a46fb06d0'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:49:27.637762+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-d1e826ae978d1d1007924b55'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T06:49:54.274245+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-5745abcc83040508f77a2e92'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:50:40.131118+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-d453b49c6527ffb269b51a95'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T06:51:24.074736+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-51de9a44f109a64f8fa425b2'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:52:12.492999+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-f1c1908121896920e07830a2'}`
- Graph timings: `{"binding_graph": 0.0, "challenge_graph": 0.0, "initial_generation": 22.38775373500539, "program_graph": 12.193531104014255, "repository_index": 32.56997756699275, "requirement_graph": 0.00517397400108166, "semantic_graph": 0.16932544800511096}`
- Graph build records: `1` (initial and every incremental/context update)
- DeepSeek calls: `120`
- Transitions: `0`; accepted `0`, rolled back `0`
- Effective components: `0/0`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-20049/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-20049`

### `sympy__sympy-21171`

- Generation/Harness: `BUDGET_EXHAUSTED` / `FAIL_TARGET`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T06:48:36.995912+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-2a9d65a6cc9249ee2c4db1bd'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T06:48:36.996002+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-1b44726377bc6df76ff2cdbc'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T06:48:36.996076+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-1b199698ce41857ddf541728'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T06:49:16.927243+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-6b4f1fa703f6a8cd8b1f187f'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-27T06:54:22.614171+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-0f6e3ca99487a3303dc61d5a'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-27T06:54:35.308839+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-37d67316968b85a4231d3802'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-27T06:54:54.973693+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-ed3aa2f2fa4ddb41b2555a0c'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-27T06:55:49.099612+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-90c3d8fa99ff05e4681a6985'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-27T06:55:49.099723+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-3aa5af3c2c1e70b550918292'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:56:57.937989+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-c5091acd3df94b3bc6ca992c'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T06:57:20.813778+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-fa5b3d897bd9e5e9c95948bb'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:58:01.537890+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-6969543d1f57d76ca1edd800'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T06:58:24.221548+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-99fbe7a108a64d43a395d495'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:59:06.876258+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-90cd7f0f9ae0e8f43992a28b'}`
- Graph timings: `{"binding_graph": 0.00021999800810590386, "challenge_graph": 0.0003451060183579102, "initial_generation": 27.77681043399207, "program_graph": 30.91832836501999, "repository_index": 31.185451389988884, "requirement_graph": 4.920866058993852, "semantic_graph": 0.11904529298772104}`
- Graph build records: `3` (initial and every incremental/context update)
- DeepSeek calls: `120`
- Transitions: `1`; accepted `1`, rolled back `0`
- Effective components: `0/0`
- Successful steps: `[{"edit_ids": ["proposed-edit-2c64f4f426d7f08852707cd7"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-4a748184014d3053f1f81afc"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-21171/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-21171`

### `sympy__sympy-22005`

- Generation/Harness: `BUDGET_EXHAUSTED` / `PASS`
- Phase path: `{'artifact_ids': [], 'event': 'semantic_hypothesis_set_built', 'from_phase': 'SEMANTIC', 'occurred_at': '2026-07-27T06:49:01.544146+00:00', 'to_phase': 'INDEX', 'transition_id': 'phase-transition-63ca1bb3edd6d6eaca0cbacb'} -> {'artifact_ids': [], 'event': 'repository_index_built', 'from_phase': 'INDEX', 'occurred_at': '2026-07-27T06:49:01.544213+00:00', 'to_phase': 'INITIAL_LOCALIZATION', 'transition_id': 'phase-transition-28711b998ad2151630de6ab5'} -> {'artifact_ids': [], 'event': 'active_slice_localized', 'from_phase': 'INITIAL_LOCALIZATION', 'occurred_at': '2026-07-27T06:49:01.544267+00:00', 'to_phase': 'INITIAL_GENERATION', 'transition_id': 'phase-transition-d457ebd7133cf4af1bc29797'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'INITIAL_GENERATION', 'occurred_at': '2026-07-27T06:49:53.652414+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-9b6d31411fc54ea8f9993df0'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-27T06:56:11.654661+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-b580384494a10fbedfb21ae7'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-27T06:56:15.942888+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-7a300c48a9f7cf1ec425dea9'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-27T06:56:18.806469+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-cbdbe9f0b71647178c03e03a'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-27T06:57:09.703729+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-7ac35fece3e33345f369ba0b'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-27T06:57:09.703815+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-b676ed974a56388bb3fcfc59'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T06:57:39.578582+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-b116e2d50ab2b6b49631c855'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-27T07:04:06.769759+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-f5a78741bed7c81bed9b07c9'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-27T07:04:14.550854+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-1132efdcf91acb8c163f3bce'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-27T07:20:20.817256+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-47c894095967bbe19318a0c2'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-27T07:20:42.135660+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-ca278dfcde6ddbf36c2d88ff'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-27T07:20:42.135735+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-08353eaba8b2289ea3feb2d3'} -> {'artifact_ids': [], 'event': 'generator_revision_submitted', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T07:21:13.132563+00:00', 'to_phase': 'MECHANICAL_VALIDATE', 'transition_id': 'phase-transition-6902b17373169f4a515f120a'} -> {'artifact_ids': [], 'event': 'mechanical_checks_passed', 'from_phase': 'MECHANICAL_VALIDATE', 'occurred_at': '2026-07-27T07:28:02.180247+00:00', 'to_phase': 'ACTIVE_GRAPH_BUILD', 'transition_id': 'phase-transition-bee765a7edd8c4737dec2a2f'} -> {'artifact_ids': [], 'event': 'active_graph_stack_updated', 'from_phase': 'ACTIVE_GRAPH_BUILD', 'occurred_at': '2026-07-27T07:28:11.066997+00:00', 'to_phase': 'CHALLENGE_EXECUTE', 'transition_id': 'phase-transition-0ba9c45e6b0b377983563e12'} -> {'artifact_ids': [], 'event': 'revision_committed', 'from_phase': 'CHALLENGE_EXECUTE', 'occurred_at': '2026-07-27T07:44:16.413756+00:00', 'to_phase': 'TRANSITION_GATE', 'transition_id': 'phase-transition-134c6d4dc72f36cd85fb3b2d'} -> {'artifact_ids': [], 'event': 'revision_requires_further_evidence', 'from_phase': 'TRANSITION_GATE', 'occurred_at': '2026-07-27T07:44:35.602280+00:00', 'to_phase': 'COUNTEREXAMPLE_FEEDBACK', 'transition_id': 'phase-transition-a230563b18d911f7eb9dafd1'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'COUNTEREXAMPLE_FEEDBACK', 'occurred_at': '2026-07-27T07:44:35.602357+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-5b088e899a510495f0441c18'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T07:46:26.935545+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-2eb01d36066f409ea0390ee5'} -> {'artifact_ids': [], 'event': 'counterexample_repair_requested', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T07:47:24.750602+00:00', 'to_phase': 'REPAIR_GENERATION', 'transition_id': 'phase-transition-c03c6c07280185ebdb85e289'} -> {'artifact_ids': [], 'event': 'patch_first_nonprogress', 'from_phase': 'REPAIR_GENERATION', 'occurred_at': '2026-07-27T07:48:33.301876+00:00', 'to_phase': 'ROOT_RECOVERY', 'transition_id': 'phase-transition-dc5d59d52cd09bcddd493b6c'} -> {'artifact_ids': [], 'event': 'seal_terminal', 'from_phase': 'ROOT_RECOVERY', 'occurred_at': '2026-07-27T07:48:58.159153+00:00', 'to_phase': 'SEALED', 'transition_id': 'phase-transition-1fcfccdf539c34b7ce50533b'}`
- Graph timings: `{"binding_graph": 0.8240148970071459, "challenge_graph": 0.038549322009203024, "initial_generation": 39.59941929500201, "program_graph": 38.309409486013465, "repository_index": 29.463632913000765, "requirement_graph": 12.613634978988557, "semantic_graph": 0.01841450399660971}`
- Graph build records: `6` (initial and every incremental/context update)
- DeepSeek calls: `120`
- Transitions: `3`; accepted `3`, rolled back `0`
- Effective components: `0/1`
- Successful steps: `[{"edit_ids": ["proposed-edit-3f879cc9c76955bc5e443ff8"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-d8525956e88ab638527de3c8"}, {"edit_ids": ["proposed-edit-a63a01dd0d8be08de753e60e"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-d252c8b19387c236f2d05673"}, {"edit_ids": ["proposed-edit-1438e62458c1660b3e2635d4"], "eliminated_counterexamples": [], "transition_id": "patch-revision-transition-00d4130591255ffab58bf4f9"}]`
- Failure reason: `no certified Reach transition`
- Patch: `/home/slt/ReachPatch/Code/experiments/swe51/runs/sympy__sympy-22005/final_patch.diff`
- Full structured process: `case_process_report.json` entry `sympy__sympy-22005`

