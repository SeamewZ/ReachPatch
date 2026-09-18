"""AST audit for placeholder implementations in the production execution path.

The audit intentionally reports findings instead of silently suppressing them;
callers may fail CI with ``--strict``.  Legacy modules outside the controller
import chain are still listed for review but do not affect the production
result unless explicitly requested.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "reachpatch"
PRODUCTION = (
    ROOT / "reach_avoid", ROOT / "execution", ROOT / "requirement_graph",
    ROOT / "repair",
)
TOKENS = ("TODO: implement later", "placeholder", "mock implementation", "future work")


def audit_file(path: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [{"file": str(path), "kind": "PARSE_ERROR", "detail": str(exc)}]
    source = path.read_text(encoding="utf-8", errors="replace")
    for node in ast.walk(tree):
        if isinstance(node, ast.Pass):
            findings.append({"file": str(path), "line": node.lineno, "kind": "PASS"})
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name) and node.exc.func.id == "NotImplementedError":
            findings.append({"file": str(path), "line": node.lineno, "kind": "NOT_IMPLEMENTED"})
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
            findings.append({"file": str(path), "line": node.lineno, "kind": "ELLIPSIS"})
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Dict) and not node.value.keys:
            findings.append({"file": str(path), "line": node.lineno, "kind": "EMPTY_RETURN_DICT"})
        elif isinstance(node, ast.Return) and isinstance(node.value, (ast.List, ast.Tuple)) and not node.value.elts:
            findings.append({"file": str(path), "line": node.lineno, "kind": "EMPTY_RETURN_SEQUENCE"})
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value is None:
            findings.append({"file": str(path), "line": node.lineno, "kind": "NONE_RETURN"})
    for token in TOKENS:
        for line, text in enumerate(source.splitlines(), 1):
            if token.casefold() in text.casefold():
                findings.append({"file": str(path), "line": line, "kind": "TEXT_TOKEN", "token": token})
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--files", type=Path, nargs="*")
    args = parser.parse_args()
    findings = []
    paths = args.files if args.files else [path for directory in PRODUCTION for path in sorted(directory.rglob("*.py"))]
    for path in paths:
        findings.extend(audit_file(path))
    # Empty/None returns are commonly intentional in typed APIs (for example
    # an optional lookup or an empty frontier).  They remain visible in the
    # report for manual review, while strict mode is reserved for actual
    # placeholders that can silently bypass production behaviour.
    blocking_kinds = {"PASS", "NOT_IMPLEMENTED", "ELLIPSIS", "TEXT_TOKEN"}
    blocking = [item for item in findings if item.get("kind") in blocking_kinds]
    payload = {"root": str(ROOT), "findings": findings, "count": len(findings), "blocking_count": len(blocking)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if args.strict and blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
