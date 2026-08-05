from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from reachpatch.program_graph.budget import Deadline
from reachpatch.program_graph.index import build_repository_index
from reachpatch.repair.deepseek_agent import (
    DeepSeekHTTPTransport,
    INITIAL_REPAIR_INSTRUCTION,
    INITIAL_ROOT_RECOVERY_INSTRUCTION,
    GeneratorConversation,
    PersistentDeepSeekAgent,
    _explicit_multi_method_names,
    _finish_summary_rejects_patch,
    _initial_patch_review_error,
    _prune_rejected_alternate_layer_edits,
)
from reachpatch.repair.tools import ProposedEdit, RepairToolExecutor


def test_explicitly_incomplete_finish_summary_is_rejected():
    assert _finish_summary_rejects_patch(
        "This patch is incomplete and does not address the actual issue."
    )
    assert _finish_summary_rejects_patch(
        "The helper has no causal role and must be replaced."
    )
    assert not _finish_summary_rejects_patch(
        "Reviewed the complete causal repair and its callers."
    )


def _review_packet(
    issue: str,
    *,
    definitions=(),
    discussion=(),
):
    return SimpleNamespace(
        issue_text=issue,
        requirement_checklist=SimpleNamespace(
            change_requirements=(issue,),
            boundary_requirements=(),
            exception_requirements=(),
        ),
        likely_definitions=tuple(definitions),
        discussion_evidence=tuple(discussion),
    )


def test_initial_review_rejects_comment_only_patch():
    error = _initial_patch_review_error(
        _review_packet("Q and Exists should be commutative."),
        "--- a/module.py\n+++ b/module.py\n@@ -1 +1,2 @@\n value = 1\n+# duplicate note\n",
        "Fixed the operator behavior.",
    )

    assert error.startswith("STAGED_PATCH_NO_EXECUTABLE_CHANGE")


def test_initial_review_rejects_import_only_patch():
    error = _initial_patch_review_error(
        _review_packet("Recorder must skip work when routing disallows it."),
        "--- a/recorder.py\n+++ b/recorder.py\n@@ -1 +1,2 @@\n"
        "+from framework.db import router\n existing = True\n",
        "Imported the router needed for the repair.",
    )

    assert error.startswith("STAGED_PATCH_IMPORT_ONLY_WITHOUT_REACHABLE_BEHAVIOR")


def test_initial_review_rejects_summary_claim_in_opposite_ternary_branch():
    packet = _review_packet(
        "Prefix relative URLs while preserving absolute URLs.",
    )
    summary = (
        "When base_url is a relative URL starting with '/', prepend the active "
        "prefix returned by get_script_prefix(). Absolute URLs are unchanged."
    )
    reversed_error = _initial_patch_review_error(
        packet,
        "--- a/storage.py\n+++ b/storage.py\n@@ -10 +10 @@\n"
        "-    return join(base_url, path)\n"
        "+    return join(base_url, path) if base_url.startswith('/') else "
        "join(get_script_prefix(), join(base_url, path))\n",
        summary,
    )
    aligned_error = _initial_patch_review_error(
        packet,
        "--- a/storage.py\n+++ b/storage.py\n@@ -10 +10 @@\n"
        "-    return join(base_url, path)\n"
        "+    return join(get_script_prefix(), join(base_url, path)) if "
        "base_url.startswith('/') else join(base_url, path)\n",
        summary,
    )

    assert reversed_error.startswith(
        "STAGED_PATCH_REVERSES_EXPLICIT_BRANCH_RELATION"
    )
    assert aligned_error is None


def test_initial_review_rejects_prefix_in_opposite_multiline_branch():
    packet = _review_packet(
        "Prepend the dynamic prefix to relative URLs when the value starts with '/'.",
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/storage.py\n+++ b/storage.py\n@@ -10,1 +10,3 @@\n"
        "-    return urljoin(base_url, path)\n"
        "+    if base_url.startswith('/'):\n"
        "+        return urljoin(base_url, path)\n"
        "+    return urljoin(get_script_prefix(), base_url) + path\n",
        "When base_url starts with '/', prepend get_script_prefix(); absolute URLs remain unchanged.",
    )

    assert error.startswith("STAGED_PATCH_REVERSES_EXPLICIT_BRANCH_RELATION")


def test_initial_review_rejects_required_prefix_used_only_as_fallback():
    error = _initial_patch_review_error(
        _review_packet(
            "The URL builder must prepend the dynamic script prefix to relative URLs."
        ),
        "--- a/storage.py\n+++ b/storage.py\n@@ -10 +10 @@\n"
        "-    return configured_url\n"
        "+    return configured_url or get_script_prefix()\n",
        "Prepended the dynamic script prefix to configured URLs.",
    )

    assert error.startswith("STAGED_PATCH_USES_REQUIRED_PREFIX_ONLY_AS_FALLBACK")


def test_initial_review_requires_explicit_side_effect_owner_change():
    source = (
        "class Recorder:\n"
        "    def ensure_schema(self):\n"
        "        self.create_table()\n\n"
        "    def record(self):\n"
        "        self.ensure_schema()\n"
        "        self.write()\n\n"
        "    def create(self):\n"
        "        return object()\n"
    )
    packet = _review_packet(
        "ensure_schema tries to create a table. This is incorrect when writes "
        "are disabled.",
        definitions=({
            "relative_path": "recorder.py",
            "start_line": 1,
            "snippet_start_line": 1,
            "symbol": "Recorder",
            "content": source,
        },),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/recorder.py\n+++ b/recorder.py\n@@ -5,3 +5,4 @@\n"
        "     def record(self):\n"
        "-        self.ensure_schema()\n"
        "+        if self.allowed():\n"
        "+            self.ensure_schema()\n"
        "         self.write()\n",
        "Guarded record but left schema creation unchanged.",
    )

    assert error.startswith(
        "STAGED_PATCH_OMITS_EXPLICIT_CAUSAL_METHOD_CORRECTION"
    )
    assert "ensure_schema" in error
    assert "'create'" not in error


def test_initial_review_combines_causal_owner_and_related_method_recovery():
    source = (
        "class Recorder:\n"
        "    def ensure_schema(self):\n"
        "        self.create_table()\n\n"
        "    def applied(self):\n"
        "        return self.query()\n\n"
        "    def record(self):\n"
        "        self.ensure_schema()\n"
        "        self.write()\n"
    )
    packet = _review_packet(
        "ensure_schema tries to create a table. This is incorrect when writes "
        "are disabled.",
        definitions=({
            "relative_path": "recorder.py", "snippet_start_line": 1,
            "symbol": "Recorder", "content": source,
        },),
        discussion=("Changes to applied and record need to be made.",),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/recorder.py\n+++ b/recorder.py\n@@ -5,2 +5,3 @@\n"
        "     def applied(self):\n"
        "-        return self.query()\n"
        "+        return self.query() if self.allowed() else {}\n",
        "Guarded only the applied read path.",
    )

    assert error.startswith(
        "STAGED_PATCH_OMITS_EXPLICIT_CAUSAL_AND_MULTI_METHOD_CORRECTION"
    )
    assert "ensure_schema" in error
    assert "record" in error
    assert set(_explicit_multi_method_names(error)) == {
        "ensure_schema", "applied", "record",
    }


def test_initial_review_rejects_unjustified_shared_utility_expansion():
    packet = _review_packet(
        "NeighborhoodComponentsAnalysis must accept integral scalar parameters.",
        definitions=(
            {
                "relative_path": "neighbors/component.py",
                "symbol": "NeighborhoodComponentsAnalysis",
            },
            {
                "relative_path": "utils/validation.py",
                "symbol": "check_scalar",
            },
        ),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/utils/validation.py\n+++ b/utils/validation.py\n"
        "@@ -2 +2,2 @@\n-if type(value) is int:\n"
        "+if isinstance(value, numbers.Integral):\n",
        "Expanded scalar validation for all callers.",
    )

    assert error.startswith("STAGED_PATCH_EXPANDS_SCOPED_FIX_INTO_SHARED_UTILITY")


def test_initial_review_allows_explicitly_named_shared_utility_contract():
    packet = _review_packet(
        "check_scalar must accept integral scalar parameters for every caller.",
        definitions=(
            {
                "relative_path": "utils/validation.py",
                "symbol": "check_scalar",
            },
        ),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/utils/validation.py\n+++ b/utils/validation.py\n"
        "@@ -2 +2,2 @@\n-if type(value) is int:\n"
        "+if isinstance(value, numbers.Integral):\n",
        "Updated the explicitly named shared validation API.",
    )

    assert error is None


def test_initial_review_allows_shared_named_module_in_issue_traceback():
    packet = _review_packet(
        "URLField must raise ValidationError. Traceback: File "
        '"django/core/validators.py", line 130, in __call__',
        definitions=(
            {
                "relative_path": "django/forms/fields.py",
                "symbol": "URLField",
            },
            {
                "relative_path": "django/core/validators.py",
                "symbol": "URLValidator",
            },
        ),
    )

    error = _initial_patch_review_error(
        packet,
        "--- a/django/core/validators.py\n"
        "+++ b/django/core/validators.py\n"
        "@@ -2 +2,3 @@\n"
        "-    value = urlsplit(value)\n"
        "+    try:\n"
        "+        value = urlsplit(value)\n"
        "+    except ValueError:\n",
        "Wrapped the exact failing traceback frame in the public validator.",
    )

    assert error is None


def test_initial_review_does_not_classify_component_base_module_by_name_only():
    packet = _review_packet(
        "NearestNeighbors must reject a floating n_neighbors before tree dispatch.",
        definitions=(
            {
                "relative_path": "sklearn/neighbors/unsupervised.py",
                "symbol": "NearestNeighbors",
            },
            {
                "relative_path": "sklearn/neighbors/base.py",
                "symbol": "KNeighborsMixin",
            },
        ),
    )

    error = _initial_patch_review_error(
        packet,
        "--- a/sklearn/neighbors/base.py\n"
        "+++ b/sklearn/neighbors/base.py\n"
        "@@ -2 +2,2 @@\n"
        "-    if n_neighbors <= 0:\n"
        "+    if not isinstance(n_neighbors, numbers.Integral) or n_neighbors <= 0:\n",
        "Validated the shared neighbor execution path used by the estimator.",
    )

    assert error is None


