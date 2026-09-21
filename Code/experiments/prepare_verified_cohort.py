"""Freeze a public-only SWE-bench Verified cohort and its source baselines.

The parquet contains gold patches and test metadata.  This script writes a
generation JSONL with those fields removed, keeps the official rows in a
separate local directory, and prepares detached git worktrees at each public
base commit.  Generation is sandboxed by the production runner, so the
official rows are never mounted during model interaction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys

import pandas as pd


REPOSITORY = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET = Path("/tmp/swebench_verified.parquet")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_exposure(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    value = json.loads(path.read_text())
    return {str(item) for item in value.get("instance_ids", ())}


def choose(rows: list[dict], count: int, seed: int, excluded: set[str]) -> list[dict]:
    candidates = [row for row in rows if str(row["instance_id"]) not in excluded]
    groups: dict[str, list[dict]] = {}
    rng = random.Random(seed)
    for row in candidates:
        groups.setdefault(str(row["repo"]), []).append(row)
    for values in groups.values():
        rng.shuffle(values)
    selected: list[dict] = []
    while len(selected) < count:
        progressed = False
        for repo in sorted(groups):
            if groups[repo] and len(selected) < count:
                selected.append(groups[repo].pop())
                progressed = True
        if not progressed:
            raise ValueError(f"only {len(selected)} unseen Verified cases are available")
    return selected


def public_row(row: dict) -> dict:
    allowed = ("repo", "instance_id", "base_commit", "problem_statement",
               "hints_text", "created_at", "version", "environment_setup_commit",
               "difficulty")
    return {key: row.get(key) for key in allowed if key in row}


def git(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True, timeout=1800,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def prepare_sources(rows: list[dict], repos: Path, trees: Path) -> None:
    repos.mkdir(parents=True, exist_ok=True)
    trees.mkdir(parents=True, exist_ok=True)
    for row in rows:
        case_id = str(row["instance_id"])
        tree = trees / case_id
        if tree.exists():
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree,
                                  text=True, capture_output=True, check=True).stdout.strip()
            if head == str(row["base_commit"]):
                continue
            raise RuntimeError(f"existing source tree has wrong base: {tree}")
        owner, name = str(row["repo"]).split("/", 1)
        bare = repos / f"{owner}__{name}.git"
        url = f"https://github.com/{owner}/{name}.git"
        if not bare.exists():
            git(["git", "clone", "--bare", "--filter=blob:none", url, str(bare)])
        commit = str(row["base_commit"])
        present = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"],
                                 cwd=bare, capture_output=True).returncode == 0
        if not present:
            git(["git", "fetch", "--filter=blob:none", "origin", commit], cwd=bare)
        git(["git", "worktree", "add", "--detach", str(tree), commit], cwd=bare)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260922)
    parser.add_argument("--output", type=Path,
                        default=REPOSITORY / "Code/experiments/swe_verified_20260922")
    parser.add_argument("--exposure", type=Path,
                        default=REPOSITORY / "Paper/fse2027/data/development_exposure.json")
    parser.add_argument("--prepare-sources", action="store_true")
    args = parser.parse_args()
    if not args.parquet.is_file():
        raise FileNotFoundError(args.parquet)
    frame = pd.read_parquet(args.parquet)
    rows = frame.to_dict("records")
    if len(rows) != 500 or frame["instance_id"].duplicated().any():
        raise ValueError("expected 500 unique Verified rows")
    excluded = read_exposure(args.exposure)
    selected = choose(rows, args.count, args.seed, excluded)
    public = args.output / "public"
    private = args.output / "private_official"
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    public_path = public / "generation_public_instances.jsonl"
    official_path = private / "official_instances.jsonl"
    dataset_official_path = public / "official_instances.jsonl"
    public_path.write_text("".join(json.dumps(public_row(row), sort_keys=True) + "\n"
                                        for row in selected))
    official_path.write_text("".join(json.dumps(row, sort_keys=True, default=str) + "\n"
                                          for row in selected))
    # The production runner resolves PUBLIC_PATH and OFFICIAL_PATH below one
    # dataset root.  The official copy is ignored and is never mounted by the
    # public-only generation sandbox; it is opened only after sealing.
    shutil.copyfile(official_path, dataset_official_path)
    manifest = {
        "schema": "swe-verified-public-cohort-v1", "dataset": "SWE-bench_Verified",
        "dataset_sha256": digest(args.parquet), "count": len(selected), "seed": args.seed,
        "excluded_exposure_count": len(excluded),
        "instance_ids": [str(row["instance_id"]) for row in selected],
        "public_sha256": digest(public_path), "official_sha256": digest(official_path),
        "source_tree_root": str((args.output / "case_trees").resolve()),
        "public_path": str(public_path.resolve()), "official_path": str(official_path.resolve()),
        "runner_dataset_root": str(public.resolve()),
    }
    (args.output / "cohort_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.output / "cohort_manifest.sha256").write_text(digest(args.output / "cohort_manifest.json") + "\n")
    if args.prepare_sources:
        prepare_sources(selected, args.output / "repos", args.output / "case_trees")
    print(json.dumps({"count": len(selected), "excluded": len(excluded),
                      "manifest": str((args.output / "cohort_manifest.json").resolve()),
                      "sources_prepared": args.prepare_sources}))


if __name__ == "__main__":
    main()
