# ReachPatch SWE-51 Graph Optimization Log

## 2026-07-26: lossless graph representation and closure reuse

Observed bottlenecks:

- `pytest-dev__pytest-7220` remained CPU-bound in the initial Requirement
  Graph after its Program Graph completed in 357.164 seconds.
- `sympy__sympy-12454` remained CPU-bound in the initial Program Graph and
  reached approximately 19 GiB RSS during an early live sample.
- Both processes consumed approximately one full CPU core. Neither process
  was blocked on DeepSeek, disk I/O, or swap.

Production changes for subsequently started cases:

- Replaced eager materialization of every qualified-name suffix with a
  final-component index plus exact suffix filtering. Exact and conservative
  suffix resolution results are preserved.
- Represented multi-target calls, parameter/return propagation, test
  observations, and protocol candidates as native graph hyperedges. All
  source and target node IDs, reachability, dispatch order, and confidence
  classes remain present.
- Removed the builder's duplicate global AST-to-module map because no
  production caller read it.
- Cached Requirement Graph observation reachability, entrypoint recovery,
  recovered path shapes, matching entry paths, and preservation callers by
  their complete semantic inputs. Requirement leaf x partition x path-class
  obligations and path-edge ledger records are still materialized separately.

Benchmark on `psf__requests-2148` after the Program Graph changes, while the
two long-running cases remained active:

| Metric | Value |
| --- | ---: |
| Wall time | 12.87 s |
| User CPU time | 12.39 s |
| Maximum RSS | 254,864 KiB |
| Program nodes | 113,843 |
| Program edges/hyperedges | 149,365 |
| Protocol operations | 2,063 |
| Protocol candidate edges/hyperedges | 1,956 |
| Protocol candidate targets retained | 10,246 |

The previously recorded Program Graph time for the same repository was about
15.10 seconds. The new measurement is approximately 15% lower despite CPU
contention. This comparison is directional because it was not collected under
an isolated benchmark scheduler.

Verification:

- `tests/unit`: 43 passed.
- `tests/integration` and `tests/conformance`: 2 passed.
- A conformance assertion verifies that the union of every protocol
  candidate hyperedge's targets exactly equals the persisted
  `ProtocolOperation.candidate_target_ids` set.

The already running pytest and SymPy processes loaded the previous code before
these edits and cannot hot-reload the optimization. They continue without a
restart and their recorded timings must not be attributed to the optimized
implementation.

## 2026-07-26: canonical hashing and closure reuse

Additional profiles showed that graph construction and Requirement/Binding
closure repeatedly paid for semantically identical work:

- `stable_id()` canonicalized already-native tuples and dictionaries and
  repeatedly sanitized the same ID prefixes.
- the first Requirement closure hashed every Program Graph component, then a
  Path Class or frontier insertion invalidated the aggregate and caused all
  unchanged nodes, edges, CFGs, and Protocol IR records to be hashed again;
- Binding construction recomputed every unit's causal repair cut while
  building RepairComponents and computed the same Impact Cone both inside and
  immediately after the cut.

The production implementation now:

- recognizes recursively JSON-native values with string-only mapping keys and
  sends them directly to the same configured `json.dumps`; sets, dataclasses,
  enums, paths, custom mappings, and subclassed containers retain the general
  conversion path;
- caches sanitized stable-ID prefixes with a bounded 256-entry cache;
- retains ordered component digest tuples for nodes, edges, CFGs, Protocol IR,
  Path Classes, and frontiers, invalidating only the component actually
  changed. The aggregate Program Graph hash formula is unchanged;
- routes internal node attribute, CFG, and Protocol IR updates through
  invalidating Program Graph methods;
- reuses the exact CausalRepairCut objects produced for BindingUnits when
  constructing RepairComponents and reuses Impact Cones by their complete
  sorted source-node tuple.

Differential safeguards:

- canonical JSON is compared against the pre-optimization conversion for
  primitives, nested mappings, sets/frozensets, enums, paths, dataclasses, and
  custom mappings; content hashes and stable IDs are also compared;
