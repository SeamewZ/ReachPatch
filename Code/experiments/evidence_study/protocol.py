"""Freeze the data, policy, resource opportunity and implementation together."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys

from reachpatch.reach_avoid.controller import ReachAvoidConfig

CODE = Path(__file__).resolve().parents[2]
PUBLIC = CODE / "dataset/patchpsro_55_unique51/generation_public_instances.jsonl"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def implementation_hash() -> str:
    paths = [*(CODE / "reachpatch").rglob("*.py"),
             *(CODE / "experiments/evidence_study").glob("*.py"),
             CODE / "experiments/run_evidence_efficiency.py",
             CODE / "experiments/publish_evidence_study.py",
             CODE / "experiments/reachavoid_51/runner.py"]
    return digest(b"".join(str(p.relative_to(CODE)).encode() + b"\0" + p.read_bytes()
                           for p in sorted(paths)))


def build_block_schedule(case_ids, arms, repetitions, seed):
    if len(set(case_ids)) != len(case_ids) or not case_ids or repetitions < 1:
        raise ValueError("unique nonempty cases and positive repetitions required")
    rng = random.Random(seed)
    blocks = [(case, repetition) for case in sorted(case_ids) for repetition in range(repetitions)]
    rng.shuffle(blocks)
    cells = []
    for index, (case, repetition) in enumerate(blocks):
        order = sorted(arms)
        rng.shuffle(order)
        for position, arm in enumerate(order):
            cells.append({"cell_id": f"b{index:03d}-r{repetition}-{arm}",
                          "instance_id": case, "repetition": repetition, "arm": arm,
                          "block": index, "position": position})
    return cells


def make_protocol(scope: str, count: int, repetitions: int, seed: int,
                  source_tree_root: Path | None = None,
                  dataset_root: Path | None = None,
                  official_dataset_path: Path | None = None):
    tools = {name: shutil.which(name) for name in ("rg", "git", "docker", "bwrap")}
    if any(path is None for path in tools.values()):
        raise RuntimeError("study requires rg, git, docker and bubblewrap on PATH")
    if scope not in {"public_smoke", "development", "verified"}:
        raise ValueError("unsupported study scope")
    if scope == "public_smoke":
        cases = ["evidence-efficiency-public-smoke"]
        calls, tokens, wall, revisions = 18, 120000, 600, 2
    elif scope == "development":
        from experiments.reachavoid_51.runner import _public_rows
        rows = _public_rows()  # validates no hidden/gold fields
        if not 1 <= count <= len(rows):
            raise ValueError("development count outside available public cohort")
        # Round-robin repositories, random order within each stratum. No
        # successful completion or historical evaluation outcome is consulted.
        rng = random.Random(seed)
        groups = {}
        for row in rows:
            groups.setdefault(row["repo"], []).append(row["instance_id"])
        for group in groups.values():
            rng.shuffle(group)
        cases = []
        while len(cases) < count:
            for repository in sorted(groups):
                if groups[repository] and len(cases) < count:
                    cases.append(groups[repository].pop())
        calls, tokens, wall, revisions = 40, 250000, 900, 2
        if source_tree_root is None or not source_tree_root.is_dir():
            raise ValueError("development requires an explicit verified source-tree root")
    else:
        if dataset_root is None or not dataset_root.is_dir():
            raise ValueError("verified requires an explicit public dataset root")
        if source_tree_root is None or not source_tree_root.is_dir():
            raise ValueError("verified requires an explicit source-tree root")
        public_path = dataset_root / "generation_public_instances.jsonl"
        if not public_path.is_file():
            raise ValueError(f"verified public dataset is missing: {public_path}")
        verified_rows = [json.loads(line) for line in public_path.read_text().splitlines() if line.strip()]
        if not 1 <= count <= len(verified_rows):
            raise ValueError("verified count outside prepared cohort")
        cases = [str(row["instance_id"]) for row in verified_rows[:count]]
        calls, tokens, wall, revisions = 40, 250000, 900, 2
    fixture = CODE / "tests/fixtures/evidence_efficiency"
    fixture_hash = digest(b"".join(p.name.encode() + b"\0" + p.read_bytes()
                                  for p in sorted(fixture.iterdir()) if p.is_file()))
    arms = {}
    for demand, reuse, name in ((False, False, "F00"), (True, False, "F10"),
                               (False, True, "F01"), (True, True, "F11")):
        config = ReachAvoidConfig(max_case_model_calls=calls, max_case_tokens=tokens,
            execution_budget_seconds=wall, max_real_patch_revisions=revisions,
            evidence_reuse_enabled=reuse, demand_driven_interaction_enabled=demand)
        arms[name] = {"demand": demand, "reuse": reuse, "policy": config.evidence_policy(),
                      "config": asdict(config)}
    public_path = (dataset_root / "generation_public_instances.jsonl"
                   if scope == "verified" and dataset_root is not None else PUBLIC)
    return {"schema": "evidence-study-v1", "scope": scope,
            "host_runtime": {"python": sys.executable, "python_version": sys.version,
                             "tools": tools, "tool_hashes": {k: digest(Path(v).read_bytes()) for k, v in tools.items()}},
            "source_tree_root": str(source_tree_root.resolve()) if source_tree_root else None,
            "dataset_root": str(dataset_root.resolve()) if dataset_root else None,
            "official_dataset_path": str(official_dataset_path.resolve()) if official_dataset_path else None,
            "public_fixture_sha256": fixture_hash,
            "model": "deepseek-flash", "temperature": 0, "provider_seed": "NOT_SUPPORTED_BY_TRANSPORT",
            "randomization_seed": seed, "repetitions": repetitions,
            "case_ids": cases, "arms": arms,
            "max_attempts_per_cell": 1, "infra_evaluation_retries": 2,
            "parallel_blocks": 2, "minimum_free_disk_gib": 100,
            "primary_cost": "ALL_ATTEMPT_PROVIDER_TOTAL_TOKENS",
            "noninferiority": "NOT_TESTED_IN_DEVELOPMENT_NO_MARGIN_CHOSEN",
            "analysis": {"bootstrap_unit": "issue", "bootstrap_samples": 10000,
                         "seed": seed, "multiplicity": "EXPLORATORY_NO_CONFIRMATORY_P_VALUES"},
            "implementation_hash": implementation_hash(), "public_sha256": digest(public_path.read_bytes()),
            "cells": build_block_schedule(cases, tuple(arms), repetitions, seed)}


def validate_protocol(value):
    runtime = value["host_runtime"]
    if runtime["python"] != sys.executable or runtime["python_version"] != sys.version:
        raise ValueError("Python runtime changed after freeze")
    for name, path in runtime["tools"].items():
        if shutil.which(name) != path or digest(Path(path).read_bytes()) != runtime["tool_hashes"][name]:
            raise ValueError(f"tool runtime changed after freeze: {name}")
    fixture = CODE / "tests/fixtures/evidence_efficiency"
    fixture_hash = digest(b"".join(p.name.encode() + b"\0" + p.read_bytes()
                                  for p in sorted(fixture.iterdir()) if p.is_file()))
    if value["public_fixture_sha256"] != fixture_hash:
        raise ValueError("public fixture changed after freeze")
    if value["implementation_hash"] != implementation_hash():
        raise ValueError("implementation changed after freeze; create a new study")
    public_path = (Path(value["dataset_root"]) / "generation_public_instances.jsonl"
                   if value.get("scope") == "verified" else PUBLIC)
    if value["public_sha256"] != digest(public_path.read_bytes()):
        raise ValueError("public dataset changed after freeze")
    if value["model"] != "deepseek-flash" or value["max_attempts_per_cell"] != 1:
        raise ValueError("unsupported model or retry policy")
    reference = dict(value["arms"]["F00"]["config"])
    allowed = {"evidence_reuse_enabled", "demand_driven_interaction_enabled"}
    for arm in value["arms"].values():
        if any(arm["config"][key] != val for key, val in reference.items() if key not in allowed):
            raise ValueError("factorial arm has a non-intervention difference")
        config = arm["config"]
        resolved = ReachAvoidConfig(evidence_reuse_enabled=config["evidence_reuse_enabled"],
                                   demand_driven_interaction_enabled=config["demand_driven_interaction_enabled"]).evidence_policy()
        if arm["policy"] != resolved:
            raise ValueError("resolved fine policy differs from factorial configuration")
    expected = build_block_schedule(value["case_ids"], tuple(value["arms"]),
                                    value["repetitions"], value["randomization_seed"])
    # JSON round trips may sort arm mappings; schedule construction sorts them.
    if sorted(expected, key=lambda x: x["cell_id"]) != sorted(value["cells"], key=lambda x: x["cell_id"]):
        raise ValueError("schedule differs from frozen randomization")
    return value


def freeze_protocol(root: Path, value):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "protocol.json"
    if path.exists():
        raise FileExistsError("refusing to overwrite a frozen protocol")
    path.write_text(json.dumps(value, indent=2) + "\n")
    (root / "protocol.sha256").write_text(digest(path.read_bytes()) + "\n")
    return path


def load_protocol(root: Path):
    path = root / "protocol.json"
    if digest(path.read_bytes()) != (root / "protocol.sha256").read_text().strip():
        raise ValueError("protocol checksum mismatch")
    return validate_protocol(json.loads(path.read_text()))
