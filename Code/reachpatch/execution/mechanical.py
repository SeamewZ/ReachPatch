from __future__ import annotations

import ast
from pathlib import Path

from reachpatch.models.evidence import ActualDiff
from reachpatch.models.graphs import ExecutableScenario
from reachpatch.models.reach_avoid import MechanicalResult
from reachpatch.execution.trace import run_trace


_FORBIDDEN_ROOTS = ("tests/", "test/", "artifacts/", ".git/", "generated/")


def run_mechanical_checks(
    trial_tree: Path,
    cumulative_diff: ActualDiff,
    commands: tuple[tuple[str, ...], ...] = (),
    command_scenarios: tuple[ExecutableScenario, ...] = (),
    oracle_paths: tuple[str, ...] = (),
    source_tree: Path | None = None,
) -> MechanicalResult:
    reasons: list[str] = []
    results: list[dict[str, object]] = []
    protected = {
        path.replace("\\", "/").removeprefix("./")
        for path in oracle_paths
    }
    forbidden = any(path.startswith(_FORBIDDEN_ROOTS) for path in cumulative_diff.changed_files)
    contamination = bool(protected.intersection(cumulative_diff.changed_files))
    if forbidden:
        reasons.append("trial edits tests, artifacts, generated files, or repository metadata")
    if contamination:
        reasons.append("trial contains non-public oracle material")
    for relative in cumulative_diff.changed_files:
        path = trial_tree / relative
        if path.suffix != ".py" or not path.is_file():
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=relative)
        except SyntaxError as exc:
            reasons.append(f"syntax error in {relative}:{exc.lineno}: {exc.msg}")
        if source_tree is not None:
            original = source_tree / relative
            if original.is_file():
                try:
                    before_tree = ast.parse(
                        original.read_text(encoding="utf-8", errors="replace"),
                        filename=relative,
                    )
                    after_tree = ast.parse(
                        path.read_text(encoding="utf-8", errors="replace"),
                        filename=relative,
                    )
                except SyntaxError:
                    # The syntax diagnostics above remain authoritative.
                    continue
                if ast.dump(before_tree, include_attributes=False) == ast.dump(
                    after_tree, include_attributes=False,
                ):
                    reasons.append(
                        f"no executable AST change in {relative}; the patch is "
                        "comments/formatting or an unchanged source excerpt"
                    )
    scenarios = tuple(command_scenarios) + tuple(
        ExecutableScenario(
            scenario_id=f"mechanical:{index}", command=command, cwd=".",
            environment=(), timeout_seconds=120.0,
        )
        for index, command in enumerate(commands)
    )
    for scenario in scenarios:
        # Mechanical command execution shares the same backend as challenges,
        # probes and generator validations.  In particular, cwd, environment
        # and timeout are not silently replaced with host-process defaults.
        trace = run_trace(
            trial_tree, scenario.command, cwd=scenario.cwd,
            environment=scenario.environment,
            timeout_seconds=scenario.timeout_seconds, trace_enabled=False,
        )
        observation = trace.observation
        backend = (
            "CONTAINER"
            if dict(scenario.environment).get("REACHPATCH_EXECUTION_IMAGE")
            else "HOST"
        )
        results.append({
            "command": scenario.command,
            "cwd": scenario.cwd,
            "environment": scenario.environment,
            "timeout_seconds": scenario.timeout_seconds,
            "backend": backend,
            "return_code": observation.return_code,
            "stdout": observation.stdout[-4000:],
            "stderr": observation.stderr[-4000:],
            "duration_seconds": observation.duration_seconds,
            "timeout": observation.exception == "TIMEOUT",
            "first_project_frame": trace.first_project_frame,
        })
        if observation.return_code != 0:
            reasons.append(
                f"mechanical command failed: {' '.join(scenario.command)}"
            )
    removed_public = {
        line[1:].split("(", 1)[0].split(":", 1)[0].strip()
        for hunk in cumulative_diff.hunks for line in hunk.lines
        if line.startswith(("-def ", "-class "))
    }
    added_public = {
        line[1:].split("(", 1)[0].split(":", 1)[0].strip()
        for hunk in cumulative_diff.hunks for line in hunk.lines
        if line.startswith(("+def ", "+class "))
    }
    unsafe_api = bool(removed_public - added_public)
    if unsafe_api:
        reasons.append("trial deletes a public definition")
    return MechanicalResult(
        passed=not reasons,
        failure_reasons=tuple(reasons),
        forbidden_edit=forbidden,
        oracle_contamination=contamination,
        unsafe_api_break=unsafe_api,
        high_risk_side_effect=False,
        command_results=tuple(results),
    )
