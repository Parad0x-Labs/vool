"""Blackbox-safe redaction for every wallet record that leaves the custody boundary.

Two layers: secret-shaped KEYS are dropped outright (a PIN under any spelling never reaches a
journal), and every remaining string passes the runtime's canonical secret masker.
"""
from __future__ import annotations

import re
from typing import Any

from core.secret_redaction import redact_secrets

SECRET_KEYS: frozenset[str] = frozenset(
    {
        "recovery_phrase", "phrase", "mnemonic", "seed", "seed_hex", "seed_b64", "secret", "secret_key", "private_key",
        "privkey", "pin", "pin_unlock", "passphrase", "password", "pan", "card_number", "cvv", "cvc", "cvv2", "sealed_blob",
        "signing_key", "keypair", "backup_value", "ack_token", "raw_b64", "raw", "raw_hex", "signed_transaction", "signed_transaction_b64",
        "credential",
    }
)

#: Public identifiers that are base58 and long enough to look like key material to the generic
#: masker. They are public by definition (an on-chain signature, an address) and stay readable.
PUBLIC_IDENTIFIER_KEYS: frozenset[str] = frozenset({"tx_signature", "public_key", "destination", "pay_to", "blockhash", "wallet_id", "proposal_id", "tx_id", "explorer_url", "from_address", "to_address"})

#: x402 digest fields: the sha256-hex the wallet itself minted to bind a payment to its request
#: (METHOD|url) and to the resource it delivered. A digest is a one-way commitment that is public
#: by construction -- it is what correlates a receipt with its binding -- and a 64-char hex digest
#: with no '0' is also entirely inside the generic base58 secret class, so without this contract
#: the masker corrupts stored receipts. The exemption is shape-guarded: only the producer's exact
#: hex64 spelling passes, so a secret-shaped string riding under these field names is still masked.
DIGEST_KEYS: frozenset[str] = frozenset({"request_digest", "resource_digest"})
_HEX64_RE = re.compile(r"\A[0-9a-f]{64}\Z")


def publish_identifier(value: str | None) -> str:
    """Register a signature the wallet produced as PUBLIC with the free-text redactor and return it.

    A transaction signature is 64 bytes of base58 -- the exact shape of a secret key -- so the
    runtime's shape-based redaction would mask it out of a chat reply. The wallet is the one
    authority that knows which such strings are public; it says so here.
    """
    from core.secret_redaction import register_public_identifier

    clean = str(value or "")
    if clean:
        register_public_identifier(clean)
    return clean


def _secret_key(name: str) -> bool:
    key = str(name or "").strip().lower()
    return key in SECRET_KEYS or key.endswith("_pin") or key.endswith("_secret") or key.endswith("_phrase")


def _public_identifier(name: str, value: Any) -> bool:
    """A string field the wallet layer itself vouches is public: a named identifier (an on-chain
    signature, an address), or an x402 digest in its producer's exact hex64 spelling."""
    if not isinstance(value, str):
        return False
    key = str(name or "").lower()
    return key in PUBLIC_IDENTIFIER_KEYS or (key in DIGEST_KEYS and bool(_HEX64_RE.match(value)))


def redact_wallet_record(record: Any, *, _depth: int = 0) -> Any:
    if _depth > 12:
        return "[depth]"
    if isinstance(record, dict):
        cleaned: dict[str, Any] = {}
        for key, value in record.items():
            name = str(key)
            if _secret_key(name):
                continue
            if _public_identifier(name, value):
                cleaned[name] = value
            else:
                cleaned[name] = redact_wallet_record(value, _depth=_depth + 1)
        return cleaned
    if isinstance(record, list | tuple):
        return [redact_wallet_record(v, _depth=_depth + 1) for v in record]
    if isinstance(record, bytes | bytearray):
        return f"[{len(record)} bytes]"
    if isinstance(record, str):
        return redact_secrets(record)
    return record
