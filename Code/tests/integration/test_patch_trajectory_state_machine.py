import json
import shutil
import sys
from pathlib import Path

from reachpatch.models.core import Instance
from reachpatch.program_graph.slice import ContextRequest
from reachpatch.reach_avoid.controller import ReachPatchConfig, ReachPatchController
from reachpatch.repair.deepseek_agent import (
    GeneratorRevision, PersistentDeepSeekAgent,
)
from reachpatch.repair.tools import ProposedEdit


FIXTURE = Path(__file__).parents[1] / "fixtures" / "simple_repo"


def _tool(name, arguments, turn):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": f"turn-{turn}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }],
    }


class _Edits:
    def __init__(self, edits):
        self.edits = list(edits)
        self.turn = 0

    def __call__(self, messages, schemas):
        self.turn += 1
        revision = (self.turn - 1) // 2
        if self.turn % 2:
            edit = self.edits[min(revision, len(self.edits) - 1)]
            return _tool("apply_edits", {
                "mechanism": (
                    "initial_issue_repair" if revision == 0
                    else "causal_slice_rewrite"
                ),
                "edits": [edit],
            }, self.turn)
        return _tool("finish_revision", {
            "summary": "reviewed the complete working diff",
        }, self.turn)


def _edit(expected, replacement, start=40, end=40):
    return {
        "relative_path": "pkg/api.py",
        "start_line": start,
        "end_line": end,
        "expected_source": expected,
        "replacement": replacement,
    }


def _controller(tmp_path, transport, *, revisions, target=True):
    target_command = (
        sys.executable,
        "-c",
        "import sys; from pkg.api import Box, public; "
        "sys.exit(public(Box([2])) != [])",
    )
    return ReachPatchController(
        config=ReachPatchConfig(
            max_submitted_revisions=revisions,
            max_total_revisions=revisions,
            mechanical_commands=(target_command,) if target else (),
            target_recovery_wall_time_s=10,
        ),
        generator_agent=PersistentDeepSeekAgent(transport, max_tool_turns=4),
        implementation_root=tmp_path,
    )


def _run(tmp_path, transport, *, revisions, target=True):
    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository)
    controller = _controller(
        tmp_path, transport, revisions=revisions, target=target,
    )
    return controller.run(
        Instance(
            "trajectory-fixture",
            str(repository),
            "base",
            "For every Box value, pkg.api.public(value) must return an empty list "
            "while existing list inputs must remain normalized.",
        ),
        run_root=tmp_path / "run",
    )


def test_no_trusted_target_outputs_first_patch_without_revision(tmp_path):
    transport = _Edits((
        _edit(
            "    return normalize(value)",
            "    return normalize(value)  # issue behavior remains unverified",
        ),
    ))
    state, certificate = _run(
        tmp_path, transport, revisions=2, target=False,
    )
    assert certificate.status == "EVIDENCE_LIMITED_COMPLETE"
    assert state.runtime_metrics["confirmed_revision_count"] == 0
    assert state.patch_trajectory.first_patch.patch_hash == (
        state.patch_trajectory.best_evidence_patch.patch_hash
    )
    assert "issue behavior remains unverified" in (
        state.checkpoint.patch.canonical_diff
    )


def test_initial_mechanical_rejection_preserves_first_patch(tmp_path):
    transport = _Edits((
        _edit(
            "REGISTRY = {}",
            "from pkg.api import public\n\nREGISTRY = {}",
            start=1,
            end=1,
        ),
    ))
    state, certificate = _run(
        tmp_path, transport, revisions=2, target=False,
    )

    assert state.patch_trajectory is not None
    assert state.patch_trajectory.first_patch.patch.canonical_diff
    assert state.patch_trajectory.first_patch.patch_hash == (
        state.patch_trajectory.best_evidence_patch.patch_hash
    )
    assert state.checkpoint.patch.canonical_diff
    assert state.runtime_metrics["first_patch_preserved_after_rejection"] is True
    assert certificate.status == "EVIDENCE_LIMITED_COMPLETE"


