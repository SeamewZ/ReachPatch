from __future__ import annotations

from .base import ProjectAdapter


class PythonAdapter(ProjectAdapter):
    name = "python"
    markers = ("pyproject.toml", "setup.py", "setup.cfg", "tox.ini")
    command_hints = (("python", "-m", "pytest"),)
    graph_hints = {
        "source_suffixes": (".py", ".pyi"),
        "public_surfaces": ("__all__", "package exports", "entry points"),
    }


class DjangoAdapter(PythonAdapter):
    name = "django"
    markers = ("django", "tests/runtests.py")
    graph_hints = {
        **PythonAdapter.graph_hints,
        "framework_surfaces": ("models", "querysets", "middleware", "settings"),
    }

    def semantic_facts(self, repository):
        facts = []
        for path in sorted(repository.rglob("*.py")):
            if any(part in {".venv", "venv", "build", "dist"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "models.Model" in text or "QuerySet" in text or ".objects" in text:
                facts.append({"label": "django.orm", "file": str(path.relative_to(repository)), "surface": "orm"})
            if "urlpatterns" in text or "middleware" in text:
                facts.append({"label": "django.routing", "file": str(path.relative_to(repository)), "surface": "routing"})
        return tuple(facts)

    def blocked_surfaces(self, repository):
        return ("database",) if any(
            "models.Model" in path.read_text(encoding="utf-8", errors="ignore")
            for path in repository.rglob("*.py")
            if path.is_file()
        ) else ()


class SymPyAdapter(PythonAdapter):
    name = "sympy"
    markers = ("sympy",)
    graph_hints = {
        **PythonAdapter.graph_hints,
        "protocol_surfaces": ("eval", "doit", "simplify", "assumptions"),
    }

    def semantic_facts(self, repository):
        facts = []
        for path in sorted(repository.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(token in text for token in ("sympy", "Symbol(", "simplify(", "assumptions")):
                facts.append({"label": "sympy.symbolic", "file": str(path.relative_to(repository)), "surface": "equivalence"})
        return tuple(facts)


class NumPyAdapter(PythonAdapter):
    name = "numpy"
    markers = ("numpy",)
    graph_hints = {
        **PythonAdapter.graph_hints,
        "object_shapes": ("shape", "dtype", "strides", "scalar-kind"),
    }

    def semantic_facts(self, repository):
        facts = []
        for path in sorted(repository.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(token in text for token in ("numpy", "np.", ".shape", ".dtype", "broadcast")):
                facts.append({"label": "numpy.array", "file": str(path.relative_to(repository)), "surface": "shape_dtype_broadcast"})
        return tuple(facts)


class RequestsAdapter(PythonAdapter):
    name = "requests"
    markers = ("requests",)
    graph_hints = {
        **PythonAdapter.graph_hints,
        "external_surfaces": ("sessions", "adapters", "hooks", "redirects"),
    }

    def semantic_facts(self, repository):
        facts = []
        for path in sorted(repository.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if any(token in text for token in ("requests.", "Response", "status_code", "raise_for_status")):
                facts.append({"label": "requests.response", "file": str(path.relative_to(repository)), "surface": "transport_response"})
        return tuple(facts)

    def blocked_surfaces(self, repository):
        return ("network_transport",)
