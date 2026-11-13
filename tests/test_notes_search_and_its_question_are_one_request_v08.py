"""A bare "check my notes" and the question after it are one request: the question is what the notes search
looks for. CI on the v0.8 line (2026-11-13): split, the notes listing and the saved-chat search each answered
half, and the model was never offered the notes tools."""
from __future__ import annotations

import pytest

from core.agent_runtime.demand_ownership import execution_units


@pytest.mark.parametrize("ask", [
    "check my notes, did I write anything down about the dentist?",
    "look through my notes, is there anything about the boiler?",
])
def test_a_bare_notes_search_and_its_question_run_as_one_unit(ask):
    assert execution_units(ask) == (("u1", ask),)


@pytest.mark.parametrize("ask, parts", [
    ("check my notes for the plumber, and what is the price of gold?", 2),
    ("check my notes, and what is the price of gold?", 2),
    ("check my notes, and what time is it?", 2),
])
def test_a_request_of_its_own_beside_the_notes_search_stays_apart(ask, parts):
    assert len(execution_units(ask)) == parts, execution_units(ask)
