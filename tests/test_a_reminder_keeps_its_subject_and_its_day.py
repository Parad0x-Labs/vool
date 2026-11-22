"""A reminder keeps what it is about and falls due on the day that was said.

Gate v3 on the installed 07803d62d (local qwen3:8b, fresh wording): "Can you remind me the day after
tomorrow at 7pm to renew my library books" was stored as "the day after tomorrow", due TOMORROW at
19:00; "um set a reminder tomorrow evening at 8 to stretch for ten minutes" as "um set a reminder
tomorrow evening". Neither could be listed, found or cancelled by what it is about.

Causes: the time reader had no "day after tomorrow" (so it read the "tomorrow" inside it) and no
delay in number words ("in two days" read as nothing); the subject cut assumed the time comes
after the subject, and (gate v3 on d2dacb04a) the reminder's frame was only looked for at the very
start, so "right, i need a reminder to ..." was stored whole. The subject is now read by POSITION against the time reader's own spans, so the
cases below are shapes (time first, time last, time in the middle), each with words that are not
in any pattern list.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.operator.handlers import _reminder_note_from_text
from core.operator.when import normalize_time_words, parse_when_expression

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)  # a Sunday


def _read(text: str):
    clean = normalize_time_words(text)
    return _reminder_note_from_text(clean), parse_when_expression(clean, now_fn=lambda: NOW)


@pytest.mark.parametrize("text, subject", [
    # time first, subject after it
    ("Can you remind me the day after tomorrow at 7pm to renew my library books", "renew my library books"),
    ("um set a reminder tomorrow evening at 8 to stretch for ten minutes", "stretch for ten minutes"),
    ("hey remind me on thursday at 6pm about the plumber's invoice", "the plumber's invoice"),
    ("pls nudge me in three hours to take the bread out", "take the bread out"),
    # time last
    # a day word at the end stays in the title, as "call the bank tomorrow" always has (Job 6b)
    ("remind me to repot the fern the day after tomorrow at 10am", "repot the fern the day after tomorrow"),
    ("remind me to email Odile in two days", "email Odile"),
    # the reminder's frame after a preamble (gate v3 on d2dacb04a stored this whole sentence)
    ("right, i need a reminder to renew my library books tomorrow evening", "renew my library books tomorrow evening"),
    ("oh and set me a reminder for the dentist on friday at 4pm", "the dentist"),
    ("tomorrow at 7, set a reminder to call mum", "call mum"),
    # a colon or dash after the time, and an emoticon at the end (gate v3 on a7b184037)
    ("Hmm please remind me tomorrow at 6pm: call the dentist :)", "call the dentist"),
    ("remind me tomorrow at 9 - take the bins out", "take the bins out"),
    ("remind me to call mum at 5pm :)", "call mum"),
    # the ordinary shapes keep working
    ("remind me tomorrow at 9 to call mom", "call mom"),
    ("remind me to call the bank at 9am", "call the bank"),
])
def test_the_subject_is_what_to_do_never_the_time(text, subject):
    note, when = _read(text)
    assert note == subject
    assert when.ok, when.problem


@pytest.mark.parametrize("text, day", [
    ("Can you remind me the day after tomorrow at 7pm to renew my library books", 24),
    ("remind me to repot the fern the day after tomorrow at 10am", 24),
    ("remind me in two days at 7pm to water the plants", 24),
    ("remind me tomorrow at 7pm to water the plants", 23),
])
def test_the_due_day_is_the_day_that_was_said(text, day):
    _note, when = _read(text)
    assert when.ok, when.problem
    assert datetime.fromisoformat(when.due_at_utc.replace("Z", "+00:00")).astimezone(timezone.utc).day == day or \
        when.due_wall.startswith(f"2026-11-{day:02d}"), (when.due_at_utc, when.due_wall)


def test_a_spelled_delay_reads_like_its_digits():
    _note, spelled = _read("remind me in ten minutes to check the oven")
    _note, digits = _read("remind me in 10 minutes to check the oven")
    assert spelled.ok and spelled.due_at_utc == digits.due_at_utc
