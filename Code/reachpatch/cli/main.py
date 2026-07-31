from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from reachpatch.artifacts import ArtifactStore, recover_run_storage, verify_run
from reachpatch.reach_avoid.controller import (
    AnalysisBlocked,
    ReachPatchConfig,
    ReachPatchController,
)
from reachpatch.reporting import build_run_report, export_patch
from reachpatch.repair.deepseek_agent import (
    DeepSeekHTTPTransport, PersistentDeepSeekAgent,
)
from reachpatch.run import config_from_manifest, load_instance, load_run_manifest


def _json(value: Any) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    print(json.dumps(value, sort_keys=True, indent=2))


def _config(args: argparse.Namespace) -> ReachPatchConfig:
    return ReachPatchConfig(
        selection_mode=args.selection_mode,
        max_submitted_revisions=args.max_revisions,
    )


def _generator(args: argparse.Namespace, config: ReachPatchConfig):
    raw_path = getattr(args, "deepseek_key_path", None) or os.environ.get(
        "REACHPATCH_DEEPSEEK_KEY_PATH"
    )
    if not raw_path:
        return None
    key_path = Path(raw_path).expanduser().resolve()
    api_key = key_path.read_text(encoding="utf-8").strip()
    if not api_key:
        raise ValueError("DeepSeek API key is empty")
    transport = DeepSeekHTTPTransport(
        api_key,
        model=getattr(args, "deepseek_model", "deepseek-chat"),
        base_url=getattr(args, "deepseek_base_url", None)
        or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        max_concurrency=1,
    )
    return PersistentDeepSeekAgent(
        transport,
        max_tool_turns=config.max_internal_tool_turns_per_revision,
    )


def _run_controller(args: argparse.Namespace, *, analyze_only: bool) -> int:
    instance = load_instance(args.instance)
    config = _config(args)
    controller = ReachPatchController(
        config=config, generator_agent=_generator(args, config)
    )
    if analyze_only:
        state = controller.analyze(instance, run_root=args.run_root)
        _json({
            "instance_id": state.instance_id,
            "run_root": state.run_root,
            "checkpoint_id": state.checkpoint.checkpoint_id,
            "target_deficit": state.target_deficit(),
            "outcome_count": len(state.outcomes),
            "phase": state.phase.value,
        })
    else:
        state, certificate = controller.run(instance, run_root=args.run_root)
        _json({
            "run_root": state.run_root,
            "terminal_certificate": certificate.to_dict(),
        })
    return 0


def _build_stage(args: argparse.Namespace, stage: str) -> int:
    """Materialize the complete graph pipeline and expose one named stage."""

    instance = load_instance(args.instance)
    config = _config(args)
    controller = ReachPatchController(
        config=config, generator_agent=_generator(args, config)
    )
    state = controller.analyze(instance, run_root=args.run_root)
    graph_map = {
        "requirements": state.requirement_graph,
        "program": state.program_graph,
        "binding": state.active_binding_graph,
        "challenges": state.challenge_graph,
    }
    graph = graph_map[stage]
    _json({
        "stage": stage,
        "run_root": state.run_root,
        "checkpoint_id": state.checkpoint.checkpoint_id,
        "graph_hash": (
            graph.graph_hash() if hasattr(graph, "graph_hash")
            else graph.to_dict().get("graph_hash")
        ),
        "artifact_ids": state.artifact_ids,
    })
    return 0


def _resume(args: argparse.Namespace) -> int:
    manifest = load_run_manifest(args.run_root)
    config = config_from_manifest(manifest)
    controller = ReachPatchController(
        config=config, generator_agent=_generator(args, config)
    )
    state, certificate = controller.resume(args.run_root)
    _json({"run_root": state.run_root, "terminal_certificate": certificate.to_dict()})
    return 0


def _status(args: argparse.Namespace) -> int:
    root = Path(args.run_root).resolve()
    manifest = load_run_manifest(root)
    instance_id = str(manifest["instance"]["instance_id"])
    store = ArtifactStore(root / "artifacts")
    state = store.latest(instance_id, "reach_avoid_state")
    terminal = store.latest(instance_id, "terminal_certificate")
    _json({
        "instance_id": instance_id,
        "run_root": str(root),
        "status": terminal.payload["status"] if terminal else "ACTIVE",
        "state": state.payload if state else None,
    })
    return 0


def _verify(args: argparse.Namespace) -> int:
    result = verify_run(args.run_root)
    _json(result)
    return 0 if result.valid else 1


