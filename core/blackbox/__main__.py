"""``python -m core.blackbox`` -- operator CLI: status, verify, turns, rollback.

    python -m core.blackbox status
    python -m core.blackbox verify
    python -m core.blackbox turns --root /path/to/workspace
    python -m core.blackbox rollback <turn_id> --root /path/to/workspace --approve [--operator name]

Rollback crosses the same permission authority as every tool call: without ``--approve`` the
matrix answers ``pending_approval`` (delete-class effects prompt in Manual AND Auto) and nothing
is written; ``--approve`` mints a bounded, expiring, root-bound scope in the operator's name.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """GENERATED_ADAPTER: the command registry owns these actions; this module keeps
    the historical ``python -m core.blackbox`` spelling and output format."""
    import json as _json

    parser = argparse.ArgumentParser(prog="python -m core.blackbox")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("verify")
    turns = sub.add_parser("turns")
    turns.add_argument("--root", default="")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("turn_id")
    rollback.add_argument("--root", required=True)
    rollback.add_argument("--mode", default="auto")
    rollback.add_argument("--operator", default="cli-operator")
    rollback.add_argument("--session", default="")
    rollback.add_argument("--approve", action="store_true",
                          help="grant this rollback a bounded authority; without it the mode matrix answers pending_approval")
    args = parser.parse_args(argv)

    from core.command_registry.execute import ExecutionContext, execute_command
    from core.command_registry.legacy import envelope_payload, envelope_status

    def _run(command_id: str, input_data: dict, approval: dict | None = None) -> int:
        envelope = execute_command(
            command_id, input_data, context=ExecutionContext(projection="cli", approval_context=approval)
        )
        status = envelope_status(envelope)
        payload = envelope_payload(envelope)
        print(_json.dumps(payload, indent=2, sort_keys=True, default=str))
        return 0 if status == 200 else 2

    if args.command == "status":
        return _run("blackbox.status", {})
    if args.command == "verify":
        return _run("blackbox.verify", {})
    if args.command == "turns":
        return _run("blackbox.turns", {"limit": 20})
    if args.command == "rollback":
        from core.mode_permission_policy import PermissionAction, grant_internal_authority, set_active_mode

        session = args.session or f"operator:{args.operator}"
        set_active_mode(session, args.mode)
        approval = None
        if args.approve:
            token = grant_internal_authority(
                label=f"blackbox-cli-rollback:{args.operator}",
                actions=[PermissionAction.DELETE_FILES, PermissionAction.OVERWRITE_EXISTING_FILES, PermissionAction.MODIFY_FILES],
                intents=["workspace.rollback_last_change"],
                workspace_root=str(Path(args.root).expanduser().resolve()),
                duration_seconds=120,
            )
            approval = {"authority_token": token}
        return _run(
            "blackbox.rollback",
            {"turn_id": args.turn_id, "workspace_root": str(Path(args.root).expanduser()), "operator": args.operator, "session": session},
            approval=approval,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
