"""A Contacts pick stands only on a question of its own, unless the message carries what a contact holds.

iMac gate on f0a19600e (qwen3:8b): a fact about a friend's pet, told with "keep that bit to yourself", was picked
Contacts; the fact went into a contact's notes behind two approval cards and the message was kept in memory. A recall
about the person's own life ran a contact search. The shared yes/no then read a recall about the person's own number
as a contacts ask (Intel app smoke of efb8e474c), so Contacts has a three-way question of its own: contact, the
person's own life, or other. A message with a phone number or an email in it is the contact save's own and is not
asked again. Names, pets and wordings drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter, plugin_ask

PEOPLE = ("my climbing buddy Rasa", "my neighbour Tomas", "our babysitter Greta", "my cousin Elif")
PETS = ("a parrot called Kiwi", "a tortoise named Bolt", "two cats, Pipiras and Druska", "a hamster called Nugget")
TELLS = ("{p} has {a}, don't write that down", "{p} has {a}", "just so you know, {p} has {a}, but keep it between us",
         "what pet does {p} have again?")
VALUES = ("+370 612 34567", "rasa.k@example.com", "(555) 014-2233")
SAVES = ("save {p}, number {v}", "add {p} to my contacts: {v}", "{p}'s new email is {v}")


@pytest.fixture
def judged(monkeypatch):
    asked: list[str] = []

    def judge(verdict, told="no"):
        monkeypatch.setattr(intent_arbiter, "arbitrate_contact_ask",
                            lambda text, **_k: asked.append(text) or verdict)
        monkeypatch.setattr(intent_arbiter, "arbitrate_choice_ask",
                            lambda *_a, **_k: pytest.fail("Contacts is asked its own question"))
        monkeypatch.setattr(intent_arbiter, "arbitrate_told_as_fact", lambda *_a, **_k: told)
        return asked
    return judge


@pytest.mark.parametrize("seed", range(8))
def test_a_contacts_pick_on_a_told_fact_is_dropped_by_its_own_no(seed, judged) -> None:
    rng = random.Random(f"tell-{seed}")
    text = rng.choice(TELLS).format(p=rng.choice(PEOPLE), a=rng.choice(PETS))
    asked = judged("no")
    assert plugin_ask._actions_confirmed(text, ("contacts",), {}, "chat-1") == ()
    assert asked == [text]


@pytest.mark.parametrize("verdict", ["yes", None])
def test_a_contacts_pick_stands_on_a_yes_or_with_no_answer(verdict, judged) -> None:
    judged(verdict)
    assert plugin_ask._actions_confirmed("find Rasa in my contacts", ("contacts",), {}, "chat-1") == ("contacts",)


def test_no_answer_beside_a_told_fact_does_not_keep_the_pick(judged) -> None:
    judged(None, told="yes")
    assert plugin_ask._actions_confirmed("my neighbour Tomas has a parrot called Kiwi", ("contacts",), {}, "c") == ()


@pytest.mark.parametrize("seed", range(6))
def test_a_message_holding_a_number_or_email_is_not_asked_again(seed, judged) -> None:
    rng = random.Random(f"save-{seed}")
    text = rng.choice(SAVES).format(p=rng.choice(PEOPLE), v=rng.choice(VALUES))
    asked = judged("no")
    assert plugin_ask._actions_confirmed(text, ("contacts",), {}, "chat-1") == ("contacts",)
    assert asked == []


def test_the_question_says_a_told_fact_about_the_persons_people_is_no() -> None:
    prompt = intent_arbiter._choice_ask_prompt("x", "Contacts: ...")
    assert "Telling or asking something about the person's own life, people or pets" in prompt


def test_the_contact_question_reads_the_persons_own_numbers_as_theirs_not_a_contact() -> None:
    prompt = intent_arbiter._contact_ask_prompt("x")
    assert "the person's own life: their own things, numbers, codes" in prompt
    assert intent_arbiter._contact_kind_reader('{"answer": "mine"}') == "no"
    assert intent_arbiter._contact_kind_reader('{"answer": "contact"}') == "yes"
    assert intent_arbiter._contact_kind_reader('{"answer": "yes"}') is None
