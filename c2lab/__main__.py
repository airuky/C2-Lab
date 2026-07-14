"""Command-line entry points for Teamserver and foreground lab nodes."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from collections.abc import Sequence

from . import __version__
from .core import LabRuntime, LabState
from .node import DEFAULT_CONTROLLER, run_node, validate_controller_url
from .protocol import (
    MAX_POLL_INTERVAL_MS,
    MIN_POLL_INTERVAL_MS,
    NODE_PROFILES,
    ProtocolError,
    clean_text,
    validate_poll_interval,
)
from .server import create_server


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 0 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be from 0 to 65535")
    return port


def _poll_interval(value: str) -> int:
    try:
        return validate_poll_interval(int(value))
    except (ValueError, ProtocolError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="c2lab",
        description="Run a localhost-only C2 and bounded purple-team playbook lab.",
    )
    subparsers = parser.add_subparsers(dest="command")

    teamserver = subparsers.add_parser("teamserver", help="start the authoritative Teamserver")
    teamserver.add_argument("--port", type=_port, default=8765, help="loopback port (default: 8765)")

    node = subparsers.add_parser("node", help="start a foreground localhost lab node")
    node.add_argument("--name", required=True, help="operator-visible node name")
    node.add_argument(
        "--controller",
        default=DEFAULT_CONTROLLER,
        help=f"loopback Teamserver URL (default: {DEFAULT_CONTROLLER})",
    )
    node.add_argument(
        "--profile",
        choices=tuple(NODE_PROFILES),
        default="training",
        help="fixed capability profile; purple_lab enables ephemeral playbooks (default: training)",
    )
    node.add_argument(
        "--poll-ms",
        type=_poll_interval,
        default=1_000,
        metavar=f"{MIN_POLL_INTERVAL_MS}..{MAX_POLL_INTERVAL_MS}",
        help="deterministic check-in interval in milliseconds (default: 1000)",
    )
    node.add_argument(
        "--enroll-token",
        help="enrollment token; omit to enter it without shell history",
    )
    return parser


def run_teamserver(port: int) -> int:
    state = LabState()
    runtime = LabRuntime(state)
    operator_token = secrets.token_urlsafe(24)
    enrollment_token = secrets.token_urlsafe(24)
    server = create_server(state, operator_token, enrollment_token, port)
    actual_port = server.server_address[1]

    print("C2 Lab Framework — LOCALHOST LAB MODE")
    print("Architecture: Operator -> Teamserver -> foreground Node processes")
    print(f"Operator URL: http://127.0.0.1:{actual_port}/#token={operator_token}")
    print(f"Node enrollment token: {enrollment_token}")
    print(f"Start a node: python3 -m c2lab node --name node-a --controller http://127.0.0.1:{actual_port}")
    print("No shell, host-file access, file transfer, persistence, evasion, or remote transport is implemented.")
    print("purple_lab playbooks, when selected, are confined to each Node's synthetic temporary workspace.")
    print("Press Ctrl-C to stop. All Teamserver state and node sessions are memory-only.")

    runtime.start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping Teamserver…")
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(argv) if argv is not None else sys.argv[1:]
    if not raw_arguments:
        raw_arguments = ["teamserver"]
    parser = build_parser()
    args = parser.parse_args(raw_arguments)

    if args.command == "teamserver":
        return run_teamserver(args.port)
    if args.command == "node":
        try:
            name = clean_text(args.name, "name", maximum=48)
            controller = validate_controller_url(args.controller)
        except (ProtocolError, ValueError) as error:
            parser.error(str(error))
        enrollment_token = args.enroll_token or getpass.getpass("Node enrollment token: ")
        if len(enrollment_token) < 16:
            parser.error("enrollment token must contain at least 16 characters")
        return run_node(
            controller_url=controller,
            enrollment_token=enrollment_token,
            name=name,
            version=__version__,
            profile=args.profile,
            poll_interval_ms=args.poll_ms,
        )
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
