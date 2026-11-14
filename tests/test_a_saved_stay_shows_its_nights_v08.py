"""A saved day range reaches the model with the nights it spans.

Measured on the real local model (qwen3:8b, H2H memory set, 7f85d313): with "the cabin dates changed, its now 13-16
march" saved, "and how many nights are we staying?" was answered "We're staying for 4 nights, from 13 March to 16
March": the model counted days. The stored fact is not changed. All dates are invented.
"""
from __future__ import annotations

import pytest

from core.tiered_context_loader import _persistent_memory_items, _with_night_counts
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture


@pytest.mark.parametrize(
    ("text", "note"),
    [
        ("the cabin dates changed, its now 13-16 march", "13-16 march is 3 nights"),
        ("we stay at the Harrow Inn from 9 to 13 may", "from 9 to 13 may is 4 nights"),
        ("the boat is booked for june 4-7", "june 4-7 is 3 nights"),
        ("the 4th to 7th of june", "4th to 7th of june is 3 nights"),
    ],
)
def test_a_day_range_carries_its_nights(text, note):
    assert _with_night_counts(text) == f"{text} ({note})"


@pytest.mark.parametrize("text", ["my locker is 214", "standup at 9-10 every weekday", "the code is 13-16-22"])
def test_text_without_a_day_range_is_unchanged(text):
    assert _with_night_counts(text) == text


def test_the_model_is_shown_the_count_and_the_store_keeps_the_fact(isolated_home):
    assert _say("keep in mind that the cabin dates changed, its now 13-16 march", "seed")[1].startswith(
        ("Locked in", "Updated"))
    items = _persistent_memory_items("how many nights at the cabin?", [], session_id="ask", policy=_policy("ask"))
    assert any(item.content.endswith("(13-16 march is 3 nights)") for item in items), [i.content for i in items]
    from core.memory.entries import list_memory_entries

    assert any(str(row.get("text") or "") == "the cabin dates changed, its now 13-16 march"
               for row in list_memory_entries(access_policy=_policy("ask"), limit=20))
