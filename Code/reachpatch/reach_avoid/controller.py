"""Execution-driven Reach-Avoid production controller."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import os
import re
import subprocess
import time
import urllib.error
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from reachpatch.execution import (
    copy_source_tree, diff_between, discard_ephemeral_tree,
    register_runtime_root, run_mechanical_checks, tree_hash,
)
from reachpatch.execution.checks import execute_check
from reachpatch.execution.case_budget import CaseBudget, CaseBudgetExhausted, BudgetedTransport, active_case_budget
from reachpatch.execution.validation_cache import validation_cache_key
from reachpatch.execution.oracle_audit import audit_return_oracles, uncovered_oracle_gaps
from reachpatch.execution.discriminating_probes import execute_sibling_probes
from reachpatch.reach_avoid.graph_policy import (
    compile_contract_obligations, evaluate_validation_closure, derive_frontier_actions,
    select_next_action, exhaust_action, select_comparable_checkpoints, record_patch_transposition, challenge_lifecycle_metrics,
)
from reachpatch.execution.target_recovery import (
    TargetRecoveryConfig, TargetRecoveryResult, materialize_diff_checks,
    recover_target_checks,
)
from reachpatch.models.base import canonical_json, content_hash, stable_id
from reachpatch.models.core import Instance
from reachpatch.models.evidence import PublicEvidence, SourceHint, public_evidence_from_instance
from reachpatch.models.execution import (
    ActiveFailureKind, CheckExecution, CheckRole, CheckStatus, ExecutableCheck, FailureHistory,
    GeneratorSession, LockedCheck, ReachAvoidPhase, ReachAvoidState,
    StateCheckpoint, TerminalResult, TransitionCertificate,
    TransitionDecision,
)
from reachpatch.reach_avoid.active_failure import select_active_failure
from reachpatch.reach_avoid.dynamic_reach_avoid_graph import (
    DynamicGraphBudget, DynamicReachAvoidGraph, GraphNodeKind, GraphEdgeKind, seed_dynamic_graph,
    update_graph_from_execution, rank_causal_cuts, build_distinct_repair_hypotheses,
    RepairHypothesis, CheckpointState, derive_validation_obligations,
    materialize_graph_guided_challenges, update_graph_from_recovery, SearchScore,
    SearchBudget, expand_dynamic_graph_frontier,
    update_graph_from_challenges,
    register_validation_checks, record_hypothesis_feedback,
    refresh_checkpoint_source,
    select_frontier_action_from_graph,
)
from reachpatch.reach_avoid.execution_checkpoint import (
    ExecutionCheckpointStore, restore_parent_working_checkpoint,
    select_final_checkpoint, update_best_checkpoint, update_safe_checkpoint,
    update_working_checkpoint,
)
from reachpatch.reach_avoid.execution_transition import (
    all_reach_conditions_pass, compute_execution_atomic_progress,
    compute_mechanical_atomic_progress, decide_transition,
)
from reachpatch.repair.execution_objective import (
    InitialPatchObjective, RepairObjective,
    compile_execution_repair_objective,
)
from reachpatch.repair.initial_agent import InitialPatchAgent
from reachpatch.requirement_graph.compiler import compile_goal_contracts
from .evidence_context import (
    claim_evidence_action, efficiency_report, initial_source_context,
    resolve_checkpoint_questions,
)

from .repair_player import RepairPlayer


_DOTTED_NOISE = {"tests", "test", "element", "class", "module", "python", "should", "must"}


def _issue_terms(text: str) -> set[str]:
    """Return meaningful issue terms used for source-definition ranking.

    Recovery must be able to find an entry point when an issue does not name a
    Python symbol explicitly (for example, ``Add check to ensure
    max_length fits ...``).  This is deliberately a small lexical index, not
    a repository-wide semantic search: punctuation/casing are normalized and
    generic prose words are ignored so a source hint remains auditable.
    """
    stop = _DOTTED_NOISE | {
        "add", "ensure", "check", "checks", "currently", "there", "is",
        "are", "be", "been", "being", "would", "very", "helpful", "often",
        "mistake", "noticed", "until", "attempt", "made", "with", "from",
        "for", "the", "a", "an", "to", "of", "in", "on", "and", "or",
        "when", "that", "this", "should", "must", "expected", "actual",
        "behavior", "behaviour", "issue", "description", "support", "allow",
        "return", "returns", "value", "values", "result", "results", "object",
        "objects", "method", "function", "api", "implementation", "longest",
        "specific", "case", "cases", "zero", "total", "including", "each",
        "different", "same", "new", "old", "current", "does", "not", "no",
    }
    terms: set[str] = set()
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.casefold()):
        if raw in stop or len(raw) < 3:
            continue
        terms.add(raw)
        terms.update(part for part in raw.split("_") if len(part) >= 3 and part not in stop)
    return terms


def _rank_source_hint_candidates(
    repo_root: Path,
    issue_text: str,
    existing_symbols: tuple[str, ...],
    *,
    limit: int = 96,
) -> tuple[tuple[str, str, int, int, str, tuple[str, ...], str], ...]:
    """Find source definitions related to issue vocabulary.

    Explicit symbols are always handled by the fast ``rg`` path below.  This
    secondary index only runs when those symbols produced no definitions and
    ranks function/class spans by overlap with issue terms.  A minimum score
    of two (or a distinctive underscored name) prevents generic helpers from
    becoming target hints.
    """
    terms = _issue_terms(issue_text)
    exact_identifiers = {
        raw.casefold()
        for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+", issue_text)
    }
    if not terms:
        return ()
    results: list[tuple[int, str, str, int, int, str]] = []
    started = time.monotonic()
    for path in sorted(repo_root.rglob("*.py")):
        if time.monotonic() - started > 20:
            break
        if any(part in {".git", "tests", "test", "__pycache__", "build", "dist", "artifacts", "harness"} for part in path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=path.name)
        except (OSError, SyntaxError):
            continue
        relative = path.relative_to(repo_root).as_posix()
        # Configuration options and protocol names are often attributes (for
        # example ``autodoc_typehints``), so they have no AST definition of
        # their own.  Attach a textual hit to its smallest enclosing project
        # definition; this gives the initial graph a real implementation span
        # while retaining the exact evidence-bearing token.
        definitions = tuple(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        for token in sorted(
            (item for item in terms if "_" in item or len(item) >= 10),
            key=lambda item: (-len(item), item),
        ):
            for match in re.finditer(rf"\b{re.escape(token)}\b", source, re.I):
                line = source.count("\n", 0, match.start()) + 1
                owners = tuple(
                    node for node in definitions
                    if int(node.lineno) <= line <= int(getattr(node, "end_lineno", node.lineno))
                )
                owner = min(owners, key=lambda node: (int(getattr(node, "end_lineno", node.lineno)) - int(node.lineno), int(node.lineno)), default=None)
                if owner is None:
                    continue
                span = ast.get_source_segment(source, owner) or ""
                score = 4 + (2 if token in relative.casefold() else 0)
                if token in exact_identifiers:
                    score += 20
                results.append((score, relative, owner.name, int(owner.lineno), int(getattr(owner, "end_lineno", owner.lineno)), span))
                break
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            name = node.name.casefold().strip("_")
            name_terms = set(part for part in re.findall(r"[a-z0-9]+", name) if len(part) >= 3)
            span = ast.get_source_segment(source, node) or ""
            span_terms = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", span.casefold()))
            path_terms = set(re.findall(r"[A-Za-z0-9]+", relative.casefold()))
            # A path component such as ``ext/autodoc`` is strong evidence
            # when the issue names ``autodoc_typehints``.  Without this
            # signal, large documentation classes with generic prose often
            # outrank the actual implementation module.
            path_overlap = len(path_terms & terms)
            # Score the candidate's own source span, not the whole file.  A
            # single configuration option appears in several documenter
            # classes; counting it globally gave every method in the module
            # the same score and selected the first (unrelated) class.
            exact_hits = sum(span.casefold().count(token) for token in exact_identifiers)
            score = 2 * len(name_terms & terms) + len(span_terms & terms) + 3 * path_overlap + 20 * exact_hits
            if "_" in node.name and name_terms & terms:
                score += 1
            if score < 2:
                continue
            results.append((score, relative, node.name, int(node.lineno), int(getattr(node, "end_lineno", node.lineno)), span))
    results.sort(key=lambda item: (-item[0], item[1], item[3], item[2]))
    evidence_id = stable_id("issue-source-evidence", issue_text)
    return tuple(
        (name, relative, start, end, span,
         (evidence_id,),
         f"AST definition ranked by issue/source vocabulary overlap (score={score})")
        for score, relative, name, start, end, span in results[:limit]
    )


def build_requirement_source_hints(
    repo_root: Path,
    issue_text: str,
    public_checks: tuple[ExecutableCheck, ...] | list[ExecutableCheck] | Any,
) -> tuple[SourceHint, ...]:
    """Build bounded source candidates without requiring a compiled target.

    Hints are intentionally evidence-bearing records.  Dotted prose tokens
    such as ``tests.element.Class`` are filtered until a real definition or
    executable check confirms the terminal symbol.
    """
    repo_root = Path(repo_root).resolve()
    checks = tuple(getattr(public_checks, "checks", public_checks or ()))
    candidates: list[tuple[str, str, int, int, str, tuple[str, ...], str]] = []
    issue_evidence_id = stable_id("issue-source-evidence", issue_text)
    symbols: list[tuple[str, tuple[str, ...]]] = []
    symbol_text = issue_text.replace("\r\n", "\n").replace("\r", "\n")
    title = symbol_text.splitlines()[0] if symbol_text.splitlines() else symbol_text

    # Identifiers appearing only in an illustrative fenced code block are
    # witnesses, not evidence that the repository defines that symbol.  A
    # common example is an issue showing ``foo`` in a reproduction snippet;
    # treating it as the target makes the graph chase test fixtures instead of
    # the implementation named by the issue title.  Keep such a token only
    # when it also occurs in normative prose outside a fence.
    fenced_ranges: list[tuple[int, int]] = []
    fence_start: int | None = None
    for fence in re.finditer(r"(?m)^\s*```[^\n]*(?:\n|$)", symbol_text):
        if fence_start is None:
            fence_start = fence.end()
        else:
            fenced_ranges.append((fence_start, fence.start()))
            fence_start = None

    def _in_fence(position: int) -> bool:
        return any(start <= position < end for start, end in fenced_ranges)

    def _outside_fence_tokens(pattern: str, text: str = symbol_text) -> tuple[str, ...]:
        return tuple(
            match.group(1) for match in re.finditer(pattern, text)
            if not _in_fence(match.start())
        )

    for token in (
        *_outside_fence_tokens(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`"),
        *_outside_fence_tokens(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\("),
        *re.findall(r"\b(?:function|method|class|API)\s+([A-Za-z_]\w*)\b", title, re.I),
        *re.findall(r"\b(?:[A-Za-z_]\w*_[A-Za-z_]\w*|[A-Z][A-Za-z0-9]+)\b", title),
    ):
        terminal = token.rsplit(".", 1)[-1]
        if terminal.casefold() not in _DOTTED_NOISE:
            symbols.append((terminal, (issue_evidence_id,)))
    for match in re.finditer(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b", symbol_text):
        if _in_fence(match.start()):
            continue
        token = match.group(0)
        parts = token.split(".")
        if parts[0].casefold() in _DOTTED_NOISE or parts[-1].casefold() in _DOTTED_NOISE:
            continue
        symbols.append((parts[-1], (issue_evidence_id,)))
    for match in re.finditer(r"(?im)^.*(?:traceback|file \"[^\"]+\"|in [A-Za-z_]\w*).*?$", symbol_text):
        if _in_fence(match.start()):
            continue
        line = match.group(0)
        frame = re.search(r"in ([A-Za-z_]\w*)", line)
        if frame and frame.group(1).casefold() not in _DOTTED_NOISE:
            symbols.append((frame.group(1), (issue_evidence_id,)))
    for check in checks:
        evidence_ids = tuple(str(item) for item in getattr(check, "evidence_ids", getattr(check, "source_evidence_ids", ())))
        for symbol in tuple(getattr(check, "target_symbols", ())) + tuple(getattr(check, "symbol_references", ())):
            terminal = str(symbol).rsplit(".", 1)[-1]
            if terminal and terminal.casefold() not in _DOTTED_NOISE:
                symbols.append((terminal, evidence_ids or (issue_evidence_id,)))
    wanted = tuple(dict.fromkeys(item[0] for item in symbols if re.fullmatch(r"[A-Za-z_]\w*", item[0])))
    started = time.monotonic()
    scanned = 0
    candidate_paths: tuple[Path, ...] = ()
    if wanted:
        definition_pattern = r"^(?:\s*)(?:async\s+def|def|class)\s+(?:" + "|".join(map(re.escape, wanted)) + r")\b"
        try:
            completed = subprocess.run(
                ["rg", "-l", "-g", "*.py", "-g", "!**/.git/**", definition_pattern, str(repo_root)],
                capture_output=True, text=True, check=False, timeout=20,
            )
            candidate_paths = tuple(Path(item) for item in completed.stdout.splitlines()) if completed.returncode in {0, 1} else ()
        except (OSError, subprocess.TimeoutExpired):
            candidate_paths = ()
    # If no exact definition is named, use a bounded lexical source index to
    # break the requirement-compiler/target-recovery deadlock.  The ranked
    # candidates are still SourceHint records and never grant Oracle authority
    # by themselves.
    # Always add a bounded lexical pass. Exact token matches often point at a
    # generic ``choices``/``value`` helper while the actual entry point is a
    # nearby method whose name combines the issue terms (for example
    # ``_check_max_length_choices``). The downstream scorer deduplicates and
    # aligns these hints with the normative span.
    fuzzy_candidates = _rank_source_hint_candidates(repo_root, issue_text, wanted)
    for item in fuzzy_candidates:
        candidates.append(item)
    scan_paths = tuple(Path(item) for item in candidate_paths)
    for path in sorted(scan_paths):
        relative = path.relative_to(repo_root).as_posix()
        if any(part in {".git", "artifacts", "official_harness", "harness", "__pycache__"} for part in path.parts):
            continue
        scanned += 1
        if scanned > 2000 or time.monotonic() - started > 30.0:
            break
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=relative)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if wanted and node.name not in wanted:
                continue
            line_start = int(node.lineno)
            line_end = int(getattr(node, "end_lineno", node.lineno))
            span = ast.get_source_segment(source, node) or ""
            evidence = tuple(dict.fromkeys(
                evidence_id for symbol, evidence_ids in symbols
                if symbol.casefold() == node.name.casefold()
                for evidence_id in evidence_ids if evidence_id
            ))
            candidates.append((node.name, relative, line_start, line_end, span, evidence, "AST definition matching issue/public symbol"))
    # Keep explicit issue/public definitions first, then preserve the score
    # order produced by the lexical index.  Sorting fuzzy candidates only by
    # symbol name used to put documentation examples (for example
    # ``ExampleClass``) ahead of the implementation named by the issue.  The
    # resulting graph contained plausible but unrelated source spans and the
    # initial generator could spend its whole turn budget reading them.  The
    # score is part of the auditable reason string, so recover it
    # deterministically without adding a second hidden ranking structure.
    def _hint_order(item: tuple[str, str, int, int, str, tuple[str, ...], str]) -> tuple[int, int, str, int, str]:
        reason = item[-1]
        fuzzy = "ranked by issue/source vocabulary" in reason
        score_match = re.search(r"score=(\d+)", reason)
        score = int(score_match.group(1)) if score_match else 0
        # Exact definitions also need lexical ranking: generic helpers such as
        # ``value``/``choices`` frequently match before the composite target
        # named by the issue.  Keep evidence provenance as the primary tier,
        # then prefer identifiers sharing distinctive issue terms.
        name_terms = set(re.findall(r"[A-Za-z0-9]+", item[0].casefold()))
        lexical = len(name_terms & _issue_terms(issue_text))
        generic_penalty = int(item[0].casefold() in {"value", "get", "model", "choices", "field", "check", "before", "g"})
        exact_score = lexical * 2 - generic_penalty
        return (1 if fuzzy else 0, -(score if fuzzy else exact_score), item[1], item[2], item[0].casefold())

    candidates.sort(key=_hint_order)
    hints: list[SourceHint] = []
    for symbol, file, start, end, span, evidence, reason in candidates:
        hints.append(SourceHint(
            hint_id=stable_id("source-hint", symbol, file, start, end),
            symbol=symbol, file=file, line_start=start, line_end=end,
            source_span=span[:12000], evidence_span_ids=tuple(evidence),
            reason=reason, authority="B" if evidence else "PROVISIONAL",
        ))
    return tuple({item.hint_id: item for item in hints}.values())[:128]


def incremental_mechanism_hash(incremental_diff: str) -> str:
    return stable_id("generator-incremental-diff", incremental_diff)


@dataclass(frozen=True, slots=True)
class ReachAvoidConfig:
    max_real_patch_revisions: int = 8
    initial_generator_attempts: int = 1
    max_no_progress_generator_attempts: int = 2
    execution_budget_seconds: float = 3600.0
    target_recovery_attempts: int = 1
    target_recovery_max_probes: int = 6
    target_recovery_stability_runs: int = 2
    # The fixed-interaction arm intentionally performs one unconditional
    # post-P0 recovery dialogue.  Give that ablation its own bounded turn
    # budget so it cannot consume the case-wide patch-search allowance.
    fixed_recovery_max_agent_turns: int = 6
    # Independent executable obligations use isolated subprocesses.  A small
    # bounded pool preserves every paired observation while avoiding a
    # validation wall-time explosion on repositories with slow test startup.
    validation_workers: int = 2
    graph_budget: DynamicGraphBudget = field(default_factory=DynamicGraphBudget)
    search_budget: SearchBudget = field(default_factory=SearchBudget)
    max_case_model_calls: int = 160
    max_case_tokens: int = 1_000_000
    final_validation_reserve_seconds: float = 30.0
    evidence_reuse_enabled: bool = True
    demand_driven_interaction_enabled: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.max_real_patch_revisions <= 8:
            raise ValueError("max_real_patch_revisions must be between 1 and 8")
        if self.max_no_progress_generator_attempts < 1:
            raise ValueError("max_no_progress_generator_attempts must be positive")
        if self.initial_generator_attempts < 1:
            raise ValueError("initial_generator_attempts must be positive")
        if self.execution_budget_seconds <= 0:
            raise ValueError("execution_budget_seconds must be positive")
        if self.max_case_model_calls < 1 or self.max_case_tokens < 1:
            raise ValueError("case model-call and token budgets must be positive")
        if self.final_validation_reserve_seconds < 0:
            raise ValueError("final validation reserve must be nonnegative")
        if self.target_recovery_max_probes < 1:
            raise ValueError("target_recovery_max_probes must be positive")
        if self.target_recovery_stability_runs < 2:
            raise ValueError("target_recovery_stability_runs must be at least two")
        if self.fixed_recovery_max_agent_turns < 1:
            raise ValueError("fixed_recovery_max_agent_turns must be positive")
        if self.validation_workers < 1:
            raise ValueError("validation_workers must be positive")


@dataclass(slots=True)
class _RunContext:
    instance: Instance
    public_evidence: PublicEvidence
    store: ExecutionCheckpointStore
    p0_patch_hash: str = ""


class ReachAvoidController:
    def __init__(self, repair_player: RepairPlayer, config: ReachAvoidConfig | None = None) -> None:
        self.repair_player = repair_player
        self.initial_patch_agent = InitialPatchAgent(repair_player)
        self.config = config or ReachAvoidConfig()
        self._contexts: dict[str, _RunContext] = {}

    @staticmethod
    def _run_root(instance: Instance, run_root: str | Path | None) -> Path:
        root = (
            Path(run_root).resolve() if run_root is not None else
            Path.cwd() / "runs" / stable_id(
                "execution-run", instance.instance_id, instance.base_commit,
                time.time_ns(),
            )
        )
        root.mkdir(parents=True, exist_ok=False)
        return root

    @staticmethod
    def _blockers(result: Any) -> tuple[str, ...]:
        names = tuple(
            f"{item.file}:{item.line}:{item.name}"
            for item in getattr(result, "undefined_name_findings", ())
            if getattr(item, "severity", "BLOCKER") == "BLOCKER"
        )
        return tuple(dict.fromkeys((
            *names,
            *(str(item) for item in getattr(result, "failure_reasons", ())),
        )))

    @classmethod
    def _applicable(cls, result: Any) -> bool:
        if getattr(result, "forbidden_edit", False) or getattr(result, "oracle_contamination", False):
            return False
        return not any(
            token in str(reason).casefold()
            for reason in getattr(result, "failure_reasons", ())
            for token in ("syntax error", "patch apply", "malformed diff")
        )

    @staticmethod
    def _observation_hash(execution: CheckExecution) -> str:
        return stable_id(
            "check-observation", execution.check_id,
            execution.status, execution.semantic_signature,
        )

    def _checkpoint(
        self,
        state: ReachAvoidState,
        tree: Path,
        *,
        parent: StateCheckpoint | None,
        status: str,
        mechanical: Any,
        targets: tuple[CheckExecution, ...],
        preservations: tuple[CheckExecution, ...],
        challenges: tuple[CheckExecution, ...] = (),
        locked_checks: tuple[LockedCheck, ...] | None = None,
        final_eligible: bool = False,
    ) -> StateCheckpoint:
        actual = diff_between(state.clean_snapshot, tree)
        checkpoint_id = stable_id(
            "execution-checkpoint", state.run_id, actual.patch_hash,
            state.revision_count, status,
            parent.checkpoint_id if parent is not None else "root",
        )
        snapshot = state.run_root / "execution_checkpoints" / checkpoint_id / "working_tree"
        checkpoint = StateCheckpoint(
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent.checkpoint_id if parent else None,
            snapshot_tree=str(snapshot), patch_hash=actual.patch_hash,
            cumulative_diff=actual.canonical_diff, status=status,
            revision=state.revision_count,
            patch_is_applicable=self._applicable(mechanical),
            repository_corrupted=any(
                "corrupt" in str(reason).casefold()
                for reason in getattr(mechanical, "failure_reasons", ())
            ),
            forbidden_path_changed=bool(getattr(mechanical, "forbidden_edit", False)),
            final_eligible=final_eligible,
            mechanical_result_hash=content_hash(mechanical),
            mechanical_blockers=self._blockers(mechanical),
            target_observation_hashes={
                item.check_id: self._observation_hash(item) for item in targets
            },
            preservation_observation_hashes={
                item.check_id: self._observation_hash(item) for item in preservations
            },
            challenge_observation_hashes={
                item.check_id: self._observation_hash(item) for item in challenges
            },
            locked_checks=locked_checks if locked_checks is not None else state.locked_checks,
            active_failure=state.active_failure,
            dynamic_failure_graph_hash=(
                state.dynamic_failure_graph.digest()
                if state.dynamic_failure_graph is not None else None
            ),
            working_tree_hash=tree_hash(tree),
            search_score=SearchScore(
                patch_applicable=int(self._applicable(mechanical)),
                fatal_mechanical_free=int(bool(getattr(mechanical, "passed", False)) and not self._blockers(mechanical)),
                locked_target_pass_count=sum(
                    item.status is CheckStatus.PASS and item.stable
                    and item.check_id in {
                        lock.check_id for lock in (locked_checks if locked_checks is not None else state.locked_checks)
                        if str(getattr(getattr(lock.check, "role", None), "value", getattr(lock.check, "role", ""))).upper() == "TARGET"
                    }
                    for item in targets
                ),
                stable_target_pass_count=sum(item.status is CheckStatus.PASS and item.stable for item in targets),
                target_stage_progress_count=sum(
                    int(getattr(item, "failure_stage", 0) or 0)
                    for item in targets if item.stable
                ),
                contract_distance_reduction_count=self._distance_reduction_count(state, targets),
                contract_distance_reduction_amount=self._distance_reduction_count(state, targets, amount=True),
                closed_counterexample_count=sum(item.status is CheckStatus.PASS and item.stable for item in challenges),
                negative_preservation_regression_count=-sum(item.status is CheckStatus.FAIL and item.stable for item in preservations),
                executable_challenge_coverage=sum(item.stable for item in challenges),
                negative_diff_size=-len(actual.canonical_diff),
            ).key(),
            certified=(status == "CERTIFIED"),
            confirmed_preservation_regression=any(
                item.stable and item.status is CheckStatus.FAIL
                and str(getattr(item, "authority", "")).upper() in {"A", "B", "C"}
                for item in preservations
            ),
        )
        saved = self._contexts[state.run_id].store.save(
            checkpoint, tree, mechanical=mechanical,
            target_results=targets, preservation_results=preservations,
            challenge_results=challenges,
        )
        state.checkpoint_history[saved.checkpoint_id] = saved
        graph = state.dynamic_failure_graph
        if isinstance(graph, DynamicReachAvoidGraph):
            parent_node = graph.nodes.get(parent.checkpoint_id) if parent else None
            graph.register_checkpoint(CheckpointState(
                saved.checkpoint_id, saved.parent_checkpoint_id, saved.cumulative_diff, saved.patch_hash,
                depth=int(parent_node.metadata.get("depth", 0)) + 1 if parent_node else 0,
                status=status, search_score=saved.search_score,
                observation_ids=tuple(self._observation_hash(item) for item in (*targets, *preservations, *challenges)),
                mechanical_blockers=saved.mechanical_blockers,
                target_results={item.check_id: item.to_dict() for item in targets},
                preservation_results={item.check_id: item.to_dict() for item in preservations},
                challenge_results={item.check_id: item.to_dict() for item in challenges},
                locked_successes=tuple(lock.check_id for lock in saved.locked_checks),
                certified=saved.certified, final_eligible=saved.final_eligible,
            ))
            checkpoint_node = graph.nodes[saved.checkpoint_id]
            graph.nodes[saved.checkpoint_id] = replace(checkpoint_node, metadata={
                **checkpoint_node.metadata, "source_version_id": saved.working_tree_hash})
        return saved

    @staticmethod
    def _distance_reduction_count(state: ReachAvoidState, targets: tuple[CheckExecution, ...], *, amount: bool = False) -> int | float:
        baseline = {}
        for node in state.dynamic_failure_graph.nodes.values():
            if node.kind is GraphNodeKind.OBSERVATION and node.metadata.get("phase") == "CLEAN":
                execution = node.metadata.get("execution", {})
                if (execution.get("stable") and execution.get("entered_target_code") is True
                    and isinstance(execution.get("distance"), (int, float)) and math.isfinite(execution["distance"])):
                    baseline[execution.get("check_id")] = execution["distance"]
        progressed = [item for item in {item.check_id: item for item in targets}.values()
                      if item.stable and item.authority in {"A", "B", "C"}
                      and item.entered_target_code is True
                      and isinstance(item.distance, (int, float)) and math.isfinite(item.distance) and item.check_id in baseline
                      and item.distance < baseline[item.check_id]]
        if amount:
            return sum((baseline[item.check_id] - item.distance) / max(1.0, abs(baseline[item.check_id]))
                       for item in progressed)
        return len(progressed)

    def _refresh_evaluated_checkpoint(self, state: ReachAvoidState, mechanical: Any,
                                      targets: tuple[CheckExecution, ...],
                                      preservations: tuple[CheckExecution, ...],
                                      challenges: tuple[CheckExecution, ...]) -> None:
        """P0 and revisions acquire scores from the same executed evidence."""
        current = state.working_checkpoint
        locked_targets = {lock.check_id for lock in state.locked_checks if str(lock.check.role) == "TARGET"}
        score = SearchScore(
            patch_applicable=int(self._applicable(mechanical)),
            fatal_mechanical_free=int(bool(getattr(mechanical, "passed", False)) and not self._blockers(mechanical)),
            locked_target_pass_count=sum(item.stable and item.status is CheckStatus.PASS and item.check_id in locked_targets for item in targets),
            stable_target_pass_count=sum(item.stable and item.status is CheckStatus.PASS for item in targets),
            target_stage_progress_count=sum(int(item.failure_stage or 0) for item in targets if item.stable),
            contract_distance_reduction_count=self._distance_reduction_count(state, targets),
            contract_distance_reduction_amount=self._distance_reduction_count(state, targets, amount=True),
            closed_counterexample_count=sum(item.stable and item.status is CheckStatus.PASS for item in challenges),
            negative_preservation_regression_count=-sum(item.stable and item.status is CheckStatus.FAIL for item in preservations),
            executable_challenge_coverage=sum(item.stable for item in challenges),
            negative_diff_size=-len(current.cumulative_diff),
        ).key()
        current = self._contexts[state.run_id].store.replace_metadata(replace(
            current, search_score=score,
            target_observation_hashes={item.check_id: self._observation_hash(item) for item in targets},
            preservation_observation_hashes={item.check_id: self._observation_hash(item) for item in preservations},
            challenge_observation_hashes={item.check_id: self._observation_hash(item) for item in challenges},
            confirmed_preservation_regression=any(item.stable and item.status is CheckStatus.FAIL
                and item.authority in {"A", "B", "C"} for item in preservations),
        ), execution_results={"target": targets, "preservation": preservations, "challenge": challenges})
        state.checkpoint_history[current.checkpoint_id] = current
        update_working_checkpoint(state, current)
        update_best_checkpoint(state, current)
        state.dynamic_failure_graph.update_checkpoint(current.checkpoint_id,
            search_score=score,
            observation_ids=tuple(self._observation_hash(item) for item in (*targets, *preservations, *challenges)),
            target_results={item.check_id: item.to_dict() for item in targets},
            preservation_results={item.check_id: item.to_dict() for item in preservations},
            challenge_results={item.check_id: item.to_dict() for item in challenges})

    @staticmethod
    def _execute_queue(
        tree: Path,
        checks: tuple[ExecutableCheck, ...],
        clean: Path,
        *,
        max_workers: int = 1,
    ) -> tuple[CheckExecution, ...]:
        def execute_one(check: ExecutableCheck) -> CheckExecution:
            return execute_check(tree, check, stability_runs=2, base_tree=clean)

        workers = min(max(1, int(max_workers)), len(checks))
        if workers <= 1:
            return tuple(execute_one(check) for check in checks)
        # Context variables carry the one case-wide execution budget.  Each
        # worker needs a distinct Context object, but all point to the same
        # budget instance so wall/execution accounting remains global.
        contexts = [copy_context() for _ in checks]
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="reachpatch-validation") as pool:
            futures = [
                pool.submit(context.run, execute_one, check)
                for context, check in zip(contexts, checks)
            ]
            # Consume in input order so graph observations and sealed logs are
            # deterministic even when subprocesses finish out of order.
            return tuple(future.result() for future in futures)

    @staticmethod
    def _split(
        results: tuple[CheckExecution, ...], state: ReachAvoidState,
    ) -> tuple[tuple[CheckExecution, ...], tuple[CheckExecution, ...], tuple[CheckExecution, ...]]:
        target_ids = {item.check_id for item in state.target_checks}
        preservation_ids = {item.check_id for item in state.preservation_checks}
        challenge_ids = {item.check_id for item in state.challenge_checks}
        return (
            tuple(item for item in results if item.check_id in target_ids),
            tuple(item for item in results if item.check_id in preservation_ids),
            tuple(item for item in results if item.check_id in challenge_ids),
        )

    @staticmethod
    def _checks(state: ReachAvoidState) -> tuple[ExecutableCheck, ...]:
        unique: dict[str, ExecutableCheck] = {}
        for check in (
            *state.target_checks,
            *state.preservation_checks,
            *state.challenge_checks,
            *(item.check for item in state.locked_checks),
        ):
            unique.setdefault(check.check_id, check)
        return tuple(unique.values())

    @staticmethod
    def _graph_checks(state: ReachAvoidState, checkpoint_id: str | None = None) -> tuple[ExecutableCheck, ...]:
        graph = state.dynamic_failure_graph
        handles = {check.check_id: check for check in ReachAvoidController._checks(state)}
        register_validation_checks(graph, tuple(handles.values()),
                                   locked_ids=tuple(lock.check_id for lock in state.locked_checks))
        batch = derive_validation_obligations(graph, checkpoint_id or state.working_checkpoint.checkpoint_id)
        ordered_ids = dict.fromkeys((*batch.mechanical_obligations, *batch.locked_success_obligations,
                                    *batch.target_obligations, *batch.preservation_obligations,
                                    *batch.challenge_obligations))
        # Missing bindings are programming/evidence errors; never silently
        # certify from an empty or stale queue.
        return tuple(handles[graph.nodes[node_id].metadata["check_id"]] for node_id in ordered_ids)

    def _derive_checkpoint_checks(self, state: ReachAvoidState, tree: Path, checkpoint_id: str,
                                   full_diff: str, observations: tuple[CheckExecution, ...]) -> tuple[ExecutableCheck, ...]:
        graph = state.dynamic_failure_graph
        refresh_checkpoint_source(graph, tree, full_diff)
        metadata = graph.nodes[checkpoint_id].metadata
        projection = CheckpointState(**{key: value for key, value in metadata.items() if key in CheckpointState.__dataclass_fields__})
        # Inspect every changed source region, not only the parent hypothesis's
        # old cut: a child can introduce another return/guard outside that cut.
        projection = replace(projection, causal_cut_ids=())
        cells = materialize_graph_guided_challenges(tree, graph, projection, observations)
        additions = self._checks_from_graph_challenges(state, cells, graph)
        state.challenge_checks = tuple({check.check_id: check for check in (*state.challenge_checks, *additions)}.values())
        return self._graph_checks(state, checkpoint_id)

    def _execute_graph_queue(self, state: ReachAvoidState, tree: Path,
                             checks: tuple[ExecutableCheck, ...], clean: Path,
                             *, phase: str) -> tuple[CheckExecution, ...]:
        budget = active_case_budget.get()
        collected: list[CheckExecution | None] = [None] * len(checks)
        cache_keys: list[str | None] = [None] * len(checks)
        misses: list[tuple[int, ExecutableCheck]] = []
        for index, check in enumerate(checks):
            cache_key = (
                validation_cache_key(tree, clean, check)
                if budget and self.config.evidence_reuse_enabled else None
            )
            cache_keys[index] = cache_key
            cached = budget.execution_cache.get(cache_key) if budget and cache_key else None
            if cached is not None:
                state.dynamic_failure_graph.record_update("VALIDATION_CACHE_HIT", cache_key=cache_key, check_id=check.check_id, phase=phase)
                collected[index] = cached
            else:
                misses.append((index, check))
        if misses:
            executed = self._execute_queue(
                tree, tuple(check for _, check in misses), clean,
                max_workers=self.config.validation_workers,
            )
            for (index, _), result in zip(misses, executed):
                collected[index] = result
                cache_key = cache_keys[index]
                if budget and cache_key and result.stable and result.status in {CheckStatus.PASS, CheckStatus.FAIL}:
                    budget.execution_cache[cache_key] = result
        if any(result is None for result in collected):
            raise RuntimeError("validation queue left an executable obligation without a result")
        results = tuple(result for result in collected if result is not None)
        graph = state.dynamic_failure_graph
        patch_hash = diff_between(clean, tree).patch_hash
        with (state.run_root / "validation_execution_log.jsonl").open("a", encoding="utf-8") as handle:
            for check, result in zip(checks, results):
                observation_id = stable_id("validation", patch_hash, phase, check.check_id, result.semantic_signature)
                executed_checkpoint = next((node.node_id for node in graph.nodes.values()
                    if node.kind is GraphNodeKind.CHECKPOINT and node.metadata.get("patch_hash") == patch_hash), None)
                graph.add_node(GraphNodeKind.OBSERVATION, node_id=observation_id,
                               status=str(result.status), metadata={"patch_hash": patch_hash,
                               "source_version_id": tree_hash(tree),
                               "checkpoint_id": executed_checkpoint,
                               "phase": phase, "check": check.to_dict(), "execution": result.to_dict()})
                graph.add_edge(GraphEdgeKind.TRACE_REACHES, stable_id("obligation", check.check_id), observation_id,
                               static_or_dynamic="dynamic", trace_ids=(observation_id,))
                handle.write(canonical_json({"observation_id": observation_id,
                    "checkpoint_id": executed_checkpoint,
                    "patch_hash": patch_hash, "phase": phase,
                    "check": check.to_dict(), "execution": result.to_dict()}) + "\n")
        return results

    @staticmethod
    def _checks_from_graph_challenges(
        state: ReachAvoidState,
        cells: tuple[Any, ...] | list[Any],
        graph: DynamicReachAvoidGraph,
    ) -> tuple[ExecutableCheck, ...]:
        """Project certifying graph challenge cells into the validation queue.

        A graph challenge with the same command as its source target is only a
        localization record; executing it again would not be a boundary
        challenge.  Only cells with an explicit adjacent command and a
        trusted typed oracle become ``ExecutableCheck`` records.  Exploratory
        cells remain in the graph artifact and never affect Reach
        certification.
        """
        existing_commands = {
            tuple(check.command)
            for check in (*state.target_checks, *state.preservation_checks)
        }
        result: list[ExecutableCheck] = []
        for cell in cells:
            if str(getattr(cell, "status", "")).upper() != "PENDING":
                continue
            authority = str(getattr(cell, "authority", "")).upper()
            command = tuple(str(part) for part in getattr(cell, "command", ()) or ())
            oracle = getattr(cell, "oracle", None)
            if authority not in {"A", "B", "C"} or not command or oracle is None:
                continue
            if command in existing_commands:
                continue
            branch = graph.nodes.get(str(getattr(cell, "source_branch_id", "")))
            if branch is None:
                continue
            bound = next(
                (
                    node for node in graph.nodes.values()
                    if node.kind is GraphNodeKind.OBLIGATION
                    and str(node.metadata.get("target_symbols", ()))
                    and str(branch.symbol or "").casefold() in {
                        str(symbol).rsplit(".", 1)[-1].casefold()
                        for symbol in node.metadata.get("target_symbols", ())
                    }
                ),
                None,
            )
            oracle_dict = oracle if isinstance(oracle, dict) else {}
            comparator = str(
                oracle_dict.get("comparator")
                or (bound.metadata.get("comparator") if bound is not None else "")
                or "EQUALS"
            )
            expected = oracle_dict.get(
                "expected",
                bound.metadata.get("expected") if bound is not None else None,
            )
            goal_id = (
                str(bound.metadata.get("goal_id"))
                if bound is not None and bound.metadata.get("goal_id") else None
            )
            evidence_ids = tuple((getattr(cell, "input_recipe", {}) or {}).get("evidence_ids", ()))
            result.append(ExecutableCheck(
                check_id=str(getattr(cell, "challenge_id", stable_id("graph-challenge-check", command))),
                goal_id=goal_id,
                role=CheckRole.CHALLENGE,
                authority=authority,
                command=command,
                cwd=str(bound.metadata.get("cwd", ".")) if bound is not None else ".",
                environment=tuple(bound.metadata.get("environment", ())) if bound is not None else (),
                timeout_seconds=float(bound.metadata.get("timeout_seconds", 120.0)) if bound is not None else 120.0,
                comparator=comparator,
                expected=expected,
                evidence_ids=evidence_ids,
                target_symbols=(str(branch.symbol),) if branch.symbol else (),
                input_recipe=getattr(cell, "input_recipe", None),
            ))
            existing_commands.add(command)
        return tuple(result)

    @staticmethod
    def _updated_locks(
        state: ReachAvoidState,
        results: tuple[CheckExecution, ...],
        patch_hash: str,
    ) -> tuple[LockedCheck, ...]:
        locks = {item.check_id: item for item in state.locked_checks}
        checks = {item.check_id: item for item in ReachAvoidController._checks(state)}
        for execution in results:
            check = checks.get(execution.check_id)
            if (
                check is None or not check.trusted or not execution.stable
                or execution.status is not CheckStatus.PASS
            ):
                continue
            locks.setdefault(execution.check_id, LockedCheck(
                check=check,
                passing_observation_hash=ReachAvoidController._observation_hash(execution),
                patch_hash_when_locked=patch_hash,
            ))
        return tuple(sorted(locks.values(), key=lambda item: item.check_id))

    def _objective(self, state: ReachAvoidState, active: Any) -> RepairObjective:
        objective = compile_execution_repair_objective(
            state, active, target_checks=state.target_checks,
            preservation_checks=state.preservation_checks,
            challenge_checks=state.challenge_checks,
            dynamic_failure_graph=state.dynamic_failure_graph,
        )
        state.current_repair_objective = objective
        return objective

    @staticmethod
    def _decision_reason(
        decision: TransitionDecision,
        progress: tuple[Any, ...],
        preservation_results: tuple[CheckExecution, ...],
        mechanical_after: Any,
    ) -> str:
        if decision is TransitionDecision.REACHED:
            return "all executable Reach conditions passed"
        reasons = tuple(
            item.reason for item in progress
            if item.strict_progress or item.partial_progress or item.regression
        )
        if reasons:
            return "; ".join(dict.fromkeys(reasons))
        if any(item.status is CheckStatus.FAIL for item in preservation_results):
            return "confirmed preservation regression remains repairable"
        if not mechanical_after.passed:
            return "mechanical blocker remains repairable"
        if decision is TransitionDecision.REJECT_TRIAL:
            return "trial is inapplicable, corrupted, duplicate, or stably worse"
        return "no verified progress and no proof that the trial is worse"

    def _certificate(
        self,
        state: ReachAvoidState,
        parent: StateCheckpoint,
        trial: StateCheckpoint,
        decision: TransitionDecision,
        active: Any,
        progress: tuple[Any, ...],
        clean_results: tuple[CheckExecution, ...],
        parent_results: tuple[CheckExecution, ...],
        trial_results: tuple[CheckExecution, ...],
        mechanical_after: Any,
        preservation_results: tuple[CheckExecution, ...],
        locked_before: tuple[LockedCheck, ...],
        locked_after: tuple[LockedCheck, ...],
    ) -> TransitionCertificate:
        observation_hashes = tuple(
            f"{phase}:{item.check_id}:{self._observation_hash(item)}"
            for phase, results in (
                ("clean", clean_results),
                ("parent", parent_results),
                ("trial", trial_results),
            )
            for item in results
        )
        certificate_id = stable_id(
            "execution-transition", parent.checkpoint_id,
            trial.checkpoint_id, decision,
        )
        result = parent if decision is TransitionDecision.REJECT_TRIAL else trial
        request_ids = tuple(
            str(item["request_id"])
            for item in state.generator_session.conversation
            if isinstance(item, dict) and item.get("request_id")
        )
        regressions = tuple(sorted(
            item.check_id for item in progress if item.regression
        ))
        return TransitionCertificate(
            certificate_id=certificate_id, case_id=state.instance_id,
            revision_index=state.revision_count,
            parent_checkpoint_id=parent.checkpoint_id,
            trial_checkpoint_id=trial.checkpoint_id,
            result_checkpoint_id=result.checkpoint_id,
            parent_patch_hash=parent.patch_hash,
            trial_patch_hash=trial.patch_hash,
            result_patch_hash=result.patch_hash,
            decision=decision, active_failure_id=active.failure_id,
            active_failure_kind=active.kind.value,
            exact_failure_command=active.command,
            check_ids=tuple(item.check_id for item in trial_results),
            observation_hashes=observation_hashes,
            atomic_progress={item.check_id: item for item in progress},
            mechanical_blockers_before=parent.mechanical_blockers,
            mechanical_blockers_after=trial.mechanical_blockers,
            locked_checks_before=tuple(item.check_id for item in locked_before),
            locked_checks_after=tuple(item.check_id for item in locked_after),
            regressions=regressions,
            dynamic_failure_graph_hash=(
                state.dynamic_failure_graph.digest()
                if state.dynamic_failure_graph is not None else None
            ),
            decision_reason=self._decision_reason(
                decision, progress, preservation_results, mechanical_after,
            ),
            model_request_ids=request_ids,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    @staticmethod
    def _persist_transition_evidence(
        state: ReachAvoidState,
        certificate: TransitionCertificate,
        clean_results: tuple[CheckExecution, ...],
        parent_results: tuple[CheckExecution, ...],
        trial_results: tuple[CheckExecution, ...],
    ) -> None:
        transitions = state.run_root / "transitions"
        observations = state.run_root / "transition_observations"
        transitions.mkdir(parents=True, exist_ok=True)
        observations.mkdir(parents=True, exist_ok=True)
        (transitions / f"{certificate.certificate_id}.json").write_text(
            canonical_json(certificate) + "\n", encoding="utf-8",
        )
        (observations / f"{certificate.certificate_id}.json").write_text(
            canonical_json({
                "clean": [item.to_dict() for item in clean_results],
                "parent": [item.to_dict() for item in parent_results],
                "trial": [item.to_dict() for item in trial_results],
            }) + "\n", encoding="utf-8",
        )

    def _output(
        self, state: ReachAvoidState, checkpoint: StateCheckpoint, status: str,
    ) -> TerminalResult:
        context = self._contexts[state.run_id]
        context.store.validate(checkpoint, None, clean_snapshot=state.clean_snapshot)
        context.store.validate_evidence(checkpoint, state.dynamic_failure_graph)
        state.phase = ReachAvoidPhase.SEALED
        state.termination_status = status
        output = state.run_root / "final.patch"
        output.write_text(checkpoint.cumulative_diff, encoding="utf-8")
        context = self._contexts[state.run_id]
        graph = state.dynamic_failure_graph
        if graph is not None:
            graph.metrics.update(challenge_lifecycle_metrics(graph))
            graph.metrics["final_differs_from_p0"] = int(
                bool(context.p0_patch_hash)
                and checkpoint.patch_hash != context.p0_patch_hash
            )
            graph.metrics["graph_recorded_action_count"] = sum(
                item.get("event") == "SELECT_FRONTIER_ACTION" for item in graph.update_log)
        graph_hash = graph.digest()
        (state.run_root / "evidence_manifest.json").write_text(canonical_json({
            "schema": "reachpatch-evidence-manifest-v1", "graph_hash": graph_hash,
            "graph_revision": graph.revision, "checkpoint_id": checkpoint.checkpoint_id,
            "patch_hash": checkpoint.patch_hash,
            "observation_references": {role: getattr(checkpoint, role + "_observation_hashes")
                                       for role in ("target", "preservation", "challenge")},
            "consistency": "VERIFIED",
        }) + "\n", encoding="utf-8")
        context.store.write_state(state)
        summary = {
            "schema": "reachpatch-execution-driven-v2",
            "status": status,
            "p0_patch_hash": context.p0_patch_hash,
            "final_patch_hash": checkpoint.patch_hash,
            "revision_count": state.revision_count,
            "transition_count": len(state.transition_history),
            "working_checkpoint": state.working_checkpoint.checkpoint_id,
            "safe_checkpoint": state.safe_checkpoint.checkpoint_id if state.safe_checkpoint else None,
            "best_checkpoint": state.best_checkpoint.checkpoint_id if state.best_checkpoint else None,
            "certified_checkpoint": state.certified_checkpoint.checkpoint_id if state.certified_checkpoint else None,
            "graph_hash": graph.digest() if graph is not None and hasattr(graph, "digest") else None,
            "graph_metrics": getattr(graph, "metrics", {}) if graph is not None else {},
            "graph_status": (
                "GRAPH_PRESENT_BUT_CAUSALLY_UNUSED"
                if graph is not None and all(int(getattr(graph, "metrics", {}).get(key, 0)) == 0 for key in (
                    "graph_localization_decision_count", "graph_generated_hypothesis_count",
                    "graph_generated_challenge_count", "graph_derived_validation_count",
                )) else "GRAPH_CAUSALLY_USED"
            ),
        }
        if graph is not None:
            (state.run_root / "dynamic_graph.json").write_text(canonical_json(graph) + "\n", encoding="utf-8")
            (state.run_root / "checkpoint_tree_view.json").write_text(canonical_json(graph.checkpoint_tree_view()) + "\n", encoding="utf-8")
            updates = [*getattr(graph, "update_log", ())]
            updates.append({"event": "FINAL", "metrics": graph.metrics, "graph_hash": graph.digest()})
            (state.run_root / "graph_updates.jsonl").write_text(
                "".join(canonical_json(item) + "\n" for item in updates), encoding="utf-8",
            )
        (state.run_root / "target_recovery.json").write_text(
            canonical_json(state.target_recovery or {"status": "UNRESOLVED"}) + "\n", encoding="utf-8",
        )
        transition_lines: list[str] = []
        observation_lines: list[str] = []
        for certificate in state.transition_history:
            transition_lines.append(canonical_json(certificate) + "\n")
            sidecar = state.run_root / "transition_observations" / f"{certificate.certificate_id}.json"
            if not sidecar.is_file():
                continue
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            for phase, executions in payload.items():
                for execution in executions:
                    observation_lines.append(canonical_json({
                        "certificate_id": certificate.certificate_id,
                        "parent_checkpoint_id": certificate.parent_checkpoint_id,
                        "trial_checkpoint_id": certificate.trial_checkpoint_id,
                        "phase": phase,
                        "execution": execution,
                    }) + "\n")
        (state.run_root / "transitions.jsonl").write_text(
            "".join(transition_lines), encoding="utf-8",
        )
        (state.run_root / "validation_observations.jsonl").write_text(
            ((state.run_root / "validation_execution_log.jsonl").read_text(encoding="utf-8")
             if (state.run_root / "validation_execution_log.jsonl").is_file()
             else "".join(observation_lines)), encoding="utf-8",
        )
        (state.run_root / "final_selection.json").write_text(
            canonical_json({
                "checkpoint_id": checkpoint.checkpoint_id,
                "patch_hash": checkpoint.patch_hash,
                "p0_patch_hash": context.p0_patch_hash,
                "status": status,
                "selection_kind": "CERTIFIED" if checkpoint.certified else "BEST_EFFORT_UNCERTIFIED",
                "search_score": checkpoint.search_score,
                "certified": checkpoint.certified,
                "graph_hash": graph.digest(),
                "graph_revision": graph.revision,
                "reason": "certified executable evidence" if checkpoint.certified else "best executable evidence without certification",
            }) + "\n", encoding="utf-8",
        )
        (state.run_root / "execution_summary.json").write_text(
            canonical_json(summary) + "\n", encoding="utf-8",
        )
        result = TerminalResult(
            state.instance_id, state.run_id, status, checkpoint.checkpoint_id,
            checkpoint.patch_hash, checkpoint.cumulative_diff, str(output),
        )
        (state.run_root / "terminal.json").write_text(
            canonical_json({"schema": "reachpatch-execution-driven-v2", "result": result}) + "\n",
            encoding="utf-8",
        )
        return result

    def _run_execution_driven(
        self, instance: Instance, *, run_root: str | Path | None = None,
    ) -> TerminalResult:
        started = time.monotonic()
        repository = instance.repository_path()
        root = self._run_root(instance, run_root)
        register_runtime_root(root)
        # Snapshot the exact clean tree before compiling any goal contracts.
        clean = root / "clean"
        copy_source_tree(
            repository, clean,
            exclude_paths=(root,) if root.is_relative_to(repository) else (),
        )
        public = public_evidence_from_instance(
            instance.issue, instance.visible_tests,
            instance.public_metadata, clean,
        )
        transport = getattr(
            getattr(self.repair_player, "generator_agent", None),
            "transport", None,
        )
        source_hints = build_requirement_source_hints(clean, instance.issue, public.checks)
        # No semantic-model call on the default path. Retain deterministic
        # alignment of both issue spans and public executable contracts.
        goals = compile_goal_contracts(instance.issue, public, source_hints, None, root)
        # ``_compile_goals_with_tool`` persists its raw protocol artifact
        # before deterministic fallback/alignment is merged. Persist the
        # actual production goals separately so sealed runs are auditable.
        # ``goal_contracts.json`` is the authoritative production input for
        # recovery/search metrics. The compiler's raw protocol attempts remain
        # preserved in ``requirement_compilation.json``.
        (root / "goal_contracts.json").write_text(
            canonical_json({"goals": goals}) + "\n", encoding="utf-8",
        )
        graph = seed_dynamic_graph(
            repo_root=clean, issue_text=instance.issue, goal_contracts=goals,
            source_hints=source_hints, public_checks=public.checks,
            budget=self.config.graph_budget,
        )
        graph.add_node(
            GraphNodeKind.OBSERVATION, node_id="evidence-policy", status="ACTIVE",
            metadata={
                "evidence_reuse_enabled": self.config.evidence_reuse_enabled,
                "demand_driven_interaction_enabled": self.config.demand_driven_interaction_enabled,
            },
        )
        recovery_config = TargetRecoveryConfig(
            max_probes=self.config.target_recovery_max_probes,
            stability_runs=self.config.target_recovery_stability_runs,
            # Keep the production default at the required 1200 seconds;
            # bounded experiment runs may explicitly reduce agent turns.
            timeout_seconds=float(os.environ.get("REACHPATCH_TARGET_RECOVERY_TIMEOUT_SECONDS", "1200")),
            max_agent_turns=int(os.environ.get("REACHPATCH_TARGET_RECOVERY_MAX_AGENT_TURNS", "24")),
            check_timeout_seconds=float(
                os.environ.get("REACHPATCH_RECOVERY_CHECK_TIMEOUT_SECONDS", "120")
            ),
            demand_driven=self.config.demand_driven_interaction_enabled,
        )
        pre_recovery = recover_target_checks(
            repository, clean, clean, goals, public, None, root,
            recovery_config,
            source_hints=source_hints, dynamic_graph=graph,
        )
        update_graph_from_recovery(graph, pre_recovery)
        run_id = stable_id("execution-run", instance.instance_id, str(root))
        checkpoint_store = ExecutionCheckpointStore(root)
        blank = diff_between(clean, clean)
        boot_id = stable_id("execution-bootstrap", instance.instance_id, blank.patch_hash)
        boot_snapshot = root / "execution_checkpoints" / boot_id / "working_tree"
        boot = StateCheckpoint(
            checkpoint_id=boot_id,
            parent_checkpoint_id=None, snapshot_tree=str(boot_snapshot),
            patch_hash=blank.patch_hash, cumulative_diff=blank.canonical_diff,
            status="BOOTSTRAP", revision=0,
            working_tree_hash=tree_hash(clean),
        )
        state = ReachAvoidState(
            clean_snapshot=clean, working_checkpoint=boot,
            safe_checkpoint=None, best_checkpoint=None,
            certified_checkpoint=None, goal_contracts=goals,
            target_checks=pre_recovery.target_checks,
            preservation_checks=pre_recovery.preservation_checks, challenge_checks=(),
            locked_checks=(), active_failure=None,
            dynamic_failure_graph=graph, failure_history={},
            transition_history=[], revision_count=0,
            instance_id=instance.instance_id, run_id=run_id,
            base_repository=repository, base_commit=instance.base_commit,
            run_root=root,
            generator_session=GeneratorSession(stable_id("execution-session", run_id)),
            execution_budget_seconds=self.config.execution_budget_seconds,
            remaining_wall_seconds=self.config.execution_budget_seconds,
            distinct_patch_hashes={blank.patch_hash},
        )
        context = _RunContext(instance, public, checkpoint_store)
        if active_case_budget.get() is not None:
            active_case_budget.get().state = state
        self._contexts[run_id] = context
        boot = context.store.save(
            boot, clean,
            mechanical=run_mechanical_checks(clean, blank, source_tree=clean),
        )
        state.working_checkpoint = boot
        state.checkpoint_history[boot.checkpoint_id] = boot
        graph.register_checkpoint(CheckpointState(boot.checkpoint_id, None,
            boot.cumulative_diff, boot.patch_hash, status="BOOTSTRAP"))
        context.store.write_state(state)
        initial = InitialPatchObjective(
            objective_id=stable_id("initial-execution-objective", run_id),
            goal_contracts=goals,
            public_context=({
                "source": "issue", "authority": "B", "content": instance.issue,
            },),
            current_full_diff=blank.canonical_diff,
            current_patch_hash=blank.patch_hash,
            graph_context=initial_source_context(graph),
        )
        state.phase = ReachAvoidPhase.INITIAL_GENERATION
        state.current_repair_objective = initial
        initial_result = self.initial_patch_agent.generate(state, initial)
        state.generator_attempt_count += 1
        # A malformed/no-op initial response must not terminate the case
        # before the execution-driven loop has had a chance to start.  Give
        # the same graph-grounded objective a bounded independent retry; the
        # staging tree is isolated by RepairPlayer, so a failed response
        # cannot leak edits into a later attempt.
        for retry_index in range(1, self.config.initial_generator_attempts):
            if initial_result.has_new_nonempty_diff and initial_result.modified_tree:
                break
            retry_objective = replace(
                initial,
                objective_id=stable_id(
                    "initial-execution-retry-objective", run_id, retry_index,
                ),
                public_context=tuple(initial.public_context) + ({
                    "source": "controller",
                    "authority": "B",
                    "content": (
                        "The previous initial response did not produce a usable executable "
                        "diff. Use the ranked graph source spans, make one behavior-changing "
                        "edit now, and return a complete patch."
                    ),
                },),
            )
            state.current_repair_objective = retry_objective
            initial_result = self.initial_patch_agent.generate(state, retry_objective)
            state.generator_attempt_count += 1
        if not initial_result.has_new_nonempty_diff or not initial_result.modified_tree:
            return self._output(state, boot, "GENERATOR_BLOCKED_EXTERNAL")
        p0_tree = Path(initial_result.modified_tree)
        p0_diff = diff_between(clean, p0_tree)
        p0_mechanical = run_mechanical_checks(p0_tree, p0_diff, source_tree=clean)
        p0 = self._checkpoint(
            state, p0_tree, parent=None, status="P0",
            mechanical=p0_mechanical, targets=(), preservations=(),
            # P0 is the initial safe/evidence-limited candidate. It is not
            # certified Reach, but it remains eligible as a final fallback if
            # later trials are retained only for continued repair.
            final_eligible=False,
        )
        graph.register_checkpoint(CheckpointState(
            checkpoint_id=p0.checkpoint_id,
            parent_checkpoint_id=None,
            base_to_current_diff=p0.cumulative_diff,
            patch_hash=p0.patch_hash,
            depth=0,
            status="OPEN",
            mechanical_blockers=p0.mechanical_blockers,
            search_score=p0.search_score,
            final_eligible=False,
        ))
        context.p0_patch_hash = p0.patch_hash
        discard_ephemeral_tree(p0_tree, root)
        update_working_checkpoint(state, p0)
        update_safe_checkpoint(state, p0)
        update_best_checkpoint(state, p0)

        # The production policy is demand-driven: deterministic Stage-A
        # evidence is reused and Stage B runs only for an unresolved graph
        # question.  The fixed-interaction ablation deliberately preserves
        # the old unconditional post-P0 recovery dialogue so matched runs can
        # measure the model calls/tokens avoided by this scheduler decision.
        if not self.config.demand_driven_interaction_enabled and transport is not None:
            previous_phase = state.phase
            state.phase = ReachAvoidPhase.TARGET_RECOVERY
            fixed_config = replace(
                recovery_config,
                max_agent_turns=min(
                    recovery_config.max_agent_turns,
                    self.config.fixed_recovery_max_agent_turns,
                ),
                demand_driven=False,
            )
            graph.record_update(
                "FIXED_RECOVERY_SCHEDULED",
                checkpoint_id=p0.checkpoint_id,
                max_agent_turns=fixed_config.max_agent_turns,
                previous_phase=str(previous_phase),
            )
            try:
                fixed_recovery = recover_target_checks(
                    repository, clean, Path(p0.snapshot_tree), goals, public,
                    transport, root, fixed_config, source_hints=source_hints,
                    dynamic_graph=graph,
                )
            finally:
                state.phase = previous_phase
            update_graph_from_recovery(graph, fixed_recovery)
            state.target_checks = tuple({
                item.check_id: item
                for item in (*state.target_checks, *fixed_recovery.target_checks)
            }.values())[: self.config.target_recovery_max_probes]
            state.preservation_checks = tuple({
                item.check_id: item
                for item in (*state.preservation_checks, *fixed_recovery.preservation_checks)
            }.values())
            state.target_recovery = fixed_recovery
        else:
            state.target_recovery = pre_recovery
        context.store.write_state(state)

        hard_goal_ids = tuple(
            str(goal.goal_id) for goal in goals
            if bool(getattr(goal, "hard", False))
        )
        compile_contract_obligations(
            graph, goals, (*state.target_checks, *state.preservation_checks),
        )

        api_failures = 0
        empty_attempts = 0
        recovery_rounds = 0
        attempted_hypotheses: set[tuple[str, str, str]] = set()
        active_hypothesis: RepairHypothesis | None = None
        active_causal_cuts: tuple[Any, ...] = ()
        active_hypotheses: tuple[RepairHypothesis, ...] = ()
        terminal_status = "BEST_EFFORT_REVISION_LIMIT"
        timed_action = None
        action_started = time.monotonic()
        while time.monotonic() - started < self.config.execution_budget_seconds:
            if timed_action is not None:
                graph.record_update("ACTION_COMPLETED", action_id=timed_action.action_id,
                    kind=timed_action.kind, duration_seconds=time.monotonic() - action_started)
                action_node = graph.nodes.get(timed_action.action_id)
                if action_node is not None and action_node.status == "SELECTED":
                    graph.nodes[action_node.node_id] = replace(
                        action_node, status="COMPLETED",
                        metadata={**action_node.metadata,
                                  "duration_seconds": time.monotonic() - action_started},
                    )
            active_hypothesis = None
            # Select the next node globally from the unified graph.  A
            # rejected sibling can therefore never become the implicit parent
            # of a later edit, while a partial-progress checkpoint remains
            # eligible for preservation repair even when it was created
            # earlier than another sibling.
            generation_allowed = (state.revision_count < min(self.config.max_real_patch_revisions, self.config.search_budget.max_evaluated_patch_nodes)
                and (active_case_budget.get() is None or active_case_budget.get().remaining_wall > self.config.final_validation_reserve_seconds))
            actions = derive_frontier_actions(graph, max_depth=self.config.search_budget.max_depth,
                generation_allowed=generation_allowed,
                max_exploratory_probes=self.config.search_budget.max_exploratory_probes_per_checkpoint,
                demand_driven=self.config.demand_driven_interaction_enabled)
            selected_action = select_next_action(graph, actions)
            timed_action = selected_action
            action_started = time.monotonic()
            if selected_action is None:
                graph.record_update("GLOBAL_FRONTIER_EXHAUSTED", reason=terminal_status)
                if terminal_status != "EVIDENCE_LIMITED":
                    terminal_status = "GLOBAL_FRONTIER_EXHAUSTED"
                break
            graph_selected = state.checkpoint_history.get(selected_action.checkpoint_id)
            if graph_selected is None and int(graph.nodes[state.working_checkpoint.checkpoint_id].metadata.get("depth", 0)) >= self.config.search_budget.max_depth:
                terminal_status = "SEARCH_DEPTH_EXHAUSTED"
                break
            if (
                graph_selected is not None
                and graph_selected.checkpoint_id != state.working_checkpoint.checkpoint_id
            ):
                context.store.validate(
                    graph_selected, repository, clean_snapshot=clean,
                )
                state.locked_checks = graph_selected.locked_checks
                update_working_checkpoint(state, graph_selected)
                context.store.write_state(state)
            context.store.validate(
                state.working_checkpoint, repository, clean_snapshot=clean,
            )
            working = Path(state.working_checkpoint.snapshot_tree)
            current_diff = diff_between(clean, working)
            refresh_checkpoint_source(graph, working, current_diff.canonical_diff)
            if selected_action.kind == "RUN_PROBE":
                from .dynamic_reach_avoid_graph import ChallengeCell
                cell_node = graph.nodes[selected_action.obligation_ids[0]]
                cell = ChallengeCell(**{key: value for key, value in cell_node.metadata.items()
                                       if key in ChallengeCell.__dataclass_fields__})
                comparisons = select_comparable_checkpoints(graph, state.working_checkpoint.checkpoint_id)
                snapshots = {key: Path(state.checkpoint_history[key].snapshot_tree) for key in comparisons
                             if key in state.checkpoint_history}
                snapshots[state.working_checkpoint.checkpoint_id] = working
                reports = execute_sibling_probes(graph, (cell,), snapshots, clean=clean, max_probes=1)
                resolve_checkpoint_questions(
                    graph, state.working_checkpoint.checkpoint_id, ("FALSIFY",),
                    "SUPPORTED" if reports else "BLOCKED",
                    evidence_ids=tuple(report.get("probe_id", "") for report in reports),
                    blocking_reason=None if reports else "PROBE_NOT_EXECUTED",
                )
                cp_node = graph.nodes[state.working_checkpoint.checkpoint_id]
                graph.nodes[cp_node.node_id] = replace(cp_node, metadata={**cp_node.metadata,
                    "exploratory_probe_attempts": cp_node.metadata.get("exploratory_probe_attempts", 0) + 1})
                exhaust_action(graph, selected_action, "PROBED_EXPLORATORY" if reports else "PROBE_NOT_EXECUTED")
                continue
            if selected_action.kind == "EXPAND_GRAPH":
                before_source = {node.node_id for node in graph.nodes.values()
                                 if node.kind in {GraphNodeKind.SYMBOL, GraphNodeKind.VALUE, GraphNodeKind.BRANCH}}
                expand_dynamic_graph_frontier(graph, working, ())
                checkpoint_node = graph.nodes[state.working_checkpoint.checkpoint_id]
                expanded = any(node.node_id not in before_source for node in graph.nodes.values()
                               if node.kind in {GraphNodeKind.SYMBOL, GraphNodeKind.VALUE, GraphNodeKind.BRANCH})
                graph.nodes[checkpoint_node.node_id] = replace(checkpoint_node, metadata={**checkpoint_node.metadata,
                    "expansion_rounds": int(checkpoint_node.metadata.get("expansion_rounds", 0)) + 1})
                exhaust_action(graph, selected_action, "FRONTIER_EXPANDED" if expanded else "NO_NEW_SOURCE")
                if expanded:
                    graph.update_checkpoint(checkpoint_node.node_id, status="OPEN")
                continue
            mechanical_before = run_mechanical_checks(
                working, current_diff, source_tree=clean,
            )
            state.last_mechanical_result = mechanical_before
            checks = self._graph_checks(state)
            parent_results = self._execute_graph_queue(state, working, checks, clean, phase="PARENT")
            targets_before, preservation_before, challenges_before = self._split(
                parent_results, state,
            )
            if parent_results:
                resolve_checkpoint_questions(
                    graph, state.working_checkpoint.checkpoint_id, ("VALIDATE",),
                    "SUPPORTED", evidence_ids=tuple(item.check_id for item in parent_results),
                )
            graph_checkpoint = CheckpointState(
                checkpoint_id=state.working_checkpoint.checkpoint_id,
                parent_checkpoint_id=state.working_checkpoint.parent_checkpoint_id,
                base_to_current_diff=state.working_checkpoint.cumulative_diff,
                patch_hash=state.working_checkpoint.patch_hash,
                depth=state.revision_count,
                status=state.working_checkpoint.status,
                causal_cut_ids=tuple(graph.nodes[state.working_checkpoint.checkpoint_id].metadata.get("causal_cut_ids", ())),
            )
            graph_challenges = materialize_graph_guided_challenges(
                working, graph, graph_checkpoint, parent_results,
            )
            graph_challenge_checks = self._checks_from_graph_challenges(
                state, graph_challenges, graph,
            )
            if graph_challenge_checks:
                known_challenge_ids = {
                    item.check_id for item in state.challenge_checks
                }
                additions = tuple(
                    item for item in graph_challenge_checks
                    if item.check_id not in known_challenge_ids
                )
                if additions:
                    state.challenge_checks = tuple((*state.challenge_checks, *additions))
                    checks = self._graph_checks(state)
                    parent_results = self._execute_graph_queue(state, working, checks, clean, phase="PARENT_CHALLENGE")
                    targets_before, preservation_before, challenges_before = self._split(
                        parent_results, state,
                    )
            update_graph_from_challenges(
                graph,
                challenges_before,
                checkpoint_id=state.working_checkpoint.checkpoint_id,
            )
            self._refresh_evaluated_checkpoint(state, mechanical_before, targets_before, preservation_before, challenges_before)
            new_locks = self._updated_locks(
                state, (*targets_before, *preservation_before),
                state.working_checkpoint.patch_hash,
            )
            if new_locks != state.locked_checks:
                state.locked_checks = new_locks
                synchronized = context.store.replace_metadata(replace(
                    state.working_checkpoint, locked_checks=new_locks,
                ))
                state.checkpoint_history[synchronized.checkpoint_id] = synchronized
                update_working_checkpoint(state, synchronized)
                if state.best_checkpoint and state.best_checkpoint.checkpoint_id == synchronized.checkpoint_id:
                    state.best_checkpoint = synchronized
                if state.safe_checkpoint and state.safe_checkpoint.checkpoint_id == synchronized.checkpoint_id:
                    state.safe_checkpoint = synchronized
                graph.update_checkpoint(synchronized.checkpoint_id, locked_successes=tuple(lock.check_id for lock in new_locks))

            potentially_reached = all_reach_conditions_pass(
                mechanical_before, targets_before,
                preservation_before, challenges_before, hard_goal_ids,
            )
            oracle_review = audit_return_oracles(
                working, graph, goals, checks, parent_results,
                patch_hash=state.working_checkpoint.patch_hash,
                wall_seconds=min(60.0, max(0.0, self.config.execution_budget_seconds - (time.monotonic() - started))),
            ) if potentially_reached else ()
            audit_gaps = uncovered_oracle_gaps(oracle_review)
            obligation_ids = compile_contract_obligations(
                graph, goals, (*state.target_checks, *state.preservation_checks),
            )
            closure = evaluate_validation_closure(graph, state.working_checkpoint.checkpoint_id, obligation_ids,
                                                   parent_results, oracle_review)
            frontier_action = select_frontier_action_from_graph(graph, state.working_checkpoint.checkpoint_id, oracle_gaps=audit_gaps)
            if potentially_reached and not audit_gaps and closure["closed"]:
                certified = self._checkpoint(
                    state, working, parent=state.working_checkpoint,
                    status="CERTIFIED", mechanical=mechanical_before,
                    targets=targets_before, preservations=preservation_before,
                    challenges=challenges_before, final_eligible=True,
                )
                update_working_checkpoint(state, certified)
                update_safe_checkpoint(state, certified)
                update_best_checkpoint(state, certified)
                state.certified_checkpoint = certified
                return self._output(state, certified, "REACHED")

            active = select_active_failure(
                mechanical_before,
                tuple(item for item in targets_before if not frontier_action["check_id"] or item.check_id == frontier_action["check_id"]),
                tuple(item for item in preservation_before if not frontier_action["check_id"] or item.check_id == frontier_action["check_id"]),
                tuple(item for item in challenges_before if not frontier_action["check_id"] or item.check_id == frontier_action["check_id"]), state.failure_history,
                target_checks=state.target_checks,
                preservation_checks=state.preservation_checks,
                challenge_checks=state.challenge_checks,
            )
            if frontier_action["kind"] == "RECOVER_EVIDENCE":
                active = None
            if active is None:
                unresolved = bool(audit_gaps) or bool(closure.get("gaps")) or not state.target_checks or any(
                    item.status in {CheckStatus.UNKNOWN, CheckStatus.BLOCKED}
                    for item in parent_results
                ) or bool(
                    set(hard_goal_ids)
                    - {
                        str(item.goal_id)
                        for item in (*state.target_checks, *state.preservation_checks)
                        if item.goal_id and item.trusted
                    }
                )
                if unresolved and recovery_rounds < self.config.target_recovery_attempts:
                    evidence = {"gaps": closure.get("gaps", ()),
                        "observations": [(item.check_id, item.semantic_signature) for item in parent_results],
                        "goals": [goal.to_dict() for goal in goals]}
                    if not claim_evidence_action(graph, state.working_checkpoint.checkpoint_id,
                            "RECOVER_EVIDENCE", "Recover the missing executable contract", evidence):
                        exhaust_action(graph, selected_action, "NO_NEW_RECOVERY_EVIDENCE")
                        continue
                    recovery_rounds += 1
                    state.phase = ReachAvoidPhase.TARGET_RECOVERY
                    recovery = recover_target_checks(
                        repository, clean, working, goals, public,
                        transport, root, recovery_config, source_hints=source_hints,
                        dynamic_graph=graph,
                        oracle_review=tuple(audit_gaps) + tuple({
                            "obligation_id": key, **graph.nodes[key].metadata,
                            "status": graph.nodes[key].status,
                            "reason": selected_action.priority_reason,
                        } for key in selected_action.obligation_ids if key in graph.nodes),
                    )
                    update_graph_from_recovery(graph, recovery)
                    state.target_checks = tuple({item.check_id: item for item in (*state.target_checks, *recovery.target_checks)}.values())[: self.config.target_recovery_max_probes]
                    state.preservation_checks = tuple({item.check_id: item for item in (*state.preservation_checks, *recovery.preservation_checks)}.values())
                    state.target_recovery = recovery
                    # Recovery changes the executable evidence universe. Bind
                    # the new handles and contract facets immediately so the
                    # same checkpoint acquires a pending VALIDATE action on
                    # the next scheduler turn. Without this hand-off, the old
                    # recovery question is resolved while the recovered target
                    # is absent from REQUIRES_VALIDATION edges, leaving the
                    # global frontier falsely empty.
                    compile_contract_obligations(
                        graph, goals,
                        (*state.target_checks, *state.preservation_checks),
                    )
                    register_validation_checks(
                        graph, self._checks(state),
                        locked_ids=tuple(lock.check_id for lock in state.locked_checks),
                    )
                    derive_validation_obligations(
                        graph, state.working_checkpoint.checkpoint_id,
                    )
                    resolve_checkpoint_questions(
                        graph, state.working_checkpoint.checkpoint_id,
                        ("TARGET_RECOVERY", "RECOVER_EVIDENCE"),
                        "SUPPORTED" if recovery.target_checks else "BLOCKED",
                        evidence_ids=("recovery-history",) if "recovery-history" in graph.nodes else (),
                        blocking_reason=None if recovery.target_checks else
                            ",".join(recovery.exhausted_reasons) or "NO_TRUSTED_TARGET",
                    )
                    continue
                terminal_status = "EVIDENCE_LIMITED" if unresolved else "MECHANISM_EXHAUSTED"
                exhaust_action(graph, selected_action, terminal_status)
                continue

            active_execution = next((
                item for item in parent_results if item.check_id == active.check_id
            ), None)
            if not generation_allowed:
                exhaust_action(graph, selected_action, "PATCH_GENERATION_BUDGET_EXHAUSTED")
                continue
            state.failure_history[active.signature] = FailureHistory(
                signature=active.signature, count=active.same_signature_count,
                check_id=active.check_id,
                last_patch_hash=state.working_checkpoint.patch_hash,
                last_observation_hash=(
                    self._observation_hash(active_execution)
                    if active_execution is not None else content_hash(active)
                ),
            )
            state.active_failure = active
            causal_cuts = active_causal_cuts
            hypotheses = active_hypotheses

            if active_execution is None and active.kind is ActiveFailureKind.MECHANICAL:
                mechanical_hypothesis = RepairHypothesis(
                    hypothesis_id=stable_id(
                        "repair-hypothesis", state.working_checkpoint.checkpoint_id,
                        active.failure_id, "fix_mechanical_blocker",
                    ),
                    parent_checkpoint_id=state.working_checkpoint.checkpoint_id,
                    requirement_id=str(active.goal_id or active.check_id),
                    failure_id=active.failure_id,
                    causal_cut_ids=(),
                    proposed_mechanism="fix_mechanical_blocker",
                    expected_path_change="remove the named import, syntax, or undefined-name blocker",
                    expected_observation_change="make the repository mechanically executable",
                    forbidden_regressions=("locked target", "trusted preservation"),
                )
                hypotheses = (mechanical_hypothesis,) if (
                    state.working_checkpoint.checkpoint_id,
                    active.failure_id,
                    mechanical_hypothesis.hypothesis_id,
                ) not in attempted_hypotheses else ()
                active_hypotheses = hypotheses
                graph.register_hypothesis(mechanical_hypothesis)

            # The first stable target failure is already actionable.  Update
            # the single graph immediately; never wait for a repeated output
            # signature and never replace it with a second graph object.
            if active_execution is not None:
                state.dynamic_failure_graph = update_graph_from_execution(
                    graph, active, active_execution, active_execution.trace,
                    current_diff,
                )
                materialized = materialize_diff_checks(
                    working, current_diff.canonical_diff, active,
                    (*state.target_checks, *state.preservation_checks),
                    state.dynamic_failure_graph, state.challenge_checks,
                )
                if materialized != state.challenge_checks:
                    state.challenge_checks = materialized
                    checks = self._graph_checks(state)
                    parent_results = self._execute_graph_queue(state, working, checks, clean, phase="PARENT_CHALLENGE")
                    targets_before, preservation_before, challenges_before = self._split(
                        parent_results, state,
                    )
                causal_cuts = rank_causal_cuts(
                    graph, str(active.goal_id or active.check_id), active.failure_id,
                    current_diff.canonical_diff, limit=self.config.search_budget.branch_factor,
                )
                if len(causal_cuts) < min(2, self.config.search_budget.branch_factor):
                    expand_dynamic_graph_frontier(
                        graph, clean,
                        tuple(
                            node_id for cut in causal_cuts
                            for node_id in cut.symbol_ids
                        ),
                    )
                    causal_cuts = rank_causal_cuts(
                        graph, str(active.goal_id or active.check_id), active.failure_id,
                        current_diff.canonical_diff,
                        limit=self.config.search_budget.branch_factor,
                    )
                all_hypotheses = build_distinct_repair_hypotheses(
                    CheckpointState(
                        checkpoint_id=state.working_checkpoint.checkpoint_id,
                        parent_checkpoint_id=state.working_checkpoint.parent_checkpoint_id,
                        base_to_current_diff=state.working_checkpoint.cumulative_diff,
                        patch_hash=state.working_checkpoint.patch_hash,
                        depth=state.revision_count,
                    ),
                    str(active.goal_id or active.check_id), active.failure_id,
                    causal_cuts, limit=self.config.search_budget.branch_factor,
                )
                hypotheses = tuple(
                    hypothesis for hypothesis in all_hypotheses
                    if (state.working_checkpoint.checkpoint_id, active.failure_id, hypothesis.hypothesis_id)
                    not in attempted_hypotheses
                    and hypothesis.hypothesis_id not in graph.nodes[state.working_checkpoint.checkpoint_id].metadata.get("expanded_hypothesis_ids", ())
                    and sum(
                        prior.kind is GraphNodeKind.REPAIR_HYPOTHESIS
                        and prior.metadata.get("failure_id") == hypothesis.failure_id
                        and tuple(prior.metadata.get("causal_cut_ids", ())) == hypothesis.causal_cut_ids
                        and prior.metadata.get("proposed_mechanism") == hypothesis.proposed_mechanism
                        and any(prior.node_id in checkpoint.metadata.get("expanded_hypothesis_ids", ())
                                for checkpoint in graph.nodes.values() if checkpoint.kind is GraphNodeKind.CHECKPOINT)
                        for prior in graph.nodes.values()
                    ) < 2
                )
                # A rejected sibling is exhausted, but the parent remains open
                # for the next distinct causal cut.  Once all cuts have been
                # tried, allow a new graph expansion/recovery round instead of
                # repeatedly submitting the same mechanism.
                active_causal_cuts = tuple(causal_cuts)
                active_hypotheses = tuple(hypotheses)
                for hypothesis in all_hypotheses:
                    graph.register_hypothesis(hypothesis)

            if not hypotheses:
                graph.update_checkpoint(state.working_checkpoint.checkpoint_id, status="EXPANDED")
                exhaust_action(graph, selected_action, "LOCAL_MECHANISMS_EXHAUSTED")
                active_causal_cuts = ()
                active_hypotheses = ()
                continue

            if hypotheses:
                if selected_action.hypothesis_id and not any(item.hypothesis_id == selected_action.hypothesis_id for item in hypotheses):
                    exhaust_action(graph, selected_action, "HYPOTHESIS_NO_LONGER_APPLICABLE")
                    continue
                active_hypothesis = next((item for item in hypotheses
                    if item.hypothesis_id == selected_action.hypothesis_id), hypotheses[0])
                hypotheses = (active_hypothesis, *(item for item in hypotheses if item != active_hypothesis))
                current_node = graph.nodes[state.working_checkpoint.checkpoint_id]
                graph.update_checkpoint(state.working_checkpoint.checkpoint_id,
                    expanded_hypothesis_ids=tuple(dict.fromkeys((*current_node.metadata.get("expanded_hypothesis_ids", ()), active_hypothesis.hypothesis_id))))
                attempted_hypotheses.add((
                    state.working_checkpoint.checkpoint_id,
                    active.failure_id,
                    active_hypothesis.hypothesis_id,
                ))
                graph.metrics["entered_repair_loop"] = 1

            clean_results = self._execute_graph_queue(state, clean, checks, clean, phase="CLEAN")
            self._refresh_evaluated_checkpoint(state, mechanical_before, targets_before, preservation_before, challenges_before)
            objective = self._objective(state, active)
            if 'causal_cuts' in locals():
                selected_cut_ids = set(active_hypothesis.causal_cut_ids if active_hypothesis else ())
                selected_cuts = tuple(
                    item for item in causal_cuts
                    if not selected_cut_ids or item.cut_id in selected_cut_ids
                )
                objective = replace(
                    objective,
                    causal_cut_ids=tuple(item.cut_id for item in selected_cuts),
                    hypothesis_id=(active_hypothesis.hypothesis_id if active_hypothesis else None),
                    graph_source_spans=tuple(span for item in selected_cuts for span in item.source_spans),
                    repair_hypothesis=active_hypothesis,
                    hypothesis_feedback=tuple(node.metadata for node in graph.nodes.values()
                        if node.kind is GraphNodeKind.OBSERVATION and node.metadata.get("hypothesis_id")
                        and graph.nodes.get(node.metadata["hypothesis_id"]) is not None
                        and selected_cut_ids.intersection(graph.nodes[node.metadata["hypothesis_id"]].metadata.get("causal_cut_ids", ())))[-6:],
                    exploratory_observations=tuple({
                        "probe_id": node.node_id, "source_branch_id": node.metadata.get("source_branch_id"),
                        "certifying": False,
                        "outcomes": {key: {"stable": value["stable"], "value": value["value"]}
                                     for key, value in node.metadata.get("outcomes", {}).items()},
                    } for node in graph.nodes.values()
                        if node.kind is GraphNodeKind.OBSERVATION and node.metadata.get("probe_id")
                        and node.metadata.get("source_branch_id") in {branch_id for cut in selected_cuts for branch_id in cut.branch_ids})[-6:],
                )
            parent = state.working_checkpoint
            if not claim_evidence_action(graph, parent.checkpoint_id, "REPAIR",
                    active_hypothesis.proposed_mechanism if active_hypothesis else active.kind.value,
                    {"failure": active.signature,
                     "observation": self._observation_hash(active_execution) if active_execution else self._blockers(mechanical_before),
                     "causal_cuts": objective.causal_cut_ids}):
                exhaust_action(graph, selected_action, "NO_NEW_REPAIR_EVIDENCE")
                continue
            state.phase = ReachAvoidPhase.REPAIR
            try:
                trial_result = self.repair_player.revise_working_patch(state, objective)
            except CaseBudgetExhausted:
                raise
            except Exception as error:
                external_failure = isinstance(
                    error, (urllib.error.URLError, TimeoutError, json.JSONDecodeError)
                )
                if external_failure:
                    api_failures += 1
                else:
                    empty_attempts += 1
                with (root / "repair_errors.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(canonical_json({
                        "failure_id": active.failure_id,
                        "kind": "EXTERNAL_API_UNAVAILABLE" if external_failure else "REPAIR_MECHANISM_ERROR",
                        "error": str(error)[-12000:],
                    }) + "\n")
                if (
                    external_failure
                    and api_failures >= self.config.max_no_progress_generator_attempts
                ):
                    # A failed model call exhausts only this hypothesis.  Keep
                    # the parent open while distinct sibling hypotheses remain
                    # so one transient/API failure cannot collapse the search
                    # tree into an immediate P0 fallback.
                    remaining = tuple(
                        item for item in active_hypotheses
                        if item.hypothesis_id != getattr(active_hypothesis, "hypothesis_id", None)
                    )
                    active_hypotheses = remaining
                    if remaining:
                        continue
                    terminal_status = "EXTERNAL_API_UNAVAILABLE"
                    break
                if (
                    not external_failure
                    and empty_attempts >= self.config.max_no_progress_generator_attempts
                ):
                    remaining = tuple(
                        item for item in active_hypotheses
                        if item.hypothesis_id != getattr(active_hypothesis, "hypothesis_id", None)
                    )
                    active_hypotheses = remaining
                    if remaining:
                        continue
                    terminal_status = "MECHANISM_EXHAUSTED"
                    graph.update_checkpoint(parent.checkpoint_id, status="EXPANDED")
                    exhaust_action(graph, selected_action, terminal_status)
                    continue
                continue
            api_failures = 0
            state.generator_attempt_count += 1
            if not trial_result.has_new_nonempty_diff or not trial_result.modified_tree:
                empty_attempts += 1
                active_hypotheses = tuple(
                    item for item in active_hypotheses
                    if item.hypothesis_id != getattr(active_hypothesis, "hypothesis_id", None)
                )
                if empty_attempts >= self.config.max_no_progress_generator_attempts:
                    if active_hypotheses:
                        empty_attempts = 0
                        continue
                    terminal_status = "MECHANISM_EXHAUSTED"
                    graph.update_checkpoint(parent.checkpoint_id, status="EXPANDED")
                    exhaust_action(graph, selected_action, terminal_status)
                    continue
                continue
            empty_attempts = 0
            trial_tree = Path(trial_result.modified_tree)
            trial_diff = diff_between(clean, trial_tree)
            if trial_diff.patch_hash in state.distinct_patch_hashes:
                record_patch_transposition(graph, parent.checkpoint_id, active_hypothesis.hypothesis_id,
                                           trial_diff.patch_hash, trial_diff.canonical_diff)
                exhaust_action(graph, selected_action, "EXACT_PATCH_TRANSPOSITION")
                discard_ephemeral_tree(trial_tree, root)
                continue

            state.revision_count += 1
            state.distinct_patch_hashes.add(trial_diff.patch_hash)
            mechanical_after = run_mechanical_checks(
                trial_tree, trial_diff, source_tree=clean,
            )
            trial_id = stable_id("execution-checkpoint", state.run_id, trial_diff.patch_hash,
                                 state.revision_count, "TRIAL", parent.checkpoint_id)
            graph.register_checkpoint(CheckpointState(trial_id, parent.checkpoint_id,
                trial_diff.canonical_diff, trial_diff.patch_hash, status="EVALUATING",
                depth=int(graph.nodes[parent.checkpoint_id].metadata.get("depth", 0)) + 1))
            trial_checks = self._derive_checkpoint_checks(state, trial_tree, trial_id, trial_diff.canonical_diff, parent_results)
            checks = tuple({check.check_id: check for check in (*checks, *trial_checks)}.values())
            # Pair every child-introduced obligation against the exact same
            # clean and parent snapshots before transition classification.
            known = {item.check_id for item in parent_results}
            extra = tuple(check for check in checks if check.check_id not in known)
            if extra:
                parent_results += self._execute_graph_queue(state, working, extra, clean, phase="PARENT_DELTA")
                clean_results += self._execute_graph_queue(state, clean, extra, clean, phase="CLEAN")
            trial_results = self._execute_graph_queue(state, trial_tree, checks, clean, phase="TRIAL")
            targets_after, preservation_after, challenges_after = self._split(
                trial_results, state,
            )
            parent_by_id = {item.check_id: item for item in parent_results}
            check_by_id = {item.check_id: item for item in checks}
            progress = tuple(
                compute_execution_atomic_progress(
                    parent_by_id[item.check_id], item, check_by_id[item.check_id],
                )
                for item in trial_results if item.check_id in parent_by_id
            ) + (compute_mechanical_atomic_progress(
                mechanical_before, mechanical_after,
            ),)
            locked_before = state.locked_checks
            locked_after = self._updated_locks(
                state, (*targets_after, *preservation_after, *challenges_after), trial_diff.patch_hash,
            )
            trial = self._checkpoint(
                state, trial_tree, parent=parent, status="TRIAL",
                mechanical=mechanical_after, targets=targets_after,
                preservations=preservation_after, challenges=challenges_after,
                locked_checks=locked_after,
            )
            if active_hypothesis is not None:
                record_hypothesis_feedback(graph, active_hypothesis, trial.checkpoint_id, parent_results, trial_results)
            update_graph_from_challenges(
                graph,
                challenges_after,
                checkpoint_id=trial.checkpoint_id,
            )
            graph.register_checkpoint(CheckpointState(
                checkpoint_id=trial.checkpoint_id,
                parent_checkpoint_id=parent.checkpoint_id,
                base_to_current_diff=trial.cumulative_diff,
                patch_hash=trial.patch_hash,
                status=trial.status,
                final_eligible=False,
                hypothesis_id=(active_hypothesis.hypothesis_id if active_hypothesis else None),
                causal_cut_ids=(active_hypothesis.causal_cut_ids if active_hypothesis else ()),
                depth=int(graph.nodes[parent.checkpoint_id].metadata.get("depth", 0)) + 1,
                search_score=trial.search_score,
                observation_ids=tuple(self._observation_hash(item) for item in trial_results),
                mechanical_blockers=trial.mechanical_blockers,
                target_results={item.check_id: item.to_dict() for item in targets_after},
                preservation_results={item.check_id: item.to_dict() for item in preservation_after},
                challenge_results={item.check_id: item.to_dict() for item in challenges_after},
                locked_successes=tuple(item.check_id for item in locked_after),
            ))
            refresh_checkpoint_source(graph, trial_tree, trial_diff.canonical_diff)
            comparable_ids = select_comparable_checkpoints(graph, trial.checkpoint_id)
            sibling_snapshots = {key: Path(state.checkpoint_history[key].snapshot_tree)
                                 for key in comparable_ids if key in state.checkpoint_history}
            if len(sibling_snapshots) >= 2:
                child_projection = CheckpointState(**{
                    key: value for key, value in graph.nodes[trial.checkpoint_id].metadata.items()
                    if key in CheckpointState.__dataclass_fields__
                })
                sibling_cells = materialize_graph_guided_challenges(trial_tree, graph, child_projection, trial_results)
                execute_sibling_probes(graph, sibling_cells, dict(list(sibling_snapshots.items())[-3:]), clean=clean,
                    wall_seconds=min(30.0, max(0.0, self.config.execution_budget_seconds - (time.monotonic() - started))))
            state.phase = ReachAvoidPhase.TRANSITION
            decision = decide_transition(
                parent, trial, mechanical_before, mechanical_after,
                progress, targets_after, preservation_after, challenges_after,
                hard_goal_ids,
            )
            if decision is TransitionDecision.REACHED:
                trial_audit = audit_return_oracles(
                    trial_tree, graph, goals, checks, trial_results, patch_hash=trial.patch_hash,
                    wall_seconds=min(60.0, max(0.0, self.config.execution_budget_seconds - (time.monotonic() - started))),
                )
                closure = evaluate_validation_closure(graph, trial.checkpoint_id,
                    compile_contract_obligations(
                        graph, goals,
                        (*state.target_checks, *state.preservation_checks),
                    ), trial_results, trial_audit)
                if uncovered_oracle_gaps(trial_audit) or not closure["closed"]:
                    decision = TransitionDecision.KEEP_REPAIRING
            certificate = self._certificate(
                state, parent, trial, decision, active, progress,
                clean_results, parent_results, trial_results,
                mechanical_after, preservation_after,
                locked_before, locked_after,
            )
            self._persist_transition_evidence(
                state, certificate, clean_results,
                parent_results, trial_results,
            )
            state.transition_history.append(certificate)
            graph.record_transition(parent.checkpoint_id, trial.checkpoint_id, decision.value, evidence_ids=(certificate.certificate_id,))
            resolve_checkpoint_questions(
                graph, parent.checkpoint_id, ("REPAIR",),
                "REFUTED" if decision is TransitionDecision.REJECT_TRIAL else "SUPPORTED",
                evidence_ids=(trial.checkpoint_id,),
                blocking_reason=("TRIAL_REJECTED" if decision is TransitionDecision.REJECT_TRIAL else None),
            )
            if decision is TransitionDecision.REACHED:
                resolve_checkpoint_questions(
                    graph, trial.checkpoint_id, ("CERTIFY",), "SUPPORTED",
                    evidence_ids=(trial.checkpoint_id,),
                )

            remaining_siblings = tuple(hypotheses[1:])
            if decision is TransitionDecision.REJECT_TRIAL:
                state.rejected_patch_hashes.add(trial.patch_hash)
                rejected = context.store.replace_metadata(replace(
                    trial, status="REJECTED", final_eligible=False,
                    transition_certificate_id=certificate.certificate_id,
                ))
                state.checkpoint_history[rejected.checkpoint_id] = rejected
                restore_parent_working_checkpoint(state, parent, context.store)
                # Keep the exact parent open and carry the untried sibling
                # hypotheses into the next iteration.  The next child is
                # therefore generated from the restored parent snapshot,
                # never from the rejected sibling's working tree.
                active_causal_cuts = tuple(causal_cuts)
                active_hypotheses = tuple(remaining_siblings)
                state.locked_checks = parent.locked_checks
                update_working_checkpoint(state, parent)
            else:
                eligible = decision in {
                    TransitionDecision.ADVANCE_SAFE,
                    TransitionDecision.REACHED,
                }
                accepted = context.store.replace_metadata(replace(
                    trial, final_eligible=eligible,
                    transition_certificate_id=certificate.certificate_id,
                    certified=(decision is TransitionDecision.REACHED),
                ))
                state.checkpoint_history[accepted.checkpoint_id] = accepted
                update_best_checkpoint(state, accepted)
                if decision is TransitionDecision.REACHED:
                    state.locked_checks = locked_after
                    update_working_checkpoint(state, accepted)
                    update_safe_checkpoint(state, accepted)
                    state.certified_checkpoint = accepted
                    discard_ephemeral_tree(trial_tree, root)
                    return self._output(state, accepted, "REACHED")
                # A child with verified target progress is the incumbent and
                # must remain the next parent so preservation repair can retain
                # its locked success.  Untried siblings are only resumed after
                # an explicitly rejected child; otherwise a duplicate model
                # call from the old parent can recreate the same target patch
                # and obscure the required FIX_REGRESSION path.
                siblings = tuple(
                    item for item in state.checkpoint_history.values()
                    if item.parent_checkpoint_id == parent.checkpoint_id
                    and item.patch_hash not in state.rejected_patch_hashes
                    and item.patch_is_applicable and not item.repository_corrupted
                )
                selected = max(
                    siblings,
                    key=lambda item: (tuple(item.search_score), item.revision, item.checkpoint_id),
                    default=accepted,
                )
                state.locked_checks = selected.locked_checks
                update_working_checkpoint(state, selected)
                if selected.final_eligible:
                    update_safe_checkpoint(state, selected)
                active_causal_cuts = ()
                active_hypotheses = ()
            context.store.write_state(state)
            discard_ephemeral_tree(trial_tree, root)

        state.remaining_wall_seconds = max(
            0.0,
            self.config.execution_budget_seconds - (time.monotonic() - started),
        )
        if not state.target_checks:
            terminal_status = "EVIDENCE_LIMITED"
        return self._output(state, select_final_checkpoint(state), terminal_status)

    def run_case(
        self, case: Instance, *, run_root: str | Path | None = None,
    ) -> TerminalResult:
        """Run the sole production execution-driven case pipeline.

        ``Instance`` is the repository's SWECase record.  Keeping this named
        entry point explicit makes it impossible for experiment code to
        accidentally select one of the retired graph-stack controllers.
        """
        budget = CaseBudget(self.config.execution_budget_seconds,
                            max_model_calls=self.config.max_case_model_calls,
                            max_tokens=self.config.max_case_tokens,
                            execution_seconds=self.config.execution_budget_seconds,
                            final_validation_reserve=self.config.final_validation_reserve_seconds)
        token = active_case_budget.set(budget)
        agent = getattr(self.repair_player, "generator_agent", None)
        original_transport = getattr(agent, "transport", None)
        if original_transport is not None:
            agent.transport = BudgetedTransport(original_transport, budget)
        try:
            try:
                return self._run_execution_driven(case, run_root=run_root)
            except CaseBudgetExhausted as error:
                if budget.state is None:
                    raise  # No patch checkpoint exists to seal.
                reason = str(error)
                status = ("EVIDENCE_LIMITED_CONTEXT" if "CONTEXT" in reason
                          else "BEST_EFFORT_BUDGET_EXHAUSTED")
                budget.state.dynamic_failure_graph.record_update(
                    "CASE_LIMIT_REACHED", reason=reason, terminal_status=status,
                )
                return self._output(budget.state, select_final_checkpoint(budget.state), status)
        finally:
            if original_transport is not None:
                agent.transport = original_transport
            if budget.state is not None:
                (budget.state.run_root / "case_budget.json").write_text(canonical_json(budget.summary()) + "\n", encoding="utf-8")
                (budget.state.run_root / "token_efficiency.json").write_text(
                    canonical_json(efficiency_report(budget.state.dynamic_failure_graph, budget)) + "\n", encoding="utf-8")
            active_case_budget.reset(token)

    def run(
        self, instance: Instance, *, run_root: str | Path | None = None,
    ) -> TerminalResult:
        # Public compatibility spelling; all production work still funnels
        # through run_case so there is only one controller implementation.
        return self.run_case(instance, run_root=run_root)
