# ReachPatch

ReachPatch is a graph-grounded, execution-backed controller for maintaining
and refining one repository patch. It compiles public evidence into semantic
hypotheses and quantified requirement-path obligations, builds a behavioral
Python program graph, binds every feasible path to executable observations and
causal repair cuts, derives challenges from both the graph and each actual
diff, and commits only safe updates with strict executed target progress.

The implementation follows `Paper/reachpatch_graph_grounded.tex`. It never
uses hidden tests, gold patches, patch selection, or model-reported correctness
as transition evidence.

The command surface is available through `reachpatch --help` after an editable
install, or directly with `python -m reachpatch.cli.main --help`.

## Run the Closed Loop

All runtime state is kept below the supplied run root. A public instance file
contains `instance_id`, `repository`, `base_commit`, `issue`, and optional
`visible_tests`:

```bash
.venv/bin/python -m reachpatch.cli.main run \
  --instance tests/fixtures/simple_instance.json \
  --run-root runs/example --max-revisions 10
```

The same production pipeline is available through `analyze`,
`build-requirements`, `build-program-graph`, `bind`, `generate-challenges`,
and `repair`. Inspect and recovery commands are:

```bash
.venv/bin/python -m reachpatch.cli.main verify-artifacts --run-root runs/example
.venv/bin/python -m reachpatch.cli.main report --run-root runs/example
.venv/bin/python -m reachpatch.cli.main export-patch --run-root runs/example
.venv/bin/python -m reachpatch.cli.main recover --run-root runs/example
```

The controller maintains one transactional working patch. Every accepted
transition has a checkpoint, paired `TraceBundle`, DICC closure and
`TransitionCertificate`; terminal sealing also records edit-retention ablation
and a `TerminalCertificate`. Unknown, unsupported, unstable and external
behavior remains an explicit frontier or non-PASS outcome.
