"""A reply that says a saved fact IS saved states what is true; it is not a report of a write nothing backs.

Measured on the real app (cloud route): asked "so who is my doctor?", the model answered "Your doctor is Dr Whitcombe —
that's saved in your memory." and the reply delivered was "I did not write anything to memory ..."; asked "where is my
shed?", it answered "It's the green one by the apple tree — that's how you've got it noted. The padlock code is 3186."
and the reply delivered was "The padlock code is 3186. Nothing from this message was saved to memory. ...". Both facts
were saved.

Whether a saved fact holds what such a sentence is about is read from the saved rows, on words, with no model call.
Pinned beside it: with no row that holds it, with another name than the saved one, or with a value the row does not
have, the sentence is a save claim and is corrected exactly as before. All names and facts are invented here.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.action_honesty_validator import _HONEST_NO_PERSISTENCE_RESPONSE, enforce_final_action_honesty
from core.memory import entries
from core.saved_fact_statement import a_saved_fact_holds_it
from tests.test_everyday_remember_v08 import _say
from tests.test_keep_receipt_decides_save_claims_v08 import CHAT, NOTHING_KEPT, _fresh, _gate  # _fresh is a fixture
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

__all__ = ["_fresh", "isolated_home"]

OWNER = {"_owner_local": True}
PLUMBER = "Your plumber is Mr Oswin — that's saved in your memory."
BIKE_SHED = "It's the grey one by the pear tree — that's how you've got it noted."


@pytest.fixture
def saved(isolated_home):
    assert _say("Remember that my plumber is Mr Oswin", "seed")[1].startswith("Locked in")
    assert _say("Remember that my bike shed is the grey one by the pear tree", "seed")[1].startswith("Locked in")
    assert _say("Remember that the bike shed padlock code is 2265", "seed")[1].startswith("Locked in")
    _policy(CHAT)
    return isolated_home


def _holds(sentence: str, before: str = "", chat: str = CHAT) -> bool:
    return a_saved_fact_holds_it(sentence, before, session_id=chat, source_context=dict(OWNER))


def _validated(reply: str, ask: str) -> dict:
    return enforce_final_action_honesty(
        {"response": reply, "confidence": 0.8}, user_input=ask, effective_input=ask, session_id=CHAT,
        source_context={**OWNER, "surface": "api", "allow_remote_fetch": False, "chat_id": CHAT},
    )


# ------------------------------------------------------------ whether a saved fact holds the sentence


@pytest.mark.parametrize(("sentence", "before"), [
    (PLUMBER, ""),
    (BIKE_SHED, ""),
    ("That's saved in your memory.", "Your plumber is Mr Oswin."),
    ("I've got that noted: the bike shed is the grey one by the pear tree.", ""),
    ("The padlock code for the bike shed is 2265 — that's what I have saved.", ""),
])
def test_a_saved_fact_holds_what_the_sentence_is_about(saved, sentence, before):
    assert _holds(sentence, before)


@pytest.mark.parametrize(("sentence", "before"), [
    ("Your plumber is Mr Osgood — that's saved in your memory.", ""),               # another name than the saved one
    ("I've saved your appointment with Mr Oswin on the 14th.", ""),                 # a value no row has
    ("Your dentist is Dr Hale — that's saved in your memory.", ""),                 # nothing saved about it
    ("The padlock code for the bike shed is 9917 — that's what I have saved.", ""), # another number
    ("That's saved in your memory.", ""),                                           # about nothing
    ("That's saved in your memory.", "Your dentist is Dr Hale."),
    ("It's the blue one by the plum tree — that's how you've got it noted.", ""),   # other things said of it
])
def test_no_saved_fact_holds_what_was_never_saved(saved, sentence, before):
    assert not _holds(sentence, before)


def test_with_nothing_saved_nothing_is_held(isolated_home):
    _policy(CHAT)
    assert not _holds(PLUMBER)


def test_an_unreadable_store_or_an_unknown_chat_holds_nothing(saved, monkeypatch):
    assert not _holds(PLUMBER, chat="")

    def unreadable(*_a, **_k):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(entries, "search_relevant_memory", unreadable)
    assert not _holds(PLUMBER)


# ------------------------------------------------------------ the first check (the validator)


def test_the_validator_delivers_a_true_statement_that_a_fact_is_saved(saved):
    reply = PLUMBER + " I don't have his number on file, but tell me and I can keep it for you."
    result = _validated(reply, "so who is my plumber?")
    assert result["response"] == reply, result.get("action_honesty_validator")
    assert "action_honesty_validator" not in result


@pytest.mark.parametrize("reply", [
    "Your plumber is Mr Osgood — that's saved in your memory.",
    "Your dentist is Dr Hale — that's saved in your memory.",
    "I've saved your appointment with Mr Oswin on the 14th to memory.",
])
def test_the_validator_still_replaces_a_statement_no_saved_fact_holds(saved, reply):
    result = _validated(reply, "so who is my plumber?")
    assert result["response"] != reply
    # "I've saved your appointment ..." is replaced as an action of the assistant's own, the two others as a write.
    assert result["action_honesty_validator"]["claim_kind"] in {"persistence", "mutation"}
    assert ("saved your appointment" in reply) == (result["response"] != _HONEST_NO_PERSISTENCE_RESPONSE)


def test_with_nothing_saved_the_validator_replaces_it_as_before(isolated_home):
    _policy(CHAT)
    assert _validated(PLUMBER, "so who is my plumber?")["response"] == _HONEST_NO_PERSISTENCE_RESPONSE


# ------------------------------------------------------------ the second check (the gate)


@pytest.mark.parametrize(("ask", "reply"), [
    ("where is my bike shed?", BIKE_SHED + " The padlock code is 2265."),
    ("so who is my plumber?", PLUMBER),
    ("who is my plumber, and is that saved?", "Your plumber is Mr Oswin. Yes, that's saved."),
    # the step's own before-run, word for word but for the fact: "Yes — it's noted: the fondue set is in the cellar. 🧀"
    # was delivered as "🧀 Nothing from this message was saved to memory. ..."
    ("where is my bike shed? have you got that noted?", "Yes — it's noted: my bike shed is the grey one by the pear tree. 🧀"),
    ("who is my plumber?", "Your plumber is Mr Oswin — that's what I have saved from your profile."),
])
def test_the_gate_delivers_a_true_statement_that_a_fact_is_saved(saved, ask, reply):
    published, record = _gate(reply, dict(NOTHING_KEPT), request=ask)
    assert published == reply, record


@pytest.mark.parametrize(("ask", "reply"), [
    ("so who is my plumber?", "Your plumber is Mr Osgood — that's saved in your memory."),
    ("where is my bike shed?", "It's the blue one by the plum tree — that's how you've got it noted."),
    ("my dentist is Dr Hale", "Got it. Your dentist is Dr Hale, and that is now saved in my memory."),
])
def test_the_gate_still_corrects_a_statement_no_saved_fact_holds(saved, ask, reply):
    published, record = _gate(reply, dict(NOTHING_KEPT), request=ask)
    assert published != reply, record
    assert record.get("memory_receipt") == "claims_without_receipt_corrected"
