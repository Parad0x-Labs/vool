"""A question that points back at what the person just said ("how much is the second one?") is not a web lookup.

Measured on the real local model (qwen3:8b, held-out set, Web lookups off): after "i'm comparing two flats: one on
Birch Row for 950 a month and one on Kell Lane for 1100 a month", "how much is the second one?" was answered "Web
lookups are off, so I can't look this up online." All names, places and figures are invented.
"""
from __future__ import annotations

import pytest

from core.online_asks import asks_about_a_chat_referent

FLATS = "i'm comparing two flats: one on Birch Row for 950 a month and one on Kell Lane for 1100 a month"


def _history(*user_turns: str) -> dict:
    turns = []
    for said in user_turns:
        turns += [{"role": "user", "content": said}, {"role": "assistant", "content": "ok"}]
    return {"conversation_history": turns}


@pytest.mark.parametrize("asked", ["how much is the second one?", "and the other one?", "how much was the cheaper one",
                                   "what does that one cost?"])
def test_a_pointer_back_at_figures_just_given_is_the_chats(asked):
    context = _history(FLATS)
    context["conversation_history"].append({"role": "user", "content": asked})
    assert asks_about_a_chat_referent(asked, context)


@pytest.mark.parametrize(
    ("earlier", "asked"),
    [
        ("i'm thinking about flats in Birch Row", "how much is the second one?"),  # no figure was given
        (FLATS, "how much is a flat in Kell Lane these days?"),  # no pointer back
        ("", "how much is the second one?"),  # nothing said before
    ],
)
def test_otherwise_the_lookup_reading_stands(earlier, asked):
    context = _history(earlier) if earlier else {"conversation_history": []}
    assert not asks_about_a_chat_referent(asked, context)
