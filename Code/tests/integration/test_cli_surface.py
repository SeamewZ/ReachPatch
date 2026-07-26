from __future__ import annotations

from reachpatch.cli.main import build_parser


def test_required_cli_commands_are_connected_to_handlers():
    parser = build_parser()
    commands = (
        ["analyze", "--instance", "instance.json"],
        ["build-requirements", "--instance", "instance.json"],
        ["build-program-graph", "--instance", "instance.json"],
        ["bind", "--instance", "instance.json"],
        ["generate-challenges", "--instance", "instance.json"],
        ["repair", "--instance", "instance.json"],
        ["resume", "--run-root", "run"],
        ["inspect", "--run-root", "run"],
        ["verify-artifacts", "--run-root", "run"],
        ["export-patch", "--run-root", "run"],
        ["report", "--run-root", "run"],
    )

    parsed = [parser.parse_args(command) for command in commands]

    assert all(callable(item.handler) for item in parsed)