def test_quality_recovery_projects_rejected_shared_owner_to_local_callsite(
    tmp_path,
):
    repository = tmp_path / "repo"
    (repository / "neighbors").mkdir(parents=True)
    (repository / "utils").mkdir()
    (repository / "utils" / "validation.py").write_text(
        "def check_scalar(value, target_type):\n"
        "    if not isinstance(value, target_type):\n"
        "        raise TypeError\n",
        encoding="utf-8",
    )
    (repository / "neighbors" / "component.py").write_text(
        "from utils.validation import check_scalar\n\n"
        "class Component:\n"
        "    def _check_params(self):\n"
        "        check_scalar(self.count, int)\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    tools = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )
    tools.apply_edits((ProposedEdit(
        relative_path="utils/validation.py", start_line=2, end_line=2,
        expected_source="    if not isinstance(value, target_type):",
        replacement="    if type(value) is not target_type:",
    ),))
    tools.staged_quality_error = (
        "STAGED_PATCH_EXPANDS_SCOPED_FIX_INTO_SHARED_UTILITY: use the local "
        "component call site instead"
    )
    packet = _review_packet(
        "Component must accept integral count values.",
        definitions=(
            {
                "relative_path": "neighbors/component.py",
                "symbol": "Component",
            },
            {
                "relative_path": "utils/validation.py",
                "symbol": "check_scalar",
            },
        ),
    )

    sources = PersistentDeepSeekAgent._quality_recovery_sources(tools, packet)

    assert any(
        item.get("relative_path") == "neighbors/component.py"
        and item.get("symbol") == "_check_params"
        and "check_scalar(self.count, int)" in item.get("content", "")
        for item in sources
    )
    assert any(
        item.get("origin") == "MANDATORY_LOCAL_CALLSITE_IMPORT_SOURCE"
        and "from utils.validation import check_scalar" in item.get("content", "")
        for item in sources
    )


def test_prefix_quality_recovery_prioritizes_value_composition_owner(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "from urllib.parse import urljoin\n\n"
        "class Storage:\n"
        "    @property\n"
        "    def base_url(self):\n"
        "        return self.configured_url\n\n"
        "    def url(self, name):\n"
        "        relative = self.to_uri(name)\n"
        "        return urljoin(self.base_url, relative)\n"
    )
    (repository / "storage.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    tools = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )
    tools.staged_quality_rejected = True
    tools.staged_quality_error = (
        "STAGED_PATCH_USES_REQUIRED_PREFIX_ONLY_AS_FALLBACK: compose the prefix"
    )
    tools.rejected_staged_paths.add("storage.py")
    packet = _review_packet(
        "Prepend the dynamic prefix while constructing relative storage URLs.",
        definitions=({
            "relative_path": "storage.py", "symbol": "Storage",
            "content": source,
        },),
    )

    sources = PersistentDeepSeekAgent._quality_recovery_sources(tools, packet)

    assert sources[0]["symbol"] == "url"
    assert sources[0]["origin"] == "MANDATORY_PREFIX_COMPOSITION_OWNER"


def test_initial_review_requires_explicit_multi_method_correction():
    packet = _review_packet(
        "Recorder must obey routing while creating and recording state.",
        definitions=({
            "relative_path": "recorder.py",
            "symbol": "Recorder",
            "content": (
                "class Recorder:\n"
                "    def ensure_schema(self):\n        pass\n"
                "    def applied_records(self):\n        pass\n"
                "    def record_applied(self):\n        pass\n"
            ),
        },),
        discussion=(
            "Changes to applied_records and record_applied need to be made so "
            "neither path accesses a table that routing disallows.",
        ),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/recorder.py\n+++ b/recorder.py\n@@ -2 +2,2 @@\n"
        " def ensure_schema(self):\n+    return router.allow()\n",
        "Guarded schema creation.",
    )

    assert error.startswith("STAGED_PATCH_OMITS_EXPLICIT_MULTI_METHOD_CORRECTION")


def test_multi_method_quality_recovery_forces_complete_replacement(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "from framework import router\n\n"
        "class Recorder:\n"
        "    def ensure_schema(self):\n"
        "        return self.has_table()\n\n"
        "    def applied_records(self):\n"
        "        return self.query()\n\n"
        "    def record_applied(self):\n"
        "        self.create()\n"
    )
    (repository / "recorder.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    tools = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    tools.apply_edits((ProposedEdit(
        relative_path="recorder.py", start_line=5, end_line=5,
        expected_source="        return self.has_table()",
        replacement="        return router.allow() and self.has_table()",
    ),))
    tools.staged_quality_rejected = True
    tools.staged_quality_error = (
        "STAGED_PATCH_OMITS_EXPLICIT_MULTI_METHOD_CORRECTION: the strongest "
        "public causal discussion explicitly says changes are needed in "
        "['applied_records', 'record_applied'], but the reviewed diff does not "
        "touch ['applied_records', 'record_applied']."
    )
    tools.staged_quality_rejected_version = tools.staged_edit_version
    issue = "Recorder must obey routing while creating and recording state."
    context = SimpleNamespace(
        issue=issue, public_discussion="", working_diff="", failed_checks=(),
        counterexamples=(), first_trace_divergences=(),
        causal_repair_cuts=(), causal_cut_candidates=(), requirement_coverage=(),
        relevant_source_snippets=({
            "relative_path": "recorder.py", "snippet_start_line": 1,
            "snippet_end_line": 11, "symbol": "Recorder", "content": source,
        },),
        active_program_slice={"files": ("recorder.py",), "symbols": ("Recorder",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    packet = {
        "issue_text": issue,
        "requirement_checklist": {
            "change_requirements": [issue],
            "boundary_requirements": [], "exception_requirements": [],
            "preservation_requirements": [], "witnesses": [],
            "uncertainties": [],
        },
        "likely_definitions": [{
            "relative_path": "recorder.py", "symbol": "Recorder",
            "content": source,
        }],
        "direct_callers": [], "related_public_tests": [],
        "discussion_evidence": [
            "Changes to applied_records and record_applied need to be made."
        ],
        "candidate_symbols": ["Recorder"], "relevant_protocols": [],
        "expected_behavior": [], "preservation_behavior": [],
        "uncertainty": [],
    }
    conversation = GeneratorConversation.create("multi-method-recovery")
    conversation.messages.append({
        "role": "user",
        "content": json.dumps({"initial_repair_packet": packet}),
    })
    calls = 0

    def transport(messages, schemas):
        nonlocal calls
        calls += 1
        available = {item["function"]["name"] for item in schemas}
        if calls == 1:
            assert available == {"replace_staged_edits"}
            payload = next(
                json.loads(message["content"])
                for message in reversed(messages)
                if message.get("role") == "user"
                and "current_staged_diff" in message.get("content", "")
            )
            assert "applied_records" in payload["current_staged_diff"] or (
                "ensure_schema" in payload["current_staged_diff"]
            )
            assert {
                item.get("symbol")
                for item in payload["mandatory_quality_recovery_source"]
            } >= {"applied_records", "record_applied"}
            name = "replace_staged_edits"
            arguments = {
                "mechanism": "guard_expand",
                "edits": [{
                    "relative_path": "recorder.py",
                    "start_line": 5, "end_line": 5,
                    "expected_source": "        return self.has_table()",
                    "replacement": "        return router.allow() and self.has_table()",
                }, {
                    "relative_path": "recorder.py",
                    "start_line": 8, "end_line": 8,
                    "expected_source": "        return self.query()",
                    "replacement": "        return self.query() if router.allow() else {}",
                }, {
                    "relative_path": "recorder.py",
                    "start_line": 11, "end_line": 11,
                    "expected_source": "        self.create()",
                    "replacement": "        if router.allow():\n            self.create()",
                }],
            }
        else:
            assert "finish_revision" in available
            name = "finish_revision"
            arguments = {
                "summary": (
                    "Reviewed ensure_schema, applied_records, and record_applied; "
                    "all disallowed routes now preserve neutral behavior."
                ),
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=4,
    ).recover_initial_patch(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={"recorder.py": ()}),
            repository_index=index,
        ),
        conversation,
        tools,
    )

    assert calls == 2
    assert revision.status == "PROPOSED"
    assert len(revision.edits) == 3


def test_initial_review_allows_unchanged_named_read_with_proven_neutral_fallback():
    packet = _review_packet(
        "Recorder must obey routing while reading and recording state.",
        definitions=({
            "relative_path": "recorder.py",
            "symbol": "Recorder",
            "content": (
                "class Recorder:\n"
                "    def applied_records(self):\n"
                "        if self.has_table():\n"
                "            return dict(self.records)\n"
                "        return {}\n\n"
                "    def record_applied(self):\n"
                "        self.records.create()\n"
            ),
        },),
        discussion=(
            "Changes to applied_records and record_applied need to be made so "
            "neither path accesses a table that routing disallows.",
        ),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/recorder.py\n+++ b/recorder.py\n@@ -8 +8,3 @@\n"
        " def record_applied(self):\n"
        "+    if not router.allow():\n"
        "+        return\n",
        (
            "record_applied now skips the disallowed write. applied_records "
            "already has an existing table guard and returns the neutral empty "
            "mapping when storage is unavailable, so no change is needed there."
        ),
    )

    assert error is None


def test_initial_review_requires_discussed_residual_state_switch():
    packet = _review_packet(
        "Chained only() and defer() calls must preserve their quantified behavior.",
        definitions=({
            "relative_path": "query.py", "symbol": "Query",
            "content": "class Query:\n    def consume(self):\n        pass\n",
        },),
        discussion=(
            "The set difference in one direction misses the incoming fields; the "
            "repair should compute the other residual and switch from only mode "
            "to defer mode.",
        ),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/query.py\n+++ b/query.py\n@@ -2 +2 @@\n"
        "-if not fields:\n+if not fields and not defer:\n",
        "Adjusted the empty consumer guard.",
    )

    assert error.startswith("STAGED_PATCH_IGNORES_EXPLICIT_STATE_TRANSITION_MECHANISM")


def test_initial_review_rejects_mode_only_flip_with_unchanged_residual():
    packet = _review_packet(
        "Chained include() and exclude() calls must agree with one batched call.",
        definitions=({
            "relative_path": "selection.py", "symbol": "Selection",
            "content": (
                "class Selection:\n"
                "    def exclude(self, incoming):\n"
                "        existing, include_mode = self.selection\n"
                "        self.selection = existing.difference(incoming), False\n"
            ),
        },),
        discussion=(
            "When the existing difference becomes empty, compute the reverse "
            "incoming residual and switch from include mode to exclude mode.",
        ),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/selection.py\n+++ b/selection.py\n@@ -4 +4 @@\n"
        "-        self.selection = existing.difference(incoming), False\n"
        "+        self.selection = existing.difference(incoming), True\n",
        "Switched the state tag when the include set is exhausted.",
    )

    assert error.startswith("STAGED_PATCH_FLIPS_MODE_WITHOUT_INCOMING_RESIDUAL")
    assert "incoming-only residual" in error


def test_initial_review_allows_mode_switch_with_reverse_residual():
    packet = _review_packet(
        "Chained include() and exclude() calls must agree with one batched call.",
        definitions=({
            "relative_path": "selection.py", "symbol": "Selection",
            "content": "class Selection:\n    pass\n",
        },),
        discussion=(
            "When the existing difference becomes empty, compute the reverse "
            "incoming residual and switch from include mode to exclude mode.",
        ),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/selection.py\n+++ b/selection.py\n@@ -4 +4,6 @@\n"
        "-        self.selection = existing.difference(incoming), False\n"
        "+        remaining = existing.difference(incoming)\n"
        "+        incoming_only = set(incoming).difference(existing)\n"
        "+        if remaining:\n"
        "+            self.selection = remaining, False\n"
        "+        else:\n"
        "+            self.selection = incoming_only, True\n",
        "Preserved the remaining include set and switched with incoming-only fields.",
    )

    assert error is None


def test_initial_review_rejects_added_assignment_overwritten_by_context_line():
    packet = _review_packet(
        "A state update must preserve all chained selection combinations.",
        definitions=({
            "relative_path": "selection.py", "symbol": "Selection",
            "content": "class Selection:\n    pass\n",
        },),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/selection.py\n+++ b/selection.py\n@@ -5,2 +5,3 @@\n"
        "+        self.selection = incoming.difference(existing), False\n"
        "         self.selection = frozenset(incoming), False\n"
        "         return self.selection\n",
        "Updated the selection residual before storing the new state.",
    )

    assert error.startswith(
        "STAGED_PATCH_ADDS_IMMEDIATELY_OVERWRITTEN_STATE_WRITE"
    )
    assert "dead store" in error


def test_initial_review_rejects_added_branch_overwritten_after_branch():
    packet = _review_packet(
        "A state update must preserve all chained selection combinations.",
        definitions=({
            "relative_path": "selection.py", "symbol": "Selection",
            "content": "class Selection:\n    pass\n",
        },),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/selection.py\n+++ b/selection.py\n@@ -5,2 +5,8 @@\n"
        "+        remaining = existing.difference(incoming)\n"
        "+        if remaining:\n"
        "+            self.selection = remaining, False\n"
        "+        else:\n"
        "+            incoming_only = set(incoming).difference(existing)\n"
        "+            self.selection = incoming_only, True\n"
        "         self.selection = frozenset(incoming), False\n"
        "         return self.selection\n",
        "Handled both residuals before storing the selection state.",
    )

    assert error.startswith(
        "STAGED_PATCH_ADDS_IMMEDIATELY_OVERWRITTEN_STATE_WRITE"
    )


def test_initial_review_allows_assignment_that_reads_previous_value():
    packet = _review_packet(
        "Increment the normalized state after initialization.",
        definitions=({
            "relative_path": "counter.py", "symbol": "Counter",
            "content": "class Counter:\n    pass\n",
        },),
    )
    error = _initial_patch_review_error(
        packet,
        "--- a/counter.py\n+++ b/counter.py\n@@ -5,1 +5,3 @@\n"
        "+        self.count = normalize(value)\n"
        "+        self.count = self.count + 1\n"
        "         return self.count\n",
        "Normalize the count and then increment that normalized value.",
    )

    assert error is None


def test_initial_review_requires_observed_state_writing_delegate():
    packet = _review_packet(
        "Selection.exclude() fails when chained after include().",
    )
    conversation = SimpleNamespace(messages=({
        "role": "tool",
        "name": "read_file",
        "content": json.dumps({
            "path": "selection/api.py", "start_line": 10,
            "content": (
                "    def exclude(self, fields):\n"
                "        clone = self.copy()\n"
                "        clone.query.add_excluded_fields(fields)\n"
                "        return clone\n\n"
                "    def include(self, fields):\n"
                "        clone = self.copy()\n"
                "        clone.query.add_included_fields(fields)\n"
                "        return clone\n"
            ),
        }),
    }, {
        "role": "tool",
        "name": "read_file",
        "content": json.dumps({
            "path": "selection/query.py", "start_line": 40,
            "content": (
                "    def add_excluded_fields(self, fields):\n"
                "        self.selection = self.selection.difference(fields)\n\n"
                "    def add_included_fields(self, fields):\n"
                "        self.selection = frozenset(fields)\n"
            ),
        }),
    }))
    error = _initial_patch_review_error(
        packet,
        "--- a/selection/query.py\n+++ b/selection/query.py\n"
        "@@ -44,2 +44,3 @@\n"
        "     def add_included_fields(self, fields):\n"
        "-        self.selection = frozenset(fields)\n"
        "+        self.selection = set(fields).difference(self.selection)\n",
        "Changed the sibling include transition.",
        conversation,
    )

    assert error.startswith("STAGED_PATCH_MISSES_OBSERVED_STATE_OWNER")
    assert "add_excluded_fields" in error


def test_initial_review_accepts_observed_state_writing_delegate_edit():
    packet = _review_packet(
        "Selection.exclude() fails when chained after include().",
    )
    conversation = SimpleNamespace(messages=({
        "role": "tool",
        "name": "read_file",
        "content": json.dumps({
            "path": "selection/api.py", "start_line": 10,
            "content": (
                "    def exclude(self, fields):\n"
                "        clone = self.copy()\n"
                "        clone.query.add_excluded_fields(fields)\n"
                "        return clone\n"
            ),
        }),
    }, {
        "role": "tool",
        "name": "read_file",
        "content": json.dumps({
            "path": "selection/query.py", "start_line": 40,
            "content": (
                "    def add_excluded_fields(self, fields):\n"
                "        self.selection = self.selection.difference(fields)\n"
            ),
        }),
    }))
    error = _initial_patch_review_error(
        packet,
        "--- a/selection/query.py\n+++ b/selection/query.py\n"
        "@@ -40,2 +40,3 @@\n"
        "     def add_excluded_fields(self, fields):\n"
        "-        self.selection = self.selection.difference(fields)\n"
        "+        remaining = self.selection.difference(fields)\n"
        "+        self.selection = remaining\n",
        "Changed the observed exclude state owner.",
        conversation,
    )

    assert error is None


def test_initial_review_rejects_ignored_causal_source_anchor():
    definitions = (
        {"relative_path": "pkg/models/fields.py", "symbol": "models.FilePathField"},
        {"relative_path": "pkg/forms/fields.py", "symbol": "forms.FilePathField"},
    )
    error = _initial_patch_review_error(
        _review_packet(
            "Allow FilePathField path to accept a callable.",
            definitions=definitions,
            discussion=(
                "The problem is in pkg/forms/fields.py. The correct fix should only "
                "change that consumer; changing the model breaks serialization.",
                "An earlier proposal mentioned pkg/models/fields.py, but later "
                "analysis superseded that location.",
            ),
        ),
        "--- a/pkg/models/fields.py\n+++ b/pkg/models/fields.py\n"
        "@@ -2 +2,2 @@\n+        path = path()\n self.path = path\n",
        "Evaluate the callable in the model field.",
    )

    assert error.startswith("STAGED_PATCH_IGNORES_CAUSAL_SOURCE_ANCHOR")


def test_initial_review_rejects_extra_same_named_definition_beyond_anchor():
    definitions = (
        {"relative_path": "pkg/models/fields.py", "symbol": "models.FilePathField"},
        {"relative_path": "pkg/forms/fields.py", "symbol": "forms.FilePathField"},
    )
    error = _initial_patch_review_error(
        _review_packet(
            "Allow FilePathField path to accept a callable.",
            definitions=definitions,
            discussion=(
                "The problem is in pkg/forms/fields.py. The correct fix should only "
                "change that consumer; the model already preserves serialization.",
            ),
        ),
        "--- a/pkg/forms/fields.py\n+++ b/pkg/forms/fields.py\n"
        "@@ -2 +2,2 @@\n+        path = path()\n self.path = path\n"
        "--- a/pkg/models/fields.py\n+++ b/pkg/models/fields.py\n"
        "@@ -2 +2,2 @@\n+        path = path()\n self.path = path\n",
        "Evaluate the callable in both same-named definitions.",
    )

    assert error.startswith("STAGED_PATCH_EXPANDS_BEYOND_CAUSAL_SOURCE_ANCHOR")


def test_initial_review_allows_evidenced_caller_owned_construction_boundary():
    definitions = (
        {"relative_path": "pkg/models/fields.py", "symbol": "models.FilePathField"},
        {"relative_path": "pkg/forms/fields.py", "symbol": "forms.FilePathField"},
    )
    error = _initial_patch_review_error(
        _review_packet(
            "Allow FilePathField path to accept a callable.",
            definitions=definitions,
            discussion=(
                "The problem is in pkg/forms/fields.py. The correct fix should only "
                "ensure that consumer receives a concrete path.",
            ),
        ),
        "--- a/pkg/models/fields.py\n+++ b/pkg/models/fields.py\n"
        "@@ -4,3 +4,4 @@\n def formfield(self):\n"
        "+    path = self.path() if callable(self.path) else self.path\n"
        "     return FormField(path=path)\n",
        "The model retains the callable for serialization while formfield() "
        "constructs the consumer and passes the resolved path to it.",
    )

    assert error is None


def test_causal_anchor_review_prunes_only_rejected_alternate_layer(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg" / "models").mkdir(parents=True)
    (repository / "pkg" / "forms").mkdir(parents=True)
    (repository / "pkg" / "models" / "fields.py").write_text(
        "class FilePathField:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n",
        encoding="utf-8",
    )
    (repository / "pkg" / "forms" / "fields.py").write_text(
        "class FilePathField:\n"
        "    def __init__(self, path):\n"
        "        self.path = path\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    executor.apply_edits((
        ProposedEdit(
            relative_path="pkg/models/fields.py", start_line=3, end_line=3,
            expected_source="        self.path = path",
            replacement=(
                "        self.path = path() if callable(path) else path"
            ),
        ),
        ProposedEdit(
            relative_path="pkg/forms/fields.py", start_line=3, end_line=3,
            expected_source="        self.path = path",
            replacement=(
                "        self.path = path() if callable(path) else path"
            ),
        ),
    ))
    packet = _review_packet(
        "Allow FilePathField path to accept a callable.",
        definitions=(
            {
                "relative_path": "pkg/models/fields.py",
                "symbol": "models.FilePathField",
            },
            {
                "relative_path": "pkg/forms/fields.py",
                "symbol": "forms.FilePathField",
            },
        ),
        discussion=(
            "The problem is in pkg/forms/fields.py. The correct fix should only "
            "change that consumer; the model must preserve serialization.",
        ),
    )
    preview = executor.show_current_diff()
    quality_error = _initial_patch_review_error(
        packet, preview["staged_diff"], "Changed both definitions.",
    )

    result = _prune_rejected_alternate_layer_edits(
        packet, executor, quality_error,
    )

    assert result is not None
    assert result["automatic_correction"] == (
        "REMOVE_REJECTED_ALTERNATE_LAYER_EDITS"
    )
    assert result["removed_paths"] == ["pkg/models/fields.py"]
    assert [edit.relative_path for edit in executor.staged_edits] == [
        "pkg/forms/fields.py"
    ]
    corrected = executor.show_current_diff()
    assert _initial_patch_review_error(
        packet, corrected["staged_diff"],
        "Retained the evidenced consumer repair and preserved serialization.",
    ) is None


def test_initial_review_rejects_state_normalization_without_transition():
    error = _initial_patch_review_error(
        _review_packet("defer() chaining with only() loses the deferred state"),
        "--- a/query.py\n+++ b/query.py\n@@ -1 +1,2 @@\n def update(field_names):\n+    field_names = set(field_names)\n",
        "Normalized the state update input.",
    )

    assert error.startswith("STAGED_PATCH_NORMALIZES_WITHOUT_STATE_TRANSITION")


def test_initial_review_rejects_return_after_unchanged_validation():
    error = _initial_patch_review_error(
        _review_packet("Parameter checks are too strict and reject valid numeric types."),
        "--- a/validation.py\n+++ b/validation.py\n@@ -3,2 +3,3 @@\n"
        " if not isinstance(x, target_type):\n     raise TypeError\n+return x\n",
        "The return makes valid numeric inputs pass validation.",
    )

    assert error.startswith("STAGED_PATCH_RETURN_AFTER_UNCHANGED_VALIDATION")


def test_initial_review_accepts_causal_validation_type_change():
    error = _initial_patch_review_error(
        _review_packet("Parameter checks are too strict and reject valid numeric types."),
        "--- a/validation.py\n+++ b/validation.py\n@@ -1,2 +1,3 @@\n"
        "+if target_type is int:\n+    target_type = numbers.Integral\n"
        " if not isinstance(x, target_type):\n",
        "Broaden the accepted numeric relation before the failing guard.",
    )

    assert error is None


def test_eof_edit_range_is_canonicalized_before_staging(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = "def value():\n    return 1\n"
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=2, end_line=4,
        expected_source="    return 1\n",
        replacement="    return 2\n",
    ),))

    assert result["accepted"]
    assert executor.staged_edits[0].end_line == 2
    assert result["relocated"][-1]["match"] == "eof_newline_clamp"


