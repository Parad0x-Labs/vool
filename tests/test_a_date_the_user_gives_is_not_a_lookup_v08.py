""""if today is 1 march, how many days until ...?" gives today's date itself: no current information is needed.

Measured on the real local model (qwen3:8b, H2H memory set, fresh home): the question was held to current information
and the answer withheld ("I didn't look anything up for this, and I had nothing to check the answer I wrote against"
on 88b3247e; "That is not mentioned in the records I have from our conversations" on 9a156361). Dates are invented.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import _supplies_todays_date, requirements_for


@pytest.mark.parametrize(
    "text",
    ["if today is 1 march, how many days until the cabin trip starts?", "say it's the 3rd of may, what day is friday?",
     "if today were march 3, how long until the 20th?", "assuming today is 2027-04-01, how many weeks to june?"],
)
def test_a_supplied_date_needs_no_current_information(text):
    assert _supplies_todays_date(text)
    assert not requirements_for(text, source_context={}).current_information_required


@pytest.mark.parametrize("text", ["what is the date today?", "is today 1 march?", "how many days until march 13?"])
def test_a_question_about_today_supplies_no_date(text):
    assert not _supplies_todays_date(text)
