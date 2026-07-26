# Patch-First Refactor Report

## Scope and result

Baseline: `c0cf40a0555f2713da12d797d427b5ca6c93c241`.

The SWE generation route is now patch-first:

```text
GenerationInstance
  -> SemanticGraph + HypothesisSet
  -> RepositoryIndex + RequirementCore + ActiveProgramSlice
  -> persistent DeepSeek initial revision
  -> mechanical/public validation
  -> incremental Active Program/Requirement/Binding/Challenge stack
  -> real challenge execution and CounterexamplePackets
  -> same DeepSeek conversation and working patch
  -> Reach-Avoid commit/rollback
  -> sealed patch
  -> separate HarnessEvaluationInstance
```

`ReachPatchController.run()` and `resume()` do not call the full-repository precise Program Graph builder. The retained legacy full-graph diagnostic entry points require the explicit environment variable `REACHPATCH_ENABLE_LEGACY_FULL_GRAPH=1`; they are not exported by the Reach-Avoid package and are not used by the SWE runner.

## Production changes

| Old file/function and behavior | New file/function | New algorithm and actual call chain | Persistent evidence |
|---|---|---|---|
| `evidence/hypotheses.py:freeze_assignment`; ambiguity could return `None` | `HypothesisSet`, `build_hypothesis_set` | Enumerate coherent authority-complete assignments, rank by authority/evidence clusters/explicitness, remove dominated assignments, retain four, extract common hard claims and unresolved decisions. `analyze -> build_hypothesis_set -> HypothesisDiscriminator.plan`. | `hypothesis_set`, `discriminator_probe`, executed `discriminator_result` |
| `controller.py:analyze`; full five-graph closure before Generator | rewritten `ReachPatchController.analyze` | Builds Semantic, Index, Requirement Core and local Program slice, creates checkpoint, then invokes `generate_initial_patch`. Binding/Challenge start empty and are materialized only after a real revision. | graph stack, checkpoint, working patch, conversation, run manifest |
| `program_graph/builder.py`; precise scan of every Python expression | `program_graph/index.py`, `slice.py`, `budget.py` | Index retains only module/class/callable/import/inheritance/decorator/public/test/token summaries and releases ASTs. Seed recovery uses issue symbols, public tests, traceback/diff/trace/context. Active slice expands one caller/callee and inheritance radius and precisely analyzes bounded callables. | `repository_index`, active `program_graph`, `ANALYSIS_TRUNCATED` frontier |
| Recursive CFG and nested callable re-analysis | `analysis.py:CFGBuilder._build_iterative`, `iter_callable_body_without_nested_callables` | Explicit worklist handles sequence, branch, loop, try/finally, with, break/continue, return/raise. Def-use skips nested functions/classes and analyzes each active callable once. | CFG and def-use edges inside Program Graph |
| Unbounded protocol candidate edges | `protocols.py:ProtocolAnalyzer.materialize`, builder budget wiring | Protocol operations are created only in precise active callables, candidates are capped per operation, and overflow becomes a summary frontier. | `ProtocolOperation`, protocol edges/frontiers |
| Full Program rebuild per edit | `incremental.py:update_active_program_slice`, `index.py:update_repository_index` | Invalidate touched-file nodes/CFG/def-use/protocol/path state, parse changed files, include direct dependents/context requests, merge with untouched node objects and hashes, and retain unaffected files. | incremental graph artifact and `TransitionCertificate.graph_delta` |
| Full initial Requirement closure and path-partition product | `compile_requirement_core`, `join_requirement_to_paths` | Core compiles common hard and preferred trusted claims plus public preservation evidence. Constraint join filters trigger/observation, links variables to predicates, checks satisfiability, merges equivalent path guards and caps obligations per leaf. | Requirement Graph leaves, partitions, obligations/frontiers |
| Promotion from every repository predicate | `promote_domains_from_diff` | Only actual changed guards/dispatch/return/exception/state/resource relations and stable trace deltas promote affected leaves. `if not x` yields both branch sides plus empty/nonempty and truthy/falsy neighbours. | `RequirementDelta`, added partitions, soft deadline frontier |
| Full Binding product and one UNKNOWN cell per missing Oracle | `build_active_binding_graph`, `OracleFrontier` | Reuse unaffected units; bind only affected/unbound obligations; admit ACTIVE only when reachable, observable, executable and trusted; aggregate same-class missing Oracles into deferred frontiers. | active/deferred `binding_unit`, aggregated `oracle_frontier` |
| Unbounded Challenge materialization | `materialize_active_challenges`, `ChallengePriority` | Only ACTIVE units enter a bounded priority queue scored by authority, failure risk, diff relevance, information gain and execution cost. At least the top challenge per unit is retained. | active `challenge_cell`, priorities/frontiers |
| Empty execution treated as successful baseline | `execute_challenges -> ChallengeExecutionResult` | Returns executed/skipped IDs, stable `TraceDelta`, and real execution count. Zero execution creates `NO_EXECUTABLE_CHALLENGE`; it does not rebuild dynamic graphs or imply PASS. | TraceBundle only for real runs; explicit frontier otherwise |
| Experimental one-node DeepSeek provider | `repair/deepseek_agent.py`, `tools.py`, `context.py` | One persistent conversation supports bounded search/read/symbol/caller/reference/diff/public-check/slice/edit/finish tools and coordinated multi-file edits for one mechanism. Tool access enforces public-evidence paths. | conversation, revisions, action rejections, external failures |
| Causal-cut miss became undifferentiated `NO_ACTION` | `convert_revision_action` | Returns ACCEPTED, NEEDS_SLICE_EXPANSION, INVALID_OPERATOR, INVALID_SOURCE or FORBIDDEN_PATH. Requested out-of-slice sources cause local expansion; unrelated sources are rejected with reasons. | `generator_action_rejection` |
| One-shot or independent patches | `evaluate_patch_revision` | Trial starts at sole incumbent; applies all revision edits; reconciles real diff; runs differential syntax/import/public checks; updates affected graphs; executes challenges; creates packets; commits progress or rolls back only the revision. | working-patch lineage, actual diff in transition certificate, accepted/rejected hashes |
| Global closure/UNKNOWN in Avoid and Reach | `gates.py`, `metrics.py` | Avoid is limited to apply/mechanical/confirmed regression/forbidden contamination/high-risk effect. Reach requires a nonempty patch, at least one passing active target, passing confirmed preservation/stable counterexamples, diff adequacy, current active hashes, no hard Oracle frontier, no high-value pending challenge and a safe checkpoint. | progress metrics and transition/terminal certificates |
| Recovery rebuilt graphs | `restore.py`, `ReachPatchController.rebuild` | Loads the active Requirement, Program, Binding and Challenge graphs, RepositoryIndex, HypothesisSet, conversation and outcomes from ArtifactStore and verifies hashes. Semantic public evidence is cheaply reparsed to detect a changed issue/test input; no precise repository rebuild occurs. | `recovery_audit`, restored state |
| Harness data shared with generation plumbing | `models/isolation.py`, `experiments/swe51/runner.py` | Public JSON creates `GenerationInstance`; official fields are rejected recursively. Only after seal does a separate command read official data into `HarnessEvaluationInstance`; results live under `experiments/swe51/harness`, outside ArtifactStore. | generation artifacts contain no harness result |