def test_apply_edits_rejects_comment_only_ast_noop(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = "def value():\n    return 1\n"
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    with pytest.raises(ValueError, match="no executable Python AST change"):
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=1, end_line=2,
            expected_source="def value():\n    return 1",
            replacement="def value():\n    # explain value\n    return 1",
        ),))


def test_apply_edits_rejects_new_unguarded_nested_mapping_return(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "def label(data):\n"
        "    return 'id_%s' % data['name']\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    with pytest.raises(ValueError, match="unguarded nested mapping lookup"):
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=2, end_line=2,
            expected_source="    return 'id_%s' % data['name']",
            replacement="    return data['attrs']['id']",
        ),))


def test_apply_edits_allows_missing_key_preserving_mapping_get(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "def label(data):\n"
        "    return 'id_%s' % data['name']\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=2, end_line=2,
        expected_source="    return 'id_%s' % data['name']",
        replacement="    return data['attrs'].get('id')",
    ),))

    assert result["accepted"] is True


def test_apply_edits_allows_object_owned_authoritative_nested_metadata(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "class BoundItem:\n"
        "    def __init__(self, data):\n"
        "        self.data = data\n"
        "\n"
        "    def label_id(self):\n"
        "        return 'id_%s_%s' % (self.data['name'], self.data['index'])\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=6, end_line=6,
        expected_source=(
            "        return 'id_%s_%s' % "
            "(self.data['name'], self.data['index'])"
        ),
        replacement="        return self.data['attrs']['id']",
    ),))

    assert result["accepted"] is True


def test_apply_edits_rejects_notimplemented_that_ignores_class_capability(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "class Predicate:\n"
        "    conditional = True\n"
        "\n"
        "    def _combine(self, other):\n"
        "        if not isinstance(other, Predicate):\n"
        "            raise TypeError(other)\n"
        "        return (self, other)\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    with pytest.raises(ValueError, match="binary capability contract"):
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=5, end_line=6,
            expected_source=(
                "        if not isinstance(other, Predicate):\n"
                "            raise TypeError(other)"
            ),
            replacement=(
                "        if not isinstance(other, Predicate):\n"
                "            return NotImplemented"
            ),
        ),))


