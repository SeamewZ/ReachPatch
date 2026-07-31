from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.enums import OutcomeStatus


@dataclass(frozen=True, slots=True)
class RepairContext(SerializableRecord):
    mode: str
    issue: str
    public_discussion: str
    working_diff: str
    active_target_check: dict[str, Any] | None
    baseline_output: dict[str, Any] | None
    patched_output: dict[str, Any] | None
    failure_signature: str | None
    first_project_frame: dict[str, Any] | None
    reproduction_command: tuple[str, ...]
    relevant_source_snippets: tuple[dict[str, Any], ...]
    causal_cut_candidates: tuple[dict[str, Any], ...]
    previous_revision: dict[str, Any] | None
    previous_failure_reason: str | None
    preservation_checks: tuple[dict[str, Any], ...]
    semantic_ambiguities: tuple[str, ...]
    requirement_coverage: tuple[dict[str, Any], ...]
    failed_checks: tuple[dict[str, Any], ...]
    counterexamples: tuple[dict[str, Any], ...]
    suggested_action_families: tuple[str, ...]
    first_trace_divergences: tuple[dict[str, Any], ...]
    active_program_slice: dict[str, Any]
    causal_repair_cuts: tuple[dict[str, Any], ...]
    impact_risks: tuple[dict[str, Any], ...]
    preserved_passes: tuple[dict[str, Any], ...]
    failed_mechanisms: tuple[str, ...]
    prohibited_mechanisms: tuple[str, ...]
    active_binding_units: tuple[dict[str, Any], ...]
    unresolved_frontier: tuple[dict[str, Any], ...]
    repair_intent: dict[str, Any] | None
    remaining_budget: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RequirementChecklist(SerializableRecord):
    change_requirements: tuple[str, ...]
    boundary_requirements: tuple[str, ...]
    exception_requirements: tuple[str, ...]
    preservation_requirements: tuple[str, ...]
    witnesses: tuple[str, ...]
    uncertainties: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InitialRepairPacket(SerializableRecord):
    issue_text: str
    requirement_checklist: RequirementChecklist
    likely_definitions: tuple[dict[str, Any], ...]
    direct_callers: tuple[dict[str, Any], ...]
    related_public_tests: tuple[dict[str, Any], ...]
    relevant_protocols: tuple[str, ...]
    expected_behavior: tuple[str, ...]
    preservation_behavior: tuple[str, ...]
    uncertainty: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FirstPatchReadiness(SerializableRecord):
    target_definition_read: bool
    caller_inspection_status: str
    test_or_contract_inspection_status: str
    root_cause_identified: bool
    requirements_accounted_for: bool
    preservation_risks_identified: bool
    final_diff_reviewed: bool
    supporting_tool_event_ids: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return all((
            self.target_definition_read,
            self.root_cause_identified,
            self.requirements_accounted_for,
            self.preservation_risks_identified,
            self.final_diff_reviewed,
        ))


@dataclass(frozen=True, slots=True)
class RevisionPacket(SerializableRecord):
    current_patch: str
    failing_requirement: str
    failure_kind: str
    concrete_input: Any
    expected_observation: Any
    actual_observation: Any
    failure_trace: str
    causal_source_slices: tuple[dict[str, Any], ...]
    causal_cut_ids: tuple[str, ...]
    changed_hunks: tuple[str, ...]
    preservation_contracts: tuple[str, ...]
    previous_failed_mechanisms: tuple[str, ...]
    prohibited_mechanisms: tuple[str, ...]


