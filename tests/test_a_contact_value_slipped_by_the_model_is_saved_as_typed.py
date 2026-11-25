"""A contact's email or phone the model typed with a slip is saved as the person typed it, never as the slip.

iMac gate on 64798c0fb (qwen3:8b): "add a contact: <name>, ieva.r@example.org" was called with "ivea.r@example.org";
the provenance guard kept the slip out, so the contact was saved with no email and the reply said the address was not
in the person's message. Values and slips drawn at random; a value with no near twin in the message stays unproven.
"""
from __future__ import annotations

import random

import pytest

from core.contacts import tools
from core.contacts.endpoints import KIND_EMAIL, KIND_PHONE, KIND_WALLET, normalize_endpoint

EMAILS = ("ieva.r@example.org", "jonas.k@example.com", "mila.p@example.net")
PHONES = ("+370 612 34567", "+44 20 7946 0958")


def _slip(rng: random.Random, value: str) -> str:
    pairs = [i for i in range(1, len(value) - 1) if value[i].isalnum() and value[i + 1].isalnum()
             and value[i] != value[i + 1]]
    i = rng.choice(pairs)
    chars = list(value)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def _pairs(kind: str, value: str):
    raw = {"kind": kind, "value": value}
    return [(raw, normalize_endpoint(raw))]


@pytest.mark.parametrize("seed", range(8))
def test_a_slipped_email_is_saved_as_the_person_typed_it(seed) -> None:
    rng = random.Random(f"em-{seed}")
    typed = rng.choice(EMAILS)
    slipped = _slip(rng, typed)
    text = f"add a contact: Someone, {typed}"
    ((_raw, endpoint),) = tools.as_the_user_typed_it(_pairs(KIND_EMAIL, slipped), text)
    assert endpoint.value.casefold() == typed.casefold() and tools.value_in_text(endpoint, text)


@pytest.mark.parametrize("seed", range(4))
def test_a_slipped_phone_is_saved_as_the_person_typed_it(seed) -> None:
    rng = random.Random(f"ph-{seed}")
    typed = rng.choice(PHONES)
    digits = list(typed.replace(" ", ""))
    k = rng.randrange(4, len(digits))
    digits[k] = str((int(digits[k]) + 1) % 10)
    text = f"save Rasa, her number is {typed}"
    ((_raw, endpoint),) = tools.as_the_user_typed_it(_pairs(KIND_PHONE, "".join(digits)), text)
    assert tools.value_in_text(endpoint, text)


def test_a_value_far_from_anything_typed_stays_unproven() -> None:
    text = "add a contact: Mila, mila.p@example.net"
    ((_raw, endpoint),) = tools.as_the_user_typed_it(_pairs(KIND_EMAIL, "someone.else@example.org"), text)
    assert endpoint.value == "someone.else@example.org" and not tools.value_in_text(endpoint, text)


def test_a_wallet_address_is_never_matched_to_a_near_one() -> None:
    typed = "0x52908400098527886E0F7030069857D2E4169EE7"
    slipped = "0x52908400098527886E0F7030069857D2E4169EE8"
    raw = {"kind": KIND_WALLET, "value": slipped, "network": "ethereum"}
    pairs = [(raw, normalize_endpoint(raw))]
    ((_raw, endpoint),) = tools.as_the_user_typed_it(pairs, f"save Ana's wallet {typed}")
    assert endpoint.value == slipped
