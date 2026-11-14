"""A saved fact answers a question about the owner's own thing ahead of a calendar or web lane.

Measured on the real local model (qwen3:8b, 88b3247e, H2H memory set, fresh home): "what time is our standup?" with
"standup went back to 9:30" saved was answered "No calendars are chosen for sync in Settings yet, so there is no agenda
to read"; "who's my dentist now and where are they?" with "my dentist moved. new one is dr Okafor on Brill street" saved
was answered "Web lookups are off". All names, places and dates are invented.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.cross_chat_recall import cross_chat_recall, saved_memory_rows
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

OWNER = {"_owner_local": True}


def _seed(*facts: str) -> None:
    for n, fact in enumerate(facts):
        assert _say(f"keep in mind that {fact}", f"seed-{n}")[1].startswith("Locked in"), fact
    _policy("ask")


@pytest.mark.parametrize(
    ("saved", "asked", "value"),
    [
        ("our team standup is at 9:30 every weekday", "what time is our standup?", "9:30"),
        ("my dentist moved. new one is dr Okafor on Brill street", "who's my dentist now and where are they?", "Okafor"),
        ("my dentist appointment is on 3 may at 14:00", "when is my dentist appointment?", "3 may"),
    ],
)
def test_the_saved_fact_is_the_answer(isolated_home, saved, asked, value):
    _seed(saved)
    found = cross_chat_recall(asked, session_id="ask", source_context=OWNER)
    assert found is not None and found.found, found
    assert value in found.response and found.response.startswith("Here is what I have saved:"), found.response


@pytest.mark.parametrize(
    ("saved", "asked"),
    [
        ("my dentist is dr Okafor on Brill street", "who's my dentist now?"),  # "now" and no change on record
        ("our team standup is at 9:30 every weekday", "is our standup still at 9:30 today?"),
        ("the rent is due on the 1st", "where did I save the rent receipts?"),
    ],
)
def test_no_saved_answer_when_it_asks_for_today_or_points_at_files(isolated_home, saved, asked):
    _seed(saved)
    assert not saved_memory_rows(asked, session_id="ask", source_context=OWNER)