def _recover(args: argparse.Namespace) -> int:
    result = recover_run_storage(args.run_root)
    _json(result)
    return 0 if result["verification"]["valid"] else 1


def _export(args: argparse.Namespace) -> int:
    destination = export_patch(args.run_root, args.output)
    _json({"patch": str(destination)})
    return 0


def _report(args: argparse.Namespace) -> int:
    _json(build_run_report(args.run_root))
    return 0


def _artifacts(args: argparse.Namespace) -> int:
    root = Path(args.run_root).resolve()
    manifest = load_run_manifest(root)
    instance_id = str(manifest["instance"]["instance_id"])
    records = ArtifactStore(root / "artifacts").list(
        artifact_type=args.type,
        instance_id=instance_id,
    )
    _json([{
        "artifact_id": item.artifact_id,
        "artifact_type": item.artifact_type,
        "content_hash": item.content_hash,
        "created_at": item.created_at,
        "status": item.status,
    } for item in records])
    return 0


def _add_generator_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--deepseek-key-path",
        default=os.environ.get("REACHPATCH_DEEPSEEK_KEY_PATH"),
        help="optional API-key file; omit for offline graph analysis",
    )
    command.add_argument(
        "--deepseek-model",
        default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    )
    command.add_argument(
        "--deepseek-base-url",
        default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reachpatch",
        description="Graph-grounded single-incumbent reach-avoid repair",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text, analyze_only in (
        ("analyze", "build the patch-first semantic/index/localization state", True),
        ("run", "run repair transitions and seal one patch", False),
        ("repair", "run repair transitions and seal one patch", False),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--instance", required=True, help="public instance JSON")
        command.add_argument("--run-root", help="new run directory inside the implementation root")
        command.add_argument(
            "--selection-mode",
            choices=("hypothesis_set", "certified", "benchmark"),
            default="hypothesis_set",
        )
        command.add_argument("--max-revisions", type=int, default=10)
        _add_generator_arguments(command)
        command.set_defaults(handler=lambda args, flag=analyze_only: _run_controller(
            args, analyze_only=flag
        ))
    for name, stage in (
        ("build-requirements", "requirements"),
        ("build-program-graph", "program"),
        ("bind", "binding"),
        ("generate-challenges", "challenges"),
    ):
        command = subparsers.add_parser(name, help=f"materialize the {stage} stage")
        command.add_argument("--instance", required=True, help="public instance JSON")
        command.add_argument("--run-root", help="new run directory inside the implementation root")
        command.add_argument(
            "--selection-mode",
            choices=("hypothesis_set", "certified", "benchmark"),
            default="hypothesis_set",
        )
        command.add_argument("--max-revisions", type=int, default=10)
        _add_generator_arguments(command)
        command.set_defaults(handler=lambda args, selected=stage: _build_stage(args, selected))
    resume = subparsers.add_parser("resume", help="rebuild and continue an interrupted run")
    resume.add_argument("--run-root", required=True)
    _add_generator_arguments(resume)
    resume.set_defaults(handler=_resume)
    for name, handler, help_text in (
        ("status", _status, "show the latest persisted state"),
        ("inspect", _status, "inspect the latest state and frontiers"),
        ("verify", _verify, "verify artifacts, checkpoint lineage, and patch replay"),
        ("verify-artifacts", _verify, "verify artifacts, checkpoint lineage, and patch replay"),
        ("recover", _recover, "rebuild artifact index and discard an interrupted trial"),
        ("report", _report, "write JSON and Markdown run reports"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--run-root", required=True)
        command.set_defaults(handler=handler)
    export = subparsers.add_parser("export", help="export the sole incumbent patch")
    export.add_argument("--run-root", required=True)
    export.add_argument("--output", help="destination inside the run root")
    export.set_defaults(handler=_export)
    export_alias = subparsers.add_parser("export-patch", help="export the sole incumbent patch")
    export_alias.add_argument("--run-root", required=True)
    export_alias.add_argument("--output")
    export_alias.set_defaults(handler=_export)
    artifacts = subparsers.add_parser("artifacts", help="list artifact metadata")
    artifacts.add_argument("--run-root", required=True)
    artifacts.add_argument("--type", help="optional artifact type filter")
    artifacts.set_defaults(handler=_artifacts)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except AnalysisBlocked as exc:
        _json({"status": exc.status, "error": exc.detail})
        return 2
    except (FileNotFoundError, ValueError, KeyError, RuntimeError) as exc:
        _json({"status": "ERROR", "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
