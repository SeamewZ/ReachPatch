from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from reachpatch.binding_graph.active import ActiveBindingGraph, ActiveBindingStatus
from reachpatch.models.base import SerializableRecord, content_hash


class RequirementCoverageStatus(StrEnum):
    UNBOUND = "UNBOUND"
    UNTESTABLE = "UNTESTABLE"
    FAILING = "FAILING"
    PASSING = "PASSING"
    PRESERVATION_RISK = "PRESERVATION_RISK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RequirementCoverageRow(SerializableRecord):
    requirement_id: str
    normalized_requirement: str
    authority: str
    witness_ids: tuple[str, ...]
    binding_unit_ids: tuple[str, ...]
    mechanism_ids: tuple[str, ...]
    executable_check_ids: tuple[str, ...]
    status: str
    evidence_ids: tuple[str, ...]
    unresolved_reason: str | None


@dataclass(frozen=True, slots=True)
class RequirementCoverageTable(SerializableRecord):
    revision: int
    rows: dict[str, RequirementCoverageRow]
    table_hash: str

    @classmethod
    def create(
        cls, revision: int, rows: dict[str, RequirementCoverageRow]
    ) -> "RequirementCoverageTable":
        digest = content_hash({
            "revision": revision,
            "rows": [rows[key].to_dict() for key in sorted(rows)],
        })
        return cls(revision=revision, rows=rows, table_hash=digest)

    def unresolved_rows(self) -> tuple[RequirementCoverageRow, ...]:
        return tuple(
            self.rows[key] for key in sorted(self.rows)
            if self.rows[key].status != RequirementCoverageStatus.PASSING.value
        )


def update_requirement_coverage(
    previous_table: RequirementCoverageTable | None,
    active_binding_graph: ActiveBindingGraph,
    observations: Any,
    counterexamples: Iterable[Any],
    attempted_mechanisms: Iterable[str] = (),
) -> RequirementCoverageTable:
    del observations
    packets = tuple(counterexamples)
    mechanisms = tuple(dict.fromkeys(map(str, attempted_mechanisms)))
    rows: dict[str, RequirementCoverageRow] = {}
    by_requirement: dict[str, list] = {}
    for unit in active_binding_graph.units.values():
        by_requirement.setdefault(unit.requirement_id, []).append(unit)
    for requirement_id, units in sorted(by_requirement.items()):
        statuses = {unit.status for unit in units}
        if ActiveBindingStatus.PRESERVATION_RISK.value in statuses:
            status = RequirementCoverageStatus.PRESERVATION_RISK.value
        elif statuses & {
            ActiveBindingStatus.FAILING.value,
            ActiveBindingStatus.COUNTEREXAMPLE_OPEN.value,
        }:
            status = RequirementCoverageStatus.FAILING.value
        elif statuses == {ActiveBindingStatus.PASSING.value}:
            status = RequirementCoverageStatus.PASSING.value
        elif ActiveBindingStatus.UNBOUND.value in statuses:
            status = RequirementCoverageStatus.UNBOUND.value
        elif statuses <= {
            ActiveBindingStatus.ORACLE_UNAVAILABLE.value,
            ActiveBindingStatus.ENVIRONMENT_BLOCKED.value,
        }:
            status = RequirementCoverageStatus.UNTESTABLE.value
        else:
            status = RequirementCoverageStatus.UNKNOWN.value
        previous = previous_table.rows.get(requirement_id) if previous_table else None
        check_ids = tuple(dict.fromkeys(
            check_id
            for unit in units
            for check_id in (
                *unit.target_check_ids,
                *unit.preservation_check_ids,
                *unit.challenge_check_ids,
            )
        ))
        evidence_ids = tuple(dict.fromkeys((
            *(previous.evidence_ids if previous else ()),
            *(item for unit in units for item in unit.evidence_ids),
            *(
                str(getattr(packet, "counterexample_id", ""))
                for packet in packets
                if getattr(packet, "binding_unit_id", None) in {
                    unit.binding_id for unit in units
                }
            ),
        )))
        rows[requirement_id] = RequirementCoverageRow(
            requirement_id=requirement_id,
            normalized_requirement=units[0].requirement_text,
            authority=units[0].requirement_authority,
            witness_ids=tuple(dict.fromkeys(
                item for unit in units for item in unit.evidence_ids
            )),
            binding_unit_ids=tuple(sorted(unit.binding_id for unit in units)),
            mechanism_ids=tuple(dict.fromkeys((
                *(previous.mechanism_ids if previous else ()), *mechanisms,
            ))),
            executable_check_ids=check_ids,
            status=status,
            evidence_ids=evidence_ids,
            unresolved_reason=next(
                (unit.unresolved_reason for unit in units if unit.unresolved_reason), None
            ),
        )
    return RequirementCoverageTable.create(active_binding_graph.revision, rows)
