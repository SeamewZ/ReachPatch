from __future__ import annotations

import dataclasses
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from reachpatch.artifacts.models import ArtifactEnvelope, ArtifactIndex
from reachpatch.artifacts.schema import DEFAULT_SCHEMA_REGISTRY, ArtifactSchemaRegistry
from reachpatch.models.base import SCHEMA_VERSION, canonical_json
from reachpatch.models.enums import Authority, Confidence


class ArtifactStoreError(RuntimeError):
    """Raised when persistent artifact invariants cannot be satisfied."""


class ArtifactStore:
    """Atomic, content-addressed artifact storage with a recoverable index."""

    def __init__(
        self,
        root: str | Path,
        *,
        schema_registry: ArtifactSchemaRegistry | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.objects_dir = self.root / "objects"
        self.index_path = self.root / "index.json"
        self.journal_path = self.root / "journal.jsonl"
        self.lock_path = self.root / ".lock"
        self.schema_registry = schema_registry or DEFAULT_SCHEMA_REGISTRY
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        if not self.index_path.exists():
            self._atomic_write_json(self.index_path, ArtifactIndex().to_dict())

    @contextmanager
    def _lock(self, exclusive: bool) -> Iterator[None]:
        with self.lock_path.open("a+b") as handle:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(handle.fileno(), mode)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _atomic_write_json(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(canonical_json(value))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _append_journal(self, envelope: ArtifactEnvelope, relative_path: str) -> None:
        entry = {
            "artifact_id": envelope.artifact_id,
            "artifact_type": envelope.artifact_type,
            "instance_id": envelope.instance_id,
            "content_hash": envelope.content_hash,
            "path": relative_path,
            "created_at": envelope.created_at,
        }
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json(entry))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _load_index_unlocked(self) -> ArtifactIndex:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError(f"cannot read artifact index: {exc}") from exc
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ArtifactStoreError(
                f"unsupported artifact index schema {raw.get('schema_version')!r}"
            )
        return ArtifactIndex(
            schema_version=raw["schema_version"],
            by_id=dict(raw.get("by_id", {})),
            by_type={key: list(value) for key, value in raw.get("by_type", {}).items()},
            by_content_hash={
                key: list(value) for key, value in raw.get("by_content_hash", {}).items()
            },
            latest_by_instance_type=dict(raw.get("latest_by_instance_type", {})),
            generation=int(raw.get("generation", 0)),
        )

    @staticmethod
    def _payload_dict(payload: Any) -> dict[str, Any]:
        if dataclasses.is_dataclass(payload):
            record = payload.to_dict() if hasattr(payload, "to_dict") else dataclasses.asdict(payload)
        elif isinstance(payload, dict):
            record = payload
        elif hasattr(payload, "to_dict"):
            record = payload.to_dict()
        else:
            raise TypeError("artifact payload must be a mapping or serializable dataclass")
        if not isinstance(record, dict):
            raise TypeError("artifact payload conversion did not produce an object")
        return record

    def put(
        self,
        artifact_type: str,
        payload: Any,
        *,
        instance_id: str,
        producer: str,
        parent_ids: tuple[str, ...] = (),
        authority: Authority | str = Authority.PROVISIONAL,
        confidence: Confidence | str = Confidence.UNKNOWN,
        status: str = "ACTIVE",
    ) -> ArtifactEnvelope:
        record = self._payload_dict(payload)
        self.schema_registry.validate(artifact_type, record)
        envelope = ArtifactEnvelope.create(
            artifact_type=artifact_type,
            instance_id=instance_id,
            payload=record,
            parent_ids=parent_ids,
            producer=producer,
            authority=authority,
            confidence=confidence,
            status=status,
        )
        envelope.validate()
        bucket = self.objects_dir / envelope.content_hash[:2] / envelope.content_hash
        object_path = bucket / f"{envelope.artifact_id}.json"
        relative_path = str(object_path.relative_to(self.root))

        with self._lock(exclusive=True):
            index = self._load_index_unlocked()
            if envelope.artifact_id in index.by_id:
                existing = self._read_path(self.root / index.by_id[envelope.artifact_id]["path"])
                existing_record = existing.to_dict()
                incoming_record = envelope.to_dict()
                existing_record.pop("created_at")
                incoming_record.pop("created_at")
                if existing_record != incoming_record:
                    raise ArtifactStoreError(f"artifact id collision for {envelope.artifact_id}")
                return existing
            for parent_id in parent_ids:
                if parent_id not in index.by_id:
                    raise ArtifactStoreError(f"unknown parent artifact: {parent_id}")
            self._atomic_write_json(object_path, envelope.to_dict())
            index.add(envelope, relative_path)
            self._append_journal(envelope, relative_path)
            self._atomic_write_json(self.index_path, index.to_dict())
        return envelope

    @staticmethod
    def _from_raw(raw: dict[str, Any]) -> ArtifactEnvelope:
        envelope = ArtifactEnvelope(
            artifact_id=raw["artifact_id"],
            artifact_type=raw["artifact_type"],
            schema_version=raw["schema_version"],
            instance_id=raw["instance_id"],
            parent_ids=tuple(raw.get("parent_ids", [])),
            content_hash=raw["content_hash"],
            producer=raw["producer"],
            created_at=raw["created_at"],
            authority=raw["authority"],
            confidence=raw["confidence"],
            status=raw["status"],
            payload=dict(raw["payload"]),
        )
        envelope.validate()
        return envelope

    def _read_path(self, path: Path) -> ArtifactEnvelope:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            envelope = self._from_raw(raw)
            self.schema_registry.validate(envelope.artifact_type, envelope.payload)
            return envelope
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArtifactStoreError(f"invalid artifact object {path}: {exc}") from exc

    def get(self, artifact_id: str) -> ArtifactEnvelope:
        with self._lock(exclusive=False):
            index = self._load_index_unlocked()
            entry = index.by_id.get(artifact_id)
            if entry is None:
                raise KeyError(artifact_id)
            return self._read_path(self.root / entry["path"])

    def find_by_content_hash(self, digest: str) -> list[ArtifactEnvelope]:
        with self._lock(exclusive=False):
            index = self._load_index_unlocked()
            identifiers = list(index.by_content_hash.get(digest, []))
        return [self.get(identifier) for identifier in identifiers]

    def list(
        self,
        *,
        artifact_type: str | None = None,
        instance_id: str | None = None,
    ) -> list[ArtifactEnvelope]:
        with self._lock(exclusive=False):
            index = self._load_index_unlocked()
            identifiers = (
                list(index.by_type.get(artifact_type, []))
                if artifact_type is not None
                else list(index.by_id)
            )
            if instance_id is not None:
                identifiers = [
                    identifier
                    for identifier in identifiers
                    if index.by_id[identifier]["instance_id"] == instance_id
                ]
        return [self.get(identifier) for identifier in identifiers]

    def latest(self, instance_id: str, artifact_type: str) -> ArtifactEnvelope | None:
        with self._lock(exclusive=False):
            index = self._load_index_unlocked()
            artifact_id = index.latest_by_instance_type.get(f"{instance_id}\0{artifact_type}")
        return self.get(artifact_id) if artifact_id is not None else None

    def recover(self) -> dict[str, int]:
        """Rebuild the index from immutable objects and validate parent links."""
        with self._lock(exclusive=True):
            recovered = ArtifactIndex()
            corrupt = 0
            for path in sorted(self.objects_dir.glob("*/*/*.json")):
                try:
                    envelope = self._read_path(path)
                    expected_bucket = self.objects_dir / envelope.content_hash[:2] / envelope.content_hash
                    if path.parent != expected_bucket:
                        raise ArtifactStoreError("object stored under the wrong content hash")
                    recovered.add(envelope, str(path.relative_to(self.root)))
                except ArtifactStoreError:
                    corrupt += 1
            dangling = sum(
                1
                for artifact_id in recovered.by_id
                for parent_id in self._read_path(
                    self.root / recovered.by_id[artifact_id]["path"]
                ).parent_ids
                if parent_id not in recovered.by_id
            )
            if corrupt or dangling:
                raise ArtifactStoreError(
                    f"recovery found corrupt={corrupt}, dangling_parents={dangling}"
                )
            self._atomic_write_json(self.index_path, recovered.to_dict())
            return {"artifacts": len(recovered.by_id), "generation": recovered.generation}

    def verify(self) -> dict[str, Any]:
        with self._lock(exclusive=False):
            index = self._load_index_unlocked()
            errors: list[str] = []
            checked = 0
            for artifact_id, entry in index.by_id.items():
                try:
                    envelope = self._read_path(self.root / entry["path"])
                    if envelope.artifact_id != artifact_id:
                        errors.append(f"id mismatch for {artifact_id}")
                    if envelope.content_hash != entry["content_hash"]:
                        errors.append(f"index hash mismatch for {artifact_id}")
                    self.schema_registry.validate(envelope.artifact_type, envelope.payload)
                    for parent_id in envelope.parent_ids:
                        if parent_id not in index.by_id:
                            errors.append(f"dangling parent {parent_id} from {artifact_id}")
                    checked += 1
                except (ArtifactStoreError, ValueError, TypeError) as exc:
                    errors.append(str(exc))
            for digest, identifiers in index.by_content_hash.items():
                for artifact_id in identifiers:
                    if index.by_id.get(artifact_id, {}).get("content_hash") != digest:
                        errors.append(f"bad content index entry {digest}:{artifact_id}")
            return {
                "valid": not errors,
                "checked": checked,
                "generation": index.generation,
                "errors": errors,
            }

    def verification_digest(self, *, exclude_types: set[str] | None = None) -> str:
        """Digest the verified immutable object set for certificate replay.

        Terminal certificates are excluded because they contain this digest;
        all other envelopes participate by id, type, instance and payload
        hash.  This makes the value stable across index reconstruction while
        avoiding a self-referential certificate hash.
        """
        excluded = exclude_types or set()
        with self._lock(exclusive=False):
            index = self._load_index_unlocked()
            records = []
            for artifact_id, entry in sorted(index.by_id.items()):
                if entry.get("artifact_type") in excluded:
                    continue
                records.append({
                    "artifact_id": artifact_id,
                    "artifact_type": entry.get("artifact_type"),
                    "instance_id": entry.get("instance_id"),
                    "content_hash": entry.get("content_hash"),
                })
        from reachpatch.models.base import content_hash
        return content_hash(records)

    def materialize_jsonl(
        self,
        path: str | Path,
        *,
        artifact_type: str,
        instance_id: str,
    ) -> Path:
        destination = Path(path)
        if not destination.is_absolute():
            destination = self.root / destination
        records = [item.to_dict() for item in self.list(
            artifact_type=artifact_type,
            instance_id=instance_id,
        )]
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(canonical_json(record))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            self._fsync_directory(destination.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return destination
