"""An order whose object is a saved contact goes to the Contacts tools: never a memory forget, never a file change.

Real-app check v3 on fe98e849 (installed app, local qwen3:8b, fresh home, 2026-11-22):
- "So erase the contact for Ines Halloran" was taken by the memory lane ("Forget applied. Removed 1 memory entry") and
  the contact stayed.
- "right, delete the conatct ines kalnin thanks" reached the model with no Contacts tool on offer, and the contact stayed.
- "um add Ines Halloran to my contacts, number +1 555 0154 thx" was admitted by the file loop as a change, whose model
  answered "Your proposal ... has been shown" with no card and no contact.
The wording is built from parts at random, so no fixed sentence is what passes. All people here are invented.
"""
from __future__ import annotations

import random

import pytest

from core.clean_loop import route
from core.clean_loop.tools import ToolContext, propose_change
from core.contacts.requests import removal_object_is_a_contact
from core.tool_demand_signals import resolve_demand_signals
from tests.test_everyday_remember_v08 import _facts, _say
from tests.test_memory_forget_resurrection_p0 import isolated_home

PEOPLE = ["Ines Halloran", "ines kalnin", "Bartholomew Quist", "Odalys Fenn", "Ruth"]
LEADS = ["", "So ", "right, ", "ok ", "um ", "Alright "]
VERBS = ["erase", "delete", "remove", "get rid of", "drop", "scrap"]
OBJECTS = ["the contact for {p}", "contact {p}", "{p} from my contacts", "the conatct {p}", "{p} from my contcats",
           "the contact card for {p}", "my contact named {p}", "the contct {p}"]
TAILS = ["", " thanks", " pls", " :)", " thx"]


def _order(rng: random.Random, person: str, verbs: list[str] = VERBS) -> str:
    return rng.choice(LEADS) + rng.choice(verbs) + " " + rng.choice(OBJECTS).format(p=person) + rng.choice(TAILS)


@pytest.mark.parametrize("seed", range(10))
def test_a_contact_removal_is_read_as_a_contacts_delete(seed):
    rng = random.Random(f"contact-removal-{seed}")
    for _ in range(5):
        said = _order(rng, rng.choice(PEOPLE))
        assert removal_object_is_a_contact(said), said
        signals = resolve_demand_signals(said)
        assert "contacts.delete" in signals.explicit_intents and signals.write_demand, (said, signals)


@pytest.mark.parametrize("seed", range(4))
def test_a_contact_removal_forgets_nothing_saved(isolated_home, seed):
    rng = random.Random(f"contact-removal-memory-{seed}")
    person = rng.choice(PEOPLE)
    assert _say(f"remember that {person} is my dentist")[1].startswith("Locked in")
    said = _order(rng, person, ["erase", "forget"])  # the memory lane's own verbs
    handled, reply = _say(said)
    assert (handled, reply) == (False, ""), (said, reply)
    assert any(person in fact for fact in _facts()), (said, _facts())


@pytest.mark.parametrize(
    "said",
    [
        "forget my contact at the bank",              # a person by role, said to memory
        "forget that my contact at the bank is Priya",
        "delete the contract for the flat",           # another word, not a slip of "contact"
        "erase the contact form draft",
        "delete the contact",                          # names no one
    ],
)
def test_words_near_contact_that_name_no_saved_contact_are_not_a_contacts_delete(said):
    assert not removal_object_is_a_contact(said), said


@pytest.mark.parametrize(
    "said",
    [
        "um add Ines Halloran to my contacts, number +1 555 0154 thx",
        "save Odalys Fenn as a contact, 086 555 0166",
        "right, delete the conatct ines kalnin thanks",
        "So erase the contact for Ines Halloran",
    ],
)
def test_the_file_loop_does_not_take_a_contacts_order(said):
    assert route._vool_lane_owns(said) == "contacts_turn", said


def test_a_change_named_only_in_words_is_not_a_proposal_when_changes_are_on(tmp_path, monkeypatch):
    monkeypatch.setenv(route.CHANGES_ENV, "1")
    ctx = ToolContext(home=tmp_path, source_context={"_owner_local": True})
    outcome = propose_change({"what": "add Ines Halloran to your contacts"}, ctx)
    assert not outcome.ok and outcome.text.startswith("not possible"), outcome
    assert ctx.proposals == [] and ctx.receipts == []
