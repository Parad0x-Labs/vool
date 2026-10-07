"""A new standing instruction on a topic replaces the old one on that topic, and only that one.

Before this, "keep answers to two sentences from now on" was saved beside an earlier "always give me long,
detailed answers": two opposite instructions on one topic, both handed to every new chat. Authored wording only.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/harbor"}
OTHER_WORKSPACE = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/meadow"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text, ctx=OWNER):
    return si.observe_turn(text, ctx, principal="owner_local", session_id="openclaw:" + "5" * 20)


def _texts(ctx=OWNER):
    return [i.text for i in si.list_instructions("owner_local", si.workspace_key(ctx))]


@pytest.mark.parametrize("old, new", [
    ("Always give me long, detailed answers.", "From now on keep your answers to two or three sentences."),
    ("Always reply in German.", "From now on, reply in English."),
    ("Always give me temperatures in Fahrenheit.", "Going forward, show temperatures in Celsius."),
    ("By default, write dates as DD/MM/YYYY.", "From now on write dates in ISO format."),
    ("Always keep the tone formal.", "From now on keep the tone casual with me."),
])
def test_a_new_instruction_on_the_same_topic_replaces_the_old_one(old, new):
    _say(old)
    out = _say(new)
    assert out["saved"] == [new]
    assert _texts() == [new]


def test_a_take_back_and_a_new_rule_in_one_sentence_leave_only_the_new_rule():
    _say("Always add a fun emoji at the end of your replies.")
    _say("Never use tables.")
    out = _say("Drop the emoji at the end, and from now on end with a one-line summary instead.")
    assert out["taken_back"] == ["Always add a fun emoji at the end of your replies."]
    # The instruction half is what is kept: the take-back words would make it removable by "emoji" later.
    assert _texts() == ["Never use tables.", "From now on end with a one-line summary instead."]


def test_an_instruction_on_another_topic_is_untouched():
    _say("Always give me long, detailed answers.")
    _say("Always give me distances in kilometres.")
    _say("Never use emojis in your answers.")
    _say("From now on keep your answers short.")
    assert _texts() == ["Always give me distances in kilometres.", "Never use emojis in your answers.",
                        "From now on keep your answers short."]


def test_the_replacement_stays_in_its_own_workspace():
    _say("Always reply in German.", ctx=OTHER_WORKSPACE)
    _say("Always reply in German.")
    _say("From now on, reply in English.")
    assert _texts(OTHER_WORKSPACE) == ["Always reply in German."]
    assert _texts() == ["From now on, reply in English."]


def test_two_units_on_different_quantities_both_stay():
    _say("Always give me distances in kilometres.")
    _say("Always give me temperatures in Celsius.")
    assert len(_texts()) == 2
