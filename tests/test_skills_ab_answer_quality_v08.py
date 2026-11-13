"""Skills A/B on the v0.8 head (Mac, real model, loop on and off, 2026-11-13): answer-quality reds.

* "summarise my notes from this week" got "Web lookups are off": "this week" dates the person's own
  notes, it does not make them news.
* The saved-chat search answered "I don't have that saved, and I looked through your other chats" to
  "am I free to meet on Friday?", "am I free to quit my job without notice in Germany?", "what does
  this csv say about our sales" and, with the loop off, "why is my mac out of space".
* "check my notes for the plumber" reached the model unparsed, and the model picked save_note.
* With the loop off, "what does the lease pdf in my downloads say about notice" searched for a file
  named exactly "lease.pdf" and missed the lease on disk.
* The largest-items reply opened with the absolute home path.
* "is Sunday free parking in London?" was asked back "Did you mean London (city), Sunday (day of the week)?".

The wordings below are new, except the measured ones named.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

import core.agent_runtime.cross_chat_recall as recall
from core.online_asks import asks_for_online_facts, requirements_want_a_lookup
from core.operator.parser import parse_operator_action_intent

SID = "openclaw:" + "s" * 20


@pytest.mark.parametrize("asked", (
    "summarise my notes from this week",          # measured
    "what did i jot down in my notes this month",
    "go over my journal from this morning",
    "what's new in my inbox today",
))
def test_the_persons_own_records_dated_now_are_not_a_web_lookup(asked):
    assert not requirements_want_a_lookup(asked)


@pytest.mark.parametrize("asked", (
    "any headlines on the rail strike?",
    "do i need a visa for a layover in doha",
))
def test_world_asks_still_need_a_lookup(asked):
    assert requirements_want_a_lookup(asked)


@pytest.mark.parametrize(("asked", "topic"), (
    ("check my notes for the plumber", "plumber"),   # measured
    ("look through my notes about the boiler", "boiler"),
    ("do my notes mention the dentist", "dentist"),
    ("go through my meeting notes for action items", "action items"),
))
def test_checking_the_notes_is_a_notes_search(asked, topic):
    found = parse_operator_action_intent(asked)
    assert found is not None and found.kind == "find_notes", found
    assert found.target_label == topic


@pytest.mark.parametrize("asked", ("check the notes app settings", "make a note: buy oat milk"))
def test_other_note_asks_keep_their_reading(asked):
    found = parse_operator_action_intent(asked)
    assert found is None or found.kind != "find_notes", found


@pytest.fixture
def searched(monkeypatch):
    monkeypatch.setattr(recall, "_owner_local", lambda _ctx: True)
    monkeypatch.setattr(recall, "_private", lambda *_a: False)
    monkeypatch.setattr(recall, "_memory_rows", lambda *_a: ([], []))
    calls: list[str] = []

    def search(text, **_kwargs):
        calls.append(text)
        return types.SimpleNamespace(ok=True, hits=[], chats_searched=3, projects_searched=0)

    monkeypatch.setattr("core.chat_housekeeping.search_chats", search)
    return calls


def _asked(question: str) -> dict:
    return {"conversation_history": [{"role": "user", "content": question}]}


@pytest.mark.parametrize("asked", (
    "am I free to meet on Friday?",                                   # measured
    "am I free to quit my job without notice in Germany?",            # measured
    "what does this csv say about our sales",                        # measured
    "why is my mac out of space",                                     # measured
    "am i busy on thursday afternoon?",
    "am I allowed to sublet my flat in Berlin?",
    "what does this spreadsheet tell us about our costs",
    "why is my laptop running out of space",
    "how do i reset my router?",
))
def test_the_saved_chat_search_leaves_asks_that_are_not_saved_facts(searched, asked):
    assert recall.cross_chat_recall(asked, session_id=SID, source_context=_asked(asked)) is None
    assert searched == []


@pytest.mark.parametrize("asked", ("where did i park the car?", "what did i name the boat?"))
def test_the_saved_chat_search_still_looks_for_the_persons_own_fact(searched, asked):
    found = recall.cross_chat_recall(asked, session_id=SID, source_context=_asked(asked))
    assert found is not None and searched


def test_a_find_by_the_words_of_a_name_finds_the_file(tmp_path, monkeypatch):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "Flat_Lease_2026.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "Downloads" / "lease_notes.txt").write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from core.runtime_execution_tools import _machine_find_file

    found = _machine_find_file({"name": "lease.pdf"})
    assert [Path(p).name for p in found.details["matches"]] == ["Flat_Lease_2026.pdf"], found.response_text
    missing = _machine_find_file({"name": "tax.pdf"})
    assert missing.details["matches"] == []


@pytest.mark.parametrize("asked", (
    "is Sunday free parking in London?",          # measured
    "is parking free on sundays in edinburgh",
    "do i pay the congestion charge on weekends in london",
))
def test_parking_rules_of_a_place_are_an_online_ask(asked):
    assert asks_for_online_facts(asked)


@pytest.mark.parametrize("asked", ("where did i park the car?", "is parking free at my office", "how do i parallel park"))
def test_the_persons_own_parking_is_not(asked):
    assert not asks_for_online_facts(asked)


# Red/blue round 7 on f4f048f7 (Mac, 2026-11-13): M5 and M6 were answered by the saved-chat search.

@pytest.mark.parametrize("asked", (
    "what is the heaviest thing on my whole computer",   # measured M5
    "whats the biggest stuff on my mac",
    "which are the largest items on this laptop",
))
def test_the_biggest_things_on_this_computer_is_the_largest_files_ask(asked):
    from core.execution.constants import machine_largest_intent

    assert machine_largest_intent(asked) == "machine.find_largest"


def test_the_heaviest_thing_in_general_is_not_a_files_ask():
    from core.execution.constants import machine_largest_intent

    assert machine_largest_intent("what is the heaviest thing a horse can carry") is None


@pytest.mark.parametrize(("asked", "topic"), (
    ("do i have a note about the boiler service anywhere", "boiler service"),   # measured M6
    ("is there a note about my car insurance", "my car insurance"),
    ("did i write a note on the dentist?", "dentist"),
))
def test_asking_whether_a_note_exists_is_a_notes_search(searched, asked, topic):
    found = parse_operator_action_intent(asked)
    assert found is not None and found.kind == "find_notes", found
    assert recall.cross_chat_recall(asked, session_id=SID, source_context=_asked(asked)) is None
    assert searched == []
