# Evidence-guided patch search

This implementation adapts expansion/evaluation separation, not HGM's agent
tree or its benchmark-success estimator. All state belongs to the existing
DynamicReachAvoidGraph; filesystem checkpoints are immutable snapshots.

## Decisions and executable interfaces

The September 15 graph-policy revision replaces checkpoint-local exhaustion
with `derive_frontier_actions` / `select_next_action`. Actions are views over
the same graph, including distinct untried hypothesis actions and bounded
source expansion. Exhaustion IDs exclude timing noise and survive checkpoint
metadata refresh. A missing-oracle region does not terminate other open regions.
Parent edges remain immutable patch provenance; they do not constrain comparable
candidate probes to siblings. Descendant credit affects expansion, never final
quality.

`compile_contract_obligations` binds each required facet to its exact command,
input recipe and comparator. `evaluate_validation_closure` requires that the
selected checkpoint itself has discharged its executable obligations. Child
source is refreshed and its challenge queue derived before paired execution,
so new obligations execute on clean, parent and child rather than inheriting a
parent PASS. Oracle gaps are independent per input partition. Preservation
inputs are not audited using another target input's return requirement.

`MechanismPrediction` nodes are OBLIGATION nodes in the unified graph. Predictions
are bound to actual executable input IDs and selected source cuts; locks have
separate preservation predictions. Typed trace records retain repository-relative
paths, AST anchors, source-version IDs and caller files. UNKNOWN branch successors
cannot become not-taken evidence. Alignment excludes other branches outside the
selected parent cut and conservatively rejects changed AST statement structure.
This remains an observable-change test, not proof that a proposed mechanism is
causally sufficient.

`CaseBudget` is operational accounting, not another graph. Compiler, recovery,
repair and execution share wall, model-call and token limits, with a final
validation reserve. Validation reuse requires explicit deterministic/isolation
attestation and an environment fingerprint; keys include source, clean source,
contract, interpreter, hashed environment and observation/trace adapters.
UNKNOWN/BLOCKED results are not reusable stable evidence.

1. `derive_validation_obligations(graph, checkpoint_id)` projects executable
   obligations before execution. Locks and trusted targets remain mandatory;
   checkpoint-specific challenges cannot leak into unrelated siblings.
   `controller._graph_checks` binds graph IDs to grounded ExecutableCheck
   records, records the selected batch, and fails closed on missing bindings.
2. `rank_causal_cuts` resolves the current requirement/failure, restricts scope
   to bound symbols and their bounded call/data-flow neighborhood, and ranks
   exact frame/line and observation-specific dynamic evidence before static
   evidence. Cut identity describes a location, not changing output text.
3. `build_distinct_repair_hypotheses` emits source-grounded mechanisms with
   predicted path changes, distinguishing input partitions and falsifiers.
   `record_hypothesis_feedback` records measured path/contract changes; it
   never treats a model prediction as observed progress.
4. `predicate_input_recipes` derives literal comparison boundaries from AST.
   Executable variants require an exact-input contract and evidence IDs;
   authority on the original input does not authorize adjacent inputs.
   `prioritize_sibling_challenges` ranks pending checks by sibling differences
   at their source branch and records affected checkpoints. Agreement is not
   an oracle, and exploratory inputs never certify.
5. `audit_return_oracles` runs bounded, process-local replacement-return
   interventions against already-passing Python probes when the authoritative
   requirement specifies return behavior. Surviving incompatible replacements
   expose an insufficient oracle. Mutation hits are required, setup failures
   and timeouts are inconclusive, and no baseline/test files are edited.
   Killing selected mutants is not proof of completeness. Audit gaps block
   certification and are sent to evidence recovery, not patch generation.
6. Expansion ordering uses measured checkpoint evidence plus descendant
   potential and untried hypotheses; final ordering uses only the candidate's
   own verified evidence. Child evidence propagates to ancestors as expansion
   information, never as inherited PASS or certification. Repeated stability
   runs do not count as independent progress.
   Numeric contract progress is measured against a stable clean execution that
   entered the target. The score records both the number of distinct improved
   obligations and normalized distance reduction, before diff-size tie-breaking;
   non-finite distances are not scored.
7. `select_frontier_action_from_graph` chooses mechanical repair, locked-success
   regression repair, target repair, evidence recovery or challenge repair from
   checkpoint execution records. The controller uses the selected check ID to
   choose its ActiveFailure. Oracle gaps force recovery instead of a speculative
   patch. Known untried mechanisms and exhausted attempts remain graph state.

`refresh_checkpoint_source` updates source spans and static dependencies in the
same graph after a patch or parent backtrack. Old source nodes are retired and
old static dependencies deactivated; historical execution evidence is retained.
Source refresh is bounded by admitted files, symbols, edges and elapsed time.

## Safety and evaluation

New probes retain command/cwd/environment/timeout and use the existing isolated
execution backend. An input recipe without an executable instantiation remains
explicitly exploratory. Unqualified ambiguous symbols are not mutation targets.
All target, lock and trusted preservation checks remain required to certify;
deferred/unknown evidence cannot be scored as success. Shared paired validation
must use identical check IDs and contracts on clean, parent and trial.

Verification includes queue causality, exact-input oracle authority, comparison
boundaries, mechanism falsifiers, sibling prioritization, ancestor propagation,
oracle survivor detection, and existing toy/controller tests. Resolved@1 gains
require sealed same-budget generation/selection ablations and official evaluation
after patch sealing; unit tests and internal REACHED do not establish them.

## Current adapter boundaries

- Automatic sibling inputs currently support a single explicit target call in
  a Python `-c` probe, positional/keyword arguments and bounded `len(parameter)`
  recipes. Indirect calls, splats, encoded/nested scripts and non-Python commands
  remain `NO_EXECUTABLE_PROBE_ADAPTER`, not fake executable challenges.
- The return-oracle audit supports ordinary Python functions/methods and Python
  `-c`, `-m`, or script commands. It rejects ambiguous bindings and unsupported
  flags. Audits cover supported typed return contracts and a narrow explicit
  empty-return clause, not arbitrary natural-language requirements.
- Existing line-arc tracing is approximate for same-line conditional expressions
  and complex guards. A path change without corresponding trusted observation
  evidence is not credited as a successful mechanism. Custom object truthiness
  and length methods are not invoked by the tracing summaries.
- Descendant scoring is a deterministic evidence heuristic, not an estimate of
  HGM's CMP or a theorem-backed probability of resolving an issue.
