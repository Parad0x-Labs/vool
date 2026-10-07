"""A task that says how its own answer should look is a task, not a standing rule.

The plain-rule law (a whole turn of instructions is saved) read "Explain how vaccines work in two sentences." as a
rule: the turn was one addressed sentence that named a length. The format words there modify that one request; a
rule's format words are the request itself ("Answer in bullet points.", "Keep your answers to two sentences.").
Authored wording only.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/lantern"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text):
    return si.observe_turn(text, OWNER, principal="owner_local", session_id="openclaw:" + "9" * 20)


@pytest.mark.parametrize("text", [
    "Explain how vaccines work in two sentences.",
    "Describe the water cycle in three short bullet points.",
    "Give me a packing list for a beach weekend as a table.",
    "explain how tides work in 2 sentences",
    "Outline the plot of a heist film in bullet points.",
    "Compare trains and buses for a commute in a short table.",
    "Tell me a fun fact about owls in one short sentence.",
    "Write a thank-you note to a neighbour in a casual tone.",
    "Describe autumn in German.",
    "Explain recursion in two sentences this time.",
])
def test_a_request_with_its_own_format_saves_nothing(text):
    assert _say(text)["saved"] == []


@pytest.mark.parametrize("text", [
    "Answer in bullet points.",
    "Reply in short sentences.",
    "Keep your answers to two sentences.",
    "Use bullet points.",
    "Write dates as day, month, year.",
])
def test_a_rule_whose_format_is_the_request_is_still_saved(text):
    assert _say(text)["saved"] == [text]
