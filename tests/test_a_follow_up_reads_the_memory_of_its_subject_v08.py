"""A short follow-up searches memory with the ask before it, so the saved fact about its subject reaches the model.

Measured on the real local model (qwen3:8b, H2H memory set): after "if today is 1 march, how many days until the cabin
trip starts?", "and how many nights are we staying?" was answered "The details about the cabin trip and the number of
nights we're staying aren't in the information I have"; "the cabin dates changed, its now 13-16 march" was saved but
shares no word with the follow-up. All names and dates are invented.
"""
from __future__ import annotations

from core.memory.entries import search_relevant_memory
from core.tiered_context_loader import _follow_up_subject
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

FIRST = "if today is 1 march, how many days until the cabin trip starts?"
FOLLOW = "and how many nights are we staying?"


def test_the_previous_ask_is_the_follow_ups_subject():
    assert _follow_up_subject(FOLLOW, [FOLLOW, FIRST]) == [FIRST]
    assert _follow_up_subject(FOLLOW, [FIRST]) == [FIRST]


def test_a_question_that_stands_alone_takes_no_hint():
    assert _follow_up_subject("how many nights are we staying at the cabin?", [FIRST]) == []
    assert _follow_up_subject("and " + "very " * 15 + "long", [FIRST]) == []
    assert _follow_up_subject(FOLLOW, []) == []


def test_the_hint_finds_the_saved_fact(isolated_home):
    assert _say("keep in mind that the cabin dates changed, its now 13-16 march", "seed")[1].startswith(
        ("Locked in", "Updated"))
    policy = _policy("ask")
    alone = [row["text"] for row in search_relevant_memory(FOLLOW, access_policy=policy, limit=12)]
    hinted = [row["text"] for row in search_relevant_memory(
        FOLLOW, access_policy=policy, topic_hints=_follow_up_subject(FOLLOW, [FIRST]), limit=12)]
    assert not any("cabin" in text for text in alone)
    assert any("cabin" in text for text in hinted)
