"""A forget given in one chat removes a fact saved from another, by what the order names, from that chat's own stores.

Measured on the fix branch (2a3f9786, function level, 2026-11-18): with "my plumber is Mr Garside on Elm Row" saved
from one chat, "Forget who my plumber is." in a second chat removed nothing; and "Forget Dr Novak." removed the saved
row while the chat that saved it kept the sentence in its conversation log and summary. The owner's order (step 8 of
the memory task): forget removes a fact saved in ANY chat, matched by what it names, and no chat answers it after.

All names and places here are invented for this contract.
"""
from __future__ import annotations

import itertools

import pytest

from core import private_chat
from core.memory.entries import list_memory_entries
from core.memory.files import conversation_log_path, load_jsonl
from core.persistent_memory import maybe_handle_memory_command
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home

SAVED_IN = "the-chat-that-saved-it"
ASKED_IN = "the-chat-that-forgets-it"
THIRD = "a-third-chat"
GLAZIER = "my glazier is Mr Whitlock on Tanner Row"
_TURNS = itertools.count(1)


def _forget(order: str, chat: str = ASKED_IN) -> str:
    """One forget turn as the runtime runs it: the turn's forget admission is opened before the memory lane (while it
    is open the serving readers withhold every row) and retired after, and the reply that stands is the one the
    admission lets through. The first cut of this change passed with the lane called bare and named nothing on the
    real app."""
    from core.persistent_memory import _finish_forget_turn_admission, _preadmit_forget_turn
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    policy = _policy(chat)
    turn = f"turn-{next(_TURNS)}"
    context = {"_owner_local": True, "chat_id": chat, "_context_access_policy": policy}
    context[TURN_REQUEST_KEY] = TurnRequest.from_ingress(
        user_text=order, source_context=context, request_id=f"req:{turn}", turn_id=turn, session_id=chat)
    admitted, early = _preadmit_forget_turn(order, session_id=chat, source_context=context)
    assert admitted, order
    if early is not None:
        return early
    handled, reply = maybe_handle_memory_command(order, session_id=chat, access_policy=policy, source_context=context)
    assert handled, order
    result = _finish_forget_turn_admission({"response": reply}, user_text=order, session_id=chat, source_context=context)
    return result["response"]


def _facts(chat: str = THIRD) -> list[str]:
    return [str(row.get("text") or "") for row in list_memory_entries(access_policy=_policy(chat), limit=50)]


def _log_of(chat: str) -> str:
    return " ".join(str(row) for row in load_jsonl(conversation_log_path()) if str(row.get("session_id") or "") == chat)


def _save(fact: str, chat: str = SAVED_IN) -> None:
    assert _say(f"Remember that {fact}", chat)[1].startswith("Locked in")


@pytest.mark.parametrize("order", [
    "Forget my glazier.",                  # the fact's own words
    "Forget who my glazier is.",           # what it names
    "Forget Mr Whitlock.",                 # its value
])
def test_a_fact_saved_from_one_chat_is_forgotten_from_another_and_leaves_the_chat_that_saved_it(isolated_home, order):
    _save(GLAZIER)
    assert "Whitlock" in _log_of(SAVED_IN) and any("Whitlock" in fact for fact in _facts())
    reply = _forget(order)
    assert reply.startswith("Forget applied. Removed 1 memory entry."), reply
    assert not any("Whitlock" in fact for fact in _facts() + _facts(SAVED_IN) + _facts(ASKED_IN))
    assert "Whitlock" not in _log_of(SAVED_IN)


def test_the_words_of_an_order_that_described_its_fact_go_on_the_forget_list(isolated_home):
    # Else the order itself is read back later as something the owner "mentioned" about the fact.
    from core.memory import revocation
    from core.vool_memory import _normalize_forget_token

    _save(GLAZIER)
    assert _forget("Forget who my glazier is.").startswith("Forget applied. Removed 1 memory entry.")
    assert _normalize_forget_token("who my glazier is") in set(revocation.snapshot(ASKED_IN, isolated_home))
    assert _facts() == []


