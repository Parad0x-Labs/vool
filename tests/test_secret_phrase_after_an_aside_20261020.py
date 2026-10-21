"""A labelled phrase secret is masked when an aside stands between its label and the colon (2026-10-20).

Measured on 7f7f0c7, on v0.7.2 (29945fd) and on v0.7.1 (baf115c) alike: "my mnemonic, which isn't public: <12
words>" and "my seed phrase, written on paper: <12 words>" kept every word in clear and contains_secret said False.
The labelled-phrase rule read a value only right after the label. The words here are wordlist words without a valid
BIP-39 checksum, so the unlabelled checksum rule does not catch them and only the label can.
"""
from __future__ import annotations

import pytest

from core.secret_redaction import contains_secret, redact_secrets

WORDS = "abandon ability able about above absent absorb abstract absurd abuse access accident"


@pytest.mark.parametrize("lead", [
    "my mnemonic, which isn't public:",
    "my mnemonic, which isn’t public:",
    "my mnemonic (not public):",
    "our mnemonic, it's not public =",
    "my seed phrase, written on paper:",
    "my recovery phrase — the handwritten copy:",
])
def test_the_words_after_an_aside_between_label_and_colon_are_masked(lead):
    text = f"{lead} {WORDS}"

    out = redact_secrets(text)

    assert "abandon" not in out and "accident" not in out, out
    assert out == f"{lead.rstrip(':= ')}: [redacted]", out
    assert contains_secret(text) is True


@pytest.mark.parametrize("text", [
    "my seed phrase, which is stored in the vault",
    "my recovery phrase, as I said, is safe with me",
    "My passphrase, the one for the router, is long. Then we talked about food and other things.",
])
def test_prose_after_a_phrase_label_with_an_aside_is_left_alone(text):
    assert redact_secrets(text) == text
    assert contains_secret(text) is False


def test_an_aside_never_reaches_past_the_end_of_its_sentence():
    # The colon belongs to the next sentence, not to the label.
    text = f"My recovery phrase is safe. Shopping list for today: {WORDS}"

    assert redact_secrets(text) == text
