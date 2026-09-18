"""Control epochs: counters bumped in the same transaction as a freeze, environment or Crypto-switch change.

A transfer records the epochs when it is claimed and a send compares them inside its guarded transaction, so a
freeze followed by an unfreeze (or a switch away and back) still reads as a change.
"""
from __future__ import annotations

from typing import Any

EPOCHS: tuple[str, ...] = ("freeze", "enabled", "environment")


def _key(name: str) -> str:
    if name not in EPOCHS:
        raise ValueError(f"unknown control epoch: {name}")
    return f"epoch.{name}"


def epochs(conn: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in EPOCHS:
        row = conn.execute("SELECT value FROM wallet_controls WHERE key = ?", (_key(name),)).fetchone()
        try:
            out[name] = int(row[0]) if row else 0
        except (TypeError, ValueError):
            out[name] = 0
    return out


def bump(conn: Any, name: str) -> int:
    """Increment one epoch on the caller's connection; returns the new value."""
    from core.wallet.store import utcnow

    current = epochs(conn)[name] + 1
    conn.execute(
        "INSERT INTO wallet_controls (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (_key(name), str(current), utcnow()),
    )
    return current


__all__ = ["EPOCHS", "bump", "epochs"]
