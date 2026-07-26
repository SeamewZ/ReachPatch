from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _jsonable(value: Any) -> Any:
    value_type = type(value)
    if value is None or value_type in {str, int, float, bool}:
        return value
    if value_type is dict:
        return {str(key): _jsonable(item) for key, item in value.items()}
    if value_type in {list, tuple}:
        return [_jsonable(item) for item in value]
    if value_type in {set, frozenset}:
        items = [_jsonable(item) for item in value]
        return sorted(items, key=canonical_json)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_jsonable(item) for item in value]
        return sorted(items, key=canonical_json) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _is_json_native(value: Any) -> bool:
    """Return whether json.dumps already emits the canonical converted form."""
    value_type = type(value)
    if value is None or value_type in {str, int, float, bool}:
        return True
    if value_type in {list, tuple}:
        return all(_is_json_native(item) for item in value)
    if value_type is dict:
        return all(
            type(key) is str and _is_json_native(item)
            for key, item in value.items()
        )
    return False


def canonical_json(value: Any) -> str:
    return json.dumps(
        value if _is_json_native(value) else _jsonable(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@lru_cache(maxsize=256)
def _clean_id_prefix(prefix: str) -> str:
    return "".join(
        char if char.isalnum() or char in "-_" else "-"
        for char in prefix
    )


def stable_id(prefix: str, *parts: Any, length: int = 24) -> str:
    return f"{_clean_id_prefix(prefix)}-{content_hash(parts)[:length]}"


class SerializableRecord:
    """Dataclass mixin with canonical, recursively JSON-safe conversion."""

    def to_dict(self) -> dict[str, Any]:
        value = _jsonable(self)
        if not isinstance(value, dict):
            raise TypeError("record must serialize to an object")
        return value

    def digest(self) -> str:
        return content_hash(self.to_dict())
