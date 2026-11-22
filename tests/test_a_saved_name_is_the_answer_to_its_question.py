"""A question that asks for a name a saved fact gives is answered with that fact, not handed to the model to read.

Independent real-app run on 61ab41c6 (local qwen3:8b, fresh home, 2026-11-22): with "my grandmother's cockatoo is
named Pistachio" and "my kayak is called Driftwood Dan" saved in one chat, "whats the name of my grandmother's
cockatoo" (after a restart) was answered "The records do not state the name of your grandmother's cockatoo", and "my
kayak, what's its name?" was answered "I don't have any record of a kayak". The saved fact was in the model's context
both times. The wording is built from parts at random here, so no fixed sentence is what passes.

All names and things here are invented for this contract.
"""
from __future__ import annotations

import random

import pytest

from core.agent_runtime.cross_chat_recall import cross_chat_recall
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

OWNER = {"_owner_local": True}

THINGS = ["sailboat", "grandmother's cockatoo", "neighbour's tortoise", "old campervan", "sourdough starter",
          "cousin's iguana", "electric bike"]
NAMES = ["Marmalade Jones", "Pistachio", "Captain Wobble", "Fennimore", "Ziggy Stardrop"]
TELLS = ["remember that my {thing} is named {name}", "keep in mind that my {thing} is called {name}",
         "remember that my {thing} goes by {name}", "remember that my {thing} is called {name}"]
ASKS = ["whats the name of my {thing}", "what's the name of my {thing}?", "my {thing}, what's its name?",
        "what is my {thing} called?", "what did I end up calling my {thing}?", "which name did I give my {thing}?",
        "what was my {thing} called again", "the name of my {thing}?", "what's my {thing}'s name again?",
        "and my {thing}, what was its name"]


@pytest.mark.parametrize("seed", range(6))
def test_a_name_question_is_answered_with_the_saved_name(isolated_home, seed):
    rng = random.Random(f"saved-name-{seed}")
    thing, name = rng.choice(THINGS), rng.choice(NAMES)
    told = rng.choice(TELLS).format(thing=thing, name=name)
    assert _say(told, "told")[1].startswith("Locked in"), told
    _policy("ask")
    for asked in rng.sample(ASKS, 4):
        asked = asked.format(thing=thing)
        found = cross_chat_recall(asked, session_id="ask", source_context=OWNER)
        assert found is not None and found.found, (told, asked, found)
        assert name in found.response and found.response.startswith("Here is what I have saved:"), (asked, found)


@pytest.mark.parametrize(
    "asked",
    [
        "what colour is my sailboat?",   # asks something the fact does not give
        "where do I keep my sailboat",
    ],
)
def test_a_question_that_asks_no_name_is_not_answered_with_the_name(isolated_home, asked):
    assert _say("remember that my sailboat is called Captain Wobble", "told")[1].startswith("Locked in")
    _policy("ask")
    found = cross_chat_recall(asked, session_id="ask", source_context=OWNER)
    assert found is None or not found.response.startswith("Here is what I have saved:\n- my sailboat is called"), found


def test_a_part_of_a_name_the_fact_does_not_give_keeps_its_lane(isolated_home):
    assert _say("remember that my manager is Pieter", "told")[1].startswith("Locked in")
    _policy("ask")
    found = cross_chat_recall("whats Pieter's last name?", session_id="ask", source_context=OWNER)
    assert found is None or found.response != "Here is what I have saved:\n- my manager is Pieter", found


def test_a_fact_that_names_nothing_is_still_handed_to_the_model(isolated_home):
    # "my dentist is dr Okafor" gives who the dentist is, not a name the dentist goes by: the model answers in words.
    assert _say("remember that my dentist is dr Okafor on Brill street", "told")[1].startswith("Locked in")
    _policy("ask")
    found = cross_chat_recall("what's the name of my dentist?", session_id="ask", source_context=OWNER)
    assert found is None or found.found, found
