from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reachpatch.models.base import SCHEMA_VERSION, SerializableRecord, content_hash, stable_id, utc_now
from reachpatch.models.enums import Authority, Confidence


@dataclass(frozen=True, slots=True)
class ArtifactEnvelope(SerializableRecord):
    artifact_id: str
    artifact_type: str
    schema_version: str
    instance_id: str
    parent_ids: tuple[str, ...]
    content_hash: str
    producer: str
    created_at: str
    authority: str
    confidence: str
    status: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        artifact_type: str,
        instance_id: str,
        payload: dict[str, Any],
        parent_ids: tuple[str, ...] = (),
        producer: str,
        authority: Authority | str = Authority.PROVISIONAL,
        confidence: Confidence | str = Confidence.UNKNOWN,
        status: str = "ACTIVE",
        schema_version: str = SCHEMA_VERSION,
        artifact_id: str | None = None,
        created_at: str | None = None,
    ) -> "ArtifactEnvelope":
        payload_hash = content_hash(payload)
        envelope_id = artifact_id or stable_id(
            artifact_type,
            instance_id,
            payload_hash,
            parent_ids,
            producer,
            status,
        )
        return cls(
            artifact_id=envelope_id,
            artifact_type=artifact_type,
            schema_version=schema_version,
            instance_id=instance_id,
            parent_ids=tuple(parent_ids),
            content_hash=payload_hash,
            producer=producer,
            created_at=created_at or utc_now(),
            authority=authority.value if isinstance(authority, Authority) else authority,
            confidence=confidence.value if isinstance(confidence, Confidence) else confidence,
            status=status,
            payload=payload,
        )

    def validate(self) -> None:
        required_strings = {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "producer": self.producer,
            "created_at": self.created_at,
            "authority": self.authority,
            "confidence": self.confidence,
            "status": self.status,
        }
        empty = [name for name, value in required_strings.items() if not isinstance(value, str) or not value]
        if empty:
            raise ValueError(f"artifact fields must be non-empty strings: {empty}")
        if not isinstance(self.payload, dict):
            raise TypeError("artifact payload must be an object")
        actual = content_hash(self.payload)
        if actual != self.content_hash:
            raise ValueError(f"artifact payload hash mismatch: expected {self.content_hash}, got {actual}")
        if len(self.content_hash) != 64:
            raise ValueError("artifact content_hash must be SHA-256")


@dataclass(slots=True)
class ArtifactIndex(SerializableRecord):
    schema_version: str = SCHEMA_VERSION
    by_id: dict[str, dict[str, str]] = field(default_factory=dict)
    by_type: dict[str, list[str]] = field(default_factory=dict)
    by_content_hash: dict[str, list[str]] = field(default_factory=dict)
    latest_by_instance_type: dict[str, str] = field(default_factory=dict)
    generation: int = 0

    def add(self, envelope: ArtifactEnvelope, relative_path: str) -> None:
        if envelope.artifact_id in self.by_id:
            existing = self.by_id[envelope.artifact_id]
            if existing["content_hash"] != envelope.content_hash:
                raise ValueError(f"artifact id collision: {envelope.artifact_id}")
            return
        self.by_id[envelope.artifact_id] = {
            "path": relative_path,
            "content_hash": envelope.content_hash,
            "instance_id": envelope.instance_id,
            "artifact_type": envelope.artifact_type,
            "created_at": envelope.created_at,
        }
        self.by_type.setdefault(envelope.artifact_type, []).append(envelope.artifact_id)
        self.by_content_hash.setdefault(envelope.content_hash, []).append(envelope.artifact_id)
        key = f"{envelope.instance_id}\0{envelope.artifact_type}"
        previous_id = self.latest_by_instance_type.get(key)
        previous = self.by_id.get(previous_id or "")
        if previous is None or (
            envelope.created_at,
            envelope.artifact_id,
        ) >= (
            previous["created_at"],
            previous_id or "",
        ):
            self.latest_by_instance_type[key] = envelope.artifact_id
        self.generation += 1