def test_a_place_asked_for_with_where_names_the_fact_that_holds_it(isolated_home):
    _save("the roof box is stored at my brother's unit")
    assert _forget("Forget where the roof box is stored.").startswith("Forget applied. Removed 1 memory entry.")
    assert _facts() == [] and "roof box" not in _log_of(SAVED_IN)


def test_one_order_that_names_two_facts_removes_both_and_keeps_the_third(isolated_home):
    _save("my archery locker is number 41")
    _save("the archery club fees are due in April")
    _save("the band uniforms go to the tailor in Ludlow", "another-chat-that-saved")
    reply = _forget("Forget my archery locker number and where the band uniforms go.")
    assert reply.startswith("Forget applied. Removed 2 memory entries."), reply
    assert _facts() == ["the archery club fees are due in April"]
    assert "Ludlow" not in _log_of("another-chat-that-saved") and "number 41" not in _log_of(SAVED_IN)
    assert "due in April" in _log_of(SAVED_IN)


def test_an_earlier_value_saved_from_a_third_chat_leaves_that_chat_too(isolated_home):
    _save("the trailer is parked at the allotment gate", "first-value-chat")
    handled, reply = _say("the trailer moved, it's parked behind the bowling green now", "second-value-chat")
    assert reply.startswith("Updated"), reply
    assert _forget("Forget where the trailer is parked.").startswith("Forget applied"), _facts()
    assert _facts() == []
    assert "allotment gate" not in _log_of("first-value-chat") and "bowling green" not in _log_of("second-value-chat")


def test_a_description_that_fits_two_facts_removes_neither(isolated_home):
    _save("my road bike has 22 gears")
    _save("my mountain bike has 11 gears")
    reply = _forget("Forget how many gears my bike has.")
    assert reply.startswith("Nothing was removed"), reply
    assert sorted(_facts()) == ["my mountain bike has 11 gears", "my road bike has 22 gears"]


def test_a_forget_of_something_never_saved_removes_nothing(isolated_home):
    _save(GLAZIER)
    reply = _forget("Forget my accordion's serial number.")
    assert reply.startswith("Nothing was removed"), reply
    assert _facts() == [GLAZIER] and "Whitlock" in _log_of(SAVED_IN)


def test_a_value_typed_bare_is_never_resolved_to_a_fact_that_only_describes_it(isolated_home):
    _save(GLAZIER)
    reply = _forget("Forget Whitlock Glazing.")  # a name the fact does not hold: erased by exactly the words typed
    assert reply.startswith("Nothing was removed"), reply
    assert _facts() == [GLAZIER]


@pytest.mark.parametrize("order", ["Forget my glazier.", "Forget who my glazier is.", "Forget Mr Whitlock."])
def test_a_private_chat_forgets_nothing_saved_from_another_chat(isolated_home, order):
    from core.persistent_memory import PRIVATE_CHAT_FORGET_REFUSED

    _save(GLAZIER)
    private = "a-private-chat"
    _policy(private)
    private_chat.set_private(private, True)
    assert _forget(order, private) == PRIVATE_CHAT_FORGET_REFUSED
    assert _facts() == [GLAZIER] and "Whitlock" in _log_of(SAVED_IN)


def test_a_fact_kept_for_one_chat_alone_is_not_resolved(isolated_home):
    # Its described forget withdraws the subject from that chat's readers and erases nothing: an existing contract
    # (tests/test_forget_subject_readers_r9.py), which the first cuts of this change broke.
    from core.memory.entries import add_memory_fact
    from core.memory.files import memory_entries_path

    assert add_memory_fact("My kiln shelf is number 6.", session_id=ASKED_IN, access_policy=_policy(ASKED_IN))
    reply = _forget("Forget where my kiln shelf is.")
    assert reply.startswith("Nothing was removed"), reply
    assert "kiln shelf is number 6" in memory_entries_path().read_text()


def test_copies_in_the_saving_chat_that_cannot_be_erased_are_said(isolated_home, monkeypatch):
    from core.memory import revocation

    _save(GLAZIER)
    real = revocation.begin_group

    def refuse_other_chats(chat, *args, **kwargs):
        if chat == SAVED_IN:
            raise RuntimeError("barrier unavailable")
        return real(chat, *args, **kwargs)

    monkeypatch.setattr(revocation, "begin_group", refuse_other_chats)
    reply = _forget("Forget my glazier.")
    assert reply.startswith("Forget could not be verified as fully applied: the copies in the chat that saved it"), reply
    assert "after 1 entry was removed" in reply and "Forget applied" not in reply