def _requirement_checklist(state) -> RequirementChecklist:
    leaves = getattr(getattr(state, "requirement_graph", None), "leaves", {})
    values = tuple(leaves.values()) if isinstance(leaves, dict) else tuple(leaves or ())
    changes: list[str] = []
    boundaries: list[str] = []
    exceptions: list[str] = []
    preservation: list[str] = []
    witnesses: list[str] = []
    uncertainties: list[str] = []
    for leaf in values:
        formula = str(getattr(leaf, "formula", leaf)).strip()
        lowered = formula.lower()
        if any(token in lowered for token in ("raise", "exception", "error")):
            exceptions.append(formula)
        elif any(token in lowered for token in ("empty", "none", "zero", "boundary", "when")):
            boundaries.append(formula)
        else:
            changes.append(formula)
        if getattr(leaf, "preservation_contract", {}):
            preservation.append(str(getattr(leaf, "preservation_contract")))
        witnesses.extend(map(str, getattr(leaf, "witnesses", ())))
        if getattr(leaf, "coverage_status", "") in {
            "NEEDS_GENERATOR_INTERPRETATION", "UNKNOWN",
        }:
            uncertainties.append(formula)
    issue = str(getattr(state, "runtime_config", {}).get(
        "primary_issue", "",
    )).strip()
    behavior_markers = re.compile(
        r"\b(?:should|must|expected|when|instead|needs?\s+to|has\s+to)\b",
        re.IGNORECASE,
    )
    preservation_markers = re.compile(
        r"\b(?:preserv|remain|existing|backward|compatib|unchanged|continue)\w*\b",
        re.IGNORECASE,
    )
    exception_markers = re.compile(
        r"\b(?:raise|exception|error|invalid|reject)\w*\b",
        re.IGNORECASE,
    )
    boundary_markers = re.compile(
        r"\b(?:when|empty|none|null|zero|nonzero|true|false|negative|positive|"
        r"before|after|instead|unless|all|every|any)\b",
        re.IGNORECASE,
    )
    issue_sentences = tuple(dict.fromkeys(
        part.strip(" \t-*#")
        for part in re.split(r"(?:\r?\n)+|(?<=[.!?])\s+", issue)
        if part.strip(" \t-*#")
    ))
    covered = "\n".join((*changes, *boundaries, *exceptions, *preservation)).lower()
    for sentence in issue_sentences:
        if not behavior_markers.search(sentence):
            continue
        if sentence.lower() in covered:
            continue
        if preservation_markers.search(sentence):
            preservation.append(sentence)
        elif exception_markers.search(sentence):
            exceptions.append(sentence)
        elif boundary_markers.search(sentence):
            boundaries.append(sentence)
        else:
            changes.append(sentence)
    witnesses.extend(
        match.group(0).strip()
        for pattern in (
            r"`[^`\n]+`",
            r"(?im)^\s*(?:for example|e\.g\.|example:)\s*.+$",
        )
        for match in re.finditer(pattern, issue)
    )
    if issue and not any((changes, boundaries, exceptions)):
        changes.append(issue)
        uncertainties.append(
            "The issue has no mechanically separable behavior sentence; interpret its full text."
        )
    return RequirementChecklist(
        change_requirements=tuple(dict.fromkeys(changes)),
        boundary_requirements=tuple(dict.fromkeys(boundaries)),
        exception_requirements=tuple(dict.fromkeys(exceptions)),
        preservation_requirements=tuple(dict.fromkeys(preservation)),
        witnesses=tuple(dict.fromkeys(witnesses)),
        uncertainties=tuple(dict.fromkeys(uncertainties)),
    )


