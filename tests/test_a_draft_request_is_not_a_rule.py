"""A request to produce something ("draft a short, friendly email to my landlord") is a task, not a standing rule.

The plain-rule law saw a whole-turn order with a tone or length word and saved it. In a draft request those words
describe the thing to produce -- the object of the order is an email, a text, a toast -- while a rule's object is how
answers look ("use a casual tone", "keep your answers short"). Authored wording only.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/foundry"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text):
    return si.observe_turn(text, OWNER, principal="owner_local", session_id="openclaw:" + "b" * 20)


@pytest.mark.parametrize("text", [
    "Draft a short, friendly email to my landlord about the leaky tap.",
    "Write a polite two-line text declining the dinner invite.",
    "Draft a formal reply to the supplier asking for a new delivery date.",
    "Write a casual birthday message for my cousin.",
    "Compose a cheerful toast for a retirement party.",
    "Make me a brief agenda for Monday's stand-up.",
    "Write two formal sentences thanking the team.",
    "draft short polite msg to the plumber",
    "Give me a concise cover letter opening for a junior designer role.",
])
def test_a_request_to_produce_something_saves_nothing(text):
    assert _say(text)["saved"] == []


@pytest.mark.parametrize("text", [
    "Use a casual tone.",
    "Keep a formal tone.",
    "Use bullet points.",
    "Answer in a friendly tone.",
    "Keep your answers under five sentences.",
    "Avoid jargon.",
])
def test_a_rule_about_how_answers_look_is_still_saved(text):
    assert _say(text)["saved"] == [text]
