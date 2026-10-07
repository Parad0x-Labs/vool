"""Implicit corrections of the previous answer: a complaint about how it read, or a lasting fact plus "redo it".

Before this, only "No, use X" corrections were read, so "that was way too formal" or "I'm vegetarian, redo it"
changed nothing in the next chat. A complaint is saved only when it is about how answers come across (tone, length,
emojis, layout) and is not a one-off; a fact only when it is the owner's own lasting fact given as the reason to
redo. Authored wording only.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/quarry"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text):
    return si.observe_turn(text, OWNER, principal="owner_local", session_id="openclaw:" + "8" * 20)


def _texts():
    return [i.text for i in si.list_instructions("owner_local", si.workspace_key(OWNER))]


@pytest.mark.parametrize("text, saved", [
    ("That was way too formal, I'm not your boss.", ["That was way too formal, I'm not your boss."]),
    ("Too stiff. Loosen up a bit.", ["Too stiff. Loosen up a bit."]),
    ("Your replies are way too long, I just need the gist.", ["Your replies are way too long, I just need the gist."]),
    ("Ugh, that answer was full of emojis.", ["Ugh, that answer was full of emojis."]),
    ("You sound like a press release, tone it down.", ["You sound like a press release, tone it down."]),
])
def test_a_complaint_about_how_answers_come_across_is_saved(text, saved):
    assert _say(text)["saved"] == saved


@pytest.mark.parametrize("text, saved", [
    ("I'm vegetarian, redo it.", ["I'm vegetarian."]),
    ("I don't eat pork. Try again.", ["I don't eat pork."]),
    ("I'm allergic to peanuts, can you redo the list?", ["I'm allergic to peanuts."]),
    ("We keep kosher at home, please rewrite the menu.", ["We keep kosher at home."]),
    ("I'm lactose intolerant so swap the cream for something else.", ["I'm lactose intolerant."]),
])
def test_a_lasting_fact_given_as_the_reason_to_redo_is_saved(text, saved):
    assert _say(text)["saved"] == saved


def test_a_correction_replaces_only_its_own_setting():
    _say("Always keep the tone formal.")
    _say("Always use bullet points.")
    _say("That was way too formal, I'm not your boss.")
    assert _texts() == ["Always use bullet points.", "That was way too formal, I'm not your boss."]


# ------------------------------------------------------------------ over-learning: nothing is saved
@pytest.mark.parametrize("text", [
    "That answer was wrong, the capital of Australia is Canberra.",
    "That one was a bit long, but it's fine for this question.",
    "Too long this time.",
    "That was too formal for a birthday card, rewrite it.",
    "I'm tired, redo it tomorrow.",
    "My sister is vegetarian, redo it.",
    "Pretend I'm vegan and redo it.",
    "I'm vegetarian.",
    "That was great, thanks!",
    "The weather was too cold yesterday.",
])
def test_a_one_off_a_content_fix_or_someone_else_saves_nothing(text):
    assert _say(text)["saved"] == []