def build_initial_repair_packet(state, context=None) -> InitialRepairPacket:
    context = context or build_repair_context(state, mode="INITIAL")
    checklist = _requirement_checklist(state)
    snippets = tuple(context.relevant_source_snippets[:8])
    program = getattr(state, "program_graph", None)
    checkpoint = getattr(state, "checkpoint", None)
    repository_index = getattr(state, "repository_index", None)
    repository = Path(
        getattr(checkpoint, "snapshot_tree", "")
        or getattr(repository_index, "repository_root", "")
        or "."
    )
    seed_ids: set[str] = set()
    leaves = getattr(getattr(state, "requirement_graph", None), "leaves", {})
    leaf_values = tuple(leaves.values()) if isinstance(leaves, dict) else tuple(
        leaves or ()
    )
    for leaf in leaf_values:
        for symbol in getattr(leaf, "entrypoint_hypotheses", ()):
            resolver = getattr(program, "resolve_symbol", None)
            if callable(resolver):
                seed_ids.update(map(str, resolver(str(symbol))))
    for snippet in snippets:
        relative = str(snippet.get("relative_path", snippet.get("path", "")))
        for node_id in getattr(program, "file_index", {}).get(relative, ()):
            seed_ids.add(str(node_id))
    caller_ids: tuple[str, ...] = ()
    if seed_ids and hasattr(program, "incoming"):
        from reachpatch.binding_graph.active import recover_direct_callers

        caller_ids = recover_direct_callers(program, seed_ids, max_depth=2)
    caller_snippets: list[dict[str, Any]] = []
    for node_id in caller_ids:
        node = getattr(program, "nodes", {}).get(node_id)
        if node is None:
            continue
        relative = str(node.attributes.get("file", ""))
        path = repository / relative
        if not relative or not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        line = max(1, int(node.attributes.get("line", 1)))
        end = max(line, int(node.attributes.get("end_line", line)))
        start = max(1, line - 3)
        stop = min(len(lines), end + 5)
        caller_snippets.append({
            "relative_path": relative,
            "start_line": start,
            "end_line": stop,
            "symbol": str(node.attributes.get("qualified_name", node.label)),
            "reason": "direct caller or value consumer",
            "content": "\n".join(lines[start - 1:stop]),
        })
        if len(caller_snippets) >= 4:
            break
    tests: list[dict[str, Any]] = [
        item for item in snippets
        if "test" in str(item.get("relative_path", item.get("path", ""))).lower()
    ]
    for relative in getattr(state, "runtime_config", {}).get(
        "visible_test_paths", (),
    ):
        path = repository / str(relative)
        if not path.is_file() or any(
            str(item.get("relative_path", item.get("path", ""))) == str(relative)
            for item in tests
        ):
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:200]
        tests.append({
            "relative_path": str(relative),
            "start_line": 1,
            "end_line": len(lines),
            "symbol": "<public-test>",
            "reason": "public test supplied by the instance",
            "content": "\n".join(lines),
        })
        if len(tests) >= 4:
            break
    return InitialRepairPacket(
        issue_text=context.issue,
        requirement_checklist=checklist,
        likely_definitions=snippets,
        direct_callers=tuple(caller_snippets),
        related_public_tests=tuple(tests),
        relevant_protocols=tuple(
            sorted({
                protocol_id
                for unit in getattr(getattr(state, "active_binding_graph", None), "units", {}).values()
                for protocol_id in unit.protocol_edge_ids
            })
        )[:20],
        expected_behavior=tuple(dict.fromkeys((
            *checklist.change_requirements,
            *checklist.boundary_requirements,
            *checklist.exception_requirements,
        ))),
        preservation_behavior=checklist.preservation_requirements,
        uncertainty=checklist.uncertainties,
    )


def assess_first_patch_readiness(state, conversation, revision=None) -> FirstPatchReadiness:
    names = []
    for message in getattr(conversation, "messages", ()):
        if message.get("role") == "tool":
            names.append(str(message.get("name", message.get("tool", "tool"))))
    inspected = bool(
        getattr(conversation, "inspected_files", ())
        or getattr(conversation, "inspected_symbols", ())
    )
    apply_seen = "apply_edits" in names and bool(getattr(revision, "edits", ()))
    finish_seen = "finish_revision" in names
    localization_seen = inspected or apply_seen or any(
        name in {"search_code", "find_callers", "find_references", "inspect_symbol"}
        for name in names
    )
    check_seen = bool(
        getattr(revision, "requested_public_checks", ())
        or any("check" in name for name in names)
    )
    # apply_edits mechanically verifies exact source and records the complete
    # staged edit set. finish_revision, when present, is additional review
    # evidence but is not required because a final-turn edit is valid.
    final_review = apply_seen
    checklist = _requirement_checklist(state)
    preservation_available = bool(
        checklist.preservation_requirements
        or getattr(getattr(state, "target_recovery", None), "preservation_checks", ())
        or not getattr(getattr(state, "target_recovery", None), "targets", ())
    )
    return FirstPatchReadiness(
        target_definition_read=inspected or apply_seen,
        caller_inspection_status=(
            "FOUND_AND_READ" if inspected else "NOT_FOUND_AFTER_BOUNDED_SEARCH"
        ),
        test_or_contract_inspection_status=(
            "FOUND_AND_READ" if check_seen else "NOT_FOUND_AFTER_BOUNDED_SEARCH"
        ),
        root_cause_identified=apply_seen and (
            localization_seen
            or (finish_seen and bool(getattr(revision, "summary", "").strip()))
        ),
        requirements_accounted_for=apply_seen and bool(
            checklist.change_requirements
            or checklist.boundary_requirements
            or checklist.exception_requirements
            or checklist.uncertainties
        ),
        preservation_risks_identified=apply_seen and (
            preservation_available or localization_seen or finish_seen
        ),
        final_diff_reviewed=final_review,
        supporting_tool_event_ids=tuple(
            stable_id("generator-tool-event", index, name)
            for index, name in enumerate(names)
        ),
    )


