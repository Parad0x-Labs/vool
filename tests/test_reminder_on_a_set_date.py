"""A reminder asked for on a set date is due on that date, not tomorrow.

Found on the v0.8 line by the real-app run: "remind me to call the bank on 5 March 2027 at 9am"
replied "Reminder scheduled: call the bank on 5 March 2027. Due 13 Nov 2026 at 09:00". The subject
cut kept the date (it did not know a year, an "of", an ISO date or a date said after the clock), so
the due time was read from "at 9am" alone. Driven through the real reminder handler and the real
when-reader, with the clock fixed on a Thursday evening in a +02:00 zone.
"""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ZONE = ZoneInfo("Europe/Vilnius")
NOW = datetime(2026, 11, 12, 21, 0, tzinfo=ZONE)  # Thursday 12 November 2026, 21:00


def _schedule(ask: str):
    from core.operator.handlers import handle_schedule_reminder
    from core.operator.when import parse_when_expression

    saved: dict = {}

    def schedule(**kwargs):
        saved.update(kwargs)
        return dict(kwargs, reminder_id="dated-reminder")

    result = handle_schedule_reminder(
        SimpleNamespace(raw_text=ask),
        task_id="t", session_id="s",
        parse_when_fn=lambda text: parse_when_expression(text, now_fn=lambda: NOW),
        schedule_reminder_fn=schedule, audit_log_fn=lambda *args, **kwargs: None,
    )
    return result, saved


@pytest.mark.parametrize("ask,due,note", [
    # The asks from the real-app finding.
    ("remind me to call the bank on 5 March 2027 at 9am", "2027-03-05T09:00", "call the bank"),
    ("remind me to call the bank on 2027-03-05 at 09:00", "2027-03-05T09:00", "call the bank"),
    # Day first, month first, ordinal, with and without a year.
    ("remind me to call the bank on 5 March at 9am", "2027-03-05T09:00", "call the bank"),
    ("remind me to call the bank on March 5th at 9am", "2027-03-05T09:00", "call the bank"),
    ("remind me to call the bank on March 5th, 2027 at 9am", "2027-03-05T09:00", "call the bank"),
    ("remind me to pay rent on 5 Mar 2027 at 9:00", "2027-03-05T09:00", "pay rent"),
    ("remind me to call the bank on the 5th of March at 9am", "2027-03-05T09:00", "call the bank"),
    ("remind me to call the bank on the 5th of March 2027 at 9am", "2027-03-05T09:00", "call the bank"),
    ("remind me to call the bank on the 5th at 9am", "2026-12-05T09:00", "call the bank"),
    # The date before the subject, and the clock before the date.
    ("remind me on 5 March 2027 at 9am to call the bank", "2027-03-05T09:00", "call the bank"),
    ("remind me to call the bank at 9am on 5 March 2027", "2027-03-05T09:00", "call the bank"),
    ("remind me to call the bank at 9:30 on the 5th", "2026-12-05T09:30", "call the bank"),
    # A part of the day instead of a clock.
    ("remind me to pick up the 3 kids at noon on March 5th 2027", "2027-03-05T12:00", "pick up the 3 kids"),
    ("remind me on 5 March in the morning to water the plants", "2027-03-05T09:00", "water the plants"),
    # A clock inside the subject stays there; the date and the last clock are the due time.
    ("remind me to book the 9am standup room on 5 March at 8am", "2027-03-05T08:00", "book the 9am standup room"),
])
def test_dated_reminder_is_due_on_its_date(ask, due, note):
    result, saved = _schedule(ask)
    assert result.ok, result.response_text
    assert saved["due_wall"].startswith(due), (ask, saved["due_wall"])
    assert saved["note"] == note
    day = datetime.fromisoformat(due).strftime("%d %b %Y")
    assert day in result.response_text, result.response_text


@pytest.mark.parametrize("ask,due,note", [
    # Unchanged readings next to the fix: a day word keeps its day, "tomorrow" keeps Job 6b's subject.
    ("remind me to call the bank on Friday at 9am", "2026-11-13T09:00", "call the bank"),
    ("remind me to call the bank at 9am on friday", "2026-11-13T09:00", "call the bank"),
    ("remind me to call the bank tomorrow at 9am", "2026-11-13T09:00", "call the bank tomorrow"),
])
def test_day_word_reminders_keep_their_reading(ask, due, note):
    result, saved = _schedule(ask)
    assert result.ok, result.response_text
    assert saved["due_wall"].startswith(due)
    assert saved["note"] == note


@pytest.mark.parametrize("ask,question", [
    ("remind me to call the bank on 5 March 2027", "Which time on 5 March 2027?"),
    ("remind me to call the bank on 31 February 2027 at 9am", "is not a real date"),
])
def test_dated_reminder_without_a_usable_time_is_asked_about(ask, question):
    result, saved = _schedule(ask)
    assert not result.ok
    assert question in result.response_text
    assert saved == {}