- the cached Program Graph hash is compared byte-for-byte with an independent
  full Merkle recomputation before and after frontier and node mutations;
- every hyperedge is projected into pairwise edges and checked for the same
  source/target relation set, all-node predecessor/successor sets, sampled
  transitive reachability, protocol candidate target sets, and unchanged
  frontiers;
- sorted adjacency and path-topology caches have mutation invalidation tests;
- the complete unit, integration, and conformance suite passes (`60 passed`).

Requests benchmark results under concurrent experiment load:

| Stage/metric | Previous recorded | Current | Semantic output check |
| --- | ---: | ---: | --- |
| Program Graph wall | 15.10 s | 9.92-10.44 s | 113,843 nodes; 149,365 edges/hyperedges; 2,063 protocol operations; 10,246 protocol candidate targets |
| Requirement Graph wall | 117.14 s | 13.74 s | 6 leaves; 1,320 partitions; 1,098 obligations; 4,086 ledger records; 216 frontiers; 366 Path Classes |
| Binding Graph wall | 29.19 s | 2.39 s | 1,098 units; 3,357 frontiers; 185 RepairComponents |
| Challenge Graph materialization wall | 22.83 s | 0.34 s | 1,098 cells; 1,098 materialization frontiers |
| Program-only maximum RSS | 254,864 KiB | 255,592 KiB | no material change |
| Program + Requirement maximum RSS | not isolated | 316,736 KiB | component digest tuples retained for reuse |

The four cases restarted after this optimization and therefore eligible for
the current timing class are `pytest-dev__pytest-7220`,
`sympy__sympy-12454`, `sympy__sympy-11870`, and
`scikit-learn__scikit-learn-14092`. Their earlier run directories were moved
by the runner to `runs/_interrupted`; no timing or failure history was
overwritten.

## 2026-07-26: iterative SCC and long dependency corridors

`astropy__astropy-14182` completed its Program Graph in 2726.92 seconds and
then failed in Requirement Graph construction with `RecursionError`. The
failure was traced to recursive Tarjan SCC traversal over the Program Graph,
not to recursion in the Astropy case.

The production implementation now uses iterative Kosaraju traversal over the
existing forward and reverse hypergraph indexes. It does not build a duplicate
adjacency graph. Compact mutable cursor frames retain only a node ID, its
ordered edge-ID tuple, and edge/endpoint positions; this avoids one Python
generator and one materialized edge list for every node on a deep DFS stack.
Filtered edges, multi-source/multi-target edges, deterministic component
ordering, and the resulting SCC partition are preserved.

Long corridors also exposed downstream quadratic storage and premature caps:

- Path Class exploration now stores paths as persistent parent links and
  materializes node/edge tuples only when an observation is reached.
- SCC visit counters are copied only for cyclic SCCs; acyclic SCCs cannot be
  revisited in the condensation DAG and require no per-path counter.
- the default path and backward-slice budgets cover at least one state per
  Program Graph node, while explicit caller-provided limits remain strict;
- unused copied reverse paths were removed from entrypoint slice states;
- observation recovery uses one complete multi-source reachability traversal
  instead of a separate traversal per seed with a 5,000-node truncation.

Synthetic scalability results, measured after graph construction:

| Workload | Result | Wall | Added peak traced memory |
| --- | --- | ---: | ---: |
| iterative SCC, 100,000-node/99,999-edge acyclic chain | 100,000 SCCs | 3.188 s | 16.60 MiB |
| SCC topology plus Path Class, same chain | one 100,000-node path, not capped | 7.907 s | 16.61 MiB |

Verification includes a 20,001-node Path Class corridor (beyond the previous
default cap), a 2,500-node SCC chain (beyond Python's recursion depth), and a
filtered directed-hyperedge SCC partition test. The complete suite passes
(`71 passed`). Astropy's recorded recursive failure remains preserved and must
be rerun in a new run directory to measure the iterative implementation.
