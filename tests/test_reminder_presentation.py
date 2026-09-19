"""Reminder prose stays readable while exact instants and artifact identity remain intact."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("prompt,expected", [
    ("In 45 seconds, remind me in this chat to inspect the beta export.", "inspect the beta export"),
    ("Remind me here to check the stamps in 2 minutes.", "check the stamps"),
    ('Remind me about "in this chat to plan the party" in 2 minutes.', '"in this chat to plan the party"'),
    ("Remind me to say here to help in 2 minutes.", "say here to help"),
])
def test_delivery_qualifier_does_not_become_reminder_subject(prompt, expected):
    from core.operator.handlers import _reminder_note_from_text

    assert _reminder_note_from_text(prompt) == expected


@pytest.mark.parametrize("instant,zone,expected", [
    ("2026-09-12T21:15:08.662139+00:00", "UTC", "12 Sep 2026 at 21:15:08 UTC"),
    ("2026-09-12T21:15:08.662139+00:00", "Europe/Athens", "13 Sep 2026 at 00:15:08 EEST"),
    ("2026-11-01T05:30:00+00:00", "America/New_York", "01 Nov 2026 at 01:30:00 EDT"),
    ("2026-11-01T06:30:00+00:00", "America/New_York", "01 Nov 2026 at 01:30:00 EST"),
])
def test_confirmation_and_delivery_share_human_due_time(monkeypatch, instant, zone, expected):
    from core.operator.handlers import handle_schedule_reminder
    from core.operator.reminder_dispatcher import _deliver_to_session_log
    from core.operator.when import WhenResolution

    saved = {}
    rendered = {}

    def schedule(**kwargs):
        saved.update(kwargs)
        return dict(kwargs, reminder_id="presentation-reminder")

    monkeypatch.setattr(
        "core.persistent_memory.append_assistant_artifact_event",
        lambda **kwargs: rendered.update(kwargs) is None,
    )
    result = handle_schedule_reminder(
        SimpleNamespace(raw_text="Remind me to inspect the inventory in 45 seconds."),
        task_id="t", session_id="s", parse_when_fn=lambda text: WhenResolution(
            ok=True, due_at_utc=instant, due_wall=instant, tz_name=zone,
        ), schedule_reminder_fn=schedule, audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok
    assert expected in result.response_text
    assert instant not in result.response_text
    assert result.details["due_at_utc"] == saved["due_at_utc"] == instant
    delivery = _deliver_to_session_log(dict(saved, reminder_id="presentation-reminder"))
    assert delivery["ok"]
    assert expected in rendered["text"]
    assert instant not in rendered["text"]
    assert rendered["artifact"] == {"reminder_id": "presentation-reminder", "status": "delivered"}
