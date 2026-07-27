# ReachPatch Paper-to-Code Implementation Matrix

Status key: `IMPLEMENTED` means the named production symbol has concrete
algorithmic code and is connected to the controller or a public compatibility
entry point. It does not claim that every row has an isolated test. Current
runtime evidence is `runs/audit-run-final` and its valid terminal verification;
focused unit evidence is listed in the final reports. Historical paper paths that moved during implementation are exposed
through compatibility modules under `reachpatch/challenge_graph`,
`reachpatch/models`, and `reachpatch/reach_avoid`.

This matrix was created before production code. Line references target
`/home/slt/ReachPatch/Paper/reachpatch_graph_grounded.tex`. Implementation
paths are relative to `/home/slt/ReachPatch/Code`. `PLANNED` means that the row has an
implementation target but is not yet evidence of completion. Final status is
set only after the named symbol is exercised through the production pipeline.

## Audit Status Overrides

The original rows below were written before the adversarial audit. These
overrides are authoritative:

| Paper item | Current status | Evidence / limitation |
|---|---|---|
| Open-world quantified coverage and universal Reach gate | `IMPLEMENTED_CONSERVATIVE` | `requirement_graph/domains.py` and `challenge_graph/materialize.py`; witness-only partitions create hard `UNIVERSAL_DOMAIN_COVERAGE` frontiers. |
| Dynamic tracing and graph merge | `IMPLEMENTED_CONNECTED` | `execution/worker.py`, `program_graph/tracing.py`, controller initialization and transition. |
| Diff-specific Impact Cone | `IMPLEMENTED_CONNECTED` | `program_graph/impact.py`, `reach_avoid/transition.py`, persisted transition graph delta. |
| Counterexample minimization | `IMPLEMENTED_CONNECTED` | `repair/counterexamples.py`; stable failures are replay-shrunk before packet construction. |
| Artifact schema and recovery validation | `IMPLEMENTED_STRICT` | `artifacts/schema.py`, `artifacts/store.py`; unknown types and invalid recovered payloads are rejected. |
| Terminal/DICC certificate replay | `IMPLEMENTED_PAYLOAD_REPLAY` | `artifacts/verify.py`, `challenge_graph/dicc.py`; persisted plans/obligations are replayed, but historical repository execution is not rerun. |
| Default repair synthesis across all mechanism classes | `PARTIAL` | `repair/session.py` deterministically emits return/identity/exception edits. Other registered mechanism classes require a validated injected `action_provider`; the CLI has no universal built-in source generator. |
| Django/SymPy/NumPy/requests semantic adapters | `PARTIAL` | Real additive facts/hints/frontiers exist; complete framework-specific semantics/oracles do not. |
| Paper experiment driver and result tables | `OUT_OF_SCOPE_CURRENT_STAGE` | Generic fixture/CLI execution exists; large-scale SWE-bench and statistical tables are not populated. |

