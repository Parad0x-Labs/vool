"""A count of the reply just given is computed from the reply, never estimated by a model.

0.7.8 release sweep (2026-11-12, block 1c): "how many words is that sentence?" got 12 for a
15-word sentence. Contributor: sls_0x.
"""
from __future__ import annotations

import pytest

import core.agent_runtime.previous_reply_count as lane

REPLY = "Rain drummed softly on the tin roof while the old dog slept beside the warm stove."


@pytest.fixture(autouse=True)
def _reply(monkeypatch):
    monkeypatch.setattr(lane, "_previous_reply", lambda _sid: REPLY)


@pytest.mark.parametrize(
    "question", ["how many words is that sentence?", "count the words in what you just wrote",
                 "How many words does your answer have?", "how many words are in that reply"],
)
def test_word_counts_are_exact(question):
    assert lane.previous_reply_count(question, session_id="s") == "My last reply has 16 words."


def test_first_and_last_word():
    assert "“Rain”" in lane.previous_reply_count("what's the first word of your sentence?", session_id="s")
    assert "“stove”" in lane.previous_reply_count("what is the last word of that reply", session_id="s")


def test_sentence_count():
    assert lane.previous_reply_count("how many sentences are in your answer?", session_id="s") == "My last reply has 1 sentence."


@pytest.mark.parametrize("question", ["how many words is a haiku?", "how many words should a cover letter have?",
                                      "count the words in this paragraph: one two three"])
def test_questions_about_other_text_are_not_this_lanes(question):
    assert lane.previous_reply_count(question, session_id="s") is None


def test_no_previous_reply_means_the_lane_stands_aside(monkeypatch):
    monkeypatch.setattr(lane, "_previous_reply", lambda _sid: "")
    assert lane.previous_reply_count("how many words is that sentence?", session_id="s") is None


@pytest.mark.parametrize(
    "reply,question,answer",
    [("58 × 9 = 522.", "plus 30?", "522 + 30 = 552"),
     ("58 × 9 = 522.", "now take away a hundred from the result", "522 − 100 = 422"),
     ("58 × 9 = 522.", "now subtract a hundred from the result", "522 − 100 = 422"),
     ("The total comes to 240 euros.", "divided by three", "240 ÷ 3 = 80"),
     ("Orders 2, 6 and 9 were cancelled.", "plus 10?", None)],
)
def test_arithmetic_continues_from_the_stated_result(monkeypatch, reply, question, answer):
    monkeypatch.setattr(lane, "_previous_reply", lambda _sid: reply)
    assert lane.previous_result_arithmetic(question, session_id="s") == answer
