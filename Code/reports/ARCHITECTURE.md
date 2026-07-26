# ReachPatch Architecture

## Runtime Topology

```text
Instance(issue, repository, public tests)
        |
        v
Evidence -> SemanticGraph -> FrozenAssignment
                              |
                              v
                    RequirementGraph + path ledger
                              |
              PythonProgramGraph (AST/CFG/data/protocol)
                              |
                              v
                 BindingGraph (constrained product)
                              |
                              v
                 ChallengeGraph + InputRecipe + Oracle
                              |
                              v
      Worktree checkpoint -> paired TraceBundle execution
                              |
             actual diff -> DICC overlay and closure
                              |
               Counterexample -> causal RepairAction
                              |
                         Reach-Avoid gate
                 /                         \
             rollback                    commit
                 \                         /
                 persistent single working patch
                              |
                    seal / ablation / certificate
```

## Ownership and Data Flow

`ReachPatchController` owns the episode and is the only production caller that
can advance the incumbent checkpoint. Graph builders are deterministic and
return serializable graph records. `TraceExecutor` is isolated from graph
construction and receives only an `InputRecipe`, a repository snapshot and an
executable scenario. `ArtifactStore` is append-only from the application point
of view; state artifacts refer to the latest IDs while prior versions remain
available for replay.

The graph hash contract is explicit:

```text
Binding.requirement_graph_hash == Requirement.semantic_layer_hash()
Binding.program_graph_hash     == Program.program_hash()
Challenge hashes               == Requirement/Program/Binding hashes
WorkingPatch.working_tree_hash == tree_hash(checkpoint.snapshot_tree)
```

Any mismatch creates a recovery or materialization error instead of allowing a
stale graph to enter a transition.

Stable execution traces are merged into the trial Program Graph before an
accepted graph rebuild. Open-world witness partitions carry an explicit
universal-coverage frontier, so a finite sample cannot satisfy a quantified
Reach gate. Adapter facts are additive graph observations; adapter-detected
network/database surfaces remain hard external frontiers.

## State Machine

```text
SEMANTIC -> GRAPH_BUILD -> INCUMBENT_CLOSE
INCUMBENT_CLOSE -> CORE_SELECT -> INTENT_SELECT -> GENERATOR_REVISE
GENERATOR_REVISE -> DIFF_RECONCILE -> DICC_VALIDATE -> TRANSITION_GATE
TRANSITION_GATE -> COUNTEREXAMPLE_FEEDBACK -> INCUMBENT_CLOSE
INCUMBENT_CLOSE -> ABLATE -> INCUMBENT_CLOSE -> SEALED
```

Root recovery branches from core/feedback phases and returns to graph closure,
or records a named terminal classification. Illegal transitions and stale phase
expectations raise immediately.

## Transaction Boundary

There is exactly one `active.json` lease. A trial is copied from the accepted
checkpoint, edited in isolation, and either moved atomically into a new
checkpoint directory or removed after hash-verified rollback. The controller
never keeps a rejected tree as a candidate. Edit-retention ablation uses the
same transaction API and can only publish a candidate after full recertification.

## Artifact Layout

```text
run/
  run_manifest.json
  artifacts/{objects, index.json, journal.jsonl}
  worktrees/checkpoints/<checkpoint>/tree
  worktrees/transaction/active.json
  worktrees/receipts/*.json
  tmp/
  final_patch.diff
  terminal_certificate.json
  reports/{run_report.json, run_report.md}
```

All run roots are checked against the configured implementation root. Export
also rejects destinations outside the run root.