def test_apply_edits_allows_existing_boolean_binary_capability(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "class Predicate:\n"
        "    conditional = True\n"
        "\n"
        "    def _combine(self, other):\n"
        "        if not isinstance(other, Predicate):\n"
        "            raise TypeError(other)\n"
        "        return (self, other)\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=5, end_line=6,
        expected_source=(
            "        if not isinstance(other, Predicate):\n"
            "            raise TypeError(other)"
        ),
        replacement=(
            "        if not getattr(other, 'conditional', False):\n"
            "            raise TypeError(other)"
        ),
    ),))

    assert result["accepted"] is True


def test_apply_edits_allows_exception_operand_after_capability_guard_line_shift(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "class Predicate:\n"
        "    conditional = True\n"
        "\n"
        "    def _combine(self, other):\n"
        "        if not isinstance(other, Predicate):\n"
        "            raise TypeError(other)\n"
        "        return (self, other)\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=5, end_line=6,
        expected_source=(
            "        if not isinstance(other, Predicate):\n"
            "            raise TypeError(other)"
        ),
        replacement=(
            "        compatible = getattr(other, 'conditional', False)\n"
            "        if not compatible:\n"
            "            raise TypeError(other)"
        ),
    ),))

    assert result["accepted"] is True


def test_apply_edits_rejects_return_expression_operand_wrapper(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "class Predicate:\n"
        "    conditional = True\n"
        "\n"
        "    def _combine(self, other):\n"
        "        if not isinstance(other, Predicate):\n"
        "            raise TypeError(other)\n"
        "        return (self, other)\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    with pytest.raises(ValueError, match="wraps binary operand") as error:
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=5, end_line=6,
            expected_source=(
                "        if not isinstance(other, Predicate):\n"
                "            raise TypeError(other)"
            ),
            replacement=(
                "        if not isinstance(other, Predicate):\n"
                "            return Predicate(self)._combine(Predicate(other))"
            ),
        ),))

    assert "boolean capability contract(s) ('conditional',)" in str(error.value)
    assert "getattr(other, 'conditional', False)" in str(error.value)


def test_apply_edits_rejects_input_adapter_factory_as_output_serializer(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "def display_value(value, field):\n"
        "    return str(value)\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    with pytest.raises(ValueError, match="input/form adapter"):
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=2, end_line=2,
            expected_source="    return str(value)",
            replacement="    return field.formfield().prepare_value(value)",
        ),))


def test_apply_edits_rejects_db_preparation_as_output_serializer(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "def display_value(value, field):\n"
        "    return str(value)\n"
    )
    (repository / "module.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index,
    )

    with pytest.raises(ValueError, match="persistence/input coercion"):
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=2, end_line=2,
            expected_source="    return str(value)",
            replacement="    return field.get_prep_value(value)",
        ),))


def test_initial_review_requires_direct_exception_raising_traceback_frame():
    packet = _review_packet(
        "Q & Exists fails. ~/pkg/query.py in _combine(self, other)\n"
        "---> 73     raise TypeError(other)\nTypeError: operand",
        definitions=(
            {
                "relative_path": "pkg/query.py",
                "symbol": "_combine",
                "content": (
                    "def _combine(self, other):\n"
                    "    if not getattr(other, 'conditional', False):\n"
                    "        raise TypeError(other)\n"
                    "    return self.children + [other]\n"
                ),
                "snippet_start_line": 70,
            },
            {
                "relative_path": "pkg/expressions.py",
                "symbol": "__rand__",
                "content": (
                    "def __rand__(self, other):\n"
                    "    return combine(other, self)\n"
                ),
                "snippet_start_line": 20,
            },
        ),
    )

    error = _initial_patch_review_error(
        packet,
        "--- a/pkg/expressions.py\n+++ b/pkg/expressions.py\n"
        "@@ -20,2 +20,3 @@\n"
        " def __rand__(self, other):\n"
        "+    if getattr(other, 'conditional', False):\n"
        "+        return combine(other, self)\n"
        "     return combine(other, self)\n",
        "Added reflected dispatch while leaving the traceback frame unchanged.",
    )

    assert error.startswith("STAGED_PATCH_OMITS_DIRECT_FAILURE_FRAME")


def test_initial_review_keeps_direct_raise_owner_when_definition_ranking_is_empty():
    packet = _review_packet(
        "Q & Exists fails. ~/pkg/query.py in _combine(self, other)\n"
        "---> 73     raise TypeError(other)\nTypeError: operand",
        definitions=(),
    )

    error = _initial_patch_review_error(
        packet,
        "--- a/pkg/expressions.py\n+++ b/pkg/expressions.py\n"
        "@@ -20,2 +20,4 @@\n"
        " def __rand__(self, other):\n"
        "+    if getattr(other, 'conditional', False):\n"
        "+        return combine(other, self)\n"
        "     raise TypeError(other)\n",
        "Added reflected dispatch while leaving the direct raise owner unchanged.",
    )

    assert error.startswith("STAGED_PATCH_OMITS_DIRECT_FAILURE_FRAME")
    assert "query.py" in error


def test_initial_review_allows_direct_raise_owner_path_without_ranked_definition():
    packet = _review_packet(
        "Q & Exists fails. ~/pkg/query.py in _combine(self, other)\n"
        "---> 73     raise TypeError(other)\nTypeError: operand",
        definitions=(),
    )

    error = _initial_patch_review_error(
        packet,
        "--- a/pkg/query.py\n+++ b/pkg/query.py\n"
        "@@ -71,3 +71,3 @@\n"
        " def _combine(self, other):\n"
        "-    if not isinstance(other, Predicate):\n"
        "+    if not getattr(other, 'conditional', False):\n"
        "         raise TypeError(other)\n",
        "Changed the direct guard to use the published capability contract.",
    )

    assert error is None


def test_initial_review_rejects_logic_appended_after_unchanged_direct_raise():
    packet = _review_packet(
        "Q & Exists fails. ~/pkg/query.py in _combine(self, other)\n"
        "---> 73     raise TypeError(other)\nTypeError: operand",
        definitions=(),
    )

    error = _initial_patch_review_error(
        packet,
        "--- a/pkg/query.py\n+++ b/pkg/query.py\n"
        "@@ -70,4 +70,10 @@\n"
        " def _combine(self, other):\n"
        "     if not isinstance(other, Predicate):\n"
        "         raise TypeError(other)\n"
        "+    return combine(self, other)\n"
        "+\n"
        "+def __rand__(self, other):\n"
        "+    return combine(other, self)\n",
        "Touched the owner file and added reflected dispatch.",
    )

    assert error.startswith("STAGED_PATCH_LEAVES_DIRECT_FAILURE_GUARD_UNCHANGED")


def test_initial_review_rejects_neighbor_method_in_same_traceback_file():
    packet = _review_packet(
        "Q & Exists fails. ~/pkg/query.py in _combine(self, other)\n"
        "---> 73     raise TypeError(other)\nTypeError: operand",
        definitions=(),
    )

    error = _initial_patch_review_error(
        packet,
        "--- a/pkg/query.py\n+++ b/pkg/query.py\n"
        "@@ -88,2 +88,4 @@\n"
        " def __and__(self, other):\n"
        "-    return self._combine(other)\n"
        "+    if isinstance(other, Predicate):\n"
        "+        return self._combine(other)\n"
        "+    return NotImplemented\n",
        "Changed forward dispatch in the same source file.",
    )

    assert error.startswith("STAGED_PATCH_OMITS_DIRECT_FAILURE_SYMBOL")
    assert "_combine" in error
    assert "__and__" in error


def test_initial_review_rejects_duplicate_raise_before_capability_guard():
    packet = _review_packet(
        "Q & Exists fails. ~/pkg/query.py in _combine(self, other)\n"
        "---> 73     raise TypeError(other)\nTypeError: operand",
        definitions=(),
    )

    error = _initial_patch_review_error(
        packet,
        "--- a/pkg/query.py\n+++ b/pkg/query.py\n"
        "@@ -70,3 +70,5 @@\n"
        " def _combine(self, other):\n"
        "     if not isinstance(other, Predicate):\n"
        "+        raise TypeError(other)\n"
        "+    if not getattr(other, 'conditional', False):\n"
        "         raise TypeError(other)\n",
        "Added a capability guard while preserving incompatible behavior.",
    )

    assert error.startswith(
        "STAGED_PATCH_DUPLICATES_DIRECT_FAILURE_BEFORE_CORRECTION"
    )


class _HTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_deepseek_transport_retries_transient_503_with_lineage(monkeypatch):
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        del request, timeout
        attempts += 1
        if attempts < 3:
            raise HTTPError(
                "https://api.deepseek.com/chat/completions",
                503, "Service Temporarily Unavailable", None, None,
            )
        return _HTTPResponse({
            "choices": [{
                "message": {"role": "assistant", "content": "done"},
                "finish_reason": "stop",
            }],
            "usage": {"completion_tokens": 1},
        })

    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.urlopen", fake_urlopen,
    )
    transport = DeepSeekHTTPTransport(
        "key", max_transient_retries=3,
        retry_base_delay_seconds=0,
    )

    message = transport([], [])

    assert message["content"] == "done"
    assert attempts == 3
    assert transport.calls[0]["status"] == "RESPONSE"
    assert transport.calls[0]["retry_count"] == 2
    assert [item["transient"] for item in transport.calls[0]["attempts"][:2]] == [
        True, True,
    ]


def test_deepseek_transport_does_not_retry_authentication_error(monkeypatch):
    attempts = 0

    def fake_urlopen(request, timeout):
        nonlocal attempts
        del request, timeout
        attempts += 1
        raise HTTPError(
            "https://api.deepseek.com/chat/completions",
            401, "Unauthorized", None, None,
        )

    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.urlopen", fake_urlopen,
    )
    transport = DeepSeekHTTPTransport(
        "bad-key", max_transient_retries=3,
        retry_base_delay_seconds=0,
    )

    with pytest.raises(HTTPError):
        transport([], [])

    assert attempts == 1
    assert transport.calls[0]["status"] == "ERROR"
    assert transport.calls[0]["attempts"][0]["transient"] is False


def test_repair_tool_budgets_and_tree_range_read_cache(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "module.py").write_text(
        "VALUE = 1\n\ndef public(value):\n    return value\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    checks = {
        f"check-{index}": (sys.executable, "-c", "pass")
        for index in range(4)
    }
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        current_tree_hash="tree-one",
        public_checks=checks,
        max_search_calls=2,
        max_read_calls=4,
        max_public_checks=3,
    )

    executor.search_code("VALUE")
    executor.find_references("public")
    with pytest.raises(ValueError, match="search budget exhausted"):
        executor.search_code("return")

    first = executor.read_file("pkg/module.py", 1, 1)
    assert executor.read_file("pkg/module.py", 1, 1) == first
    assert executor.read_calls == 1
    executor.read_file("pkg/module.py", 1, 2)
    executor.read_file("pkg/module.py", 2, 3)
    executor.read_file("pkg/module.py", 3, 4)
    with pytest.raises(ValueError, match="read budget exhausted"):
        executor.read_file("pkg/module.py", 1, 4)

    for check_id in tuple(checks)[:3]:
        assert executor.run_public_check(check_id)["return_code"] == 0
    with pytest.raises(ValueError, match="public check budget exhausted"):
        executor.run_public_check("check-3")


def test_initial_instruction_requires_helpers_to_change_a_reachable_path():
    assert "wire it into that path in the same edit set" in INITIAL_REPAIR_INSTRUCTION
    assert "uncalled helper" in INITIAL_REPAIR_INSTRUCTION
    assert "bounded caller that owns that information" in INITIAL_REPAIR_INSTRUCTION
    assert "not a patch revision and not a separate candidate" in (
        INITIAL_ROOT_RECOVERY_INSTRUCTION
    )
    assert "uncalled helper" in INITIAL_ROOT_RECOVERY_INSTRUCTION
    assert "caller that owns that state" in INITIAL_ROOT_RECOVERY_INSTRUCTION
    assert "modify the existing call path" in INITIAL_ROOT_RECOVERY_INSTRUCTION


