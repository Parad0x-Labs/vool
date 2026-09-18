""""Remember when ...?" is recalling something together, not an instruction to store it.

Instance eleven of the defect this repo keeps finding. Measured live 2026-07-30 on 0b74d3a:
"Remember when we said we would never use jQuery again?" was answered "Locked in. I'll remember
that." in 0.2s, and the question itself was written into the operator's memory as a fact.

The verb is identical in both readings; the MOOD is not. "Remember X" is an imperative naming a
proposition. A reminiscence is a question -- it opens with an interrogative after the verb, or is
asked as one -- and a question hands over nothing storable. So the test is what the sentence IS,
not which words it contains.
"""
from __future__ import annotations

import pytest

from core.persistent_memory import (
    _ANSWER_REQUEST_RE,
    _REMEMBER_RE,
    _is_a_reminiscence,
)


def _would_store(text: str) -> bool:
    """The exact condition guarding add_memory_fact in maybe_handle_memory_command."""
    match = _REMEMBER_RE.match(text)
    return bool(
        match
        and not _ANSWER_REQUEST_RE.search(text)
        and not _is_a_reminiscence(match.group(1), text)
    )


REMINISCENCES = (
    "Remember when we said we would never use jQuery again?",
    "Remember how hard it was to set up the VPN?",
    "Remember that time we shipped on a Friday?",
    "remember when the office flooded",
    "Remember why we dropped the old vendor?",
    "note when we last did this?",
    "Remember where we put the spare keys?",
    "remember who signed off on the last release?",
    "Remember whether we ever paid that invoice?",
    "Remember how the old build script used to work?",
)

REAL_MEMORY_INSTRUCTIONS = (
    "remember that my birthday is in March",
    "remember my api key lives in 1password",
    "remember I prefer tabs over spaces",
    "remember to buy milk on the way home",
    "note that the client prefers email",
    "store this: the staging url is stg.example.com",
    "remember the deploy window is Tuesday 09:00",
    "remember that Rasa owns the billing integration",
    "note the office wifi password is on the fridge",
    "remember I am based in Vilnius, not Kaunas",
)


@pytest.mark.parametrize("text", REMINISCENCES)
def test_a_reminiscence_is_not_written_into_memory(text: str) -> None:
    assert _would_store(text) is False, text


@pytest.mark.parametrize("text", REAL_MEMORY_INSTRUCTIONS)
def test_a_real_memory_instruction_is_still_stored(text: str) -> None:
    assert _would_store(text) is True, text
