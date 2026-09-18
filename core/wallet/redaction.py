"""Blackbox-safe redaction for every wallet record that leaves the custody boundary.

Two layers: secret-shaped KEYS are dropped outright (a PIN under any spelling never reaches a
journal), and every remaining string passes the runtime's canonical secret masker.
"""
from __future__ import annotations

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


def redact_wallet_record(record: Any, *, _depth: int = 0) -> Any:
    if _depth > 12:
        return "[depth]"
    if isinstance(record, dict):
        cleaned: dict[str, Any] = {}
        for key, value in record.items():
            name = str(key)
            if _secret_key(name):
                continue
            if name.lower() in PUBLIC_IDENTIFIER_KEYS and isinstance(value, str):
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
