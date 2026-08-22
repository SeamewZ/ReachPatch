# ReachPatch

ReachPatch is a graph-grounded, execution-backed controller for maintaining
and refining one repository patch. It compiles public evidence into quantified
requirement-path obligations, builds a behavioral
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
  --run-root runs/example --max-revisions 8
```

The only production execution path is `run` (the explicit `repair` command is
the same single working-patch loop). Runtime inspection is read-only:

```bash
.venv/bin/python -m reachpatch.cli.main status --run-root runs/example
.venv/bin/python -m reachpatch.cli.main export --run-root runs/example
.venv/bin/python -m reachpatch.cli.main verify --run-root runs/example
```

The controller maintains one transactional working patch. Every transition has
a checkpoint, paired `TraceBundle`, four-graph closure and a
`TransitionCertificate`. Unknown, unsupported, unstable and external behavior
remains an explicit frontier or non-PASS outcome.

## External Outcome Accounting

Resolved outcomes can be compared after a separate evaluator has assessed p0
and the final patch. This reporting-only command never feeds external results
back into DeepSeek, graph construction, Challenge selection, or Reach–Avoid
decisions:

```bash
.venv/bin/python -m reachpatch.cli.main assess-outcomes \
  --outcomes external-p0-final-outcomes.json
```

The JSON input is a list (or `{"outcomes": [...]}`) of objects containing
`instance_id`, `initial_resolved`, and `final_resolved`. The report includes
improved, regressed, unchanged, net-improvement, and their ratios.
