"""A forget order is carried out when other words stand in front of it.

Measured on the real app with the facts saved (two cloud models and the local model): "you can forget my work parking
bay." was answered by the model ("I won't reference it again", "I haven't actually deleted the saved note yet"), on the
local model the sentence was itself saved as a memory, and 131 was served after it; "could you forget my library card
number?" was answered by the model, corrected for claiming a memory write, and 40721 was served after it. The command
readers take an order only at the very start of the message.

A lead-in the readers themselves list ("could you", "please") makes the order theirs with no judgement. Any other
front is put to one balanced yes/no judgement (`core.intent_arbiter.arbitrate_forget_ask`), as an unlisted "remember"
wording is. The order is then carried out by the forget command as any other.

Pinned beside it: no judge, a no, a front about the person's own forgetting, a private chat and a turn that is not the
owner's erase nothing; a secret is never sent to the judge. All names and facts are invented here.
"""
from __future__ import annotations

import pytest

import core.intent_arbiter as intent_arbiter
import core.persistent_memory as pm
from core.persistent_memory import _forget_order_in_other_words, everyday_memory_turn, maybe_handle_memory_command
from tests.test_everyday_remember_v08 import _say
from tests.test_keep_receipt_decides_save_claims_v08 import CHAT, _fresh  # _fresh is a fixture
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

__all__ = ["_fresh", "isolated_home"]

OWNER = {"_owner_local": True, "chat_id": CHAT, "surface": "api"}


@pytest.fixture
def saved(isolated_home):
    from core.memory.revocation import initialize_memory_home

    initialize_memory_home(isolated_home)
    assert _say("Remember that my parking bay at work is number 131", "seed")[1].startswith("Locked in")
    assert _say("Remember that my library card number is 40721", "seed")[1].startswith("Locked in")
    _policy(CHAT)
    pm._JUDGED_FORGET_ASKS.clear()
    return isolated_home


@pytest.fixture
def judge(monkeypatch):
    """The judgement, scripted: records what it was asked and answers what the test sets."""
    asked: list[tuple[str, str]] = []
    state = {"answer": "yes"}

    def scripted(lead, order, **_kwargs):
        asked.append((lead, order))
        return state["answer"]

    monkeypatch.setattr(intent_arbiter, "arbitrate_forget_ask", scripted)
    pm._JUDGED_FORGET_ASKS.clear()
    return asked, state


def _order(text, context=None):
    return _forget_order_in_other_words(text, session_id=CHAT, source_context=dict(OWNER) if context is None else context)


def _entries():
    from core.memory import entries
    from core.memory.files import memory_entries_path

    return " ".join(str(row.get("text") or "") for row in entries._load_erasure_rows(memory_entries_path())
                    if str(row.get("status") or "active").lower() not in {"erased", "superseded"})


# ------------------------------------------------------------ which messages are an order


def test_an_unlisted_front_is_judged_and_a_yes_gives_the_order(saved, judge):
    asked, _ = judge
    assert _order("you can forget my work parking bay.") == "forget my work parking bay."
    assert asked == [("you can", "forget my work parking bay.")]


def test_a_no_gives_no_order(saved, judge):
    _, state = judge
    state["answer"] = "no"
    assert _order("you can forget about going to the beach, it is raining") is None


def test_no_judge_gives_no_order(saved, judge):
    _, state = judge
    state["answer"] = None
    assert _order("you can forget my work parking bay.") is None


def test_a_listed_lead_in_needs_no_judgement(saved, judge):
    asked, _ = judge
    assert _order("could you forget my library card number?") == "forget my library card number?"
    assert _order("please forget my library card number") == "forget my library card number"
    assert asked == []


@pytest.mark.parametrize("text", [
    "I always forget where I put my keys.",
    "we never forget a birthday in this house",
    "don't forget my dentist is Dr Marlow",
    "you can't forget my work parking bay",
])
def test_a_front_about_the_persons_own_forgetting_or_a_denied_order_is_never_judged(saved, judge, text):
    asked, _ = judge
    assert _order(text) is None
    assert asked == []


def test_a_plain_order_and_other_messages_are_not_this_readers(saved, judge):
    asked, _ = judge
    assert _order("forget my sweep.") is None            # the command readers take it, as before
    assert _order("what is my parking bay number?") is None
    assert _order("you can forget my work parking bay.\nand the library card") is None  # more than one line
    assert asked == []


def test_a_team_agents_turn_asks_nobody(saved, judge):
    asked, _ = judge
    assert _order("you can forget my work parking bay.", context={**OWNER, "turn_author": "agent"}) is None
    assert asked == []


def test_a_secret_is_never_sent_to_the_judge(saved, judge):
    asked, _ = judge
    _order("you can forget that my password is Harkness42")
    assert asked == [("you can", "forget (a password or another secret)")]


def test_the_judgement_is_asked_once_for_the_same_message(saved, judge):
    asked, _ = judge
    for _ in range(3):
        assert _order("you can forget my work parking bay.")
    assert len(asked) == 1


def test_the_question_put_to_the_judge_names_both_parts():
    prompt = intent_arbiter._forget_ask_prompt("you can", "forget my work parking bay.")
    assert 'Front: "you can" Rest: "forget my work parking bay." ->' in prompt
    assert prompt.count('{"answer": "yes"}') == prompt.count('{"answer": "no"}')  # balanced examples and instruction


# ------------------------------------------------------------ the order is carried out


def test_the_order_with_an_unlisted_front_forgets_the_saved_fact(saved, judge):
    assert "131" in _entries()
    assert everyday_memory_turn("you can forget my work parking bay.", session_id=CHAT, source_context=dict(OWNER))
    handled, reply = maybe_handle_memory_command("you can forget my work parking bay.", session_id=CHAT, source_context=dict(OWNER))
    assert handled
    assert str(reply).startswith("Forget applied. Removed 1 memory entry."), reply
    assert "131" not in _entries()
    assert "40721" in _entries()


def test_the_order_with_a_listed_lead_in_forgets_the_saved_fact(saved, judge):
    handled, reply = maybe_handle_memory_command("could you forget my library card number?", session_id=CHAT, source_context=dict(OWNER))
    assert handled
    assert str(reply).startswith("Forget applied. Removed 1 memory entry."), reply
    assert "40721" not in _entries()
    assert "131" in _entries()


def test_with_a_no_nothing_is_forgotten_and_the_turn_is_not_the_memory_lanes(saved, judge):
    _, state = judge
    state["answer"] = "no"
    text = "you can forget about the parking bay drama, it is sorted"
    assert not everyday_memory_turn(text, session_id=CHAT, source_context=dict(OWNER))
    handled, _reply = maybe_handle_memory_command(text, session_id=CHAT, source_context=dict(OWNER))
    assert not handled
    assert "131" in _entries()


def test_the_persons_own_forgetting_forgets_nothing(saved, judge):
    handled, _reply = maybe_handle_memory_command("I always forget my library card number.", session_id=CHAT, source_context=dict(OWNER))
    assert "40721" in _entries()
    assert not str(_reply).startswith("Forget applied")
