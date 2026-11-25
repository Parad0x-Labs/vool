"""VOOL's own 64-hex ids are never read as a Solana secret key (2026-11-25).

The disclosure suite failed now and then in CI (4e06fdc5c, 0c3d44046): a receipt or action id is 64 hex digits, and
one with no "0" in it (about 1 in 60) fits the base58 alphabet, so the secret check read it as a Solana key and the
privacy gate kept a cloud model's second request on the computer. The id the runtime minted is known as its own where
it is minted; a run anyone else wrote, inside a hyphenated name or not, is still a key."""
from __future__ import annotations

import random

import pytest

from core.agent_runtime import orchestrator
from core.secret_redaction import clear_exact_secrets_for_tests, contains_secret, redact_secrets

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


@pytest.fixture(autouse=True)
def _fresh():
    clear_exact_secrets_for_tests()
    yield
    clear_exact_secrets_for_tests()


def _hex_without_zero(rng: random.Random, n: int = 64) -> str:
    return "".join(rng.choice("123456789abcdef") for _ in range(n))


def _minted(nonce: int) -> dict:
    return orchestrator.build_tool_action_record(
        {"chat_id": "hex-id-chat"}, event_type="tool_executed", message="Synthetic id proof.",
        details={"tool_name": "workspace.read_file", "status": "tool_executed", "ok": True,
                 "summary": f"Synthetic id proof {nonce}.", "tool_call_id": f"hex-id-call-{nonce}",
                 "arguments": {"path": f"synthetic-{nonce}.txt"}})


def test_ids_the_runtime_minted_never_trip_the_check():
    zero_free = 0
    for nonce in range(1500):
        record = _minted(nonce)
        for ident in (record["action_id"], record["receipt_id"]):
            zero_free += "0" not in ident.rsplit("-", 1)[1]
            text = f'{{"receipt_id": "{ident}", "note": "done"}}'
            assert not contains_secret(text), text
            assert ident in redact_secrets(text)
    assert zero_free  # the probe met the base58-shaped ids it is about


def test_a_digest_shaped_run_nobody_minted_is_still_a_key():
    rng = random.Random("hex-ids")
    for _ in range(20):
        ident = _hex_without_zero(rng)
        for text in (f"tool-receipt-{ident}", f"my-key-{ident}", ident):
            assert contains_secret(text), text
            assert ident not in redact_secrets(text)


def test_a_base58_secret_key_is_still_caught():
    rng = random.Random("b58-keys")
    for length in (64, 87, 88):
        key = "".join(rng.choice(_B58) for _ in range(length))
        for text in (f"my key is {key}", f"key:{key}", f'{{"secret": "{key}"}}', f"seed={key}", f"wallet-{key}"):
            assert contains_secret(text), text
            assert key not in redact_secrets(text)
