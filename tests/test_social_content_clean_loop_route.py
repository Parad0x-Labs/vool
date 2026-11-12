"""The clean local loop must not take a content-planning ask away from the social content manager.

Found on the real chat page (2026-11-12): "Plan a launch campaign for our privacy tool across X and
LinkedIn" was admitted to the clean loop as a privacy-meter ask (the word "privacy"), and the next
short ask was admitted as its follow-up. The clean loop's brain carries no skill guidance, so the
social content manager never reached the model. An ask that names the owner's files stays with the
clean loop, and the loop's own asks are unchanged.
"""
from __future__ import annotations

import pytest

from core.clean_loop.route import FLAG_ENV, decide, note_clean_turn, skill_owns_turn

OWNER = {"surface": "cli"}


def _decide(text: str, session_id: str = "s-social-route"):
    return decide(text, source_context=OWNER, session_id=session_id,
                  resolve_brain=lambda: object(), env={FLAG_ENV: "1"})


@pytest.mark.parametrize("text", [
    "Plan a launch campaign for our privacy tool across X and LinkedIn",
    "Help me manage our social content for the next month",
    "content calendar for bluesky n mastodon next 2 weeks??",
    "Coordinate the posting plan across all our private beta channels",
])
def test_content_planning_asks_leave_the_clean_loop(text: str) -> None:
    decision = _decide(text)
    assert not decision.admit
    assert decision.reason == "skill_owns_turn"


def test_a_planning_ask_is_not_a_follow_up_of_a_clean_turn() -> None:
    session = "s-follow-up-social"
    note_clean_turn(session)
    decision = _decide("content calendar for the launch??", session)
    assert not decision.admit and decision.reason == "skill_owns_turn"


@pytest.mark.parametrize("text", [
    "what's in the content calendar file in my Documents?",
    "is the content calendar on my laptop or in Downloads",
])
def test_an_ask_that_names_the_owners_files_stays_with_the_clean_loop(text: str) -> None:
    assert not skill_owns_turn(text)
    assert _decide(text).reason != "skill_owns_turn"


@pytest.mark.parametrize("text", [
    "how much of my stuff went to online AI today",
    "find the plumber invoice",
    "what did I save about privacy settings",
])
def test_the_clean_loops_own_asks_are_unchanged(text: str) -> None:
    assert not skill_owns_turn(text)
    assert _decide(text).admit
