"""A forget that names something the person said earlier in this chat forgets that sentence.

Measured on the real app (two cloud models): "My neighbour Odile told me her train to Riga leaves at 7:40 on Friday."
then "Forget what I told you about Odile's train." was answered "Nothing was removed. Forget found no memory in this
chat that matched "Odile's train". In this chat, the forgotten words now stay only in VOOL's forget list ..., so they
are never used again." The words "Odile's train" stand nowhere in the sentence in that order, so the sentence stayed in
everything the next turn read, and the next reply stated 7:40.

A forget erases by words. A target that describes a SAVED fact is already resolved to that fact's own words
(`_saved_facts_a_forget_names`). A target that names the person's own earlier sentence of the chat is now resolved the
same way, the forget erases by that sentence's words, and the reply the assistant gave to it at the time, which usually
says it back, is forgotten with it.

Pinned beside it: it fails closed. A target that names nothing, more than one sentence, only an assistant line or a
question resolves to nothing; words that already select a turn keep the keyword path; another sentence of the same
message is not forgotten; a forget of a saved fact works as before. All names and facts are invented here.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.evidence_kernel.revocation import without_revoked
from core.persistent_memory import (
    _own_chat_sentence_a_forget_names,
    _replies_to_own_chat_sentence,
    maybe_handle_memory_command,
)
from tests.test_everyday_remember_v08 import _say
from tests.test_keep_receipt_decides_save_claims_v08 import CHAT, _fresh  # _fresh is a fixture
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

__all__ = ["_fresh", "isolated_home"]

TRAIN = "My neighbour Odile told me her train to Riga leaves at 7:40 on Friday."
CAT = "Her cat is called Biscuit."
ECHO = "Nice — so that'd be tomorrow morning, Friday 20 November, at 7:40. Early start for Odile!"
OFFER = "Anything you'd like to do with that — set a reminder, or just noting it?"
ORDER = "Forget what I told you about Odile's train."


@pytest.fixture
def chat(isolated_home):
    from core.memory.revocation import initialize_memory_home

    initialize_memory_home(isolated_home)
    _policy(CHAT)
    return isolated_home


def _context(*turns, order=ORDER):
    history = []
    for user, assistant in turns:
        history.append({"role": "user", "content": user})
        if assistant:
            history.append({"role": "assistant", "content": assistant})
    history.append({"role": "user", "content": order})
    return {"_owner_local": True, "chat_id": CHAT, "surface": "api", "conversation_history": history}


def _names(target, context):
    return _own_chat_sentence_a_forget_names(target, session_id=CHAT, source_context=context)


# ------------------------------------------------------------ which sentence the target names


def test_the_target_names_the_persons_own_earlier_sentence(chat):
    assert _names("Odile's train", _context((f"{TRAIN} {CAT}", f"{ECHO}\n\n{OFFER}"))) == TRAIN


def test_a_target_that_names_nothing_said_resolves_to_nothing(chat):
    assert _names("my dentist", _context((TRAIN, ECHO))) is None


def test_more_than_one_named_sentence_is_no_target(chat):
    context = _context((TRAIN, ECHO), ("Odile says the train back from Riga is on Sunday.", "Noted."))
    assert _names("Odile's train", context) is None


def test_words_that_already_select_a_turn_keep_the_keyword_path(chat):
    assert _names("7:40", _context((TRAIN, ECHO))) is None


def test_a_subject_the_persons_own_words_hold_keeps_the_subject_withdrawal(chat):
    """"Forget the kiln locker code" over "My kiln locker code is 7428.": the chat's subject withdrawal owns it."""
    context = _context(("My kiln locker code is 7428.", "Noted."), order="Forget the kiln locker code, it is wrong.")
    assert _names("the kiln locker code", context) is None
    handled, reply = maybe_handle_memory_command(
        "Forget the kiln locker code, it is wrong.", session_id=CHAT, access_policy=_policy(CHAT), source_context=context)
    assert handled and str(reply).startswith("Nothing was removed"), reply


def test_a_line_only_the_assistant_said_is_never_the_target(chat):
    context = _context(("when is the next ferry?", "The Dublin ferry leaves at 08:55 from berth two."))
    assert _names("the Dublin ferry", context) is None


def test_a_question_is_never_the_target(chat):
    assert _names("Odile's train", _context(("When does Odile's train to Riga leave on Friday?", "I don't know."))) is None


def test_a_secret_target_resolves_to_nothing(chat):
    assert _names("my password hunter2-Xq9!", _context((TRAIN, ECHO))) is None


def test_no_chat_resolves_to_nothing():
    assert _own_chat_sentence_a_forget_names("Odile's train", session_id="", source_context=None) is None


def test_the_replies_to_the_sentence_are_read_line_by_line(chat):
    lines = _replies_to_own_chat_sentence(TRAIN, session_id=CHAT, source_context=_context((f"{TRAIN} {CAT}", f"{ECHO}\n\n{OFFER}")))
    assert lines == [ECHO, OFFER]


# ------------------------------------------------------------ the forget itself


def test_the_forget_erases_by_the_sentence_and_the_reply_that_said_it_back(chat):
    context = _context((f"{TRAIN} {CAT}", f"{ECHO}\n\n{OFFER}"))
    handled, reply = maybe_handle_memory_command(ORDER, session_id=CHAT, source_context=context)
    assert handled
    # The forget's own reply names the target as typed and never says the forgotten sentence back.
    assert "Odile's train" in str(reply)
    assert "7:40" not in str(reply) and "Riga" not in str(reply)
    revoked = cr.revoked_tokens_for_chat(CHAT)
    assert cr.text_carries_revoked_token(TRAIN, revoked)
    assert cr.text_carries_revoked_token(ECHO, revoked)
    # Another sentence of the same message is not forgotten.
    assert not cr.text_carries_revoked_token(CAT, revoked)
    # The readers of the forget list drop the line and keep the other.
    kept, _ = without_revoked(f"- user said: {TRAIN}\n- user said: {CAT}", [], CHAT)
    assert kept == f"- user said: {CAT}"


def test_a_forget_that_names_nothing_said_is_answered_as_before(chat):
    handled, reply = maybe_handle_memory_command("Forget what I told you about my dentist.", session_id=CHAT,
                                                 source_context=_context((TRAIN, ECHO), order="Forget what I told you about my dentist."))
    assert handled
    assert str(reply).startswith("Nothing was removed.")
    assert not cr.text_carries_revoked_token(TRAIN, cr.revoked_tokens_for_chat(CHAT))


def test_a_forget_of_a_saved_fact_works_as_before(chat):
    assert _say("Remember that my chimney sweep is Bryn Malpas", "seed")[1].startswith("Locked in")
    _policy(CHAT)
    handled, reply = maybe_handle_memory_command("forget my sweep.", session_id=CHAT,
                                                 source_context=_context((TRAIN, ECHO), order="forget my sweep."))
    assert handled
    assert str(reply).startswith("Forget applied. Removed 1 memory entry."), reply
    assert not cr.text_carries_revoked_token(TRAIN, cr.revoked_tokens_for_chat(CHAT))
