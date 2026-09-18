"""Conservative decomposition of cited requirements, independent of patches."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Sequence

from reachpatch.models.base import stable_id
from reachpatch.models.execution import GoalContract


def decompose_required_facets(goals: Sequence[GoalContract]) -> tuple[GoalContract, ...]:
    result = list(goals)
    identities = {(g.parent_goal_id, g.facet_kind) for g in goals}
    for goal in goals:
        if not goal.hard or goal.parent_goal_id or not goal.evidence_spans:
            continue
        quote = " ".join(span.quote for span in goal.evidence_spans)
        facets = []
        if re.search(r"\b(?:should|must)\s+not\s+(?:fail|raise|throw)\b", quote, re.I):
            if goal.comparator not in {"EXIT_ZERO", "NOT_RAISES"}:
                facets.append(("NO_EXCEPTION", "EXIT_ZERO", {"exit_code": 0}, None))
        # Only an explicit singular container has an unambiguous outer type.
        empty = re.search(r"\b(?:should|must)\s+(?:instead\s+)?return\s+(?:an?\s+)?empty\s+(list|tuple)\b(?!\s*[/\w])", quote, re.I)
        if empty:
            facets.extend((("RETURN_TYPE", "INSTANCE_OF", empty.group(1).lower(), None),
                           ("RETURN_LENGTH", "LENGTH_EQUALS", 0, None)))
        elif goal.comparator in {"EXIT_ZERO", "NOT_RAISES", "RELATION_HOLDS"} and re.search(r"\b(?:should|must)\b[^.\n]*\breturn\s+(?:an?\s+)?empty\b", quote, re.I):
            facets.append(("RETURN_STRUCTURE", "RELATION_HOLDS",
                           {"required_property": "empty_return", "layout": "UNRESOLVED"},
                           "RETURN_LAYOUT_REQUIRES_PUBLIC_EVIDENCE"))
        elif goal.comparator in {"EXIT_ZERO", "NOT_RAISES"} and re.search(
                r"\b(?:should|must)\b[^.\n]*\b(?:return|shape|state)\b", quote, re.I):
            facets.append(("SEMANTIC_RESULT", "RELATION_HOLDS", {"evidence": quote},
                           "UNCOMPILED_RESULT_FACET"))
        for kind, comparator, expected, unresolved in facets:
            if (goal.goal_id, kind) in identities:
                continue
            identities.add((goal.goal_id, kind))
            result.append(replace(goal, goal_id=stable_id("goal-facet", goal.goal_id, kind),
                parent_goal_id=goal.goal_id, facet_kind=kind, comparator=comparator,
                expected=expected, unresolved_reason=unresolved,
                alignment_reason=f"Required {kind} facet of {goal.goal_id}; derived only from cited prose"))
    return tuple(result)


def match_grounded_probe_goal(goals, probe) -> GoalContract | None:
    """Bind to an explicit requirement family, never by coincidental value alone.

    A stronger contract may match a separately grounded facet of the requested
    goal. Unresolved facets cannot acquire authority from a model assertion.
    """
    requested = getattr(probe, "requirement_id", None)
    candidates = [g for g in goals if g.hard and g.authority in {"A", "B", "C"}
                  and not g.unresolved_reason and
                  (g.goal_id == requested or g.parent_goal_id == requested)]
    contract = probe.contract
    matches = [g for g in candidates if g.comparator.upper() == contract.normalized_comparator
               and g.expected == contract.expected]
    if len(matches) == 1:
        return matches[0]
    return None  # UNBOUND_OR_AMBIGUOUS_GROUNDED_FACET
