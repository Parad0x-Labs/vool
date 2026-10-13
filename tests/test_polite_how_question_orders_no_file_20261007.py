"""A polite how-question names a format without ordering a file.

Found 2026-10-06 while proving the formats fix: asks_for_a_file("can you explain how to make a PDF
accessible?") was True. The question starts with no question word, so the imperative reading took
"to make a PDF" as an order and the turn was sent into the file tool loop.
"""
from __future__ import annotations

import pytest

from core.tool_demand_signals import asks_for_a_file


@pytest.mark.parametrize(
    "text",
    [
        "can you explain how to make a PDF accessible?",
        "could you tell me how to convert a Word document to PDF?",
        "can you show me how to save a spreadsheet as CSV in Excel?",
        "would you walk me through how to export a slide deck to PDF",
        "Could you please describe how to zip a folder on Windows?",
    ],
)
def test_a_polite_how_question_is_not_a_file_order(text):
    assert not asks_for_a_file(text)


@pytest.mark.parametrize(
    "text",
    [
        "Can you give me that as a PDF?",
        "can you explain how photosynthesis works and give it to me as a PDF?",
        "Could you show me how the budget breaks down and export it as an Excel file?",
        "Can you make me a PDF of the packing list?",
    ],
)
def test_a_polite_request_that_hands_over_a_file_still_orders_it(text):
    assert asks_for_a_file(text)
