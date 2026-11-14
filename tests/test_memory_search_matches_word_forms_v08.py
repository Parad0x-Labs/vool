"""Memory search matches a word in any of its forms ("seat"/"seats", "preference"/"prefer", "booked"/"booking").

Measured on the real local model (qwen3:8b, H2H memory set, fresh home): with "i always prefer window seats when i
fly" saved, "booking a flight later, any seat preference i told you about?" was answered "No, you didn't mention a seat
preference yet": the memory search compared exact tokens, found nothing to show the model, and on 88b3247e the clean
loop's model invented "an aisle seat" from a file instead. All names and places are invented.
"""
from __future__ import annotations

import pytest

from core.memory.entries import _match_stem, search_relevant_memory
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture


@pytest.mark.parametrize(
    ("a", "b"),
    [("seat", "seats"), ("preference", "prefer"), ("booked", "booking"), ("lives", "live"), ("dates", "date"),
     ("waters", "water")],
)
def test_word_forms_share_a_stem(a, b):
    assert _match_stem(a) == _match_stem(b)


@pytest.mark.parametrize(("a", "b"), [("seat", "sea"), ("cabin", "cab"), ("plate", "plan"), ("note", "not")])
def test_different_words_keep_apart(a, b):
    assert _match_stem(a) != _match_stem(b)


def test_a_question_in_other_word_forms_finds_the_saved_fact(isolated_home):
    assert _say("keep in mind that i always prefer window seats when i fly", "seed")[1].startswith("Locked in")
    assert _say("keep in mind that the bike lock code is 4471", "seed-2")[1].startswith("Locked in")
    found = [row["text"] for row in search_relevant_memory(
        "booking a flight later, any seat preference i told you about?", access_policy=_policy("ask"), limit=12)]
    assert "i always prefer window seats when i fly" in found
    assert "the bike lock code is 4471" not in found
