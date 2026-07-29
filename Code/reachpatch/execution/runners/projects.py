from __future__ import annotations

import re
from pathlib import Path

from reachpatch.execution.models import NormalizedSelector

from .base import BaseProjectRunner


class PytestProjectRunner(BaseProjectRunner):
    name = "pytest"


class DjangoRunner(BaseProjectRunner):
    name = "django"
    package_names = ("django",)

    def normalize_selector(self, selector: str) -> NormalizedSelector:
        original = str(selector).strip()
        if not original:
            return NormalizedSelector(original, "", False, "empty selector")
        label = original.replace("\\", "/")
        path_part, separator, suffix = label.partition("::")
        path = Path(path_part)
        if path.is_absolute():
            try:
                path_part = str(path.resolve().relative_to(self.repository)).replace(
                    "\\", "/"
                )
            except ValueError:
                return NormalizedSelector(original, label, False, "selector outside repository")
        if path_part.endswith(".py"):
            if not (self.repository / path_part).is_file():
                return NormalizedSelector(original, path_part, False, "test module does not exist")
            path_part = path_part[:-3].replace("/", ".")
            if path_part.startswith("tests."):
                path_part = path_part[len("tests."):]
        elif "/" in path_part:
            path_part = path_part.strip("/").replace("/", ".")
        normalized = path_part
        if separator and suffix:
            normalized += "." + suffix.replace("::", ".")
        return NormalizedSelector(original, normalized, bool(normalized))

    def command_for_selector(self, selector: str) -> tuple[str, ...]:
        return (
            self.python_executable, "tests/runtests.py", selector,
            "--parallel", "1",
        )


class SymPyRunner(BaseProjectRunner):
    name = "sympy"
    package_names = ("sympy",)

    def normalize_selector(self, selector: str) -> NormalizedSelector:
        original = str(selector).strip()
        if not original:
            return NormalizedSelector(original, "", False, "empty selector")
        normalized = original.replace("\\", "/")
        path_part, separator, function = normalized.partition("::")
        path = Path(path_part)
        if path.is_absolute():
            try:
                path_part = str(path.resolve().relative_to(self.repository)).replace(
                    "\\", "/"
                )
            except ValueError:
                return NormalizedSelector(original, normalized, False, "selector outside repository")
        if path_part.endswith(".py"):
            if not (self.repository / path_part).is_file():
                return NormalizedSelector(original, path_part, False, "test file does not exist")
        elif path_part.startswith("test_") and "/" not in path_part and "." not in path_part:
            matches = []
            expression = re.compile(rf"^\s*def\s+{re.escape(path_part)}\s*\(", re.MULTILINE)
            for candidate in self.repository.rglob("test_*.py"):
                if any(part in {".git", ".venv", "venv", "__pycache__"} for part in candidate.parts):
                    continue
                try:
                    if expression.search(candidate.read_text(encoding="utf-8", errors="replace")):
                        matches.append(candidate)
                except OSError:
                    continue
            if len(matches) != 1:
                return NormalizedSelector(
                    original, path_part, False,
                    "bare SymPy test function is missing or ambiguous",
                )
            path_part = str(matches[0].relative_to(self.repository)).replace("\\", "/")
            function = original
            separator = "::"
        elif "." in path_part:
            candidate = path_part.replace(".", "/") + ".py"
            if (self.repository / candidate).is_file():
                path_part = candidate
            else:
                return NormalizedSelector(original, path_part, False, "unsupported SymPy selector")
        else:
            return NormalizedSelector(original, path_part, False, "unsupported SymPy selector")
        result = path_part + (f"::{function}" if separator and function else "")
        return NormalizedSelector(original, result, True)

    def command_for_selector(self, selector: str) -> tuple[str, ...]:
        path, separator, function = selector.partition("::")
        if (self.repository / "bin" / "test").is_file():
            command = [self.python_executable, "bin/test", path]
            if separator and function:
                command.extend(("-k", function))
            return tuple(command)
        return (
            self.python_executable, "-m", "pytest", "-q",
            path + (f"::{function}" if separator and function else ""),
        )


class AstropyRunner(PytestProjectRunner):
    name = "astropy"
    package_names = ("astropy",)


class ScikitLearnRunner(PytestProjectRunner):
    name = "scikit-learn"
    package_names = ("sklearn",)


class SphinxRunner(PytestProjectRunner):
    name = "sphinx"
    package_names = ("sphinx",)


class RequestsRunner(PytestProjectRunner):
    name = "requests"
    package_names = ("requests",)


class MatplotlibRunner(PytestProjectRunner):
    name = "matplotlib"
    package_names = ("matplotlib",)


def select_project_runner(repository: str | Path, **kwargs) -> BaseProjectRunner:
    root = Path(repository).resolve()
    if (root / "django").is_dir() and (root / "tests" / "runtests.py").is_file():
        cls = DjangoRunner
    elif (root / "sympy").is_dir():
        cls = SymPyRunner
    elif (root / "astropy").is_dir():
        cls = AstropyRunner
    elif (root / "sklearn").is_dir():
        cls = ScikitLearnRunner
    elif (root / "sphinx").is_dir():
        cls = SphinxRunner
    elif (root / "requests").is_dir():
        cls = RequestsRunner
    elif (root / "matplotlib").is_dir():
        cls = MatplotlibRunner
    else:
        cls = PytestProjectRunner
    return cls(root, **kwargs)
