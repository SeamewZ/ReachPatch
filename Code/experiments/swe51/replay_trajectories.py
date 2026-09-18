"""Render unified graph checkpoint trajectories for archived runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _trajectory(run_root: Path) -> dict[str, object]:
    tree = run_root / "checkpoint_tree_view.json"
    summary = run_root / "execution_summary.json"
    if not tree.is_file() or not summary.is_file():
        raise FileNotFoundError(f"missing unified graph artifacts under {run_root}")
    return {
        "run_root": str(run_root.resolve()),
        "checkpoint_tree": json.loads(tree.read_text(encoding="utf-8")),
        "execution_summary": json.loads(summary.read_text(encoding="utf-8")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_roots", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = {"case_count": len(args.run_roots), "reports": [_trajectory(path) for path in args.run_roots]}
    rendered = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
