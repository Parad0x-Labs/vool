"""A date the person supplies sets "today" for date arithmetic only; any other present reading in the turn is kept.

Independent review of the job 19 commits (C4): "if today is <date>" or "if it is <number>.<number>" turned off current
information for the whole turn, so the weather, a carrier's price or a shop's opening were answered with no lookup; a
weight ("2.5 kg") and a time ("12.30") were read as dates. All places and figures are invented.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import _supplies_todays_date, requirements_for


@pytest.mark.parametrize(
    "text",
    ["if today is 1 march, what is the weather in Oslo?", "if it is 2.5 kg what does the courier charge now?",
     "say it is 12.30, is the pharmacy open now?"],
)
def test_a_live_ask_beside_a_supplied_date_keeps_its_lookup(text):
    assert not _supplies_todays_date(text)
    assert "user_stipulated_frame" not in requirements_for(text, source_context={}).reason_codes


@pytest.mark.parametrize(
    "text",
    ["if today is 3/5, how many days until the 20th?", "say today is 14.11.2026, what day is the 20th?"],
)
def test_a_date_shape_with_date_arithmetic_is_still_supplied(text):
    assert _supplies_todays_date(text)