def _shown_in(chat: str, question: str, answer: str) -> None:
    from core.persistent_memory import append_conversation_event

    append_conversation_event(session_id=chat, user_input=question, assistant_output=answer, persist_now=True,
                              access_policy=_policy(chat))


def test_a_chat_the_fact_was_shown_in_lets_go_of_it_too(isolated_home):
    # Mac run16 (28ac44632): a recall in two other chats answered "You told me: <the fact>"; the forget erased the row
    # and the chats it was told and forgotten in, and those two recall replies kept the fact on disk.
    _save(GLAZIER)
    _shown_in(THIRD, "kas mano stiklius?", f"You told me: {GLAZIER}")
    _shown_in("a-fourth-chat", "who fixes my windows again", f"You told me:\n- {GLAZIER}\n- the shed is green")
    _shown_in("a-fifth-chat", "what colour is the shed", "You told me: the shed is green")
    assert _forget("Forget my glazier.").startswith("Forget applied. Removed 1 memory entry.")
    for chat in (SAVED_IN, THIRD, "a-fourth-chat"):
        assert "Whitlock" not in _log_of(chat), chat
    assert "shed is green" in _log_of("a-fifth-chat")


def test_a_chat_that_could_not_read_the_fact_is_left_alone(isolated_home):
    # Words that only look like an erased row's, in a chat the row was never readable from, are that chat's own.
    from core.memory.entries import add_memory_fact

    assert add_memory_fact("My kiln shelf is number 6.", session_id=SAVED_IN, access_policy=_policy(SAVED_IN))
    _shown_in(THIRD, "note", "My kiln shelf is number 6.")
    _forget("Forget my kiln shelf.", SAVED_IN)
    assert "kiln shelf is number 6" in _log_of(THIRD)


def test_a_chat_whose_summary_alone_holds_the_fact_lets_go_of_it(isolated_home):
    # Mac run17 (1bf694c5): the R1 chat's recall went to another lane, so its log never held the fact, and its summary
    # ("Relevant durable memory: <the fact>") kept it after the forget.
    from core.memory.files import append_jsonl, session_summaries_path

    _save(GLAZIER)
    _policy(THIRD)
    _shown_in(THIRD, "who fixes the windows", "Web lookups are off.")
    append_jsonl(session_summaries_path(), {"session_id": THIRD, "summary": f"Relevant durable memory: {GLAZIER}."})
    assert _forget("Forget my glazier.").startswith("Forget applied. Removed 1 memory entry.")
    summaries = " ".join(str(row) for row in load_jsonl(session_summaries_path()))
    assert "Whitlock" not in summaries, summaries


def test_a_forget_takes_the_reply_that_said_the_forgotten_statement_back(isolated_home):
    """Mac gate on 91e898675: after "and remember my gym bag is the black one" (kept nowhere, answered "I remember your
    gym bag is the black one."), "forget what i told you about my gym bag" erased the person's turns, the reply stayed
    in the chat's history, and "what colour is my gym bag?" was answered "Your gym bag is black."."""
    _policy(ASKED_IN)
    _shown_in(ASKED_IN, "and remember my gym bag is the black one", "I remember your gym bag is the black one.")
    from storage.dialogue_memory import recent_dialogue_turns

    assert "black" in " ".join(str(t) for t in recent_dialogue_turns(ASKED_IN, limit=20, speaker_roles=None))
    _shown_in(ASKED_IN, "what's the weather like", "Sunny.")
    reply = _forget("forget what i told you about my gym bag")
    assert reply.startswith("Forget applied"), reply
    from storage.dialogue_memory import recent_dialogue_turns

    assert "black" not in _log_of(ASKED_IN), _log_of(ASKED_IN)
    turns = " ".join(str(turn) for turn in recent_dialogue_turns(ASKED_IN, limit=20, speaker_roles=None))
    assert "black" not in turns, turns
    assert "Sunny" in _log_of(ASKED_IN)
