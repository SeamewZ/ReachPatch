from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from reachpatch.execution.models import CheckComparison
from reachpatch.models.base import SerializableRecord, content_hash


@dataclass(frozen=True, slots=True)
class ObservationBundle(SerializableRecord):
    revision: int
    check_comparisons: tuple[CheckComparison, ...] = ()
    challenge_results: tuple[Any, ...] = ()
    mechanical_checks: tuple[Any, ...] = ()
    environment_frontier_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    bundle_hash: str = ""

    @classmethod
    def create(
        cls,
        *,
        revision: int,
        check_comparisons: Iterable[CheckComparison] = (),
        challenge_results: Iterable[Any] = (),
        mechanical_checks: Iterable[Any] = (),
        environment_frontier_ids: Iterable[str] = (),
    ) -> "ObservationBundle":
        comparisons = tuple(check_comparisons)
        challenges = tuple(challenge_results)
        mechanical = tuple(mechanical_checks)
        frontiers = tuple(dict.fromkeys(map(str, environment_frontier_ids)))
        evidence = tuple(dict.fromkeys((
            *(item.comparison_id for item in comparisons),
            *(
                str(getattr(item, "challenge_id", getattr(item, "outcome_id", "")))
                for item in challenges
            ),
            *(str(getattr(item, "check_id", "")) for item in mechanical),
        )))
        digest = content_hash({
            "revision": revision,
            "comparisons": [item.to_dict() for item in comparisons],
            "challenges": [
                item.to_dict() if hasattr(item, "to_dict") else item for item in challenges
            ],
            "mechanical": [
                item.to_dict() if hasattr(item, "to_dict") else item for item in mechanical
            ],
            "frontiers": frontiers,
        })
        return cls(
            revision=revision,
            check_comparisons=comparisons,
            challenge_results=challenges,
            mechanical_checks=mechanical,
            environment_frontier_ids=frontiers,
            evidence_ids=evidence,
            bundle_hash=digest,
        )

    @property
    def target_comparison_count(self) -> int:
        return sum(
            getattr(item.classification, "value", item.classification).startswith("TARGET_")
            for item in self.check_comparisons
        )

    @property
    def preservation_comparison_count(self) -> int:
        return sum(
            getattr(item.classification, "value", item.classification)
            in {"PASS_PRESERVED", "PRESERVATION_REGRESSION"}
            for item in self.check_comparisons
        )

    @property
    def dicc_executed_challenge_count(self) -> int:
        return len(self.challenge_results)

    @property
    def real_execution_count(self) -> int:
        return (
            self.target_comparison_count
            + self.preservation_comparison_count
            + self.dicc_executed_challenge_count
        )
