from __future__ import annotations

from pathlib import Path

from reachpatch.execution.worktree import (
    copy_source_tree, diff_between, discard_ephemeral_tree,
)
from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.reach_avoid import GeneratorResult, ReachAvoidState, RepairObjective
from reachpatch.repair.tools import RepairToolExecutor


class RepairPlayer:
    def __init__(self, generator_agent) -> None:
        self.generator_agent = generator_agent

    def revise_working_patch(
        self,
        state: ReachAvoidState,
        objective: RepairObjective,
        *,
        initial: bool = False,
    ) -> GeneratorResult:
        source = Path(state.working_checkpoint.snapshot_tree)
        staging_root = state.run_root / "generator_staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging = staging_root / stable_id(
            "generation", state.generator_session.session_id,
            state.generator_attempt_count, objective.objective_id,
        )
        if staging.exists():
            raise FileExistsError(staging)
        copy_source_tree(source, staging)
        tools = RepairToolExecutor(staging, state, objective)
        state.generator_session.conversation.append({
            "role": "user",
            "objective_id": objective.objective_id,
            "objective_kind": objective.objective_kind,
            "working_patch_hash": state.graph_stack.patch_hash,
        })
        if hasattr(self.generator_agent, "revise"):
            response = self.generator_agent.revise(objective, tools, initial=initial)
        elif hasattr(self.generator_agent, "revise_working_patch"):
            response = self.generator_agent.revise_working_patch(state, objective, staging, tools)
        else:
            raise TypeError("generator agent must implement revise or revise_working_patch")
        if isinstance(response, GeneratorResult):
            state.generator_session.structure_recovery_used = (
                state.generator_session.structure_recovery_used
                or response.structure_recovery_attempted
            )
            state.generator_session.conversation.append({
                "role": "assistant",
                "summary": response.summary,
                "mechanism": response.mechanism,
                "error_kind": response.error_kind,
            })
            self._record_attempt(
                state, objective, response, source, tools,
            )
            if response.error_kind or not response.has_new_nonempty_diff:
                discard_ephemeral_tree(staging, state.run_root)
            return response
        values = dict(response or {})
        incremental = diff_between(source, staging)
        error = values.get("error_kind")
        state.generator_session.structure_recovery_used = (
            state.generator_session.structure_recovery_used
            or bool(values.get("recovery_used", False))
        )
        state.generator_session.conversation.append({
            "role": "assistant",
            "summary": str(values.get("summary", "")),
            "mechanism": str(values.get("mechanism", "causal_edit")),
            "error_kind": str(error) if error else None,
        })
        if error:
            incremental_text = ""
        else:
            incremental_text = incremental.canonical_diff
        result = GeneratorResult(
            result_id=stable_id(
                "generator-result", objective.objective_id,
                incremental.patch_hash, values,
            ),
            incremental_diff=incremental_text,
            mechanism=str(values.get("mechanism", "causal_edit")),
            summary=str(values.get("summary", "")),
            modified_tree=str(staging) if incremental_text else None,
            error_kind=str(error) if error else None,
            structure_recovery_attempted=bool(values.get("recovery_used", False)),
        )
        self._record_attempt(state, objective, result, source, tools)
        if not result.has_new_nonempty_diff:
            discard_ephemeral_tree(staging, state.run_root)
        return result

    @staticmethod
    def _record_attempt(
        state: ReachAvoidState,
        objective: RepairObjective,
        result: GeneratorResult,
        source: Path,
        tools: RepairToolExecutor,
    ) -> None:
        """Persist the facts needed by the next revision, not model prose."""

        incremental = diff_between(source, Path(result.modified_tree)) if (
            result.modified_tree and Path(result.modified_tree).exists()
        ) else diff_between(source, source)
        tool_summary = tools.attempt_summary()
        record = {
            "attempt_id": stable_id(
                "generator-attempt", state.generator_session.session_id,
                state.generator_attempt_count + 1, objective.objective_id,
                incremental.patch_hash, result.error_kind,
            ),
            "objective_id": objective.objective_id,
            "objective_kind": objective.objective_kind,
            "source_patch_hash": state.graph_stack.patch_hash,
            "incremental_patch_hash": incremental.patch_hash,
            "incremental_diff_hash": content_hash(incremental.canonical_diff),
            "incremental_diff": incremental.canonical_diff,
            "changed_files": incremental.changed_files,
            "changed_hunk_ids": tuple(hunk.hunk_id for hunk in incremental.hunks),
            "changed_symbols": incremental.changed_symbols,
            "tool_calls": tool_summary["tool_calls"],
            "validation": tool_summary["validation"],
            "result_kind": (
                "GENERATOR_ERROR" if result.error_kind else
                "NO_NEW_DIFF" if incremental.empty else "PENDING_TRANSITION"
            ),
            "error_kind": result.error_kind,
            "model_summary": result.summary[-1200:],
            "model_mechanism": result.mechanism[-400:],
        }
        state.generator_session.attempt_history.append(record)
