"""Zcash private invoices: the switch and the operator knobs, read on every call."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

ENABLED_ENV = "VOOL_ZCASH_ENABLED"
NETWORK_ENV = "VOOL_ZCASH_NETWORK"
DEVTOOL_ENV = "VOOL_ZCASH_DEVTOOL"
DEVTOOL_SHA256_ENV = "VOOL_ZCASH_DEVTOOL_SHA256"
SERVER_ENV = "VOOL_ZCASH_SERVER"
CONNECTION_ENV = "VOOL_ZCASH_CONNECTION"
CONFIRMATIONS_ENV = "VOOL_ZCASH_CONFIRMATIONS"
STALE_AFTER_ENV = "VOOL_ZCASH_STALE_AFTER_SECONDS"
DATA_DIR_ENV = "VOOL_ZCASH_DATA_DIR"

NETWORK_MAIN = "main"
NETWORK_TEST = "test"
DEFAULT_CONFIRMATIONS = 3
MAX_CONFIRMATIONS = 1000
#: A sync older than this cannot prove an invoice is still unpaid or newly paid: the answer is unknown.
DEFAULT_STALE_AFTER_SECONDS = 15 * 60

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def zcash_enabled() -> bool:
    """OFF by default. A truthy env value forces it on, an explicit false forces it off; unset, Settings decides.

    A money lane never exists outside the Personal edition (VOOL School has no money features), whatever the switch says.
    """
    try:
        from core.product_edition import ProductEdition, active_edition

        if active_edition() is not ProductEdition.PERSONAL:
            return False
    except Exception:
        return False
    raw = str(os.environ.get(ENABLED_ENV) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    try:
        from core.user_preferences import load_preferences

        return bool(getattr(load_preferences(), "zcash_invoices_enabled", False))
    except Exception:
        return False


def network() -> str:
    """``main`` (the default) or ``test``. Anything else is refused by the caller, never guessed."""
    raw = str(os.environ.get(NETWORK_ENV) or NETWORK_MAIN).strip().lower()
    return {"mainnet": NETWORK_MAIN, "testnet": NETWORK_TEST}.get(raw, raw)


def ticker(net: str | None = None) -> str:
    return "TAZ" if (net or network()) == NETWORK_TEST else "ZEC"


def devtool_path() -> str:
    return str(os.environ.get(DEVTOOL_ENV) or "").strip()


def devtool_sha256() -> str:
    return str(os.environ.get(DEVTOOL_SHA256_ENV) or "").strip().lower()


def server() -> str:
    """The lightwalletd operator or host:port handed to the devtool; its own default when unset."""
    return str(os.environ.get(SERVER_ENV) or "zecrocks").strip()


def connection() -> str:
    """``direct`` (default), ``tor`` or ``socks5://host:port`` -- the devtool's own connection modes."""
    return str(os.environ.get(CONNECTION_ENV) or "direct").strip()


def _int_env(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(hi, max(lo, value))


def confirmations_required() -> int:
    return _int_env(CONFIRMATIONS_ENV, DEFAULT_CONFIRMATIONS, lo=1, hi=MAX_CONFIRMATIONS)


def stale_after_seconds() -> int:
    return _int_env(STALE_AFTER_ENV, DEFAULT_STALE_AFTER_SECONDS, lo=30, hi=7 * 24 * 3600)


def data_root() -> Path:
    """VOOL's Zcash folder (owner-only): one subfolder per network, plus the pinned tool copy."""
    override = str(os.environ.get(DATA_DIR_ENV) or "").strip()
    if override:
        root = Path(override)
    else:
        from core.runtime_paths import data_path

        root = data_path("zcash")
    return _owner_only(root)


def data_dir() -> Path:
    """Where the view-only wallet and the invoice store live: under VOOL's data dir, per network."""
    return _owner_only(data_root() / network())


def _owner_only(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o700)
    return path