| Paper location / normative item | Implementation file | Class or function | Input | Output | Persistent artifact | Production caller | Error / frontier state | Status |
|---|---|---|---|---|---|---|---|---|
| Sec. 2, Eqs. instance/scenario/trace/predicate | `reachpatch/models/core.py` | `Instance`, `Scenario`, `ExecutionTrace`, `TruthValue` | repository, issue, environment, inputs | typed trace semantics | evidence and trace records | orchestration, executor | `UNKNOWN_EXECUTION`, `BLOCKED_EXTERNAL` | IMPLEMENTED |
| Sec. 2.2 target/regression/uncertainty separation | `reachpatch/reach_avoid/state.py` | `classify_state_information` | outcomes and frontiers | disjoint PASS/FAIL/UNKNOWN/BLOCKED sets | reach-avoid state | transition gate | unknown never coerced to pass | IMPLEMENTED |
| Sec. 4, Eq. HGraph | `reachpatch/evidence/semantic_graph.py` | `build_semantic_graph` | issue, tests, docs, signatures | typed evidence-semantic graph | `semantic_hypothesis_graph` | requirement compiler | contradiction, unknown decision | IMPLEMENTED |
| Sec. 4 typed extraction and authority rules | `reachpatch/evidence/extract.py` | `segment_evidence`, `deterministic_semantic_parse`, `adjudicate_authority` | public text and AST assertions | evidence nodes and support/refute edges | evidence ledger | semantic graph builder | unsupported/provisional evidence | IMPLEMENTED |
| Sec. 4, Eqs. H(d), Theta and Alg. semantic construction | `reachpatch/evidence/hypotheses.py` | `factor_decisions`, `enumerate_assignments`, `freeze_assignment` | HGraph and constraints | coherent assignment beam | episode assignment | analyzer/controller | `SEMANTIC_BLOCKED`, `SEMANTIC_RESTART` | IMPLEMENTED |
| Sec. 4.3 compilation boundary | `reachpatch/requirement_graph/compiler.py` | `compile_assignment_overlay` | HGraph and frozen theta | authority-qualified leaves | requirement graph | path compiler | contested/revoked authority | IMPLEMENTED |
| Sec. 5, Eqs. RGraph/hyperedge/semantic leaf | `reachpatch/requirement_graph/models.py` | `RequirementGraph`, `RequirementHyperEdge`, `RequirementLeaf` | semantic assignment | graph schema and leaves | `requirement_graph` | path compiler/binder | custom/unknown frontier | IMPLEMENTED |
| Sec. 5.2, Eq. path obligation | `reachpatch/requirement_graph/models.py` | `RequirementPathObligation`, `PathClass` | leaf, partition, path, exit | atomic quantified path obligation | path obligations | binding builder | unresolved trigger/path/oracle | IMPLEMENTED |
| Sec. 5.3, Alg. requirement-path compilation | `reachpatch/requirement_graph/compiler.py` | `compile_requirement_paths` | HGraph, theta, PGraph, evidence, caps | obligations, ledger, frontiers | requirement graph/path/edge ledger | analyzer/DICC | cap, dynamic target, unsupported edge | IMPLEMENTED |
| Sec. 5.3 symbolic partitioning/domain promotion | `reachpatch/requirement_graph/domains.py` | `symbolic_scenario_partitions`, `promote_program_predicates` | quantified domains and branch predicates | satisfiable requirement/challenge partitions | domain promotions | path compiler/DICC | `PROVED_INFEASIBLE` or frontier | IMPLEMENTED |
| Sec. 5.4, Eqs. ReqPathClose/PathCov | `reachpatch/requirement_graph/closure.py` | `requirement_path_closure`, `path_coverage` | leaves, obligations, edge ledger, frontiers | Boolean certificate and diagnostic ratio | closure snapshot | reach gate/report | missing obligation or ledger edge | IMPLEMENTED |
| Sec. 5.5 authority partition L_H/L_D/L_P/L_R | `reachpatch/requirement_graph/authority.py` | `partition_leaves`, `apply_authority_change` | leaves and evidence delta | authority groups and invalidations | authority audit | oracle/binding/controller | contested/demoted/revoked | IMPLEMENTED |
| Sec. 6, Eq. PGraph and construction passes | `reachpatch/program_graph/builder.py` | `PythonProgramGraphBuilder.build`, `incremental_update` | Python repository, seeds, traces | behavioral interaction graph | `program_graph` | analyzer/DICC/root recovery | parse/import/dynamic frontiers | IMPLEMENTED |
| Sec. 6 AST/scope/import/points-to/CFG/def-use | `reachpatch/program_graph/analysis.py` | `ScopeAnalyzer`, `CFGBuilder`, `DefUseAnalyzer`, `PointsToAnalysis` | Python AST and symbol tables | nodes, flow and dependency edges | program graph facts | graph builder | syntax/unresolved target | IMPLEMENTED |
| Sec. 6 protocol/dispatch behavior | `reachpatch/program_graph/protocols.py` | `ProtocolAnalyzer`, `ProtocolOperation` | AST operations and type candidates | candidate/selected/fallback/infeasible edges | protocol IR | graph builder/DICC | dynamic dispatch frontier | IMPLEMENTED |
| Sec. 6 targeted dynamic pass | `reachpatch/program_graph/tracing.py` | `DynamicTracer`, `merge_trace` | isolated recipe/check | calls, branches, exceptions, shapes, effects | dynamic trace and graph version | graph escalation/executor | timeout/flaky/unsupported | IMPLEMENTED |
| Sec. 6.3-6.5, Alg. entrypoint recovery | `reachpatch/program_graph/entrypoints.py` | `recover_entrypoints` | seed/trigger/observation and PGraph | condition-aware paths, bypasses, frontiers | entrypoint recovery result | path compiler/binder | localization/dynamic frontier | IMPLEMENTED |
| Sec. 5.2 loop/recursion compression | `reachpatch/program_graph/paths.py` | `summarize_path_classes` | CFG/interprocedural paths | zero/one/many/exits path classes | path classes | requirement compiler | analysis cap frontier | IMPLEMENTED |
| Sec. 7.4, Eqs. causal cut/ranking/hitting set | `reachpatch/program_graph/slicing.py` | `causal_repair_cut`, `rank_repair_loci`, `component_repair_frontier` | entry, path, observations, failures | dependency-closed minimal cuts | repair cuts/components | binder/repair policy | no legal modifiable cut | IMPLEMENTED |
| Sec. 7.4 and actual diff influence | `reachpatch/program_graph/impact.py` | `impact_cone`, `guarded_diff_influence_cone` | touched nodes/relations and PGraph | downstream callers, effects and regressions | impact cone | DICC/transition gate | residual/hard frontier | IMPLEMENTED |
| Sec. 7, Eqs. BGraph/BindingUnit | `reachpatch/binding_graph/models.py` | `BindingGraph`, `BindingUnit`, `RepairComponent` | requirement paths and program facts | constrained graph product | `binding_graph` | challenge/repair/controller | stale/blocked unit | IMPLEMENTED |
| Sec. 7.3, Alg. path-complete binding | `reachpatch/binding_graph/builder.py` | `build_binding_graph`, `bind_path_obligation` | RPG, PGraph, evidence, checkpoint | one exact unit per feasible path | bindings and indexes | analyzer/DICC | missing compatibility projection | IMPLEMENTED |
| Sec. 7 product compatibility/projection | `reachpatch/binding_graph/compatibility.py` | `project_domain_to_guard`, `project_observation`, `oracle_applicable` | symbolic domain/path/contract/oracle | proof-bearing compatibility | binding witness | binder | UNSAT or explicit frontier | IMPLEMENTED |
| Sec. 7.3 bypass and closure | `reachpatch/binding_graph/closure.py` | `expand_bypasses`, `compute_binding_path_closure` | RPG and BGraph | new obligations and closure measure | binding closure | analyzer/reach gate | missing/stale path binding | IMPLEMENTED |
| Sec. 8 executable scenario tuple | `reachpatch/oracle/models.py` | `ObservationContract`, `Oracle`, `ExecutableScenario` | requirement observation/relation/evidence | locked executable contract | oracle/scenario ledger | challenge materializer/executor | provisional/contested oracle | IMPLEMENTED |
| Sec. 8.1 oracle lifecycle | `reachpatch/oracle/authority.py` | `resolve_oracle`, `contest_oracle`, `adjudicate_oracle` | evidence and contradictions | active A/B/C or provisional record | oracle lifecycle ledger | binder/controller | contested/downgraded/revoked | IMPLEMENTED |
| Sec. 8.2 normative paired classifier | `reachpatch/oracle/classifier.py` | `classify_pair` | base run, patch run, scenario | PASS/FAIL/UNKNOWN with origin | validation outcome | executor/transition gate | environment/schema/precondition unknown | IMPLEMENTED |
| Sec. 8.3 discriminator separation | `reachpatch/oracle/discriminator.py` | `HypothesisDiscriminator` | viable hypotheses and observation plan | raw evidence only | discriminator ledger | semantic adjudication | discriminator-only | IMPLEMENTED |
| Sec. 8.4 scenario-operator algebra | `reachpatch/challenge_graph/operators.py` | `ScenarioOperatorRegistry` | binding/program relations | deterministic challenge proposals | operator/proposal records | DICC/materializer | unsupported relation frontier | IMPLEMENTED |
| Sec. 8.5 challenge admission | `reachpatch/challenge_graph/materialize.py` | `materialize_challenges`, `admit_scenario` | obligations, locked oracle, graph hashes | deduplicated executable challenges | validation plan | controller | malformed/source mutation/untrusted oracle | IMPLEMENTED |
| User contract and Sec. 8 challenge view | `reachpatch/challenge_graph/models.py` | `ChallengeGraph`, `ChallengeCell` | R/P/B graphs, diff, outcomes | versioned challenge projection | `challenge_graph` | controller/report | all named terminal statuses | IMPLEMENTED |
| User DICC input protocol | `reachpatch/challenge_graph/recipes.py` | `InputRecipe`, `RecipeCompiler`, `CandidateGenerator` | constraints/witnesses/setup | executable imports/construction/calls/operators/traces | input recipes | challenge executor | unsupported/external/unsat | IMPLEMENTED |
| User counterexample algorithm | `reachpatch/challenge_graph/counterexamples.py` | `counterexample_from_challenge`, `minimize_counterexample` | cell, recipe candidates, executor/oracle | stable minimized packet or exact unknown | counterexample packet | controller/generator session | flaky/blocked/unknown oracle | IMPLEMENTED |
| Sec. 9/Appendix paired TraceBundle | `reachpatch/execution/executor.py` | `TraceExecutor.execute_recipe`, `execute_paired`, `execute_with_stability` | isolated base/trial trees and recipes | return/exception/stdout/stderr/state/effects/traces | trace bundles/executions | challenges/controller | timeout/resource/unserializable/flaky | IMPLEMENTED |
| Sec. 9 worktree isolation and reconcile | `reachpatch/execution/worktree.py` | `WorktreeManager`, `reconcile_actual_diff` | base commit/checkpoint/trial | one transactional tree and canonical diff | checkpoint/diff snapshots | controller/repair action | invalid/empty/forbidden diff | IMPLEMENTED |
| Sec. 9 mechanical validation | `reachpatch/execution/mechanical.py` | `run_mechanical_checks` | trial tree and project config | syntax/import/collection/build results | execution records | transition evaluator | mechanical avoid | IMPLEMENTED |
| Sec. 9.4 counterexample packet formula | `reachpatch/models/counterexample.py` | `CounterexamplePacket`, `build_controller_counterexample` | failures, traces, slices, diff | trusted typed feedback bundle | counterexamples | persistent generator/repair policy | missing oracle kept explicit | IMPLEMENTED |
| Sec. 9.8 mechanism fingerprints | `reachpatch/repair/diagnosis.py` | `diagnose_mechanism`, `mechanism_fingerprint` | first divergence, graph slice, diff | root-mechanism diagnosis/fingerprint | mechanism memory | intent selector/root recovery | unclassified mechanism | IMPLEMENTED |
| User registered diff operator | `reachpatch/repair/operators.py` | `RegisteredDiffOperator`, `apply_registered_operator` | repair intent, AST cut, worktree | structured edit, actual diff, impact cone | repair action/transition | generator session/controller | precondition/scope/apply failure | IMPLEMENTED |
| Sec. 9.6/9.8 next intent and pressure | `reachpatch/repair/policy.py` | `select_losing_core`, `next_untried_repair_intent` | state, components, failures, memory | exactly one component-complete intent | repair intent | controller | no legal action/root recovery | IMPLEMENTED |
| Sec. 9.8 root recovery | `reachpatch/repair/recovery.py` | `root_recovery` | stagnating core and current graphs | new cut/rebinding/classified terminal | root recovery record | controller | named recovery outcomes | IMPLEMENTED |
| Sec. 9.1-9.5 persistent generator contract | `reachpatch/repair/session.py` | `GeneratorSession`, `revise_from` | checkpoint, intent, feedback, tool adapter | at most one transactional trial | generator session ledger | controller | revision exhausted/malformed | IMPLEMENTED |
| Sec. 9.5, Alg. DICC | `reachpatch/challenge_graph/dicc.py` | `diff_induced_challenge_plan`, `finalize_diff_induced_challenge_closure` | state, trial, actual delta | overlay, edge ledger, closure certificate | diff obligations/certificate | transition evaluator/reach gate | hard/residual frontier | IMPLEMENTED |
| Sec. 9.3 Eqs. target deficit/established passes | `reachpatch/reach_avoid/metrics.py` | `target_deficit`, `progress_metrics` | current units/outcomes/weights | deficit and decomposed progress | state/certificate | transition gate | graph-version mismatch | IMPLEMENTED |
| Sec. 9.2 Eqs. target/safe/raw avoid/terminal avoid | `reachpatch/reach_avoid/gates.py` | `in_target_set`, `in_safe_set`, `raw_avoid_reasons`, `terminal_avoid_reason` | state and certificates | reach/avoid decisions | state/terminal certificate | controller | exact named terminal reason | IMPLEMENTED |
| Sec. 9.4, Alg. single transition | `reachpatch/reach_avoid/transition.py` | `evaluate_single_update` | state, session, one intent | atomic accepted or rolled-back result | transition certificate | controller | UNKNOWN safety blocks commit | IMPLEMENTED |
| Sec. 9.7 transactional semantics | `reachpatch/reach_avoid/checkpoint.py` | `atomic_commit_checkpoint`, `rollback` | state/trial/evidence | new sole checkpoint or exact restoration | checkpoint lineage/receipt | transition evaluator/resume | restoration inconsistency avoid | IMPLEMENTED |
| Sec. 9.10 edit-retention ablation | `reachpatch/repair/ablation.py` | `edit_retention_ablation` | graph-reached checkpoint/edit groups | cleaned or restored incumbent | ablation ledger | terminal sealing | failed recertification restores prior | IMPLEMENTED |
| Sec. 9.12, Alg. end-to-end controller | `reachpatch/reach_avoid/controller.py` | `ReachPatchController.run`, `resume` | instance/base/budgets/config/generator | one sealed incumbent patch/certificate | all required artifacts | CLI | graph reached or explicit terminal | IMPLEMENTED |
| Sec. 10 role-isolated budgets | `reachpatch/models/budget.py` | `BudgetVector`, `BudgetLedger` | role/episode/transition costs | atomic charge/reserve decisions | budget ledger | all model/execution roles | typed exhaustion status | IMPLEMENTED |
| Sec. 10.3 required artifacts | `reachpatch/artifacts/store.py` | `ArtifactStore` | typed artifact envelopes | atomic content-addressed JSON/JSONL/index | artifact store/index | every module | schema/hash/recovery inconsistency | IMPLEMENTED |
| User artifact envelope contract | `reachpatch/artifacts/models.py` | `ArtifactEnvelope`, `ArtifactSchemaRegistry` | artifact metadata/payload | validated versioned envelope | every artifact | ArtifactStore | schema/content hash mismatch | IMPLEMENTED |
| Appendix normative schemas | `reachpatch/models/*.py` | all named dataclasses | graph/controller records | serializable validated records | named ledgers | production pipeline | validation error | IMPLEMENTED |
| Appendix normative state machine | `reachpatch/reach_avoid/machine.py` | `ControllerPhase`, `StateMachine.transition` | current phase/event/artifacts | legal next phase | trajectory steps | controller/resume | illegal/stale transition | IMPLEMENTED |
| Appendix prompt contracts | `reachpatch/repair/contracts.py` | `validate_repair_plan`, `validate_materialization` | Generator/materializer structured output | accepted typed plan/proposal | session/proposal records | session/challenges | missing/stale/cross-assignment | IMPLEMENTED |
| User general adapters | `reachpatch/adapters/*.py` | `ProjectAdapter`, Python/Django/SymPy/NumPy/Requests adapters | repository and semantic probes | additive graph/oracle/execution hints | adapter observations | analyzer/executor | blocked/unsupported, never fabricated PASS | IMPLEMENTED |
| User and Appendix CLI contract | `reachpatch/cli/main.py`, `reachpatch/run.py` | all required subcommands | config/instance/run directory | shared production operations | command audit | console entry point | nonzero typed failures | IMPLEMENTED |
| Appendix unit/integration contract | `tests/unit`, `tests/integration`, `tests/conformance` | normative acceptance tests | controlled generic repositories | executable conformance evidence | test reports | CI/manual verification | failed gate | IMPLEMENTED |
| Appendix run validity and terminal export | `reachpatch/artifacts/verify.py`, `reachpatch/reporting.py` | `verify_artifacts`, `seal_terminal`, `export_patch` | run artifact store/checkpoint | replay proof and one pure diff | terminal certificate/final patch | CLI | hash/lineage/evidence inconsistency | IMPLEMENTED |

## Patch-first Production Override (2026-07-27)

Rows describing the pre-refactor full-closure route are historical. The
authoritative production symbols are documented in
`PATCH_FIRST_REFACTOR_PLAN.md` and `PATCH_FIRST_REFACTOR_REPORT.md`:
`build_hypothesis_set`, `build_repository_index`,
`compile_requirement_core`, `build_active_program_slice`,
`evaluate_patch_revision`, `run_public_checks_paired`, sparse active binding
and challenge queues, persistent DeepSeek revisions, and Reach-Avoid
commit/rollback. The current suite has 105 passing tests. Legacy
`build_augmented_program_graph` callers are explicitly environment-gated and
not reachable from normal `run`, `resume`, or SWE generation.
