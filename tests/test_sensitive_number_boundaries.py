"""Hex identifiers survive card intake; cards remain protected even when glued to text."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from core.agent_runtime.secret_intake import consume_secret_intake
from core.secret_redaction import (
    contains_secret,
    redact_secrets,
    redactable_personal_kinds,
)


def test_generated_non_card_numbers_reach_intake_unchanged():
    rng = random.Random(222)
    # These are generated UUIDs on which the old substring matcher fired: both
    # Luhn-valid digit fragments and a hex "cc" fragment mistaken for a label.
    identifiers = [
        "17fc2553-5349-4882-9e61-c85ea35bea9f",
        "720ced25-cc11-4791-8691-000ad2d95c6c",
        "cc745763-8000-4673-8152-bc2200233167",
        "80d93a9c-7399-4761-8118-80f5af1a33cf",
        "11111111-1111-4111-8111-111111111111",
    ]
    identifiers.extend(str(UUID(int=rng.getrandbits(128), version=4)) for _ in range(1000))
    texts = [f"approve calendar {value}" for value in identifiers]
    for _ in range(1000):
        moment = datetime(2020, 1, 1) + timedelta(minutes=rng.randrange(10_000_000))
        texts.extend([
            f"meeting on {moment:%Y-%m-%d} at {moment:%H:%M:%S}",
            f"scheduled={moment.isoformat(timespec='seconds')}",
            f"Order {rng.randrange(10**12, 10**19)} shipped",
            f"ts={rng.randrange(10**12, 10**13)}",
        ])
    for text in texts:
        assert redactable_personal_kinds(text) == (), text
        assert not contains_secret(text), text
        assert redact_secrets(text) == text, text
        assert consume_secret_intake(text, owner_local=True) is None, text


# Public payment-network test PANs: valid checksums with 13, 14, 15, 16 and 19
# digits and both supported grouping shapes. No live card data is used.
@pytest.mark.parametrize("number", [
    "4222 2222 2222 2",
    "3056 930902 5904",
    "3782 822463 10005",
    "4111 1111 1111 1111",
    "5555 5555 5555 4444",
    "6011 1111 1111 1117",
    "3530 1113 3330 0000",
    "4000 0000 0000 0000006",
])
@pytest.mark.parametrize("separator", [" ", "-"])
def test_real_card_shapes_remain_secret_beside_non_card_numbers(number, separator):
    raw = number.replace(" ", separator)
    # A nearby benign identifier must never exempt another value from detection.
    prefix = "approve calendar 17fc2553-5349-4882-9e61-c85ea35bea9f; "
    for value in (raw, f"({raw})", f'"{raw}"', f"visa: {raw}"):
        text = prefix + value
        assert contains_secret(text), text
        assert redactable_personal_kinds(text) == ("card_number",), text
        masked = redact_secrets(text)
        assert raw not in masked and masked.startswith(prefix), masked
        intake = consume_secret_intake(text, owner_local=True)
        assert intake is not None and "didn't save your card number" in intake.reply
        assert raw not in intake.remainder


@pytest.mark.parametrize("text,kind", [
    ("my credit card is 1234567812345678", "card_number"),
    ("my card number is 4111111111111111", "card_number"),
    ("my PIN is 4821", "pin"),
    ("my password is Qx7-plover-91", "password"),
])
def test_explicit_secret_labels_still_protect_their_values(text, kind):
    assert contains_secret(text)
    assert redactable_personal_kinds(text) == (kind,)
    assert redact_secrets(text) != text
    intake = consume_secret_intake(text, owner_local=True)
    assert intake is not None and "didn't save your" in intake.reply


@pytest.mark.parametrize("text,masked", [
    ("card4111111111111111", "card[redacted-card-number]"),
    ("visa_4111111111111111", "visa_[redacted-card-number]"),
    ("card 4111111111111111-exp 12/29", "card [redacted-card-number]-exp 12/29"),
    ("card 4111111111111111exp", "card [redacted-card-number]exp"),
    ("card 0x4111111111111111", "card 0x[redacted-card-number]"),
    ("card a4111111111111111g", "card a[redacted-card-number]g"),
    ("card a11111111111111", "card a[redacted-card-number]"),
])
def test_card_values_glued_to_non_hex_text_are_still_redacted(text, masked):
    assert contains_secret(text)
    assert redactable_personal_kinds(text) == ("card_number",)
    assert redact_secrets(text) == masked
    intake = consume_secret_intake(text, owner_local=True)
    assert intake is not None and "didn't save your card number" in intake.reply
    assert "4111111111111111" not in intake.remainder


@pytest.mark.parametrize("token", [
    "a111111111111111",  # exactly 16 alphanumeric hex characters, including a letter
    "a4111111111111111",
    "4111111111111111dead",
    "FACE4111111111111111BEEF",
    "17fc2553-5349-4882-9e61-c85ea35bea9f",
    "11111111-1111-4111-8111-111111111111",  # a UUID need not contain a letter
])
def test_only_whole_hex_tokens_exempt_their_own_numeric_fragments(token):
    for text in (token, f"card {token}"):
        assert redactable_personal_kinds(text) == ()
        assert not contains_secret(text)
        assert redact_secrets(text) == text
        assert consume_secret_intake(text, owner_local=True) is None
    mixed = f"card {token}; card4111111111111111"
    assert redact_secrets(mixed) == f"card {token}; card[redacted-card-number]"
