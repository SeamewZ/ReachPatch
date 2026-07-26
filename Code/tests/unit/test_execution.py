from __future__ import annotations

from pathlib import Path

from reachpatch.challenge_graph.recipes import InputRecipe
from reachpatch.execution import TraceExecutor


FIXTURE = Path(__file__).parents[1] / "fixtures" / "simple_repo"


def test_worker_trace_contains_repository_code_not_interpreter_pseudo_files(tmp_path):
    recipe = InputRecipe.create(
        imports=({"op": "import", "module": "pkg.api", "as": "api"},),
        stimulus=({
            "op": "call",
            "target": "api.public",
            "args": [[1]],
            "kwargs": {},
            "save_as": "result",
        },),
        observations=({"op": "observe", "channel": "return", "source": "result"},),
    )

    bundle = TraceExecutor(temporary_root=tmp_path / "exec").execute_recipe(
        recipe,
        FIXTURE,
        repository_role="BASE",
    )

    events = [event for run in bundle.runs for event in run.trace_events]
    assert bundle.stability_status == "STABLE"
    assert events
    assert len(events) < 200
    assert all(not event.file.startswith("<") for event in events)
    assert {event.file for event in events} <= {"pkg/__init__.py", "pkg/api.py"}
