from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import ast
import builtins
import keyword
import re
import subprocess
from pathlib import Path
from typing import Any

from .base import SerializableRecord, content_hash, stable_id


class OutcomeStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"
    UNREACHABLE = "UNREACHABLE"
    PROVISIONAL = "PROVISIONAL"


class PairClassification(StrEnum):
    PASS_PRESERVED = "PASS_PRESERVED"
    TARGET_FIXED = "TARGET_FIXED"
    PRESERVATION_REGRESSION = "PRESERVATION_REGRESSION"
    TARGET_REGRESSED = "TARGET_REGRESSED"
    TARGET_STILL_FAILING = "TARGET_STILL_FAILING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ObservationContract(SerializableRecord):
    relation: str
    expected: Any
    observable: str = "return"
    comparator: str = "equals"

    _COMPARATORS = {
        "EXIT_ZERO", "EQUALS", "NOT_EQUALS", "RAISES",
        "NOT_RAISES", "TYPE_IS", "CONTAINS", "ORDER_EQUALS",
        "LENGTH_EQUALS", "HAS_ATTR", "STATE_DELTA_EQUALS",
        "RELATION_HOLDS",
    }

    @property
    def normalized_comparator(self) -> str:
        value = (self.comparator or "EQUALS").replace("-", "_").replace(" ", "_").upper()
        aliases = {"EQUAL": "EQUALS", "EXIT0": "EXIT_ZERO", "NOT_EQUAL": "NOT_EQUALS"}
        return aliases.get(value, value if value in self._COMPARATORS else "RELATION_HOLDS")

    @property
    def contract_id(self) -> str:
        return content_hash(self.normalized())

    def normalized(self) -> dict[str, Any]:
        relation = " ".join((self.relation or "").split()).casefold()
        return {
            "comparator": self.normalized_comparator,
            "relation": relation,
            "observable": " ".join((self.observable or "return").split()).casefold(),
            "expected": _normalize_observation_value(self.expected),
        }

    def matches(self, observation: Any) -> bool:
        """Compare a concrete observation without free-text equality."""
        value = observation
        if isinstance(observation, RunObservation):
            value = observation.value
            if value is None:
                output = observation.stdout.strip()
                if output:
                    last_line = output.splitlines()[-1]
                    try:
                        import json
                        value = json.loads(last_line)
                    except (json.JSONDecodeError, TypeError):
                        try:
                            value = ast.literal_eval(last_line)
                        except (ValueError, SyntaxError):
                            value = last_line
            if self.normalized_comparator == "EXIT_ZERO":
                if observation.return_code != 0 or observation.status is not OutcomeStatus.PASS:
                    return False
                if isinstance(self.expected, dict):
                    expected_payload = {
                        key: value for key, value in self.expected.items()
                        if key in {"stdout", "stderr", "value", "exception"}
                    }
                    if expected_payload:
                        observed_payload = {
                            "stdout": observation.stdout,
                            "stderr": observation.stderr,
                            "value": value,
                            "exception": observation.exception,
                        }
                        return all(
                            observed_payload.get(key) == expected_value
                            for key, expected_value in expected_payload.items()
                        )
                return True
            if self.normalized_comparator in {"RAISES", "NOT_RAISES"}:
                # Exception contracts are typed oracles.  A generic non-zero
                # exit is not enough for ``RAISES(ValueError)``: matching the
                # expected exception class (and optional message pattern) is
                # required, while NOT_RAISES must reject any actual exception.
                raised_text = "\n".join(
                    item for item in (observation.exception, observation.stderr)
                    if item
                )
                raised = bool(raised_text) or observation.status is OutcomeStatus.FAIL
                if self.normalized_comparator == "NOT_RAISES":
                    return not raised
                expected = self.expected
                if isinstance(expected, dict):
                    expected_type = str(
                        expected.get("exception_type")
                        or expected.get("type")
                        or expected.get("exception")
                        or ""
                    )
                    message_pattern = expected.get("message_pattern", expected.get("message"))
                else:
                    expected_type = str(expected or "")
                    message_pattern = None
                expected_type = expected_type.rsplit(".", 1)[-1]
                type_matches = (
                    not expected_type
                    or re.search(rf"(?<![A-Za-z0-9_]){re.escape(expected_type)}(?![A-Za-z0-9_])", raised_text)
                    is not None
                )
                message_matches = (
                    message_pattern is None
                    or str(message_pattern) in raised_text
                )
                return raised and type_matches and message_matches
        comparator = self.normalized_comparator
        expected = self.expected
        if isinstance(observation, RunObservation) and isinstance(expected, dict) and any(
            key in expected for key in ("exit_code", "stdout", "stderr", "value", "exception")
        ):
            observed = {
                "exit_code": observation.return_code,
                "stdout": observation.stdout,
                "stderr": observation.stderr,
                "value": value,
                "exception": observation.exception,
            }
            return all(observed.get(key) == expected_value for key, expected_value in expected.items())
        if comparator in {"EQUALS", "ORDER_EQUALS", "STATE_DELTA_EQUALS"}:
            return _normalize_observation_value(value) == _normalize_observation_value(expected)
        if comparator == "NOT_EQUALS":
            return _normalize_observation_value(value) != _normalize_observation_value(expected)
        if comparator == "TYPE_IS":
            return type(value).__name__ == str(expected) or type(value).__qualname__ == str(expected)
        if comparator == "CONTAINS":
            try:
                return expected in value
            except TypeError:
                return False
        if comparator == "LENGTH_EQUALS":
            try:
                return len(value) == int(expected)
            except (TypeError, ValueError):
                return False
        if comparator == "HAS_ATTR":
            return hasattr(value, str(expected))
        # RELATION_HOLDS is intentionally conservative.  A relation supplied
        # by an authority is executable only when the caller provides a
        # boolean observation; text is never treated as proof.
        return value is True


