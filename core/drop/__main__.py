"""Explicit local controller CLI. UNREVIEWED: not for real messages."""

from __future__ import annotations

import argparse
import json
import sys

from core.drop import WARNING, DropError, require_enabled
from core.drop.service import DropHome, LocalRelay


def main():
    parser = argparse.ArgumentParser(description=WARNING)
    parser.add_argument("--home")
    parser.add_argument("--relay", required=True)
    parser.add_argument("--deny-read", action="store_true")
    parser.add_argument("--now-ms", type=int, help="Laboratory clock control")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("relay-init", "identity", "poll", "sweep", "list"):
        sub.add_parser(command)
    sub.add_parser("invite").add_argument("pin", help="The invitee's fingerprint, confirmed with them directly")
    join = sub.add_parser("join")
    join.add_argument("link")
    join.add_argument("pin")
    for command in ("show", "execute", "delete"):
        sub.add_parser(command).add_argument("sid")
    send = sub.add_parser("send")
    send.add_argument("sid")
    send.add_argument("--note", required=True)
    send.add_argument("--file", required=True)
    task = sub.add_parser("task")
    task.add_argument("sid")
    task.add_argument("description")
    task.add_argument("--permissions", nargs="+", default=["read_files"])
    approve = sub.add_parser("approve")
    approve.add_argument("sid")
    approve.add_argument("task_id")
    args = parser.parse_args()
    print(WARNING, file=sys.stderr)
    try:
        require_enabled()
        if args.command == "relay-init":
            LocalRelay(args.relay, create=True)
            out = {"relay": "ready"}
        else:
            clock = None if args.now_ms is None else lambda: args.now_ms
            with DropHome(args.home, args.relay, clock=clock,
                          allowed=() if args.deny_read else ("read_files",)) as home:
                if args.command == "identity":
                    out = {"fingerprint": home.fingerprint}
                elif args.command == "invite":
                    out = {"link": home.invite(args.pin)}
                elif args.command == "join":
                    out = {"sid": home.join(args.link, args.pin)}
                elif args.command == "poll":
                    out = home.poll()
                elif args.command == "list":
                    out = home.list()
                elif args.command == "sweep":
                    out = home.sweep()
                elif args.command == "show":
                    out = home.show(args.sid)
                elif args.command == "send":
                    out = home.send_packet(args.sid, args.note, args.file)
                elif args.command == "task":
                    out = {"task_id": home.send_task(args.sid, args.description, permissions=args.permissions)}
                elif args.command == "approve":
                    home.approve(args.sid, args.task_id)
                    out = {"state": "approved"}
                elif args.command == "execute":
                    out = {"result": home.execute_approved(args.sid)}
                elif args.command == "delete":
                    home.delete(args.sid)
                    out = {"state": "deleted"}
                else:
                    raise DropError("UNSUPPORTED_COMMAND")
        print(json.dumps(out, sort_keys=True))
        return 0
    except DropError as exc:
        print(json.dumps({"refused": str(exc)}))
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        # Local failures cannot print a profile path, decrypted body or key.
        print(json.dumps({"refused": "LOCAL_INPUT_OR_STATE_FAILURE"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
