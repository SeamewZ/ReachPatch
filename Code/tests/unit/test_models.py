from __future__ import annotations

import dataclasses
import enum
import json
from collections import UserDict
from pathlib import Path
from typing import Any

import pytest

from reachpatch.models.base import canonical_json, content_hash, stable_id


class Mode(enum.Enum):
    ACTIVE = "active"


@dataclasses.dataclass(frozen=True)
class SampleRecord:
    name: str
    values: tuple[int, ...]
    mode: Mode


def _reference_jsonable(value: Any) -> Any:
    """Pre-optimization conversion order used as a semantic reference."""
    if dataclasses.is_dataclass(value):
        return {
            field.name: _reference_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict | UserDict):
        return {
            str(key): _reference_jsonable(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_reference_jsonable(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(items, key=_reference_canonical_json)
        return items
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def _reference_canonical_json(value: Any) -> str:
    return json.dumps(
        _reference_jsonable(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        7,
        2.5,
        "text",
        Path("pkg/module.py"),
        Mode.ACTIVE,
        SampleRecord("record", (3, 1), Mode.ACTIVE),
        {"nested": [1, {"path": Path("a/b")}], "set": {3, 1, 2}},
        UserDict({"record": SampleRecord("x", (), Mode.ACTIVE)}),
        frozenset({("b", 2), ("a", 1)}),
    ],
)
def test_canonical_json_fast_path_matches_reference(value: Any):
    assert canonical_json(value) == _reference_canonical_json(value)


def test_canonical_hash_and_identifier_are_stable():
    value = {"items": {"beta", "alpha"}, "record": SampleRecord("x", (1,), Mode.ACTIVE)}
    expected_json = _reference_canonical_json(value)

    assert content_hash(value) == content_hash(json.loads(expected_json))
    assert stable_id("sample", value) == stable_id("sample", json.loads(expected_json))
