from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Keep direct script execution usable without requiring an installed package.
CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from reachpatch.diagnostics import build_revision_trajectory_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_roots", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [
        build_revision_trajectory_report(path) for path in args.run_roots
    ]
    payload = {
        "case_count": len(reports),
        "reports": reports,
    }
    rendered = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
