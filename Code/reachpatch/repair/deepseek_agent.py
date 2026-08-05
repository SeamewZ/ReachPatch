from __future__ import annotations

import ast
import json
import random
import re
import subprocess
import threading
import textwrap
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from reachpatch.models.base import SerializableRecord, content_hash, stable_id
from reachpatch.repair.context import (
    InitialRepairPacket,
    RequirementChecklist,
    assess_first_patch_readiness,
    build_initial_repair_packet,
    build_repair_context,
    build_revision_packet,
)
from reachpatch.repair.tools import ProposedEdit, RepairToolExecutor


SYSTEM_PROMPT = """You are the Repair Player maintaining one persistent working patch.
Preserve previously validated edits. Use repository tools to inspect only relevant code.
The primary issue is normative. Public discussion is provisional context only: use it
for witnesses or mechanisms, never as a new requirement when it differs from the issue.
You may make multiple coordinated edits when they implement one repair mechanism.
For apply_edits, choose exactly one registered mechanism value from the tool schema;
put a human-readable explanation in finish_revision instead of inventing a mechanism name.
Use the smallest sufficient edit ranges. expected_source must be copied exactly from a
read_file result; never reconstruct it from memory or replace an entire class when a
few statements implement the repair.
Do not claim success from reasoning alone. After editing, request executable public checks.
An apply_edits call starts an uncheckpointed edit set. Review the actual staged diff
before finishing. If review finds an unnecessary or incorrect earlier edit, call
replace_staged_edits with the complete corrected edit set; do not append a compensating
edit or merely describe a revert in the summary.
When evidence is insufficient, request targeted context or declare a concrete blocker.
Never access hidden tests, gold patches, test_patch, or harness outcomes."""

INITIAL_REPAIR_INSTRUCTION = """Repair the complete issue, not only its examples.
Before editing, read the most likely target definition, at least one direct caller when
one exists, and a related public test or public contract. Identify the root cause, list
every independent requirement, and identify behavior that must remain compatible.
The packet may include bounded discussion evidence ordered strongest-first. Later
corrections, explicit source paths, and causal explanations outrank early speculative
questions, but remain hypotheses: verify the mechanism in checked-out source and test the complete
input/state/exception boundary, and reject a suggestion that conflicts with callers or
existing protocol behavior. In particular, do not narrow a multi-variable or sibling
branch path merely because one example is univariate, empty, or otherwise special.
Use candidate symbols as search seeds, including the named base class and its shared
protocol methods; do not assume that the first subclass or file returned by search is
the owner of the public behavior.
For APIs that update an include/exclude or deferred/immediate state, trace empty and
non-empty states explicitly and require a chained call to agree with the equivalent
single batched call. When ``existing - incoming`` becomes empty, also compute
``incoming - existing``: switch mode only with that normalized incoming-only residual,
normalizing an incoming iterable with ``set`` or ``frozenset`` before using set-only
operations, and keep the neutral state when both residuals are empty. Do not restore the raw
incoming values. Before changing a consumer's empty-state guard, inspect every bounded
producer (including no-argument/reset paths) that can emit the same payload and tag.
Implement a minimal root-cause repair without changing tests or hard-coding witnesses.
Every edit must change an execution path reachable from an existing entry point. If a
new helper is necessary, wire it into that path in the same edit set; never answer a
no-op or source-anchor rejection by appending an uncalled helper, a duplicate function,
or an unchanged copy of the selected definition. If the selected function lacks the
configuration, original state, or invocation context needed by the requirement, inspect
the bounded caller that owns that information and change its existing decision or call;
do not add an alternate helper with an extra parameter that no caller supplies. When the
required behavior is local, edit the existing guard, expression, state write, or return
directly instead of placing a second implementation beside it.
After editing, review the complete diff against requirements, callers, protocol paths,
exceptions, boundaries, and preservation behavior. Check that an edit extends the
existing class/function instead of shadowing it, and that defaults are not passed twice
through kwargs. Every changed file must have a distinct causal role; identical class or
method names in different modules are not evidence that both should change. Preserve
missing-key and None behavior, avoid mutating caller-owned objects unless ownership is
transferred, inspect reverse binary dispatch, and search sibling predicates/indexing
logic for the same boundary defect. Request a bounded public check when a related test
or contract exists."""

INITIAL_ROOT_RECOVERY_INSTRUCTION = """The preceding first-patch attempt did not
stage a reachable behavior change. This is the one bounded initial root-recovery
attempt, not a patch revision and not a separate candidate. Reuse the inspected source
and re-diagnose why the attempted mechanism left behavior unchanged. Stage one complete
minimal repair in the existing target execution path. Do not submit a no-op, append an
uncalled helper, duplicate a definition, or copy the original body under another name.
The recovery packet may contain ``rejected_staged_diff_do_not_reuse``. It is negative
review evidence, not the current patch: do not copy or resubmit it. Use its validation
error to choose the corrected owner and mechanism from the mandatory source slices.
If the attempted target lacks required state, move to the already-inspected bounded
caller that owns that state and modify the existing call path. If the behavior is local,
change the existing guard, expression, state write, or return in place. When the issue
already states operational steps such as record, compare, normalize, raise, or return,
map each step to statements in the existing function body; the replacement must visibly
change that body before any supporting definition is added."""

REVISION_REPAIR_INSTRUCTION = """Preserve behavior already satisfied by the current
working patch. Repair only the mechanism exposed by this one confirmed executable
failure. Before editing, explain why the concrete input follows the failing path,
identify the supplied causal cut, and identify behavior protected by the locked check
set. Continue editing the same working patch; do not create an independent candidate,
expand unrelated APIs, or repeat a prohibited mechanism. After editing, review the
counterexample, preservation contracts, and complete diff. The controller will execute
the identical LockedCheckSet on the before and after checkpoints and will reject any
revision without confirmed improvement. For a tagged or mode-bearing state transition,
trace both its producer and every bounded consumer. If a set/difference legitimately
becomes empty, preserve the normalized result. For a transition between complementary
include/exclude modes, compute both residuals: ``existing - incoming`` and
``set(incoming) - existing``. Normalize incoming iterables before calling set-only
methods. A mode switch may carry only the incoming-only residual; when
both are empty it must preserve the neutral behavior. Do not rebuild the just-removed
raw input. A consumer guard may change only after inspecting every bounded producer,
including no-argument/reset paths, and proving that the same empty payload plus tag has
no preservation meaning. Do not replace or duplicate consumer bodies or sibling mode
branches."""


_SELF_REJECTED_PATCH_PATTERNS = (
    re.compile(
        r"\b(?:patch|edit|fix|repair)\s+(?:is\s+)?(?:still\s+)?"
        r"(?:incomplete|incorrect|wrong|unrelated|insufficient)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdoes not (?:address|fix|resolve)\b", re.IGNORECASE),
    re.compile(r"\bmust be (?:replaced|corrected)\b", re.IGNORECASE),
    re.compile(r"\bno causal role\b", re.IGNORECASE),
    re.compile(r"\bnever (?:called|used|reached)\b", re.IGNORECASE),
)


def _finish_summary_rejects_patch(summary: str) -> bool:
    """Reject an explicit model admission that its staged patch is not a fix."""

    return any(pattern.search(summary) for pattern in _SELF_REJECTED_PATCH_PATTERNS)


_REVIEW_SOURCE_PATH = re.compile(
    r"(?:[A-Za-z_][\w.-]*/)+[A-Za-z_][\w.-]*\.py\b",
)
_REVIEW_DECISION_LANGUAGE = re.compile(
    r"\b(?:the\s+problem\s+is|was\s+correct|should\s+be|should\s+only|"
    r"can\s+be\s+avoided|first\s+thing|instead|resolved)\b",
    re.IGNORECASE,
)
_REVIEW_REJECTS_ANCHOR = re.compile(
    r"\b(?:reject|incorrect|wrong|conflict|outdated|not\s+causal|"
    r"does\s+not\s+own|is\s+not\s+the\s+owner)\w*\b",
    re.IGNORECASE,
)
_REVIEW_CALLER_OWNER_JUSTIFICATION = re.compile(
    r"\b(?:retain|preserv|keep)\w*\b[\s\S]{0,500}"
    r"\b(?:pass|construct|build|creat|receiv|consumer)\w*\b|"
    r"\b(?:pass|construct|build|creat|receiv|consumer)\w*\b[\s\S]{0,500}"
    r"\b(?:retain|preserv|keep)\w*\b",
    re.IGNORECASE,
)
_REVIEW_CALLER_OWNER_DIFF = re.compile(
    r"\bdef\s+(?:\w*formfield|\w*factory|\w*build\w*|\w*create\w*|"
    r"\w*construct\w*|to_\w+)\s*\(",
    re.IGNORECASE,
)