@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        ("candidate leaves the selected execution path unchanged", "change an existing"),
        ("no-op edit is not a repair", "existing executable statement"),
        ("duplicate top-level definition 'public'", "second copy"),
        ("candidate Python source is invalid", "smaller exact statement span"),
        (
            "writes through caller-owned alias 'self.query.subquery'",
            "self.query = self.query.clone()",
        ),
        ("candidate bypasses binary protocol dispatch", "reflected dispatch"),
        ("partial rectangular-index boundary", "every structurally equivalent"),
    ),
)
def test_mechanical_failure_produces_concrete_local_edit_constraint(
    failure, expected,
):
    instruction = PersistentDeepSeekAgent._mechanical_correction_instruction(
        failure,
    )

    assert expected in instruction


def test_binary_mechanical_feedback_preserves_discovered_capability_predicate():
    instruction = PersistentDeepSeekAgent._mechanical_correction_instruction(
        "candidate bypasses binary protocol dispatch. The owning class exposes "
        "boolean capability contract(s) ('conditional',); test "
        "getattr(other, 'conditional', False) instead of constructing a replacement "
        "operand."
    )

    assert "`getattr(other, 'conditional', False)`" in instruction
    assert "rejecting guard" in instruction


def test_statement_change_stages_unique_existing_executable_block(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def public(value):\n"
        "    normalized = value\n"
        "    return normalized\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    mechanism, result = PersistentDeepSeekAgent._apply_statement_change(
        executor,
        {
            "mechanism": "guard_expand",
            "relative_path": "module.py",
            "expected_statement": "    normalized = value",
            "replacement_statement": "    normalized = list(value)",
        },
    )

    assert mechanism == "guard_expand"
    assert result["edit_count"] == 1
    assert executor.staged_edits[0].start_line == 2
    assert executor.staged_edits[0].replacement == "    normalized = list(value)"


@pytest.mark.parametrize(
    ("expected", "replacement", "error"),
    (
        ("    return value", "    return value", "must differ"),
        (
            "def public(value):\n    return value",
            "def replacement(value):\n    return list(value)",
            "same single existing",
        ),
    ),
)
def test_statement_change_rejects_noop_and_definitions(
    tmp_path, expected, replacement, error,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def public(value):\n    return value\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    with pytest.raises(ValueError, match=error):
        PersistentDeepSeekAgent._apply_statement_change(executor, {
            "mechanism": "guard_expand",
            "relative_path": "module.py",
            "expected_statement": expected,
            "replacement_statement": replacement,
        })


def test_statement_change_allows_same_unique_existing_definition(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def public(value):\n    return value\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    _mechanism, result = PersistentDeepSeekAgent._apply_statement_change(
        executor, {
            "mechanism": "guard_expand",
            "relative_path": "module.py",
            "expected_statement": "def public(value):\n    return value",
            "replacement_statement": (
                "def public(value):\n"
                "    normalized = list(value)\n"
                "    return normalized"
            ),
        },
    )

    assert result["accepted"] is True
    assert executor.staged_edits[0].start_line == 1
    assert "normalized = list(value)" in executor.staged_edits[0].replacement


def test_statement_change_scopes_repeated_block_to_owner_symbol(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def first(value=None):\n"
        "    if value is None:\n"
        "        value = DEFAULT\n"
        "    return value\n\n"
        "def second(value=None):\n"
        "    if value is None:\n"
        "        value = DEFAULT\n"
        "    return value\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    _mechanism, result = PersistentDeepSeekAgent._apply_statement_change(
        executor, {
            "mechanism": "guard_expand",
            "relative_path": "module.py",
            "expected_statement": (
                "    if value is None:\n"
                "        value = DEFAULT"
            ),
            "replacement_statement": (
                "    if value is None:\n"
                "        value = SECOND_DEFAULT"
            ),
            "owner_symbol": "second",
            "anchor_line": 6,
        },
    )

    assert result["accepted"] is True
    assert executor.staged_edits[0].start_line == 7
    assert "SECOND_DEFAULT" in executor.staged_edits[0].replacement


def test_apply_edits_relocates_unique_whitespace_normalized_anchor(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "module.py").write_text(
        "def public(value):\n"
        "    if value:\n"
        "        if value.ready:\n"
        "            return value.old\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        public_checks={},
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="pkg/module.py",
        start_line=2,
        end_line=2,
        expected_source="    if value.ready:",
        replacement="    if value.ready and value.current:",
    ),))

    edit = executor.staged_edits[0]
    assert result["relocated"] == [{
        "path": "pkg/module.py",
        "from_start_line": 2,
        "to_start_line": 3,
        "match": "normalized_whitespace",
    }]
    assert edit.start_line == 3
    assert edit.expected_source == "        if value.ready:"
    assert edit.replacement == "        if value.ready and value.current:"


def test_apply_edits_relocates_unique_complete_definition(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "module.py").write_text(
        "class Point:\n"
        "\n"
        "    def vel(self, frame):\n"
        "        \"\"\"Actual public documentation.\"\"\"\n"
        "        return self.values[frame]\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="pkg/module.py", start_line=40, end_line=44,
        expected_source=(
            "    def vel(self, frame):\n"
            "        \"\"\"Stale documentation.\"\"\"\n"
            "        return self.values[frame]"
        ),
        replacement=(
            "    def vel(self, frame):\n"
            "        \"\"\"Stale documentation.\"\"\"\n"
            "        return self.values.get(frame)"
        ),
    ),))

    assert result["relocated"] == [{
        "path": "pkg/module.py",
        "from_start_line": 40,
        "to_start_line": 3,
        "match": "unique_definition",
    }]
    edit = executor.staged_edits[0]
    assert edit.start_line == 3
    assert edit.end_line == 5
    assert "Actual public documentation" in edit.expected_source


def test_apply_edits_relocates_decorated_complete_definition(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "class Report:\n"
        "\n"
        "    @property\n"
        "    def value(self):\n"
        "        \"\"\"Actual documentation.\"\"\"\n"
        "        return self._value\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=40, end_line=43,
        expected_source=(
            "    @property\n"
            "    def value(self):\n"
            "        \"\"\"Stale documentation.\"\"\"\n"
            "        return self._value"
        ),
        replacement=(
            "    @property\n"
            "    def value(self):\n"
            "        \"\"\"Stale documentation.\"\"\"\n"
            "        return self._value or 0"
        ),
    ),))

    assert result["relocated"] == [{
        "path": "module.py",
        "from_start_line": 40,
        "to_start_line": 3,
        "match": "unique_definition",
    }]
    edit = executor.staged_edits[0]
    assert edit.start_line == 3
    assert edit.end_line == 6
    assert edit.expected_source.startswith("    @property\n")


def test_apply_edits_does_not_consume_blank_definition_separator(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def normalize(value):\n"
        "    return value\n"
        "\n"
        "def consumer(value):\n"
        "    return normalize(value)\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=1, end_line=3,
        expected_source="def normalize(value):\n    return value",
        replacement=(
            "def normalize(value):\n"
            "    return _coerce(value)\n"
            "\n"
            "def _coerce(value):\n"
            "    return value"
        ),
    ),))

    edit = executor.staged_edits[0]
    assert edit.start_line == 1
    assert edit.end_line == 2
    assert not edit.expected_source.endswith("\n")


@pytest.mark.parametrize("helper_name", ("_coerce", "coerce"))
def test_apply_edits_rejects_helper_after_unchanged_path(tmp_path, helper_name):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def normalize(value):\n    return value\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    with pytest.raises(ValueError, match="selected execution path unchanged"):
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=1, end_line=2,
            expected_source="def normalize(value):\n    return value",
            replacement=(
                "def normalize(value):\n"
                "    return value\n"
                "\n\ndef " + helper_name + "(value):\n"
                "    return str(value)"
            ),
        ),))


def test_apply_edits_does_not_relocate_ambiguous_definition(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "class Left:\n"
        "    def render(self):\n"
        "        return 'left'\n"
        "class Right:\n"
        "    def render(self):\n"
        "        return 'right'\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    with pytest.raises(ValueError, match="expected source mismatch"):
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=20, end_line=22,
            expected_source=(
                "    def render(self):\n"
                "        # stale\n"
                "        return 'left'"
            ),
            replacement=(
                "    def render(self):\n"
                "        # stale\n"
                "        return 'updated'"
            ),
        ),))


def test_apply_edits_uses_unique_nearest_short_statement_anchor(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def first():\n"
        "    if ready():\n"
        "        return 1\n"
        "\n"
        "\n"
        "def second():\n"
        "    if ready():\n"
        "        return 2\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=8, end_line=8,
        expected_source="    if ready():",
        replacement="    if ready() and ready():",
    ),))

    assert result["accepted"] is True
    assert executor.staged_edits[0].start_line == 7
    assert result["relocated"][-1]["match"] == "nearest_line_anchor"


def test_apply_edits_consumes_existing_trailing_delimiter(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def collect(items):\n"
        "    values = [\n"
        "        item for item in items\n"
        "    ]\n"
        "    return values\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    result = executor.apply_edits((ProposedEdit(
        relative_path="module.py", start_line=2, end_line=3,
        expected_source=(
            "    values = [\n"
            "        item for item in items"
        ),
        replacement=(
            "    values = [\n"
            "        item for item in items\n"
            "    ]\n"
            "    seen = set(values)"
        ),
    ),))

    assert any(
        item["match"] == "trailing_source_overlap"
        for item in result["relocated"]
    )
    edit = executor.staged_edits[0]
    assert edit.end_line == 4
    assert edit.expected_source.endswith("    ]")


def test_apply_edits_rejects_trailing_overlap_that_becomes_noop(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "values = [\n    item\n]\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )

    with pytest.raises(ValueError, match="no-op edit"):
        executor.apply_edits((ProposedEdit(
            relative_path="module.py", start_line=1, end_line=2,
            expected_source="values = [\n    item",
            replacement="values = [\n    item\n]",
        ),))


def test_repair_anchor_prefers_recent_source_for_explicit_named_owner():
    context = SimpleNamespace(
        issue="Remove the label target from MultiWidget.",
        relevant_source_snippets=({
            "relative_path": "pkg/sibling.py", "snippet_start_line": 1,
            "snippet_end_line": 4, "symbol": "SplitWidget.id_for_label",
            "content": (
                "class SplitWidget:\n"
                "    def id_for_label(self, value):\n"
                "        return value"
            ),
        },),
    )
    conversation = GeneratorConversation.create("named-owner")
    conversation.messages.append({
        "role": "tool", "name": "read_file", "content": json.dumps({
            "path": "pkg/widgets.py", "start_line": 20, "end_line": 28,
            "content": (
                "class MultiWidget(Widget):\n"
                "    def id_for_label(self, value):\n"
                "        return value + '_0'"
            ),
        }),
    })

    anchors = PersistentDeepSeekAgent._exact_source_anchor(
        context, conversation,
    )

    assert anchors[0]["relative_path"] == "pkg/widgets.py"
    assert "class MultiWidget" in anchors[0]["content"]


def test_large_named_owner_anchor_keeps_recent_causal_source_in_budget():
    methods = "\n".join(
        f"    def unrelated_{index}(self):\n        return {index}"
        for index in range(400)
    )
    context = SimpleNamespace(
        issue="QuerySet.defer() must compose with QuerySet.only().",
        relevant_source_snippets=({
            "relative_path": "pkg/queryset.py", "snippet_start_line": 1,
            "snippet_end_line": 805, "symbol": "pkg.queryset.QuerySet",
            "content": (
                "class QuerySet:\n"
                "    def defer(self, fields):\n"
                "        return self.query.defer(fields)\n"
                "    def only(self, fields):\n"
                "        return self.query.only(fields)\n"
                + methods
            ),
        },),
    )
    conversation = GeneratorConversation.create("bounded-owner")
    conversation.messages.append({
        "role": "assistant", "content": "", "tool_calls": [{
            "id": "request-causal", "type": "function", "function": {
                "name": "request_program_slice", "arguments": json.dumps({
                    "symbols": ["Query.add_deferred_loading"],
                    "relation_kinds": ["reads", "writes"],
                }),
            },
        }],
    })
    conversation.messages.append({
        "role": "tool", "name": "read_file", "content": json.dumps({
            "path": "pkg/sql_query.py", "start_line": 200, "end_line": 210,
            "content": (
                "    def add_deferred_loading(self, fields):\n"
                "        existing, defer = self.deferred_loading\n"
                "        self.deferred_loading = existing.difference(fields), defer"
            ),
        }),
    })

    anchors = PersistentDeepSeekAgent._exact_source_anchor(
        context, conversation,
    )

    assert sum(len(item["content"]) for item in anchors) <= 14000
    assert any(
        item["relative_path"] == "pkg/sql_query.py" for item in anchors
    )
    assert any("def defer" in item["content"] for item in anchors)
    assert anchors[0]["relative_path"] == "pkg/sql_query.py"


def test_recovery_anchor_materializes_bounded_search_result_source(tmp_path):
    repository = tmp_path / "repo"
    (repository / "pkg").mkdir(parents=True)
    (repository / "pkg" / "formatter.py").write_text(
        "class Formatter:\n"
        "    def make_path(self, path):\n"
        "        return self.base.bestrelpath(path)\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    tools = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="Displayed paths must remain relative to the original directory.",
        relevant_source_snippets=(),
    )
    conversation = GeneratorConversation.create("search-source")
    conversation.messages.extend((
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "search", "type": "function", "function": {
                "name": "search_code",
                "arguments": json.dumps({"query": "bestrelpath"}),
            },
        }]},
        {"role": "tool", "name": "search_code", "content": json.dumps({
            "matches": [{
                "path": "pkg/formatter.py", "line": 3,
                "text": "return self.base.bestrelpath(path)",
            }],
            "truncated": False,
        })},
    ))

    anchors = PersistentDeepSeekAgent._exact_source_anchor(
        context, conversation, tools,
    )

    assert anchors[0]["relative_path"] == "pkg/formatter.py"
    assert "def make_path" in anchors[0]["content"]
    assert anchors[0]["start_line"] == 1


