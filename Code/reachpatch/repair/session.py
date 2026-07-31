from __future__ import annotations

from dataclasses import replace
from typing import Callable

from reachpatch.models.base import stable_id
from reachpatch.models.controller import (
    GeneratorSessionRecord,
    ReachAvoidState,
    RepairAction,
    RepairIntent,
    RepairPlan,
    StructuredEditIntent,
)
from reachpatch.models.enums import OutcomeStatus
from reachpatch.repair.contracts import validate_repair_action


ActionProvider = Callable[[ReachAvoidState, RepairIntent, GeneratorSessionRecord], RepairAction | None]


def _literal_expression(value) -> str:
    rendered = repr(value)
    compile(rendered, "<repair-literal>", "eval")
    return rendered


class PersistentGeneratorSession:
    """Keep repair context across commit and rollback on one patch lineage."""

    def __init__(
        self,
        episode_id: str,
        checkpoint_id: str,
        *,
        action_provider: ActionProvider | None = None,
    ) -> None:
        self.record = GeneratorSessionRecord(
            session_id=stable_id("generator-session", episode_id, checkpoint_id),
            episode_id=episode_id,
            current_checkpoint_id=checkpoint_id,
            cursor=0,
            delivered_counterexample_ids=(),
            submitted_transition_ids=(),
            internal_tool_turns=0,
            active=True,
        )
        self.action_provider = action_provider

    @classmethod
    def from_record(
        cls,
        record: GeneratorSessionRecord,
        *,
        action_provider: ActionProvider | None = None,
    ) -> "PersistentGeneratorSession":
        session = cls(record.episode_id, record.current_checkpoint_id, action_provider=action_provider)
        session.record = record
        return session

    def propose_action(
        self,
        state: ReachAvoidState,
        intent: RepairIntent,
    ) -> RepairAction | None:
        if not self.record.active:
            raise RuntimeError("generator session is sealed")
        if intent.source_checkpoint_id != state.checkpoint.checkpoint_id:
            raise ValueError("repair intent references a stale checkpoint")
        self.record = replace(
            self.record,
            cursor=self.record.cursor + 1,
            internal_tool_turns=self.record.internal_tool_turns + 1,
        )
        action = self._deterministic_action(state, intent)
        if action is None and self.action_provider is not None:
            action = self.action_provider(state, intent, self.record)
        if action is not None:
            validation = validate_repair_action(
                action,
                intent,
                checkpoint_id=state.checkpoint.checkpoint_id,
            )
            if not validation.valid:
                return None
        return action

    def _deterministic_action(
        self,
        state: ReachAvoidState,
        intent: RepairIntent,
    ) -> RepairAction | None:
        units = [
            unit for unit in state.active_binding_graph.units.values()
            if unit.path_obligation_id in intent.losing_path_obligation_ids
        ]
        oracles = [
            state.active_binding_graph.oracles[unit.oracle_id]
            for unit in units if unit.oracle_id in state.active_binding_graph.oracles
        ]
        relations = [oracle.relation for oracle in oracles if oracle.active_and_trusted]
        if not relations:
            return None
        relation_keys = {
            (str(item.get("kind")), repr(item.get("expected")), str(item.get("expected_type")))
            for item in relations
        }
        if len(relation_keys) != 1:
            return None
        relation = relations[0]
        kind = str(relation.get("kind"))
        if kind == "equality":
            replacement_statement = f"return {_literal_expression(relation.get('expected'))}"
            operator = "replace_return"
        elif kind == "input_identity":
            replacement_statement = f"return {relation.get('input_name', 'x')}"
            operator = "replace_return"
        elif kind == "exception" and relation.get("expected_type"):
            exception_name = str(relation["expected_type"])
            if not exception_name.replace(".", "").isidentifier():
                return None
            replacement_statement = f"raise {exception_name}()"
            operator = "replace_node"
        else:
            return None
        unit_paths = [set(unit.interaction_path_ids) for unit in units]
        common_path = set.intersection(*unit_paths) if unit_paths else set()
        candidate_ids = [
            node_id for node_id in common_path
            if node_id in state.program_graph.nodes
            and state.program_graph.nodes[node_id].kind == "return"
        ]
        if not candidate_ids:
            candidate_ids = sorted({
                node_id
                for unit in units
                for node_id in unit.interaction_path_ids
                if node_id in state.program_graph.nodes
                and state.program_graph.nodes[node_id].kind == "return"
            })
        if not candidate_ids:
            return None
        edit_intents: list[StructuredEditIntent] = []
        for node_id in sorted(candidate_ids):
            node = state.program_graph.nodes[node_id]
            attributes = node.attributes
            source = state.program_graph.source_segment(node_id)
            if not source.startswith("return "):
                continue
            edit_intents.append(StructuredEditIntent(
                edit_id=stable_id("edit", intent.intent_id, node_id, replacement_statement),
                operator=operator,
                relative_path=str(attributes["file"]),
                target_node_id=node_id,
                expected_span=(int(attributes["line"]), int(attributes["end_line"])),
                expected_source=source,
                replacement=replacement_statement,
                payload={
                    "ast_kind": attributes.get("ast_kind", "Return"),
                    "column": int(attributes.get("column", 0)),
                    "end_column": int(attributes.get("end_column", 0)),
                    "oracle_relation": relation,
                },
                reads=tuple(sorted({str(item) for item in relation.values()})),
                writes=(node_id,),
                control_flow_effects=("return_source",),
                exception_effects=("exception_to_return",) if kind != "exception" else ("return_to_exception",),
                object_shape_effects=("return_representation",),
            ))
        if not edit_intents:
            return None
        causal_cut = tuple(sorted(set(intent.repair_cut_ids) | {item.target_node_id for item in edit_intents}))
        coverage = {}
        current_passes = {
            item.path_obligation_id
            for item in state.outcomes.values()
            if item.status == OutcomeStatus.PASS
        }
        for path_id in intent.complete_component_path_ids:
            coverage[path_id] = {
                "account": "PRESERVE" if path_id in current_passes else "REPAIR",
                "changed_node_ids": [item.target_node_id for item in edit_intents],
                "causal_cut_ids": list(causal_cut),
                "frontier_id": None,
            }
        plan = RepairPlan(
            plan_id=stable_id("repair-plan", self.record.session_id, intent.intent_id, coverage),
            session_id=self.record.session_id,
            intent_id=intent.intent_id,
            checkpoint_id=intent.source_checkpoint_id,
            losing_core_id=intent.losing_core_id,
            component_id=intent.component_id,
            root_mechanism=intent.root_mechanism_class,
            repair_cut_ids=causal_cut,
            ordered_edit_intents=tuple(edit_intents),
            coverage_by_path=coverage,
            protected_pass_pairs=intent.protected_pass_pairs,
            preservation_ids=intent.preservation_ids,
            expected_graph_invalidations=tuple(item.target_node_id for item in edit_intents),
            forbidden_fingerprints=intent.forbidden_fingerprints,
            atomic_compound=len(edit_intents) > 1,
        )
        return RepairAction(
            action_id=stable_id("repair-action", plan.plan_id, [item.edit_id for item in edit_intents]),
            intent_id=intent.intent_id,
            operator=operator,
            causal_cut_ids=causal_cut,
            edit_intents=tuple(edit_intents),
            read_set=tuple(sorted({item for edit in edit_intents for item in edit.reads})),
            write_set=tuple(sorted({item.relative_path for item in edit_intents})),
            expected_impact_node_ids=tuple(sorted({
                node_id for unit in units for node_id in unit.impact_cone_node_ids
            })),
            plan=plan,
        )

    def record_submission(self, transition_id: str) -> None:
        self.record = replace(
            self.record,
            submitted_transition_ids=self.record.submitted_transition_ids + (transition_id,),
        )

    def resume(self, checkpoint_id: str, counterexample_ids: tuple[str, ...]) -> None:
        delivered = tuple(sorted(
            set(self.record.delivered_counterexample_ids) | set(counterexample_ids)
        ))
        self.record = replace(
            self.record,
            current_checkpoint_id=checkpoint_id,
            delivered_counterexample_ids=delivered,
            cursor=self.record.cursor + 1,
        )

    def seal(self) -> None:
        self.record = replace(self.record, active=False)
