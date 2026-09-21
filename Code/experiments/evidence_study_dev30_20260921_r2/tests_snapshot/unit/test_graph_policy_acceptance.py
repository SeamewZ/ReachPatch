import json
from pathlib import Path

from experiments.graph_policy_acceptance import evaluate_diagnostic


def test_unsealed_diagnostic_never_reads_official_data(tmp_path, monkeypatch):
    original = Path.read_text
    def guarded(path, *args, **kwargs):
        assert "harness" not in path.parts, "official data opened before all-case seal"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    report = evaluate_diagnostic(tmp_path)
    assert report["status"] == "NOT_COMPLETED"
    assert report["unmet"] == ["ALL_TEN_PATCHES_NOT_SEALED"]


def test_incomplete_case_set_cannot_pass_seal_gate(tmp_path):
    (tmp_path / "sealed_generation.json").write_text(json.dumps({"case_count": 10, "instance_ids": ["one"]}))
    report = evaluate_diagnostic(tmp_path)
    assert report["status"] == "NOT_COMPLETED"
    assert report["unmet"] == ["INVALID_TEN_CASE_SEAL"]
