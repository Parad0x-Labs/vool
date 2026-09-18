"""`python -m core.repoops` — the operator's terminal view of the same state the console shows.

Read-only by construction. There is no push here and no authorize here: Authorize Push is an
owner-local gesture at the served door, and giving it a second home in a CLI would be a second
authority for the one decision this whole vertical exists to keep single.
"""

from __future__ import annotations

import argparse
import json
import sys


def _sessions() -> int:
    from core.web.api.repoops_api import repo_sessions_payload

    payload = repo_sessions_payload()
    if not payload["sessions"]:
        print("no repo sessions in the journal")
        return 0
    for row in payload["sessions"]:
        flags = []
        if row["dirty"]:
            flags.append("dirty")
        if row["awaiting_authorization"]:
            flags.append("awaiting-authorization")
        if row["remote_verified"]:
            flags.append("remote-verified")
        print(
            f"{row['repo_session_id']}  {row['stage']:<10}  {row['local_head'][:12] or '-':<12}  "
            f"{row['provider'] or '-':<8}  {row['objective'][:48]}"
            + (f"  [{', '.join(flags)}]" if flags else "")
        )
    return 0


def _receipt(session_id: str) -> int:
    from core.web.api.repoops_api import repo_session_payload

    payload = repo_session_payload(session_id)
    if not payload.get("found"):
        print(payload.get("error") or "not found", file=sys.stderr)
        return 1
    print(json.dumps(payload["session"], indent=2, sort_keys=True))
    return 0


def _authority() -> int:
    """The two mechanical checks behind the architecture claim, run here rather than asserted."""

    from core.kas.conformance import scan_adapters
    from core.kas.egress_census import modules_with_direct_egress

    findings = scan_adapters()
    print(f"KAS adapter conformance: {'PASS' if not findings else 'FAIL'}")
    for finding in findings:
        print(f"  {finding}")
    egress = modules_with_direct_egress()
    print(f"modules with direct transport outside the one door: {len(egress)}")
    for path in sorted(egress):
        print(f"  {egress[path]:>3}  {path}")
    return 0 if not findings else 1


def _plugins() -> int:
    from core.plugin_lifecycle import lifecycle_snapshot

    snapshot = lifecycle_snapshot()
    if not snapshot["plugins"]:
        print("no plugins have entered the lifecycle")
        return 0
    for row in snapshot["plugins"]:
        print(
            f"{row['plugin_id']:<24}  stage={row['stage']:<12}  verified={row['verified']!s:<5}  "
            f"enabled={row['enabled']!s:<5}  available={row['available']}"
        )
    print(f"\navailable to the model: {snapshot['available'] or 'none'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.repoops", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sessions", help="every RepoOps session in the journal")
    receipt = sub.add_parser("receipt", help="one session's full journal, as JSON")
    receipt.add_argument("session_id")
    sub.add_parser("authority", help="run the KAS conformance scan and the egress census")
    sub.add_parser("plugins", help="the plugin lifecycle, and what is actually available")
    args = parser.parse_args(argv)
    if args.command == "sessions":
        return _sessions()
    if args.command == "receipt":
        return _receipt(args.session_id)
    if args.command == "authority":
        return _authority()
    return _plugins()


if __name__ == "__main__":
    raise SystemExit(main())
