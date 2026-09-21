from __future__ import annotations

import ast
from dataclasses import replace
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from reachpatch.models.base import content_hash, stable_id
from reachpatch.models.evidence import ActualDiff, DiffHunk


_EXCLUDES = {
    ".git", ".hg", ".svn", ".reachpatch", ".pytest_cache", "__pycache__",
    ".venv", "venv", "node_modules", "build", "dist",
}
_RUNTIME_ROOTS: set[Path] = set()
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def tree_hash(root: str | Path) -> str:
    base = Path(root).resolve()
    digest = hashlib.sha256()
    for current, directories, names in os.walk(base):
        directories[:] = sorted(
            name for name in directories
            if name not in _EXCLUDES
            and not _is_runtime_path(Path(current) / name, traversal_root=base)
        )
        directory = Path(current)
        for name in sorted(names):
            if name in _EXCLUDES:
                continue
            path = directory / name
            if path.is_symlink():
                data = os.readlink(path).encode("utf-8")
            else:
                data = path.read_bytes()
            relative = path.relative_to(base).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(data)
            digest.update(b"\0")
    return digest.hexdigest()


def copy_source_tree(
    source: Path,
    destination: Path,
    *,
    exclude_paths: tuple[Path, ...] = (),
    hardlink_files: bool = False,
) -> None:
    excluded = tuple(path.resolve() for path in exclude_paths)
    def ignored(_: str, names: list[str]) -> set[str]:
        return {name for name in names if name in _EXCLUDES}

    def ignore_with_paths(current: str, names: list[str]) -> set[str]:
        ignored_names = ignored(current, names)
        current_path = Path(current).resolve()
        for name in names:
            candidate = current_path / name
            if any(
                candidate == path or candidate.is_relative_to(path)
                for path in excluded
            ):
                ignored_names.add(name)
        return ignored_names

    def link_or_copy(source_name: str, destination_name: str) -> str:
        try:
            os.link(source_name, destination_name)
            return destination_name
        except OSError:
            return shutil.copy2(source_name, destination_name)

    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=ignore_with_paths,
        copy_function=link_or_copy if hardlink_files else shutil.copy2,
    )


def register_runtime_root(root: Path) -> None:
    _RUNTIME_ROOTS.add(root.resolve())


def discard_ephemeral_tree(
    path: str | Path | None,
    run_root: str | Path,
    *,
    allowed_parents: tuple[str, ...] = ("trials", "generator_staging"),
) -> bool:
    if path is None:
        return False
    candidate = Path(path).resolve()
    root = Path(run_root).resolve()
    permitted = tuple((root / name).resolve() for name in allowed_parents)
    if not any(candidate.parent == parent for parent in permitted):
        return False
    if candidate.is_dir():
        shutil.rmtree(candidate)
        return True
    return False


def discard_bootstrap_tree(path: str | Path | None, run_root: str | Path) -> bool:
    if path is None:
        return False
    candidate = Path(path).resolve()
    root = Path(run_root).resolve()
    if candidate not in {
        (root / "bootstrap_working").resolve(),
        (root / "initial_working").resolve(),
    }:
        return False
    if candidate.is_dir():
        shutil.rmtree(candidate)
        return True
    return False


def _is_runtime_path(path: Path, *, traversal_root: Path) -> bool:
    resolved = path.resolve()
    base = traversal_root.resolve()
    return any(
        runtime != base
        and runtime.is_relative_to(base)
        and (resolved == runtime or resolved.is_relative_to(runtime))
        for runtime in _RUNTIME_ROOTS
    )


def create_trial_tree(source_checkpoint, run_root: Path | None = None) -> Path:
    source = Path(source_checkpoint.snapshot_tree).resolve()
    root = (run_root or source.parent.parent / "trials").resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / stable_id(
        "trial", source_checkpoint.checkpoint_id, tree_hash(source), next(tempfile._get_candidate_names()),
    )
    copy_source_tree(source, destination)
    return destination


def _files(root: Path) -> dict[str, Path]:
    base = root.resolve()
    result: dict[str, Path] = {}
    for current, directories, names in os.walk(base):
        directories[:] = sorted(
            name for name in directories
            if name not in _EXCLUDES
            and not _is_runtime_path(Path(current) / name, traversal_root=base)
        )
        for name in names:
            if name in _EXCLUDES:
                continue
            path = Path(current) / name
            result[path.relative_to(base).as_posix()] = path
    return result


def _text(path: Path | None) -> tuple[list[str], bool]:
    if path is None:
        return [], True
    data = path.read_bytes()
    if b"\0" in data:
        return [], False
    return data.decode("utf-8", errors="replace").splitlines(keepends=True), True


