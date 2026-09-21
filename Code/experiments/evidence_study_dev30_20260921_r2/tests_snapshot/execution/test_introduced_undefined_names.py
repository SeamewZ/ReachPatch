from reachpatch.execution.mechanical import find_introduced_undefined_names


def test_new_module_name_without_import_is_blocked():
    original = "def f(value):\n    return value\n"
    patched = "def f(value):\n    return numbers.Real(value)\n"
    findings = find_introduced_undefined_names(original, patched, {2}, "calc.py")
    assert [(item.name, item.line, item.severity) for item in findings] == [
        ("numbers", 2, "BLOCKER"),
    ]


def test_import_resolves_new_module_name():
    original = "def f(value):\n    return value\n"
    patched = "import numbers\ndef f(value):\n    return numbers.Real(value)\n"
    assert find_introduced_undefined_names(original, patched, {3}, "calc.py") == []


def test_builtins_and_function_locals_are_not_reported():
    original = "def f(value):\n    return value\n"
    patched = (
        "def f(value):\n"
        "    local = len(value)\n"
        "    return local\n"
    )
    assert find_introduced_undefined_names(original, patched, {2, 3}, "calc.py") == []


def test_comprehension_scope_is_lexical():
    original = "def f(values):\n    return values\n"
    patched = "def f(values):\n    return [item for item in values if item]\n"
    assert find_introduced_undefined_names(original, patched, {2}, "calc.py") == []


def test_global_and_nonlocal_bindings_are_respected():
    original = "value = 1\ndef outer():\n    value = 2\n    def inner():\n        return value\n    return inner\n"
    patched = "value = 1\ndef outer():\n    value = 2\n    def inner():\n        nonlocal value\n        return value\n    return inner\n"
    assert find_introduced_undefined_names(original, patched, {5, 6}, "calc.py") == []


def test_star_import_is_unknown_not_definite_failure():
    original = "from package import *\n"
    patched = "from package import *\nvalue = exported_name\n"
    findings = find_introduced_undefined_names(original, patched, {2}, "calc.py")
    assert len(findings) == 1
    assert findings[0].name == "exported_name"
    assert findings[0].severity == "UNKNOWN"


def test_existing_dynamic_global_reference_is_not_newly_reported():
    original = "def f():\n    return external_name\n"
    patched = "def f():\n    return external_name + 1\n"
    assert find_introduced_undefined_names(original, patched, {2}, "calc.py") == []
