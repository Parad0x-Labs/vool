"""A bare correction fragment ("Too long.", "Less formal.") is about the answer just given, not a standing rule.

Saved, it reached every later chat as a line with no subject ("- Too long."): nothing in a new chat says what was too
long. A correction becomes standing only when it says what it is about or what to do instead ("Your replies are way
too long", "Too stiff. Loosen up a bit."). Authored wording only.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/tidewater"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text):
    return si.observe_turn(text, OWNER, principal="owner_local", session_id="openclaw:" + "c" * 20)


@pytest.mark.parametrize("text", [
    "Too long.",
    "Less formal.",
    "Way too wordy.",
    "Shorter.",
    "No, warmer.",
    "Too many emojis.",
    "ugh too stiff",
    "A bit more casual!",
])
def test_a_bare_fragment_saves_nothing(text):
    assert _say(text)["saved"] == []


@pytest.mark.parametrize("text", [
    "Too stiff. Loosen up a bit.",
    "Your replies are way too long, I just need the gist.",
    "That was way too formal, I'm not your boss.",
    "Use fewer emojis.",
])
def test_a_correction_that_says_what_it_is_about_is_still_saved(text):
    assert _say(text)["saved"] == [text]
