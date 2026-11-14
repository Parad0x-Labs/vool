"""The clean loop keeps file asks and leaves to memory what a saved fact answers.

Measured on the real local model (qwen3:8b, fresh home, a fake owner home whose files share the asks' words):
"when is the rent due?" with "the rent is now due on the 1st" saved was searched on the disk and answered "The rent
receipts are in the blue folder": "rent" is one of the loop's topic words, and the saved-fact check stood down for
any ask a topic word admitted.
All names, places and dates are invented.
"""
from __future__ import annotations

import pytest

from core.clean_loop import route
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

OWNER = {"_owner_local": True}


def _decide(text: str, session: str, words: set[str]) -> route.RouteDecision:
    return route.decide(text, source_context=OWNER, session_id=session, resolve_brain=lambda: object(), env={},
                        file_words=lambda: words)


@pytest.mark.parametrize(
    "text",
    ["when is the rent due?", "who is my team lead?", "what day does Anselm water the plants?",
     "how much is my rent?"],
)
def test_a_topic_word_alone_does_not_point_at_files(text):
    assert not route.points_at_files(text)


@pytest.mark.parametrize(
    "text",
    ["find the quarterly report", "what's in the quarterly report?", "open the boat booking update file",
     "what does the parking permit renewal checklist say?", "where did I save the rent receipts?",
     "is the quarterly report pdf still on my desktop?", "where is the boat booking letter?"],
)
def test_an_ask_that_reads_finds_or_opens_points_at_files(text):
    assert route.points_at_files(text)


def test_a_saved_fact_takes_a_topic_word_question_from_the_files(isolated_home):
    assert _say("keep in mind that the rent is due on the 1st", "seed")[1].startswith("Locked in")
    _policy("rent-ask")
    decision = _decide("when is the rent due?", "rent-ask", {"rent", "receipts", "landlord"})
    assert not decision.admit and decision.reason == "saved_memory_answers", decision
    kept = _decide("where did I save the rent receipts?", "rent-ask", {"rent", "receipts", "landlord"})
    assert kept.reason != "saved_memory_answers", kept
