from __future__ import annotations

import ast
import re
from dataclasses import replace
from typing import Any, Iterable

from reachpatch.models.base import stable_id
from reachpatch.models.enums import Authority, OracleLifecycle, OutcomeStatus, RequirementAuthorityClass
from reachpatch.oracle.models import ObservationContract, Oracle, OracleEvaluation
from reachpatch.requirement_graph.models import RequirementLeaf


def observation_contract_from_leaf(leaf: RequirementLeaf) -> ObservationContract:
    payload = leaf.observation_contract
    channels = tuple(str(item) for item in payload.get("channels", ["return"]))
    return ObservationContract(
        contract_id=str(payload.get("contract_id") or stable_id("observation", leaf.leaf_id)),
        channels=channels,
        object_fields=tuple(leaf.state_contract.get("fields", [])),
        visible_state_keys=tuple(leaf.state_contract.get("fields", [])),
        capture_calls="calls" in channels,
        capture_protocol_selection="calls" in channels,
        multi_trace_relation=(
            str(leaf.required_trace_relation["kind"])
            if int(leaf.required_trace_relation.get("arity", 1)) > 1
            else None
        ),
    )


def _literal_after_return(formula: str) -> tuple[bool, Any]:
    patterns = [
        r"\breturns?\s+(.+?)(?:\s+for\b|\s+when\b|[.;]|$)",
        r"\bexpected(?:\s+result)?\s*(?:is|to be|==|:)\s*(.+?)(?:[.;]|$)",
        r"==\s*(.+?)(?:[.;]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, formula, re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1).strip().strip("`")
        try:
            return True, ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            if raw.lower() in {"none", "true", "false"}:
                return True, {"none": None, "true": True, "false": False}[raw.lower()]
    try:
        tree = ast.parse(formula, mode="exec")
    except SyntaxError:
        return False, None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare) and len(node.test.comparators) == 1:
            comparator = node.test.comparators[0]
            try:
                return True, ast.literal_eval(comparator)
            except (ValueError, SyntaxError):
                return False, None
    return False, None


def _authority_source(authority: Authority, leaf: RequirementLeaf) -> str:
    if leaf.authority_class == RequirementAuthorityClass.PRESERVATION:
        return "baseline_preservation_differential"
    return {
        Authority.A: "explicit_issue_or_public_assertion",
        Authority.B: "public_documentation_or_type_contract",
        Authority.C: "checked_relation_or_protocol_axiom",
        Authority.PROVISIONAL: "exploration_only",
    }[authority]


def _normalize_expected(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        import base64

        return {
            "type": "bytes",
            "base64": base64.b64encode(value).decode("ascii"),
            "length": len(value),
        }
    if isinstance(value, (list, tuple)):
        return {
            "type": type(value).__qualname__,
            "length": len(value),
            "items": [_normalize_expected(item) for item in value],
        }
    if isinstance(value, (set, frozenset)):
        items = [_normalize_expected(item) for item in value]
        return {
            "type": type(value).__qualname__,
            "length": len(value),
            "items": sorted(items, key=repr),
        }
    if isinstance(value, dict):
        return {
            "type": "dict",
            "length": len(value),
            "items": [
                [_normalize_expected(key), _normalize_expected(item)]
                for key, item in value.items()
            ],
        }
    return value


def resolve_oracle(leaf: RequirementLeaf) -> Oracle:
    relation_kind = str(leaf.required_trace_relation.get("kind", "trace_predicate"))
    relation: dict[str, Any] = {"kind": relation_kind, "formula": leaf.formula}
    executable = False
    kind = "EXPLORATION_ONLY"
    authority = leaf.authority
    if leaf.authority_class == RequirementAuthorityClass.PRESERVATION or leaf.preservation_contract.get("required"):
        relation = {
            "kind": "preservation",
            "channels": leaf.observation_contract.get("channels", ["return"]),
        }
        authority = authority if authority.trusted else Authority.C
        executable = True
        kind = "BASELINE_DIFFERENTIAL"
    elif relation_kind == "exception":
        expected_type = leaf.exception_contract.get("type")
        if expected_type:
            relation = {
                "kind": "exception",
                "expected_type": expected_type,
                "message_category": leaf.exception_contract.get("message_category"),
                "phase": leaf.exception_contract.get("phase", "stimulus"),
            }
            executable = authority.trusted
            kind = "EXPLICIT_EXCEPTION"
    elif relation_kind == "equality":
        found, expected = _literal_after_return(leaf.formula)
        if found:
            relation = {"kind": "equality", "channel": "return", "expected": expected}
            executable = authority.trusted
            kind = "EXPLICIT_RELATION"
        elif re.search(r"\bsame\s+(?:object\s+)?as\s+(?:the\s+)?input\b", leaf.formula, re.IGNORECASE):
            relation = {"kind": "input_identity", "channel": "return", "input_name": "x"}
            executable = authority.trusted
            kind = "EXPLICIT_RELATION"
    elif relation_kind == "temporal" and authority.trusted:
        relation = {"kind": "temporal", "formula": leaf.formula}
        executable = bool(re.search(r"\bbefore\b.*\bafter\b|\bmust\s+(?:precede|follow)\b", leaf.formula, re.IGNORECASE))
        kind = "RELATION_ORACLE" if executable else "EXPLORATION_ONLY"
    elif relation_kind == "metamorphic" and authority.trusted:
        relation = {"kind": "idempotence", "channel": "return", "repeats": 2}
        executable = "idempotent" in leaf.formula.lower()
        kind = "METAMORPHIC"

    lifecycle = (
        OracleLifecycle.ACTIVE
        if executable and authority.trusted
        else OracleLifecycle.DOWNGRADED
    )
    unknown_reason = (
        "oracle lifecycle is not active"
        if lifecycle != OracleLifecycle.ACTIVE
        else "observation channel missing, incomparable, unstable, or precondition false"
    )
    return Oracle(
        oracle_id=stable_id("oracle", leaf.leaf_id, relation, authority, lifecycle),
        input_domain={
            domain.variable: domain.to_dict() for domain in leaf.domains
        },
        observation_channels=tuple(leaf.observation_contract.get("channels", ["return"])),
        relation=relation,
        authority=authority,
        authority_source=_authority_source(authority, leaf),
        evidence_ids=leaf.supporting_evidence,
        evidence_cluster_id=stable_id("oracle-cluster", leaf.supporting_evidence),
        applicability_condition=leaf.precondition,
        counterexample_condition="active predicate evaluates false on a stable comparable run",
        unknown_condition=unknown_reason,
        stability_repeats=2,
        disagreement_repeat=1,
        lifecycle=lifecycle,
        executable=executable,
        kind=kind,
    )


def evaluate_oracle(
    oracle: Oracle,
    observation: dict[str, Any],
    *,
    baseline: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    related_observations: Iterable[dict[str, Any]] = (),
) -> OracleEvaluation:
    if not oracle.active_and_trusted:
        return OracleEvaluation(
            status=OutcomeStatus.UNKNOWN,
            reason="oracle_not_active",
            expected=oracle.relation,
            actual=observation,
            channel=None,
        )
    relation = oracle.relation
    kind = relation["kind"]
    if kind == "equality":
        channel = str(relation.get("channel", "return"))
        actual = observation.get(channel)
        expected = _normalize_expected(relation.get("expected"))
        status = OutcomeStatus.PASS if actual == expected else OutcomeStatus.FAIL
        return OracleEvaluation(status, "equality", expected, actual, channel)
    if kind == "exception":
        actual_exception = observation.get("exception")
        actual_type = actual_exception.get("type") if isinstance(actual_exception, dict) else None
        expected_type = relation.get("expected_type")
        type_pass = actual_type == expected_type
        actual_message = str(actual_exception.get("message", "")) if isinstance(actual_exception, dict) else ""
        category = relation.get("message_category")
        message_pass = (
            category is None
            or str(category).casefold() in actual_message.casefold()
        )
        expected_phase = relation.get("phase")
        actual_phase = actual_exception.get("stage") if isinstance(actual_exception, dict) else None
        phase_pass = expected_phase is None or expected_phase == actual_phase
        status = OutcomeStatus.PASS if type_pass and message_pass and phase_pass else OutcomeStatus.FAIL
        return OracleEvaluation(
            status,
            "exception_type_message_phase",
            {
                "type": expected_type,
                "message_category": category,
                "phase": expected_phase,
            },
            {
                "type": actual_type,
                "message": actual_message,
                "phase": actual_phase,
            },
            "exception",
        )
    if kind == "preservation":
        if baseline is None:
            return OracleEvaluation(OutcomeStatus.UNKNOWN, "missing_baseline", baseline, observation, None)
        channels = tuple(relation.get("channels", oracle.observation_channels))
        expected = {channel: baseline.get(channel) for channel in channels}
        actual = {channel: observation.get(channel) for channel in channels}
        status = OutcomeStatus.PASS if actual == expected else OutcomeStatus.FAIL
        return OracleEvaluation(status, "baseline_differential", expected, actual, ",".join(channels))
    if kind == "input_identity":
        input_name = str(relation.get("input_name", "x"))
        if inputs is None or input_name not in inputs:
            return OracleEvaluation(OutcomeStatus.UNKNOWN, "missing_input_identity", input_name, None, "return")
        expected_identity = observation.get("input_identities", {}).get(input_name)
        actual_identity = observation.get("return_identity")
        status = OutcomeStatus.PASS if expected_identity == actual_identity else OutcomeStatus.FAIL
        return OracleEvaluation(status, "input_identity", expected_identity, actual_identity, "return")
    if kind == "idempotence":
        related = list(related_observations)
        if not related:
            return OracleEvaluation(OutcomeStatus.UNKNOWN, "missing_related_trace", None, None, "return")
        actual = [observation.get("return")] + [item.get("return") for item in related]
        status = OutcomeStatus.PASS if all(item == actual[0] for item in actual[1:]) else OutcomeStatus.FAIL
        return OracleEvaluation(status, "idempotence", actual[0], actual, "return")
    return OracleEvaluation(OutcomeStatus.UNKNOWN, "unsupported_oracle_relation", relation, observation, None)


def contest_oracle(oracle: Oracle, evidence_ids: Iterable[str]) -> Oracle:
    return replace(
        oracle,
        lifecycle=OracleLifecycle.CONTESTED,
        evidence_ids=tuple(sorted(set(oracle.evidence_ids) | set(evidence_ids))),
        executable=False,
        unknown_condition="contradictory evidence requires adjudication",
    )


def adjudicate_oracle(
    oracle: Oracle,
    *,
    decision: str,
    narrowed_relation: dict[str, Any] | None = None,
) -> Oracle:
    if decision not in {"restore", "narrow", "downgrade", "revoke"}:
        raise ValueError(f"unsupported oracle adjudication decision {decision!r}")
    if decision == "restore":
        return replace(oracle, lifecycle=OracleLifecycle.ACTIVE, executable=True)
    if decision == "narrow":
        if not narrowed_relation:
            raise ValueError("narrow adjudication requires a relation")
        return replace(
            oracle,
            relation=dict(narrowed_relation),
            lifecycle=OracleLifecycle.ACTIVE,
            executable=True,
        )
    if decision == "downgrade":
        return replace(oracle, lifecycle=OracleLifecycle.DOWNGRADED, executable=False)
    return replace(oracle, lifecycle=OracleLifecycle.REVOKED, executable=False)
