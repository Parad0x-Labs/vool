"""A one-word reply constraint is not a Word document.

Measured in CI 2026-10-13 (tests/test_pb01_served_sufficiency_served.py): "Reply in exactly one word:
acknowledged or denied, which do you prefer? Just one word." was read as a file order ("in exactly one
word" matched the Word format), the turn went to the file lane and the plain one-word answer failed.
"""
from __future__ import annotations

import pytest

from core.tool_demand_signals import asks_for_a_file


@pytest.mark.parametrize("text", [
    "Reply in exactly one word: acknowledged or denied, which do you prefer? Just one word.",
    "How many days did the crossing take? Answer in one word.",
    "In a word, is the bridge safe to cross?",
    "Turn the answer into one word.",
])
def test_a_one_word_reply_constraint_orders_no_file(text):
    assert not asks_for_a_file(text)


@pytest.mark.parametrize("text", [
    "Save the summary as a Word document",
    "Export the notes to Word",
    "export the notes to word",
    "Put this in a word doc for me",
])
def test_the_word_program_is_still_ordered_as_a_file(text):
    assert asks_for_a_file(text)
