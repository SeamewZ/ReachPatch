from __future__ import annotations

import ast
import subprocess
import time
from pathlib import Path

from reachpatch.models.evidence import ActualDiff
from reachpatch.models.reach_avoid import MechanicalResult


_FORBIDDEN_ROOTS = ("tests/", "test/", "artifacts/", ".git/", "generated/")


def run_mechanical_checks(
    trial_tree: Path,
    cumulative_diff: ActualDiff,
    commands: tuple[tuple[str, ...], ...] = (),
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
    for command in commands:
        started = time.monotonic()
        try:
            result = subprocess.run(
                command, cwd=trial_tree, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=120, check=False,
            )
            results.append({
                "command": command,
                "return_code": result.returncode,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
                "duration_seconds": time.monotonic() - started,
            })
            if result.returncode != 0:
                reasons.append(f"mechanical command failed: {' '.join(command)}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            reasons.append(f"mechanical command unavailable: {' '.join(command)}: {exc}")
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
