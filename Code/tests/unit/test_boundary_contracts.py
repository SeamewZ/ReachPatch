from __future__ import annotations

from types import SimpleNamespace

from reachpatch.adapters import NumPyAdapter, select_adapter
from reachpatch.challenge_graph.recipes import CandidateGenerator
from reachpatch.evidence.hypotheses import HypothesisAssignment
from reachpatch.evidence.models import SemanticDecision
from reachpatch.models.controller import (
    RepairAction,
    RepairIntent,
    RepairPlan,
    StructuredEditIntent,
)
from reachpatch.oracle.discriminator import HypothesisDiscriminator
from reachpatch.repair.contracts import validate_repair_action
from reachpatch.requirement_graph.models import (
    DomainPartition,
    DomainSpec,
    QuantifiedVariable,
)


def test_candidate_generator_emits_only_constraint_witnesses_and_honors_limit():
    domain = DomainSpec(
        domain_id="domain-x",
        variable="x",
        type_names=("int",),
        literal_values=(),
        open_world=True,
    )
    leaf = SimpleNamespace(
        quantified_variables=(QuantifiedVariable("x", domain.domain_id),),
        domains=(domain,),
    )
    partition = DomainPartition(
        partition_id="partition-positive",
        variable_names=("x",),
        constraints=("x >= 1",),
        candidate_bindings=(),
        source="unit-test",
        scope="REQUIREMENT",
        satisfiable=True,
        proof={},
    )

    candidates = CandidateGenerator().generate(leaf, partition, limit=2)

    assert candidates == ({"x": 1}, {"x": 2})
    assert CandidateGenerator().generate(
        leaf,
        SimpleNamespace(constraints=("open('secret')",)),
    ) == ()


def _assignment(identifier: str, choice: str) -> HypothesisAssignment:
    return HypothesisAssignment(
        assignment_id=identifier,
        choice_by_decision={"decision": choice},
        common_hard_node_ids=(),
        assignment_node_ids=(choice,),
        preservation_node_ids=(),
        contradiction_ids=(),
        coherent=True,
        authority_complete=True,
        selection_mode="certified",
        score=1.0,
    )


def test_discriminator_outputs_observations_without_correctness_authority():
    decision = SemanticDecision(
        decision_id="decision",
        subject="pkg.api.public",
        alternative_claim_ids=("claim-a", "claim-b"),
        unknown_claim_id="unknown",
        contradiction_ids=(),
    )
    discriminator = HypothesisDiscriminator()
    probe = discriminator.plan(
        (decision,),
        (_assignment("assignment-a", "claim-a"), _assignment("assignment-b", "claim-b")),
    )[0]
    result = discriminator.record(
        probe,
        ({"return": 1},),
        evidence_ids=("dynamic-observation",),
        selected_claim_id="claim-a",
    )

    assert probe.correctness_authority == "NONE"
    assert result.correctness_status == "DISCRIMINATOR_ONLY"
    assert result.raw_observations == ({"return": 1},)


def test_repair_contract_rejects_stale_cross_component_undeclared_action():
    intent = RepairIntent(
        intent_id="intent",
        source_checkpoint_id="checkpoint-current",
        losing_core_id="core",
        component_id="component-a",
        losing_path_obligation_ids=("path",),
        complete_component_path_ids=("path",),
        repair_cut_ids=("cut",),
        root_mechanism_class="return_relation",
        actual_failure_execution_ids=(),
        protected_pass_pairs=(),
        preservation_ids=(),
        forbidden_fingerprints=(),
        frontier_resolution_ids=(),
        selection_witness={},
    )
    edit = StructuredEditIntent(
        edit_id="edit",
        operator="replace_return",
        relative_path="pkg/api.py",
        target_node_id="node",
        expected_span=(1, 1),
        expected_source="return 1",
        replacement="return 2",
        payload={"ast_kind": "Return"},
        reads=(),
        writes=("node",),
        control_flow_effects=("return_source",),
        exception_effects=(),
        object_shape_effects=("return_representation",),
    )
    plan = RepairPlan(
        plan_id="plan",
        session_id="session",
        intent_id=intent.intent_id,
        checkpoint_id="checkpoint-stale",
        losing_core_id=intent.losing_core_id,
        component_id="component-b",
        root_mechanism=intent.root_mechanism_class,
        repair_cut_ids=(),
        ordered_edit_intents=(edit,),
        coverage_by_path={},
        protected_pass_pairs=(),
        preservation_ids=(),
        expected_graph_invalidations=(),
        forbidden_fingerprints=(),
        atomic_compound=False,
    )
    action = RepairAction(
        action_id="action",
        intent_id=intent.intent_id,
        operator="replace_return",
        causal_cut_ids=(),
        edit_intents=(edit,),
        read_set=(),
        write_set=("undeclared.py",),
        expected_impact_node_ids=(),
        plan=plan,
    )

    validation = validate_repair_action(
        action, intent, checkpoint_id="checkpoint-current"
    )

    assert not validation.valid
    assert set(validation.errors) == {
        "repair plan references a stale checkpoint",
        "repair plan crosses repair components",
        "declared write set contains a file without an edit",
        "action omits the selected causal repair cut",
    }


def test_project_adapter_is_observation_only(tmp_path):
    (tmp_path / "numpy").mkdir()

    adapter = select_adapter(tmp_path)
    observation = adapter.observe(tmp_path)

    assert isinstance(adapter, NumPyAdapter)
    assert observation.status == "OBSERVED_NOT_CORRECTNESS"
    assert observation.marker_paths == ("numpy",)
    assert set(observation.to_dict()) == {
        "adapter",
        "marker_paths",
        "mechanical_command_hints",
        "graph_hints",
        "status",
    }
