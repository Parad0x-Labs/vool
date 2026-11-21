"""A log of the person's own day is saved as a note on any model, and the reply names the entry.

CI item 177 (tests/test_a_log_of_my_day_is_not_a_lookup_served_v08.py, pinned local model): "log today's session:
5k run, 28 minutes" went to the chosen model, which answered "Logged today's session ..." with nothing kept, and the
claim check rightly replaced that with "Nothing from this message was saved". On an unpinned chat the note WAS saved
and the reply was still corrected to "No note was saved": the note run was not on the turn's ledger. sls (Nov 21):
a log ask saves the entry and the reply confirms it in its own words. Controls: other note asks under a chosen model
stay with that model, and "log in" / "log out" are no log.
"""
from __future__ import annotations

import pytest

from core.operator.notes import parse_note_request
from core.operator.parser import asks_to_log_an_entry


@pytest.mark.parametrize("text", [
    "log today's session: 5k run, 28 minutes",
    "track my run: 5k in 28 minutes",
    "please log my sleep: 7 hours",
])
def test_a_log_entry_is_recognised_and_named(text):
    assert asks_to_log_an_entry(text)
    parsed = parse_note_request(text)
    assert parsed["logged_entry"] is True and parsed["body"], parsed


@pytest.mark.parametrize("text", [
    "log in to my email",
    "log out of the app",
    "write a short thank-you note to my landlord",
    "save a note with agenda: packaging review",
    "show me the log",
])
def test_other_asks_are_no_log_entry(text):
    assert not asks_to_log_an_entry(text)
    assert not parse_note_request(text).get("logged_entry"), text
