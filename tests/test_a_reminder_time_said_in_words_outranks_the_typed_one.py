"""When VOOL's own reader takes a due time from the person's words, it outranks the time a model typed; a time typed
for words the reader cannot take still stands.

iMac gate41 on 9141095e4 (qwen3:8b): "give me a nudge three quarters of an hour from now to take the washing in" was
typed in_minutes 226 and "and another one in a quarter of an hour to water the tomatoes" 105. Driven through the real
tool-call intent builder, the real reminder handler and the real when-reader. Wordings are fresh.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.execution import operator_tools

NOW = datetime(2026, 11, 26, 13, 0, tzinfo=timezone.utc)


class _Clock:
    def now_utc(self):
        return NOW


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    from core import time_authority

    monkeypatch.setattr(time_authority, "CLOCK", _Clock())


def _due(arguments: dict) -> tuple[datetime, str]:
    from core.operator.handlers import handle_schedule_reminder
    from core.operator.when import parse_when_expression

    saved: dict = {}

    def schedule(**kwargs):
        saved.update(kwargs)
        return dict(kwargs, reminder_id="r1")

    handle_schedule_reminder(
        operator_tools.build_operator_action_intent("schedule_reminder", arguments),
        task_id="t", session_id="s",
        parse_when_fn=lambda text: parse_when_expression(text, now_fn=lambda: NOW),
        schedule_reminder_fn=schedule, audit_log_fn=lambda *args, **kwargs: None,
    )
    return datetime.fromisoformat(saved["due_at_utc"]), saved["note"]


@pytest.mark.parametrize(("text", "minutes"), [
    ("ping me three quarters of an hour from now to bring the bins in", 45),
    ("and one more in a quarter of an hour to turn the oven off", 15),
    ("set a reminder for 20 minutes from now to move the car", 20),
])
def test_the_words_outrank_a_typed_wait(text, minutes):
    due, _note = _due({"note": "do it", "in_minutes": 226, "due_iso": "2026-11-26T18:46", "text": text})
    assert abs(due - (NOW + timedelta(minutes=minutes))) < timedelta(seconds=61)


def test_a_typed_time_stands_for_words_the_reader_cannot_take():
    due, _note = _due({"note": "paskambinti mamai", "in_minutes": 25,
                       "text": "priminkit po dvidešimt penkių minučių paskambinti mamai"})
    assert due == NOW + timedelta(minutes=25)