def test_program_slice_request_ends_revision_until_context_is_materialized(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def public():\n    return 1\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), relevant_source_snippets=(),
        active_program_slice={"files": (), "symbols": ()},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    calls = 0

    def transport(messages, schemas):
        nonlocal calls
        calls += 1
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": "slice",
                "type": "function",
                "function": {
                    "name": "request_program_slice",
                    "arguments": json.dumps({
                        "symbols": ["pkg.api.public"],
                        "relation_kinds": ["calls"],
                    }),
                },
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={}),
            repository_index=index,
        ),
        GeneratorConversation.create("case"),
        executor,
    )

    assert calls == 1
    assert revision.tool_turns == 1
    assert revision.status == "CONTEXT_ONLY"
    assert revision.context_requests[0].symbols == ("pkg.api.public",)


def test_compacted_correction_keeps_primary_repair_packet():
    conversation = GeneratorConversation.create("case")
    primary = '{"initial_repair_packet":{"issue_text":"normative issue"}}'
    conversation.messages.extend((
        {"role": "user", "content": primary},
        {"role": "assistant", "content": "truncated", "finish_reason": "length"},
        {"role": "user", "content": "return one compact action"},
    ))

    messages = PersistentDeepSeekAgent._request_messages(conversation)

    assert any(message.get("content") == primary for message in messages)
    assert messages[-1]["content"] == "return one compact action"
    assert any(
        "primary repair task" in message.get("content", "")
        for message in messages
    )


def test_initial_recovery_replays_reads_without_failed_action_chain(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def public(value):\n    return value\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="public() must normalize values", working_diff="",
        failed_checks=(), counterexamples=(), first_trace_divergences=(),
        causal_repair_cuts=(), causal_cut_candidates=(),
        relevant_source_snippets=(),
        active_program_slice={"files": ("module.py",), "symbols": ()},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": "public() must normalize values"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_initial_repair_packet",
        lambda *_args, **_kwargs: pytest.fail(
            "initial recovery must reference, not rebuild, the primary packet"
        ),
    )
    conversation = GeneratorConversation.create("recovery-evidence")
    conversation.messages.extend((
        {"role": "user", "content": "original repair packet"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "name": "read_file", "content": json.dumps({
            "path": "module.py", "start_line": 1, "end_line": 2,
            "content": "def public(value):\n    return value",
        })},
        {"role": "tool", "name": "apply_edits", "content": json.dumps({
            "error": "ValueError: no-op edit is not a repair",
        })},
    ))

    def transport(messages, schemas):
        payload = json.loads(messages[-1]["content"])
        assert payload["inspected_source_evidence"][0]["content"].startswith(
            "def public"
        )
        assert payload["previous_mechanical_failures"] == [
            "ValueError: no-op edit is not a repair"
        ]
        assert "first user message" in payload["primary_task_reference"]
        assert "existing executable statement" in payload["required_correction"]
        assert "no-op edit" not in " ".join(
            message.get("content", "")
            for message in messages[:-1]
            if isinstance(message.get("content", ""), str)
        )
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "blocked", "type": "function", "function": {
                    "name": "declare_blocker",
                    "arguments": json.dumps({"reason": "fixture complete"}),
                },
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=2,
    ).recover_initial_patch(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={}),
            repository_index=index,
        ),
        conversation,
        executor,
    )

    assert revision.status == "DECLARED_BLOCKER"


def test_length_response_forces_compact_edit_without_losing_issue(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        public_checks={},
    )
    context = SimpleNamespace(
        issue="normative issue", working_diff="", failed_checks=(),
        counterexamples=(), first_trace_divergences=(),
        causal_repair_cuts=(), causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "start_line": 1, "end_line": 1,
            "symbol": "VALUE", "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("VALUE",)},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "normative issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )

    class TruncatedThenStructured:
        def __init__(self):
            self.calls = 0

        def __call__(self, messages, schemas):
            self.calls += 1
            assert self.calls == 1
            return {
                "role": "assistant", "content": "x" * 100,
                "finish_reason": "length",
            }

        def call_with_tool_choice(self, messages, schemas, tool_choice):
            self.calls += 1
            available = {
                schema["function"]["name"] for schema in schemas
            }
            assert "normative issue" in "\n".join(
                str(message.get("content", "")) for message in messages
            )
            assert tool_choice == "required"
            if self.calls == 2:
                assert available == {
                    "apply_edits", "replace_staged_edits",
                    "finish_revision", "declare_blocker",
                }
                return {
                    "role": "assistant", "content": "", "tool_calls": [{
                        "id": "edit", "type": "function", "function": {
                            "name": "apply_edits", "arguments": json.dumps({
                                "mechanism": "initial_issue_repair",
                                "edits": [{
                                    "relative_path": "module.py",
                                    "start_line": 1, "end_line": 1,
                                    "expected_source": "VALUE = 1",
                                    "replacement": "VALUE = 2",
                                }],
                            }),
                        },
                    }],
                }
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "finish", "type": "function", "function": {
                        "name": "finish_revision", "arguments": json.dumps({
                            "summary": "reviewed the complete diff",
                        }),
                    },
                }],
            }

    transport = TruncatedThenStructured()
    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={"module.py": ()}),
            repository_index=index,
        ),
        GeneratorConversation.create("case"),
        executor,
    )

    assert transport.calls == 3
    assert revision.status == "PROPOSED"
    assert revision.edits[0].replacement == "VALUE = 2"


def test_initial_review_can_replace_unnecessary_multifile_edit_set(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "api.py").write_text(
        "def public(value):\n    return value\n", encoding="utf-8",
    )
    (repository / "shared.py").write_text(
        "def identity(value):\n    return value\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = "public(value) must normalize value without changing shared identity()."
    context = SimpleNamespace(
        issue=issue, public_discussion="", working_diff="", failed_checks=(),
        counterexamples=(), first_trace_divergences=(),
        causal_repair_cuts=(), causal_cut_candidates=(), requirement_coverage=(),
        relevant_source_snippets=({
            "relative_path": "api.py", "snippet_start_line": 1,
            "snippet_end_line": 2, "symbol": "public",
            "content": "def public(value):\n    return value",
        },),
        active_program_slice={"files": ("api.py",), "symbols": ("public",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    calls = 0

    def transport(messages, schemas):
        nonlocal calls
        calls += 1
        available = {item["function"]["name"] for item in schemas}
        if calls == 1:
            name = "apply_edits"
            arguments = {
                "mechanism": "overbroad_initial_repair",
                "edits": [{
                    "relative_path": "api.py", "start_line": 2, "end_line": 2,
                    "expected_source": "    return value",
                    "replacement": "    return (value,)",
                }, {
                    "relative_path": "shared.py", "start_line": 2, "end_line": 2,
                    "expected_source": "    return value",
                    "replacement": "    return (value,)",
                }],
            }
        elif calls == 2:
            assert "replace_staged_edits" in available
            assert "SYSTEM_STAGED_DIFF_PREVIEW" in messages[-1]["content"]
            name = "replace_staged_edits"
            arguments = {
                "mechanism": "localized_initial_repair",
                "edits": [{
                    "relative_path": "api.py", "start_line": 2, "end_line": 2,
                    "expected_source": "    return value",
                    "replacement": "    return (value,)",
                }],
            }
        else:
            assert "finish_revision" in available
            assert "SYSTEM_STAGED_DIFF_PREVIEW" in messages[-1]["content"]
            name = "finish_revision"
            arguments = {
                "summary": "localized root-cause repair with shared behavior preserved",
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        runtime_metrics={},
        requirement_graph=SimpleNamespace(leaves={}),
        active_binding_graph=SimpleNamespace(units={}),
        program_graph=SimpleNamespace(file_index={"api.py": ()}),
        repository_index=index,
    )
    conversation = GeneratorConversation.create("multifile-review")

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(state, conversation, executor)

    assert calls == 3
    assert revision.status == "PROPOSED"
    assert revision.mechanism == "localized_initial_repair"
    assert [edit.relative_path for edit in revision.edits] == ["api.py"]
    assert executor.staged_edit_version == 2
    assert executor.finished_staged_version == 2
    readiness = state.runtime_metrics["first_patch_readiness"]
    assert all(readiness[key] for key in (
        "target_definition_read",
        "root_cause_identified",
        "requirements_accounted_for",
        "preservation_risks_identified",
        "final_diff_reviewed",
    ))
    assert readiness["caller_inspection_status"] in {
        "FOUND_AND_READ", "NOT_FOUND_AFTER_BOUNDED_SEARCH",
    }
    assert readiness["test_or_contract_inspection_status"] in {
        "FOUND_AND_READ", "NOT_FOUND_AFTER_BOUNDED_SEARCH",
    }


def test_unresolved_quality_review_returns_explicit_rejected_status(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "api.py").write_text(
        "def public(value):\n    return value\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = "public(value) must return the normalized successor value."
    context = SimpleNamespace(
        issue=issue, public_discussion="", working_diff="", failed_checks=(),
        counterexamples=(), first_trace_divergences=(),
        causal_repair_cuts=(), causal_cut_candidates=(), requirement_coverage=(),
        relevant_source_snippets=({
            "relative_path": "api.py", "snippet_start_line": 1,
            "snippet_end_line": 2, "symbol": "public",
            "content": "def public(value):\n    return value",
        },),
        active_program_slice={"files": ("api.py",), "symbols": ("public",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    calls = 0

    def transport(messages, schemas):
        nonlocal calls
        calls += 1
        if calls == 1:
            name = "apply_edits"
            arguments = {
                "mechanism": "unchecked_successor",
                "edits": [{
                    "relative_path": "api.py", "start_line": 2, "end_line": 2,
                    "expected_source": "    return value",
                    "replacement": "    return value + 1",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {
                "summary": (
                    "This patch is incomplete and does not address the actual issue."
                ),
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        runtime_metrics={}, requirement_graph=SimpleNamespace(leaves={}),
        active_binding_graph=SimpleNamespace(units={}),
        program_graph=SimpleNamespace(file_index={"api.py": ()}),
        repository_index=index,
    )
    conversation = GeneratorConversation.create("unresolved-review")
    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=2,
    ).generate_initial_patch(
        state, conversation, executor,
    )

    assert revision.status == "STAGED_PATCH_REVIEW_REJECTED"
    assert revision.edits
    assert executor.finished_staged_version != executor.staged_edit_version
    assert executor.staged_quality_rejected
    assert calls >= 3

    recovery_calls = 0

    def recovery_transport(messages, schemas):
        nonlocal recovery_calls
        recovery_calls += 1
        available = {item["function"]["name"] for item in schemas}
        if recovery_calls == 1:
            assert "replace_staged_edits" in available
            name = "replace_staged_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "api.py", "start_line": 2, "end_line": 2,
                    "expected_source": "    return value",
                    "replacement": "    return value + 2",
                }],
            }
        else:
            assert "finish_revision" in available
            name = "finish_revision"
            arguments = {
                "summary": "Reviewed the complete reachable successor repair.",
            }
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"recovery-{recovery_calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    recovered = PersistentDeepSeekAgent(
        recovery_transport, max_tool_turns=2,
    ).recover_initial_patch(state, conversation, executor)

    assert recovered.status == "PROPOSED"
    assert recovered.edits[0].replacement == "    return value + 2"
    assert executor.finished_staged_version == executor.staged_edit_version
    assert not executor.staged_quality_rejected


def test_root_recovery_for_empty_patch_gets_structural_edit_correction(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "start_line": 1, "end_line": 1,
            "symbol": "VALUE", "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("VALUE",)},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue", "working_diff": ""},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    calls: list[set[str]] = []

    def transport(messages, schemas):
        available = {schema["function"]["name"] for schema in schemas}
        calls.append(available)
        if len(calls) == 1:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "model-blocker", "type": "function",
                    "function": {
                        "name": "declare_blocker",
                        "arguments": json.dumps({
                            "reason": "more evidence would be helpful",
                        }),
                    },
                }],
            }
        if "finish_revision" in available:
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": "root-correction-review", "type": "function",
                    "function": {
                        "name": "finish_revision",
                        "arguments": json.dumps({
                            "summary": "reviewed the structural recovery diff",
                        }),
                    },
                }],
            }
        assert available == {"apply_edits", "request_program_slice"}
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": "root-correction-edit", "type": "function",
                "function": {
                    "name": "apply_edits",
                    "arguments": json.dumps({
                        "mechanism": "root_recovery_edit",
                        "edits": [{
                            "relative_path": "module.py", "start_line": 1,
                            "end_line": 1, "expected_source": "VALUE = 1",
                            "replacement": "VALUE = 2",
                        }],
                    }),
                },
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={"module.py": ()}),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert len(calls) == 3
    assert revision.status == "PROPOSED"
    assert revision.mechanism == "root_recovery_edit"
    assert revision.edits[0].replacement == "VALUE = 2"


