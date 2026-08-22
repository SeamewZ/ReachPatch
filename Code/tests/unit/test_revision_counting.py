from __future__ import annotations

import json
from pathlib import Path

from reachpatch.models.core import Instance
from reachpatch.models.reach_avoid import GeneratorResult
from reachpatch.reach_avoid.controller import ReachAvoidController
from reachpatch.reach_avoid.controller import ReachAvoidConfig
from reachpatch.reach_avoid.gates import evaluate_reach
from reachpatch.models.graphs import ChallengeStatus
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.reach_avoid.transition import evaluate_trial_transition


_PATCH = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def calc():
-    return 1
+    return 2
"""


class InitialGenerator:
    def revise(self, objective, tools, initial=False):
        tools.apply_patch(_PATCH)
        tools.finish_revision("initial")
        return {"summary": "initial", "mechanism": "return"}


class EmptyThenInitialGenerator:
    def __init__(self, empty_attempts: int) -> None:
        self.empty_attempts = empty_attempts
        self.calls = 0
        self.failed_mechanisms = []
        self.attempt_history = []

    def revise(self, objective, tools, initial=False):
        self.calls += 1
        self.failed_mechanisms.append(objective.failed_mechanisms)
        self.attempt_history.append(
            tuple(tools.state.generator_session.attempt_history)
        )
        if self.calls <= self.empty_attempts:
            return {
                "summary": "upstream generator returned no usable action",
                "mechanism": "external-empty-response",
                "error_kind": "EMPTY_RESPONSE",
            }
        tools.apply_patch(_PATCH)
        tools.finish_revision("initial")
        return {"summary": "initial", "mechanism": "return"}


class InitialThenEmptyGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def revise(self, objective, tools, initial=False):
        self.calls += 1
        if initial:
            tools.apply_patch(_PATCH)
            tools.finish_revision("initial partial patch")
            return {"summary": "initial partial patch", "mechanism": "return-two"}
        return {
            "summary": "the generator repeated a rejected response",
            "mechanism": "repeated-rejected-patch",
            "error_kind": "REPEATED_REJECTED_PATCH",
        }


class CapturingController(ReachAvoidController):
    def _output_single_patch(self, state, checkpoint, status):
        self.final_state = state
        return super()._output_single_patch(state, checkpoint, status)


def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "calc.py").write_text("def calc():\n    return 1\n", encoding="utf-8")
    return root


def test_initial_patch_does_not_consume_revision(tmp_path):
    root = repository(tmp_path)
    controller = ReachAvoidController(RepairPlayer(InitialGenerator()))
    state = controller.initialize(
        Instance("instance", str(root), "base", "`calc` must return 2."),
        run_root=tmp_path / "run",
    )
    controller.generate_initial_patch(state)
    controller.initialize_graph_stack(state)
    assert state.repair_revision_count == 0
    assert state.working_checkpoint.canonical_diff


def test_initial_objective_contains_bounded_non_normative_public_context(tmp_path):
    root = repository(tmp_path)
    controller = ReachAvoidController(RepairPlayer(InitialGenerator()))
    discussion = "public implementation discussion " * 1000
    state = controller.initialize(
        Instance(
            "public-context", str(root), "base",
            "`calc` must return 2.\n\nPublic maintainer hints:\n" + discussion,
        ),
        run_root=tmp_path / "run-context",
    )

    objective = controller._initial_objective(state)

    assert objective.public_context[0]["normative"] is True
    assert objective.public_context[0]["content"] == "`calc` must return 2."
    assert objective.public_context[1]["normative"] is False
    assert objective.public_context[1]["authority"] == "PROVISIONAL"
    assert len(objective.public_context[1]["content"]) == 16000


def test_empty_response_does_not_consume_revision(state_factory):
    state = state_factory()
    result = GeneratorResult("result", "", "none", "empty", error_kind="EMPTY_RESPONSE")
    assert not result.has_new_nonempty_diff
    assert state.repair_revision_count == 0


def test_initial_empty_response_retries_from_unchanged_bootstrap(tmp_path):
    root = repository(tmp_path)
    (root / "target_check.py").write_text(
        "from calc import calc\nassert calc() == 2\n", encoding="utf-8",
    )
    generator = EmptyThenInitialGenerator(empty_attempts=1)
    controller = CapturingController(RepairPlayer(generator))
    result = controller.run(
        Instance(
            "initial-retry", str(root), "base", "`calc` must return 2.",
            public_metadata={"public_checks": ({
                "check_id": "target-check",
                "command": ("python", "target_check.py"),
                "role": "TARGET",
                "authority": "A",
                "symbol_references": ("calc",),
            },)},
        ),
        run_root=tmp_path / "run-retry",
    )
    attempts = [
        json.loads(line)
        for line in (tmp_path / "run-retry" / "generator_attempts.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    assert result.status == "REACHED"
    assert generator.calls == 2
    assert generator.attempt_history[0] == ()
    assert generator.attempt_history[1][0]["result_kind"] == "GENERATOR_ERROR"
    assert generator.attempt_history[1][0]["error_kind"] == "EMPTY_RESPONSE"
    assert [item["attempt"] for item in attempts] == [1, 2]
    assert controller.final_state.generator_attempt_count == 2
    assert controller.final_state.repair_revision_count == 0
    assert not (tmp_path / "run-retry" / "bootstrap_working").exists()
    assert not (tmp_path / "run-retry" / "initial_working").exists()


def test_two_initial_empty_responses_block_without_patch_revision(tmp_path):
    root = repository(tmp_path)
    generator = EmptyThenInitialGenerator(empty_attempts=2)
    controller = CapturingController(RepairPlayer(generator))
    result = controller.run(
        Instance("initial-blocked", str(root), "base", "`calc` must return 2."),
        run_root=tmp_path / "run-blocked",
    )
    bootstrap = tmp_path / "run-blocked" / "bootstrap_working"
    assert result.status == "GENERATOR_BLOCKED_EXTERNAL"
    assert result.unified_diff == ""
    assert generator.calls == 2
    assert controller.final_state.generator_attempt_count == 2
    assert controller.final_state.repair_revision_count == 0
    assert (bootstrap / "calc.py").read_text(encoding="utf-8") == (
        "def calc():\n    return 1\n"
    )
    assert not (tmp_path / "run-blocked" / "initial_working").exists()
    assert not any((tmp_path / "run-blocked" / "generator_staging").iterdir())


def test_repair_nonprogress_seals_best_working_patch_without_revision(tmp_path):
    root = repository(tmp_path)
    (root / "target_check.py").write_text(
        "from calc import calc\nassert calc() == 3\n", encoding="utf-8",
    )
    generator = InitialThenEmptyGenerator()
    controller = CapturingController(RepairPlayer(generator))

    result = controller.run(
        Instance(
            "repair-frontier-exhausted", str(root), "base", "`calc` must return 3.",
            public_metadata={"public_checks": ({
                "check_id": "target-check",
                "command": ("python", "target_check.py"),
                "role": "TARGET",
                "authority": "A",
                "symbol_references": ("calc",),
            },)},
        ),
        run_root=tmp_path / "run-repair-frontier-exhausted",
    )

    assert result.status == "BEST_EFFORT_FRONTIER_EXHAUSTED"
    assert result.unified_diff == _PATCH
    assert generator.calls == 3
    assert controller.final_state.repair_revision_count == 0
    assert controller.final_state.generator_attempt_count == 3


def test_same_diff_does_not_consume_revision(state_factory):
    state = state_factory()
    result = GeneratorResult(
        "result", "diff --git a/calc.py b/calc.py\n", "same", "same",
        modified_tree=state.working_checkpoint.snapshot_tree,
    )
    transition = evaluate_trial_transition(state, result)
    assert not transition.trial_patch_changed
    assert not transition.entered_evaluation
    assert state.repair_revision_count == 0


def test_eight_real_revisions_is_maximum():
    assert ReachAvoidConfig().max_real_patch_revisions == 8


def test_reach_stops_before_eight(state_factory):
    state = state_factory(target_status=ChallengeStatus.PASS, stability_runs=2)
    assert state.repair_revision_count == 0
    assert evaluate_reach(state).reached
