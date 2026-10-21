"""An apostrophe reads the same however a keyboard types it, in the secret filter and in a forget (2026-10-20).

Measured at 1033851, on v0.7.2 (29945fd) and on v0.7.1 (baf115c) alike:

- "our isn´t-public mnemonic: <twelve words>" kept the words in clear (contains_secret False) with an acute accent,
  a prime or a reversed apostrophe: the secret filter's negation read a smaller apostrophe set than memory admission.
- "Forget what I’ve told you about my locker code." and "Forget my locker code 5906, it’s old." answered "Nothing
  was removed" and the memory stayed; with a straight apostrophe both remove it.
"""
from __future__ import annotations

import pytest

from core.secret_redaction import contains_secret, redact_secrets
from tests.test_forget_counts_only_what_was_forgotten_20261020 import (
    BYSTANDER,
    CODE,
    _hash_backend,  # the deterministic embedding backend fixture
    _live_entries,
    _remember,
    command,
    isolated_pages,  # the runtime home fixture
)

WORDS = "abandon ability able about above absent absorb abstract absurd abuse access accident"
# Straight, curly left/right, reversed, modifier letter, prime, acute accent, fullwidth.
MARKS = pytest.mark.parametrize("mark", ["'", "\u2018", "\u2019", "\u201b", "\u02bc", "\u2032", "\u00b4", "\uff07"],
                                ids=lambda mark: f"U+{ord(mark):04X}")


@MARKS
def test_a_not_public_mnemonic_is_masked_whatever_apostrophe_the_negation_uses(mark):
    text = f"our isn{mark}t-public mnemonic: {WORDS}"

    assert "abandon ability" not in redact_secrets(text)
    assert contains_secret(text) is True


@MARKS
def test_a_public_mnemonic_with_a_possessive_stays_as_written(mark):
    text = f"Remember our band{mark}s public music mnemonic: every good bird dances softly."

    assert redact_secrets(text) == text


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("command_text", [
    "Forget what I’ve told you about my locker code.",
    f"Forget my locker code {CODE}, it’s old.",
    f"Forget my locker code {CODE}, that’s old.",
    "Forget what I´ve told you about my locker code.",
], ids=["told-you-curly", "its-old-curly", "thats-old-curly", "told-you-acute"])
def test_a_phone_apostrophe_in_the_forgets_wording_still_names_the_memory(isolated_pages, command_text):
    chat = "forget-apostrophe-" + "".join(char for char in command_text.casefold() if char.isalnum())[:40]
    _remember(isolated_pages, chat, f"my locker code is {CODE}.")
    _remember(isolated_pages, chat, f"my harbor lantern is painted {BYSTANDER}.")

    handled, reply = command(isolated_pages, chat, command_text)

    assert handled and reply == "Forget applied. Removed 1 memory entry.", reply
    assert _live_entries() == [f"my harbor lantern is painted {BYSTANDER}."]
