"""Take-backs said the way people say them: "that got old, please stop", "no more X", "enough with X".

Before this, only "stop / forget / drop …" read as a take-back, so a rule the owner was tired of stayed active, and
"calling me <nickname> got old" left the saved name in the profile. Authored wording only.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si
from core.operator_profile_interpretation import interpret_profile_turn

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/harbor"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text):
    return si.observe_turn(text, OWNER, principal="owner_local", session_id="openclaw:" + "6" * 20)


def _texts():
    return [i.text for i in si.list_instructions("owner_local", si.workspace_key(OWNER))]


@pytest.mark.parametrize("rule, take_back", [
    ("Always end your reply with a one-line summary.", "The summary line at the end got old, please stop."),
    ("Always open with a short motivational quote.", "Those motivational quotes are getting old."),
    ("Always add an emoji to every reply.", "No more emojis, please."),
    ("Always use bullet points.", "Enough with the bullet points."),
    ("Always include a fun fact about the topic.", "I'm tired of the fun facts, you can drop them."),
    ("Always add a joke at the end.", "Lose the jokes."),
])
def test_a_rule_the_owner_is_tired_of_is_taken_back(rule, take_back):
    _say(rule)
    _say("Never use tables.")
    out = _say(take_back)
    assert out["taken_back"] == [rule]
    assert _texts() == ["Never use tables."]


def test_no_more_with_nothing_saved_is_a_new_instruction():
    assert _say("No more emojis in your replies.")["saved"] == ["No more emojis in your replies."]


@pytest.mark.parametrize("text", [
    "The weather got old fast this week.",
    "My old laptop is getting old.",
    "No more coffee for me after six, it keeps me awake.",
    "I'm tired of my commute.",
])
def test_talk_about_other_things_takes_nothing_back_and_saves_nothing(text):
    _say("Always use bullet points.")
    out = _say(text)
    assert out == {"saved": [], "taken_back": []}
    assert _texts() == ["Always use bullet points."]


@pytest.mark.parametrize("text", [
    "Calling me Skipper got old, please stop.",
    "Being called Skipper is getting old.",
    "No more nicknames, please.",
    "Enough with the nickname.",
    "Please drop the Skipper nickname.",
])
def test_dropping_a_nickname_forgets_the_saved_name(text):
    proposals = interpret_profile_turn(text)
    assert [(p.category, p.action) for p in proposals] == [("preferred_name", "forget")]


@pytest.mark.parametrize("text", [
    "My cat's nickname is Skipper.",
    "Call me Skipper.",
    "The name of the boat got old, we repainted it.",
])
def test_other_name_talk_forgets_no_name(text):
    assert not [p for p in interpret_profile_turn(text) if p.action == "forget"]