def test_initial_structural_recovery_records_final_preview_for_readiness(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = "VALUE must be 2 while preserving module import behavior."
    context = SimpleNamespace(
        issue=issue, public_discussion="", working_diff="", failed_checks=(),
        counterexamples=(), first_trace_divergences=(),
        causal_repair_cuts=(), causal_cut_candidates=(), requirement_coverage=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE", "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("VALUE",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    calls = 0

    def transport(messages, schemas):
        nonlocal calls
        calls += 1
        available = {item["function"]["name"] for item in schemas}
        if calls == 1:
            name = "declare_blocker"
            arguments = {"reason": "need compact structural synthesis"}
        elif "apply_edits" in available:
            name = "apply_edits"
            arguments = {
                "mechanism": "structural_initial_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1, "end_line": 1,
                    "expected_source": "VALUE = 1", "replacement": "VALUE = 2",
                }],
            }
        else:
            assert available <= {"finish_revision", "replace_staged_edits"}
            assert "staged_patch_preview" in messages[-1]["content"]
            name = "finish_revision"
            arguments = {"summary": "reviewed compact root-cause repair"}
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{calls}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    state = SimpleNamespace(
        runtime_config={"primary_issue": issue, "visible_test_paths": ()},
        runtime_metrics={},
        requirement_graph=SimpleNamespace(leaves={}),
        active_binding_graph=SimpleNamespace(units={}),
        program_graph=SimpleNamespace(file_index={"module.py": ()}),
        repository_index=index,
    )
    conversation = GeneratorConversation.create("structural-readiness")

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).generate_initial_patch(state, conversation, executor)

    assert revision.status == "PROPOSED"
    readiness = state.runtime_metrics["first_patch_readiness"]
    assert readiness["final_diff_reviewed"] is True
    assert readiness["root_cause_identified"] is True
    assert readiness["requirements_accounted_for"] is True
    assert readiness["preservation_risks_identified"] is True


def test_redundant_program_slice_request_is_corrected_into_an_edit(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository,
        repository_index=index,
        public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), relevant_source_snippets=(),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn == 1:
            name = "request_program_slice"
            arguments = {
                "symbols": ["pkg.api.public"],
                "relation_kinds": ["calls"],
            }
        elif turn == 2:
            assert any(
                "CONTEXT_ALREADY_ACTIVE" in message.get("content", "")
                for message in messages
            )
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {"summary": "changed the active failing source"}
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    program_graph = SimpleNamespace(
        file_index={"module.py": ()},
        resolve_symbol=lambda symbol: ("node",) if symbol.endswith("public") else (),
    )
    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(program_graph=program_graph),
        GeneratorConversation.create("case"),
        executor,
    )

    assert turn == 3
    assert revision.status == "PROPOSED"
    assert revision.edits[0].replacement == "VALUE = 2"
    assert not revision.context_requests


def test_unknown_symbol_cannot_bypass_active_slice_during_synthesis(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "VALUE = 1\n\ndef public():\n    return VALUE\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE",
            "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn <= 2:
            name, arguments = "show_current_diff", {}
        elif turn == 3:
            name = "request_program_slice"
            arguments = {
                "symbols": ["pkg.api.public", "pkg.api._missing"],
                "relation_kinds": ["calls"],
            }
        elif turn == 4:
            assert any(
                "SYMBOL_NOT_FOUND" in message.get("content", "")
                and "VALUE = 1" in message.get("content", "")
                for message in messages
            )
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {"summary": "used the grounded active source"}
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    program_graph = SimpleNamespace(
        file_index={"module.py": ()},
        resolve_symbol=lambda symbol: ("node",) if symbol.endswith("public") else (),
    )
    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(program_graph=program_graph, repository_index=index),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 5
    assert revision.status == "PROPOSED"
    assert revision.edits[0].replacement == "VALUE = 2"
    assert not revision.context_requests


def test_synthesis_analysis_without_tool_gets_a_correction_turn(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE",
            "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ()},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn <= 2:
            name, arguments = "show_current_diff", {}
        elif turn == 3:
            return {
                "role": "assistant",
                "content": "A long analysis that forgot to call a tool.",
            }
        elif turn == 4:
            assert any(
                "Analysis text without a tool call" in message.get("content", "")
                and "VALUE = 1" in message.get("content", "")
                for message in messages
            )
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {"summary": "completed after synthesis correction"}
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(
            program_graph=SimpleNamespace(file_index={}),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 5
    assert revision.status == "PROPOSED"
    assert revision.edits[0].replacement == "VALUE = 2"


def test_synthesis_edit_mismatch_returns_actual_source_before_budget_ends(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="issue", working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), relevant_source_snippets=(),
        active_program_slice={"files": ("module.py",), "symbols": ()},
        failure_signature="failure", first_project_frame=None,
        baseline_output={"stderr": "failure"},
        to_dict=lambda: {"issue": "issue"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn <= 2:
            name, arguments = "show_current_diff", {}
        elif turn == 3:
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = stale",
                    "replacement": "VALUE = 2",
                }],
            }
        elif turn == 4:
            assert any(
                "actual source at requested range: 'VALUE = 1'"
                in message.get("content", "")
                for message in messages
            )
            name = "apply_edits"
            arguments = {
                "mechanism": "initial_issue_repair",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = 1",
                    "replacement": "VALUE = 2",
                }],
            }
        else:
            name = "finish_revision"
            arguments = {"summary": "corrected exact source mismatch"}
        return {
            "role": "assistant",
            "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=6,
    ).generate_initial_patch(
        SimpleNamespace(program_graph=SimpleNamespace(file_index={})),
        GeneratorConversation.create("case"),
        executor,
    )

    assert turn == 5
    assert revision.status == "PROPOSED"
    assert revision.edits[0].expected_source == "VALUE = 1"


def test_structural_correction_returns_accepted_slice_to_controller(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "VALUE = 1\n\ndef needed():\n    return VALUE\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    context = SimpleNamespace(
        issue="needed() must return the normalized public value",
        working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE",
            "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ()},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": "needed() must return the normalized public value"},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        if turn == 1:
            name = "declare_blocker"
            arguments = {"reason": "need the exact needed implementation"}
        elif turn == 2:
            assert "needed() must return" in messages[-1]["content"]
            name = "request_program_slice"
            arguments = {"symbols": ["needed"], "relation_kinds": ["calls"]}
        else:
            raise AssertionError("accepted context expansion must end the revision")
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(
                file_index={"module.py": ()}, resolve_symbol=lambda symbol: (),
            ),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 2
    assert revision.status == "CONTEXT_ONLY"
    assert revision.context_requests[0].symbols == ("needed",)
    assert not revision.edits


def test_structural_recovery_retries_rejected_edit_with_issue_and_actual_source(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "VALUE = 1\n\ndef public():\n    return VALUE\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = "public() must return 2 while preserving its callable API"
    context = SimpleNamespace(
        issue=issue, working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 1, "symbol": "VALUE",
            "content": "VALUE = 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        available = {item["function"]["name"] for item in schemas}
        if (
            "finish_revision" in available
            and available <= {"finish_revision", "replace_staged_edits"}
        ):
            name = "finish_revision"
            arguments = {"summary": "reviewed the recovered exact-source edit"}
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": f"turn-{turn}", "type": "function",
                    "function": {
                        "name": name, "arguments": json.dumps(arguments),
                    },
                }],
            }
        if turn == 1:
            name = "declare_blocker"
            arguments = {"reason": "need one more source decision"}
        elif turn == 2:
            name = "request_program_slice"
            arguments = {"symbols": ["public"], "relation_kinds": ["calls"]}
        elif turn == 3:
            assert issue in messages[-1]["content"]
            name = "apply_edits"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "edits": [{
                    "relative_path": "module.py", "start_line": 1,
                    "end_line": 1, "expected_source": "VALUE = stale",
                    "replacement": "VALUE = 2",
                }],
            }
        elif turn == 4:
            assert issue in messages[-1]["content"]
            assert "actual source at requested range: 'VALUE = 1'" in messages[-1]["content"]
            assert "same single existing name and type" in messages[-1]["content"]
            assert [item["function"]["name"] for item in schemas] == [
                "apply_statement_change"
            ]
            name = "apply_statement_change"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "relative_path": "module.py",
                "expected_statement": "VALUE = 1",
                "replacement_statement": "VALUE = 2",
            }
        else:
            raise AssertionError("structural retry must remain bounded")
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(
                file_index={"module.py": ()},
                resolve_symbol=lambda symbol: ("node",) if symbol == "public" else (),
            ),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 5
    assert revision.status == "PROPOSED"
    assert revision.edits[0].expected_source == "VALUE = 1"
    assert revision.edits[0].replacement == "VALUE = 2"


