"""What may sit on a fault record: allowlisted keys, redacted values, bounded size.

A fault record is durable diagnostics, which makes it a leak surface: the raw material
a boundary would love to attach -- the failing path, the request payload, the
exception's ``str()`` -- is exactly what must never persist. The law here is stricter
than "mask values":

* CONTEXT KEYS ARE ALLOWLISTED. A key the vocabulary never declared is DROPPED, not
  value-masked -- a future ``session_token`` key added by a well-meaning producer must
  not ride a record to disk because its value happened to look clean;
* VALUES pass the repo's high-confidence secret masker plus path stripping (the same
  two passes ``core.error_surface`` applies to chat-safe text), then a hard length cap;
* the result is deterministic and idempotent, so a record re-derived from a record is
  byte-stable.
"""
from __future__ import annotations

import contextlib
from typing import Any

from core.error_surface import _strip_paths
from core.secret_redaction import redact_secrets

#: The only context keys a fault record may carry. Everything else is dropped.
#: Extend deliberately: a key here declares "this key's redacted value is safe and
#: useful on a durable diagnostic".
CONTEXT_KEY_ALLOWLIST: tuple[str, ...] = (
    "tool_name",
    "intent",
    "provider_id",
    "model_id",
    "model_call_id",
    "call_id",
    "call_role",
    "host",
    "lane",
    "operation",
    "effect_class",
    "error_class",
    "status",
    "reason",
    "decision",
    "mode",
    "surface",
    "stage",
    "credential_name",
    "resource_class",
    "target_name",
    "entry_id",
    "receipt_id",
    "task_id",
    "claim_count",
    "missing_count",
    # wallet family: public identifiers only (a destination is a public address; a PIN or phrase is never a context key)
    "wallet_id",
    "proposal_id",
    "network",
    "asset",
    "amount_minor",
    "destination",
    "limit",
    "origin",
    "tx_signature",
    "idempotency_key",
    "method",
    "field",
    "surface",
)

#: Hard per-value cap. A diagnostic that needed more than this to be useful is a
#: diagnostic that should reference evidence by id instead.
_MAX_VALUE_CHARS = 200

_TRUNCATION_MARK = "…[truncated]"


def redact_text(value: Any, *, max_chars: int = _MAX_VALUE_CHARS) -> str:
    """One value, safe for a durable record: secret-masked, path-stripped, capped.

    Deterministic and idempotent -- masking already-masked text changes nothing, which
    is what makes re-derivation stable.
    """
    try:
        text = str(value or "")
    except Exception:
        return ""
    if not text:
        return ""
    # Either masking pass failing must never fail the redaction: the unmasked text is
    # the one thing this function may never return.
    with contextlib.suppress(Exception):
        text = redact_secrets(text)
    with contextlib.suppress(Exception):
        text = _strip_paths(text)
    if len(text) > max_chars:
        text = text[: max(0, max_chars - len(_TRUNCATION_MARK))].rstrip() + _TRUNCATION_MARK
    return text


def redact_context(mapping: dict[str, Any] | None) -> dict[str, str]:
    """The allowlisted, redacted form of a producer's context.

    Unknown keys are dropped (never guessed at), known keys keep their redacted values,
    and empty values are omitted entirely so a sparse context stays sparse.
    """
    if not isinstance(mapping, dict):
        return {}
    allowlist = set(CONTEXT_KEY_ALLOWLIST)
    out: dict[str, str] = {}
    for key, value in mapping.items():
        name = str(key or "").strip()
        if name not in allowlist:
            continue
        if value is None or value == "" or value == ():
            continue
        out[name] = redact_text(value)
    return out


__all__ = ["CONTEXT_KEY_ALLOWLIST", "redact_context", "redact_text"]
