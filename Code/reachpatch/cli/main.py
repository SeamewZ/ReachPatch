from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from reachpatch.reach_avoid.controller import ReachAvoidConfig, ReachAvoidController
from reachpatch.reach_avoid.execution_checkpoint import (
    ExecutionCheckpointStore, record_from_dict,
)
from reachpatch.models.execution import TransitionCertificate
from reachpatch.repair import DeepSeekAgent, DeepSeekConfig, DeepSeekHTTPTransport
from reachpatch.reach_avoid.repair_player import RepairPlayer
from reachpatch.reporting import summarize_external_outcomes
from reachpatch.run import load_instance


def _json(value) -> None:
    print(json.dumps(value.to_dict() if hasattr(value, "to_dict") else value, indent=2, sort_keys=True))


def _controller(args) -> ReachAvoidController:
    key_path = args.deepseek_key_path or os.environ.get("REACHPATCH_DEEPSEEK_KEY_PATH")
    if not key_path:
        raise RuntimeError("DeepSeek API key is required for the production repair path")
    key = Path(key_path).read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("DeepSeek API key is empty")
    transport = DeepSeekHTTPTransport(
        key,
        model=args.deepseek_model,
        base_url=args.deepseek_base_url,
    )
    return ReachAvoidController(
        RepairPlayer(DeepSeekAgent(transport, DeepSeekConfig.from_environment())),
        ReachAvoidConfig(max_real_patch_revisions=args.max_revisions),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reachpatch")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run the single working-patch Reach-Avoid loop")
    run.add_argument("--instance", required=True)
    run.add_argument("--run-root")
    run.add_argument("--max-revisions", type=int, default=8)
    run.add_argument("--deepseek-key-path")
    run.add_argument("--deepseek-model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    run.add_argument("--deepseek-base-url", default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    run.set_defaults(handler=_run)
    repair = subparsers.add_parser("repair", help="run the single working-patch Reach-Avoid loop")
    for action in run._actions[1:]:
        if action.dest == "help":
            continue
        repair._add_action(action)
    repair.set_defaults(handler=_run)
    status = subparsers.add_parser("status")
    status.add_argument("--run-root", required=True)
    status.set_defaults(handler=_status)
    export = subparsers.add_parser("export")
    export.add_argument("--run-root", required=True)
    export.set_defaults(handler=_export)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--run-root", required=True)
    verify.set_defaults(handler=_verify)
    assess = subparsers.add_parser(
        "assess-outcomes",
        help="summarize external p0/final resolved outcomes without affecting repair",
    )
    assess.add_argument("--outcomes", required=True)
    assess.set_defaults(handler=_assess_outcomes)
    return parser


def _run(args) -> int:
    instance = load_instance(args.instance)
    result = _controller(args).run(instance, run_root=args.run_root)
    _json(result)
    return 0


def _status(args) -> int:
    path = Path(args.run_root).resolve() / "terminal.json"
    _json(json.loads(path.read_text(encoding="utf-8")))
    return 0


def _export(args) -> int:
    path = Path(args.run_root).resolve() / "final.patch"
    print(str(path))
    return 0


def _verify(args) -> int:
    root = Path(args.run_root).resolve()
    store = ExecutionCheckpointStore(root)
    state = store.read_state()
    repository = Path(state.base_repository).resolve()
    checkpoint_count = 0
    for path in sorted(store.root.iterdir()):
        if not path.is_dir():
            continue
        checkpoint = store.load(path.name)
        store.validate(checkpoint, repository, clean_snapshot=state.clean_snapshot)
        checkpoint_count += 1
    transition_count = 0
    checkpoints = {
        path.name: store.load(path.name)
        for path in sorted(store.root.iterdir())
        if path.is_dir() and (path / "checkpoint.json").is_file()
    }
    for path in sorted((root / "transitions").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        # Transition files are canonical certificates in execution v2.  The
        # observation sidecars are intentionally separate and never used for
        # deciding Reach; hashes in the certificate provide traceability.
        certificate = record_from_dict(TransitionCertificate, raw)
        parent = checkpoints.get(certificate.parent_checkpoint_id)
        trial = checkpoints.get(certificate.trial_checkpoint_id)
        result_checkpoint = checkpoints.get(certificate.result_checkpoint_id)
        if parent is None or trial is None or result_checkpoint is None:
            raise RuntimeError(f"transition {path.name} references a missing checkpoint")
        if parent.patch_hash != certificate.parent_patch_hash:
            raise RuntimeError(f"transition {path.name} parent patch hash mismatch")
        if trial.patch_hash != certificate.trial_patch_hash:
            raise RuntimeError(f"transition {path.name} trial patch hash mismatch")
        if result_checkpoint.patch_hash != certificate.result_patch_hash:
            raise RuntimeError(f"transition {path.name} result patch hash mismatch")
        if not certificate.exact_failure_command and certificate.active_failure_kind != "MECHANICAL":
            raise RuntimeError(f"transition {path.name} has no exact failure command")
        sidecar = root / "transition_observations" / path.name
        if not sidecar.is_file():
            raise RuntimeError(f"transition {path.name} is missing observation sidecar")
        observations = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(observations, dict) or not all(
            isinstance(observations.get(phase), list)
            for phase in ("clean", "parent", "trial")
        ):
            raise RuntimeError(f"transition {path.name} has malformed observation sidecar")
        if certificate.observation_hashes and len(certificate.observation_hashes) < len(certificate.check_ids):
            raise RuntimeError(f"transition {path.name} observation trace is incomplete")
        transition_count += 1
    terminal = json.loads((root / "terminal.json").read_text(encoding="utf-8"))
    result = terminal["result"]
    output = Path(result["output_path"])
    selected = checkpoints.get(str(result["checkpoint_id"]))
    valid = (
        selected is not None
        and output.is_file()
        and output.read_text(encoding="utf-8") == result["unified_diff"]
        and selected.cumulative_diff == result["unified_diff"]
        and selected.patch_hash == result["patch_hash"]
    )
    _json({
        "valid": valid,
        "checkpoint_id": result["checkpoint_id"],
        "checkpoint_count": checkpoint_count,
        "transition_count": transition_count,
    })
    return 0 if valid else 1


def _assess_outcomes(args) -> int:
    _json(summarize_external_outcomes(args.outcomes))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileNotFoundError, RuntimeError, ValueError, KeyError) as exc:
        _json({"status": "ERROR", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