def _parse_hunks(diff: str, after_tree: Path | None = None) -> tuple[DiffHunk, ...]:
    result: list[DiffHunk] = []
    path = ""
    lines = diff.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("diff --git a/"):
            match = re.match(r"diff --git a/(.*?) b/(.*)$", line)
            path = match.group(2) if match else ""
            index += 1
            continue
        match = _HUNK.match(line)
        if not match:
            index += 1
            continue
        body: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and not lines[cursor].startswith(("@@ ", "diff --git ")):
            if not lines[cursor].startswith(("--- ", "+++ ")):
                body.append(lines[cursor])
            cursor += 1
        header = match.group(5).strip()
        symbols = tuple(dict.fromkeys(
            name
            for value in body if value.startswith("+")
            for name in re.findall(r"\b(?:def|class)\s+([A-Za-z_]\w*)", value)
        ))
        hunk_id = stable_id("diff-hunk", path, line, body)
        result.append(DiffHunk(
            hunk_id=hunk_id,
            path=path,
            old_start=int(match.group(1)),
            old_count=int(match.group(2) or 1),
            new_start=int(match.group(3)),
            new_count=int(match.group(4) or 1),
            header=header,
            lines=tuple(body),
            changed_symbols=symbols,
        ))
        index = cursor
    if after_tree is None:
        return tuple(result)
    parsed_files: dict[str, ast.AST | None] = {}
    enriched: list[DiffHunk] = []
    for hunk in result:
        if hunk.path not in parsed_files:
            source_path = after_tree / hunk.path
            try:
                parsed_files[hunk.path] = (
                    ast.parse(source_path.read_text(encoding="utf-8", errors="replace"))
                    if source_path.suffix == ".py" and source_path.is_file()
                    else None
                )
            except (OSError, SyntaxError):
                parsed_files[hunk.path] = None
        tree = parsed_files[hunk.path]
        if tree is None:
            enriched.append(hunk)
            continue
        changed_lines = hunk.changed_new_lines or (hunk.new_start,)
        enclosing = sorted(
            (
                node for node in ast.walk(tree)
                if isinstance(node, (
                    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                ))
                and any(
                    node.lineno <= line <= getattr(node, "end_lineno", node.lineno)
                    for line in changed_lines
                )
            ),
            key=lambda node: (
                getattr(node, "end_lineno", node.lineno) - node.lineno,
                node.lineno,
                node.name,
            ),
        )
        enriched.append(replace(
            hunk,
            changed_symbols=tuple(dict.fromkeys(
                hunk.changed_symbols + tuple(node.name for node in enclosing)
            )),
        ))
    return tuple(enriched)


def diff_between(before: str | Path, after: str | Path) -> ActualDiff:
    left = Path(before).resolve()
    right = Path(after).resolve()
    left_files = _files(left)
    right_files = _files(right)
    chunks: list[str] = []
    changed_files: list[str] = []
    for relative in sorted(set(left_files) | set(right_files)):
        old_lines, old_text = _text(left_files.get(relative))
        new_lines, new_text = _text(right_files.get(relative))
        if old_text and new_text and old_lines == new_lines:
            continue
        if not old_text or not new_text:
            old_bytes = left_files[relative].read_bytes() if relative in left_files else b""
            new_bytes = right_files[relative].read_bytes() if relative in right_files else b""
            if old_bytes == new_bytes:
                continue
            # Binary edits are represented deterministically and rejected by
            # patch application unless a generator supplied a real git binary patch.
            chunks.extend((
                f"diff --git a/{relative} b/{relative}\n",
                f"Binary files a/{relative} and b/{relative} differ\n",
            ))
            changed_files.append(relative)
            continue
        changed_files.append(relative)
        chunks.append(f"diff --git a/{relative} b/{relative}\n")
        old_name = "/dev/null" if relative not in left_files else f"a/{relative}"
        new_name = "/dev/null" if relative not in right_files else f"b/{relative}"
        chunks.extend(difflib.unified_diff(
            old_lines, new_lines, fromfile=old_name, tofile=new_name, n=3,
        ))
    canonical = "".join(chunks)
    hunks = _parse_hunks(canonical, right)
    symbols = tuple(dict.fromkeys(
        symbol for hunk in hunks for symbol in hunk.changed_symbols
    ))
    return ActualDiff(
        canonical_diff=canonical,
        patch_hash=content_hash(canonical),
        hunks=hunks,
        changed_files=tuple(changed_files),
        changed_symbols=symbols,
    )


def apply_unified_diff(tree: Path, patch: str) -> None:
    if not patch.strip():
        return
    environment = os.environ.copy()
    environment["GIT_CEILING_DIRECTORIES"] = str(tree.resolve().parent)
    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("git", "apply", *arguments, "--recount", "--whitespace=nowarn", "-"),
            cwd=tree,
            input=patch,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )

    strict = run()
    if strict.returncode == 0:
        return
    # Models sometimes emit a valid hunk whose unchanged context count is zero.
    # Git rejects that format by default even when every removed line matches.
    # Check first so the relaxed format cannot partially modify the tree.
    relaxed_check = run("--check", "--unidiff-zero")
    if relaxed_check.returncode == 0:
        relaxed = run("--unidiff-zero")
        if relaxed.returncode == 0:
            return
        relaxed_error = relaxed.stderr.strip()
    else:
        relaxed_error = relaxed_check.stderr.strip()
    raise RuntimeError(
        "generator patch does not apply: "
        f"{strict.stderr.strip()}\nzero-context check: {relaxed_error}"
    )


