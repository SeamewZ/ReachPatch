from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reachpatch.models.base import SerializableRecord
from reachpatch.models.core import Instance


_FORBIDDEN_GENERATION_KEYS = {
    "test_patch", "patch", "gold_patch", "hidden_tests", "harness_logs",
    "fail_to_pass", "pass_to_pass", "FAIL_TO_PASS", "PASS_TO_PASS",
}


def is_official_only_path(path: str) -> bool:
    """Return true for paths reserved for post-seal evaluation evidence."""

    normalized = path.replace("\\", "/").lower()
    parts = set(Path(normalized).parts)
    return (
        any(token in normalized for token in (
            "test_patch", "gold_patch", "gold-patch", "hidden_test",
            "hidden-test", "harness_result", "harness-log",
        ))
        or bool(parts & {"gold", "hidden", "official_harness", "harness_logs"})
    )


def _assert_public_payload(value: Any, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in _FORBIDDEN_GENERATION_KEYS:
                raise ValueError(f"official-only field in GenerationInstance: {path}.{key}")
            _assert_public_payload(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_public_payload(item, path=f"{path}[{index}]")


def assert_generation_payload(value: Any, *, path: str = "root") -> None:
    """Reject official-only evidence at every production Generator boundary."""

    _assert_public_payload(value, path=path)


@dataclass(frozen=True, slots=True)
class GenerationInstance(SerializableRecord):
    instance_id: str
    repository_name: str
    base_commit: str
    issue: str
    hints: str = ""
    visible_tests: tuple[str, ...] = ()
    public_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _assert_public_payload(self.public_metadata)

    @classmethod
    def from_public_record(cls, raw: dict[str, Any]) -> "GenerationInstance":
        allowed = {
            "instance_id", "repo", "base_commit", "problem_statement",
            "hints_text", "version", "environment_setup_commit",
            "visible_tests",
        }
        public = {key: raw[key] for key in allowed if key in raw}
        _assert_public_payload(public)
        return cls(
            instance_id=str(raw["instance_id"]),
            repository_name=str(raw["repo"]),
            base_commit=str(raw["base_commit"]),
            issue=str(raw["problem_statement"]),
            hints=str(raw.get("hints_text", "")),
            visible_tests=tuple(map(str, raw.get("visible_tests", ()))),
            public_metadata={
                "repo": str(raw["repo"]),
                "version": raw.get("version"),
                "hints_text": str(raw.get("hints_text", "")),
                "environment_setup_commit": raw.get("environment_setup_commit"),
                "generation_source": "generation_public_instances.jsonl",
            },
        )

    def to_controller_instance(self, repository: Path) -> Instance:
        return Instance(
            instance_id=self.instance_id,
            repository=str(repository.resolve()),
            base_commit=self.base_commit,
            issue=self.issue,
            visible_tests=self.visible_tests,
            public_metadata=dict(self.public_metadata),
        )


@dataclass(frozen=True, slots=True)
class HarnessEvaluationInstance(SerializableRecord):
    instance_id: str
    repository_name: str
    base_commit: str
    patch_path: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]

    @classmethod
    def from_official_record(
        cls,
        raw: dict[str, Any],
        *,
        patch_path: Path,
    ) -> "HarnessEvaluationInstance":
        def list_field(name: str) -> tuple[str, ...]:
            value = raw.get(name, ())
            if isinstance(value, str):
                import json
                parsed = json.loads(value)
                value = parsed if isinstance(parsed, list) else ()
            return tuple(map(str, value)) if isinstance(value, (list, tuple)) else ()

        return cls(
            instance_id=str(raw["instance_id"]),
            repository_name=str(raw["repo"]),
            base_commit=str(raw["base_commit"]),
            patch_path=str(patch_path.resolve()),
            fail_to_pass=list_field("FAIL_TO_PASS"),
            pass_to_pass=list_field("PASS_TO_PASS"),
        )