## Budget behavior

`GraphBudget` and `Deadline` are checked inside repository file scanning, AST node creation, CFG worklists, def-use loops, protocol materialization, entrypoint/path worklists, domain promotion/join, active binding join and challenge proposal/materialization. A reached limit preserves completed work, records a soft `ANALYSIS_TRUNCATED` frontier and returns to generation/validation. No recursion-limit increase is used.

The production defaults are the values required by the refactor instruction: 10,000 index files; 40 precise files; 200 precise functions; 50,000 nodes; 150,000 edges; 8 protocol candidates; 24 path classes per leaf; 20 target and 20 preservation bindings; 40 challenges; 60/90/30/15/15 second graph-stage deadlines; and 2,048 MiB graph RSS. The Controller passes every value to the corresponding production loop. DeepSeek tool turns are clamped to the configured per-revision maximum.

## Verification evidence

- Final full suite: `88 passed in 81.57s`.
- Patch-first behavior suite covers semantic ambiguity, local graph scaling, Oracle aggregation, conversion status, persistent `COMMIT -> ROLLBACK -> COMMIT`, index deletion, differential import, evidence isolation, external Generator blocking, budget truncation, long iterative CFG and diff-only domain promotion.
- The persistent-lineage test reaches `GRAPH_REACHED` with one conversation, patch version 2, two accepted hashes, one rejected hash and no selector population.
- Artifact restore was exercised with identical patch and active graph hashes and restored conversation/outcomes; recovery performs no challenge replay.
- The production-path test replaces the legacy full builder with a function that raises on invocation; patch-first run still reaches its target.

## Remaining limitations

- DeepSeek availability, repository dependencies, databases, networks, C extensions and nondeterministic behavior remain external constraints and terminate or remain `BLOCKED_EXTERNAL`, `UNSUPPORTED`, `FLAKY` or `UNKNOWN`; they are not converted to PASS.
- `IMPORT_BASELINE_BLOCKED` is a differential non-regression result, not proof that the module is importable. Its baseline/trial diagnostics remain in the mechanical check.
- The full 51-case experiment was not rerun as part of this code-refactor verification. New per-case manifests contain the requested timing/resource/generator/transition metrics when generation is run.
- Legacy full-graph diagnostic code remains for old artifact investigation, but requires `REACHPATCH_ENABLE_LEGACY_FULL_GRAPH=1` and has no production runner/run/resume route.

## Required answers

1. **Initial patch before complete five graphs:** Yes. Binding and Challenge objects are empty active-stack placeholders at first generation; path/product/challenges are built after the real diff.
2. **DeepSeek initial Generator and Repair Player:** Yes, through `PersistentDeepSeekAgent.generate_initial_patch`, `repair_from_counterexamples` and `root_recovery`.
3. **Same conversation and working patch:** Yes. One `GeneratorConversation` and one checkpoint lineage survive commit and rollback.
4. **Local precise Program Graph:** Yes. RepositoryIndex is global summary only; precise nodes are limited to ActiveProgramSlice.
5. **Incremental post-patch graphs:** Yes. Program, Requirement, Binding and Challenge updates are affected-slice operations; ablation reuses the incrementally prepared candidate Requirement Graph.
6. **UNKNOWN product:** No per-unit UNKNOWN_ORACLE Challenge product remains. Missing Oracles are deferred and aggregated.
7. **Real commit and rollback:** Yes. The integration trajectory records `COMMIT, ROLLBACK, COMMIT` and reaches a nonempty patch.
8. **Harness/gold leakage:** No production path found. Types, record filtering, tool path enforcement, directory separation and tests enforce this boundary.
9. **Hard UNKNOWN:** A mandatory high-authority missing Oracle remains a hard aggregated OracleFrontier; Generator/API/environment failure remains an explicit blocked terminal. Low-risk deferred units and analysis truncation are soft.
10. **Production full-repository precise call:** No. Only environment-gated legacy diagnostics can invoke `build_augmented_program_graph`.
