from __future__ import annotations

from dataclasses import dataclass, replace

from reachpatch.models.base import SerializableRecord, stable_id
from reachpatch.models.enums import Authority, RequirementAuthorityClass
from reachpatch.requirement_graph.models import RequirementGraph


@dataclass(frozen=True, slots=True)
class AuthorityPartition(SerializableRecord):
    hard_leaf_ids: tuple[str, ...]
    derived_leaf_ids: tuple[str, ...]
    hypothesis_leaf_ids: tuple[str, ...]
    preservation_leaf_ids: tuple[str, ...]
    provisional_leaf_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorityChange(SerializableRecord):
    change_id: str
    leaf_id: str
    old_authority: Authority
    new_authority: Authority
    old_class: RequirementAuthorityClass
    new_class: RequirementAuthorityClass
    invalidated_path_obligation_ids: tuple[str, ...]
    invalidated_ledger_ids: tuple[str, ...]
    reason: str


def partition_leaves(graph: RequirementGraph) -> AuthorityPartition:
    groups = {item: [] for item in RequirementAuthorityClass}
    provisional = []
    for leaf in graph.leaves.values():
        groups[leaf.authority_class].append(leaf.leaf_id)
        if not leaf.authority.trusted:
            provisional.append(leaf.leaf_id)
    return AuthorityPartition(
        hard_leaf_ids=tuple(sorted(groups[RequirementAuthorityClass.HARD])),
        derived_leaf_ids=tuple(sorted(groups[RequirementAuthorityClass.DERIVED])),
        hypothesis_leaf_ids=tuple(sorted(groups[RequirementAuthorityClass.HYPOTHESIS])),
        preservation_leaf_ids=tuple(sorted(groups[RequirementAuthorityClass.PRESERVATION])),
        provisional_leaf_ids=tuple(sorted(provisional)),
    )


def apply_authority_change(
    graph: RequirementGraph,
    leaf_id: str,
    *,
    authority: Authority,
    authority_class: RequirementAuthorityClass,
    reason: str,
) -> AuthorityChange:
    if leaf_id not in graph.leaves:
        raise KeyError(leaf_id)
    old = graph.leaves[leaf_id]
    path_ids = tuple(sorted(
        item.path_obligation_id
        for item in graph.path_obligations.values() if item.leaf_id == leaf_id
    ))
    # Path-state identifiers are content hashes and do not expose leaf ownership.
    # Until the ledger carries an explicit owner index, an authority change must
    # conservatively invalidate every path-edge proof.
    ledger_ids = tuple(sorted(graph.edge_ledger))
    mandatory = authority.trusted and authority_class in {
        RequirementAuthorityClass.HARD,
        RequirementAuthorityClass.DERIVED,
        RequirementAuthorityClass.HYPOTHESIS,
        RequirementAuthorityClass.PRESERVATION,
    }
    graph.leaves[leaf_id] = replace(
        old,
        authority=authority,
        authority_class=authority_class,
        mandatory=mandatory,
        coverage_status="AUTHORITY_CHANGED_RECOMPILE_REQUIRED",
    )
    node = graph.nodes[leaf_id]
    graph.nodes[leaf_id] = replace(node, attributes=graph.leaves[leaf_id].to_dict())
    graph.finalize_authority_snapshot()
    return AuthorityChange(
        change_id=stable_id(
            "authority-change", leaf_id, old.authority, authority,
            old.authority_class, authority_class, reason,
        ),
        leaf_id=leaf_id,
        old_authority=old.authority,
        new_authority=authority,
        old_class=old.authority_class,
        new_class=authority_class,
        invalidated_path_obligation_ids=path_ids,
        invalidated_ledger_ids=ledger_ids,
        reason=reason,
    )
