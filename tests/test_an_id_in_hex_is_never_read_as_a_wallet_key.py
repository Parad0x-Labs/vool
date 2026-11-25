"""VOOL's own 64-hex ids are never read as a Solana secret key (2026-11-25).

The disclosure suite failed now and then in CI (4e06fdc5c, 0c3d44046): a receipt or action id is 64 hex digits, and
one with no "0" in it (about 1 in 60) fits the base58 alphabet, so the secret check read it as a Solana key and the
privacy gate kept a cloud model's second request on the computer. The tail of a hyphen-joined name is that name's id;
a key on its own, digest-shaped or not, is still caught."""
from __future__ import annotations

import random
import secrets

from core.secret_redaction import contains_secret, redact_secrets

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _hex_without_zero(rng: random.Random, n: int = 64) -> str:
    return "".join(rng.choice("123456789abcdef") for _ in range(n))


def test_a_hex_id_with_no_zero_is_no_secret():
    rng = random.Random("hex-ids")
    for _ in range(50):
        ident = _hex_without_zero(rng)
        text = f'{{"receipt_id": "tool-receipt-{ident}", "action_id": "tool-action-{ident.upper()}"}}'
        assert not contains_secret(text), text
        assert ident in redact_secrets(text)


def test_random_receipt_ids_never_trip_the_check():
    hits = [h for h in (secrets.token_hex(32) for _ in range(3000)) if contains_secret(f"tool-receipt-{h}")]
    assert hits == []


def test_a_base58_secret_key_is_still_caught():
    rng = random.Random("b58-keys")
    for length in (64, 87, 88):
        key = "".join(rng.choice(_B58) for _ in range(length))
        for text in (f"my key is {key}", f"key:{key}", f'{{"secret": "{key}"}}', f"seed={key}"):
            assert contains_secret(text), text
            assert key not in redact_secrets(text)
    bare = _hex_without_zero(rng)
    assert contains_secret(bare) and bare not in redact_secrets(bare), "a bare digest-shaped run is still a key"
