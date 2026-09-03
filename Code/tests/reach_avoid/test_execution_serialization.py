from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

from reachpatch.models.execution import AtomicProgress, StateCheckpoint, TransitionCertificate
from reachpatch.reach_avoid.execution_checkpoint import (
    ExecutionCheckpointStore, record_from_dict,
)
from reachpatch.reach_avoid.dynamic_failure_graph import (
    DynamicFailureGraph, DynamicFailureGraphBudget, build_dynamic_failure_graph,
)


def test_checkpoint_certificate_round_trip(tmp_path):
    toy = runpy.run_path("tests/reach_avoid/test_execution_toy_e2e.py")
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "calc.py").write_text("def calc():\n    return None\n", encoding="utf-8")
    from reachpatch.models.core import Instance
    from reachpatch.reach_avoid.controller import ReachAvoidController
    from reachpatch.reach_avoid.repair_player import RepairPlayer
    instance = Instance(
        "toy-serialization", str(repository), "base", "calc must return numbers.Real.",
        public_metadata={"public_checks": ({
            "check_id": "target",
            "command": ("python", "-c", "from calc import calc; import numbers; assert calc() is numbers.Real"),
            "role": "TARGET", "authority": "A", "symbol_references": ("calc",),
        },)},
    )
    run_root = tmp_path / "run"
    result = ReachAvoidController(RepairPlayer(toy["_ImportRepair"]())).run(instance, run_root=run_root)
    assert result.status == "REACHED"
    store = ExecutionCheckpointStore(run_root)
    checkpoints = [store.load(path.name) for path in (run_root / "execution_checkpoints").iterdir() if (path / "checkpoint.json").exists()]
    assert checkpoints
    for checkpoint in checkpoints:
        restored = record_from_dict(StateCheckpoint, checkpoint.to_dict())
        assert restored.patch_hash == checkpoint.patch_hash
        assert restored.checkpoint_id == checkpoint.checkpoint_id
        assert restored.locked_checks == checkpoint.locked_checks
    certificates = [
        record_from_dict(TransitionCertificate, json.loads(path.read_text(encoding="utf-8")))
        for path in (run_root / "transitions").glob("*.json")
    ]
    assert certificates
    for certificate in certificates:
        restored = record_from_dict(TransitionCertificate, certificate.to_dict())
        assert restored.parent_patch_hash == certificate.parent_patch_hash
        assert restored.observation_hashes == certificate.observation_hashes
        assert set(restored.atomic_progress) == set(certificate.atomic_progress)
        for progress in restored.atomic_progress.values():
            assert record_from_dict(AtomicProgress, progress.to_dict()).check_id == progress.check_id
    state = store.read_state()
    assert state.working_checkpoint.checkpoint_id == result.checkpoint_id
    assert state.certified_checkpoint is not None
    assert state.certified_checkpoint.checkpoint_id == result.checkpoint_id
    assert state.revision_count >= 1


def test_dynamic_failure_graph_json_round_trip():
    from types import SimpleNamespace
    graph = build_dynamic_failure_graph(
        Path("."), Path("."),
        "diff --git a/calc.py b/calc.py\n+++ b/calc.py\n@@ -1,1 +1,1 @@\n+return 1\n",
        SimpleNamespace(failure_id="f", same_signature_count=2),
        SimpleNamespace(executed_line_ids=("calc.py:1",), events=()), None,
        DynamicFailureGraphBudget(),
    )
    restored = record_from_dict(DynamicFailureGraph, graph.to_dict())
    assert restored.graph_id == graph.graph_id
    assert restored.nodes.keys() == graph.nodes.keys()
    assert restored.edges.keys() == graph.edges.keys()


def test_state_round_trip_restores_dynamic_failure_graph(tmp_path):
    from reachpatch.models.core import Instance
    from reachpatch.reach_avoid.controller import ReachAvoidController
    from reachpatch.reach_avoid.repair_player import RepairPlayer

    # Use the existing serializable toy run to obtain a valid execution state.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def calc():\n    return 1\n", encoding="utf-8")
    instance = Instance(
        "graph-state", str(repo), "base", "calc must return 2.",
        public_metadata={"public_checks": ({
            "check_id": "target", "command": ("python", "-c", "from calc import calc; assert calc() == 2"),
            "authority": "A", "symbol_references": ("calc",),
        },)},
    )
    class InitialPatch:
        def revise(self, objective, tools, initial=False):
            tools.apply_patch(
                "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def calc():\n-    return 1\n+    return 2\n"
            )
            tools.finish_revision("initial", "toy")
            return {}

    run_root = tmp_path / "run"
    ReachAvoidController(RepairPlayer(InitialPatch())).run(instance, run_root=run_root)
    store = ExecutionCheckpointStore(run_root)
    state = store.read_state()
    graph = build_dynamic_failure_graph(
        repo, repo, "", SimpleNamespace(failure_id="f", same_signature_count=2),
        SimpleNamespace(executed_line_ids=("calc.py:1",), events=()), None,
        DynamicFailureGraphBudget(),
    )
    state.dynamic_failure_graph = graph
    store.write_state(state)
    restored = store.read_state()
    assert isinstance(restored.dynamic_failure_graph, DynamicFailureGraph)
    assert restored.dynamic_failure_graph.digest() == graph.digest()
