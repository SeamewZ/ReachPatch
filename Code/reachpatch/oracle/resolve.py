from __future__ import annotations

import ast
import re

from reachpatch.models.base import stable_id
from reachpatch.models.evidence import (
    ExecutableOracle, OracleResolution, PublicEvidence, TraceBundle,
    issue_witnesses,
)
from reachpatch.models.graphs import RequirementLeaf


def _explicit_issue_witness_expected(
    requirement: RequirementLeaf,
    public_evidence: PublicEvidence,
):
    if not requirement.witness_ids:
        return None
    operation = re.escape(requirement.operation.split(".")[-1])
    for record in public_evidence.records:
        if record.source != "issue" or record.evidence_id not in requirement.evidence_ids:
            continue
        match = re.search(
            rf"\b{operation}\s*\(.*?\)\s+(?:must\s+)?(?:return|returns|=>)\s+"
            r"(`[^`]+`|None|True|False|-?\d+(?:\.\d+)?|\[[^\n.]*\]|\{[^\n.]*\}|\([^\n.]*\))",
            record.content,
            re.IGNORECASE,
        )
        if match is None:
            continue
        raw = match.group(1).strip("`")
        try:
            return ast.literal_eval(raw), record.evidence_id
        except (ValueError, SyntaxError):
            continue
    return None


def resolve_oracle(
    requirement: RequirementLeaf,
    public_evidence: PublicEvidence,
    baseline_execution: TraceBundle | None,
    *,
    witness_id: str | None = None,
) -> OracleResolution:
    for check in public_evidence.checks:
        explicitly_bound = requirement.requirement_id in check.requirement_ids
        evidence_bound = bool(set(requirement.evidence_ids).intersection(check.source_evidence_ids))
        symbol_bound = any(
            symbol.lower() in requirement.operation.lower()
            for symbol in check.symbol_references
        )
        if not (explicitly_bound or evidence_bound or symbol_bound):
            continue
        relation = requirement.expected_observation.relation
        expected = check.expected.expected if check.expected is not None else {"exit_code": 0}
        oracle = ExecutableOracle(
            oracle_id=stable_id("oracle", check.check_id, relation, expected),
            authority=check.authority,
            relation=relation,
            expected=expected,
            executable=True,
            source_evidence_ids=check.source_evidence_ids,
        )
        exploration = not oracle.trusted or not oracle.executable
        return OracleResolution(
            oracle,
            None if not exploration else "The executable check has only provisional authority",
            exploration,
        )
    if witness_id is not None:
        witness = next((
            item
            for record in public_evidence.records
            if record.source == "issue"
            and record.evidence_id in requirement.evidence_ids
            for item in issue_witnesses(record)
            if item["witness_id"] == witness_id
        ), None)
        if witness is not None:
            oracle = ExecutableOracle(
                oracle_id=stable_id(
                    "oracle", requirement.requirement_id,
                    witness_id, witness["expected_relation"], witness["expected"],
                ),
                authority=str(witness["authority"]),
                relation=requirement.expected_observation.relation,
                expected=witness["expected"],
                executable=True,
                source_evidence_ids=(str(witness["evidence_id"]),),
            )
            return OracleResolution(
                oracle,
                None if oracle.trusted else "Issue witness expectation is provisional",
                not oracle.trusted,
            )
    witness = _explicit_issue_witness_expected(requirement, public_evidence)
    if witness is not None:
        expected, evidence_id = witness
        oracle = ExecutableOracle(
            oracle_id=stable_id(
                "oracle", requirement.requirement_id,
                "issue-witness", expected,
            ),
            authority="B",
            relation=requirement.expected_observation.relation,
            expected=expected,
            executable=True,
            source_evidence_ids=(evidence_id,),
        )
        return OracleResolution(oracle, None, False)
    if requirement.authority == "B" and requirement.evidence_ids:
        expected = (
            requirement.exception_contract.exception_type
            if requirement.exception_contract is not None
            else requirement.expected_observation.expected
        )
        oracle = ExecutableOracle(
            oracle_id=stable_id("oracle", requirement.requirement_id, "issue"),
            authority="B",
            relation=requirement.expected_observation.relation,
            expected=expected,
            executable=requirement.expected_observation.comparator in {
                "equals", "raises", "forbidden", "succeeds",
            },
            source_evidence_ids=requirement.evidence_ids,
        )
        if oracle.executable:
            return OracleResolution(oracle, None, False)
    if requirement.preservation and baseline_execution is not None and baseline_execution.stable_runs >= 2:
        oracle = ExecutableOracle(
            oracle_id=stable_id(
                "oracle", requirement.requirement_id,
                baseline_execution.observation,
            ),
            authority="C",
            relation="patched observation preserves stable baseline observation",
            expected=baseline_execution.observation,
            executable=True,
            source_evidence_ids=(baseline_execution.trace_bundle_id,),
        )
        return OracleResolution(oracle, None, False)
    oracle = ExecutableOracle(
        oracle_id=stable_id("oracle", requirement.requirement_id, "provisional"),
        authority="PROVISIONAL",
        relation=requirement.expected_observation.relation,
        expected=requirement.expected_observation.expected,
        executable=False,
        source_evidence_ids=requirement.evidence_ids,
    )
    return OracleResolution(
        oracle,
        "No public executable assertion or stable baseline relation grounds this oracle",
        True,
    )