def _diff_behavior_lines(diff: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return added/removed executable-looking lines from a unified diff."""

    added: list[str] = []
    removed: list[str] = []
    for line in str(diff or "").splitlines():
        if line.startswith(("+++ ", "--- ", "@@ ", "diff --git ")):
            continue
        if not line.startswith(("+", "-")):
            continue
        value = line[1:].strip()
        if not value or value.startswith("#"):
            continue
        if line.startswith("+"):
            added.append(value)
        else:
            removed.append(value)
    return tuple(added), tuple(removed)


def _strongest_review_anchor_paths(packet) -> frozenset[str]:
    """Resolve the strongest source owner named by causal public discussion."""

    definitions = tuple(getattr(packet, "likely_definitions", ()) or ())
    decision_segments = tuple(
        str(segment)
        for segment in tuple(getattr(packet, "discussion_evidence", ()) or ())[:5]
        if _REVIEW_DECISION_LANGUAGE.search(str(segment))
        and _REVIEW_SOURCE_PATH.search(str(segment))
    )
    strongest_source_segment = decision_segments[0] if decision_segments else ""
    decision_anchors = tuple(dict.fromkeys(
        match.replace("\\", "/").lstrip("./")
        for match in _REVIEW_SOURCE_PATH.findall(strongest_source_segment)
    ))

    def suffix_component_score(relative: str, anchor: str) -> int:
        relative_parts = tuple(part for part in relative.split("/") if part)
        anchor_parts = tuple(part for part in anchor.split("/") if part)
        score = 0
        for left, right in zip(reversed(relative_parts), reversed(anchor_parts)):
            if left != right:
                break
            score += 1
        return score

    definition_anchor_scores = {
        str(item.get("relative_path", item.get("path", ""))).replace("\\", "/"):
        max((
            suffix_component_score(
                str(item.get("relative_path", item.get("path", ""))).replace(
                    "\\", "/",
                ),
                anchor,
            )
            for anchor in decision_anchors
        ), default=0)
        for item in definitions
    }
    strongest_anchor_score = max(definition_anchor_scores.values(), default=0)
    return frozenset(
        relative for relative, score in definition_anchor_scores.items()
        if score == strongest_anchor_score and score >= 2
    )


def _prune_rejected_alternate_layer_edits(
    packet,
    tools: RepairToolExecutor,
    quality_error: str | None,
) -> dict | None:
    """Mechanically apply a precise causal-anchor review instruction.

    This is intentionally limited to the review rule that already proved the
    patch contains an executable edit in the strongest source owner plus an
    edit to a same-named definition in another layer.  It never invents code;
    it transactionally removes only the alternate-layer edits and retains the
    model-produced edits in the evidenced owner.
    """

    if not str(quality_error or "").startswith(
        "STAGED_PATCH_EXPANDS_BEYOND_CAUSAL_SOURCE_ANCHOR"
    ):
        return None
    anchored_definitions = _strongest_review_anchor_paths(packet)
    if not anchored_definitions:
        return None
    definitions = tuple(getattr(packet, "likely_definitions", ()) or ())
    anchored_symbols = {
        str(item.get("symbol", item.get("qualified_name", ""))).rsplit(".", 1)[-1]
        for item in definitions
        if str(item.get("relative_path", item.get("path", ""))).replace(
            "\\", "/",
        ) in anchored_definitions
    }
    alternate_paths = {
        str(item.get("relative_path", item.get("path", ""))).replace("\\", "/")
        for item in definitions
        if str(item.get("relative_path", item.get("path", ""))).replace(
            "\\", "/",
        ) not in anchored_definitions
        and str(item.get("symbol", item.get("qualified_name", ""))).rsplit(
            ".", 1,
        )[-1] in anchored_symbols
    }
    retained = tuple(
        edit for edit in tools.staged_edits
        if edit.relative_path.replace("\\", "/") not in alternate_paths
    )
    removed = tuple(
        edit.relative_path for edit in tools.staged_edits
        if edit.relative_path.replace("\\", "/") in alternate_paths
    )
    if not retained or not removed or len(retained) == len(tools.staged_edits):
        return None
    result = tools.replace_staged_edits(retained)
    return {
        **result,
        "automatic_correction": "REMOVE_REJECTED_ALTERNATE_LAYER_EDITS",
        "anchored_paths": sorted(anchored_definitions),
        "removed_paths": sorted(set(removed)),
    }


def _observed_causal_delegate_error(
    packet,
    staged_diff: str,
    conversation,
) -> str | None:
    """Require an observed public operation to edit its state-writing delegate.

    The relation is recovered only from source the Generator actually read. It
    is therefore a mechanical caller-to-owner check, not a lexical guess over
    the repository or a project-specific rule.
    """

    if conversation is None:
        return None
    title = str(getattr(packet, "issue_text", "")).splitlines()[0]
    primary_operations = tuple(dict.fromkeys(
        match.rsplit(".", 1)[-1]
        for match in re.findall(
            r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\(\s*\)", title,
        )
    ))
    primary_operations = tuple(item[:-2].rsplit(".", 1)[-1] for item in primary_operations)
    if not primary_operations:
        return None

    functions: list[dict[str, Any]] = []
    for message in getattr(conversation, "messages", ()):
        if message.get("role") != "tool" or message.get("name") != "read_file":
            continue
        try:
            payload = json.loads(str(message.get("content", "{}")))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        content = payload.get("content") if isinstance(payload, dict) else None
        relative = str(payload.get("path", "")) if isinstance(payload, dict) else ""
        if not isinstance(content, str) or not content.strip() or not relative:
            continue
        lines = content.splitlines()
        base_line = int(payload.get("start_line", 1) or 1)
        definitions: list[tuple[int, int, str]] = []
        for index, line in enumerate(lines):
            match = re.match(
                r"^(\s*)(?:async\s+def|def)\s+([A-Za-z_]\w*)\s*\(", line,
            )
            if match:
                definitions.append((index, len(match.group(1)), match.group(2)))
        for position, (index, indentation, name) in enumerate(definitions):
            stop = len(lines)
            for next_index, next_indentation, _next_name in definitions[position + 1:]:
                if next_indentation <= indentation:
                    stop = next_index
                    break
            source = textwrap.dedent("\n".join(lines[index:stop]))
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            function = next((
                node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ), None)
            if function is None:
                continue
            calls: set[str] = set()
            state_write = False
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        calls.add(node.func.attr)
                    elif isinstance(node.func, ast.Name):
                        calls.add(node.func.id)
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = (
                        node.targets if isinstance(node, ast.Assign)
                        else (node.target,)
                    )
                    state_write = state_write or any(
                        isinstance(child, ast.Attribute)
                        and isinstance(child.value, ast.Name)
                        and child.value.id == "self"
                        for target in targets for child in ast.walk(target)
                    )
            functions.append({
                "name": name,
                "path": relative.replace("\\", "/"),
                "start": base_line + index,
                "end": base_line + stop - 1,
                "calls": calls,
                "state_write": state_write,
            })

    changed_lines: dict[str, set[int]] = {}
    current_path = ""
    old_line = new_line = 0
    for raw in str(staged_diff or "").splitlines():
        if raw.startswith("+++ b/"):
            current_path = raw[6:].strip().replace("\\", "/")
            changed_lines.setdefault(current_path, set())
            continue
        if raw.startswith("@@"):
            match = re.search(
                r"@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)", raw,
            )
            if match:
                old_line, new_line = map(int, match.groups())
            continue
        if not current_path:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            changed_lines[current_path].add(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            changed_lines[current_path].add(old_line)
            old_line += 1
        elif raw.startswith(" "):
            old_line += 1
            new_line += 1

    for operation in primary_operations:
        public_functions = [item for item in functions if item["name"] == operation]
        callees = {
            callee
            for item in public_functions
            for callee in item["calls"]
            if operation.lower() in callee.lower() and callee != operation
        }
        if not callees:
            continue
        ranked = sorted(
            callees,
            key=lambda name: (
                not name.lower().startswith(f"add_{operation.lower()}"),
                not name.lower().startswith(f"set_{operation.lower()}"),
                len(name),
                name,
            ),
        )
        owners = [
            item for item in functions
            if item["name"] == ranked[0] and item["state_write"]
        ]
        if not owners:
            continue
        if any(
            any(owner["start"] <= line <= owner["end"] for line in changed_lines.get(
                owner["path"], (),
            ))
            for owner in owners
        ):
            continue
        owner_labels = [
            f"{owner['path']}:{owner['start']}:{owner['name']}" for owner in owners
        ]
        return (
            "STAGED_PATCH_MISSES_OBSERVED_STATE_OWNER: the primary public operation "
            f"{operation}() was observed calling the state-writing delegate "
            f"{ranked[0]}(), but the diff does not touch its read source range "
            f"{owner_labels!r}. Replace the edit with the causal delegate's state "
            "transition; a sibling operation cannot repair this call path"
        )
    return None


def _initial_patch_review_error(
    packet,
    staged_diff: str,
    summary: str,
    conversation=None,
) -> str | None:
    """Mechanically reject an initial patch whose claimed mechanism is absent.

    This is deliberately narrower than correctness prediction.  It catches
    only cases where the diff cannot perform the behavior claimed in the
    issue/summary: no executable change, an ignored source-anchored correction,
    input normalization without a state transition, or a terminal pass-through
    added after unchanged validation failures.
    """

    added, removed = _diff_behavior_lines(staged_diff)
    if not added and not removed:
        return (
            "STAGED_PATCH_NO_EXECUTABLE_CHANGE: the unified diff changes only "
            "comments or formatting; replace it with a reachable behavior change"
        )

    def added_dead_store() -> tuple[str, str] | None:
        """Find an added assignment immediately overwritten in the same block."""

        def assignment_for(source: str) -> tuple[str, ast.AST] | None:
            try:
                body = ast.parse(source.strip()).body
            except SyntaxError:
                return None
            if len(body) != 1 or not isinstance(body[0], (ast.Assign, ast.AnnAssign)):
                return None
            statement = body[0]
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else (statement.target,)
            )
            if len(targets) != 1 or statement.value is None:
                return None
            return (
                ast.unparse(targets[0]),
                statement.value,
            )

        def reads_target(value: ast.AST, target: str) -> bool:
            return any(
                isinstance(node, (ast.Name, ast.Attribute))
                and ast.unparse(node) == target
                for node in ast.walk(value)
            )

        hunks: list[list[tuple[str, str]]] = []
        hunk_lines: list[tuple[str, str]] | None = None
        for raw in staged_diff.splitlines():
            if raw.startswith("@@"):
                if hunk_lines is not None:
                    hunks.append(hunk_lines)
                hunk_lines = []
                continue
            if hunk_lines is None or raw.startswith(("---", "+++")) or not raw:
                continue
            marker = raw[0]
            if marker not in {"+", "-", " "}:
                continue
            hunk_lines.append((marker, raw[1:]))
        if hunk_lines is not None:
            hunks.append(hunk_lines)

        for hunk in hunks:
            for index, (marker, source) in enumerate(hunk):
                if marker != "+":
                    continue
                current = assignment_for(source)
                if current is None:
                    continue
                target, _value = current
                indentation = len(source) - len(source.lstrip())
                for later_marker, later_source in hunk[index + 1:]:
                    if later_marker == "-":
                        continue
                    stripped = later_source.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    later_indentation = len(later_source) - len(
                        later_source.lstrip()
                    )
                    if later_indentation != indentation:
                        break
                    later = assignment_for(later_source)
                    if (
                        later is not None
                        and later[0] == target
                        and not reads_target(later[1], target)
                    ):
                        return source.strip(), later_source.strip()
                    break

            # An assignment in a newly added branch is equally dead when the
            # first executable statement after that branch unconditionally
            # overwrites the same target. Walk backward through only added
            # lines so unchanged intervening behavior cannot be misclassified.
            for index, (marker, source) in enumerate(hunk):
                if marker == "-":
                    continue
                later = assignment_for(source)
                if later is None:
                    continue
                target, value = later
                if reads_target(value, target):
                    continue
                indentation = len(source) - len(source.lstrip())
                for previous_marker, previous_source in reversed(hunk[:index]):
                    if previous_marker == "-":
                        continue
                    stripped = previous_source.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    previous_indentation = len(previous_source) - len(
                        previous_source.lstrip()
                    )
                    if previous_indentation < indentation:
                        break
                    if previous_marker != "+":
                        break
                    previous = assignment_for(previous_source)
                    if previous is not None and previous[0] == target:
                        return previous_source.strip(), source.strip()
        return None

    overwritten = added_dead_store()
    if overwritten is not None:
        first, second = overwritten
        return (
            "STAGED_PATCH_ADDS_IMMEDIATELY_OVERWRITTEN_STATE_WRITE: the added "
            f"assignment {first!r} is overwritten by {second!r} in the same block "
            "before any read or branch can observe it. Replace the existing causal "
            "assignment or branch; do not insert a dead store beside it"
        )

    import_statement = re.compile(
        r"^(?:from\s+[A-Za-z_][\w.]*\s+import\s+|import\s+)"
    )
    if added and all(import_statement.match(line) for line in (*added, *removed)):
        return (
            "STAGED_PATCH_IMPORT_ONLY_WITHOUT_REACHABLE_BEHAVIOR: imports can "
            "support a repair but cannot implement the issue by themselves. "
            "Replace the complete edit set with a change to the existing reachable "
            "method, guard, state write, exception path, or return; include the "
            "import only when that behavior change uses it"
        )

    def reversed_explicit_branch_relation() -> tuple[str, str] | None:
        """Compare a concrete summary predicate with added ternary behavior.

        Final review summaries are evidence only when they describe the actual
        staged diff.  For a mechanically explicit relation such as "when X starts
        with '/' call helper()", a ternary that calls the helper only in the else
        branch is the exact opposite behavior.  Restrict this check to literal
        ``startswith`` predicates and call names present in both the summary and
        exactly one branch, avoiding broad natural-language correctness guesses.
        """

        lowered_summary = str(summary or "").lower()

        def call_names(node: ast.AST) -> set[str]:
            names: set[str] = set()
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                if isinstance(child.func, ast.Name):
                    names.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    names.add(child.func.attr)
            return names

        for line in added:
            source = line.strip()
            expression = ""
            if source.startswith("return "):
                expression = source[len("return "):]
            elif "=" in source and not source.startswith(("if ", "elif ")):
                expression = source.split("=", 1)[1].strip()
            if " if " not in expression or " else " not in expression:
                continue
            try:
                parsed = ast.parse(expression, mode="eval").body
            except SyntaxError:
                continue
            if not isinstance(parsed, ast.IfExp):
                continue

            test = parsed.test
            positive_branch = parsed.body
            negative_branch = parsed.orelse
            if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                test = test.operand
                positive_branch, negative_branch = negative_branch, positive_branch
            if not (
                isinstance(test, ast.Call)
                and isinstance(test.func, ast.Attribute)
                and test.func.attr == "startswith"
                and test.args
                and isinstance(test.args[0], ast.Constant)
                and isinstance(test.args[0].value, str)
            ):
                continue
            literal = test.args[0].value
            positive_calls = call_names(positive_branch)
            negative_calls = call_names(negative_branch)
            distinctive_negative_calls = negative_calls - positive_calls
            if not distinctive_negative_calls:
                continue

            predicate_positions = [
                position
                for marker in ("starts with", "starting with", "startswith")
                for position in [lowered_summary.find(marker)]
                if position >= 0
            ]
            for position in predicate_positions:
                window = lowered_summary[
                    max(0, position - 220): position + 420
                ]
                if literal.lower() not in window or not re.search(
                    r"\b(?:when|if)\b", window,
                ):
                    continue
                for call_name in sorted(distinctive_negative_calls):
                    if re.search(
                        rf"\b{re.escape(call_name.lower())}\b", window,
                    ):
                        return literal, call_name
        return None

    reversed_relation = reversed_explicit_branch_relation()
    if reversed_relation is not None:
        literal, call_name = reversed_relation
        return (
            "STAGED_PATCH_REVERSES_EXPLICIT_BRANCH_RELATION: the final review "
            f"says {call_name}() is used when the value starts with {literal!r}, "
            "but the staged conditional calls it only in the opposite branch. "
            "Replace the complete edit set so the claimed condition and actual "
            "execution branch agree; preserve the opposite branch unchanged"
        )

    issue_requirement_text = "\n".join((
        str(getattr(packet, "issue_text", "")),
        *map(str, getattr(
            getattr(packet, "requirement_checklist", None),
            "change_requirements", (),
        ) or ()),
    )).lower()
    if (
        re.search(r"\bprepend\w*\b", issue_requirement_text)
        and re.search(r"\bprefix\w*\b", issue_requirement_text)
    ):
        for line in added:
            try:
                tree = ast.parse(textwrap.dedent(line))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
                    continue
                calls = [
                    child for value in node.values for child in ast.walk(value)
                    if isinstance(child, ast.Call)
                ]
                prefix_calls = [
                    call for call in calls
                    if (
                        isinstance(call.func, ast.Name)
                        and "prefix" in call.func.id.lower()
                    ) or (
                        isinstance(call.func, ast.Attribute)
                        and "prefix" in call.func.attr.lower()
                    )
                ]
                if prefix_calls:
                    return (
                        "STAGED_PATCH_USES_REQUIRED_PREFIX_ONLY_AS_FALLBACK: the "
                        "issue requires prepending a dynamic prefix to an existing "
                        "value, but the staged BoolOp returns the prefix only when "
                        "the existing value is false. Compose the prefix with the "
                        "relative value on the required branch and preserve absolute "
                        "or already-prefixed values"
                    )
        # Apply the same direction check to a multiline ``if`` block. A
        # relative/absolute branch is easy to invert when a model turns a
        # ternary into statements; inspect indentation to determine which
        # branch contains the prefix call.
        added_lines = [line for line in str(staged_diff or "").splitlines()
                       if line.startswith("+") and not line.startswith("+++")]
        for index, line in enumerate(added_lines):
            condition = re.match(
                r"^(?P<indent>\s*)if\s+.+\.startswith\(\s*(['\"])(?P<literal>[^'\"]+)\2\s*\)\s*:",
                line[1:],
            )
            if condition is None:
                continue
            indent = len(condition.group("indent"))
            branch_lines: list[str] = []
            later_lines: list[str] = []
            closed = False
            for candidate in added_lines[index + 1:]:
                content = candidate[1:]
                if not content.strip():
                    continue
                content_indent = len(content) - len(content.lstrip())
                if not closed and content_indent > indent:
                    branch_lines.append(content)
                    continue
                closed = True
                later_lines.append(content)
            branch_has_prefix = any(
                "prefix" in value.lower() for value in branch_lines
            )
            later_has_prefix = any(
                "prefix" in value.lower() for value in later_lines
            )
            if not branch_has_prefix and later_has_prefix:
                literal = condition.group("literal")
                summary_window = str(summary or "").lower()
                if (
                    literal.lower() in summary_window
                    and re.search(r"\b(?:when|if|relative)\b", summary_window)
                    and re.search(r"\b(?:prefix|prepend)\w*\b", summary_window)
                ):
                    return (
                        "STAGED_PATCH_REVERSES_EXPLICIT_BRANCH_RELATION: the final "
                        f"review describes the {literal!r} branch as requiring the "
                        "prefix, but the staged multiline conditional applies the "
                        "prefix only after that branch. Replace the complete edit "
                        "set so the condition and execution branch agree"
                    )

    changed_paths = {
        line[6:].strip()
        for line in str(staged_diff or "").splitlines()
        if line.startswith("+++ b/")
    } | {
        line[6:].strip()
        for line in str(staged_diff or "").splitlines()
        if line.startswith("--- a/")
    }
    definitions = tuple(getattr(packet, "likely_definitions", ()) or ())
    issue_text = str(getattr(packet, "issue_text", ""))

    def definition_path(item) -> str:
        return str(item.get("relative_path", item.get("path", ""))).replace(
            "\\", "/",
        )

    def definition_symbol(item) -> str:
        return str(item.get("symbol", item.get("qualified_name", ""))).rsplit(
            ".", 1,
        )[-1]

    def is_shared_utility_path(path: str) -> bool:
        parts = tuple(part.lower() for part in Path(path).parts)
        stem = Path(path).stem.lower()
        return bool(
            set(parts) & {
                "utils", "util", "common", "shared", "helpers", "validators",
            }
            or stem in {
                "utils", "util", "common", "shared", "helpers", "validation",
                "validators",
            }
        )

    def issue_explicitly_anchors_path(path: str) -> bool:
        """Recognize an exact source owner named by the public issue.

        A traceback is useful localization evidence even when the repository
        index ranks a downstream exception class ahead of the frame that owns
        the bad behavior. A module named ``base.py`` or ``validators.py`` is
        not, by its filename alone, an unjustified repository-wide expansion.
        """

        normalized = path.replace("\\", "/").lstrip("./")
        issue_normalized = issue_text.replace("\\", "/")
        if normalized and normalized in issue_normalized:
            return True
        dotted = (
            normalized[:-3].replace("/", ".")
            if normalized.endswith(".py") else ""
        )
        if dotted and re.search(
            rf"(?<![\w.]){re.escape(dotted)}(?![\w.])", issue_text,
        ):
            return True
        for item in definitions:
            if definition_path(item) != normalized:
                continue
            symbol = definition_symbol(item)
            if (
                symbol
                and not symbol.startswith("<")
                and re.search(rf"\b{re.escape(symbol)}\b", issue_text)
            ):
                return True
        return False

    # Initial generation has no execution-backed authority for widening a
    # component-scoped issue into a high-fanout shared helper.  Keep a shared
    # API editable when the issue explicitly names it; otherwise require the
    # local definition or its call site to own the repair.
    if definitions:
        primary = definitions[0]
        primary_path = definition_path(primary)
        primary_symbol = definition_symbol(primary)
        issue_lower = issue_text.lower()
        primary_tokens = {
            primary_symbol.lower(), Path(primary_path).stem.lower(),
        } - {"", "base", "utils", "util", "validation", "common"}
        component_scoped = any(
            re.search(rf"\b{re.escape(token)}\b", issue_lower)
            for token in primary_tokens
        )
        risky_shared_paths = {
            path for path in changed_paths
            if (
                is_shared_utility_path(path)
                and path != primary_path
                and not issue_explicitly_anchors_path(path)
            )
        }
        shared_api_explicitly_named = any(
            definition_path(item) in risky_shared_paths
            and definition_symbol(item)
            and re.search(
                rf"\b{re.escape(definition_symbol(item))}\b", issue_text,
                re.IGNORECASE,
            )
            for item in definitions
        )
        if (
            component_scoped
            and risky_shared_paths
            and primary_path not in changed_paths
            and not shared_api_explicitly_named
        ):
            return (
                "STAGED_PATCH_EXPANDS_SCOPED_FIX_INTO_SHARED_UTILITY: the issue and "
                f"highest-ranked definition scope the repair to {primary_path!r}, "
                f"but the diff changes shared utility path(s) "
                f"{sorted(risky_shared_paths)!r} without execution-backed authority. "
                "Repair the component's existing validation call/guard or explain an "
                "explicit issue-level contract for changing every utility caller"
            )

    # Discussion evidence is already ordered strongest-first.  Later segments
    # often quote the superseded proposal as history; unioning all paths would
    # incorrectly make both the correction and the rejected layer authoritative.
    anchored_definitions = _strongest_review_anchor_paths(packet)
    if anchored_definitions and not (changed_paths & anchored_definitions):
        anchored_symbols = {
            str(item.get("symbol", item.get("qualified_name", ""))).rsplit(".", 1)[-1]
            for item in definitions
            if str(item.get("relative_path", item.get("path", ""))).replace(
                "\\", "/",
            ) in anchored_definitions
        }
        changed_same_named_definition = any(
            str(item.get("relative_path", item.get("path", ""))).replace(
                "\\", "/",
            ) in changed_paths
            and str(item.get("symbol", item.get("qualified_name", ""))).rsplit(
                ".", 1,
            )[-1] in anchored_symbols
            for item in definitions
        )
        caller_owner_justified = bool(
            _REVIEW_CALLER_OWNER_JUSTIFICATION.search(summary)
            and _REVIEW_CALLER_OWNER_DIFF.search(str(staged_diff or ""))
        )
        if (
            changed_same_named_definition
            and not _REVIEW_REJECTS_ANCHOR.search(summary)
            and not caller_owner_justified
        ):
            return (
                "STAGED_PATCH_IGNORES_CAUSAL_SOURCE_ANCHOR: the strongest public "
                f"correction names {sorted(anchored_definitions)!r}, but the diff "
                "changes a same-named definition in another layer. Inspect the "
                "anchored consumer and replace the edit, or explicitly explain from "
                "source/callers why that correction conflicts with public behavior"
            )
    if anchored_definitions and (changed_paths & anchored_definitions):
        anchored_symbols = {
            str(item.get("symbol", item.get("qualified_name", ""))).rsplit(".", 1)[-1]
            for item in definitions
            if str(item.get("relative_path", item.get("path", ""))).replace(
                "\\", "/",
            ) in anchored_definitions
        }
        extra_same_named_definitions = {
            str(item.get("relative_path", item.get("path", ""))).replace("\\", "/")
            for item in definitions
            if str(item.get("relative_path", item.get("path", ""))).replace(
                "\\", "/",
            ) in changed_paths - anchored_definitions
            and str(item.get("symbol", item.get("qualified_name", ""))).rsplit(
                ".", 1,
            )[-1] in anchored_symbols
        }
        if extra_same_named_definitions:
            return (
                "STAGED_PATCH_EXPANDS_BEYOND_CAUSAL_SOURCE_ANCHOR: the diff already "
                f"changes the strongest anchored owner {sorted(anchored_definitions)!r} "
                "but also changes same-named definitions in "
                f"{sorted(extra_same_named_definitions)!r}. Remove the alternate-layer "
                "edits; identical names are not evidence that both own the behavior"
            )

    discussion_segments = tuple(
        map(str, getattr(packet, "discussion_evidence", ()) or ())
    )[:5]
    definition_source = "\n".join(
        str(item.get("content", "")) for item in definitions
    )

    def changed_old_lines_by_path() -> dict[str, set[int]]:
        changed: dict[str, set[int]] = {}
        current_path = ""
        old_line = 0
        for raw_line in str(staged_diff or "").splitlines():
            if raw_line.startswith("--- a/"):
                current_path = raw_line[6:].strip().replace("\\", "/")
                changed.setdefault(current_path, set())
                continue
            if raw_line.startswith("+++ b/"):
                continue
            if raw_line.startswith("@@"):
                match = re.search(r"@@\s+-(\d+)(?:,\d+)?\s+\+\d+", raw_line)
                if match is not None:
                    old_line = int(match.group(1))
                continue
            if not current_path or raw_line.startswith("diff --git "):
                continue
            if raw_line.startswith("-") and not raw_line.startswith("---"):
                changed[current_path].add(old_line)
                old_line += 1
            elif raw_line.startswith("+") and not raw_line.startswith("+++"):
                # Attribute an insertion to its original-source anchor. This
                # lets a new guard at the start of a method count as touching
                # that method without pretending later methods changed.
                changed[current_path].add(max(1, old_line))
            elif raw_line.startswith(" "):
                old_line += 1
        return changed

    def touched_source_methods() -> set[str]:
        changed = changed_old_lines_by_path()
        touched: set[str] = set()
        for item in definitions:
            path = definition_path(item)
            source = str(item.get("content", ""))
            if not path or not source or path not in changed:
                continue
            try:
                tree = ast.parse(textwrap.dedent(source), filename=path)
            except SyntaxError:
                continue
            snippet_start = int(item.get(
                "snippet_start_line", item.get("start_line", 1),
            ) or 1)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                start = snippet_start + int(node.lineno) - 1
                end = snippet_start + int(
                    getattr(node, "end_lineno", node.lineno)
                ) - 1
                if any(start <= line <= end for line in changed[path]):
                    touched.add(node.name)
        return touched

    # A public traceback which marks the exact line that directly raises the
    # reported exception is stronger than a speculative suggestion to add a
    # reflected method elsewhere. Require the staged diff to touch that frame.
    # Ordinary downstream traceback frames remain localization evidence only.
    direct_raise_line = re.compile(
        r"^\s*(?:--->?|>)\s*\d+\s+raise\s+\w+",
    )
    traceback_frame_patterns = (
        re.compile(
            r"(?P<path>[^\s\"']+\.py)\s+in\s+"
            r"(?P<symbol>[A-Za-z_]\w*)\s*\(",
        ),
        re.compile(
            r"File\s+[\"'](?P<path>[^\"']+\.py)[\"'],\s+line\s+\d+,\s+in\s+"
            r"(?P<symbol>[A-Za-z_]\w*)",
        ),
    )

    # Locate the frame which owns each arrow-marked direct ``raise``.  The
    # initial packet can legitimately have no statically ranked definitions
    # even after the generator read the target through tools.  Keeping the raw
    # public traceback anchor prevents that retrieval miss from silently
    # disabling the causal-owner gate.
    raw_direct_raise_anchors: set[tuple[str, str]] = set()
    direct_raise_sources: dict[tuple[str, str], set[str]] = {}
    issue_lines = issue_text.splitlines()
    for line_index, line in enumerate(issue_lines):
        if direct_raise_line.search(line) is None:
            continue
        for previous in reversed(issue_lines[:line_index]):
            frame_match = next(
                (
                    match
                    for pattern in traceback_frame_patterns
                    if (match := pattern.search(previous)) is not None
                ),
                None,
            )
            if frame_match is None:
                continue
            anchor = (
                frame_match.group("path").replace("\\", "/"),
                frame_match.group("symbol"),
            )
            raw_direct_raise_anchors.add(anchor)
            raise_match = re.search(r"\braise\s+.+$", line)
            if raise_match is not None:
                direct_raise_sources.setdefault(anchor, set()).add(
                    " ".join(raise_match.group(0).split())
                )
            break

    direct_raise_traceback = bool(raw_direct_raise_anchors)
    traceback_anchors: set[tuple[str, str]] = set()
    if direct_raise_traceback:
        for traceback_path, symbol in raw_direct_raise_anchors:
            for definition in definitions:
                path = definition_path(definition)
                definition_name = definition_symbol(definition)
                if (
                    path
                    and traceback_path.endswith(path)
                    and definition_name == symbol
                ):
                    traceback_anchors.add((path, symbol))

        # Definition ranking is advisory.  When it misses the public traceback
        # owner entirely, still require the diff to change that source path.
        # A path-only fallback is intentionally limited to an arrow-marked
        # direct ``raise``; ordinary traceback frames do not activate it.
        missing_raw_owner_paths = tuple(sorted(
            traceback_path
            for traceback_path, _symbol in raw_direct_raise_anchors
            if not any(
                traceback_path.endswith(changed_path)
                for changed_path in changed_paths
            )
        ))
        if missing_raw_owner_paths:
            return (
                "STAGED_PATCH_OMITS_DIRECT_FAILURE_FRAME: the public traceback "
                "marks a direct exception-raising source path "
                f"{list(missing_raw_owner_paths)!r}, but the reviewed diff changes "
                f"only {sorted(changed_paths)!r}. Repair the guard/dispatch at the "
                "marked owner; changing a neighboring reflected or wrapper layer "
                "cannot prevent the unchanged direct raise"
            )

        def visible_changed_hunk_symbols(traceback_path: str) -> set[str]:
            """Return method names mechanically visible in changed owner hunks.

            Unified diffs normally retain the owning ``def`` in context for edits
            near a method entry.  Use that explicit evidence to distinguish an edit
            to the direct traceback owner from a neighboring method in the same
            file.  If no method header is visible, return an empty set and leave the
            decision to source-backed definition ranges rather than guessing.
            """

            current_path = ""
            hunk_symbols: set[str] = set()
            changed = False
            visible: set[str] = set()

            def commit_hunk() -> None:
                if (
                    current_path
                    and traceback_path.endswith(current_path)
                    and changed
                ):
                    visible.update(hunk_symbols)

            for raw_line in str(staged_diff or "").splitlines():
                if raw_line.startswith("--- a/"):
                    commit_hunk()
                    current_path = raw_line[6:].strip().replace("\\", "/")
                    hunk_symbols = set()
                    changed = False
                    continue
                if raw_line.startswith("@@"):
                    commit_hunk()
                    hunk_symbols = set(re.findall(
                        r"\b(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(",
                        raw_line,
                    ))
                    changed = False
                    continue
                if not current_path or raw_line.startswith(("+++", "diff --git ")):
                    continue
                if raw_line[:1] in {" ", "+", "-"}:
                    hunk_symbols.update(re.findall(
                        r"\b(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(",
                        raw_line[1:],
                    ))
                    if raw_line.startswith(("+", "-")):
                        changed = True
            commit_hunk()
            return visible

        wrong_visible_symbols: list[tuple[str, str, tuple[str, ...]]] = []
        for traceback_path, symbol in sorted(raw_direct_raise_anchors):
            visible_symbols = visible_changed_hunk_symbols(traceback_path)
            if visible_symbols and symbol not in visible_symbols:
                wrong_visible_symbols.append((
                    traceback_path, symbol, tuple(sorted(visible_symbols)),
                ))
        if wrong_visible_symbols:
            return (
                "STAGED_PATCH_OMITS_DIRECT_FAILURE_SYMBOL: the public traceback "
                "marks a direct exception-raising method, but the changed hunk(s) "
                "in the same source file visibly belong to neighboring methods: "
                f"{wrong_visible_symbols!r}. Modify the marked method's rejecting "
                "guard or direct raise; returning NotImplemented or adding reverse "
                "dispatch in a sibling method does not change the failing path"
            )

        def duplicates_direct_raise(
            traceback_path: str,
            raise_sources: set[str],
        ) -> bool:
            current_path = ""
            added_sources: set[str] = set()
            retained_sources: set[str] = set()
            for raw_line in str(staged_diff or "").splitlines():
                if raw_line.startswith("--- a/"):
                    current_path = raw_line[6:].strip().replace("\\", "/")
                    continue
                if not current_path or not traceback_path.endswith(current_path):
                    continue
                if raw_line.startswith(("+++", "@@", "diff --git ")):
                    continue
                if raw_line[:1] not in {" ", "+"}:
                    continue
                normalized = " ".join(raw_line[1:].strip().split())
                if normalized not in raise_sources:
                    continue
                if raw_line.startswith("+"):
                    added_sources.add(normalized)
                else:
                    retained_sources.add(normalized)
            return bool(added_sources & retained_sources)

        duplicated_failure_owners = tuple(sorted(
            (traceback_path, symbol)
            for (traceback_path, symbol), sources in direct_raise_sources.items()
            if sources and duplicates_direct_raise(traceback_path, sources)
        ))
        if duplicated_failure_owners:
            return (
                "STAGED_PATCH_DUPLICATES_DIRECT_FAILURE_BEFORE_CORRECTION: the "
                "reviewed hunk retains the public traceback's direct raise and also "
                f"adds another copy for {list(duplicated_failure_owners)!r}. Replace "
                "the original governing guard with the compatibility/capability "
                "predicate; do not insert a duplicate exception before the new guard"
            )

        def unchanged_direct_raise_has_unchanged_dominator(
            traceback_path: str,
            raise_sources: set[str],
        ) -> bool:
            """Detect an edit inserted only after an unchanged direct failure.

            Merely touching the owning method is insufficient when its original
            guard still reaches the exact arrow-marked ``raise`` before any added
            behavior.  Inspect the unified hunk and require the closest governing
            statement at the raise indentation (or less) to be edited.  A normal
            guard replacement therefore passes, while appending duplicate logic or
            a reflected method after the raise does not.
            """

            current_path = ""
            hunk: list[tuple[str, str]] = []

            def hunk_is_unfixed(lines: list[tuple[str, str]]) -> bool:
                for index, (marker, source) in enumerate(lines):
                    normalized = " ".join(source.strip().split())
                    if marker != " " or normalized not in raise_sources:
                        continue
                    raise_indent = len(source) - len(source.lstrip())
                    for previous_marker, previous_source in reversed(lines[:index]):
                        if not previous_source.strip():
                            continue
                        previous_indent = len(previous_source) - len(
                            previous_source.lstrip()
                        )
                        if previous_indent > raise_indent:
                            continue
                        return previous_marker == " "
                    # The direct raise itself is unchanged and no preceding edit
                    # in the hunk can alter whether execution reaches it.
                    return True
                return False

            for raw_line in str(staged_diff or "").splitlines():
                if raw_line.startswith("--- a/"):
                    if (
                        current_path
                        and traceback_path.endswith(current_path)
                        and hunk_is_unfixed(hunk)
                    ):
                        return True
                    current_path = raw_line[6:].strip().replace("\\", "/")
                    hunk = []
                    continue
                if raw_line.startswith("@@"):
                    if (
                        current_path
                        and traceback_path.endswith(current_path)
                        and hunk_is_unfixed(hunk)
                    ):
                        return True
                    hunk = []
                    continue
                if not current_path or raw_line.startswith(("+++", "diff --git ")):
                    continue
                if raw_line[:1] in {" ", "+", "-"}:
                    hunk.append((raw_line[0], raw_line[1:]))
            return bool(
                current_path
                and traceback_path.endswith(current_path)
                and hunk_is_unfixed(hunk)
            )

        unchanged_failure_owners = tuple(sorted(
            (traceback_path, symbol)
            for (traceback_path, symbol), sources in direct_raise_sources.items()
            if sources and unchanged_direct_raise_has_unchanged_dominator(
                traceback_path, sources,
            )
        ))
        if unchanged_failure_owners:
            return (
                "STAGED_PATCH_LEAVES_DIRECT_FAILURE_GUARD_UNCHANGED: the reviewed "
                "diff touches the public traceback owner but leaves its arrow-marked "
                f"direct raise and governing statement unchanged for "
                f"{list(unchanged_failure_owners)!r}. Replace the rejecting guard or "
                "the direct raise itself; code appended after the exception and "
                "neighboring reflected methods cannot repair the forward path"
            )
    if traceback_anchors:
        touched_methods = touched_source_methods()
        missing_traceback_anchors = tuple(sorted(
            (path, symbol) for path, symbol in traceback_anchors
            if path not in changed_paths or symbol not in touched_methods
        ))
        if missing_traceback_anchors:
            return (
                "STAGED_PATCH_OMITS_DIRECT_FAILURE_FRAME: the public traceback marks "
                "a direct exception-raising source frame "
                f"{list(missing_traceback_anchors)!r}, but the reviewed diff leaves "
                "that owner unchanged. Repair the guard/dispatch at the marked "
                "frame; a neighboring reflected or wrapper layer cannot prevent "
                "the unchanged direct raise"
            )

    source_method_names = {
        match.group(1)
        for match in re.finditer(
            r"(?m)^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(",
            definition_source,
        )
    }
    issue_sentences = tuple(filter(None, (
        part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", issue_text)
    )))
    explicit_causal_methods: set[str] = set()
    side_effect = re.compile(
        r"\b(?:creat|writ|delet|remov|rais|mutat|persist)\w*\b",
        re.IGNORECASE,
    )
    normative_error = re.compile(
        r"\b(?:incorrect|wrong|should\s+not|must\s+not|does\s+not\s+obey|"
        r"unexpected|expect(?:ation|ed)?\s+(?:would\s+)?(?:be|that))\b",
        re.IGNORECASE,
    )
    for index, sentence in enumerate(issue_sentences):
        if not side_effect.search(sentence):
            continue
        context_sentence = " ".join(issue_sentences[index:index + 2])
        if not normative_error.search(context_sentence):
            continue
        for name in source_method_names:
            explicitly_code_named = (
                "_" in name
                or re.search(
                    rf"(?:\.\s*{re.escape(name)}\b|"
                    rf"\b{re.escape(name)}\s*\()",
                    sentence,
                )
            )
            if explicitly_code_named and re.search(
                rf"\b{re.escape(name)}\b", sentence,
            ):
                explicit_causal_methods.add(name)

    missing_causal_methods = tuple(sorted(
        explicit_causal_methods - touched_source_methods()
    ))

    explicitly_required_methods: set[str] = set()
    for segment in discussion_segments:
        for match in re.finditer(
            r"changes?\s+to\s+(.{1,320}?)\s+need(?:s)?\s+to\s+be\s+made",
            segment,
            re.IGNORECASE | re.DOTALL,
        ):
            for name in re.findall(r"\b[A-Za-z_]\w*\b", match.group(1)):
                if re.search(
                    rf"(?m)^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(",
                    definition_source,
                ):
                    explicitly_required_methods.add(name)

    def existing_defensive_contract_is_justified(name: str) -> bool:
        """Allow a named method to remain unchanged when source proves it safe.

        Public discussion often lists every related read/write method before
        inspecting the current revision.  A read path may already implement the
        required neutral fallback while the write paths remain defective.  Forcing
        a textual no-op edit in that method both degrades patch quality and can
        erase an otherwise complete first patch.  Require two independent facts:
        the checked-out AST contains a guard plus a neutral fallback return, and
        the final review explicitly accounts for the unchanged method.
        """

        summary_pattern = re.compile(
            rf"\b{re.escape(name)}\b[\s\S]{{0,240}}"
            r"\b(?:already|existing|unchanged|needs?\s+no\s+change|"
            r"no\s+change\s+(?:is\s+)?(?:needed|required)|neutral|empty)\b",
            re.IGNORECASE,
        )
        reverse_summary_pattern = re.compile(
            r"\b(?:already|existing|unchanged|needs?\s+no\s+change|"
            r"no\s+change\s+(?:is\s+)?(?:needed|required)|neutral|empty)\b"
            rf"[\s\S]{{0,240}}\b{re.escape(name)}\b",
            re.IGNORECASE,
        )
        if not (
            summary_pattern.search(summary)
            or reverse_summary_pattern.search(summary)
        ):
            return False
        method = None
        for definition in definitions:
            source = str(definition.get("content", ""))
            if not source or not re.search(
                rf"(?m)^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(",
                source,
            ):
                continue
            try:
                tree = ast.parse(textwrap.dedent(source))
            except SyntaxError:
                continue
            method = next((
                node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name
            ), None)
            if method is not None:
                break
        if method is None:
            return False

        neutral_returns = {
            "None", "False", "{}", "[]", "()", "set()", "frozenset()",
        }
        has_neutral_fallback = any(
            isinstance(node, ast.Return)
            and ast.unparse(node.value) in neutral_returns
            for node in ast.walk(method)
        )
        has_guard = any(isinstance(node, ast.If) for node in ast.walk(method))
        # The proof is deliberately structural: a named read method which
        # already guards resource existence and returns a neutral value need not
        # be changed merely so its name appears in the diff.  Write methods do
        # not usually have such a neutral-return contract and still must change.
        return has_guard and has_neutral_fallback

    missing_explicit_methods = tuple(sorted(
        name for name in explicitly_required_methods
        if not re.search(rf"\b{re.escape(name)}\b", staged_diff)
        and not existing_defensive_contract_is_justified(name)
    ))
    if missing_causal_methods or missing_explicit_methods:
        if missing_causal_methods and missing_explicit_methods:
            return (
                "STAGED_PATCH_OMITS_EXPLICIT_CAUSAL_AND_MULTI_METHOD_CORRECTION: "
                "the public issue requires the side-effect owner(s) "
                f"{list(sorted(explicit_causal_methods))!r} and the related "
                f"reachable methods {list(sorted(explicitly_required_methods))!r}; "
                "the reviewed diff omits "
                f"{list(sorted(set(missing_causal_methods) | set(missing_explicit_methods)))!r}. "
                "Replace the complete edit set with "
                "the actual guards/calls/state writes for every named method"
            )
        if missing_causal_methods:
            return (
                "STAGED_PATCH_OMITS_EXPLICIT_CAUSAL_METHOD_CORRECTION: the public "
                "issue identifies side-effect owner(s) "
                f"{list(sorted(explicit_causal_methods))!r} as creating, writing, "
                "deleting, mutating, or raising the incorrect behavior, but the "
                f"reviewed diff does not change {list(missing_causal_methods)!r}. "
                "Modify the actual side-effect path; changes to neighboring methods "
                "cannot prove that another caller no longer reaches it"
            )
    if missing_explicit_methods:
        return (
            "STAGED_PATCH_OMITS_EXPLICIT_MULTI_METHOD_CORRECTION: the strongest "
            "public causal discussion explicitly says changes are needed in "
            f"{sorted(explicitly_required_methods)!r}, but the reviewed diff does "
            f"not touch {list(missing_explicit_methods)!r}. Replace the complete "
            "edit set so every named reachable method has its required behavior; "
            "an import or one upstream guard alone is incomplete"
        )

    checklist = getattr(packet, "requirement_checklist", None)
    primary_text = "\n".join((
        str(getattr(packet, "issue_text", "")),
        *map(str, getattr(checklist, "change_requirements", ()) or ()),
        *map(str, getattr(checklist, "boundary_requirements", ()) or ()),
        *map(str, getattr(checklist, "exception_requirements", ()) or ()),
    )).lower()
    normalized_assignment = re.compile(
        r"^([A-Za-z_]\w*)\s*=\s*(?:set|frozenset)\(\s*\1\s*\)$",
    )
    state_issue = re.search(
        r"\b(?:chain|defer|only|include|exclude|immediate|state|residual)\w*\b",
        primary_text,
    )
    if state_issue:
        delegate_error = _observed_causal_delegate_error(
            packet, staged_diff, conversation,
        )
        if delegate_error:
            return delegate_error
    discussion_text = "\n".join(discussion_segments).lower()
    explicit_residual_switch = bool(
        "difference" in discussion_text
        and re.search(r"\b(?:switch|mode|residual)\w*\b", discussion_text)
        and re.search(r"\b(?:only|include|immediate)\w*\b", discussion_text)
        and re.search(r"\b(?:defer|exclude)\w*\b", discussion_text)
    )
    diff_has_residual = any(
        ".difference(" in line or re.search(r"\w\s+-\s+\w", line)
        for line in added
    )
    diff_has_state_write = any(
        re.search(r"(?:self\.)?[A-Za-z_]\w*\s*=", line)
        and not normalized_assignment.match(line)
        for line in added
    )

    def mode_only_residual_flip() -> bool:
        """Detect a tag flip that leaves the directional residual unchanged.

        Complementary include/exclude state is commonly stored as
        ``(field_set, is_deferred)`` or an equivalent pair.  Replacing only the
        boolean tag while retaining the exact same one-way difference cannot
        account for fields that occur only in the incoming set.  This check is
        intentionally structural and is enabled only when public causal
        evidence explicitly calls for the reverse residual and a mode switch.
        """

        def assignments(lines: tuple[str, ...]) -> dict[str, ast.Assign]:
            parsed: dict[str, ast.Assign] = {}
            for line in lines:
                try:
                    statement = ast.parse(line).body
                except SyntaxError:
                    continue
                if len(statement) != 1 or not isinstance(statement[0], ast.Assign):
                    continue
                assignment = statement[0]
                if len(assignment.targets) != 1:
                    continue
                parsed[ast.dump(assignment.targets[0], include_attributes=False)] = assignment
            return parsed

        old_assignments = assignments(removed)
        new_assignments = assignments(added)
        for target_id in old_assignments.keys() & new_assignments.keys():
            old_value = old_assignments[target_id].value
            new_value = new_assignments[target_id].value
            if not (
                isinstance(old_value, (ast.Tuple, ast.List))
                and isinstance(new_value, type(old_value))
                and len(old_value.elts) == 2
                and len(new_value.elts) == 2
            ):
                continue
            old_residual, old_tag = old_value.elts
            new_residual, new_tag = new_value.elts
            if ast.dump(old_residual, include_attributes=False) != ast.dump(
                new_residual, include_attributes=False,
            ):
                continue
            if not any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "difference"
                for node in ast.walk(new_residual)
            ):
                continue
            if (
                isinstance(old_tag, ast.Constant)
                and isinstance(new_tag, ast.Constant)
                and isinstance(old_tag.value, bool)
                and isinstance(new_tag.value, bool)
                and old_tag.value != new_tag.value
            ):
                return True
        return False

    if (
        state_issue
        and explicit_residual_switch
        and not (diff_has_residual and diff_has_state_write)
    ):
        return (
            "STAGED_PATCH_IGNORES_EXPLICIT_STATE_TRANSITION_MECHANISM: the strongest "
            "public causal correction requires a set-difference residual and a "
            "mode/state switch, but the diff changes neither the residual computation "
            "nor its state write. Modify the producer transition; do not reinterpret "
            "the empty payload only in a downstream consumer guard"
        )
    if state_issue and explicit_residual_switch and mode_only_residual_flip():
        return (
            "STAGED_PATCH_FLIPS_MODE_WITHOUT_INCOMING_RESIDUAL: the diff keeps the "
            "same directional set-difference payload and changes only its companion "
            "mode/tag. This loses incoming-only members and cannot make chained and "
            "batched calls equivalent. Compute the normalized incoming-only residual "
            "and carry that residual only on the actual mode-switch branch"
        )
    if (
        state_issue
        and added
        and not removed
        and all(normalized_assignment.match(line) for line in added)
    ):
        return (
            "STAGED_PATCH_NORMALIZES_WITHOUT_STATE_TRANSITION: converting an input "
            "to set/frozenset alone cannot repair a chained include/exclude state. "
            "Trace both residuals and modify the actual branch or state write"
        )

    validation_issue = re.search(
        r"\b(?:too\s+strict|type|accept|reject|raises?|fails?|error|invalid)\w*\b",
        primary_text,
    )
    explicitly_requires_return = re.search(
        r"\b(?:should|must|expected\s+to|needs?\s+to)\s+return\b|"
        r"\breturn\s+value\b",
        primary_text,
    )
    if (
        validation_issue
        and not explicitly_requires_return
        and added
        and not removed
        and all(re.fullmatch(r"return\s+[A-Za-z_]\w*", line) for line in added)
    ):
        return (
            "STAGED_PATCH_RETURN_AFTER_UNCHANGED_VALIDATION: a terminal pass-through "
            "return cannot make an input pass earlier unchanged type/range guards. "
            "Modify the causal validation predicate, accepted type relation, or caller"
        )
    return None


def _mark_staged_quality_rejection(
    tools: RepairToolExecutor,
    review_error: str | None,
    *,
    fallback: str,
) -> str:
    """Persist initial-review rejection across bounded recovery invocations."""

    error = str(review_error or tools.staged_quality_error or fallback)
    tools.staged_quality_rejected = True
    tools.staged_quality_error = error
    tools.staged_quality_rejected_version = tools.staged_edit_version
    if error.startswith(
        "STAGED_PATCH_EXPANDS_SCOPED_FIX_INTO_SHARED_UTILITY"
    ):
        for edit in tools.staged_edits:
            path = edit.relative_path.replace("\\", "/")
            parts = {part.lower() for part in Path(path).parts}
            if (
                parts & {
                    "utils", "util", "common", "shared", "helpers",
                    "validators",
                }
                or Path(path).stem.lower() == "validation"
            ):
                tools.prohibited_staged_paths.add(path)
    return error


def _explicit_multi_method_names(quality_error: str) -> tuple[str, ...]:
    """Extract the mechanically named method contract from a review error."""

    if not str(quality_error).startswith((
        "STAGED_PATCH_OMITS_EXPLICIT_MULTI_METHOD_CORRECTION",
        "STAGED_PATCH_OMITS_EXPLICIT_CAUSAL_METHOD_CORRECTION",
        "STAGED_PATCH_OMITS_EXPLICIT_CAUSAL_AND_MULTI_METHOD_CORRECTION",
    )):
        return ()
    matches = re.findall(
        r"(?:changes are needed in|side-effect owner\(s\)|"
        r"related reachable methods)\s*(\[[^\]]*\])",
        str(quality_error),
    )
    if not matches:
        return ()
    values: list[str] = []
    for value_text in matches:
        try:
            parsed = ast.literal_eval(value_text)
        except (SyntaxError, ValueError):
            continue
        values.extend(
            str(value) for value in parsed
            if re.fullmatch(r"[A-Za-z_]\w*", str(value))
        )
    return tuple(dict.fromkeys(values))


@dataclass(slots=True)
class GeneratorConversation(SerializableRecord):
    conversation_id: str
    messages: list[dict]
    inspected_files: set[str] = field(default_factory=set)
    inspected_symbols: set[str] = field(default_factory=set)
    attempted_mechanisms: list[str] = field(default_factory=list)
    accepted_patch_hashes: list[str] = field(default_factory=list)
    rejected_patch_hashes: list[str] = field(default_factory=list)
    delivered_counterexamples: set[str] = field(default_factory=set)
    pending_context_requests: list = field(default_factory=list)
    current_working_diff: str = ""
    inspected_line_ranges: set[str] = field(default_factory=set)
    mechanism_failure_signatures: dict[str, list[str]] = field(default_factory=dict)
    rolled_back_diffs: list[str] = field(default_factory=list)
    eliminated_counterexamples: set[str] = field(default_factory=set)
    unresolved_counterexamples: set[str] = field(default_factory=set)
    passed_preservation_checks: set[str] = field(default_factory=set)
    last_evidence_fingerprint: str | None = None
    evidence_repeat_counts: dict[str, int] = field(default_factory=dict)
    revision_count: int = 0

    @classmethod
    def create(cls, instance_id: str) -> "GeneratorConversation":
        return cls(
            conversation_id=stable_id("generator-conversation", instance_id),
            messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        )


@dataclass(frozen=True, slots=True)
class GeneratorRevision(SerializableRecord):
    revision_id: str
    mechanism: str
    edits: tuple[ProposedEdit, ...]
    summary: str
    context_requests: tuple
    requested_public_checks: tuple[str, ...]
    tool_turns: int
    status: str


class ActionConversionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    NEEDS_SLICE_EXPANSION = "NEEDS_SLICE_EXPANSION"
    INVALID_OPERATOR = "INVALID_OPERATOR"
    INVALID_SOURCE = "INVALID_SOURCE"
    FORBIDDEN_PATH = "FORBIDDEN_PATH"


class GeneratorBlockedExternal(RuntimeError):
    """The persistent generator could not reach or decode its external model."""

    def __init__(self, operation: str, cause: Exception) -> None:
        self.operation = operation
        self.cause_type = type(cause).__name__
        self.detail = str(cause)
        super().__init__(f"{operation}: {self.cause_type}: {self.detail}")


@dataclass(frozen=True, slots=True)
class ActionConversionResult(SerializableRecord):
    status: ActionConversionStatus
    revision: GeneratorRevision
    reasons: tuple[str, ...]


_MECHANISMS = {
    "guard_expand", "guard_tighten", "operand_predicate", "protocol_dispatch",
    "remove_wrapper", "restore_representation", "exception_edge",
    "cross_function_propagation", "state_update_order", "preservation_restore",
    "causal_slice_rewrite", "initial_issue_repair",
}


def _normalize_mechanism(value: str, *, initial: bool) -> str | None:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _MECHANISMS:
        return normalized
    keyword_mapping = (
        (("exception", "error", "raise", "catch", "wrap"), "exception_edge"),
        (("dispatch", "notimplemented", "operand"), "protocol_dispatch"),
        (("guard", "condition", "predicate"), "guard_tighten"),
        (("wrapper",), "remove_wrapper"),
        (("representation", "shape"), "restore_representation"),
        (("state", "order"), "state_update_order"),
        (("preserv", "regression"), "preservation_restore"),
        (("import", "propagat", "caller", "callee"), "cross_function_propagation"),
        (("fix", "repair", "rewrite", "replace"), "causal_slice_rewrite"),
    )
    for keywords, mechanism in keyword_mapping:
        if any(keyword in normalized for keyword in keywords):
            return "initial_issue_repair" if initial and mechanism == "causal_slice_rewrite" else mechanism
    return None


def convert_revision_action(state, revision: GeneratorRevision) -> ActionConversionResult:
    normalized_mechanism = _normalize_mechanism(
        revision.mechanism,
        initial=not bool(getattr(
            getattr(state.checkpoint, "patch", None), "canonical_diff", ""
        )),
    )
    if normalized_mechanism is None:
        return ActionConversionResult(
            ActionConversionStatus.INVALID_OPERATOR, revision,
            (f"unregistered mechanism: {revision.mechanism}",),
        )
    if normalized_mechanism != revision.mechanism:
        revision = replace(revision, mechanism=normalized_mechanism)
    root = __import__("pathlib").Path(state.checkpoint.snapshot_tree).resolve()
    active_files = set(state.program_graph.file_index)
    requested_files = {
        path for request in revision.context_requests
        for path in getattr(request, "file_paths", ())
    }
    needs_expansion = False
    reasons: list[str] = []
    for edit in revision.edits:
        relative = edit.relative_path.replace("\\", "/")
        lowered = relative.lower()
        if (
            relative.startswith("/") or ".." in __import__("pathlib").Path(relative).parts
            or any(token in lowered for token in ("test_patch", "gold", "hidden", ".git/"))
            or __import__("pathlib").Path(relative).name.startswith("test_")
            or "tests" in __import__("pathlib").Path(relative).parts
        ):
            return ActionConversionResult(
                ActionConversionStatus.FORBIDDEN_PATH, revision,
                (f"forbidden edit path: {relative}",),
            )
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return ActionConversionResult(
                ActionConversionStatus.INVALID_SOURCE, revision,
                (f"source file missing: {relative}",),
            )
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        actual = "\n".join(lines[edit.start_line - 1:edit.end_line])
        if actual != edit.expected_source.rstrip("\n"):
            return ActionConversionResult(
                ActionConversionStatus.INVALID_SOURCE, revision,
                (f"source changed at {relative}:{edit.start_line}",),
            )
        if relative not in active_files:
            if relative in requested_files:
                needs_expansion = True
                reasons.append(f"expand active slice for {relative}")
            else:
                return ActionConversionResult(
                    ActionConversionStatus.INVALID_SOURCE, revision,
                    (f"source outside active slice without context request: {relative}",),
                )
    return ActionConversionResult(
        ActionConversionStatus.NEEDS_SLICE_EXPANSION if needs_expansion else ActionConversionStatus.ACCEPTED,
        revision, tuple(reasons),
    )


Transport = Callable[[list[dict], list[dict]], dict]


class DeepSeekHTTPTransport:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        max_concurrency: int = 10,
        request_timeout_seconds: float = 180.0,
        max_output_tokens: int = 8_000,
        max_transient_retries: int = 3,
        retry_base_delay_seconds: float = 1.0,
        retry_max_delay_seconds: float = 8.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.max_output_tokens = int(max_output_tokens)
        self.max_transient_retries = max(0, int(max_transient_retries))
        self.retry_base_delay_seconds = max(
            0.0, float(retry_base_delay_seconds),
        )
        self.retry_max_delay_seconds = max(
            self.retry_base_delay_seconds,
            float(retry_max_delay_seconds),
        )

    @staticmethod
    def _transient_error(exc: Exception) -> bool:
        if isinstance(exc, HTTPError):
            return exc.code in {408, 425, 429} or 500 <= exc.code <= 599
        return isinstance(exc, (URLError, TimeoutError, ConnectionError))

    def _retry_delay(self, exc: Exception, retry_index: int) -> float:
        retry_after = None
        if isinstance(exc, HTTPError) and exc.headers is not None:
            try:
                retry_after = float(exc.headers.get("Retry-After", ""))
            except (TypeError, ValueError):
                retry_after = None
        if retry_after is not None:
            return min(self.retry_max_delay_seconds, max(0.0, retry_after))
        exponential = self.retry_base_delay_seconds * (2 ** retry_index)
        jitter = random.uniform(0.0, self.retry_base_delay_seconds)
        return min(self.retry_max_delay_seconds, exponential + jitter)

    def _call_with_tool_choice(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | dict,
    ) -> dict:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "max_tokens": self.max_output_tokens,
        }
        request_data = json.dumps(payload).encode("utf-8")
        started = time.monotonic()
        record: dict[str, Any] = {
            "message_count": len(messages), "status": "REQUESTED",
            "attempts": [],
        }
        try:
            raw = None
            for attempt in range(self.max_transient_retries + 1):
                attempt_started = time.monotonic()
                request = Request(
                    f"{self.base_url}/chat/completions",
                    data=request_data, method="POST",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                try:
                    with self._semaphore, urlopen(
                        request, timeout=max(1.0, self.request_timeout_seconds),
                    ) as response:
                        raw = json.loads(response.read().decode("utf-8"))
                    record["attempts"].append({
                        "attempt": attempt + 1,
                        "status": "RESPONSE",
                        "duration_seconds": time.monotonic() - attempt_started,
                    })
                    break
                except Exception as exc:
                    transient = self._transient_error(exc)
                    record["attempts"].append({
                        "attempt": attempt + 1,
                        "status": "ERROR",
                        "transient": transient,
                        "error": f"{type(exc).__name__}: {exc}",
                        "duration_seconds": time.monotonic() - attempt_started,
                    })
                    if not transient or attempt >= self.max_transient_retries:
                        raise
                    delay = self._retry_delay(exc, attempt)
                    record["attempts"][-1]["retry_delay_seconds"] = delay
                    if delay:
                        time.sleep(delay)
            if raw is None:
                raise RuntimeError("DeepSeek transport completed without a response")
            choice = raw["choices"][0]
            message = dict(choice["message"])
            message["finish_reason"] = choice.get("finish_reason")
            record.update({
                "status": "RESPONSE",
                "tool_names": [
                    str(item.get("function", {}).get("name", ""))
                    for item in message.get("tool_calls", ())
                ],
                "usage": dict(raw.get("usage", {})),
                "retry_count": max(0, len(record["attempts"]) - 1),
            })
            return message
        except Exception as exc:
            record.update({
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
            })
            raise
        finally:
            record["duration_seconds"] = time.monotonic() - started
            with self._lock:
                self.calls.append(record)

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        return self._call_with_tool_choice(messages, tools, "auto")

    def call_with_tool_choice(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | dict,
    ) -> dict:
        """Expose transport-level tool choice for callers that explicitly need it."""
        return self._call_with_tool_choice(messages, tools, tool_choice)


def _tool_schema(
    allowed_names: frozenset[str] | None = None,
) -> list[dict]:
    def tool(name: str, properties: dict, required: list[str] | None = None) -> dict:
        return {"type": "function", "function": {"name": name, "description": name.replace("_", " "),
                "parameters": {"type": "object", "properties": properties, "required": required or []}}}
    string = {"type": "string"}
    strings = {"type": "array", "items": string}
    edit_properties = {
        "relative_path": string,
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "expected_source": string,
        "replacement": string,
    }
    edit_items = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": edit_properties,
            "required": [
                "relative_path", "start_line", "end_line",
                "expected_source", "replacement",
            ],
        },
    }
    schemas = [
        tool("search_code", {"query": string, "paths": strings}, ["query"]),
        tool("read_file", {"path": string, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, ["path"]),
        tool("inspect_symbol", {"symbol": string}, ["symbol"]),
        tool("find_callers", {"symbol": string}, ["symbol"]),
        tool("find_references", {"symbol": string}, ["symbol"]),
        tool("show_current_diff", {}),
        tool("run_public_check", {"check_id": string}, ["check_id"]),
        tool("request_program_slice", {"symbols": strings, "relation_kinds": strings}, ["symbols", "relation_kinds"]),
        tool("apply_edits", {
            "mechanism": {"type": "string", "enum": sorted(_MECHANISMS)},
            "edits": edit_items,
        }, ["mechanism", "edits"]),
        tool("replace_staged_edits", {
            "mechanism": {"type": "string", "enum": sorted(_MECHANISMS)},
            "edits": edit_items,
        }, ["mechanism", "edits"]),
        tool("finish_revision", {"summary": string}, ["summary"]),
        tool("declare_blocker", {"reason": string, "missing_evidence": strings}, ["reason"]),
    ]
    if allowed_names is None:
        return schemas
    return [
        schema for schema in schemas
        if schema["function"]["name"] in allowed_names
    ]


def _statement_change_schema() -> list[dict]:
    """Schema for the final local-edit recovery after repeated malformed edits."""

    string = {"type": "string"}
    return [{
        "type": "function",
        "function": {
            "name": "apply_statement_change",
            "description": (
                "Replace one unique existing executable statement or small block. "
                "A complete definition is accepted only when both source fields "
                "contain the same single existing function/class name and type. "
                "replacement_statement must differ from expected_statement. "
                "When the statement can occur more than once, provide the owning "
                "function/class name and the source anchor line."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mechanism": {
                        "type": "string", "enum": sorted(_MECHANISMS),
                    },
                    "relative_path": string,
                    "expected_statement": string,
                    "replacement_statement": string,
                    "owner_symbol": string,
                    "anchor_line": {"type": "integer", "minimum": 1},
                },
                "required": [
                    "mechanism", "relative_path", "expected_statement",
                    "replacement_statement",
                ],
            },
        },
    }]


_ALLOWED_TOOL_NAMES = frozenset(
    item["function"]["name"] for item in _tool_schema()
)
_FINAL_TURN_TOOL_NAMES = frozenset({
    "apply_edits", "replace_staged_edits", "request_program_slice", "finish_revision",
    "declare_blocker",
})

_INITIAL_REVIEW_TOOL_NAMES = frozenset({
    "replace_staged_edits", "finish_revision", "read_file", "search_code",
    "find_callers", "find_references", "run_public_check",
})


def _compile_reproduction_message(
    message: dict[str, Any],
    *,
    primary_issue: str = "",
    project_runner: str = "",
) -> tuple[dict[str, str] | None, str]:
    for call in message.get("tool_calls") or ():
        function = call.get("function", {})
        if function.get("name") != "submit_reproduction":
            continue
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            filename = Path(str(arguments["filename"])).name
            setup_source = str(arguments["setup_source"])
            expression = str(arguments["observation_expression"]).strip()
            expected = str(arguments["expected_observation"]).strip()
            tree = ast.parse(setup_source)
            expression_tree = ast.parse(expression, mode="eval")
        except (KeyError, TypeError, ValueError, SyntaxError, json.JSONDecodeError) as exc:
            return None, f"invalid reproduction payload or Python syntax: {exc}"
        if not filename.endswith(".py") or not expected or not expression:
            return None, "filename, observation expression, and expected observation are required"
        if filename.lower() in {"__init__.py", "conftest.py"}:
            filename = "test_public_reproduction.py"
        if expected.lower().strip(" .") in {
            "true", "false", "pass", "success", "expected behavior",
            "correct behavior", "a value",
        }:
            expected = f"public behavior satisfies: {expression}"
        forbidden_setup = any(
            isinstance(node, (ast.Assert, ast.Raise))
            for node in ast.walk(tree)
        ) or "sys.exit" in setup_source
        if forbidden_setup:
            return None, "setup_source contains an assertion, raise, or sys.exit"
        if project_runner.lower() == "django":
            for model_class in (
                node for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
                and any(
                    isinstance(base, ast.Attribute) and base.attr == "Model"
                    for base in node.bases
                )
            ):
                for nested in ast.walk(model_class):
                    if not isinstance(nested, ast.Call):
                        continue
                    function = nested.func
                    if not (
                        isinstance(function, ast.Attribute)
                        and function.attr == "check"
                        and isinstance(function.value, ast.Name)
                        and function.value.id != model_class.name
                    ):
                        continue
                    return None, (
                        "Django model class bodies must not call field.check(); "
                        "record the model class and call Model.check() after its "
                        "definition so field binding and backend checks are valid"
                    )
        direct_attribute_type_checks = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"isinstance", "callable"}
            and node.args
            and isinstance(node.args[0], ast.Attribute)
        ]
        issue_lower = primary_issue.lower()
        explicit_attribute_type_contract = any(
            marker in issue_lower for marker in (
                "attribute must be", "attribute should be", "property must be",
                "property should be", "must return a string",
                "should return a string", "must return a callable",
                "should return a callable", "isinstance(",
            )
        )
        if direct_attribute_type_checks and not explicit_attribute_type_contract:
            return None, (
                "observation inspects the runtime type of an intermediate object "
                "attribute, but the primary issue does not define that internal "
                "representation as a public contract; observe the end-to-end API "
                "result, serializer/writer output, exception, or externally visible "
                "behavior instead"
            )
        if not any(
            isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            for node in ast.walk(expression_tree)
        ):
            return None, "observation_expression does not load an observed value"
        expression_names = {
            node.id for node in ast.walk(expression_tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        observation_suffixes = (
            "_success", "_matches", "_unchanged", "_equal", "_ok",
            "_valid", "_callable", "_preserved", "_raised",
            "_warning", "_warnings",
        )
        setup_observations: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if (
                    name.startswith(("has_", "is_", "can_", "supports_"))
                    or name.endswith(observation_suffixes)
                ):
                    setup_observations.add(name)
        missing_observations = sorted(setup_observations - expression_names)
        if missing_observations:
            expression = " and ".join((
                f"({expression})",
                *(f"({name})" for name in missing_observations),
            ))
        source = (
            setup_source.rstrip()
            + "\n\n"
            + f"assert ({expression}), {expected!r}\n"
        )
        return {
            "filename": filename,
            "source": source,
            "expected_observation": expected,
        }, ""
    return None, "response did not call submit_reproduction"


class PersistentDeepSeekAgent:
    def __init__(
        self,
        transport: Transport,
        *,
        max_tool_turns: int = 6,
        max_revisions: int = 6,
        max_wall_time_seconds: float = 300.0,
        max_completion_tokens: int = 32_000,
    ) -> None:
        self.transport = transport
        # Keep the caller's bound for deterministic transports.  The
        # controller may raise the initial-generation budget to the production
        # default, but short-budget fixtures must still be able to terminate
        # their contextless loop predictably.
        self.requested_max_tool_turns = max_tool_turns
        self.max_tool_turns = max_tool_turns
        self.max_revisions = max_revisions
        self.max_wall_time_seconds = float(max_wall_time_seconds)
        self.max_completion_tokens = int(max_completion_tokens)

    @staticmethod
    def _request_messages(conversation: GeneratorConversation) -> list[dict]:
        user_indices = [
            index for index, message in enumerate(conversation.messages)
            if message.get("role") == "user"
        ]
        first_user = user_indices[0]
        last_user = user_indices[-1]
        memory = {
            "conversation_id": conversation.conversation_id,
            "inspected_files": sorted(conversation.inspected_files)[-40:],
            "inspected_symbols": sorted(conversation.inspected_symbols)[-40:],
            "attempted_mechanisms": conversation.attempted_mechanisms[-20:],
            "accepted_patch_hashes": conversation.accepted_patch_hashes[-10:],
            "rejected_patch_hashes": conversation.rejected_patch_hashes[-10:],
            "delivered_counterexamples": sorted(conversation.delivered_counterexamples)[-20:],
            "current_working_diff": conversation.current_working_diff,
            "inspected_line_ranges": sorted(conversation.inspected_line_ranges)[-40:],
            "mechanism_failure_signatures": conversation.mechanism_failure_signatures,
            "rolled_back_diffs": conversation.rolled_back_diffs[-10:],
            "eliminated_counterexamples": sorted(conversation.eliminated_counterexamples)[-20:],
            "unresolved_counterexamples": sorted(conversation.unresolved_counterexamples)[-20:],
            "passed_preservation_checks": sorted(conversation.passed_preservation_checks)[-20:],
        }
        messages = [
            conversation.messages[0],
            {
                "role": "system",
                "content": "Persistent conversation memory: "
                + json.dumps(memory, sort_keys=True),
            },
        ]
        if first_user != last_user:
            # Structural correction prompts are intentionally compact, but
            # they must not replace the normative issue and checklist.  Keep
            # the first bounded repair packet as the task authority while
            # retaining only the latest correction exchange around it.
            messages.extend((
                conversation.messages[first_user],
                {
                    "role": "system",
                    "content": (
                        "The preceding user message is the primary repair task. "
                        "The following user message is only the latest structural "
                        "or mechanical correction and must not replace that task."
                    ),
                },
            ))
        messages.extend(conversation.messages[last_user:])
        return messages

    @staticmethod
    def _recent_mechanical_failures(
        conversation: GeneratorConversation,
        *,
        limit: int = 6,
    ) -> tuple[str, ...]:
        failures: list[str] = []
        for message in reversed(conversation.messages):
            if message.get("role") != "tool":
                continue
            try:
                payload = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            error = payload.get("error") if isinstance(payload, dict) else None
            if not isinstance(error, str) or not error.strip():
                continue
            if error not in failures:
                failures.append(error)
            if len(failures) >= limit:
                break
        return tuple(reversed(failures))

    @staticmethod
    def _mechanical_correction_instruction(feedback: str) -> str:
        """Turn a validator failure into one concrete, bounded edit constraint."""

        lowered = feedback.lower()
        if "omits_explicit_multi_method_correction" in lowered:
            return (
                "The public causal correction names multiple existing methods. "
                "Submit one complete replace_staged_edits call that changes every "
                "method named in the validator error, plus any import they use. "
                "Do not finish with only ensure/setup logic, and do not resubmit a "
                "subset of the named methods. Copy each small method body exactly "
                "from the supplied source anchor before replacing it. For named read "
                "paths, return the existing neutral/empty public result when routing "
                "disallows the backing store. For named record/write paths, avoid the "
                "creation and write when the same route is disallowed (or preserve the "
                "repository's documented refusal exception). Reuse one predicate; do "
                "not add no-op copies of the methods merely to mention their names."
            )
        if "ignores_explicit_state_transition_mechanism" in lowered:
            return (
                "Change the producer's residual computation and state/mode write in "
                "one complete edit. Compute both directional residuals described by "
                "the public correction; do not modify only a downstream empty-state "
                "consumer guard."
            )
        if "unresolved direct name" in lowered:
            candidates = ""
            match = re.search(
                r"Import candidates:\s*(.+?)(?:\.(?:\s|$)|$)",
                feedback,
                re.IGNORECASE,
            )
            if match is not None:
                candidates = (
                    " Prefer one of the mechanically located repository imports: "
                    + match.group(1).strip()
                    + "."
                )
            return (
                "Keep the behavior edit and add the missing import/assignment/parameter "
                "in the same complete edit set. Do not replace it with an import-only "
                "patch."
                + candidates
            )
        if "unused direct import" in lowered:
            return (
                "The dependency named by the issue was imported but never read or "
                "called. Change the existing causal guard, call, expression, state "
                "write, exception, or return to use that dependency in the same "
                "complete edit set. If it is not part of the real mechanism, remove "
                "the import and implement the actual root-cause behavior instead."
            )
        if "reverses_explicit_branch_relation" in lowered:
            return (
                "The reviewed conditional implements the opposite of the explicit "
                "when/if relation. Keep the predicate and preservation branch, but "
                "move the newly required call/action into the branch where that "
                "literal condition is true. Review the complete ternary or if/else "
                "rather than only changing the summary."
            )
        if "uses_required_prefix_only_as_fallback" in lowered:
            return (
                "The requirement says to prepend a prefix to an existing relative "
                "value. Do not use the prefix call as the right-hand side of `or` "
                "or as a replacement for the value. Do not change a configuration "
                "getter/property merely to return the prefix. Use the mandatory "
                "source slice that actually joins or concatenates the configured "
                "base with the relative value, and modify that executable "
                "construction so both are composed. Leave absolute and "
                "already-prefixed values unchanged."
            )
        if "omits_explicit_causal_method_correction" in lowered:
            return (
                "The issue names the method that performs the incorrect side effect. "
                "Change that existing method's guard, call, state write, exception, "
                "or return in the complete edit set. Do not modify only neighboring "
                "read/write methods while an unchanged caller can still reach the "
                "side effect."
            )
        if "omits_explicit_causal_and_multi_method_correction" in lowered:
            return (
                "The issue names both a side-effect owner and related reachable "
                "methods. Submit one complete edit set that changes every method "
                "listed in the validation error, using the exact source slices. "
                "Do not fix only the first guard while leaving the record/read/write "
                "paths able to reach the old behavior."
            )
        if "caller-owned alias" in lowered or "caller-owned state" in lowered:
            alias_match = re.search(
                r"caller-owned alias [\"']([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)",
                feedback,
            )
            owned_path = ""
            if alias_match is not None:
                written_path = alias_match.group(1)
                owned_path = written_path.rsplit(".", 1)[0]
            concrete = (
                f" The corrected replacement must include `{owned_path} = "
                f"{owned_path}.clone()` (or the repository's established copy "
                f"equivalent) before writing `{alias_match.group(1)}`."
                if owned_path and alias_match is not None else ""
            )
            return (
                "The proposed write can still refer to an object supplied by the caller. "
                "Clone/copy that object immediately before changing its state, or move "
                "the write to an owned result. Preserve missing/None behavior and do "
                "not mutate the input through a getattr fallback alias. Do not resubmit "
                "the same assignment plus state write without an intervening clone."
                + concrete
            )
        if "ignores existing binary capability contract" in lowered:
            capability_match = re.search(
                r"capability contract\(s\) \(([^)]*)\)", feedback,
                re.IGNORECASE,
            )
            capability = "the boolean capability named in the validator error"
            if capability_match is not None:
                names = re.findall(r"['\"]([A-Za-z_]\w*)['\"]", capability_match.group(1))
                if names:
                    capability = f"`{names[0]}`"
            return (
                "The owning class already publishes " + capability + " as the "
                "binary compatibility contract. Replace the concrete peer-type "
                "rejection with a check of that capability and keep TypeError for "
                "operands which do not expose it. Do not return NotImplemented from "
                "the helper unless an inspected reflected dunder concretely accepts "
                "this receiver. Preserve the operand object and sibling operators."
            )
        if "binary protocol dispatch" in lowered or "coerces binary operand" in lowered:
            capability_example = ""
            capability_match = re.search(
                r"test\s+(getattr\([^\n]+?\))\s+instead\s+of",
                feedback,
                re.IGNORECASE,
            )
            if capability_match is not None:
                capability_example = (
                    " The validator mechanically found the concrete compatibility "
                    f"predicate `{capability_match.group(1)}`; use that predicate "
                    "directly in the rejecting guard."
                )
            return (
                "Do not wrap or reconstruct the binary operand inside _combine or an "
                "operator method. Preserve the operand object. If the inspected source "
                "exposes a boolean capability marker, accept operands through that "
                "marker and retain TypeError for operands without it. Return "
                "NotImplemented only when a concrete inspected reflected dunder is "
                "proven to accept the receiver through reflected dispatch; keep "
                "sibling operators symmetric."
                + capability_example
            )
        if "input/form adapter inside a presentation path" in lowered:
            return (
                "Do not construct a form field or input adapter inside the display, "
                "render, format, or serialization helper. Identify the actual runtime "
                "value provenance: persisted/model values cannot be invalid form-input "
                "sentinels. Use the producer/model object's already inspected configured "
                "encoder or serialization contract directly, preserving custom encoder "
                "configuration and the existing empty/None branches."
            )
        if "persistence/input coercion as an output serializer" in lowered:
            return (
                "Database preparation, input cleaning, and to_python conversion do "
                "not serialize a runtime value for display. Keep the edit in the "
                "presentation owner, but call the producer/model object's inspected "
                "configured output encoder or serializer directly. If the source "
                "validates serializability with an encoder argument, reuse that same "
                "encoder contract and catch only its documented serialization error."
            )
        if (
            "staged_patch_omits_direct_failure_frame" in lowered
            or "staged_patch_omits_direct_failure_symbol" in lowered
            or "staged_patch_duplicates_direct_failure_before_correction" in lowered
            or "staged_patch_leaves_direct_failure_guard_unchanged" in lowered
        ):
            return (
                "The public traceback points to the exact unchanged guard or dispatch "
                "which directly raises the reported exception. Modify that source "
                "frame using its existing compatibility/capability contract. Replace "
                "the rejecting predicate (for example, a concrete peer-type check) "
                "with the inspected published capability check while retaining the "
                "exception for incompatible values. Do not append code below the "
                "raise, duplicate the remainder of the method, or move the fix to a "
                "neighboring wrapper/reflected method while the marked raise remains "
                "reachable."
            )
        if "rectangular-index" in lowered or "column dimension" in lowered:
            return (
                "This boundary is shared by sibling row/column predicates. Inspect the "
                "exact sibling source already returned and submit one complete apply_edits "
                "call covering every structurally equivalent unsafe index, with each "
                "expected_source copied verbatim. Bound the inner index by the actual "
                "column dimension without narrowing valid square/empty cases."
            )
        if "selected execution path unchanged" in lowered:
            return (
                "The previous replacement copied the selected body unchanged and "
                "only added a definition. In the next call, change an existing "
                "return, call, guard, assignment, or state write in that body. If a "
                "helper is necessary, include a separate edit that changes the "
                "existing body to call it; otherwise add no new def or class."
            )
        if "no-op edit" in lowered:
            return (
                "The previous replacement was identical to the repository source. "
                "Submit a small span containing an existing executable statement and "
                "change that statement; do not append a helper or neighboring "
                "definition."
            )
        if "shadowing definitions" in lowered or "duplicate" in lowered:
            return (
                "Do not emit a second copy of any function or class. Replace only the "
                "unique existing definition, or preferably the smallest statements "
                "inside it that implement the behavior change."
            )
        if "python source is invalid" in lowered:
            return (
                "Use a smaller exact statement span wholly inside one existing "
                "function. Preserve indentation and do not include the following "
                "function/class header or a truncated control-flow block."
            )
        if "expected source is ambiguous" in lowered:
            return (
                "Choose a longer unique expected_source span from one exact source "
                "anchor, including the owning function header or a distinctive "
                "neighboring statement."
            )
        if "expected source mismatch" in lowered:
            return (
                "Copy expected_source verbatim from the latest exact source anchor "
                "and keep the edit within that anchor's stated line interval."
            )
        return (
            "Correct the stated mechanical failure with the smallest changed "
            "executable statement in the existing path."
        )

    @staticmethod
    def _stage_edits_with_mechanical_completion(
        tools: RepairToolExecutor,
        edits: tuple[ProposedEdit, ...],
        *,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """Stage edits and close only a mechanically proven missing-import gap."""

        try:
            return (
                tools.replace_staged_edits(edits)
                if replace_existing else tools.apply_edits(edits)
            )
        except ValueError as exc:
            if "unresolved direct name" not in str(exc).lower():
                raise
            try:
                return tools.complete_unresolved_name_edits(
                    replace_existing=replace_existing,
                )
            except ValueError:
                # Preserve the actionable validator failure when the bounded
                # repository index cannot prove an import. The next recovery
                # turn can still submit an explicit complete edit set; replacing
                # this with a secondary "no candidate" error would incorrectly
                # route it back to another statement-only attempt.
                raise exc

    @staticmethod
    def _apply_statement_change(
        tools: RepairToolExecutor,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Validate and stage a unique local statement replacement."""

        mechanism = str(arguments["mechanism"])
        expected = str(arguments["expected_statement"]).rstrip("\n")
        replacement = str(arguments["replacement_statement"]).rstrip("\n")
        if not expected.strip() or not replacement.strip():
            raise ValueError("statement change source and replacement must be non-empty")
        if expected == replacement:
            raise ValueError("statement change replacement must differ from source")
        declaration = re.compile(r"(?m)^\s*(?:async\s+def|def|class)\s+")
        if declaration.search(expected) or declaration.search(replacement):
            def single_definition(source: str) -> ast.AST | None:
                try:
                    parsed = ast.parse(textwrap.dedent(source))
                except SyntaxError:
                    return None
                if len(parsed.body) != 1 or not isinstance(parsed.body[0], (
                    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                )):
                    return None
                return parsed.body[0]

            before_definition = single_definition(expected)
            after_definition = single_definition(replacement)
            same_existing_definition = (
                before_definition is not None
                and after_definition is not None
                and type(before_definition) is type(after_definition)
                and before_definition.name == after_definition.name
            )
            if not same_existing_definition:
                raise ValueError(
                    "definition change must replace the same single existing "
                    "function/class; new, renamed, or multiple definitions are "
                    "not allowed"
                )
        expected_lines = expected.splitlines()
        replacement_lines = replacement.splitlines()
        if len(expected_lines) > 80 or len(replacement_lines) > 80:
            raise ValueError("statement change must contain at most 80 lines")
        if len(expected) > 8_000 or len(replacement) > 8_000:
            raise ValueError("statement change exceeds the bounded source size")
        relative_path = str(arguments["relative_path"])
        path = tools._path(relative_path, for_edit=True)
        source_lines = path.read_text(
            encoding="utf-8", errors="replace",
        ).splitlines()
        matches = [
            index + 1
            for index in range(len(source_lines) - len(expected_lines) + 1)
            if source_lines[index:index + len(expected_lines)] == expected_lines
        ]
        owner_symbol = str(arguments.get("owner_symbol", "")).rsplit(".", 1)[-1]
        anchor_line = int(arguments.get("anchor_line", 0) or 0)

        if len(matches) > 1 and owner_symbol:
            try:
                tree = ast.parse("\n".join(source_lines))
            except SyntaxError:
                tree = ast.Module(body=[], type_ignores=[])
            owner_ranges = [
                (
                    int(getattr(node, "lineno", 0)),
                    int(getattr(node, "end_lineno", getattr(node, "lineno", 0))),
                )
                for node in ast.walk(tree)
                if isinstance(node, (
                    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                ))
                and node.name == owner_symbol
            ]
            scoped_matches = [
                match
                for match in matches
                if any(
                    start <= match
                    and match + len(expected_lines) - 1 <= end
                    for start, end in owner_ranges
                )
            ]
            if len(scoped_matches) == 1:
                matches = scoped_matches

        if len(matches) > 1 and anchor_line:
            distances = [(abs(match - anchor_line), match) for match in matches]
            minimum = min(distance for distance, _match in distances)
            nearest = [
                match for distance, match in distances if distance == minimum
            ]
            if len(nearest) == 1:
                matches = nearest

        if len(matches) > 1:
            raise ValueError(
                "expected source is ambiguous in "
                f"{relative_path}; candidate lines: {matches[:12]}. "
                "Provide owner_symbol and anchor_line from the exact source anchor."
            )
        start_line = matches[0] if matches else max(1, anchor_line or 1)
        output = PersistentDeepSeekAgent._stage_edits_with_mechanical_completion(
            tools,
            (ProposedEdit(
                relative_path=relative_path,
                start_line=start_line,
                end_line=start_line + len(expected_lines) - 1,
                expected_source=expected,
                replacement=replacement,
            ),),
        )
        return mechanism, output

    @staticmethod
    def _latest_reasoning_summary(
        conversation: GeneratorConversation,
        *,
        limit: int = 3_000,
    ) -> str:
        """Retain the last substantive diagnosis without replaying its tool trace."""

        for message in reversed(conversation.messages):
            if message.get("role") != "assistant":
                continue
            content = str(message.get("content") or "").strip()
            if content:
                return content[:limit]
        return ""

    @classmethod
    def _compact_structural_anchor(
        cls,
        context,
        conversation: GeneratorConversation,
        tools: RepairToolExecutor,
        *,
        preferred_path: str = "",
    ) -> dict[str, Any]:
        """Compress initial synthesis evidence around the rejected edit path."""

        snippets = list(cls._exact_source_anchor(context, conversation, tools))
        if preferred_path:
            snippets.sort(key=lambda item: (
                str(item.get("relative_path", "")) != preferred_path,
                int(item.get("end_line", 0)) - int(item.get("start_line", 0)),
            ))
        selected: list[dict[str, Any]] = []
        source_characters = 0
        for snippet in snippets:
            content = str(snippet.get("content", ""))
            if not content or source_characters + len(content) > 9_000:
                continue
            selected.append(snippet)
            source_characters += len(content)
            if len(selected) >= 3:
                break

        requirements = []
        for row in getattr(context, "requirement_coverage", ()):
            requirement = str(row.get("normalized_requirement", "")).strip()
            if not requirement:
                continue
            requirements.append({
                "requirement_id": str(row.get("requirement_id", "")),
                "authority": str(row.get("authority", "")),
                "status": str(row.get("status", "")),
                "requirement": requirement[:1_500],
            })
            if len(requirements) >= 8:
                break
        return {
            "issue_excerpt": str(context.issue)[:8_000],
            "requirements": tuple(requirements),
            "earlier_root_cause_analysis": cls._latest_reasoning_summary(
                conversation,
            ),
            "current_working_diff": str(getattr(context, "working_diff", ""))[-8_000:],
            "failed_checks": tuple(getattr(context, "failed_checks", ()))[:4],
            "failure_signature": getattr(context, "failure_signature", None),
            "causal_cut_candidates": tuple(
                getattr(context, "causal_cut_candidates", ())
            )[:3],
            "exact_source_snippets": tuple(selected),
            "mechanical_recovery_anchors": tuple(
                getattr(tools, "mechanical_recovery_anchors", ())
            )[:3],
        }

    @staticmethod
    def _structural_request_messages(
        conversation: GeneratorConversation,
    ) -> list[dict]:
        """Send only the compact correction packet to forced edit calls."""

        latest_user = next(
            message for message in reversed(conversation.messages)
            if message.get("role") == "user"
        )
        return [
            conversation.messages[0],
            {
                "role": "system",
                "content": (
                    "This is the same initial repair trajectory. The compact user "
                    "packet below preserves the normative issue, requirements, "
                    "earlier root-cause analysis, and exact repository source. "
                    "Mechanically rejected edits are not evidence that the repair "
                    "is correct. Return only the required changed tool action."
                ),
            },
            latest_user,
        ]

    @staticmethod
    def _persisted_initial_packet(
        conversation: GeneratorConversation,
    ) -> InitialRepairPacket | None:
        """Restore the immutable first packet from the persistent conversation.

        Initial recovery must not rerun semantic compilation and silently change
        the task. The first user message already contains the exact packet used by
        initial generation, so reconstruct only that serialized value.
        """

        for message in conversation.messages:
            if message.get("role") != "user":
                continue
            try:
                payload = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            packet = payload.get("initial_repair_packet")
            if not isinstance(packet, dict):
                continue
            checklist = packet.get("requirement_checklist", {})
            if not isinstance(checklist, dict):
                continue
            return InitialRepairPacket(
                issue_text=str(packet.get("issue_text", "")),
                requirement_checklist=RequirementChecklist(
                    change_requirements=tuple(map(
                        str, checklist.get("change_requirements", ()),
                    )),
                    boundary_requirements=tuple(map(
                        str, checklist.get("boundary_requirements", ()),
                    )),
                    exception_requirements=tuple(map(
                        str, checklist.get("exception_requirements", ()),
                    )),
                    preservation_requirements=tuple(map(
                        str, checklist.get("preservation_requirements", ()),
                    )),
                    witnesses=tuple(map(str, checklist.get("witnesses", ()))),
                    uncertainties=tuple(map(
                        str, checklist.get("uncertainties", ()),
                    )),
                ),
                likely_definitions=tuple(packet.get("likely_definitions", ())),
                direct_callers=tuple(packet.get("direct_callers", ())),
                related_public_tests=tuple(packet.get(
                    "related_public_tests", (),
                )),
                discussion_evidence=tuple(map(
                    str, packet.get("discussion_evidence", ()),
                )),
                candidate_symbols=tuple(map(
                    str, packet.get("candidate_symbols", ()),
                )),
                relevant_protocols=tuple(map(
                    str, packet.get("relevant_protocols", ()),
                )),
                expected_behavior=tuple(map(
                    str, packet.get("expected_behavior", ()),
                )),
                preservation_behavior=tuple(map(
                    str, packet.get("preservation_behavior", ()),
                )),
                uncertainty=tuple(map(str, packet.get("uncertainty", ()))),
            )
        return None

    @staticmethod
    def _exact_source_anchor(
        context,
        conversation: GeneratorConversation | None = None,
        tools: RepairToolExecutor | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Keep exact implementation source available after compaction.

        The initial context is deliberately bounded and can contain low-value
        lexical matches (for example examples/ snippets).  Once the model has
        inspected a file, its tool result is stronger evidence than those
        matches, so fold recent ``read_file`` results into the correction
        anchor and rank implementation paths ahead of examples and tests.
        """

        snippets = list(context.relevant_source_snippets)
        if conversation is not None:
            for message in conversation.messages:
                if message.get("role") != "tool":
                    continue
                try:
                    payload = json.loads(message.get("content") or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict) or not payload.get("path"):
                    continue
                content = payload.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                snippets.append({
                    "relative_path": str(payload["path"]),
                    "start_line": int(payload.get("start_line", 1)),
                    "end_line": int(payload.get("end_line", 1)),
                    "snippet_start_line": int(payload.get("start_line", 1)),
                    "snippet_end_line": int(payload.get("end_line", 1)),
                    "symbol": "<recent-read-file>",
                    "content": content,
                    "origin": "RECENT_READ_FILE",
                })

            # A model can locate the correct implementation with search_code
            # but spend its bounded read calls on a downstream formatter or
            # consumer. Materialize a small source window around those real
            # repository hits so root recovery does not discard successful
            # localization. This is still bounded source evidence, not a
            # repository-wide prompt dump.
            if tools is not None:
                root = tools.repository_root.resolve()
                materialized: set[tuple[str, int]] = set()
                for message in conversation.messages:
                    if message.get("role") != "tool":
                        continue
                    try:
                        payload = json.loads(message.get("content") or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    matches = payload.get("matches") if isinstance(payload, dict) else None
                    if not isinstance(matches, list):
                        continue
                    for match in matches[:3]:
                        if not isinstance(match, dict):
                            continue
                        relative = str(match.get("path", ""))
                        line = int(match.get("line", 0) or 0)
                        parts = {part.lower() for part in Path(relative).parts}
                        key = (relative, line)
                        if (
                            not relative or line < 1 or key in materialized
                            or parts & {
                                "tests", "test", "examples", "example",
                                "benchmarks", "docs", "doc",
                            }
                        ):
                            continue
                        path = (root / relative).resolve()
                        if not path.is_relative_to(root) or not path.is_file():
                            continue
                        lines = path.read_text(
                            encoding="utf-8", errors="replace",
                        ).splitlines()
                        start = max(1, line - 30)
                        end = min(len(lines), line + 40)
                        materialized.add(key)
                        snippets.append({
                            "relative_path": relative,
                            "start_line": start,
                            "end_line": end,
                            "snippet_start_line": start,
                            "snippet_end_line": end,
                            "symbol": "<search-result-source>",
                            "content": "\n".join(lines[start - 1:end]),
                            "origin": "SEARCH_RESULT_SOURCE",
                        })
                        if len(materialized) >= 8:
                            break
                    if len(materialized) >= 8:
                        break

        explicit_methods = set(re.findall(
            r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", context.issue,
        ))
        explicit_owners = {
            token for token in re.findall(
                r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", context.issue,
            )
            if any(character.isupper() for character in token[1:])
        }
        requested_symbols: set[str] = set()
        if conversation is not None:
            for message in conversation.messages:
                for call in message.get("tool_calls") or ():
                    function = call.get("function", {})
                    name = str(function.get("name", ""))
                    try:
                        arguments = json.loads(
                            function.get("arguments") or "{}"
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if name == "request_program_slice":
                        requested_symbols.update(
                            str(symbol).rsplit(".", 1)[-1]
                            for symbol in arguments.get("symbols", ())
                        )
                    elif name in {"search_code", "inspect_symbol"}:
                        value = str(
                            arguments.get("query", arguments.get("symbol", ""))
                        ).strip()
                        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", value):
                            requested_symbols.add(value.rsplit(".", 1)[-1])

        def bounded_windows(item: dict[str, Any]) -> tuple[dict[str, Any], ...]:
            """Split oversized class reads around explicit issue symbols.

            A complete class returned by symbol inspection can be tens of
            thousands of characters.  Keeping it as the first anchor would
            exhaust the entire correction budget and discard narrower source
            that the model read later.  Windows retain exact repository text
            and adjusted line numbers, so they remain valid edit anchors.
            """

            content = str(item.get("content", ""))
            if len(content) <= 8000:
                return (item,)
            lines = content.splitlines()
            starts: list[tuple[int, str, int]] = []
            for index, line in enumerate(lines):
                match = re.match(
                    r"^(\s*)(?:async\s+def|def|class)\s+"
                    r"([A-Za-z_][A-Za-z0-9_]*)\b",
                    line,
                )
                if not match:
                    continue
                name = match.group(2)
                if name in explicit_methods or name in explicit_owners:
                    starts.append((index, name, len(match.group(1))))
            method_starts = [
                item for item in starts if item[1] in explicit_methods
            ]
            if method_starts:
                starts = method_starts
            if not starts:
                return ({
                    **item,
                    "content": "\n".join(lines[:120]),
                    "end_line": int(item.get("start_line", 1))
                    + min(len(lines), 120) - 1,
                    "snippet_end_line": int(item.get(
                        "snippet_start_line", item.get("start_line", 1),
                    )) + min(len(lines), 120) - 1,
                    "origin": str(item.get("origin", "")) + ":BOUNDED_HEAD",
                },)
            windows: list[dict[str, Any]] = []
            base_line = int(item.get(
                "snippet_start_line", item.get("start_line", 1),
            ))
            for index, name, indentation in starts[:4]:
                stop = min(len(lines), index + 80)
                for candidate in range(index + 1, stop):
                    next_match = re.match(
                        r"^(\s*)(?:async\s+def|def|class)\s+"
                        r"[A-Za-z_][A-Za-z0-9_]*\b",
                        lines[candidate],
                    )
                    if (
                        next_match
                        and len(next_match.group(1)) <= indentation
                    ):
                        stop = candidate
                        break
                windows.append({
                    **item,
                    "start_line": base_line + index,
                    "end_line": base_line + stop - 1,
                    "snippet_start_line": base_line + index,
                    "snippet_end_line": base_line + stop - 1,
                    "symbol": (
                        str(item.get("symbol", "")).rstrip(".") + "." + name
                    ),
                    "content": "\n".join(lines[index:stop]),
                    "origin": str(item.get("origin", "")) + ":SYMBOL_WINDOW",
                })
            return tuple(windows)

        snippets = [
            window
            for item in snippets
            for window in bounded_windows(item)
        ]

        def source_priority(
            item: dict[str, Any],
        ) -> tuple[int, int, int, int, int, int, str]:
            relative = str(item.get("relative_path", ""))
            parts = {part.lower() for part in Path(relative).parts}
            content = str(item.get("content", ""))
            symbol = str(item.get("symbol", ""))
            owns_named_symbol = any(
                re.search(
                    rf"\b(?:class|def)\s+{re.escape(owner)}\b", content,
                )
                or owner in symbol.split(".")
                for owner in explicit_owners
            )
            contains_requested_symbol = any(
                re.search(
                    rf"\b(?:class|def)\s+{re.escape(symbol)}\b", content,
                )
                or symbol in str(item.get("symbol", "")).split(".")
                for symbol in requested_symbols
            )
            implementation = not bool(
                parts & {"examples", "example", "benchmarks", "docs", "doc", "tests", "test"}
            )
            evidence_origin = str(item.get("origin", ""))
            source_evidence_rank = (
                0 if evidence_origin == "SEARCH_RESULT_SOURCE"
                else 1 if evidence_origin == "RECENT_READ_FILE"
                else 2
            )
            explicit_symbol = not str(item.get("symbol", "")).startswith("<")
            width = max(
                0,
                int(item.get("snippet_end_line", item.get("end_line", 0)))
                - int(item.get("snippet_start_line", item.get("start_line", 0))),
            )
            # Recent implementation reads are the most reliable expected_source
            # anchors; narrow snippets are preferred within the same source.
            return (
                0 if implementation else 1,
                0 if contains_requested_symbol else 1,
                0 if owns_named_symbol else 1,
                source_evidence_rank,
                0 if explicit_symbol else 1,
                width,
                relative,
            )

        snippets = sorted(snippets, key=source_priority)
        selected: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int, str]] = set()
        total_characters = 0
        for item in snippets:
            content = str(item.get("content", ""))
            relative_path = str(item.get("relative_path", ""))
            if not relative_path or not content:
                continue
            start_line = int(item.get("snippet_start_line", item.get("start_line", 1)))
            end_line = int(item.get("snippet_end_line", item.get("end_line", start_line)))
            key = (relative_path, start_line, end_line, content)
            if key in seen:
                continue
            if total_characters + len(content) > 14000:
                continue
            seen.add(key)
            selected.append({
                "relative_path": relative_path,
                "start_line": int(
                    item.get("snippet_start_line", item.get("start_line", 1))
                ),
                "end_line": int(
                    item.get("snippet_end_line", item.get("end_line", 1))
                ),
                "symbol": str(item.get("symbol", "")),
                "content": content,
            })
            total_characters += len(content)
            if len(selected) >= 5:
                break
        return tuple(selected)

    @staticmethod
    def _quality_recovery_sources(
        tools: RepairToolExecutor,
        packet=None,
    ) -> tuple[dict[str, Any], ...]:
        """Materialize exact source slices for a rejected staged edit set.

        A rejected patch is often rejected precisely because it touched only one
        member of an explicitly named multi-method contract.  Supplying another
        broad file window leaves the model free to copy stale/no-op methods back
        into ``replace_staged_edits``.  Keep the bounded file anchor for context,
        but also extract each explicitly named existing method as a small,
        line-addressed source slice.  This is mechanical source projection, not
        synthesized code, and therefore remains safe for every repository.
        """

        packet_text = "\n".join((
            str(getattr(packet, "issue_text", "") or ""),
            *map(str, getattr(packet, "discussion_evidence", ()) or ()),
        ))
        quality_error = str(tools.staged_quality_error or "")
        prefix_composition_recovery = quality_error.startswith((
            "STAGED_PATCH_USES_REQUIRED_PREFIX_ONLY_AS_FALLBACK",
            "STAGED_PATCH_REVERSES_EXPLICIT_BRANCH_RELATION",
        ))
        # Collect names from the validator's explicit list and from the public
        # issue/discussion.  Intersecting with real definitions below prevents
        # prose words ("changes", "need", ...) from becoming edit targets.
        explicit_multi_methods = set(_explicit_multi_method_names(quality_error))
        mentioned_names = (
            set(explicit_multi_methods)
            if explicit_multi_methods
            else set(re.findall(r"\b[A-Za-z_]\w*\b", packet_text))
        )
        if not explicit_multi_methods:
            mentioned_names.update(
                re.findall(r"\b[A-Za-z_]\w*\b", quality_error)
            )

        definitions = tuple(getattr(packet, "likely_definitions", ()) or ())
        primary_path = (
            str(definitions[0].get(
                "relative_path", definitions[0].get("path", ""),
            ))
            if definitions else ""
        )
        rejected_owner_names: set[str] = set()
        shared_utility_recovery = quality_error.startswith(
            "STAGED_PATCH_EXPANDS_SCOPED_FIX_INTO_SHARED_UTILITY"
        )
        for edit in tools.staged_edits if shared_utility_recovery else ():
            try:
                edit_path = tools._path(edit.relative_path)
                edit_tree = ast.parse(
                    edit_path.read_text(encoding="utf-8", errors="replace"),
                    filename=edit.relative_path,
                )
            except (OSError, SyntaxError, ValueError):
                continue
            containing = [
                node for node in ast.walk(edit_tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and int(getattr(node, "lineno", 0)) <= edit.start_line
                <= int(getattr(node, "end_lineno", getattr(node, "lineno", 0)))
            ]
            if containing:
                owner = min(
                    containing,
                    key=lambda node: int(getattr(node, "end_lineno", 0))
                    - int(getattr(node, "lineno", 0)),
                )
                rejected_owner_names.add(owner.name)

        path_candidates = [
            *sorted(tools.rejected_staged_paths),
            *(edit.relative_path for edit in tools.staged_edits),
        ]
        for item in definitions:
            path = str(item.get("relative_path", item.get("path", "")))
            if path:
                path_candidates.append(path)
        paths = tuple(dict.fromkeys(path_candidates))[:8]
        sources: list[dict[str, Any]] = []
        method_sources: list[dict[str, Any]] = []
        supporting_sources: list[dict[str, Any]] = []
        for relative in paths:
            try:
                path = tools._path(relative)
                lines = path.read_text(
                    encoding="utf-8", errors="replace",
                ).splitlines()
            except (OSError, ValueError):
                continue
            if not lines:
                continue
            edit_lines = [edit.start_line for edit in tools.staged_edits
                          if edit.relative_path == relative]
            anchor = min(edit_lines, default=1)
            start = 1 if len(lines) <= 240 else max(1, anchor - 40)
            end = len(lines) if len(lines) <= 240 else min(len(lines), start + 219)
            sources.append({
                "relative_path": relative,
                "start_line": start,
                "end_line": end,
                "content": "\n".join(lines[start - 1:end]),
                "origin": "MANDATORY_QUALITY_RECOVERY_SOURCE",
            })
            # Use the AST to locate exact method boundaries.  Include only
            # methods explicitly named by the issue/validator and keep the
            # source small enough that the recovery prompt can show every one.
            try:
                tree = ast.parse("\n".join(lines), filename=relative)
            except SyntaxError:
                tree = None
            if tree is None:
                continue
            file_has_explicit_multi_method = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in explicit_multi_methods
                for node in ast.walk(tree)
            )
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                node_source = "\n".join(lines[
                    int(getattr(node, "lineno", 1)) - 1:
                    int(getattr(node, "end_lineno", node.lineno))
                ])
                composes_values = any(
                    (
                        isinstance(child, ast.Call)
                        and (
                            (
                                isinstance(child.func, ast.Name)
                                and child.func.id.lower().endswith("join")
                            )
                            or (
                                isinstance(child.func, ast.Attribute)
                                and child.func.attr.lower().endswith("join")
                            )
                        )
                    )
                    or (
                        isinstance(child, ast.BinOp)
                        and isinstance(child.op, (ast.Add, ast.Mod))
                    )
                    for child in ast.walk(node)
                )
                prefix_composition_owner = bool(
                    prefix_composition_recovery
                    and composes_values
                    and re.search(r"\b(?:url|uri|path|prefix|base)\w*\b", node_source)
                )
                calls_rejected_owner = bool(
                    relative == primary_path
                    and rejected_owner_names
                    and any(
                        (
                            isinstance(call.func, ast.Name)
                            and call.func.id in rejected_owner_names
                        ) or (
                            isinstance(call.func, ast.Attribute)
                            and call.func.attr in rejected_owner_names
                        )
                        for call in ast.walk(node)
                        if isinstance(call, ast.Call)
                    )
                )
                if (
                    node.name not in mentioned_names
                    and not calls_rejected_owner
                    and not prefix_composition_owner
                ):
                    continue
                start_line = int(getattr(node, "lineno", 1))
                end_line = int(getattr(node, "end_lineno", start_line))
                # Keep the method's decorators when present; they can affect
                # the actual callable contract during recovery.
                decorators = list(getattr(node, "decorator_list", ()))
                if decorators:
                    start_line = min(
                        start_line,
                        min(int(getattr(item, "lineno", start_line)) for item in decorators),
                    )
                if end_line < start_line or end_line - start_line > 180:
                    continue
                method_sources.append({
                    "relative_path": relative,
                    "start_line": start_line,
                    "end_line": end_line,
                    "symbol": node.name,
                    "content": "\n".join(lines[start_line - 1:end_line]),
                    "origin": (
                        "MANDATORY_PREFIX_COMPOSITION_OWNER"
                        if prefix_composition_owner
                        else "MANDATORY_EXPLICIT_METHOD_SOURCE"
                    ),
                })
            if (
                (relative == primary_path and rejected_owner_names)
                or file_has_explicit_multi_method
            ):
                import_end = 0
                for node in tree.body:
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        import_end = max(
                            import_end,
                            int(getattr(node, "end_lineno", node.lineno)),
                        )
                    elif import_end and not isinstance(node, ast.Expr):
                        break
                if import_end:
                    supporting_sources.append({
                        "relative_path": relative,
                        "start_line": 1,
                        "end_line": import_end,
                        "symbol": "<module-imports>",
                        "content": "\n".join(lines[:import_end]),
                        "origin": (
                            "MANDATORY_MULTI_METHOD_IMPORT_SOURCE"
                            if file_has_explicit_multi_method
                            else "MANDATORY_LOCAL_CALLSITE_IMPORT_SOURCE"
                        ),
                    })
        # Method slices come first: the model must see the complete set of
        # named methods before the larger file context.  De-duplicate aliases
        # introduced by repeated packet projections.
        unique_methods: list[dict[str, Any]] = []
        seen_methods: set[tuple[str, str, int, int]] = set()
        method_sources.sort(key=lambda item: (
            0 if item.get("origin") == "MANDATORY_PREFIX_COMPOSITION_OWNER" else 1,
            str(item.get("relative_path", "")),
            int(item.get("start_line", 0)),
        ))
        for item in method_sources:
            key = (
                str(item["relative_path"]), str(item["symbol"]),
                int(item["start_line"]), int(item["end_line"]),
            )
            if key in seen_methods:
                continue
            seen_methods.add(key)
            unique_methods.append(item)
        ordered = unique_methods[:12] + supporting_sources[:2]
        if not explicit_multi_methods:
            ordered += sources[:5]
        selected: list[dict[str, Any]] = []
        total_characters = 0
        for item in ordered:
            content = str(item.get("content", ""))
            if not content or total_characters + len(content) > 16_000:
                continue
            selected.append(item)
            total_characters += len(content)
        return tuple(selected)

    @classmethod
    def _repair_anchor(
        cls,
        context,
        conversation: GeneratorConversation | None = None,
        tools: RepairToolExecutor | None = None,
    ) -> dict[str, Any]:
        """Keep normative and executable evidence across message compaction."""

        unresolved_requirements = tuple(
            row for row in getattr(context, "requirement_coverage", ())
            if str(row.get("status", ""))
            in {"FAILING", "UNBOUND", "UNTESTABLE", "PRESERVATION_RISK", "UNKNOWN"}
        )
        return {
            "issue": context.issue,
            "public_discussion_context": getattr(
                context, "public_discussion", ""
            ),
            "authority_rule": (
                "primary issue and executable public contracts are normative; "
                "public discussion is provisional"
            ),
            "requirement_coverage": unresolved_requirements,
            "active_target_check": getattr(context, "active_target_check", None),
            "failed_checks": getattr(context, "failed_checks", ()),
            "counterexamples": getattr(context, "counterexamples", ()),
            "preferred_action_families": getattr(
                context, "suggested_action_families", ()
            ),
            "failure_signature": context.failure_signature,
            "first_project_frame": context.first_project_frame,
            "causal_cut_candidates": context.causal_cut_candidates[:3],
            "exact_source_snippets": cls._exact_source_anchor(
                context, conversation, tools
            ),
            "baseline_output": context.baseline_output,
            "patched_output": getattr(context, "patched_output", None),
            "preservation_checks": getattr(context, "preservation_checks", ()),
            "prohibited_mechanisms": getattr(
                context, "prohibited_mechanisms", ()
            ),
            "repair_intent": getattr(context, "repair_intent", None),
            "mechanical_recovery_anchors": tuple(
                getattr(tools, "mechanical_recovery_anchors", ())
            ),
        }

    @staticmethod
    def _requested_symbol_status(state, symbols: tuple[str, ...]) -> tuple[
        tuple[str, ...], tuple[str, ...], tuple[str, ...]
    ]:
        """Partition slice requests into active, expandable, and unknown symbols."""

        resolver = getattr(state.program_graph, "resolve_symbol", None)
        repository_symbols = getattr(
            getattr(state, "repository_index", None), "symbols", {}
        )
        active: list[str] = []
        expandable: list[str] = []
        unknown: list[str] = []
        for symbol in symbols:
            leaf = symbol.rsplit(".", 1)[-1]
            if callable(resolver) and (resolver(symbol) or resolver(leaf)):
                active.append(symbol)
            elif repository_symbols.get(symbol) or repository_symbols.get(leaf):
                expandable.append(symbol)
            else:
                unknown.append(symbol)
        return tuple(active), tuple(expandable), tuple(unknown)

    def _finalize_staged_initial_patch(
        self,
        *,
        state,
        context,
        conversation: GeneratorConversation,
        tools: RepairToolExecutor,
        mechanism: str,
        summary: str,
    ) -> tuple[str, str, bool]:
        """Review an edit staged by a final structural recovery call."""

        if (
            not tools.staged_edits
            or tools.finished_staged_version == tools.staged_edit_version
        ):
            return mechanism, summary, False
        packet = build_initial_repair_packet(state, context=context)
        quality_review_rejected = bool(tools.staged_quality_rejected)
        last_quality_error: str | None = None
        for review_attempt in range(4):
            preview = tools.show_current_diff()
            conversation.messages.append({
                "role": "system",
                "content": json.dumps({
                    "event": "SYSTEM_STAGED_DIFF_PREVIEW",
                    "staged_edit_version": preview["staged_edit_version"],
                    "review_is_current": preview["review_is_current"],
                    "preview": preview,
                }, sort_keys=True),
            })
            review_payload = {
                "instruction": (
                    "This is the mandatory final review of the one persistent first "
                    "patch. Inspect the exact staged diff. Finish only if every edit "
                    "has a causal role and the complete issue is covered. Check "
                    "optional key/None paths, mutation of caller-owned state, reverse "
                    "protocol dispatch, sibling predicates/index bounds, and related "
                    "public tests. Before the final review you may replace the entire "
                    "staged edit set. Do not append a compensating edit or create a "
                    "second candidate."
                ),
                "requirement_checklist": packet.requirement_checklist.to_dict(),
                "discussion_evidence": packet.discussion_evidence[:3],
                "direct_callers": packet.direct_callers[:2],
                "related_public_tests": packet.related_public_tests[:2],
                "uncertainty": packet.uncertainty,
                "staged_patch_preview": preview,
            }
            if last_quality_error:
                review_payload.update({
                    "previous_quality_error": last_quality_error,
                    "required_correction": self._mechanical_correction_instruction(
                        last_quality_error,
                    ),
                    "mandatory_quality_recovery_source": (
                        self._quality_recovery_sources(tools, packet)
                    ),
                    "rejected_staged_diff_do_not_reuse": (
                        tools.last_rejected_staged_diff
                    ),
                })
            conversation.messages.append({
                "role": "user", "content": json.dumps(review_payload),
            })
            allowed = {"finish_revision"}
            if review_attempt < 3:
                allowed.add("replace_staged_edits")
            review_schemas = _tool_schema(frozenset(allowed))
            try:
                constrained_call = getattr(
                    self.transport, "call_with_tool_choice", None,
                )
                review_message = (
                    constrained_call(
                        self._structural_request_messages(conversation),
                        review_schemas,
                        "required",
                    )
                    if callable(constrained_call)
                    else self.transport(
                        self._structural_request_messages(conversation),
                        review_schemas,
                    )
                )
            except GeneratorBlockedExternal:
                raise
            except Exception as exc:
                raise GeneratorBlockedExternal(
                    "deepseek_initial_patch_review", exc,
                ) from exc
            conversation.messages.append(review_message)
            replaced = False
            self_rejected = False
            for call in review_message.get("tool_calls") or ():
                function = call.get("function", {})
                name = str(function.get("name", ""))
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if name == "finish_revision":
                        summary = str(arguments["summary"])
                        review_error = _initial_patch_review_error(
                            packet, preview.get("staged_diff", ""), summary,
                            conversation,
                        )
                        if _finish_summary_rejects_patch(summary) or review_error:
                            self_rejected = True
                            quality_review_rejected = True
                            last_quality_error = _mark_staged_quality_rejection(
                                tools,
                                review_error,
                                fallback=(
                                    "STAGED_PATCH_SELF_REJECTED: the model's final "
                                    "summary explicitly rejects the staged patch"
                                ),
                            )
                            output = {
                                "error": "STAGED_PATCH_SELF_REJECTED",
                                "quality_error": review_error,
                                "instruction": (
                                    "The final review did not establish a complete "
                                    "causal behavior change. Replace the entire staged "
                                    "edit set with the root-cause repair before finishing. "
                                    + (review_error or "")
                                ),
                            }
                        else:
                            output = tools.finish_revision(summary)
                    elif name == "replace_staged_edits" and review_attempt < 3:
                        # Replacement is an explicit rejection of the current
                        # reviewed patch. Persist it before applying the new
                        # edit set so a stale source error cannot revive the old
                        # staged patch as a valid first checkpoint.
                        quality_review_rejected = True
                        _mark_staged_quality_rejection(
                            tools,
                            None,
                            fallback="STAGED_PATCH_REPLACEMENT_REQUESTED_DURING_REVIEW",
                        )
                        mechanism = str(arguments.pop("mechanism", mechanism))
                        edits = tuple(
                            ProposedEdit(**item) for item in arguments["edits"]
                        )
                        output = self._stage_edits_with_mechanical_completion(
                            tools, edits, replace_existing=True,
                        )
                        replaced = True
                    else:
                        output = {
                            "error": "INVALID_TOOL",
                            "requested_tool": name,
                            "allowed_tools": sorted(allowed),
                        }
                except (
                    OSError, ValueError, KeyError, TypeError,
                    subprocess.SubprocessError, json.JSONDecodeError,
                ) as exc:
                    output = {"error": f"{type(exc).__name__}: {exc}"}
                conversation.messages.append({
                    "role": "tool",
                    "name": name,
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(output, sort_keys=True),
                })
            if tools.finished_staged_version == tools.staged_edit_version:
                break
            if self_rejected:
                conversation.messages.append({
                    "role": "user",
                    "content": (
                        "The final review rejected the staged diff because it does not "
                        "contain the claimed causal behavior change. Replace the entire "
                        "staged edit set now; do not finish, retain a no-op, or append "
                        "an uncalled helper. Validator evidence: "
                        + str(last_quality_error or "")
                        + " Required correction: "
                        + self._mechanical_correction_instruction(
                            str(last_quality_error or ""),
                        )
                        + " The next review payload includes exact source slices for "
                        "every mechanically named owner; change all required existing "
                        "methods in one replacement edit set."
                    ),
                })
                continue
            if not replaced:
                break
        if (
            tools.staged_edits
            and tools.finished_staged_version != tools.staged_edit_version
        ):
            try:
                correction = _prune_rejected_alternate_layer_edits(
                    packet, tools, last_quality_error,
                )
                if correction is not None:
                    corrected_preview = tools.show_current_diff()
                    corrected_summary = (
                        "Mechanically removed only the alternate-layer edits that "
                        "the causal source-anchor review rejected. Retained and "
                        "reviewed the executable repair in the strongest evidenced "
                        "owner; unchanged layers preserve their existing public and "
                        "serialization behavior."
                    )
                    corrected_error = _initial_patch_review_error(
                        packet,
                        corrected_preview.get("staged_diff", ""),
                        corrected_summary,
                        conversation,
                    )
                    if corrected_error is None:
                        finish_output = tools.finish_revision(corrected_summary)
                        summary = corrected_summary
                        conversation.messages.append({
                            "role": "system",
                            "content": json.dumps({
                                "event": (
                                    "SYSTEM_CAUSAL_ANCHOR_EDIT_SET_CORRECTION"
                                ),
                                "correction": correction,
                                "finish": finish_output,
                                "staged_patch_preview": corrected_preview,
                            }, sort_keys=True),
                        })
                        if hasattr(state, "runtime_metrics"):
                            state.runtime_metrics[
                                "causal_anchor_edit_set_corrections"
                            ] = int(state.runtime_metrics.get(
                                "causal_anchor_edit_set_corrections", 0,
                            )) + 1
                    else:
                        last_quality_error = corrected_error
            except (OSError, ValueError, TypeError) as exc:
                conversation.messages.append({
                    "role": "system",
                    "content": json.dumps({
                        "event": "SYSTEM_CAUSAL_ANCHOR_CORRECTION_REJECTED",
                        "error": f"{type(exc).__name__}: {exc}",
                    }, sort_keys=True),
                })
        return (
            mechanism,
            summary,
            quality_review_rejected
            and tools.finished_staged_version != tools.staged_edit_version,
        )

    def _invoke(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor, *, mode: str) -> GeneratorRevision:
        invocation_started = time.monotonic()
        completion_tokens = 0
        wall_time_exhausted = False
        token_budget_exhausted = False
        if hasattr(state, "runtime_metrics"):
            trajectory = getattr(state, "patch_trajectory", None)
            working = getattr(trajectory, "working_patch", None)
            budget_scope = {
                "generation_run_id": str(getattr(state, "generation_run_id", "")),
                "instance_id": str(getattr(state, "instance_id", "")),
                "trajectory_id": str(
                    getattr(working, "checkpoint_id", "initial-trajectory")
                ),
                "revision_id": stable_id(
                    "generator-budget-scope",
                    getattr(state, "generation_run_id", ""),
                    getattr(state, "instance_id", ""),
                    getattr(working, "checkpoint_id", "initial-trajectory"),
                    mode,
                    conversation.revision_count,
                ),
                "mode": mode,
                "max_tool_turns": self.max_tool_turns,
                "wall_time_seconds": self.max_wall_time_seconds,
                "completion_token_budget": self.max_completion_tokens,
            }
            state.runtime_metrics.setdefault(
                "generator_budget_scopes", [],
            ).append(budget_scope)
        context = build_repair_context(state, mode=mode)
        initial_packet_for_review = None
        evidence_fingerprint = content_hash({
            "issue": context.issue,
            "public_discussion": getattr(context, "public_discussion", ""),
            "working_diff": context.working_diff,
            "failed_checks": context.failed_checks,
            "counterexamples": context.counterexamples,
            "divergences": context.first_trace_divergences,
            "cuts": context.causal_repair_cuts,
            "causal_cut_candidates": context.causal_cut_candidates,
            "source_snippets": context.relevant_source_snippets,
            "active_slice_files": context.active_program_slice.get("files", ()),
            "active_slice_symbols": context.active_program_slice.get("symbols", ()),
        })
        repeated_evidence = (
            mode != "INITIAL"
            and conversation.last_evidence_fingerprint == evidence_fingerprint
        )
        if repeated_evidence:
            count = conversation.evidence_repeat_counts.get(evidence_fingerprint, 0) + 1
            conversation.evidence_repeat_counts[evidence_fingerprint] = count
            conversation.messages.append({
                "role": "system",
                "content": (
                    "The observable failure repeated. Do not stop. Diagnose why the "
                    "previous mechanism did not change behavior, preserve the current "
                    "working diff, and revise the causal mechanism. Use a different "
                    "mechanism when it is prohibited by the repair context. Repetition "
                    f"count for this evidence is {count}."
                ),
            })
        if conversation.revision_count >= self.max_revisions:
            return GeneratorRevision(
                revision_id=stable_id(
                    "generator-revision-budget", conversation.conversation_id,
                    conversation.revision_count,
                ),
                mechanism="causal_slice_rewrite", edits=(),
                summary="revision budget exhausted", context_requests=(),
                requested_public_checks=(), tool_turns=0,
                status="REVISION_BUDGET_EXHAUSTED",
            )
        conversation.last_evidence_fingerprint = evidence_fingerprint
        conversation.revision_count += 1
        conversation.current_working_diff = context.working_diff
        prompt_payload = context.to_dict()
        if mode in {"INITIAL", "INITIAL_RECOVERY"}:
            if mode == "INITIAL":
                initial_packet = build_initial_repair_packet(
                    state, context=context,
                )
                initial_packet_for_review = initial_packet
                definition_paths = tuple(dict.fromkeys(
                    str(item.get("relative_path", item.get("path", "")))
                    for item in initial_packet.likely_definitions
                    if item.get("relative_path", item.get("path", ""))
                ))
                caller_paths = tuple(dict.fromkeys(
                    str(item.get("relative_path", item.get("path", "")))
                    for item in initial_packet.direct_callers
                    if item.get("relative_path", item.get("path", ""))
                ))
                test_paths = tuple(dict.fromkeys(
                    str(item.get("relative_path", item.get("path", "")))
                    for item in initial_packet.related_public_tests
                    if item.get("relative_path", item.get("path", ""))
                ))
                tools.allowed_test_paths.update(test_paths)
                if hasattr(state, "runtime_metrics"):
                    state.runtime_metrics["initial_packet_evidence"] = {
                        "definition_paths": list(definition_paths),
                        "caller_paths": list(caller_paths),
                        "test_paths": list(test_paths),
                        "definition_search_completed": True,
                        "caller_search_completed": True,
                        "test_search_completed": True,
                        "evidence_ids": [
                            stable_id("initial-packet-definition", path)
                            for path in definition_paths
                        ] + [
                            stable_id("initial-packet-caller", path)
                            for path in caller_paths
                        ] + [
                            stable_id("initial-packet-test", path)
                            for path in test_paths
                        ],
                    }
                prompt_payload = {
                    "instruction": INITIAL_REPAIR_INSTRUCTION,
                    "initial_repair_packet": initial_packet.to_dict(),
                }
            else:
                initial_packet_for_review = self._persisted_initial_packet(
                    conversation,
                )
                tools.allowed_test_paths.update(
                    map(str, getattr(state, "runtime_metrics", {}).get(
                        "initial_packet_evidence", {},
                    ).get("test_paths", ()))
                )
                # Request compaction intentionally drops the long failed tool
                # exchange. The first user message retained by
                # _request_messages() is already the complete normative packet;
                # repeating it here can double the context and crowd out the edit.
                # Replay only mechanically sourced reads and unique validation
                # failures so recovery keeps useful code evidence without
                # anchoring on repeated invalid actions.
                failures = self._recent_mechanical_failures(conversation)
                quality_error = str(tools.staged_quality_error or "")
                prompt_payload = {
                    "instruction": INITIAL_ROOT_RECOVERY_INSTRUCTION,
                    "primary_task_reference": (
                        "Use the complete issue and requirement checklist in the "
                        "first user message of this persistent conversation."
                    ),
                    "inspected_source_evidence": self._exact_source_anchor(
                        context, conversation, tools,
                    ),
                    "mandatory_quality_recovery_source": (
                        self._quality_recovery_sources(
                            tools, initial_packet_for_review,
                        )
                    ),
                    "previous_mechanical_failures": failures,
                    "persistent_staged_quality_error": quality_error,
                    "current_staged_diff": tools._staged_diff(),
                    "rejected_staged_diff_do_not_reuse": (
                        tools.last_rejected_staged_diff
                    ),
                    "prohibited_staged_paths": sorted(
                        tools.prohibited_staged_paths
                    ),
                    "required_correction": self._mechanical_correction_instruction(
                        quality_error or (failures[-1] if failures else "")
                    ),
                }
        else:
            prompt_payload = {
                "instruction": REVISION_REPAIR_INSTRUCTION,
                "revision_packet": build_revision_packet(
                    state, context=context,
                ).to_dict(),
            }
        conversation.messages.append({"role": "user", "content": json.dumps(prompt_payload)})
        mechanism = "initial_issue_repair" if mode == "INITIAL" else "causal_slice_rewrite"
        requested_checks: list[str] = []
        summary = ""
        turns = 0
        invalid_synthesis_calls = 0
        invalid_feedback = ""
        quality_review_rejected = bool(tools.staged_quality_rejected)
        final_correction_used = False
        synthesis_edit_only = False
        force_synthesis = False
        review_prompted_versions: set[int] = set()
        evidence_anchor = json.dumps(
            self._repair_anchor(context, conversation, tools), sort_keys=True
        )
        while turns < self.max_tool_turns:
            remaining_wall_time = (
                self.max_wall_time_seconds
                - (time.monotonic() - invocation_started)
            )
            if remaining_wall_time <= 0:
                wall_time_exhausted = True
                break
            if completion_tokens >= self.max_completion_tokens:
                token_budget_exhausted = True
                break
            initial_review_turn = bool(
                mode in {"INITIAL", "INITIAL_RECOVERY"}
                and tools.staged_edits
                and tools.finished_staged_version != tools.staged_edit_version
            )
            if (
                initial_review_turn
                and tools.staged_edit_version not in review_prompted_versions
            ):
                preview = tools.show_current_diff()
                review_prompted_versions.add(tools.staged_edit_version)
                review_packet = initial_packet_for_review
                if review_packet is None:
                    review_packet = build_initial_repair_packet(
                        state, context=context,
                    )
                conversation.messages.append({
                    "role": "system",
                    "content": json.dumps({
                        "event": "SYSTEM_STAGED_DIFF_PREVIEW",
                        "instruction": (
                            "Review this exact uncheckpointed patch before finishing. "
                            "Check every requirement and witness, every changed file's "
                            "causal role, optional key/None behavior, mutation of "
                            "caller-owned state, reverse protocol dispatch, sibling "
                            "branches and index bounds. Use finish_revision only if "
                            "the edit set is complete and preservation-safe. Otherwise "
                            "use replace_staged_edits with the entire corrected edit "
                            "set; do not append a compensating edit."
                        ),
                        "preview": preview,
                        "strongest_discussion_evidence": (
                            review_packet.discussion_evidence[:3]
                        ),
                        "direct_callers": review_packet.direct_callers[:2],
                        "related_public_tests": (
                            review_packet.related_public_tests[:2]
                        ),
                    }, sort_keys=True),
                })
            turns += 1
            schemas = _tool_schema()
            synthesis_turn = (
                initial_review_turn
                or force_synthesis
                or
                turns == self.max_tool_turns
                or (
                    self.max_tool_turns >= 6
                    and turns >= self.max_tool_turns - 1
                )
                or (
                    4 <= self.max_tool_turns < 6
                    and turns == self.max_tool_turns - 1
                )
            )
            if initial_review_turn:
                available = set(_INITIAL_REVIEW_TOOL_NAMES)
                if (
                    tools.staged_quality_rejected_version
                    == tools.staged_edit_version
                    and _explicit_multi_method_names(
                        str(tools.staged_quality_error or "")
                    )
                ):
                    # The exact staged version was already proven incomplete by
                    # the multi-method contract. More browsing or another finish
                    # call cannot change it; require one complete replacement.
                    available = {"replace_staged_edits"}
                if tools.search_calls >= tools.max_search_calls:
                    available.difference_update({
                        "search_code", "find_callers", "find_references",
                    })
                if tools.read_calls >= tools.max_read_calls:
                    available.discard("read_file")
                if tools.public_check_calls >= tools.max_public_checks:
                    available.discard("run_public_check")
                if not tools.public_checks:
                    available.discard("run_public_check")
                schemas = _tool_schema(frozenset(available))
            elif synthesis_turn:
                # Rebuild the anchor after every inspection turn so the final
                # synthesis sees the exact source just returned by read_file,
                # rather than the low-confidence lexical snippets from the
                # initial context projection.
                evidence_anchor = json.dumps(
                    self._repair_anchor(context, conversation, tools), sort_keys=True
                )
                conversation.messages.append({
                    "role": "system",
                    "content": (
                        (
                            "This is the final tool turn for this revision. "
                            if turns == self.max_tool_turns
                            else "The remaining turns are reserved for synthesis and correction. "
                        )
                        + "Stop "
                        "browsing. Apply an evidence-constrained repair, finish an "
                        "already staged revision, request an exact missing slice, or "
                        "declare_blocker. If a required symbol is outside the active "
                        "slice, use request_program_slice with exact symbols. "
                        "Do not guess and do not call search/read/check tools. "
                        "Keep the proposed edit tied to this execution anchor: "
                        + evidence_anchor
                    ),
                })
                final_tools = _FINAL_TURN_TOOL_NAMES
                if synthesis_edit_only:
                    final_tools = final_tools - {"request_program_slice"}
                schemas = _tool_schema(final_tools)
            else:
                available = set(_ALLOWED_TOOL_NAMES)
                if tools.search_calls >= tools.max_search_calls:
                    available.difference_update({
                        "search_code", "find_callers", "find_references",
                    })
                if tools.read_calls >= tools.max_read_calls:
                    available.discard("read_file")
                if tools.public_check_calls >= tools.max_public_checks:
                    available.discard("run_public_check")
                if not tools.public_checks:
                    available.discard("run_public_check")
                schemas = _tool_schema(frozenset(available))
            available_names = frozenset(
                schema["function"]["name"] for schema in schemas
            )
            previous_timeout = getattr(
                self.transport, "request_timeout_seconds", None,
            )
            try:
                request_messages = self._request_messages(conversation)
                if previous_timeout is not None:
                    self.transport.request_timeout_seconds = min(
                        float(previous_timeout), max(1.0, remaining_wall_time),
                    )
                transport_records = getattr(self.transport, "calls", ())
                call_count = (
                    len(transport_records)
                    if isinstance(transport_records, (list, tuple)) else 0
                )
                constrained_call = getattr(
                    self.transport, "call_with_tool_choice", None
                )
                if synthesis_turn and callable(constrained_call):
                    # Require a decision among the advertised synthesis tools;
                    # never force a particular tool such as apply_edits.
                    message = constrained_call(
                        request_messages, schemas, "required"
                    )
                else:
                    message = self.transport(request_messages, schemas)
                transport_records = getattr(self.transport, "calls", ())
                if (
                    isinstance(transport_records, (list, tuple))
                    and len(transport_records) > call_count
                ):
                    usage = dict(transport_records[-1].get("usage", {}))
                    completion_tokens += int(usage.get(
                        "completion_tokens", usage.get("output_tokens", 0),
                    ) or 0)
            except GeneratorBlockedExternal:
                raise
            except Exception as exc:
                raise GeneratorBlockedExternal(
                    f"deepseek_{mode.lower()}", exc
                ) from exc
            finally:
                if previous_timeout is not None:
                    self.transport.request_timeout_seconds = previous_timeout
            if not isinstance(message, dict):
                raise GeneratorBlockedExternal(
                    f"deepseek_{mode.lower()}_response",
                    ValueError("model response is not an object"),
                )
            conversation.messages.append(message)
            calls = message.get("tool_calls") or ()
            if not calls:
                summary = str(message.get("content") or "revision response without finish tool")
                finish_reason = str(message.get("finish_reason", "")).lower()
                if finish_reason == "length":
                    force_synthesis = True
                    synthesis_edit_only = True
                    conversation.messages.append({
                        "role": "user",
                        "content": (
                            "The response was truncated. Preserve the current working "
                            "diff and return one compact structured tool action now. "
                            "Omit repeated reasoning. Browsing is now closed; use "
                            "apply_edits, finish_revision, or declare_blocker."
                        ),
                    })
                    if turns < self.max_tool_turns:
                        continue
                if turns < self.max_tool_turns and (
                    synthesis_turn or self.max_tool_turns >= 6
                ):
                    invalid_synthesis_calls += 1
                    conversation.messages.append({
                        "role": "user",
                        "content": (
                            "Analysis text without a tool call does not advance "
                            "this revision. On the next response call one advertised "
                            "repository tool. Before the final two turns you may use "
                            "search_code/read_file/inspect_symbol; in the final two "
                            "turns use only apply_edits, finish_revision, "
                            "request_program_slice, or declare_blocker. Do not repeat "
                            "the analysis. Use this execution and exact-source anchor: "
                            + evidence_anchor
                        ),
                    })
                    continue
                break
            finished = False
            invalid_synthesis_turn = False
            for call in calls:
                function = call.get("function", {})
                name = str(function.get("name", ""))
                arguments: dict[str, Any] = {}
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if name not in available_names:
                        if synthesis_turn:
                            invalid_synthesis_calls += 1
                            invalid_synthesis_turn = True
                        output = {
                            "error": "INVALID_TOOL",
                            "requested_tool": name,
                            "allowed_tools": sorted(available_names),
                            "instruction": "use one of the registered repository tools",
                        }
                    elif name == "apply_edits":
                        mechanism = str(arguments.pop("mechanism", mechanism))
                        edits = tuple(ProposedEdit(**item) for item in arguments["edits"])
                        output = self._stage_edits_with_mechanical_completion(
                            tools, edits,
                        )
                    elif name == "replace_staged_edits":
                        if mode in {"INITIAL", "INITIAL_RECOVERY"}:
                            quality_review_rejected = True
                            _mark_staged_quality_rejection(
                                tools,
                                None,
                                fallback=(
                                    "STAGED_PATCH_REPLACEMENT_REQUESTED_DURING_REVIEW"
                                ),
                            )
                        mechanism = str(arguments.pop("mechanism", mechanism))
                        edits = tuple(ProposedEdit(**item) for item in arguments["edits"])
                        output = self._stage_edits_with_mechanical_completion(
                            tools, edits, replace_existing=True,
                        )
                    elif name == "finish_revision":
                        summary = str(arguments["summary"])
                        review_error = None
                        if mode in {"INITIAL", "INITIAL_RECOVERY"}:
                            review_packet = initial_packet_for_review
                            if review_packet is None:
                                review_packet = build_initial_repair_packet(
                                    state, context=context,
                                )
                            preview = tools.show_current_diff()
                            review_error = _initial_patch_review_error(
                                review_packet,
                                preview.get("staged_diff", ""),
                                summary,
                                conversation,
                            )
                        if _finish_summary_rejects_patch(summary) or review_error:
                            quality_review_rejected = True
                            _mark_staged_quality_rejection(
                                tools,
                                review_error,
                                fallback=(
                                    "STAGED_PATCH_SELF_REJECTED: the summary admits "
                                    "the patch is not a complete causal fix"
                                ),
                            )
                            invalid_synthesis_calls += 1
                            invalid_synthesis_turn = True
                            invalid_feedback = review_error or (
                                "STAGED_PATCH_SELF_REJECTED: the summary admits the "
                                "patch is not a complete causal fix"
                            )
                            output = {
                                "error": "STAGED_PATCH_REVIEW_REJECTED",
                                "quality_error": review_error,
                                "instruction": (
                                    "Replace the complete staged edit set with a "
                                    "reachable root-cause change before finishing. "
                                    + invalid_feedback
                                ),
                            }
                        else:
                            output = tools.finish_revision(summary)
                            finished = True
                    elif name == "declare_blocker":
                        output = tools.declare_blocker(**arguments)
                        summary = str(arguments["reason"])
                        finished = True
                    elif name == "run_public_check":
                        requested_checks.append(str(arguments["check_id"]))
                        output = tools.run_public_check(**arguments)
                    elif name == "request_program_slice":
                        requested_symbols = tuple(map(str, arguments["symbols"]))
                        active, expandable, unknown = self._requested_symbol_status(
                            state, requested_symbols
                        )
                        if not expandable:
                            invalid_synthesis_calls += 1
                            invalid_synthesis_turn = True
                            error = (
                                "SYMBOL_NOT_FOUND" if unknown
                                else "CONTEXT_ALREADY_ACTIVE"
                            )
                            invalid_feedback = error + ": " + (
                                "unknown symbols cannot expand the repository slice"
                                if unknown
                                else "the requested symbols are already precise in "
                                "the active slice"
                            )
                            output = {
                                "error": error,
                                "active_symbols": list(active),
                                "unknown_symbols": list(unknown),
                                "instruction": (
                                    "Use the exact source snippets in the execution "
                                    "anchor to apply a minimal edit. Request only a "
                                    "real repository symbol outside the active slice, "
                                    "or declare a concrete blocker."
                                ),
                            }
                        else:
                            output = tools.request_program_slice(
                                symbols=expandable,
                                relation_kinds=arguments["relation_kinds"],
                            )
                            if active or unknown:
                                output["ignored_active_symbols"] = list(active)
                                output["ignored_unknown_symbols"] = list(unknown)
                            summary = "requested targeted program context"
                            finished = True
                    else:
                        output = getattr(tools, name)(**arguments)
                    if name == "read_file" and "path" in arguments:
                        conversation.inspected_files.add(str(arguments["path"]))
                        conversation.inspected_line_ranges.add(
                            f"{arguments['path']}:{arguments.get('start_line')}:{arguments.get('end_line')}"
                        )
                    if (
                        name in {"inspect_symbol", "find_callers", "find_references"}
                        and "symbol" in arguments
                    ):
                        conversation.inspected_symbols.add(str(arguments["symbol"]))
                except (
                    OSError, ValueError, KeyError, TypeError,
                    subprocess.SubprocessError,
                ) as exc:
                    output = {"error": f"{type(exc).__name__}: {exc}"}
                    if synthesis_turn and name in {
                        "apply_edits", "replace_staged_edits",
                    }:
                        invalid_synthesis_calls += 1
                        invalid_synthesis_turn = True
                        invalid_feedback = output["error"]
                conversation.messages.append({
                    "role": "tool", "name": name,
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(output, sort_keys=True),
                })
            if finished:
                break
            if invalid_synthesis_turn and (
                turns < self.max_tool_turns
                or not final_correction_used
            ):
                # Repeating the same malformed edit for the remainder of a long
                # generation budget anchors the model on that invalid mechanism.
                # Two evidence-bearing attempts are enough; the compact structural
                # correction below gets a fresh synthesis context.
                if invalid_synthesis_calls >= 2:
                    break
                synthesis_edit_only = True
                if turns == self.max_tool_turns:
                    # A model response outside the advertised final schema is
                    # not useful work. Reuse the final budget slot once for a
                    # correction, while keeping the reported tool-turn budget
                    # bounded at max_tool_turns.
                    final_correction_used = True
                    turns -= 1
                conversation.messages.append({
                    "role": "user",
                    "content": (
                            "Your previous synthesis tool call did not produce a "
                            "mechanically valid staged edit. Browsing is closed; use "
                            "the source already returned. "
                            "On the next and final response, call exactly one advertised "
                            "tool: apply_edits, finish_revision, request_program_slice, "
                            "or declare_blocker. Do not call any search, read, inspect, "
                            "reference, caller, or public-check tool. The edit must "
                            "directly address this execution anchor. If the feedback is "
                            "CONTEXT_ALREADY_ACTIVE or the anchor contains exact source "
                            "snippets, do not request the same slice again: choose "
                            "apply_edits or declare_blocker. Tool feedback: "
                            + (invalid_feedback or "invalid synthesis tool call")
                        + ". Required correction: "
                        + self._mechanical_correction_instruction(
                            invalid_feedback or "invalid synthesis tool call"
                        )
                        + ". Execution anchor: " + evidence_anchor
                    ),
                })
        conversation.attempted_mechanisms.append(mechanism)

        # A long initial pass can spend its last turns on source browsing or be
        # truncated immediately before emitting the edit.  Preserve the
        # persistent working tree and give the model one compact, tool-only
        # synthesis opportunity.  This is deliberately limited to initial
        # generation and to production-sized budgets; short deterministic test
        # transports and revision calls retain their normal turn limits.
        blocker_reason = str((tools.blocker or {}).get("reason", "")).lower()
        model_declared_blocker = bool(tools.blocker) and not any(
            marker in blocker_reason
            for marker in ("repository", "worktree", "api", "cannot save", "unavailable")
        )
        empty_patch_recovery = (
            mode in {"INITIAL", "INITIAL_RECOVERY"}
            or (mode == "ROOT_RECOVERY" and not context.working_diff)
        )
        if (
            empty_patch_recovery
            and self.max_tool_turns >= 6
            and self.requested_max_tool_turns >= 6
            and not wall_time_exhausted
            and not token_budget_exhausted
            and not tools.staged_edits
            and (tools.blocker is None or model_declared_blocker)
            and (
                not tools.context_requests
                or model_declared_blocker
                or bool(context.relevant_source_snippets)
            )
        ):
            structural_preferred_path = ""
            if model_declared_blocker:
                # A model-level "insufficient evidence" declaration is not an
                # external failure.  Keep the conversation recoverable and let
                # the bounded structural correction or root recovery decide.
                tools.blocker = None
            correction_prompt = (
                "This repair pass did not stage a patch. This is a structural "
                "correction, not a request for more browsing. Preserve any current "
                "working diff, use only the exact source already returned, and call "
                "exactly one final tool: apply_edits for the smallest complete fix "
                "or request_program_slice for one concrete missing symbol. Actual "
                "repository/API failures are handled by the controller. Do not "
                "return prose or finish_revision without edits. Execution anchor: "
                + json.dumps(
                    self._compact_structural_anchor(
                        context, conversation, tools,
                    ),
                    sort_keys=True,
                )
            )
            conversation.messages.append({"role": "user", "content": correction_prompt})
            correction_schemas = _tool_schema(frozenset({
                "apply_edits", "request_program_slice",
            }))
            try:
                constrained_call = getattr(
                    self.transport, "call_with_tool_choice", None
                )
                correction_message = (
                    constrained_call(
                        self._structural_request_messages(conversation),
                        correction_schemas,
                        "required",
                    )
                    if callable(constrained_call)
                    else self.transport(
                        self._structural_request_messages(conversation),
                        correction_schemas,
                    )
                )
            except GeneratorBlockedExternal:
                raise
            except Exception as exc:
                raise GeneratorBlockedExternal(
                    f"deepseek_{mode.lower()}_structural_correction", exc
                ) from exc
            conversation.messages.append(correction_message)
            correction_calls = correction_message.get("tool_calls") or ()
            accepted_context_expansion = False
            for call in correction_calls:
                function = call.get("function", {})
                name = str(function.get("name", ""))
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if name == "apply_edits":
                        mechanism = str(arguments.pop("mechanism", mechanism))
                        raw_edits = list(arguments["edits"])
                        if raw_edits:
                            structural_preferred_path = str(
                                raw_edits[0].get("relative_path", "")
                            )
                        edits = tuple(
                            ProposedEdit(**item) for item in raw_edits
                        )
                        output = self._stage_edits_with_mechanical_completion(
                            tools, edits,
                        )
                        summary = "structural correction staged the initial patch"
                    elif name == "request_program_slice":
                        requested_symbols = tuple(map(str, arguments["symbols"]))
                        active, expandable, unknown = self._requested_symbol_status(
                            state, requested_symbols
                        )
                        if not expandable:
                            output = {
                                "error": (
                                    "CONTEXT_ALREADY_ACTIVE" if active
                                    else "SYMBOL_NOT_FOUND"
                                ),
                                "active_symbols": list(active),
                                "unknown_symbols": list(unknown),
                                "instruction": (
                                    "Use the exact implementation source already in "
                                    "the correction anchor and call apply_edits."
                                ),
                            }
                            invalid_synthesis_calls += 1
                        else:
                            output = tools.request_program_slice(
                                symbols=expandable,
                                relation_kinds=arguments["relation_kinds"],
                            )
                            summary = "requested one targeted source slice"
                            accepted_context_expansion = True
                    else:
                        output = {
                            "error": "INVALID_TOOL",
                            "requested_tool": name,
                            "allowed_tools": ["apply_edits", "request_program_slice"],
                        }
                        invalid_synthesis_calls += 1
                except (
                    OSError, ValueError, KeyError, TypeError,
                    subprocess.SubprocessError, json.JSONDecodeError,
                ) as exc:
                    output = {"error": f"{type(exc).__name__}: {exc}"}
                    invalid_synthesis_calls += 1
                conversation.messages.append({
                    "role": "tool",
                    "name": name,
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(output, sort_keys=True),
                })

            # A model can spend the correction call on a redundant slice request
            # even though the exact implementation was already inspected. Give
            # that case one bounded convergence call using only the edit tool.
            if (
                not tools.staged_edits
                and tools.blocker is None
                and not accepted_context_expansion
                and (context.relevant_source_snippets or conversation.inspected_files)
                and any(
                    str(call.get("function", {}).get("name", ""))
                    in {"request_program_slice", "apply_edits"}
                    for call in correction_calls
                )
            ):
                conversation.messages.append({
                    "role": "user",
                    "content": (
                        "The correction request did not stage an edit. The repository "
                        "source is already available in the exact anchor and recent "
                        "read results. Call apply_edits now for the smallest complete "
                        "fix. Do not request another program slice. The replacement "
                        "must be non-empty and differ from expected_source. Use exact "
                        "expected_source text from this repair anchor: "
                        + json.dumps(
                            self._compact_structural_anchor(
                                context, conversation, tools,
                                preferred_path=structural_preferred_path,
                            ),
                            sort_keys=True,
                        )
                    ),
                })
                recovery_schemas = _tool_schema(frozenset({"apply_edits"}))
                try:
                    constrained_call = getattr(
                        self.transport, "call_with_tool_choice", None
                    )
                    recovery_message = (
                        constrained_call(
                            self._structural_request_messages(conversation),
                            recovery_schemas,
                            "required",
                        )
                        if callable(constrained_call)
                        else self.transport(
                            self._structural_request_messages(conversation),
                            recovery_schemas,
                        )
                    )
                except GeneratorBlockedExternal:
                    raise
                except Exception as exc:
                    raise GeneratorBlockedExternal(
                        f"deepseek_{mode.lower()}_structural_recovery", exc
                    ) from exc
                conversation.messages.append(recovery_message)
                recovery_failure = ""
                for call in (recovery_message.get("tool_calls") or ()):
                    function = call.get("function", {})
                    name = str(function.get("name", ""))
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                        if name == "apply_edits":
                            mechanism = str(arguments.pop("mechanism", mechanism))
                            raw_edits = list(arguments["edits"])
                            if raw_edits:
                                structural_preferred_path = str(
                                    raw_edits[0].get("relative_path", "")
                                )
                            edits = tuple(
                                ProposedEdit(**item) for item in raw_edits
                            )
                            output = self._stage_edits_with_mechanical_completion(
                                tools, edits,
                            )
                            summary = "structural recovery staged a working patch"
                        else:
                            output = {
                                "error": "INVALID_TOOL",
                                "requested_tool": name,
                                "allowed_tools": ["apply_edits"],
                            }
                            invalid_synthesis_calls += 1
                    except (
                        OSError, ValueError, KeyError, TypeError,
                        subprocess.SubprocessError, json.JSONDecodeError,
                    ) as exc:
                        output = {"error": f"{type(exc).__name__}: {exc}"}
                        recovery_failure = output["error"]
                        invalid_synthesis_calls += 1
                    conversation.messages.append({
                        "role": "tool",
                        "name": name,
                        "tool_call_id": call.get("id"),
                        "content": json.dumps(output, sort_keys=True),
                    })

                # The forced edit can still contain truncated JSON, stale line
                # numbers, or a no-op replacement. Give it one final bounded
                # retry through a narrower statement-only tool. This keeps the
                # exact same source and AST validation while preventing another
                # whole-definition copy or uncalled helper.
                if not tools.staged_edits and recovery_failure:
                    recovery_failure_lower = str(recovery_failure).lower()
                    local_statement_recovery = any(
                        marker in recovery_failure_lower
                        for marker in (
                            "selected execution path unchanged",
                            "only appends definition",
                            "shadowing definitions",
                            "duplicate member",
                            "duplicate assignment",
                            "no executable source change",
                            "no-op edit",
                            "unused direct import",
                        )
                    )
                    multi_edit_recovery = (
                        not local_statement_recovery
                        and any(
                        marker in str(recovery_failure).lower()
                        for marker in (
                            "rectangular-index", "column dimension",
                            "unresolved direct name", "import-only edit",
                            "same complete edit set",
                        )
                        )
                    )
                    retry_action = (
                        "apply_edits with one complete edit set containing every "
                        "required supporting import and behavior change"
                        if multi_edit_recovery
                        else "apply_statement_change with one unique existing "
                        "executable statement or small block"
                    )
                    conversation.messages.append({
                        "role": "user",
                        "content": (
                            "The required apply_edits call was mechanically rejected: "
                            + recovery_failure
                            + ". Correct that exact validation failure now. Call only "
                            + retry_action
                            + " copied exactly from the actual "
                            "source anchor and a changed replacement. A full definition "
                            "is allowed only if both fields contain the same single "
                            "existing name and type. Do not append a helper, duplicate "
                            "a definition, or resubmit original text. Required correction: "
                            + self._mechanical_correction_instruction(recovery_failure)
                            + " Repair anchor: "
                            + json.dumps(
                                self._compact_structural_anchor(
                                    context, conversation, tools,
                                    preferred_path=structural_preferred_path,
                                ),
                                sort_keys=True,
                            )
                        ),
                    })
                    retry_schemas = (
                        _tool_schema(frozenset({"apply_edits"}))
                        if multi_edit_recovery
                        else _statement_change_schema()
                    )
                    try:
                        constrained_call = getattr(
                            self.transport, "call_with_tool_choice", None
                        )
                        retry_message = (
                            constrained_call(
                                self._structural_request_messages(conversation),
                                retry_schemas,
                                "required",
                            )
                            if callable(constrained_call)
                            else self.transport(
                                self._structural_request_messages(conversation),
                                retry_schemas,
                            )
                        )
                    except GeneratorBlockedExternal:
                        raise
                    except Exception as exc:
                        raise GeneratorBlockedExternal(
                            f"deepseek_{mode.lower()}_structural_retry", exc
                        ) from exc
                    conversation.messages.append(retry_message)
                    statement_failure = ""
                    statement_arguments: dict[str, Any] = {}
                    for call in (retry_message.get("tool_calls") or ()):
                        function = call.get("function", {})
                        name = str(function.get("name", ""))
                        try:
                            arguments = json.loads(
                                function.get("arguments") or "{}"
                            )
                            statement_arguments = dict(arguments)
                            if name == "apply_edits":
                                mechanism = str(arguments.pop("mechanism", "boundary_repair"))
                                edits = tuple(
                                    ProposedEdit(**item)
                                    for item in arguments["edits"]
                                )
                                output = self._stage_edits_with_mechanical_completion(
                                    tools, edits,
                                )
                                summary = "structural retry staged a complete boundary repair"
                            elif name == "apply_statement_change":
                                mechanism, output = self._apply_statement_change(
                                    tools, arguments,
                                )
                                summary = (
                                    "structural retry staged a working patch"
                                )
                            else:
                                output = {
                                    "error": "INVALID_TOOL",
                                    "requested_tool": name,
                                    "allowed_tools": (
                                        ["apply_edits"]
                                        if multi_edit_recovery
                                        else ["apply_statement_change"]
                                    ),
                                }
                                invalid_synthesis_calls += 1
                        except (
                            OSError, ValueError, KeyError, TypeError,
                            subprocess.SubprocessError, json.JSONDecodeError,
                        ) as exc:
                            output = {"error": f"{type(exc).__name__}: {exc}"}
                            statement_failure = output["error"]
                            invalid_synthesis_calls += 1
                        conversation.messages.append({
                            "role": "tool",
                            "name": name,
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(output, sort_keys=True),
                        })

                    # A statement-only response can still repeat the rejected
                    # text or choose a block that occurs in multiple methods.
                    # Give the same initial trajectory one last, narrower
                    # mechanical correction. This is not a patch revision: no
                    # checkpoint exists until an edit passes source and AST
                    # validation.
                    if not tools.staged_edits and statement_failure:
                        statement_failure_lower = str(statement_failure).lower()
                        final_local_statement_recovery = any(
                            marker in statement_failure_lower
                            for marker in (
                                "selected execution path unchanged",
                                "only appends definition",
                                "shadowing definitions",
                                "duplicate member",
                                "duplicate assignment",
                                "no executable source change",
                                "no-op edit",
                                "unused direct import",
                            )
                        )
                        # A previous import-only/full-edit failure must not pin
                        # every later correction to the broad apply_edits tool.
                        # When the most recent candidate merely copied a body or
                        # appended a helper, first force a real local statement
                        # change. If that statement introduces a direct name, the
                        # mechanical import completion above will combine it with
                        # a repository-sourced import in the same edit set.
                        final_multi_edit_recovery = (
                            not final_local_statement_recovery
                            and (
                                multi_edit_recovery
                                or any(
                                marker in str(statement_failure).lower()
                                for marker in (
                                    "rectangular-index", "column dimension",
                                    "unresolved direct name", "import-only edit",
                                    "same complete edit set",
                                )
                                )
                            )
                        )
                        statement_path = str(
                            statement_arguments.get(
                                "relative_path", structural_preferred_path,
                            )
                        )
                        conversation.messages.append({
                            "role": "user",
                            "content": (
                                "The statement-only edit was mechanically rejected: "
                                + statement_failure
                                + ". Return exactly one "
                                + (
                                    "apply_edits call containing every related boundary edit"
                                    if final_multi_edit_recovery
                                    else "apply_statement_change call"
                                )
                                + ". "
                                "Preserve the earlier root-cause mechanism, but replace "
                                "a genuinely changed executable statement in the "
                                "existing path. Do not repeat either source field from "
                                "the rejected call. If the source block can occur more "
                                "than once, set owner_symbol to its existing function "
                                "or class and anchor_line to the line interval supplied "
                                "by the exact source snippet. Required correction: "
                                + self._mechanical_correction_instruction(
                                    statement_failure,
                                )
                                + " Compact repair evidence: "
                                + json.dumps(
                                    self._compact_structural_anchor(
                                        context, conversation, tools,
                                        preferred_path=statement_path,
                                    ),
                                    sort_keys=True,
                                )
                            ),
                        })
                        final_retry_schemas = (
                            _tool_schema(frozenset({"apply_edits"}))
                            if final_multi_edit_recovery
                            else _statement_change_schema()
                        )
                        try:
                            constrained_call = getattr(
                                self.transport, "call_with_tool_choice", None,
                            )
                            final_retry_message = (
                                constrained_call(
                                    self._structural_request_messages(conversation),
                                    final_retry_schemas,
                                    "required",
                                )
                                if callable(constrained_call)
                                else self.transport(
                                    self._structural_request_messages(conversation),
                                    final_retry_schemas,
                                )
                            )
                        except GeneratorBlockedExternal:
                            raise
                        except Exception as exc:
                            raise GeneratorBlockedExternal(
                                f"deepseek_{mode.lower()}_structural_final_retry",
                                exc,
                            ) from exc
                        conversation.messages.append(final_retry_message)
                        for call in (
                            final_retry_message.get("tool_calls") or ()
                        ):
                            function = call.get("function", {})
                            name = str(function.get("name", ""))
                            try:
                                arguments = json.loads(
                                    function.get("arguments") or "{}"
                                )
                                if name == "apply_edits" and final_multi_edit_recovery:
                                    mechanism = str(arguments.pop(
                                        "mechanism", "boundary_repair",
                                    ))
                                    edits = tuple(
                                        ProposedEdit(**item)
                                        for item in arguments["edits"]
                                    )
                                    output = self._stage_edits_with_mechanical_completion(
                                        tools, edits,
                                    )
                                    summary = (
                                        "final structural retry staged a complete "
                                        "multi-edit repair"
                                    )
                                elif name == "apply_statement_change":
                                    mechanism, output = self._apply_statement_change(
                                        tools, arguments,
                                    )
                                    summary = (
                                        "final structural retry staged a working patch"
                                    )
                                else:
                                    output = {
                                        "error": "INVALID_TOOL",
                                        "requested_tool": name,
                                        "allowed_tools": (
                                            ["apply_edits"]
                                            if final_multi_edit_recovery
                                            else ["apply_statement_change"]
                                        ),
                                    }
                                    invalid_synthesis_calls += 1
                            except (
                                OSError, ValueError, KeyError, TypeError,
                                subprocess.SubprocessError,
                                json.JSONDecodeError,
                            ) as exc:
                                output = {
                                    "error": f"{type(exc).__name__}: {exc}",
                                }
                                invalid_synthesis_calls += 1
                            conversation.messages.append({
                                "role": "tool",
                                "name": name,
                                "tool_call_id": call.get("id"),
                                "content": json.dumps(output, sort_keys=True),
                            })
        if (
            empty_patch_recovery
            and tools.staged_edits
            and not wall_time_exhausted
            and not token_budget_exhausted
        ):
            mechanism, summary, final_review_rejected = (
                self._finalize_staged_initial_patch(
                state=state,
                context=context,
                conversation=conversation,
                tools=tools,
                mechanism=mechanism,
                summary=summary,
                )
            )
            quality_review_rejected = (
                quality_review_rejected or final_review_rejected
                or tools.staged_quality_rejected
            )
        active_files = set(state.program_graph.file_index)
        for relative in sorted({item.relative_path for item in tools.staged_edits} - active_files):
            tools.context_requests.append(__import__(
                "reachpatch.program_graph.slice", fromlist=["ContextRequest"]
            ).ContextRequest(file_paths=(relative,), reason="generator_inspected_edit_target"))
        conversation.pending_context_requests.extend(tools.context_requests)
        revision = GeneratorRevision(
            revision_id=stable_id(
                "generator-revision", conversation.conversation_id,
                len(conversation.attempted_mechanisms), tools.staged_edits,
            ),
            mechanism=mechanism, edits=tuple(tools.staged_edits),
            summary=summary, context_requests=tuple(tools.context_requests),
            requested_public_checks=tuple(dict.fromkeys(requested_checks)),
            tool_turns=turns,
            status=(
                "STAGED_PATCH_REVIEW_REJECTED"
                if (
                    tools.staged_edits
                    and (quality_review_rejected or tools.staged_quality_rejected)
                    and tools.finished_staged_version
                    != tools.staged_edit_version
                )
                else "PROPOSED" if tools.staged_edits
                else "DECLARED_BLOCKER" if tools.blocker
                else "GENERATOR_WALL_TIME_EXHAUSTED" if wall_time_exhausted
                else "GENERATOR_TOKEN_BUDGET_EXHAUSTED" if token_budget_exhausted
                else "GENERATOR_BROWSE_LOOP" if invalid_synthesis_calls
                else "CONTEXT_ONLY"
            ),
        )
        return revision

    def generate_initial_patch(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor) -> GeneratorRevision:
        revision = self._invoke(state, conversation, tools, mode="INITIAL")
        if hasattr(state, "runtime_metrics"):
            readiness = assess_first_patch_readiness(state, conversation, revision)
            state.runtime_metrics["first_patch_readiness"] = readiness.to_dict()
        return revision

    def recover_initial_patch(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor) -> GeneratorRevision:
        revision = self._invoke(
            state, conversation, tools, mode="INITIAL_RECOVERY",
        )
        if hasattr(state, "runtime_metrics"):
            readiness = assess_first_patch_readiness(state, conversation, revision)
            state.runtime_metrics["first_patch_readiness"] = readiness.to_dict()
        return revision

    def repair_from_counterexamples(self, state, conversation: GeneratorConversation, packets, tools: RepairToolExecutor) -> GeneratorRevision:
        conversation.delivered_counterexamples.update(item.counterexample_id for item in packets)
        return self._invoke(state, conversation, tools, mode="COUNTEREXAMPLE_REPAIR")

    def root_recovery(self, state, conversation: GeneratorConversation, tools: RepairToolExecutor) -> GeneratorRevision:
        return self._invoke(state, conversation, tools, mode="ROOT_RECOVERY")

    def generate_target_reproduction(
        self,
        *,
        issue: str,
        public_discussion: str = "",
        source_context: tuple[dict[str, Any], ...],
        project_runner: str,
    ) -> dict[str, str] | None:
        """Request at most one public-API observation, without proposing edits."""

        schema = [{
            "type": "function",
            "function": {
                "name": "submit_reproduction",
                "description": (
                    "Submit public-API observation setup and one executable "
                    "post-fix relation. ReachPatch appends the only assertion so "
                    "the relation, rather than the known broken behavior, is the oracle"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "setup_source": {"type": "string"},
                        "observation_expression": {
                            "type": "string",
                            "description": (
                                "Python expression that is true exactly when the "
                                "issue's expected public behavior is satisfied"
                            ),
                        },
                        "expected_observation": {"type": "string"},
                    },
                    "required": [
                        "filename", "setup_source", "observation_expression",
                        "expected_observation",
                    ],
                },
            },
        }]
        runner_guidance = (
            "For Django, the runner supplies DJANGO_SETTINGS_MODULE; "
            "call django.setup() before defining any models, and give every "
            "standalone models.Model subclass a Meta class with an explicit "
            "app_label such as 'reachpatch_reproduction'. To inspect "
            "model system checks, call Model.check() on the model class, "
            "never field_descriptor.check() on Model.field. Do not treat "
            "missing app_label, AppRegistryNotReady, or DeferredAttribute "
            "AttributeError as the issue behavior; those indicate an invalid "
            "standalone reproduction. "
            if project_runner.lower() == "django" else ""
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Recover one executable failing target from public evidence. "
                    "Use only the primary issue, public discussion, and supplied "
                    "public source snippets. The primary_issue is the normative "
                    "authority. public_discussion may clarify a failure or propose "
                    "implementations, but it must not add an assertion that conflicts "
                    "with the primary issue. If comments disagree, retain only their "
                    "shared end-to-end behavior and the primary issue's contract. The "
                    "script runs from the repository root and must assert every "
                    "explicit behavioral relation and qualifier in the issue, not "
                    "only its simplest example. Keep those observations in one "
                    "bounded script so a partial patch can expose the next failing "
                    "assertion. Cover ordering, metadata, negative, boundary, and "
                    "symmetry relations whenever the issue explicitly names them. "
                    "Before submitting, audit every conjunct against the supplied "
                    "public text. Do not add a negative case, rejection rule, timing "
                    "constraint, or compatibility requirement unless the issue "
                    "explicitly states it. Do not combine proposal alternatives or "
                    "superseded discussion into mutually exclusive requirements. In "
                    "particular, when evidence says a deferred representation must "
                    "be preserved for serialization but evaluated by a consumer, do "
                    "not also require the stored representation itself to be eagerly "
                    "replaced; distinguish the producer, stored state, and consumer. "
                    "When discussion contains competing implementation proposals, "
                    "assert only the end-to-end public behavior shared by the viable "
                    "proposals. Do not assert the type or timing of an intermediate "
                    "value after __init__, the raw contents returned by deconstruct, "
                    "or eager-versus-lazy internal state unless the main issue defines "
                    "that detail as a public contract. Observe serialization through "
                    "the project's public serializer or writer output, not by guessing "
                    "an intermediate representation. "
                    "Return setup_source that only performs calls and records their "
                    "outputs, exceptions, warnings, or state. It must not contain "
                    "assert, raise, sys.exit, pytest expectation helpers, or an "
                    "oracle of its own. Return observation_expression as a Python "
                    "expression over those recorded values that is TRUE for the "
                    "behavior requested by the issue and FALSE for the described "
                    "baseline bug. Never encode or confirm the known incorrect "
                    "output as the expected relation. Every named boolean "
                    "observation recorded by setup_source must occur in the final "
                    "expression, including migration, serialization, metadata, and "
                    "backward-compatibility observations. expected_observation must "
                    "state the concrete expected relation in human-readable terms; "
                    "it cannot be True, success, or another generic label. Use a "
                    "descriptive test_*.py filename, never __init__.py or conftest.py. "
                    "ReachPatch will append the assertion itself. "
                    + runner_guidance
                    + "Do not read or "
                    "name hidden tests, test_patch, gold patches, harness fields, or "
                    "official outcomes. Do not edit project files. If the evidence "
                    "cannot support a concrete observation, return no tool call."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "primary_issue": issue,
                    "public_discussion": public_discussion,
                    "project_runner": project_runner,
                    "public_source_context": source_context,
                }, sort_keys=True),
            },
        ]
        rejection = ""
        for attempt in range(2):
            try:
                message = self.transport(messages, schema)
            except Exception as exc:
                raise GeneratorBlockedExternal(
                    "deepseek_target_reproduction", exc,
                ) from exc
            proposal, rejection = _compile_reproduction_message(
                message, primary_issue=issue, project_runner=project_runner,
            )
            if proposal is not None:
                return proposal
            if attempt == 0:
                messages.extend((
                    {
                        "role": "assistant",
                        "content": str(
                            message.get("content")
                            or "The previous response did not yield a valid reproduction."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "The candidate was mechanically rejected: "
                            f"{rejection}. Correct it once using the same public "
                            "evidence. Call submit_reproduction, keep all assertions "
                            "out of setup_source, and put the expected relation only "
                            "in observation_expression."
                        ),
                    },
                ))
        return None


def generate_initial_patch(state, conversation, agent: PersistentDeepSeekAgent, tools: RepairToolExecutor) -> GeneratorRevision:
    return agent.generate_initial_patch(state, conversation, tools)


def repair_from_counterexamples(state, conversation, packets, agent: PersistentDeepSeekAgent, tools: RepairToolExecutor) -> GeneratorRevision:
    return agent.repair_from_counterexamples(state, conversation, packets, tools)
