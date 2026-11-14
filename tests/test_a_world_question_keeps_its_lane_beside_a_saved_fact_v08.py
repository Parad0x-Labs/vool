"""A saved fact takes a question from the web or live lanes only when the question is about the owner's own thing.

Independent review of the job 19 commits (C3): "every subject word is in a saved fact" let a saved row answer world and
live questions ahead of their lanes. "who is the president?" with "my brother is president of the chess club" saved
was answered "Here is what I have saved: ..."; "what's the weather in Lisbon this weekend?" quoted a saved trip; "what's
the gas price now?" quoted a stored reading as the current one. All names, places and figures are invented.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.cross_chat_recall import _saved_rows_answer_it, saved_memory_rows
from tests.test_a_saved_fact_answers_ahead_of_calendar_and_web_v08 import OWNER, _seed
from tests.test_memory_forget_resurrection_p0 import isolated_home  # isolated_home is a fixture


@pytest.mark.parametrize(
    ("saved", "asked"),
    [
        ("my brother is president of the chess club", "who is the president?"),
        ("we fly to Lisbon this weekend, hoping for good weather", "what's the weather in Lisbon this weekend?"),
        ("we fly to Lisbon on friday, hoping for good weather", "what's the weather in Lisbon?"),
    ],
)
def test_a_world_question_is_not_taken_by_a_saved_row(isolated_home, saved, asked):
    _seed(saved)
    assert not _saved_rows_answer_it(asked, "ask", OWNER)


def test_a_stored_reading_is_never_the_current_one(isolated_home):
    _seed("the gas price went up, its now 1.89 a litre at the corner station")
    assert not saved_memory_rows("what's the gas price now?", session_id="ask", source_context=OWNER)


@pytest.mark.parametrize(
    ("saved", "asked"),
    [
        ("our team standup is at 9:30 every weekday", "what time is our standup?"),
        ("we booked the boat for saturday, booking code KV-3381", "what's the booking code for the boat?"),
        ("my brother Tobiah lives in Quillon", "where does Tobiah live?"),
    ],
)
def test_the_owners_own_thing_still_answers(isolated_home, saved, asked):
    _seed(saved)
    assert _saved_rows_answer_it(asked, "ask", OWNER)
