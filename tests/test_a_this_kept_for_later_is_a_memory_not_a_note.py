""""Keep this for later: ..." with no notes named is the assistant's memory, not a note.

Measured on the installed app (Mac gate 15 on 06a3f89fc): a fact asked to be kept "in the back of your head for later"
went to the notes, and a later question about it found nothing held. The note reader took the filler "for later"
behind "this" for a destination, although a demonstrative names no note words of its own: only the notes store named
as the place makes it a note.

All names, places and dates here are invented for this contract. The keep judge is replaced in these tests.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter as arbiter
from core import persistent_memory
from core.operator.parser import parse_operator_action_intent
from tests.test_a_keep_ask_in_any_words_v08 import _facts, _judge, _say
from tests.test_memory_forget_resurrection_p0 import isolated_home


@pytest.fixture(autouse=True)
def _fresh_judgements():
    persistent_memory._JUDGED_LEADS.clear()
    yield
    persistent_memory._JUDGED_LEADS.clear()


@pytest.mark.parametrize("text", [
    "keep this for later: the boat club meets on the second Thursday",
    "Retain this for me: the boiler pressure should sit near 1.3",
    "stick that somewhere in your head for later: Ilse's flat is number 41",
    "put this in your memory for later - the vet is closed on Wednesdays",
    "drop this to myself: the shed padlock is 7210",
])
def test_a_demonstrative_with_only_a_filler_names_no_note(text):
    action = parse_operator_action_intent(text)
    assert action is None or action.kind != "save_note"


@pytest.mark.parametrize("text", [
    "stick this in my notes: renew the parking permit",
    "put that in my notes for later: oil the hinges",
    "add this to Apple Notes: tyre pressure 2.4 front",
    "keep this in my notebook: the attic hatch sticks",
    "keep a note for later: phone the glazier",
])
def test_a_note_or_the_notes_store_named_is_still_a_note(text):
    action = parse_operator_action_intent(text)
    assert action is not None and action.kind == "save_note"


def test_keep_this_for_later_is_kept_as_a_memory(isolated_home, monkeypatch):
    _judge(monkeypatch, "yes")
    handled, reply = _say("Keep this tucked away for later: the boat club meets on the second Thursday")
    assert handled and reply.startswith("Locked in")
    assert _facts() == ["the boat club meets on the second Thursday"]


def test_a_named_store_is_never_a_memory_row(isolated_home, monkeypatch):
    _judge(monkeypatch, "yes")
    handled, _ = _say("keep this in my notes: the attic hatch sticks")
    assert not handled and _facts() == []
    assert arbiter  # judge module stays importable for the stand-in


@pytest.mark.parametrize(("said", "kept"), [
    ("Store this for later: the boat club meets on the second Thursday", "the boat club meets on the second Thursday"),
    ("Remember this for my allotment: the leeks go in late May", "the leeks go in late May (my allotment)"),
    ("keep that in mind for later that the vet is closed on Wednesdays", "the vet is closed on Wednesdays"),
    ("Remember this is my locker code at the pool: 4471", "this is my locker code at the pool: 4471"),
])
def test_the_pointer_and_its_filler_are_the_ask_not_the_fact(isolated_home, monkeypatch, said, kept):
    _judge(monkeypatch, "yes")
    handled, reply = _say(said)
    assert handled and reply.startswith("Locked in")
    assert _facts() == [kept]
