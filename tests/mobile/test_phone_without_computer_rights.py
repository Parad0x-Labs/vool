"""A phone without "Use tools on my computer" asking about the owner's files is told so, never given a guess.

Real-model test 2026-10-28 (qwen3:8b): such a phone got "you have 2.3 gigabytes of storage remaining" and "No
photos were found for your Lisbon trip" (three were there) from the tools-less model.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.request_trust as rt
from core.agent_runtime.fast_paths_machine import maybe_answer_a_phone_without_computer_rights

AGENT = SimpleNamespace(_fast_path_result=lambda **kw: kw)


@pytest.fixture
def phone(monkeypatch):
    state = {"device": {"device_id": "phone:a"}, "computer": False}
    monkeypatch.setattr(rt, "remote_turn_device", lambda _sc: state["device"])
    monkeypatch.setattr(rt, "remote_turn_has_grant", lambda _sc, grant: grant == rt.REMOTE_GRANT_COMPUTER and state["computer"])
    return state


def _answer(text):
    found = maybe_answer_a_phone_without_computer_rights(AGENT, text, session_id="s", source_context={rt.REMOTE_DEVICE_KEY: "phone:a"})
    return found["response"] if found else None


# Listings and disk asks ("whats on my desktop", "how much room have i got left") already get this answer from
# the machine lanes' own grant check; these are the asks that reached the tools-less model.
@pytest.mark.parametrize("text", ["show me the photos from my lisbon trip",
                                  "cant find my boarding pass for the flight, can u look for it?",
                                  "im sure i saved a pic of the cat on the sofa at some point, find it?"])
def test_a_file_ask_from_a_phone_without_the_right_is_told_where_to_turn_it_on(phone, text):
    assert _answer(text) == rt.REMOTE_GRANT_REFUSAL_TEXT[rt.REMOTE_GRANT_COMPUTER]


def test_chat_and_phones_with_the_right_and_the_computer_itself_are_untouched(phone):
    assert _answer("whats an easy pasta dish that kids will actually eat?") is None
    phone["computer"] = True
    assert _answer("show me the photos from my lisbon trip") is None
    phone["computer"], phone["device"] = False, None
    assert _answer("show me the photos from my lisbon trip") is None