def _structured_update(source: str, body: list[str], relative: str) -> str:
    source_lines = source.splitlines()
    trailing_newline = source.endswith("\n")
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in body:
        if line.startswith("@@"):
            current = []
            hunks.append(current)
            continue
        if line == "\\ No newline at end of file":
            continue
        if current is None:
            raise RuntimeError(f"structured update for {relative} has content before a hunk")
        if not line.startswith((" ", "+", "-")):
            raise RuntimeError(f"structured update for {relative} has an invalid hunk line")
        current.append(line)
    if not hunks:
        raise RuntimeError(f"structured update for {relative} has no hunks")

    cursor = 0
    changed = False
    for hunk in hunks:
        old = [line[1:] for line in hunk if line.startswith((" ", "-"))]
        new = [line[1:] for line in hunk if line.startswith((" ", "+"))]
        changed = changed or old != new
        if not old:
            raise RuntimeError(
                f"structured update for {relative} requires exact old context"
            )
        width = len(old)
        matches = [
            index for index in range(cursor, len(source_lines) - width + 1)
            if source_lines[index:index + width] == old
        ]
        if not matches:
            raise RuntimeError(
                f"structured update context does not match current source: {relative}"
            )
        if len(matches) != 1:
            raise RuntimeError(
                f"structured update context is ambiguous in current source: {relative}"
            )
        position = matches[0]
        source_lines[position:position + width] = new
        cursor = position + len(new)
    if not changed:
        raise RuntimeError(f"structured update for {relative} is a no-op")
    rendered = "\n".join(source_lines)
    return rendered + ("\n" if trailing_newline else "")


def apply_patch_action(tree: Path, patch: str) -> None:
    """Apply one model patch action exactly, then let callers derive a real git diff."""

    stripped = patch.strip()
    lines = stripped.splitlines()
    if (
        stripped.startswith("diff --git ")
        or (
            len(lines) >= 2
            and lines[0].startswith("--- ")
            and lines[1].startswith("+++ ")
        )
    ):
        apply_unified_diff(tree, patch)
        return
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise RuntimeError(
            "patch must be a git unified diff or a complete structured patch action"
        )

    pending: dict[Path, str | None] = {}
    index = 1
    while index < len(lines) - 1:
        header = re.fullmatch(r"\*\*\* (Update|Add|Delete) File: (.+)", lines[index])
        if header is None:
            raise RuntimeError("structured patch contains an invalid file header")
        operation, relative = header.groups()
        candidate = (tree.resolve() / relative).resolve()
        if not candidate.is_relative_to(tree.resolve()):
            raise RuntimeError("structured patch path escapes working tree")
        if candidate in pending:
            raise RuntimeError(f"structured patch repeats file action: {relative}")
        index += 1
        body: list[str] = []
        while index < len(lines) - 1 and not lines[index].startswith("*** "):
            body.append(lines[index])
            index += 1

        if operation == "Update":
            if not candidate.is_file():
                raise RuntimeError(f"structured update target does not exist: {relative}")
            original = candidate.read_text(encoding="utf-8", errors="strict")
            pending[candidate] = _structured_update(original, body, relative)
        elif operation == "Add":
            if candidate.exists():
                raise RuntimeError(f"structured add target already exists: {relative}")
            if any(not line.startswith("+") for line in body):
                raise RuntimeError(f"structured add for {relative} has a non-addition line")
            pending[candidate] = "\n".join(line[1:] for line in body) + "\n"
        else:
            if not candidate.is_file():
                raise RuntimeError(f"structured delete target does not exist: {relative}")
            pending[candidate] = None

    if not pending:
        raise RuntimeError("structured patch contains no file actions")
    originals = {
        path: path.read_bytes() if path.exists() else None for path in pending
    }
    try:
        for path, content in pending.items():
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    except OSError:
        for path, original in originals.items():
            if original is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original)
        raise


def apply_generator_result(tree: Path, generator_result) -> None:
    incremental = str(generator_result.incremental_diff)
    if not incremental.strip():
        return
    apply_unified_diff(tree, incremental)
    if generator_result.modified_tree:
        generated = Path(generator_result.modified_tree).resolve()
        if not generated.is_dir():
            raise FileNotFoundError(generated)
        replay_mismatch = diff_between(tree, generated)
        if not replay_mismatch.empty:
            raise RuntimeError(
                "applied generator diff does not reproduce its modified working tree"
            )
