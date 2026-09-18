"""A blockchain identifier is not a model-size demand.

Measured on the correction-2 cumulative (served wallet matrix, 2026-09-15): the served chat request
"Send 0.002 ETH on ethereum to 0xcb67d1327a06435db7f6600c6d753dbbd230e82b for the ethereum testnet
proof" was sized as an explicit 82B demand -- the `0x` prefix kept the address out of the hex-identifier
stripper (no word boundary after the `x`) while the tail `82b` closed the token -- and with no heavy local
lane the turn was refused before any adapter. 234 of 10,000 random lowercase EVM addresses did the same.
The owning classifier now removes `0x`-prefixed hex strings and long base58 runs (Solana addresses and
signatures) before sizing; genuine size requests, model names and mixed requests still qualify.
"""
from __future__ import annotations

import random

import pytest

from core.local_inference_autopilot import _explicit_heavy_requested, _largest_parameter_size_b, _requested_heavy_marker

ORIGINAL = "Send 0.002 ETH on ethereum to 0xcb67d1327a06435db7f6600c6d753dbbd230e82b for the ethereum testnet proof"


def _heavy(text: str, requested_model: str = "") -> bool:
    return _explicit_heavy_requested(user_text=text, source_context={"requested_model": requested_model})


def test_the_measured_recipient_is_not_a_size() -> None:
    assert _largest_parameter_size_b(ORIGINAL) == 0.0
    assert _heavy(ORIGINAL) is False and _requested_heavy_marker(user_text=ORIGINAL, source_context={}) is None


@pytest.mark.parametrize(
    "text",
    [
        "Pay 0.5 ETH to 0x9b731f4e62224a92f0e685efdb715e7b7eb9259b now",                       # a novel address, tail 9259b
        "transfer to 0x45b2209a72ea27259b10d3ee84694c5a70ff763b on base",                        # 763b
        "0x074111cbc1a931da3cdcd3ef16ac2abce37de62b received it",                                 # 62b, sentence-initial
        "the tx 0xd5122fda6cd5be5eb3f5eb4e998067ec3c581fdd4a0455af56dd0a5cf1f5ec40 confirmed",  # a transaction hash
        "Send 1 SOL to 7GmDf1qUjkr2W6x1z5Xc9nVhQ8PbNw4tK3rL9sYaz82b please",                    # a Solana address, tail 82b
        "signature 2CLLLowCGDSj6vRgndnFvtRkjMJpH2nrQNvVDh6SgWm3CMRyk5Tx2qWeUhucewAKqkkWeZQF8DtmWvTsKo5MckFJ",
        "Send 0.0004 SOL on solana to E8EwqzDtTsrTXZCc38djcLEYrizB5E3qrnZxZCW7yUgn for the native proof",
    ],
)
def test_addresses_hashes_and_signatures_never_qualify(text: str) -> None:
    assert _largest_parameter_size_b(text) == 0.0 and _heavy(text) is False


def test_random_evm_recipients_never_qualify() -> None:
    rng = random.Random(20260915)
    for _ in range(2_000):
        address = "0x" + "".join(rng.choice("0123456789abcdef") for _ in range(40))
        text = f"Send 0.002 ETH on ethereum to {address} for the proof"
        assert _heavy(text) is False, address
        assert _heavy(text.replace(address, address.upper().replace("0X", "0x"))) is False, address  # checksum-style casing


@pytest.mark.parametrize(
    ("text", "requested_model", "size"),
    [
        ("please run this on a 70b model", "", 70.0),
        ("", "qwen3:32b", 32.0),
        ("", "nemotron-3-ultra-550b-a55b:free", 550.0),
        ("", "mixtral-8x22b", 176.0),
        ("send 0.01 ETH to 0xcb67d1327a06435db7f6600c6d753dbbd230e82b and use the 32b model to write the memo", "", 32.0),  # mixed
        ('the report says "the 405b variant" is best', "", 405.0),                                                    # quoted
        ("use the heavy model for this", "", 0.0),
    ],
)
def test_genuine_size_requests_model_names_mixed_and_quoted_still_qualify(text: str, requested_model: str, size: float) -> None:
    assert _heavy(text, requested_model) is True
    assert _largest_parameter_size_b(requested_model, text) == size


@pytest.mark.parametrize("text", ["send 0.1 ETH to 0xabc", "a 4b model is enough", "", "0x deadbeef"])
def test_small_or_absent_sizes_stay_light(text: str) -> None:
    assert _heavy(text) is False


def test_the_size_regex_itself_is_unchanged_for_plain_prose() -> None:
    assert _largest_parameter_size_b("an 82b run in plain prose") == 82.0  # the parser is untouched; identifiers are removed before it
