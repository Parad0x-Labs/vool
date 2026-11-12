"""A question about the world meets the web switch, never the saved-chat search or a withheld answer.

Red team round 3 (blue2 real-app run on the v0.8 chat line 524b7af7, 2026-11-12, Web lookups off):

* W3 "do i need a visa for a layover in doha" was answered "I don't have that saved, and I looked
  through your other chats and projects (3 chats)": the clean loop handed the world ask back and the
  recall lane claimed it before the web switch was read ("do I need" read as the person's own fact).
* W2 "can i legally use a photo from a news site on my blog" is held to a web lookup by the
  requirement authority, but the front door's reads did not see it: with Web lookups off the answer
  was withdrawn as "That is not mentioned in the records I have", and with them on it was withheld
  as "I didn't look anything up for this".

One reading decides both (`core.online_asks`): travel-entry rules are public facts that change, and
an ask the requirement authority holds to a web lookup is a web ask at every door before the model.
The wordings below are new, not the measured ones, except where a measured one is named.
"""
from __future__ import annotations

import types

import pytest

import core.agent_runtime.cross_chat_recall as recall
from core.online_asks import asks_for_online_facts, requirements_want_a_lookup

SID = "openclaw:" + "w" * 20

ENTRY_RULE_ASKS = (
    "do i need a visa for a layover in doha",                 # measured W3
    "do we need an esta for a stopover in new york",
    "is a visa required for brazilians going to canada",
    "which vaccinations do i need for thailand",
    "can i get a visa on arrival in nepal",
    "what are the entry requirements for vietnam",
    "do i need an international driving permit in japan",
    "do i need a passport to fly from london to dublin",
)

OWN_DOCUMENT_ASKS = (
    "find my passport scan",
    "where is the pdf of my visa",
    "when does my passport expire",
    "what's my passport number",
    "did i book the visa appointment",
    "remind me to apply for my visa tomorrow",
    "do i have a copy of my visa in downloads",
    "do i need to renew my passport soon according to my notes",
)


@pytest.mark.parametrize("asked", ENTRY_RULE_ASKS)
def test_a_travel_entry_rule_is_an_online_ask(asked):
    assert asks_for_online_facts(asked)
    assert requirements_want_a_lookup(asked)


@pytest.mark.parametrize("asked", OWN_DOCUMENT_ASKS)
def test_the_persons_own_document_is_not_an_online_ask(asked):
    assert not asks_for_online_facts(asked)


def test_an_ask_the_requirement_authority_holds_to_a_lookup_is_read_as_one():
    # W2 was held to a lookup by "news" naming its source; round 4 sets that reading aside (see
    # tests/test_round4_world_asks_read_alike_v08.py). This ask is held to one by the authority alone.
    asked = "any headlines on the rail strike?"
    assert not asks_for_online_facts(asked)
    from core.execution_requirements import requirements_for

    need = requirements_for(asked, source_context={})
    assert need.current_information_required and "web_search" in need.allowed_toolsets
    assert requirements_want_a_lookup(asked)


@pytest.mark.parametrize("asked", (
    "explain why a stack removes its newest item first",
    "what is my sisters name?",
    "is port 8080 open right now",
    "write release notes for this project",
))
def test_questions_the_web_does_not_answer_are_not_held_to_a_lookup(asked):
    assert not requirements_want_a_lookup(asked)


@pytest.fixture
def searched(monkeypatch):
    """The owner's own local chat with nothing saved; records every search of the other chats."""
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
    "do i need a visa for a layover in doha",
    "do i need a visa to go to morocco from ireland?",
    "any headlines on the rail strike?",
    "do we need a yellow fever jab for ghana?",
))
def test_the_recall_lane_leaves_a_world_ask_to_the_web_switch(searched, asked):
    assert recall.cross_chat_recall(asked, session_id=SID, source_context=_asked(asked)) is None
    assert searched == []


def test_the_recall_lane_still_looks_for_the_persons_own_fact(searched):
    asked = "where did i park the car?"
    found = recall.cross_chat_recall(asked, session_id=SID, source_context=_asked(asked))
    assert found is not None and not found.found
    assert searched
