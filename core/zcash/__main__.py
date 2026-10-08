"""Owner console for Zcash private invoices.

    python -m core.zcash set-key [--birthday HEIGHT]   # reads the viewing key without echo (or from stdin)
    python -m core.zcash status                         # sync and print every invoice's state
    python -m core.zcash address                        # the shielded receiving address
    python -m core.zcash export [--since D] [--until D] # CSV of paid invoices to stdout

The viewing key is never taken as a command-line argument (it would land in shell history and the process list).
"""
from __future__ import annotations

import argparse
import getpass
import sys

from core.zcash import config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.zcash")
    sub = parser.add_subparsers(dest="command", required=True)
    set_key = sub.add_parser("set-key")
    set_key.add_argument("--birthday", type=int, default=None, help="block height to scan from (default: near the chain tip)")
    sub.add_parser("status")
    sub.add_parser("address")
    export = sub.add_parser("export")
    export.add_argument("--since", default="")
    export.add_argument("--until", default="")
    args = parser.parse_args(argv)

    if not config.zcash_enabled():
        print("Zcash invoices are switched off (Settings, or VOOL_ZCASH_ENABLED=1).", file=sys.stderr)
        return 2
    from core.zcash.invoices import InvoiceError
    from core.zcash.keys import ZcashKeyRefused
    from core.zcash.service import ZcashLane
    from core.zcash.watch import ZcashWatchError

    try:
        lane = ZcashLane()
        if args.command == "set-key":
            raw = getpass.getpass("Zcash full viewing key (uview1...): ") if sys.stdin.isatty() else sys.stdin.readline()
            out = lane.set_viewing_key(raw, birthday=args.birthday)
            print(f"Watch-only wallet ready on {out['network']}net. Receiving address: {out['address']}")
            return 0
        if args.command == "address":
            print(lane.receiving_address())
            return 0
        if args.command == "export":
            sys.stdout.write(lane.export_csv(since=args.since, until=args.until))
            return 0
        report = lane.refresh()
        if not report["fresh"]:
            print(f"Cannot confirm payments right now ({report['reason']}). {report['sync_error'] or report['read_error']}".strip())
        print(f"Chain tip: {report['tip']}")
        for status in report["statuses"]:
            d = status.to_dict()
            print(f"{d['invoice_id']}  {d['amount']} {config.ticker()}  {d['state']:<11} memo={d['memo']} txids={','.join(d['txids']) or '-'}")
        for note in report["unmatched"]:
            print(f"unmatched payment: {note.value_zat} zat txid={note.txid} pool={note.pool}")
        return 0 if report["fresh"] else 1
    except (InvoiceError, ZcashWatchError, ZcashKeyRefused) as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
