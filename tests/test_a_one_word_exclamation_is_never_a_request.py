"""A one-word exclamation before an ask ("morning!", "labas!", "¡hola!") is never a request of its own.

Fresh-question audit on 02ff11496: "morning! quick one, ..." answered the question and also listed "morning! quick
one" under "Could not be answered". A single word standing alone before "!" is a greeting or an interjection in any
language and script; it is recognised by that shape, not by a list of greetings. Greetings and asks are drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core.agent_runtime.answer_coverage import demand_units
from core.agent_runtime.filler_words import is_filler_fragment

GREETINGS = ("morning!", "labas!", "¡hola!", "hey!", "привет!", "servus!", "yo!", "こんにちは!")
LEADS = ("", " quick one,", " real quick,")
ASKS = ("how tall is the eiffel tower?", "kiek valandų turi para?", "what's 37*19?", "¿cuántos días tiene febrero?")


@pytest.mark.parametrize("seed", range(8))
def test_only_the_ask_is_a_demand(seed) -> None:
    rng = random.Random(f"exclaim-{seed}")
    ask = rng.choice(ASKS)
    message = f"{rng.choice(GREETINGS)}{rng.choice(LEADS)} {ask}"
    assert [unit.text for unit in demand_units(message)] == [ask], message


@pytest.mark.parametrize("fragment", ["call mom!", "help me!", "stop! what is the time", "hey! what is 2+2"])
def test_words_of_their_own_still_count(fragment) -> None:
    assert not is_filler_fragment(fragment)
