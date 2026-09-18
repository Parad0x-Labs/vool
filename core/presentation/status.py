"""Canonical status -> visual mapping. Emoji/symbols are PRESENTATION.

The typed Status stays the only semantic carrier. UNKNOWN never becomes
green; PARTIAL never looks identical to PASS. Every rendering pairs the
symbol with the word so color/emoji is never the sole carrier.
"""
from __future__ import annotations

from core.presentation.model import KNOWN_STATUSES, Status

_SYMBOLS: dict[str, str] = {
    "PASS": "✅", "FAIL": "❌", "WARNING": "⚠️", "PENDING": "⏳",
    "UNKNOWN": "❓", "PARTIAL": "◐", "BLOCKED": "⛔",
    "PARKED": "🅿️", "EXPERIMENT": "🧪", "SKIP": "⏭",
}

_MINIMAL: dict[str, str] = {
    "PASS": "[OK]", "FAIL": "[FAIL]", "WARNING": "[WARN]",
    "PENDING": "[...]", "UNKNOWN": "[?]", "PARTIAL": "[~]",
    "BLOCKED": "[X]", "PARKED": "[P]", "EXPERIMENT": "[EXP]",
    "SKIP": "[skip]",
}


def render_status(status: Status, emoji_mode: str = "STANDARD") -> str:
    """Always ``SYMBOL WORD`` (detail appended when present)."""
    if emoji_mode == "MINIMAL":
        token = _MINIMAL.get(status.name, f"[{status.name}]")
    else:
        token = f"{_SYMBOLS[status.name]} {status.name}"
    return f"{token} {status.detail}".rstrip() if status.detail else token


def coerce_status(raw: object) -> Status | None:
    """Only EXACT known vocabulary coerces; anything else stays raw text."""
    if isinstance(raw, Status):
        return raw
    if isinstance(raw, str) and raw in KNOWN_STATUSES:
        return Status(raw)  # type: ignore[arg-type]
    return None
