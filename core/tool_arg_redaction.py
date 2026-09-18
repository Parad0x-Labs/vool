"""A short, secret-safe, human-readable summary of a tool call's arguments for the Activity timeline.

The Activity panel shows WHAT a tool ran with (`path=README.md`, `query="how to..."`), not just the
tool name. Arguments can carry file contents, a search query, or an accidental secret, so this drops
keys that name a secret outright, summarizes bulky values as a length, truncates the rest, and runs
the whole line through the shared secret redactor as a final pass. It is DISPLAY-ONLY -- never used to
re-execute anything -- and never raises.
"""
from __future__ import annotations

from typing import Any

from core.secret_redaction import redact_secrets

# A key is treated as secret if it is exactly one of these...
_SECRET_KEYS_EXACT = {"key", "token", "secret", "password", "passwd", "pwd", "authorization", "bearer"}
# ...or contains one of these strong markers (so "keyword"/"keys" stay readable, "api_key" does not).
_SECRET_KEY_MARKERS = (
    "password", "secret", "mnemonic", "passphrase", "credential", "api_key", "apikey",
    "access_token", "auth_token", "client_secret", "private_key", "seed_phrase",
)
# Keys whose value is bulky/irrelevant for a one-line trace -> summarized as a length, not inlined.
_BULKY_KEYS = {"content", "contents", "body", "text", "data", "file_content", "patch", "diff", "source"}
# Internal plumbing that is not part of the user-facing intent.
_SKIP_KEYS = {"idempotency_key", "task_id", "source_context", "checkpoint_id", "step_index"}

_MAX_VALUE = 60      # per-value display cap
_MAX_LINE = 160      # whole-summary cap


def _is_secret_key(name: str) -> bool:
    lowered = name.lower()
    if lowered in _SECRET_KEYS_EXACT:
        return True
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _short_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        try:
            count = len(value)
        except Exception:
            count = 0
        kind = "keys" if isinstance(value, dict) else "items"
        return f"({count} {kind})"
    text = " ".join(str(value).split())      # collapse newlines/whitespace to one line
    if len(text) > _MAX_VALUE:
        text = text[:_MAX_VALUE].rstrip() + "…"
    return text


def redact_tool_arguments(arguments: Any) -> str:
    """A one-line ``k=v, k=v`` summary of ``arguments``, secret-safe. Returns "" when there is nothing
    worth showing. Never raises."""
    try:
        if not isinstance(arguments, dict) or not arguments:
            return ""
        bits: list[str] = []
        for key, value in arguments.items():
            name = str(key)
            if name.startswith("_") or name in _SKIP_KEYS:
                continue
            if _is_secret_key(name):
                bits.append(f"{name}=[redacted]")
            elif name.lower() in _BULKY_KEYS:
                bits.append(f"{name}=({len(str(value))} chars)")
            else:
                bits.append(f"{name}={_short_value(value)}")
        line = ", ".join(bit for bit in bits if bit)
        if len(line) > _MAX_LINE:
            line = line[:_MAX_LINE].rstrip() + "…"
        # Belt-and-suspenders: mask any high-confidence secret that slipped through a plain value.
        return redact_secrets(line)
    except Exception:
        return ""


__all__ = ["redact_tool_arguments"]