def build_revision_packet(state, context=None) -> RevisionPacket:
    context = context or build_repair_context(state, mode="COUNTEREXAMPLE_REPAIR")
    selected_id = str(getattr(state, "runtime_metrics", {}).get(
        "selected_confirmed_failure_id", "",
    ))
    failure = next(
        (
            item for item in getattr(state, "confirmed_failures", ())
            if item.failure_id == selected_id
        ),
        None,
    )
    if failure is None:
        # Structural root recovery is also used inside the initial Generator
        # session when a tool call is malformed or asks for bounded context.
        # The production controller never enters the revision loop through
        # this path: it requires a selected ConfirmedFailure before invoking
        # repair_from_counterexamples/root_recovery.
        return RevisionPacket(
            current_patch=str(getattr(context, "working_diff", "")),
            failing_requirement=str(getattr(context, "issue", "")),
            failure_kind="INITIAL_STRUCTURAL_RECOVERY",
            concrete_input={
                "command": list(getattr(context, "reproduction_command", ())),
            },
            expected_observation={"status": "VALID_GENERATOR_ACTION"},
            actual_observation=(getattr(context, "baseline_output", None) or {}),
            failure_trace=str(
                (getattr(context, "baseline_output", None) or {}).get("stderr", "")
            ),
            causal_source_slices=tuple(
                getattr(context, "relevant_source_snippets", ())[:8]
            ),
            causal_cut_ids=tuple(
                str(item.get("node_id", item.get("relation_id", "")))
                for item in getattr(context, "causal_repair_cuts", ())
                if item.get("node_id") or item.get("relation_id")
            ),
            changed_hunks=(),
            preservation_contracts=tuple(
                str(item.get("check_id", ""))
                for item in getattr(context, "preservation_checks", ())
                if item.get("check_id")
            ),
            previous_failed_mechanisms=tuple(
                getattr(context, "failed_mechanisms", ())
            ),
            prohibited_mechanisms=tuple(
                getattr(context, "prohibited_mechanisms", ())
            ),
        )
    packet = next((
        item for item in reversed(state.counterexamples)
        if (
            item.counterexample_id == failure.failure_id
            or item.public_trigger_id == failure.check_id
            or (
                failure.binding_unit_id is not None
                and item.binding_unit_id == failure.binding_unit_id
            )
        )
    ), None)
    unit = state.active_binding_graph.units.get(failure.binding_unit_id or "")
    requirement_text = ""
    if failure.requirement_id:
        leaf = getattr(state.requirement_graph, "leaves", {}).get(failure.requirement_id)
        requirement_text = str(getattr(leaf, "formula", "")) if leaf else ""
    if not requirement_text and unit is not None:
        requirement_text = unit.requirement_text
    before = failure.before_patch_observation
    failure_trace = "\n".join(filter(None, (
        str(getattr(before, "stderr", "")),
        str(getattr(before, "stdout", "")),
        str(failure.failure_location or ""),
    )))
    allowed_cuts = set(failure.causal_cut_ids)
    slices = tuple(
        item for item in context.relevant_source_snippets
        if not allowed_cuts
        or str(item.get("node_id", "")) in allowed_cuts
        or str(item.get("reason", "")).startswith("backward causal")
    )[:8]
    histories = getattr(state, "failure_histories", {})
    history = histories.get(failure.failure_signature)
    previous_mechanisms = tuple(
        getattr(history, "attempted_mechanism_ids", ())
        or getattr(history, "attempted_mechanisms", ())
    )
    preservation = tuple(dict.fromkeys((
        *(str(item.get("check_id", "")) for item in context.preservation_checks),
        *(getattr(packet, "protected_behavior", ()) if packet is not None else ()),
        *(unit.preservation_check_ids if unit is not None else ()),
    )))
    return RevisionPacket(
        current_patch=state.patch_trajectory.working_patch.patch.canonical_diff,
        failing_requirement=requirement_text or str(failure.requirement_id or ""),
        failure_kind=failure.kind,
        concrete_input=(packet.concrete_input if packet is not None else {
            "command": list(getattr(context, "reproduction_command", ())),
        }),
        expected_observation=(
            packet.expected_observation if packet is not None else {"status": "PASS"}
        ),
        actual_observation=(
            packet.actual_observation if packet is not None else before.to_dict()
        ),
        failure_trace=failure_trace,
        causal_source_slices=slices,
        causal_cut_ids=failure.causal_cut_ids,
        changed_hunks=(unit.changed_hunk_ids if unit is not None else ()),
        preservation_contracts=preservation,
        previous_failed_mechanisms=previous_mechanisms,
        prohibited_mechanisms=tuple(sorted(state.prohibited_mechanisms)),
    )


