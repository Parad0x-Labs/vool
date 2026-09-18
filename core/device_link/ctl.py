"""devicectl — the local operator CLI for Device Link grants.

Ported in spirit from the device-mesh pass's tetherctl.py (devices / tier /
revoke / mint-token), adapted to the converged grant protocol and a
FILE-BACKED grant book so it works with no bridge running. The operator law
from the source pass is preserved verbatim in behavior: COMPANIONS CAN NEVER
ELEVATE OR REVOKE THEMSELVES — tiers and revocation are operator actions on
the desktop host, and the only writer of this book is this CLI (and, at
integration time, the bridge with the same authority key).

Nothing here opens a socket. `mint-token` prints the pairing payload for the
operator to move by hand (QR at integration time — operator-gated).

Usage:
    python -m core.device_link.ctl --book <path> --key <path> devices
    python -m core.device_link.ctl --book <path> --key <path> mint-token \
        --device "Pixel 9" --envelope FILES+TERMINAL --scope one_project \
        --path ~/src/project --ttl 3600
    python -m core.device_link.ctl --book <path> revoke <grant-id>
    python -m core.device_link.ctl --book <path> revoke-all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.device_link.identity import load_or_create
from core.device_link.protocol import (
    ENVELOPE_ORDER,
    GrantRegistry,
    issue_grant,
)

#: Envelope aliases the CLI accepts, from the source pass's tier vocabulary.
_TIER_ALIASES = {
    "view_only": "VIEW_ONLY",
    "files": "FILES",
    "terminal": "FILES+TERMINAL",
    "dev_machine": "DEV_MACHINE",
    "full": "FULL_REMOTE_CONTROL",
}


def load_book(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"grants": {}, "revoked": {}}
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or "grants" not in data:
        raise ValueError(f"not a device-link grant book: {path}")
    return data


def save_book(path: Path, book: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, indent=2, sort_keys=True))
    tmp.replace(path)


def registry_from_book(book: dict) -> GrantRegistry:
    registry = GrantRegistry()
    for grant_id, grant in (book.get("grants") or {}).items():
        grant = dict(grant)
        grant["grant_id"] = grant_id
        registry.register(grant)
    for grant_id, revocation in (book.get("revoked") or {}).items():
        registry.revoke(grant_id, reason=str((revocation or {}).get("reason") or ""))
    return registry


def _envelope(value: str) -> str:
    resolved = _TIER_ALIASES.get(str(value).strip().lower(), str(value).strip().upper())
    if resolved not in ENVELOPE_ORDER:
        raise SystemExit(
            f"unknown envelope {value!r}; one of {', '.join(ENVELOPE_ORDER)} (or "
            f"alias {', '.join(_TIER_ALIASES)})"
        )
    return resolved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="devicectl", description=__doc__.splitlines()[0])
    ap.add_argument("--book", required=True, type=Path, help="grant book JSON path")
    ap.add_argument("--key", type=Path, help="authority key path (mint-token only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices", help="list grants and their live status")
    mint = sub.add_parser("mint-token", help="mint a grant and print its pairing payload")
    mint.add_argument("--device", required=True)
    mint.add_argument("--envelope", required=True)
    mint.add_argument("--scope", default="one_session",
                      choices=["one_action", "one_session", "one_project", "paths", "until_revoked"])
    mint.add_argument("--path", action="append", default=[], help="allowed root (repeatable)")
    mint.add_argument("--ttl", type=int, default=3600)
    mint.add_argument("--max-uses", type=int, default=1)
    rv = sub.add_parser("revoke", help="revoke one grant by id")
    rv.add_argument("grant_id")
    rv.add_argument("--reason", default="operator revocation")
    sub.add_parser("revoke-all", help="revoke every grant (panic)")

    args = ap.parse_args(argv)
    book = load_book(args.book)

    if args.cmd == "devices":
        rows = registry_from_book(book).active_summary()
        if not rows:
            print("no grants in the book")
            return 0
        for row in rows:
            state = "ALIVE" if row["alive"] else ("REVOKED" if row["revoked"] else "INACTIVE")
            print(f"{row['grant_id'][:8]}  {state:<8} {row['envelope']:<20} "
                  f"{row['device_name']:<20} uses={row['uses']} "
                  f"exp={row['expires_at']}")
        return 0

    if args.cmd == "mint-token":
        if not args.key:
            print("--key is required for mint-token", file=sys.stderr)
            return 2
        signing_key = load_or_create(args.key)
        grant = issue_grant(
            signing_key=signing_key,
            device_name=args.device,
            envelope=_envelope(args.envelope),
            scope_kind=args.scope,
            allowed_paths=args.path or None,
            ttl_seconds=args.ttl,
            max_uses=args.max_uses,
        )
        book.setdefault("grants", {})[grant["grant_id"]] = grant
        save_book(args.book, book)
        print(json.dumps(grant, indent=2, sort_keys=True))
        return 0

    if args.cmd == "revoke":
        ok = registry_from_book(book).revoke(args.grant_id, reason=args.reason)
        if not ok:
            print(f"no such grant: {args.grant_id}", file=sys.stderr)
            return 1
        book.setdefault("revoked", {})[args.grant_id] = {"reason": args.reason}
        save_book(args.book, book)
        print(f"revoked {args.grant_id}")
        return 0

    if args.cmd == "revoke-all":
        n = registry_from_book(book).revoke_all(reason="panic")
        for grant_id in list((book.get("grants") or {})):
            book.setdefault("revoked", {}).setdefault(
                grant_id, {"reason": "panic"})
        save_book(args.book, book)
        print(f"revoked {n} grant(s)")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
