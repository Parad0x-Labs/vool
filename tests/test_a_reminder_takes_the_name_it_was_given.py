"""A reminder the ask names takes that name as its note (Telegram 2026-11-27, list item 49: "named it test 123" was stored)."""
from __future__ import annotations

import pytest

from core.operator.handlers import _reminder_note_from_text


@pytest.mark.parametrize("ask", [
    "remind me in 5 minutes named it test 123",
    "set a reminder in 2 minutes and name it test 123",
    "remind me tomorrow at 9, name it test 123",
    "create reminder named test 123 in 5 min",
    "make a reminder in 10 minutes call it test 123",
    "reminder in 1 minute with the name test 123",
    "remind me at 3pm, title: test 123",
])
def test_the_given_name_is_the_note(ask):
    assert _reminder_note_from_text(ask) == "test 123"


@pytest.mark.parametrize(("ask", "note"), [
    ("remind me to call it a day at 6pm", "call it a day"),
    ("remind me about the dog named Rex at 5pm", "the dog named Rex"),
    ("remind me to change my name on the lease friday at 10", "change my name on the lease"),
    ("remind me to update the title of my thesis at 4pm", "update the title of my thesis"),
])
def test_naming_words_inside_the_thing_to_do_stay(ask, note):
    assert _reminder_note_from_text(ask) == note