def _issue_text(state) -> str:
    primary_issue = str(state.runtime_config.get("primary_issue", "")).strip()
    values = [
        evidence.content
        for evidence in state.semantic_graph.evidence.values()
        if evidence.kind.value in {"ISSUE_NORMATIVE", "ISSUE_WITNESS"}
    ]
    return primary_issue or "\n".join(dict.fromkeys(values))


def _public_discussion(state) -> str:
    return str(state.runtime_config.get("generation_hints", "")).strip()


_CONTEXT_STOPWORDS = {
    "about", "after", "also", "because", "before", "being", "could",
    "does", "from", "have", "into", "just", "must", "only", "should",
    "that", "than", "their", "there", "these", "this", "when", "with",
    "would", "will", "where", "which", "while", "not", "for", "and",
    "com", "org", "file", "line", "core", "packages", "kwargs",
    "traceback", "typeerror", "valueerror", "runtimeerror",
}


def _issue_context_snippets(state, issue: str, active_files: list[str]) -> list[dict[str, Any]]:
    """Project issue vocabulary onto a bounded set of real source locations."""

    repository = Path(state.checkpoint.snapshot_tree)
    index = getattr(state, "repository_index", None)
    if index is None or not repository.is_dir():
        return []
    raw_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", issue)
    syntax_tokens = {
        token.lower()
        for match in re.findall(
            r"(?:\.|@)([A-Za-z_][A-Za-z0-9_]{2,})|"
            r"([A-Za-z_][A-Za-z0-9_]{2,})(?=\s*\()",
            issue,
        )
        for token in match
        if token
    }
    headline_tokens = {
        token.lower()
        for token in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]{2,}", issue.split("```")[0]
        )
    }
    tokens = [
        token for token in raw_tokens
        if token.lower() not in _CONTEXT_STOPWORDS
        and (
            token.lower() in syntax_tokens
            or "_" in token
            or any(character.isupper() for character in token[1:])
        )
    ]
    if not tokens:
        tokens = [
            token for token in raw_tokens
            if token.lower() not in _CONTEXT_STOPWORDS and len(token) >= 8
        ][:20]
    lowered = {token.lower() for token in tokens}
    token_scores = {}
    for token in lowered:
        frequency = len(index.token_index.get(token, ()))
        frequency_adjustment = (
            -10 if frequency > 100
            else -7 if frequency > 30
            else -3 if frequency > 10
            else 2
        )
        structural_bonus = (
            8 if "_" in token.strip("_")
            else 4 if any(character.isupper() for character in token[1:])
            else 0
        )
        token_scores[token] = (
            frequency_adjustment
            + structural_bonus
            + (8 if token in headline_tokens else 0)
            - (4 if len(token) <= 3 else 0)
        )
    candidates: dict[tuple[str, int, int], tuple[int, str]] = {}

    def add_location(location, score: int) -> None:
        relative = str(getattr(location, "relative_path", ""))
        line = int(getattr(location, "line", 1))
        end_line = int(getattr(location, "end_line", line))
        if not relative or relative not in index.source_hashes:
            return
        path_parts = {part.lower() for part in Path(relative).parts}
        if path_parts & {"examples", "example", "benchmarks", "docs", "doc", "tests", "test"}:
            score -= 4
        implementation_components = {
            component for token in lowered
            for component in token.split("_") if len(component) >= 5
        }
        score += 5 * len(path_parts & implementation_components)
        key = (relative, line, end_line)
        current = candidates.get(key)
        if current is None or score > current[0]:
            candidates[key] = (score, str(getattr(location, "qualified_name", "")))

    # Exact and suffix symbol matches are much more precise than a repository
    # wide text scan, and remain bounded by the active vocabulary.
    for name, locations in index.symbols.items():
        name_lower = str(name).lower()
        suffix = name_lower.rsplit(".", 1)[-1]
        score = 0
        for token in lowered:
            if token == name_lower or token == suffix:
                score = max(score, 12 + token_scores[token])
            elif len(token) >= 5 and token in name_lower:
                score = max(score, 7 + token_scores[token])
        if score:
            for location in locations[:4]:
                add_location(location, score)

    # The lexical index also covers module-level constants and attributes that
    # do not have SymbolLocation entries (for example __version_info__ or
    # autodoc_typehints).  It is precomputed during repository indexing, so the
    # projection does not scan the repository here.
    for token in lowered:
        for relative in index.token_index.get(token, ())[:8]:
            add_location(
                type("Location", (), {
                    "relative_path": relative, "line": 1, "end_line": 1,
                    "qualified_name": f"<token:{token}>",
                })(),
                10 + token_scores[token],
            )
        for component in token.split("_"):
            if len(component) < 5:
                continue
            for relative in index.token_index.get(component, ())[:4]:
                add_location(
                    type("Location", (), {
                        "relative_path": relative, "line": 1, "end_line": 1,
                        "qualified_name": f"<token:{component}>",
                    })(),
                    6,
                )

        # Prefer implementation modules whose path reflects a package or
        # subsystem named by the issue token, while still using only the index
        # metadata and a small candidate cap.
        components = [part for part in token.split("_") if len(part) >= 5]
        for relative in sorted(index.source_hashes):
            path_lower = relative.lower()
            if any(component in path_lower for component in components):
                add_location(
                    type("Location", (), {
                        "relative_path": relative, "line": 1, "end_line": 1,
                        "qualified_name": f"<path:{token}>",
                    })(),
                    7 + token_scores[token],
                )
                if sum(1 for item in candidates if item[0] == relative) >= 8:
                    break

    # Include active files whose path or module name carries issue vocabulary.
    for relative in active_files:
        path_lower = relative.lower()
        score = sum(2 for token in lowered if token in path_lower)
        if score:
            add_location(
                type("Location", (), {
                    "relative_path": relative, "line": 1, "end_line": 1,
                    "qualified_name": "<issue-file>",
                })(), score,
            )

    snippets: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for (relative, line, end_line), (score, symbol) in sorted(
        candidates.items(), key=lambda item: (-item[1][0], item[0][0], item[0][1])
    ):
        if relative in seen_paths:
            continue
        path = repository / relative
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line == 1 and end_line == 1:
            search_tokens = [
                token for token in sorted(
                    lowered, key=lambda item: (-token_scores[item], item)
                )
                if token_scores[token] >= 4
            ] or list(lowered)
            matches = [
                index_line + 1 for index_line, text in enumerate(lines)
                if any(token in text.lower() for token in search_tokens if len(token) >= 5)
            ]
            if matches:
                line = matches[0]
                end_line = line
        start = max(1, line - 4)
        stop = min(len(lines), max(end_line, line) + 4)
        snippets.append({
            "relative_path": relative,
            "start_line": start,
            "end_line": stop,
            "snippet_start_line": start,
            "snippet_end_line": stop,
            "symbol": symbol,
            "reason": "bounded issue-to-symbol source projection",
            "content": "\n".join(lines[start - 1:stop]),
        })
        seen_paths.add(relative)
        if len(snippets) >= 6 or sum(len(item["content"]) for item in snippets) >= 14000:
            break
    return snippets


def build_repair_context(
    state,
    *,
    mode: Literal["INITIAL", "COUNTEREXAMPLE_REPAIR", "ROOT_RECOVERY"],
) -> RepairContext:
    target_checks = tuple(
        getattr(getattr(state, "target_recovery", None), "targets", ())
    )
    target_ids = {item.check_id for item in target_checks}
    comparisons = tuple(getattr(state, "check_comparisons", ()))
    priority = {
        "TARGET_STILL_FAILING": 0,
        "TARGET_REGRESSED": 1,
        "TARGET_FIXED": 2,
    }
    active_comparison = min(
        (item for item in comparisons if item.check_id in target_ids),
        key=lambda item: priority.get(item.classification.value, 9),
        default=None,
    )
    active_check = next(
        (
            item for item in target_checks
            if active_comparison is not None and item.check_id == active_comparison.check_id
        ),
        target_checks[0] if target_checks else None,
    )
    baseline_execution = (
        active_comparison.baseline if active_comparison is not None
        else getattr(getattr(state, "target_recovery", None), "execution_for", lambda _: None)(
            active_check.check_id
        ) if active_check is not None else None
    )
    patched_execution = active_comparison.patched if active_comparison is not None else None
    coverage_table = getattr(state, "requirement_coverage", None)
    if coverage_table is not None:
        coverage = [
            coverage_table.rows[key].to_dict()
            for key in sorted(coverage_table.rows)
        ]
    else:
        coverage = []
    failed_rows = [{
        "outcome_id": item.outcome_id,
        "status": item.status.value,
        "origin": item.failure_origin,
        "observation": item.observation,
    } for item in state.outcomes.values() if item.status == OutcomeStatus.FAIL]
    failed_rows.extend({
        "outcome_id": item.get("check_id"),
        "status": item.get("classification"),
        "origin": "PUBLIC_CHECK",
        "observation": {
            "command": item.get("command", ()),
            "baseline_return_code": item.get("baseline_return_code"),
            "patched_return_code": item.get("patched_return_code"),
            "patched_stdout": item.get("patched_stdout", ""),
            "patched_stderr": item.get("patched_stderr", ""),
        },
    } for item in state.runtime_metrics.get("last_public_check_comparisons", ())
    if item.get("classification") in {
        "TARGET_STILL_FAILING", "TARGET_REGRESSED", "PRESERVATION_REGRESSION",
    })
    failed = tuple(failed_rows)
    packets = tuple({
        "counterexample_id": item.counterexample_id,
        "failure_signature": item.failure_signature,
        "failure_origin": item.failure_origin,
        "authority": item.authority,
        "command": item.command,
        "minimal_input": item.minimal_input,
        "expected": item.expected_observation,
        "actual": item.actual_observation,
        "reproduction_recipe_id": item.reproduction_recipe_id,
        "candidate_repair_cut_ids": list(item.candidate_repair_cut_ids),
        "causal_cut_ids": list(item.causal_cut_ids),
        "impact_risks": list(item.impact_risks),
        "failure_location": item.failure_location,
        "suggested_action_families": list(item.suggested_action_families),
        "preservation_path_ids": list(item.preservation_path_ids),
        "uncertain_information": list(item.uncertain_information),
    } for item in state.counterexamples[-20:])
    suggested_action_families = tuple(dict.fromkeys(
        family
        for item in reversed(state.counterexamples[-20:])
        for family in item.suggested_action_families
        if family not in getattr(state, "prohibited_mechanisms", ())
    ))
    divergences = tuple(
        paired.first_divergence
        for paired in state.trace_bundles.values()
        if paired.first_divergence is not None
    )
    slice_files = sorted(state.program_graph.file_index)
    slice_summary = {
        "files": slice_files,
        "node_count": len(state.program_graph.nodes),
        "edge_count": len(state.program_graph.edges),
        "callable_count": len(state.program_graph.cfgs),
        "frontiers": [
            {"kind": item.kind, "reason": item.reason, "hard": item.hard}
            for item in state.program_graph.frontiers.values()
        ][:20],
        "symbols": sorted(state.program_graph.symbol_index)[:40],
    }
    cuts = tuple({
        "unit_id": unit.unit_id,
        "node_ids": list(unit.repair_cut_node_ids),
        "path_obligation_id": unit.path_obligation_id,
    } for unit in state.active_binding_graph.units.values() if unit.repair_cut_node_ids)
    impacts = tuple({
        "unit_id": unit.unit_id,
        "node_ids": list(unit.impact_cone_node_ids[:100]),
        "preservation_node_ids": list(unit.preservation_node_ids[:100]),
    } for unit in state.active_binding_graph.units.values() if unit.impact_cone_node_ids)
    passes = tuple({
        "outcome_id": item.outcome_id,
        "path_obligation_id": item.path_obligation_id,
        "observation": item.observation,
    } for item in state.outcomes.values() if item.status == OutcomeStatus.PASS and item.kind == "PRESERVATION")
    failed_mechanisms = tuple(sorted({
        attempt.mechanism_class
        for attempts in state.mechanism_memory.values() for attempt in attempts
        if attempt.result != "COMMIT"
    } | set(state.runtime_metrics.get("failed_generator_mechanisms", ()))
      | set(getattr(state, "prohibited_mechanisms", ()))))
    active_units = tuple(
        unit.to_dict()
        for unit in state.active_binding_graph.relevant_units()
    )[:20]
    unresolved_frontier = tuple(
        item.to_dict() if hasattr(item, "to_dict") else dict(item)
        for item in getattr(state, "unresolved_frontier", ())
    )[:20]
    source_snippets = []
    source_root = __import__("pathlib").Path(state.checkpoint.snapshot_tree)
    source_snippets.extend(_issue_context_snippets(state, _issue_text(state), slice_files))
    candidate_nodes = tuple(dict.fromkeys(
        node_id for causal in getattr(state, "causal_slices", ())
        for node_id in causal.candidate_cut_node_ids
    ))[:5]
    cut_candidates = []
    for node_id in candidate_nodes:
        node = state.program_graph.nodes.get(node_id)
        if node is None:
            continue
        relative = str(node.attributes.get("file", ""))
        line = int(node.attributes.get("line", 1))
        end = int(node.attributes.get("end_line", line))
        cut = {
            "node_id": node_id,
            "relative_path": relative,
            "start_line": line,
            "end_line": end,
            "symbol": str(node.attributes.get("qualified_name", node.label)),
            "reason": "backward causal slice from stable target failure",
        }
        cut_candidates.append(cut)
        path = source_root / relative
        if path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(1, line - 3)
            stop = min(len(lines), end + 3)
            source_snippets.append({
                **cut,
                "snippet_start_line": start,
                "snippet_end_line": stop,
                "content": "\n".join(lines[start - 1:stop]),
            })
    if active_check is not None:
        run_root = __import__("pathlib").Path(state.run_root).resolve()
        for raw_path in active_check.temporary_artifact_paths[:1]:
            path = __import__("pathlib").Path(raw_path).resolve()
            try:
                path.relative_to(run_root)
            except ValueError:
                continue
            if not path.is_file():
                continue
            content = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()[:160]
            source_snippets.append({
                "relative_path": str(path.relative_to(run_root)),
                "start_line": 1,
                "end_line": len(content),
                "symbol": "<public-target-reproduction>",
                "reason": "stable public target reproduction",
                "origin": "TARGET_REPRODUCTION_ARTIFACT",
                "content": "\n".join(content),
            })
    preservation_checks = tuple({
        "check_id": item.check_id,
        "command": list(item.command),
        "selector": item.selector,
    } for item in getattr(
        getattr(state, "target_recovery", None), "preservation_checks", ()
    ))
    previous = state.repair_history[-1] if state.repair_history else None
    return RepairContext(
        mode=mode, issue=_issue_text(state),
        public_discussion=_public_discussion(state),
        working_diff=state.checkpoint.patch.canonical_diff,
        active_target_check=(active_check.to_dict() if active_check else None),
        baseline_output=(baseline_execution.to_dict() if baseline_execution else None),
        patched_output=(patched_execution.to_dict() if patched_execution else None),
        failure_signature=(
            patched_execution.failure_signature if patched_execution
            else baseline_execution.failure_signature if baseline_execution else None
        ),
        first_project_frame=(
            patched_execution.first_project_frame if patched_execution
            else baseline_execution.first_project_frame if baseline_execution else None
        ),
        reproduction_command=(active_check.command if active_check else ()),
        relevant_source_snippets=tuple(source_snippets),
        causal_cut_candidates=tuple(cut_candidates),
        previous_revision=(previous.graph_delta if previous else None),
        previous_failure_reason=(
            str(previous.graph_delta.get("avoid_reasons", "")) if previous else None
        ),
        preservation_checks=preservation_checks,
        semantic_ambiguities=tuple(
            getattr(getattr(state, "hypothesis_set", None), "unresolved_decision_ids", ())
        ),
        requirement_coverage=tuple(coverage), failed_checks=failed,
        counterexamples=packets,
        suggested_action_families=suggested_action_families,
        first_trace_divergences=divergences,
        active_program_slice=slice_summary, causal_repair_cuts=cuts,
        impact_risks=impacts, preserved_passes=passes,
        failed_mechanisms=failed_mechanisms,
        prohibited_mechanisms=tuple(sorted(state.prohibited_mechanisms)),
        active_binding_units=active_units,
        unresolved_frontier=unresolved_frontier,
        repair_intent=state.runtime_metrics.get("current_repair_intent"),
        remaining_budget=state.remaining_budget.to_dict(),
    )
