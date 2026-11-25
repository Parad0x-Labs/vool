"""A cancel that names a reminder which does not exist cancels nothing, even when the chat holds one other reminder.

iMac gate on 0d419ddb5 (qwen3:8b, from source): "set a reminder for thursday at 10 to renew the car insurance", then in
the same chat "delete my reminder about the dentist appointment". No dentist reminder existed; the chat's only reminder
was cancelled in its place ("Reminder cancelled: renew the car insurance will not be delivered"). The chat's-only-one
fallback is for a request that names no reminder at all, and the order words that say nothing about which reminder
("reminder", "thanks") were cut off at twelve, so "cancel my reminder" also seemed to name one. Notes and asks are
drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core.operator.handlers import _reminder_words, _select_reminder_target, reminder_target_or_refusal
from core.operator.models import OperatorActionIntent

NOTES = ("renew the car insurance", "water the tomatoes", "pay the electricity bill", "call grandma")
ABSENT = ("the dentist appointment", "the vet visit for the cat", "my passport renewal", "the plumber")
UNNAMED = ("cancel my reminder", "scrap the reminder, thanks", "delete that reminder please", "cancel it",
           "remove the reminder")


def _rows(note: str) -> list[dict]:
    return [{"reminder_id": "a1b2c3d4e5f6", "session_id": "chat-1", "status": "scheduled", "note": note,
             "due_at_utc": "2026-11-26T08:00:00+00:00", "tz_name": "Europe/Vilnius", "due_wall": "2026-11-26T10:00"}]


def _target(text: str, rows: list[dict]):
    intent = OperatorActionIntent(kind="cancel_reminder", raw_text=text)
    return reminder_target_or_refusal(intent, kind="cancel_reminder", session_id="chat-1", load_reminder_fn=lambda _i: None,
                                      list_reminders_fn=lambda **_k: list(rows))


@pytest.mark.parametrize("seed", range(8))
def test_a_reminder_that_is_not_there_is_never_swapped_for_the_only_one(seed) -> None:
    rng = random.Random(f"absent-{seed}")
    rows = _rows(rng.choice(NOTES))
    asked = f"{rng.choice(('delete', 'cancel', 'remove'))} my reminder about {rng.choice(ABSENT)}"

    target, refusal = _target(asked, rows)

    assert target is None and refusal is not None and not refusal.ok
    assert "No reminder matches that" in refusal.response_text and rows[0]["note"] in refusal.response_text


@pytest.mark.parametrize("asked", UNNAMED)
def test_a_cancel_that_names_no_reminder_still_means_the_only_one(asked) -> None:
    rows = _rows("renew the car insurance")
    assert not _reminder_words(asked)
    assert _select_reminder_target(OperatorActionIntent(kind="cancel_reminder", raw_text=asked), session_id="chat-1",
                                   load_reminder_fn=lambda _i: None, list_reminders_fn=lambda **_k: rows) == rows[0]


@pytest.mark.parametrize("seed", range(4))
def test_the_reminder_named_in_its_own_words_is_still_found(seed) -> None:
    rng = random.Random(f"named-{seed}")
    note = rng.choice(NOTES)
    target, refusal = _target(f"cancel the {note.split()[-1]} reminder", _rows(note))
    assert refusal is None and target is not None and target["note"] == note
