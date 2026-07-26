from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

Validator = Callable[[dict[str, Any]], None]


class ArtifactSchemaRegistry:
    """Runtime payload validation with explicit schemas per artifact type."""

    def __init__(self) -> None:
        self._validators: dict[str, Validator] = {}

    def register(self, artifact_type: str, validator: Validator) -> None:
        if not artifact_type:
            raise ValueError("artifact_type cannot be empty")
        if artifact_type in self._validators:
            raise ValueError(f"schema already registered for {artifact_type}")
        self._validators[artifact_type] = validator

    def validate(self, artifact_type: str, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise TypeError("artifact payload must be an object")
        validator = self._validators.get(artifact_type)
        if validator is None:
            raise ValueError(f"no schema registered for artifact type {artifact_type!r}")
        validator(payload)

    @staticmethod
    def require_fields(*fields: str) -> Validator:
        def validator(payload: dict[str, Any]) -> None:
            missing = [field for field in fields if field not in payload]
            if missing:
                raise ValueError(f"artifact payload missing fields: {missing}")
        return validator


def validate_mapping_of_lists(payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, list):
            raise TypeError("expected an object whose values are lists")


def validate_mapping(payload: dict[str, Any]) -> None:
    if not payload:
        raise ValueError("artifact payload cannot be empty")


DEFAULT_SCHEMA_REGISTRY = ArtifactSchemaRegistry()

for _artifact_type, _fields in {
    "evidence": ("evidence_id", "kind", "source", "content"),
    "semantic_hypothesis_graph": ("nodes", "edges", "graph_hash"),
    "episode_assignment": ("assignment_id", "choice_by_decision", "coherent"),
    "requirement_graph": ("nodes", "edges", "leaves", "graph_hash"),
    "program_graph": ("nodes", "edges", "graph_hash"),
    "binding_graph": ("units", "components", "graph_hash"),
    "challenge_graph": ("cells", "graph_hash"),
    "trace_bundle": ("recipe_id", "runs", "stability_status"),
    "counterexample": ("counterexample_id", "failure_origin", "raw_execution_ids"),
    "repair_action": ("action_id", "operator", "causal_cut_ids"),
    "reach_avoid_state": ("state_id", "base_commit", "working_patch_hash"),
    "transition_certificate": ("transition_id", "safe", "decision"),
    "edit_retention_ablation": (
        "ablation_id", "source_checkpoint_id", "final_checkpoint_id", "attempts",
    ),
    "terminal_certificate": ("instance_id", "status", "final_diff_hash"),
    "working_patch": ("version", "canonical_diff", "canonical_diff_hash"),
    "adapter_observation": ("adapter", "marker_paths", "status"),
    "diff_closure_certificate": ("closure_id", "update_id", "diff_challenge_closed"),
    "generator_session": ("session_id", "current_checkpoint_id", "cursor"),
    "incumbent_checkpoint": ("checkpoint_id", "patch", "graph_hashes"),
    "mechanism_memory": (),
    "repair_intent": ("intent_id", "component_id", "repair_cut_ids"),
    "recovery_audit": ("run_root", "checkpoint_id", "graph_hashes"),
    "root_recovery": ("recovery_id", "core_id", "classification"),
    "discriminator_probe": ("probe_id", "correctness_authority"),
    "path_class": ("path_class_id", "entrypoint_id", "exit_kind"),
    "binding_unit": ("unit_id", "path_obligation_id", "status"),
    "challenge_cell": ("challenge_id", "binding_unit_id", "terminal_status"),
    "input_recipe": ("recipe_id", "stimulus", "resource_limits"),
    "observation_contract": ("contract_id", "channels"),
    "oracle": ("oracle_id", "relation", "authority", "lifecycle"),
}.items():
    DEFAULT_SCHEMA_REGISTRY.register(
        _artifact_type,
        validate_mapping if not _fields else ArtifactSchemaRegistry.require_fields(*_fields),
    )