def _normalize_observation_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_observation_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_observation_value(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return value


@dataclass(frozen=True, slots=True)
class ExceptionContract(SerializableRecord):
    exception_type: str
    message_pattern: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceRecord(SerializableRecord):
    evidence_id: str
    source: str
    authority: str
    content: str
    executable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutableCheck(SerializableRecord):
    check_id: str
    command: tuple[str, ...]
    role: str
    authority: str
    requirement_ids: tuple[str, ...] = ()
    symbol_references: tuple[str, ...] = ()
    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 60.0
    expected: ObservationContract | None = None
    concrete_input: Any = None
    source_evidence_ids: tuple[str, ...] = ()
    # Execution-driven aliases.  They are data only; graph objects are not
    # required to create or certify a check.
    goal_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    target_symbols: tuple[str, ...] = ()
    input_recipe: Any = None

    @property
    def trusted(self) -> bool:
        return self.authority in {"A", "B", "C"}

    @property
    def comparator(self) -> str:
        expected = self.expected
        return expected.normalized_comparator if isinstance(expected, ObservationContract) else "RELATION_HOLDS"


@dataclass(frozen=True, slots=True)
class PublicEvidence(SerializableRecord):
    records: tuple[EvidenceRecord, ...] = ()
    checks: tuple[ExecutableCheck, ...] = ()
    api_contracts: tuple[EvidenceRecord, ...] = ()
    baseline_contracts: tuple[EvidenceRecord, ...] = ()

    def by_id(self) -> dict[str, EvidenceRecord]:
        return {
            item.evidence_id: item
            for item in (*self.records, *self.api_contracts, *self.baseline_contracts)
        }


_FENCED_CODE = re.compile(
    r"```(?:python|py)?[ \t]*\r?\n(?P<body>.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_REPL_LINE = re.compile(
    r"^\s*(>>>|\.\.\.|In\s*\[\d+\]:|\.\.\.+:?)\s?(.*)$",
    re.IGNORECASE,
)
_FORBIDDEN_WITNESS_IMPORTS = {
    "ctypes", "ftplib", "glob", "http", "multiprocessing", "os", "pathlib",
    "pickle", "requests", "shutil", "signal", "socket", "subprocess",
    "tempfile", "urllib",
}
_FORBIDDEN_WITNESS_CALLS = {
    "__import__", "breakpoint", "compile", "eval", "exec", "input", "open",
    "popen", "remove", "removedirs", "rename", "replace", "rmdir", "rmtree",
    "system", "unlink", "write_bytes", "write_text",
}
_NON_NORMATIVE_ISSUE_MARKER = re.compile(
    r"(?im)^\s*(?:public\s+)?(?:maintainer\s+)?(?:hints?|discussion|comments?)\s*:\s*$"
)


def primary_issue_content(content: str) -> str:
    """Return the reporter-owned issue body, excluding appended discussion.

    Public discussion remains available to the generator as evidence, but its
    proposed implementations are not normative behavior contracts or issue
    witnesses.
    """

    marker = _NON_NORMATIVE_ISSUE_MARKER.search(content)
    return content[:marker.start()].rstrip() if marker is not None else content


def _repl_source(block: str) -> str | None:
    statements: list[str] = []
    current: list[str] = []
    saw_prompt = False
    for raw_line in block.splitlines():
        match = _REPL_LINE.match(raw_line)
        if match is None:
            continue
        saw_prompt = True
        prompt, value = match.groups()
        if (prompt == ">>>" or prompt.lower().startswith("in")) and current:
            statements.append("\n".join(current))
            current = []
        current.append(value)
    if current:
        statements.append("\n".join(current))
    return "\n".join(statements) if saw_prompt else None


def _repl_expected_outputs(block: str) -> dict[int, str]:
    outputs: dict[int, str] = {}
    statement_index = -1
    output_lines: list[str] = []

    def finish() -> None:
        nonlocal output_lines
        while output_lines and not output_lines[0].strip():
            output_lines.pop(0)
        while output_lines and not output_lines[-1].strip():
            output_lines.pop()
        # REPL transcripts frequently print an exception banner before the
        # actual ``Traceback (most recent call last)`` line (for example,
        # ``ValueError Traceback ...``).  Everything in that output block is
        # observed failure text, never the expected stdout contract.
        traceback_at = next((
            index for index, line in enumerate(output_lines)
            if re.search(r"\bTraceback\b|\b(?:Error|Exception|Warning)\s+Traceback", line)
            or line.lstrip().startswith(("File \"", "---->"))
        ), len(output_lines))
        if traceback_at == len(output_lines) and any(
            re.search(r"\b(?:Error|Exception)\b", line)
            for line in output_lines
        ):
            # A traceback banner may be rendered without the word
            # ``Traceback``; do not turn diagnostic exception text into an
            # exact stdout expectation.
            output_lines = []
            return
        if traceback_at < len(output_lines):
            output_lines = []
            return
        while output_lines and not output_lines[-1].strip():
            output_lines.pop()
        if output_lines:
            normalized = [
                re.sub(r"^Out\[\d+\]:\s?", "", line)
                for line in output_lines
            ]
            outputs[statement_index] = "\n".join(normalized) + "\n"
        output_lines = []

    continuation = False
    for raw_line in block.splitlines():
        match = _REPL_LINE.match(raw_line)
        if match is not None:
            prompt, _ = match.groups()
            starts_statement = prompt == ">>>" or prompt.lower().startswith("in")
            if starts_statement:
                if statement_index >= 0:
                    finish()
                statement_index += 1
                continuation = True
            continue
        if statement_index >= 0 and continuation:
            output_lines.append(raw_line)
    if statement_index >= 0:
        finish()
    return outputs


def _safe_witness_expression(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, (
            ast.Await, ast.Delete, ast.GeneratorExp, ast.Lambda, ast.NamedExpr,
            ast.Yield, ast.YieldFrom,
        )):
            return False
        if isinstance(child, ast.Attribute) and child.attr.startswith("__"):
            return False
        if isinstance(child, ast.Name) and child.id in _FORBIDDEN_WITNESS_CALLS:
            return False
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                name = child.func.id
            elif isinstance(child.func, ast.Attribute):
                name = child.func.attr
            else:
                return False
            if name in _FORBIDDEN_WITNESS_CALLS or name.startswith("__"):
                return False
    return True


def _safe_witness_statement(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        modules = (
            [item.name for item in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
        return all(
            module.split(".", 1)[0] not in _FORBIDDEN_WITNESS_IMPORTS
            for module in modules
        )
    if isinstance(node, ast.Assign):
        return all(
            isinstance(target, (ast.Name, ast.Tuple, ast.List))
            for target in node.targets
        ) and _safe_witness_expression(node.value)
    if isinstance(node, ast.AnnAssign):
        return isinstance(node.target, ast.Name) and (
            node.value is None or _safe_witness_expression(node.value)
        )
    if isinstance(node, (ast.Expr, ast.Assert)):
        return _safe_witness_expression(node)
    if isinstance(node, ast.ClassDef):
        return (
            not node.decorator_list
            and all(_safe_witness_expression(item) for item in node.bases)
            and all(_safe_witness_expression(item.value) for item in node.keywords)
            and all(
                isinstance(item, ast.Pass) or _safe_witness_statement(item)
                for item in node.body
            )
        )
    return False


def _call_operation(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return "public operation"


_PLAIN_PYTHON_START = re.compile(
    r"^(?:from\s+[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s+import\s+|"
    r"import\s+[A-Za-z_]\w*|class\s+[A-Za-z_]\w*\s*(?:\([^\n]*\))?\s*:|"
    r"def\s+[A-Za-z_]\w*\s*\()"
)
_PLAIN_PYTHON_TOP_LEVEL = re.compile(
    r"^(?:from\s+|import\s+|class\s+|def\s+|async\s+def\s+|@|"
    r"[A-Za-z_]\w*\s*(?::[^=]+)?=)"
)


def _plain_python_blocks(content: str) -> tuple[tuple[int, str, str], ...]:
    """Find bounded, parseable top-level Python examples outside fences."""

    visible = _FENCED_CODE.sub(lambda match: "\n" * match.group(0).count("\n"), content)
    lines = visible.splitlines()
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    blocks: list[tuple[int, str, str]] = []
    index = 0
    while index < len(lines):
        if not _PLAIN_PYTHON_START.match(lines[index].strip()):
            index += 1
            continue
        start = index
        candidate = [lines[index]]
        index += 1
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if not stripped:
                candidate.append(line)
                index += 1
                continue
            if line[:1].isspace() or _PLAIN_PYTHON_TOP_LEVEL.match(stripped):
                candidate.append(line)
                index += 1
                continue
            break
        source = "\n".join(candidate).strip()
        try:
            module = ast.parse(source)
        except SyntaxError:
            index = max(index, start + 1)
            continue
        if (
            len(module.body) >= 2
            and any(isinstance(item, (ast.ClassDef, ast.FunctionDef)) for item in module.body)
        ):
            trailing = "\n".join(lines[index:index + 12])
            blocks.append((offsets[start], source, trailing))
    return tuple(blocks)


def _external_observation_expression(text: str) -> str | None:
    match = re.search(
        r"\b(?:accessing|evaluating|calling|rendering)\s+"
        r"(?P<expression>[A-Za-z_]\w*(?:\([^\n()]*\))?"
        r"(?:\.[A-Za-z_]\w*(?:\([^\n()]*\))?)*)"
        r"\s+(?:results?|returns?|produces?|gives?)\b",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    expression = match.group("expression")
    try:
        ast.parse(expression, mode="eval")
    except SyntaxError:
        return None
    return expression


def _ordered_expected_values(text: str) -> tuple[str, ...]:
    match = re.search(
        r"\b(?:(?:in|into|with)\s+(?:the\s+)?order|"
        r"(?:produce|return|yield|give|be)\s+(?:the\s+)?order)\s+"
        r"(?P<values>[^\n]+)",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return ()
    value_text = re.split(r"(?<=[.!?])\s+(?=[A-Z])", match.group("values"), 1)[0]
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", value_text)
    values = quoted or re.findall(
        r"[A-Za-z0-9_@+/-]+(?:\.[A-Za-z0-9_@+/-]+)+",
        value_text,
    )
    return tuple(dict.fromkeys(value.rstrip("." ) for value in values))


def _plain_witness_source(source: str, trailing: str) -> tuple[str, str] | None:
    expression = _external_observation_expression(trailing)
    if expression is None:
        return None
    expected_order = _ordered_expected_values(trailing)
    target = f"_reachpatch_observed = {expression}"
    if expected_order:
        target += (
            "\n_reachpatch_rendered = repr(_reachpatch_observed)"
            f"\n_reachpatch_expected_order = {expected_order!r}"
            "\nassert all([item in _reachpatch_rendered for item in _reachpatch_expected_order])"
            "\nassert [_reachpatch_rendered.index(item) for item in _reachpatch_expected_order] == "
            "sorted([_reachpatch_rendered.index(item) for item in _reachpatch_expected_order])"
        )
    else:
        target += "\nassert _reachpatch_observed is not None"
    return f"{source}\n{target}\n", expression


def _compiled_expected_order(module: ast.Module) -> tuple[Any, ...]:
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "_reachpatch_expected_order"
            for target in statement.targets
        ):
            continue
        try:
            value = ast.literal_eval(statement.value)
        except (ValueError, TypeError):
            return ()
        return tuple(value) if isinstance(value, (list, tuple)) else ()
    return ()


def _nearby_expected_exception(content: str, expression: str) -> str | None:
    """Find an explicit expected exception clause adjacent to a witness."""
    if not expression:
        return None
    position = content.find(expression)
    if position < 0:
        # AST-unparsed source may normalize whitespace; use the operation name
        # only as a conservative fallback and keep the same local window.
        operation = expression.rsplit(".", 1)[-1].split("(", 1)[0]
        position = content.find(operation)
    if position < 0:
        return None
    prefix = content[max(0, position - 420):position]
    match = re.search(
        r"\b(?:must|should|shall|expected\s+to|expects?\s+the\s+call\s+to)\s+(?:raise|throw)\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
        prefix, re.IGNORECASE,
    )
    return match.group(1) if match else None


def extract_issue_witnesses(
    content: str,
    evidence_id: str,
) -> tuple[dict[str, Any], ...]:
    """Extract deterministic, side-effect-bounded Python witnesses from an issue.

    Only prompts or syntactically valid fenced Python are considered. Console
    output and traceback text are never copied into the executable script.
    """

    witnesses: list[dict[str, Any]] = []
    content = primary_issue_content(content)
    public_text = re.sub(r"```.*?```", " ", content, flags=re.DOTALL)
    positive_contract = bool(re.search(
        r"\b(?:must|should|support|works?|succeed|allow|accept|return|produce|"
        r"would\s+be\s+great|fails?|raises?|throws?|errors?)\b",
        public_text,
        re.IGNORECASE,
    ))
    candidates: list[tuple[int, str, dict[int, str], str | None, str]] = []
    for block_index, match in enumerate(_FENCED_CODE.finditer(content)):
        block = match.group("body")
        source = _repl_source(block)
        repl_outputs = _repl_expected_outputs(block) if source is not None else {}
        if source is None:
            source = block.strip()
        # Keep only the prose immediately adjacent to this fence.  This lets
        # a normative sentence such as "should return empty arrays" ground
        # the following witness without allowing an unrelated positive issue
        # sentence to promote every code block.
        adjacent = content[max(0, match.start() - 600):match.start()]
        candidates.append((block_index, source, repl_outputs, None, adjacent))
    for plain_index, (_, source, trailing) in enumerate(_plain_python_blocks(content)):
        compiled = _plain_witness_source(source, trailing)
        if compiled is not None:
            witness_source, expression = compiled
            candidates.append((10_000 + plain_index, witness_source, {}, expression, trailing))

    for block_index, source, repl_outputs, operation_expression, adjacent_context in candidates:
        if not source:
            continue
        try:
            module = ast.parse(source)
        except SyntaxError:
            continue
        if not module.body or not all(_safe_witness_statement(item) for item in module.body):
            continue
        if operation_expression is not None:
            target_start = next((
                index for index, statement in enumerate(module.body)
                if isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name)
                    and target.id == "_reachpatch_observed"
                    for target in statement.targets
                )
            ), None)
            if target_start is None:
                continue
            setup_script = "\n".join(
                ast.unparse(item) for item in module.body[:target_start]
            )
            target_source = "\n".join(
                ast.unparse(item) for item in module.body[target_start:]
            )
            reset_script = (
                "import builtins\n"
                "getattr(builtins, '__reachpatch_trace_reset__', lambda: None)()"
            )
            script = "\n".join(filter(None, (
                setup_script, reset_script, target_source,
            ))) + "\n"
            try:
                compile(script, "<reachpatch-issue-witness>", "exec")
            except SyntaxError:
                continue
            operation = operation_expression.rsplit(".", 1)[-1]
            expected_exception = _nearby_expected_exception(content, operation_expression)
            witness_id = stable_id(
                "witness", evidence_id, block_index, operation_expression, script,
            )
            witnesses.append({
                "witness_id": witness_id,
                "evidence_id": evidence_id,
                "language": "python",
                "script": script,
                "operation": operation,
                "target_expression": operation_expression,
                "expected_relation": (
                    f"issue witness raises {expected_exception}"
                    if expected_exception else "issue witness satisfies its explicit observation"
                ),
                "expected": (
                    {"exception_type": expected_exception}
                    if expected_exception else {"exit_code": 0}
                ),
                "expected_order": _compiled_expected_order(module),
                "authority": "B" if positive_contract else "PROVISIONAL",
                "_adjacent_context": adjacent_context,
                "derivation": (
                    f"validated plain Python witness from {evidence_id}",
                    f"observable expression is {operation_expression}",
                    "ordered expected values were compiled into an executable assertion",
                ),
            })
            continue
        setup: list[ast.stmt] = []
        target_indexes = [
            index for index, statement in enumerate(module.body)
            if (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Call)
            ) or isinstance(statement, ast.Assert)
        ]
        if not target_indexes:
            assigned_calls = [
                index for index, statement in enumerate(module.body)
                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                and isinstance(statement.value, ast.Call)
            ]
            target_indexes = assigned_calls[-1:]
        if operation_expression is not None:
            target_indexes = (len(module.body) - 1,)
        for statement_index, statement in enumerate(module.body):
            if statement_index not in target_indexes:
                if isinstance(statement, (
                    ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign,
                    ast.ClassDef,
                )):
                    setup.append(statement)
                continue
            target_call = (
                statement.value
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                else statement.value
                if isinstance(statement, (ast.Assign, ast.AnnAssign)) and isinstance(statement.value, ast.Call)
                else next(
                    (item for item in ast.walk(statement) if isinstance(item, ast.Call)),
                    None,
                )
            )
            if target_call is None:
                continue
            setup_script = "\n".join(ast.unparse(item) for item in setup)
            reset_script = (
                "import builtins\n"
                "getattr(builtins, '__reachpatch_trace_reset__', lambda: None)()"
            )
            target_source = ast.unparse(statement)
            executable_target = (
                f"exec(compile({target_source!r}, "
                "'<reachpatch-issue-witness>', 'single'))"
                if isinstance(statement, ast.Expr) else target_source
            )
            script = "\n".join(filter(None, (
                setup_script, reset_script, executable_target,
            ))) + "\n"
            try:
                compile(script, "<reachpatch-issue-witness>", "exec")
            except SyntaxError:
                continue
            operation = (
                operation_expression.rsplit(".", 1)[-1]
                if operation_expression is not None
                else _call_operation(target_call)
            )
            witness_id = stable_id(
                "witness", evidence_id, block_index, statement_index, script,
            )
            expected: dict[str, Any] = {"exit_code": 0}
            expected_output = repl_outputs.get(statement_index)
            if expected_output is not None:
                expected["stdout"] = expected_output
            expected_exception = _nearby_expected_exception(content, target_source)
            if expected_exception:
                expected = {"exception_type": expected_exception}
            witnesses.append({
                "witness_id": witness_id,
                "evidence_id": evidence_id,
                "language": "python",
                "script": script,
                "operation": operation,
                "target_expression": target_source,
                "expected_relation": (
                    f"issue witness raises {expected_exception}"
                    if expected_exception else (
                        "issue witness completes with the displayed stdout"
                        if expected_output is not None
                        else "issue witness completes successfully"
                    )
                ),
                "expected": expected,
                "authority": "B" if positive_contract else "PROVISIONAL",
                "_adjacent_context": adjacent_context,
                "derivation": (
                    f"validated fenced Python witness from {evidence_id}",
                    f"target call is {operation}",
                    "traceback lines were excluded from the expected observation",
                ),
            })
    # Authority is local to the witness, never inherited from an unrelated
    # positive sentence elsewhere in the issue.  Only an adjacent normative
    # clause (or an explicit Expected/Desired section) can promote a witness
    # to reporter-grounded Authority B.
    for witness in witnesses:
        expression = str(witness.get("target_expression", ""))
        position = content.find(expression) if expression else -1
        # Restrict authority inference to the witness-local prose clause. A
        # positive sentence elsewhere in the issue must not promote every
        # code block to reporter-grounded evidence.
        local = ""
        if position >= 0:
            line_start = content.rfind("\n", 0, position) + 1
            line_end = content.find("\n", position)
            if line_end < 0:
                line_end = len(content)
            local = content[line_start:line_end]
            # Fenced/repl witnesses place the executable expression on a
            # source-only line.  Include only a bounded adjacent prose window
            # when that line itself carries no normative clause; the operation
            # name must still occur in the window, preventing an unrelated
            # positive sentence from promoting every witness to Authority B.
            if not local.strip() or local.lstrip().startswith((">>>",)) or not re.search(
                r"\b(?:expected|desired|must|should|shall|return|raise|without\s+(?:an?\s+)?(?:error|exception)|support|accept|allow)\b",
                local, re.IGNORECASE,
            ):
                local = content[max(0, position - 240):position]
        operation_name = str(witness.get("operation", "")).rsplit(".", 1)[-1]
        adjacent = str(witness.pop("_adjacent_context", ""))
        explicit_heading = bool(re.search(r"(?im)^\s*(?:expected|desired)\s*:", local))
        explicit = explicit_heading or bool(
            operation_name and operation_name.casefold() in local.casefold()
            and re.search(
                r"\b(?:expected|desired|must|should|shall|return|raise|without\s+(?:an?\s+)?(?:error|exception)|support|accept|allow)\b",
                local, re.IGNORECASE,
            )
        )
        adjacent_explicit = bool(
            operation_name
            and re.search(
                r"\b(?:expected|desired|must|should|shall|return|raise|without\s+(?:an?\s+)?(?:error|exception)|support|accept|allow)\b",
                adjacent,
                re.IGNORECASE,
            )
            and not re.search(r"\b(?:for example|e\.g\.|such as)\b", adjacent, re.IGNORECASE)
        )
        if not explicit and not adjacent_explicit:
            witness["authority"] = "PROVISIONAL"
        elif adjacent_explicit:
            witness["authority"] = "B"
    unique = {str(item["witness_id"]): item for item in witnesses}
    return tuple(unique[key] for key in sorted(unique))


def issue_witnesses(record: EvidenceRecord) -> tuple[dict[str, Any], ...]:
    raw_values = record.metadata.get("issue_witnesses")
    if isinstance(raw_values, (list, tuple)):
        valid = tuple(
            dict(item) for item in raw_values
            if isinstance(item, dict)
            and isinstance(item.get("witness_id"), str)
            and isinstance(item.get("script"), str)
            and isinstance(item.get("operation"), str)
        )
        if valid:
            return valid
    return extract_issue_witnesses(record.content, record.evidence_id)


_GENERIC_METHOD_NAMES = {
    "__init__", "add", "clean", "close", "create", "delete", "get", "load",
    "open", "read", "remove", "run", "save", "set", "update", "write",
    "data", "line", "lines", "result", "value", "values",
}


def _changed_public_symbols(repository: Path, actual_diff: ActualDiff) -> tuple[str, ...]:
    values = list(actual_diff.changed_symbols)
    for hunk in actual_diff.hunks:
        path = repository / hunk.path
        if not path.is_file() or path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        changed_lines = hunk.changed_new_lines or (hunk.new_start,)
        enclosing = sorted(
            (
                node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and any(
                    node.lineno <= line <= getattr(node, "end_lineno", node.lineno)
                    for line in changed_lines
                )
            ),
            key=lambda node: getattr(node, "end_lineno", node.lineno) - node.lineno,
        )
        values.extend(node.name for node in enclosing[:3])
        changed_line_set = set(changed_lines)
        for node in ast.walk(tree):
            node_lines = range(
                getattr(node, "lineno", -1),
                getattr(node, "end_lineno", getattr(node, "lineno", -1)) + 1,
            )
            if not changed_line_set.intersection(node_lines):
                continue
            if isinstance(node, ast.Name):
                values.append(node.id)
            elif isinstance(node, ast.Attribute):
                values.append(node.attr)
            elif isinstance(node, ast.arg):
                values.append(node.arg)
    return tuple(dict.fromkeys(
        value for value in values
        if re.fullmatch(r"[A-Za-z_]\w*", value)
        and value not in {"self", "cls"}
        and value not in _GENERIC_METHOD_NAMES
        and value not in set(dir(builtins))
        and not keyword.iskeyword(value)
        and len(value) >= 3
    ))[:16]


def _test_command(repository: Path, relative: str, test_name: str) -> tuple[str, ...]:
    if (repository / "tests" / "runtests.py").is_file() and relative.startswith("tests/"):
        module = relative.removeprefix("tests/")[:-3].replace("/", ".")
        return ("python", "tests/runtests.py", f"{module}.{test_name}")
    return (
        "python", "-m", "pytest", "-q",
        f"{relative}::{test_name.replace('.', '::')}",
    )


def _qualified_test_functions(tree: ast.AST):
    def visit(body: list[ast.stmt], prefix: tuple[str, ...] = ()):
        for node in body:
            if isinstance(node, ast.ClassDef):
                yield from visit(node.body, (*prefix, node.name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test"):
                    yield node, ".".join((*prefix, node.name))

    yield from visit(getattr(tree, "body", []))


def discover_diff_public_checks(
    repository: Path,
    actual_diff: ActualDiff,
    existing_checks: tuple[ExecutableCheck, ...] = (),
    *,
    max_checks: int = 6,
) -> tuple[ExecutableCheck, ...]:
    """Discover bounded public tests with real AST references to changed symbols."""

    symbols = _changed_public_symbols(repository, actual_diff)
    if not symbols or max_checks <= 0:
        return ()
    pattern = r"\b(?:" + "|".join(map(re.escape, symbols)) + r")\b"
    command = [
        "rg", "-l", "-g", "test*.py", "-g", "*_test.py",
        "-g", "!**/.git/**", pattern, str(repository),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=20,
        )
        candidates = completed.stdout.splitlines() if completed.returncode in {0, 1} else []
    except (OSError, subprocess.TimeoutExpired):
        candidates = []
    existing_ids = {check.check_id for check in existing_checks}
    discovered: list[tuple[tuple[int, ...], ExecutableCheck]] = []
    changed_parents = tuple(Path(path).parent.parts for path in actual_diff.changed_files)
    changed_stems = {Path(path).stem.casefold() for path in actual_diff.changed_files}
    symbol_tokens = {symbol.casefold() for symbol in symbols}
    changed_path_tokens = {
        token.casefold()
        for changed in actual_diff.changed_files
        for part in Path(changed).parts
        for token in re.findall(r"[A-Za-z0-9]+", part)
    }

    def candidate_priority(path: Path) -> tuple[int, int, int, str]:
        path_tokens = {
            token.casefold()
            for part in path.parts
            for token in re.findall(r"[A-Za-z0-9]+", part)
        }
        stem_tokens = {
            token.casefold() for token in re.findall(r"[A-Za-z0-9]+", path.stem)
        }
        return (
            -len(stem_tokens.intersection(symbol_tokens)),
            -len(path_tokens.intersection(changed_path_tokens)),
            len(path.parts),
            path.as_posix(),
        )

    ranked = sorted((Path(value) for value in candidates), key=candidate_priority)
    for path in ranked[:24]:
        try:
            relative = path.resolve().relative_to(repository.resolve()).as_posix()
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=relative)
        except (OSError, SyntaxError, ValueError):
            continue
        source_lines = source.splitlines()
        for node, test_name in _qualified_test_functions(tree):
            node_source = "\n".join(source_lines[
                max(0, node.lineno - 1):getattr(node, "end_lineno", node.lineno)
            ])
            referenced = tuple(
                symbol for symbol in symbols
                if re.search(
                    rf"\b{re.escape(symbol)}\b",
                    node_source,
                )
            )
            if not referenced:
                continue
            check_id = stable_id("diff-public-check", relative, test_name, referenced)
            if check_id in existing_ids:
                continue
            test_parts = Path(relative).parts
            proximity = max((
                next((index for index, (left, right) in enumerate(zip(parent, test_parts))
                      if left != right), min(len(parent), len(test_parts)))
                for parent in changed_parents
            ), default=0)
            symbol_strength = sum(
                4 if any(character.isupper() for character in symbol)
                else 3 if "_" in symbol
                else 2
                for symbol in referenced
            )
            same_module_test = any(
                Path(relative).stem.casefold() in {
                    f"test_{stem}", f"{stem}_test"
                }
                for stem in changed_stems
            )
            check = ExecutableCheck(
                check_id=check_id,
                command=_test_command(repository, relative, test_name),
                role="PRESERVATION",
                authority="A",
                symbol_references=referenced,
                timeout_seconds=120.0,
                expected=ObservationContract(
                    relation=f"public test {relative}::{node.name} must remain successful",
                    expected={"exit_code": 0},
                    observable="process",
                    comparator="EXIT_ZERO",
                ),
                source_evidence_ids=(stable_id(
                    "public-test-evidence", relative, node.name, referenced,
                ),),
            )
            discovered.append((
                (
                    not same_module_test, -symbol_strength,
                    -len(referenced), -proximity,
                    len(test_parts), relative, node.name,
                ),
                check,
            ))
            existing_ids.add(check_id)
    discovered.sort(key=lambda item: item[0])
    return tuple(check for _, check in discovered[:max_checks])


@dataclass(frozen=True, slots=True)
class DiffHunk(SerializableRecord):
    hunk_id: str
    path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: tuple[str, ...]
    changed_symbols: tuple[str, ...] = ()

    @property
    def changed_new_lines(self) -> tuple[int, ...]:
        line = self.new_start
        result: list[int] = []
        for value in self.lines:
            if value.startswith("+") and not value.startswith("+++"):
                result.append(line)
                line += 1
            elif value.startswith("-") and not value.startswith("---"):
                continue
            else:
                line += 1
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ActualDiff(SerializableRecord):
    canonical_diff: str
    patch_hash: str
    hunks: tuple[DiffHunk, ...]
    changed_files: tuple[str, ...]
    changed_symbols: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not self.canonical_diff.strip()

    @classmethod
    def empty_diff(cls) -> "ActualDiff":
        return cls("", content_hash(""), (), (), ())


@dataclass(frozen=True, slots=True)
class RunObservation(SerializableRecord):
    status: OutcomeStatus
    return_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    value: Any = None
    exception: str | None = None


@dataclass(frozen=True, slots=True)
class TraceBundle(SerializableRecord):
    trace_bundle_id: str
    tree_hash: str
    command: tuple[str, ...]
    observation: RunObservation
    executed_symbol_ids: tuple[str, ...]
    executed_path_ids: tuple[str, ...]
    executed_line_ids: tuple[str, ...] = ()
    state_reads: tuple[str, ...] = ()
    state_writes: tuple[str, ...] = ()
    dispatch_routes: tuple[str, ...] = ()
    first_project_frame: str | None = None
    stable_runs: int = 1
    comparable: bool = True
    cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    backend: str = "shared-executor"
    # Localization context copied from the first traced run. It is not
    # certification evidence and is ignored by semantic stability checks.
    events: tuple[tuple[Any, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class PairedTraceBundle(SerializableRecord):
    paired_bundle_id: str
    check_id: str
    challenge_id: str
    patch_hash: str
    baseline: TraceBundle
    patched: TraceBundle
    classification: PairClassification
    oracle_id: str
    oracle_authority: str
    expected_relation: str
    stable_runs: int
    previous: TraceBundle | None = None
    # The normalized observation-contract identity is execution evidence.
    # ``expected_relation`` remains a human-readable diagnostic only.
    oracle_contract_id: str = ""

    @property
    def comparable(self) -> bool:
        return self.baseline.comparable and self.patched.comparable


@dataclass(frozen=True, slots=True)
class ExecutableOracle(SerializableRecord):
    oracle_id: str
    authority: str
    relation: str
    expected: Any
    executable: bool
    source_evidence_ids: tuple[str, ...] = ()

    @property
    def trusted(self) -> bool:
        return self.authority in {"A", "B", "C"}


@dataclass(frozen=True, slots=True)
class OracleResolution(SerializableRecord):
    oracle: ExecutableOracle | None
    frontier: str | None
    exploration_only: bool


@dataclass(frozen=True, slots=True)
class CounterexamplePacket(SerializableRecord):
    counterexample_id: str
    requirement_id: str
    binding_id: str
    challenge_id: str
    patch_hash: str
    reproduction_command: tuple[str, ...]
    concrete_input: Any
    input_derivation: tuple[str, ...]
    oracle_id: str
    oracle_authority: str
    expected_relation: str
    baseline_observation: Any
    patched_observation: Any
    failure_signature: str
    first_divergence: Any
    executed_path_ids: tuple[str, ...]
    changed_hunk_ids: tuple[str, ...]
    causal_cut_ids: tuple[str, ...]
    impact_risk_ids: tuple[str, ...]
    protected_target_ids: tuple[str, ...]
    protected_preservation_ids: tuple[str, ...]
    suggested_action_families: tuple[str, ...]
    # Structured execution evidence used by RepairFrontier and DeepSeek.
    frontier_kind: str | None = None
    frontier_id: str | None = None
    observation_projection: Any = None
    command_cwd: str = "."
    environment: tuple[tuple[str, str], ...] = ()
    backend: str = "shared-executor"
    stdout: str = ""
    stderr: str = ""
    first_project_frame: str | None = None
    binding_path_hit: bool = False
    changed_hunk_hit: bool = False
    changed_hunks: tuple[dict[str, Any], ...] = ()
    causal_cuts: tuple[dict[str, Any], ...] = ()
    protected_behavior: tuple[str, ...] = ()
    failure_kind: str | None = None
    stability_evidence: dict[str, Any] = field(default_factory=dict)
    # Structured observations are the executable contract for the next repair
    # objective.  The legacy baseline/patched fields remain as raw trace
    # payloads, while these aliases make incumbent-vs-trial comparisons
    # explicit and prevent a free-text relation from being mistaken for an
    # oracle value.
    expected_observation: Any = None
    incumbent_observation: Any = None
    trial_observation: Any = None
    comparator: str = "RELATION_HOLDS"


@dataclass(frozen=True, slots=True)
class ConfirmedFailure(SerializableRecord):
    failure_id: str
    requirement_id: str
    binding_id: str
    challenge_id: str
    counterexample_id: str
    patch_hash: str
    failure_signature: str
    causal_component_id: str
    first_divergence: Any
    hard: bool
    priority: int
    open: bool = True


@dataclass(slots=True)
class FailureHistory(SerializableRecord):
    failure_signature: str
    # Each entry is a content-addressed failed edit record.  Never store the
    # generator's free-text mechanism as the identity of a failed approach.
    mechanism_failures: list[dict[str, Any]] = field(default_factory=list)
    counterexample_ids: list[str] = field(default_factory=list)
    closed: bool = False


@dataclass(slots=True)
class ObservationBundle(SerializableRecord):
    by_challenge: dict[str, PairedTraceBundle] = field(default_factory=dict)
    by_requirement: dict[str, list[str]] = field(default_factory=dict)

    def record(self, execution: PairedTraceBundle, requirement_id: str) -> None:
        self.by_challenge[execution.challenge_id] = execution
        target = self.by_requirement.setdefault(requirement_id, [])
        if execution.paired_bundle_id not in target:
            target.append(execution.paired_bundle_id)

    def retain_patch(self, patch_hash: str) -> None:
        retained = {
            challenge_id: execution
            for challenge_id, execution in self.by_challenge.items()
            if execution.patch_hash == patch_hash
        }
        retained_bundle_ids = {
            execution.paired_bundle_id for execution in retained.values()
        }
        self.by_challenge = retained
        self.by_requirement = {
            requirement_id: [
                bundle_id for bundle_id in bundle_ids
                if bundle_id in retained_bundle_ids
            ]
            for requirement_id, bundle_ids in self.by_requirement.items()
            if any(bundle_id in retained_bundle_ids for bundle_id in bundle_ids)
        }


@dataclass(slots=True)
class LockedCheckSet(SerializableRecord):
    target_ids: set[str] = field(default_factory=set)
    preservation_ids: set[str] = field(default_factory=set)

    def all_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.target_ids | self.preservation_ids))


def failure_signature(observation: RunObservation) -> str:
    return content_hash({
        "status": observation.status,
        "return_code": observation.return_code,
        "stdout_tail": observation.stdout[-1000:],
        "stderr_tail": observation.stderr[-1000:],
        "exception": observation.exception,
    })


def public_evidence_from_instance(
    issue: str,
    visible_tests: tuple[str, ...],
    metadata: dict[str, Any],
    repository: Path,
) -> PublicEvidence:
    issue_evidence_id = stable_id("evidence", "issue", issue)
    witnesses = extract_issue_witnesses(issue, issue_evidence_id)
    records = [EvidenceRecord(
        evidence_id=issue_evidence_id,
        source="issue",
        authority="B",
        content=issue,
        executable=False,
        metadata={
            "requirement_proposals": tuple(
                item for item in metadata.get("requirement_proposals", ())
                if isinstance(item, dict)
            ),
            "issue_witnesses": witnesses,
        },
    )]
    checks: list[ExecutableCheck] = []
    raw_checks = metadata.get("public_checks", ())
    for index, raw in enumerate(raw_checks):
        if isinstance(raw, str):
            command = tuple(raw.split())
            values: dict[str, Any] = {}
        else:
            values = dict(raw)
            command = tuple(str(item) for item in values.get("command", ()))
        if not command:
            continue
        check_id = str(values.get("check_id") or stable_id("public-check", index, command))
        expected = values.get("expected")
        if isinstance(expected, dict):
            expected = ObservationContract(
                relation=str(expected.get("relation", f"{check_id} must pass")),
                expected=expected.get("expected", {"exit_code": 0}),
                observable=str(expected.get("observable", "return")),
                comparator=str(expected.get("comparator", "equals")),
            )
        elif expected is not None:
            expected = ObservationContract(
                relation=f"{check_id} must equal its public expected value",
                expected=expected,
            )
        checks.append(ExecutableCheck(
            check_id=check_id,
            command=command,
            role=str(values.get("role", "TARGET")),
            authority=str(values.get("authority", "A")),
            requirement_ids=tuple(values.get("requirement_ids", ())),
            symbol_references=tuple(values.get("symbol_references", ())),
            cwd=str(values.get("cwd", ".")),
            environment=tuple(sorted(
                (str(key), str(value))
                for key, value in dict(values.get("environment", {})).items()
            )),
            timeout_seconds=float(values.get("timeout_seconds", 60.0)),
            expected=expected,
            concrete_input=values.get("concrete_input"),
            source_evidence_ids=(stable_id("evidence", "public-check", check_id),),
        ))
    for index, test_spec in enumerate(visible_tests):
        path = repository / test_spec.split("::", 1)[0]
        symbols: tuple[str, ...] = ()
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            symbols = tuple(sorted(set(
                token for token in content.replace("(", " ").replace(".", " ").split()
                if token.isidentifier()
            )))
        check_id = stable_id("public-check", "visible", test_spec)
        checks.append(ExecutableCheck(
            check_id=check_id,
            command=("python", "-m", "pytest", "-q", test_spec),
            role="TARGET",
            authority="A",
            symbol_references=symbols,
            source_evidence_ids=(stable_id("evidence", "visible-test", index, test_spec),),
        ))
    contracts = [EvidenceRecord(
        evidence_id=str(raw.get("evidence_id") or stable_id("api-contract", raw)),
        source=str(raw.get("source", "public_api")),
        authority=str(raw.get("authority", "B")),
        content=str(raw.get("content", "")),
        executable=bool(raw.get("executable", False)),
        metadata=dict(raw.get("metadata", {})),
    ) for raw in metadata.get("api_contracts", ()) if isinstance(raw, dict)]

    issue_record = records[0]
    identifiers = tuple(dict.fromkeys(
        re.findall(r"`([A-Za-z_]\w*)`", issue)
        + [
            value.split(".")[-1]
            for value in re.findall(
                r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\s*(?=\(|\b)", issue,
            )
        ]
        + [
            str(item["operation"])
            for item in issue_witnesses(issue_record)
        ]
        + [symbol for check in checks for symbol in check.symbol_references]
    ))[:40]
    candidate_paths = {
        str(item).replace("\\", "/").removeprefix("./")
        for item in metadata.get("public_source_paths", ())
        if isinstance(item, str)
    }
    candidate_paths.update(
        match.replace("\\", "/").removeprefix("./")
        for match in re.findall(r"(?:[A-Za-z_]\w*/)*[A-Za-z_]\w*\.py", issue)
    )
    candidate_paths.update(
        argument.replace("\\", "/").removeprefix("./")
        for check in checks for argument in check.command
        if argument.endswith(".py")
    )
    candidate_paths.update(
        test_spec.split("::", 1)[0].replace("\\", "/").removeprefix("./")
        for test_spec in visible_tests
    )
    if identifiers:
        searched = 0
        for path in sorted(repository.rglob("*.py")):
            relative = path.relative_to(repository)
            if any(part in {
                ".git", ".venv", "venv", "node_modules", "build",
                "dist", "__pycache__",
            } for part in relative.parts):
                continue
            searched += 1
            if searched > 800 or len(candidate_paths) >= 40:
                break
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(re.search(
                rf"\b(?:def|class)\s+{re.escape(identifier)}\b", text,
            ) for identifier in identifiers):
                candidate_paths.add(relative.as_posix())
    for relative_name in sorted(candidate_paths):
        path = repository / relative_name
        if not path.is_file() or path.suffix != ".py":
            continue
        relative = path.relative_to(repository)
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=relative.as_posix())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if (
                not identifiers
                or node.name.startswith("_")
                or node.name not in identifiers
            ):
                continue
            docstring = ast.get_docstring(node, clean=True) or ""
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                arguments = ", ".join(ast.unparse(item) for item in node.args.args)
                returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                signature = f"{node.name}({arguments}){returns}"
            else:
                signature = f"class {node.name}"
            content = f"`{node.name}` public signature is {signature}."
            if docstring:
                content += f" Public docstring: {docstring}"
            contracts.append(EvidenceRecord(
                evidence_id=stable_id(
                    "public-contract", relative.as_posix(), node.name,
                    getattr(node, "lineno", 0), content,
                ),
                source=f"source:{relative.as_posix()}:{getattr(node, 'lineno', 0)}",
                authority="B",
                content=content,
                executable=False,
                metadata={"symbol": node.name, "kind": "docstring_and_type_signature"},
            ))
    documentation_paths = [repository / "README.md", repository / "README.rst"]
    documentation_paths.extend(sorted((repository / "docs").glob("*.md"))[:20] if (repository / "docs").is_dir() else ())
    for path in documentation_paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if not identifiers or not any(identifier in text for identifier in identifiers):
            continue
        contracts.append(EvidenceRecord(
            evidence_id=stable_id("public-documentation", path.relative_to(repository).as_posix(), text),
            source=f"documentation:{path.relative_to(repository).as_posix()}",
            authority="B",
            content=text[:12000],
            executable=False,
            metadata={"kind": "public_documentation"},
        ))
    return PublicEvidence(tuple(records), tuple(checks), tuple(contracts), ())
