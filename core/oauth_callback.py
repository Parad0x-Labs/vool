"""Desktop-owned receiver for the OAuth authorization callback delivered via the ``vool://`` scheme.

Boundary (VOOL_MIGRATION.md §3 / the coordination note): the website (vool.dev) is a **pure relay** —
it 302s the provider's ``?code=&state=`` to ``vool://auth/<provider>/callback`` and never sees the
PKCE verifier or exchanges anything. The macOS app registers that scheme; its window catches the URL
and POSTs ``{code, state}`` to the local runtime, which lands **here**. The desktop owns state
verification and the code→key exchange (verify ``state`` against the flow it started → exchange
``code`` + the PKCE ``code_verifier`` for the provider key → store it in the Keychain).

This module holds only the most recent callback, in memory, for the exchanger to consume. The
authorization ``code`` is short-lived and single-use: it is never logged, never written to disk, and
the status view returns only whether a code is present — never the code itself.
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_PENDING: dict[str, object] | None = None  # {provider, state, code, received_at}


def record_callback(provider: str, code: str, state: str) -> dict[str, str]:
    """Store the just-received callback for the exchanger. Returns a redacted ack (no code)."""
    global _PENDING
    entry: dict[str, object] = {
        "provider": str(provider or "").strip().lower()[:40],
        "state": str(state or "").strip()[:512],
        "code": str(code or ""),          # in memory only — never logged or persisted
        "received_at": time.time(),
    }
    with _LOCK:
        _PENDING = entry
    return {"provider": str(entry["provider"]), "state": str(entry["state"])}


def take_callback() -> dict[str, object] | None:
    """Pop the pending callback (single-use) for the OAuth exchanger, or None if there is none."""
    global _PENDING
    with _LOCK:
        entry, _PENDING = _PENDING, None
    return dict(entry) if entry else None


def callback_status() -> dict[str, object]:
    """Redacted view for diagnostics/verification: provider, state, has_code, age — NEVER the code."""
    with _LOCK:
        entry = _PENDING
    if not entry:
        return {"pending": False}
    return {
        "pending": True,
        "provider": entry.get("provider"),
        "state": entry.get("state"),
        "has_code": bool(entry.get("code")),
        "age_seconds": round(max(0.0, time.time() - float(entry.get("received_at") or 0)), 1),
    }


def clear() -> None:
    global _PENDING
    with _LOCK:
        _PENDING = None


__all__ = ["callback_status", "clear", "record_callback", "take_callback"]
