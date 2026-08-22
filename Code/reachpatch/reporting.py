from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class PatchOutcomeComparison:
    instance_id: str
    initial_resolved: bool
    final_resolved: bool

    @property
    def outcome(self) -> str:
        if not self.initial_resolved and self.final_resolved:
            return "IMPROVED"
        if self.initial_resolved and not self.final_resolved:
            return "REGRESSED"
        return "UNCHANGED"


def summarize_patch_outcomes(
    comparisons: Iterable[PatchOutcomeComparison],
) -> dict[str, object]:
    """Summarize an external p0/final evaluation without feeding the controller."""

    values = tuple(comparisons)
    counts = {"IMPROVED": 0, "REGRESSED": 0, "UNCHANGED": 0}
    for comparison in values:
        counts[comparison.outcome] += 1
    total = len(values)
    denominator = total or 1
    return {
        "total": total,
        "improved": counts["IMPROVED"],
        "regressed": counts["REGRESSED"],
        "unchanged": counts["UNCHANGED"],
        "improved_ratio": counts["IMPROVED"] / denominator,
        "regressed_ratio": counts["REGRESSED"] / denominator,
        "unchanged_ratio": counts["UNCHANGED"] / denominator,
        "net_improvement": counts["IMPROVED"] - counts["REGRESSED"],
        "outcomes": [
            {
                "instance_id": comparison.instance_id,
                "initial_resolved": comparison.initial_resolved,
                "final_resolved": comparison.final_resolved,
                "outcome": comparison.outcome,
            }
            for comparison in values
        ],
    }


def summarize_external_outcomes(path: str | Path) -> dict[str, object]:
    """Load externally produced p0/final outcomes; this is reporting-only."""

    source = Path(path).resolve()
    raw = json.loads(source.read_text(encoding="utf-8"))
    rows = raw.get("outcomes", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("external outcome report must be a list or an outcomes object")
    comparisons = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("external outcome entries must be objects")
        required = {"instance_id", "initial_resolved", "final_resolved"}
        if not required.issubset(row):
            raise ValueError("external outcome entry is missing p0/final resolution fields")
        comparisons.append(PatchOutcomeComparison(
            instance_id=str(row["instance_id"]),
            initial_resolved=bool(row["initial_resolved"]),
            final_resolved=bool(row["final_resolved"]),
        ))
    return summarize_patch_outcomes(comparisons)


def export_patch(run_root: str | Path, output: str | Path | None = None) -> Path:
    root = Path(run_root).resolve()
    source = root / "final.patch"
    if not source.is_file():
        raise FileNotFoundError(source)
    destination = Path(output).resolve() if output else source
    if destination != source:
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def build_run_report(run_root: str | Path) -> dict:
    root = Path(run_root).resolve()
    terminal = json.loads((root / "terminal.json").read_text(encoding="utf-8"))
    return terminal["result"]
