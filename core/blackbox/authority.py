"""Who may roll a turn back: an operator, through a bound, single-use, expiring token that only
server code can mint.

The token rides in the SOURCE CONTEXT under ``blackbox_rollback`` -- the server-built dict --
never in tool arguments, which are the model's channel. The dispatcher rejects unknown argument
keys for the rollback intent outright, so no model output can carry a directive; and a directive
without a valid token is a typed refusal, not a rollback.

Limit, stated: the MAC key is derived from the journal key in the store directory. A process
running as the same user can mint a valid token. This authority separates the MODEL from the
OPERATOR; it does not separate the operator from another process of the operator's own account.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.blackbox.store import BlackboxStore, default_store

ROLLBACK_TOKEN_TTL_SECONDS = 600
_TOKEN_PREFIX = "bbrb1"
DIRECTIVE_KEY = "blackbox_rollback"


@dataclass(frozen=True)
class AuthorizationVerdict:
    ok: bool
    reason: str
    nonce: str


def _authority_key(store: BlackboxStore) -> bytes:
    return hmac.new(store.journal.key(), b"blackbox-rollback-authority", hashlib.sha256).digest()


def _message(turn_id: str, root: str, nonce: str, expires: int) -> bytes:
    return f"{turn_id}\n{root}\n{nonce}\n{expires}".encode()


def _canonical_root(workspace_root: Path | str) -> str:
    return str(Path(workspace_root).expanduser().resolve())


def mint_rollback_authorization(
    *,
    turn_id: str,
    workspace_root: Path | str,
    operator: str,
    store: BlackboxStore | None = None,
    ttl_seconds: int = ROLLBACK_TOKEN_TTL_SECONDS,
) -> str:
    store = store or default_store()
    root = _canonical_root(workspace_root)
    nonce = uuid.uuid4().hex
    expires = int(time.time()) + int(ttl_seconds)
    mac = hmac.new(_authority_key(store), _message(str(turn_id), root, nonce, expires), hashlib.sha256).hexdigest()
    store.append(
        {
            "schema": "blackbox_effect_v1",
            "kind": "rollback_authorized",
            "nonce": nonce,
            "rollback_of_turn": str(turn_id),
            "root": root,
            "operator": str(operator or ""),
            "expires_at_epoch": expires,
        }
    )
    return f"{_TOKEN_PREFIX}.{nonce}.{expires}.{mac}"


def verify_rollback_authorization(
    token: str,
    *,
    turn_id: str,
    workspace_root: Path | str,
    store: BlackboxStore | None = None,
) -> AuthorizationVerdict:
    store = store or default_store()
    parts = str(token or "").split(".")
    if len(parts) != 4 or parts[0] != _TOKEN_PREFIX:
        return AuthorizationVerdict(False, "malformed_token", "")
    _prefix, nonce, expires_text, mac = parts
    try:
        expires = int(expires_text)
    except ValueError:
        return AuthorizationVerdict(False, "malformed_token", nonce)
    root = _canonical_root(workspace_root)
    expected = hmac.new(_authority_key(store), _message(str(turn_id), root, nonce, expires), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return AuthorizationVerdict(False, "signature_mismatch_or_mistargeted", nonce)
    if time.time() > expires:
        return AuthorizationVerdict(False, "expired", nonce)
    minted = False
    for entry in store.entries():
        kind = entry.get("kind")
        if kind == "rollback_authorized" and entry.get("nonce") == nonce:
            if str(entry.get("rollback_of_turn")) != str(turn_id) or str(entry.get("root")) != root:
                return AuthorizationVerdict(False, "mistargeted", nonce)
            minted = True
        elif kind == "rollback_authorization_consumed" and entry.get("nonce") == nonce:
            return AuthorizationVerdict(False, "already_used", nonce)
    if not minted:
        return AuthorizationVerdict(False, "unknown_nonce", nonce)
    return AuthorizationVerdict(True, "verified", nonce)


def consume_rollback_authorization(nonce: str, *, store: BlackboxStore | None = None, reason: str = "") -> None:
    store = store or default_store()
    store.append(
        {
            "schema": "blackbox_effect_v1",
            "kind": "rollback_authorization_consumed",
            "nonce": str(nonce),
            "reason": str(reason or ""),
        }
    )


def directive_from_context(source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    directive = (source_context or {}).get(DIRECTIVE_KEY)
    if not isinstance(directive, dict):
        return None
    turn_id = str(directive.get("turn_id") or "").strip()
    if not turn_id:
        return None
    return {
        "turn_id": turn_id,
        "authorization": str(directive.get("authorization") or ""),
        "operator": str(directive.get("operator") or ""),
    }


__all__ = [
    "DIRECTIVE_KEY",
    "ROLLBACK_TOKEN_TTL_SECONDS",
    "AuthorizationVerdict",
    "consume_rollback_authorization",
    "directive_from_context",
    "mint_rollback_authorization",
    "verify_rollback_authorization",
]