def test_structural_recovery_corrects_rejected_statement_without_new_revision(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def first(value=None):\n"
        "    if value is None:\n"
        "        value = DEFAULT\n"
        "    return value\n\n"
        "def public(value=None):\n"
        "    if value is None:\n"
        "        value = DEFAULT\n"
        "    return value\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = "public() must use PUBLIC_DEFAULT while preserving first()"
    context = SimpleNamespace(
        issue=issue, working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), requirement_coverage=({
            "requirement_id": "requirement-public-default",
            "authority": "A", "status": "UNBOUND",
            "normalized_requirement": issue,
        },),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 6,
            "snippet_end_line": 9, "symbol": "public",
            "content": (
                "def public(value=None):\n"
                "    if value is None:\n"
                "        value = DEFAULT\n"
                "    return value"
            ),
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        available = {item["function"]["name"] for item in schemas}
        if (
            "finish_revision" in available
            and available <= {"finish_revision", "replace_staged_edits"}
        ):
            name = "finish_revision"
            arguments = {"summary": "reviewed the corrected statement diff"}
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": f"turn-{turn}", "type": "function",
                    "function": {
                        "name": name, "arguments": json.dumps(arguments),
                    },
                }],
            }
        if turn == 1:
            name = "declare_blocker"
            arguments = {"reason": "need synthesis"}
        elif turn == 2:
            name = "request_program_slice"
            arguments = {"symbols": ["public"], "relation_kinds": ["calls"]}
        elif turn == 3:
            name = "apply_edits"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "edits": [{
                    "relative_path": "module.py", "start_line": 6,
                    "end_line": 9, "expected_source": "stale source",
                    "replacement": "def public():\n    return PUBLIC_DEFAULT",
                }],
            }
        elif turn == 4:
            assert [item["function"]["name"] for item in schemas] == [
                "apply_statement_change"
            ]
            name = "apply_statement_change"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "relative_path": "module.py",
                "expected_statement": "    return value",
                "replacement_statement": "    return value",
            }
        elif turn == 5:
            assert issue in messages[-1]["content"]
            assert len(messages) == 3
            name = "apply_statement_change"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "relative_path": "module.py",
                "expected_statement": (
                    "    if value is None:\n"
                    "        value = DEFAULT"
                ),
                "replacement_statement": (
                    "    if value is None:\n"
                    "        value = PUBLIC_DEFAULT"
                ),
                "owner_symbol": "public",
                "anchor_line": 6,
            }
        else:
            raise AssertionError("structural recovery exceeded its bounded retry")
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(
                file_index={"module.py": ()},
                resolve_symbol=lambda symbol: (
                    ("node",) if symbol == "public" else ()
                ),
            ),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 6
    assert revision.status == "PROPOSED"
    assert revision.tool_turns == 1
    assert revision.edits[0].start_line == 7
    assert "PUBLIC_DEFAULT" in revision.edits[0].replacement


def test_structural_recovery_uses_multi_edit_retry_for_new_import(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def public():\n    return 1\n", encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = "public() must delegate to pkg.router.allowed()"
    context = SimpleNamespace(
        issue=issue, working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), requirement_coverage=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 2, "symbol": "public",
            "content": "def public():\n    return 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        available = {item["function"]["name"] for item in schemas}
        if (
            "finish_revision" in available
            and available <= {"finish_revision", "replace_staged_edits"}
        ):
            name = "finish_revision"
            arguments = {"summary": "reviewed import and reachable delegation"}
        elif turn == 1:
            name = "declare_blocker"
            arguments = {"reason": "need synthesis"}
        elif turn == 2:
            name = "request_program_slice"
            arguments = {"symbols": ["public"], "relation_kinds": ["calls"]}
        elif turn == 3:
            name = "apply_edits"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "edits": [{
                    "relative_path": "module.py", "start_line": 2,
                    "end_line": 2, "expected_source": "    return stale",
                    "replacement": "    return router.allowed()",
                }],
            }
        elif turn == 4:
            assert [item["function"]["name"] for item in schemas] == [
                "apply_statement_change"
            ]
            name = "apply_statement_change"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "relative_path": "module.py",
                "expected_statement": "    return 1",
                "replacement_statement": "    return router.allowed()",
                "owner_symbol": "public",
                "anchor_line": 1,
            }
        elif turn == 5:
            assert [item["function"]["name"] for item in schemas] == [
                "apply_edits"
            ]
            assert "same complete edit set" in messages[-1]["content"]
            name = "apply_edits"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "edits": [
                    {
                        "relative_path": "module.py", "start_line": 1,
                        "end_line": 1, "expected_source": "def public():",
                        "replacement": "from pkg import router\n\ndef public():",
                    },
                    {
                        "relative_path": "module.py", "start_line": 2,
                        "end_line": 2, "expected_source": "    return 1",
                        "replacement": "    return router.allowed()",
                    },
                ],
            }
        else:
            raise AssertionError("multi-edit structural recovery exceeded its budget")
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(
                file_index={"module.py": ()},
                resolve_symbol=lambda symbol: (
                    ("node",) if symbol == "public" else ()
                ),
            ),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 6
    assert revision.status == "PROPOSED"
    assert len(revision.edits) == 2
    assert any("from pkg import router" in edit.replacement for edit in revision.edits)
    assert any("router.allowed()" in edit.replacement for edit in revision.edits)


def test_structural_recovery_switches_from_unused_import_to_local_statement(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "module.py").write_text(
        "def public():\n    return 1\n", encoding="utf-8",
    )
    (repository / "router_usage.py").write_text(
        "from pkg import router\n\n"
        "def existing():\n    return router.allowed()\n",
        encoding="utf-8",
    )
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = "public() must delegate its decision to the existing router"
    context = SimpleNamespace(
        issue=issue, working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(), requirement_coverage=(),
        relevant_source_snippets=({
            "relative_path": "module.py", "snippet_start_line": 1,
            "snippet_end_line": 2, "symbol": "public",
            "content": "def public():\n    return 1",
        },),
        active_program_slice={"files": ("module.py",), "symbols": ("public",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        available = {item["function"]["name"] for item in schemas}
        if (
            "finish_revision" in available
            and available <= {"finish_revision", "replace_staged_edits"}
        ):
            name = "finish_revision"
            arguments = {
                "summary": (
                    "Reviewed the complete diff: public now delegates to the "
                    "repository router while preserving its callable API."
                ),
            }
        elif turn == 1:
            name = "declare_blocker"
            arguments = {"reason": "need bounded synthesis"}
        elif turn == 2:
            name = "request_program_slice"
            arguments = {"symbols": ["public"], "relation_kinds": ["calls"]}
        elif turn == 3:
            assert [item["function"]["name"] for item in schemas] == [
                "apply_edits"
            ]
            name = "apply_edits"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "edits": [
                    {
                        "relative_path": "module.py", "start_line": 1,
                        "end_line": 1, "expected_source": "def public():",
                        "replacement": "from pkg import router\n\ndef public():",
                    },
                    {
                        "relative_path": "module.py", "start_line": 2,
                        "end_line": 2, "expected_source": "    return 1",
                        "replacement": "    return 2",
                    },
                ],
            }
        elif turn == 4:
            # An import that never enters the execution path is immediately
            # localized to a statement repair. The executor then mechanically
            # combines the unresolved statement name with the indexed import.
            assert [item["function"]["name"] for item in schemas] == [
                "apply_statement_change"
            ]
            assert "unused direct import" in messages[-1]["content"]
            name = "apply_statement_change"
            arguments = {
                "mechanism": "causal_slice_rewrite",
                "relative_path": "module.py",
                "expected_statement": "    return 1",
                "replacement_statement": "    return router.allowed()",
                "owner_symbol": "public",
                "anchor_line": 1,
            }
        else:
            raise AssertionError("structural recovery exceeded its bounded retries")
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(
                file_index={"module.py": ()},
                resolve_symbol=lambda symbol: (
                    ("node",) if symbol == "public" else ()
                ),
            ),
            repository_index=index,
        ),
        GeneratorConversation.create("case"), executor,
    )

    assert turn == 5
    assert revision.status == "PROPOSED"
    assert len(revision.edits) == 2
    assert any("from pkg import router" in edit.replacement for edit in revision.edits)
    assert any("router.allowed()" in edit.replacement for edit in revision.edits)


def test_structural_recovery_uses_mechanical_state_consumer_anchor(
    tmp_path, monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    source = (
        "class Query:\n"
        "    def defer(self, field_names):\n"
        "        existing, mode = self.state\n"
        "        self.state = existing.difference(field_names), False\n"
        "\n"
        "    def materialize(self):\n"
        "        field_names, defer = self.state\n"
        "        if not field_names:\n"
        "            return []\n"
        "        if defer:\n"
        "            return exclude(field_names)\n"
        "        return include(field_names)\n"
    )
    (repository / "query.py").write_text(source, encoding="utf-8")
    index = build_repository_index(
        repository, max_files=10, deadline=Deadline.after(10),
    )
    executor = RepairToolExecutor(
        repository_root=repository, repository_index=index, public_checks={},
    )
    issue = (
        "An empty normalized field set in exclusion mode must still be "
        "interpreted as exclusion mode."
    )
    context = SimpleNamespace(
        issue=issue, working_diff="", failed_checks=(), counterexamples=(),
        first_trace_divergences=(), causal_repair_cuts=(),
        causal_cut_candidates=(),
        relevant_source_snippets=({
            "relative_path": "query.py", "snippet_start_line": 2,
            "snippet_end_line": 4, "symbol": "Query.defer",
            "content": "\n".join(source.splitlines()[1:4]),
        },),
        active_program_slice={"files": ("query.py",), "symbols": ("Query.defer",)},
        failure_signature=None, first_project_frame=None,
        baseline_output={"stderr": ""},
        to_dict=lambda: {"issue": issue},
    )
    monkeypatch.setattr(
        "reachpatch.repair.deepseek_agent.build_repair_context",
        lambda state, mode: context,
    )
    turn = 0

    def producer_edit():
        return {
            "mechanism": "state_update_order",
            "edits": [{
                "relative_path": "query.py", "start_line": 4,
                "end_line": 4,
                "expected_source": (
                    "        self.state = "
                    "existing.difference(field_names), False"
                ),
                "replacement": (
                    "        remaining = existing.difference(field_names)\n"
                    "        if remaining:\n"
                    "            self.state = remaining, False\n"
                    "        else:\n"
                    "            self.state = frozenset(field_names), True"
                ),
            }],
        }

    def transport(messages, schemas):
        nonlocal turn
        turn += 1
        available = {item["function"]["name"] for item in schemas}
        if (
            "finish_revision" in available
            and available <= {"finish_revision", "replace_staged_edits"}
        ):
            name = "finish_revision"
            arguments = {"summary": "reviewed the state-consumer repair diff"}
            return {
                "role": "assistant", "content": "", "tool_calls": [{
                    "id": f"turn-{turn}", "type": "function",
                    "function": {
                        "name": name, "arguments": json.dumps(arguments),
                    },
                }],
            }
        if turn == 1:
            name = "declare_blocker"
            arguments = {"reason": "need a bounded structural correction"}
        elif turn == 2:
            name = "apply_edits"
            arguments = producer_edit()
        elif turn == 3:
            recovery_prompt = messages[-1]["content"]
            assert "mechanical_recovery_anchors" in recovery_prompt
            assert "STATE_CONSUMER_GUARD" in recovery_prompt
            assert "if not field_names" in recovery_prompt
            name = "apply_edits"
            arguments = {
                "mechanism": "guard_tighten",
                "edits": [{
                    "relative_path": "query.py", "start_line": 8,
                    "end_line": 9,
                    "expected_source": (
                        "        if not field_names:\n"
                        "            return []"
                    ),
                    "replacement": (
                        "        if not field_names and not defer:\n"
                        "            return []"
                    ),
                }],
            }
        else:
            raise AssertionError("state consumer recovery must remain bounded")
        return {
            "role": "assistant", "content": "", "tool_calls": [{
                "id": f"turn-{turn}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }],
        }

    revision = PersistentDeepSeekAgent(
        transport, max_tool_turns=8,
    ).root_recovery(
        SimpleNamespace(
            program_graph=SimpleNamespace(
                file_index={"query.py": ()},
                resolve_symbol=lambda symbol: ("node",),
            ),
            repository_index=index,
        ),
        GeneratorConversation.create("state-consumer-case"), executor,
    )

    assert turn == 4
    assert revision.status == "PROPOSED"
    assert len(revision.edits) == 1
    assert revision.edits[0].start_line == 8
    assert "not defer" in revision.edits[0].replacement
