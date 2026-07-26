from __future__ import annotations

import json

import pytest

from reachpatch.artifacts.store import ArtifactStore, ArtifactStoreError
from reachpatch.models.enums import Authority, Confidence


def evidence_payload(identifier: str, content: str = "must return the value") -> dict[str, str]:
    return {
        "evidence_id": identifier,
        "kind": "ISSUE_NORMATIVE",
        "source": "issue.md",
        "content": content,
    }


def test_put_get_content_lookup_latest_and_idempotency(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    first = store.put(
        "evidence",
        evidence_payload("e-1"),
        instance_id="instance-1",
        producer="unit-test",
        authority=Authority.A,
        confidence=Confidence.CONFIRMED,
    )
    same = store.put(
        "evidence",
        evidence_payload("e-1"),
        instance_id="instance-1",
        producer="unit-test",
        authority=Authority.A,
        confidence=Confidence.CONFIRMED,
    )
    second = store.put(
        "evidence",
        evidence_payload("e-2", "shall raise ValueError"),
        instance_id="instance-1",
        producer="unit-test",
        parent_ids=(first.artifact_id,),
        authority=Authority.A,
        confidence=Confidence.CONFIRMED,
    )

    assert same.artifact_id == first.artifact_id
    assert store.get(first.artifact_id).payload["evidence_id"] == "e-1"
    assert [item.artifact_id for item in store.find_by_content_hash(first.content_hash)] == [
        first.artifact_id
    ]
    assert store.latest("instance-1", "evidence").artifact_id == second.artifact_id
    assert store.verify() == {
        "valid": True,
        "checked": 2,
        "generation": 2,
        "errors": [],
    }


def test_schema_and_parent_validation_are_enforced(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="missing fields"):
        store.put("evidence", {"content": "x"}, instance_id="i", producer="test")
    with pytest.raises(ArtifactStoreError, match="unknown parent"):
        store.put(
            "evidence",
            evidence_payload("e"),
            instance_id="i",
            producer="test",
            parent_ids=("absent",),
        )


def test_recovery_rebuilds_index_and_materializes_jsonl(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    artifact = store.put(
        "evidence",
        evidence_payload("e-1"),
        instance_id="i",
        producer="test",
    )
    store.index_path.write_text("{}\n", encoding="utf-8")

    assert store.recover() == {"artifacts": 1, "generation": 1}
    assert store.get(artifact.artifact_id).content_hash == artifact.content_hash
    output = store.materialize_jsonl(
        "exports/evidence.jsonl", artifact_type="evidence", instance_id="i"
    )
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["artifact_id"] == artifact.artifact_id


def test_recovery_preserves_latest_by_created_at_not_object_path_order(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    older = store.put(
        "evidence", evidence_payload("older"), instance_id="i", producer="test"
    )
    newer = store.put(
        "evidence", evidence_payload("newer"), instance_id="i", producer="test"
    )
    assert store.latest("i", "evidence").artifact_id == newer.artifact_id

    store.recover()

    assert store.latest("i", "evidence").artifact_id == newer.artifact_id
    assert store.get(older.artifact_id).payload["evidence_id"] == "older"


def test_verify_reports_corrupt_object(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    artifact = store.put(
        "evidence",
        evidence_payload("e-1"),
        instance_id="i",
        producer="test",
    )
    index = json.loads(store.index_path.read_text(encoding="utf-8"))
    object_path = store.root / index["by_id"][artifact.artifact_id]["path"]
    raw = json.loads(object_path.read_text(encoding="utf-8"))
    raw["payload"]["content"] = "tampered"
    object_path.write_text(json.dumps(raw), encoding="utf-8")

    verification = store.verify()
    assert verification["valid"] is False
    assert "hash mismatch" in verification["errors"][0]