def test_initial_context_request_continues_same_first_patch_process(
    tmp_path, monkeypatch,
):
    class ContextThenEditAgent:
        def __init__(self):
            self.calls = 0
            self.max_revisions = 0
            self.max_tool_turns = 0
            self.max_wall_time_seconds = 0.0
            self.max_completion_tokens = 0

        def generate_initial_patch(self, state, conversation, tools):
            self.calls += 1
            if self.calls == 1:
                return GeneratorRevision(
                    revision_id="context", mechanism="initial_issue_repair",
                    edits=(), summary="need direct dependency",
                    context_requests=(ContextRequest(
                        symbols=("pkg.api.normalize",),
                        relation_kinds=("calls",),
                    ),),
                    requested_public_checks=(), tool_turns=2,
                    status="CONTEXT_ONLY",
                )
            edit = ProposedEdit(
                relative_path="pkg/api.py", start_line=40, end_line=40,
                expected_source="    return normalize(value)",
                replacement=(
                    "    return [] if isinstance(value, Box) else normalize(value)"
                ),
            )
            tools.apply_edits((edit,))
            revision = GeneratorRevision(
                revision_id="first-patch", mechanism="initial_issue_repair",
                edits=(edit,), summary="reviewed complete first patch",
                context_requests=(), requested_public_checks=(), tool_turns=2,
                status="PROPOSED",
            )
            state.runtime_metrics["first_patch_readiness"] = {
                "target_definition_read": True,
                "root_cause_identified": True,
                "requirements_accounted_for": True,
                "preservation_risks_identified": True,
                "final_diff_reviewed": True,
            }
            return revision

        def generate_target_reproduction(self, **kwargs):
            return None

    repository = tmp_path / "repo"
    shutil.copytree(FIXTURE, repository)
    agent = ContextThenEditAgent()
    controller = ReachPatchController(
        config=ReachPatchConfig(
            max_submitted_revisions=1,
            max_total_revisions=1,
            target_recovery_wall_time_s=1,
        ),
        generator_agent=agent,
        implementation_root=tmp_path,
    )
    monkeypatch.setattr(controller, "_expand_generator_context", lambda *_: True)

    state, certificate = controller.run(
        Instance(
            "initial-context-fixture", str(repository), "base",
            "pkg.api.public(value) must return an empty list for every Box value.",
        ),
        run_root=tmp_path / "run-context",
    )

    assert agent.calls == 2
    assert agent.max_revisions == 3
    assert state.runtime_metrics["initial_context_continuation_count"] == 1
    assert state.runtime_metrics["confirmed_revision_count"] == 0
    assert state.transition_index == 1
    assert state.patch_trajectory.first_patch.patch.canonical_diff
    assert certificate.status == "EVIDENCE_LIMITED_COMPLETE"


def test_confirmed_failure_revision_promotes_execution_improvement(tmp_path):
    transport = _Edits((
        _edit("    return normalize(value)", "    return [1]"),
        _edit(
            "    return [1]",
            "    return [] if isinstance(value, Box) else normalize(value)",
        ),
    ))
    state, certificate = _run(tmp_path, transport, revisions=1)
    assert certificate.status == "REACHED"
    assert state.runtime_metrics["confirmed_revision_count"] == 1
    assert state.patch_trajectory.revision_history[-1].decision == "PROMOTE"
    assert state.patch_trajectory.best_evidence_patch.patch_hash != (
        state.patch_trajectory.first_patch.patch_hash
    )


def test_revision_without_confirmed_improvement_rolls_back_to_first(tmp_path):
    transport = _Edits((
        _edit("    return normalize(value)", "    return [1]"),
        _edit("    return [1]", "    return [2]"),
    ))
    state, _certificate = _run(tmp_path, transport, revisions=1)
    assert state.patch_trajectory.revision_history[-1].decision == "ROLLBACK"
    assert state.patch_trajectory.best_evidence_patch.patch_hash == (
        state.patch_trajectory.first_patch.patch_hash
    )
    assert "return [1]" in state.checkpoint.patch.canonical_diff
    assert "return [2]" not in state.checkpoint.patch.canonical_diff


def test_target_fixed_regression_is_repaired_before_promotion(tmp_path):
    initial = (
        "    if isinstance(value, Box):\n"
        "        return [1]\n"
        "    return normalize(value)"
    )
    corrected = (
        "    if isinstance(value, Box):\n"
        "        return []\n"
        "    return normalize(value)"
    )
    transport = _Edits((
        _edit("    return normalize(value)", initial),
        _edit(initial, "    return []", 40, 42),
        _edit("    return []", corrected),
    ))
    state, certificate = _run(tmp_path, transport, revisions=2)
    decisions = [item.decision for item in state.patch_trajectory.revision_history]
    assert decisions == ["KEEP_TRIAL_FOR_REGRESSION_REPAIR", "PROMOTE"]
    assert certificate.status == "REACHED"
    assert "isinstance(value, Box)" in state.checkpoint.patch.canonical_diff
    assert not state.patch_trajectory.best_evidence_patch.preservation_regression_ids
