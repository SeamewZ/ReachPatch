from __future__ import annotations

import json

from reachpatch.reporting import (
    PatchOutcomeComparison, summarize_external_outcomes, summarize_patch_outcomes,
)


def test_outcome_accounting_distinguishes_method_improvement_and_regression(tmp_path):
    summary = summarize_patch_outcomes((
        PatchOutcomeComparison("improved", False, True),
        PatchOutcomeComparison("regressed", True, False),
        PatchOutcomeComparison("unchanged-pass", True, True),
        PatchOutcomeComparison("unchanged-fail", False, False),
    ))
    assert summary["improved"] == summary["regressed"] == 1
    assert summary["unchanged"] == 2
    assert summary["net_improvement"] == 0
    assert summary["improved_ratio"] == 0.25

    source = tmp_path / "external.json"
    source.write_text(json.dumps({"outcomes": summary["outcomes"]}), encoding="utf-8")
    assert summarize_external_outcomes(source)["total"] == 4
