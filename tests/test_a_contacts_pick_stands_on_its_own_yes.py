"""A Contacts pick stands only on a yes/no of its own, unless the message carries what a contact holds.

iMac gate on f0a19600e (qwen3:8b): a fact about a friend's pet, told with "keep that bit to yourself", was picked
Contacts; the fact went into a contact's notes behind two approval cards and the message was kept in memory. A recall
about the person's own life ran a contact search. A message with a phone number or an email in it is the contact
save's own and is not asked again. Names, pets and wordings drawn at random.
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
    asked: list[tuple[str, str]] = []

    def judge(verdict):
        monkeypatch.setattr(intent_arbiter, "arbitrate_choice_ask",
                            lambda text, choice, **_k: asked.append((text, choice)) or verdict)
        return asked
    return judge


@pytest.mark.parametrize("seed", range(8))
def test_a_contacts_pick_on_a_told_fact_is_dropped_by_its_own_no(seed, judged) -> None:
    rng = random.Random(f"tell-{seed}")
    text = rng.choice(TELLS).format(p=rng.choice(PEOPLE), a=rng.choice(PETS))
    asked = judged("no")
    assert plugin_ask._actions_confirmed(text, ("contacts",), {}, "chat-1") == ()
    assert len(asked) == 1 and asked[0][0] == text and asked[0][1].startswith("Contacts:")


@pytest.mark.parametrize("verdict", ["yes", None])
def test_a_contacts_pick_stands_on_a_yes_or_with_no_answer(verdict, judged) -> None:
    judged(verdict)
    assert plugin_ask._actions_confirmed("find Rasa in my contacts", ("contacts",), {}, "chat-1") == ("contacts",)


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
