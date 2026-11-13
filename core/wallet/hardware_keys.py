"""Whether this VOOL offers the hardware protections that need a device in hand: a security key (YubiKey or
similar) as a second unlock, and a Ledger as a Solana wallet's signer.

Both are off until they are proven on real hardware. While off, Settings shows no "Add a security key" and no
"Add a Ledger", and both add doors refuse with nothing changed. A wallet that already has a key or a Ledger keeps
working (opening, paying, removing the key), so turning this off never locks anyone out of their money.

``VOOL_WALLET_HARDWARE_KEYS=1`` turns them on for a hardware test run.
"""
from __future__ import annotations

import os

ENV = "VOOL_WALLET_HARDWARE_KEYS"
REASON_NOT_OFFERED = "hardware_keys_not_offered"


def offered() -> bool:
    return os.environ.get(ENV, "").strip() == "1"


__all__ = ["ENV", "REASON_NOT_OFFERED", "offered"]
