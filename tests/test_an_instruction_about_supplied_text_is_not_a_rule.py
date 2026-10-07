"""An instruction that comes with the text it applies to is a task on that text, not a standing rule.

The plain-rule law judged the owner's own words after quoted and pasted material was cut away, so
'Translate into Spanish: "Where is the station?"' became the whole-turn order "Translate into Spanish:" and was saved
-- and every later answer came out in Spanish. A turn that supplies material (a quote, a paste, text after a colon or
a dash) is about that material. Authored wording only.
"""
from __future__ import annotations

import pytest

from core import standing_instructions as si

OWNER = {"surface": "chat", "_owner_local": True, "workspace_root": "/srv/projects/meridian"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_store_path", lambda: tmp_path / "standing_instructions.json")


def _say(text):
    return si.observe_turn(text, OWNER, principal="owner_local", session_id="openclaw:" + "a" * 20)


@pytest.mark.parametrize("text", [
    'Translate into Spanish: "Where is the nearest train station?"',
    "translate to german - I will be late tonight",
    'Summarize in three bullet points:\n"Remote teams need clear written norms, overlapping hours and a shared place '
    'for decisions."',
    "Rewrite in a formal tone:\n\nhey, can u send me the invoice by friday, thx",
    "Shorten to two sentences: The library will close early on Friday for staff training and reopen on Saturday "
    "morning at the usual time.",
    "Put into bullet points -- buy milk, call the bank, book the dentist, water the plants",
    "In plain English please:\n> The party of the first part shall indemnify the party of the second part.",
    'Make it friendlier: "Send the file now."',
])
def test_an_instruction_with_its_material_saves_nothing(text):
    assert _say(text)["saved"] == []


@pytest.mark.parametrize("text", [
    "Use metric units.",
    "Answer in bullet points.",
    "Keep your answers to two sentences.",
])
def test_a_rule_with_no_material_is_still_saved(text):
    assert _say(text)["saved"] == [text]


def test_a_standing_rule_with_a_colon_is_still_saved():
    text = "From now on: reply in English."
    assert _say(text)["saved"] == [text]
