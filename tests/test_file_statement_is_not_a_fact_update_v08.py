"""A statement about a file or folder never replaces an unrelated saved fact.

Review of 88b3247e (everyday remember on the real routing): the clean loop hands every update-shaped statement to the
memory lane before it reads files, and an update replaced the saved fact sharing the most topic words, one being
enough: with "the quarterly report is due on the 3rd" saved, "I moved the quarterly report pdf to the archive folder"
was answered "Updated. ... The old one is replaced." and the due date was gone. All names, places and dates are
invented.
"""
from __future__ import annotations

import pytest

from core.clean_loop import route
from core.persistent_memory import everyday_memory_turn
from tests.test_everyday_remember_v08 import _facts, _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture


def _seed(*facts: str) -> None:
    for n, fact in enumerate(facts):
        handled, reply = _say(f"keep in mind that {fact}", f"seed-{n}")
        assert handled and reply.startswith("Locked in"), (fact, reply)
    for chat in ("ask-new", "say-new"):
        _policy(chat)  # a new chat reads the owner's saved facts, as a served chat does


@pytest.mark.parametrize(
    ("saved", "said"),
    [
        ("the quarterly report is due on the 3rd", "I moved the quarterly report pdf to the archive folder"),
        ("my tax advisor is Ottilie Brand", "the tax folder is now on the desktop"),
        ("we booked the boat on lake Orrin for 4-7 june, booking code BR-5527", "the boat booking letter moved to downloads"),
        ("the garden party is on 9 july", "the garden party photos are now in garden_party.zip"),
    ],
)
def test_a_file_statement_does_not_replace_an_unrelated_fact(isolated_home, saved, said):
    _seed(saved)
    assert not everyday_memory_turn(said, session_id="say-new", source_context={})
    assert not route._memory_lane_owns(said, "say-new", {})
    handled, reply = _say(said, "say-new")
    assert not reply.startswith("Updated"), reply
    assert saved in _facts("say-new")


def test_a_file_fact_is_still_updated_by_a_file_statement(isolated_home):
    _seed("the tax papers folder is in my documents folder")
    said = "small update: the tax papers folder moved to the desktop"
    assert everyday_memory_turn(said, session_id="say-new", source_context={})
    handled, reply = _say(said, "say-new")
    assert handled and reply.startswith("Updated"), reply


def test_an_ordinary_update_still_replaces_its_fact(isolated_home):
    _seed("our planning call moved to 10:15 every monday")
    said = "and the planning call went back to 10:00"
    assert everyday_memory_turn(said, session_id="say-new", source_context={})
    handled, reply = _say(said, "say-new")
    assert handled and reply.startswith("Updated"), reply
    assert "our planning call moved to 10:15 every monday" not in _facts("say-new")
