"""RETIRED: the installer no longer creates a signing key.

The wallet is created by the operator, explicitly, in the app (watch-only by default; a pocket
wallet only behind the typed warning) — core.wallet is the one custody authority. Running this
script records a typed refusal and exits 1 without touching the filesystem.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def initialize_agent_wallet(runtime_home: str) -> str:
    """RETIRED: refuses typed; creates nothing."""
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("installer.initialize_agent_wallet")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    runtime_home = args[0] if args else ""
    try:
        pubkey = initialize_agent_wallet(runtime_home)
    except Exception as exc:
        print(f"wallet setup is done in the app (core.wallet): {getattr(exc, 'code', '') or exc}", file=sys.stderr)
        return 1
    # stdout carries ONLY the public key so the caller can capture it for the receipt.
    print(pubkey)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
