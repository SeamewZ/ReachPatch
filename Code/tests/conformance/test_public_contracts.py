from __future__ import annotations


def test_public_compatibility_contracts_are_importable():
    from reachpatch.artifacts import verify_artifacts
    from reachpatch.challenge_graph.counterexamples import counterexample_from_challenge
    from reachpatch.challenge_graph.materialize import admit_scenario
    from reachpatch.models.counterexample import CounterexamplePacket
    from reachpatch.reach_avoid.checkpoint import atomic_commit_checkpoint, rollback

    assert all(callable(item) for item in (
        verify_artifacts,
        counterexample_from_challenge,
        admit_scenario,
        atomic_commit_checkpoint,
        rollback,
    ))
    assert CounterexamplePacket.__name__ == "CounterexamplePacket"
