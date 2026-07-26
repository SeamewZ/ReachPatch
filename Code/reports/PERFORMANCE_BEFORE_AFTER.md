# Performance Before/After

## Why the old route failed

The old generation order called the full precise Program Graph before the first patch, then rebuilt Requirement, Binding and Challenge products, executed an often-empty baseline, and rebuilt products again. Every transition could call the full Program builder once more. Runtime and memory therefore scaled with unrelated repository expressions and repeated products rather than with the repair location.

The new route separates a global summary index from a precise ActiveProgramSlice and generates the first patch before path/product/challenge expansion. Later edits invalidate only touched functions and direct dependents.

## Reproducible synthetic benchmark

Date: 2026-07-27. Python process measured with `/usr/bin/time`. Repository shape:

- one relevant `pkg/target.py` containing `public(value)` and an empty-value guard;
- 60 unrelated modules;
- 300 assignment expressions per unrelated module;
- identical repository for both commands;
- no network, model or test execution included.

The “before” command is the isolated full precise `build_augmented_program_graph` API. Because it includes the newer iterative correctness fixes, this is conservative relative to the historical Astropy run rather than an exaggerated historical result.

| Metric | Full precise API | RepositoryIndex + ActiveProgramSlice | Change |
|---|---:|---:|---:|
| Wall time | 28.05 s | 0.46 s | 61.0x faster |
| Maximum RSS | 487,136 KiB (475.7 MiB) | 26,768 KiB (26.1 MiB) | 18.2x lower |
| RepositoryIndex time | n/a | 0.2114 s | all 61 files summarized |
| ActiveProgramSlice time | n/a | 0.0091 s | one precise file |
| Precise files | 61 | 1 | 98.4% fewer |
| Program nodes | 180,075 | 14 | 99.992% fewer |
| Program edges | 252,026 | 26 | 99.990% fewer |
| Active CFGs | 1 | 1 | relevant callable retained |

Command-level new result:

```text
index_seconds=0.211399663
slice_seconds=0.009057557
index_files=61
precise_files=1
nodes=14
edges=26
rss_mib=25.48046875
WALL=0.46
MAX_RSS_KIB=26768
```

Command-level full precise result:

```text
nodes=180075
edges=252026
precise_files=61
functions=1
WALL=28.05
MAX_RSS_KIB=487136
```

## Real-run context

Before this refactor, the recorded Astropy Program Graph peak approached 10 GiB and some cases spent hours in repository-scale graph construction. That observation motivated the change but is not presented as a controlled before/after benchmark here. The 51 SWE cases must be regenerated with the new code before claiming a corpus-level speedup.

## Complexity change

| Stage | Old scaling | New scaling |
|---|---|---|
| Repository discovery | all files plus all precise AST nodes | all files with declaration summaries only |
| Precise AST/CFG/def-use | all Python functions/expressions | seed slice, direct call/inheritance radius, bounded callables |
| Protocol IR | all detected operations/candidates | active callable operations, bounded candidates |
| Requirement product | all paths times partitions | affected leaves with constrained join and dominance |
| Binding | all feasible obligations | affected/unbound active obligations plus aggregated deferred Oracle frontier |
| Challenge | per binding/scenario product | bounded priority queue over ACTIVE units |
| Patch transition | full graph rebuild | changed file/function invalidation and merge |
| Resume | source rebuild and replay | ArtifactStore deserialize and active-hash verification |

## Enforced production limits

Repository scan, precise AST creation, CFG, def-use, protocol materialization, path enumeration, domain promotion, binding and challenge loops check their budgets internally. Limits create soft partial-analysis frontiers. They do not sleep, raise the recursion limit, wrap an unchanged full build in a timeout, or claim the unfinished graph is closed.

The SWE runner records these per case in `run_manifest.json` and result JSON:

```text
first_patch_generation_seconds
repository_index_seconds / repository_index_files
active_program_slice_seconds
program_nodes / program_edges
precise_files / precise_functions
peak_rss_mib
requirement_leaves / requirement_partitions
candidate_binding_count / active_binding_count / deferred_binding_count
active_challenge_count / real_execution_challenge_count
deepseek_tool_turns / initial / repair / root-recovery counts
accepted_transitions / rolled_back_transitions
final_patch_nonempty / final_patch_hash / final_status
```

## Interpretation and residual risk

RepositoryIndex still parses each selected Python file once to recover declarations and imports. A single extremely large or parser-hostile file cannot be interrupted inside CPython's `ast.parse`; the deadline is checked before and after files. Token summaries are capped at 5,000 unique identifiers per file with a soft frontier. Precise memory remains bounded by `GraphBudget`, but actual execution of project tests or imports can consume memory outside the graph budget and is reported separately.
