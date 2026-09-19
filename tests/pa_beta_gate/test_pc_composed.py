"""pa_beta_gate -- composed requests run and report every part (row 11).

A request naming several things runs each part through its ordinary handler and reports each
outcome; a refusing part does not sink or silently drop the others; each part's durable store
(pending action, note file, reminder row) is the composed record across restart.

LABELLED: loopback CalDAV fixture; injected clock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
CAL = "/calendars/team/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    prepared = prepare_home(tmp_path, monkeypatch)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Athens")))
    return prepared


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed the request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _setup(base):
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="caldav", base_url=base, label="Team")
    assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
    assert calendar_accounts.select_calendar(account["account_id"], CAL, selected=True, default_write=True)["ok"]
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)


def test_composed_request_reports_each_part_and_makes_each_artifact(home, monkeypatch, tmp_path):
    """ORIGINAL: availability check + save a note + remind me — one reply with three part
    sections; the note file and the reminder row exist afterwards; the availability part shows
    the fixture's busy event."""
    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        state.seed_event(CAL, "busy@fixture", summary="Standing sync", start=T0 + timedelta_day(1, 5), minutes=60)
        session = "composed"
        result = _run(
            "Check Tuesday afternoon for a free 30-minute slot, save a note with agenda: packaging checks, "
            "and remind me to review the agenda on 2026-09-22 at 08:00",
            session_id=session,
        )
        text = result.response_text
        assert "3 parts" in text, text
        assert "Standing sync" in text, text
        assert result.details["parts"][0]["kind"] == "check_availability" and result.details["parts"][0]["ok"]
        assert result.details["parts"][1]["kind"] == "save_note" and result.details["parts"][1]["ok"]
        assert result.details["parts"][2]["kind"] == "schedule_reminder" and result.details["parts"][2]["ok"]

        from core.operator.reminders import list_reminders
        from storage.db import get_connection

        [reminder] = list_reminders(session_id=session, get_connection_fn=get_connection)
        assert "review the agenda" in reminder["note"]
        note_files = list((tmp_path / "workspace" / "notes").glob("*.md"))
        assert note_files and "packaging checks" in note_files[0].read_text()
    finally:
        server.shutdown(); server.server_close()


def timedelta_day(days, hours):
    from datetime import timedelta

    return timedelta(days=days, hours=hours)


def test_a_failing_part_is_reported_and_the_rest_still_run(home, monkeypatch):
    """NOVEL: an unreadable availability part (no day named parses for the availability
    window) is refused as its own part; the note and reminder still complete; the overall
    reply is partial, not success and not silent."""
    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        session = "composed-partial"
        result = _run(
            "Check the calendar availability whenever, save a note with agenda: fallback plan, "
            "and remind me on 2026-09-23 at 09:00 to call the vendor",
            session_id=session,
        )
        parts = result.details["parts"]
        by_kind = {part["kind"]: part for part in parts}
        assert by_kind["check_availability"]["ok"] is False, parts
        assert by_kind["save_note"]["ok"] is True, parts
        assert by_kind["schedule_reminder"]["ok"] is True, parts
        assert result.status == "partial", result.status
        assert "Which day" in result.response_text, result.response_text

        from core.operator.reminders import list_reminders
        from storage.db import get_connection

        assert list_reminders(session_id=session, get_connection_fn=get_connection)
    finally:
        server.shutdown(); server.server_close()


def test_single_intent_requests_are_not_composed(home, monkeypatch):
    """CONTROL: plain availability, plain note and plain reminder keep their ordinary single
    replies (no 'parts' header), and an approval never composes."""
    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        session = "composed-ctl"
        plain = _run("Check Tuesday afternoon for a free 30-minute slot", session_id=session)
        assert plain.ok and "parts" not in plain.response_text and "parts" not in plain.details, plain.response_text
        note = _run("save a note with agenda: solo", session_id=session)
        assert note.ok and "parts" not in note.response_text, note.response_text
        reminder = _run("remind me on 2026-09-24 at 10:00 to stretch", session_id=session)
        assert reminder.ok and "parts" not in reminder.response_text, reminder.response_text
    finally:
        server.shutdown(); server.server_close()


def test_dependent_composed_chain_with_per_part_receipts(home, monkeypatch):
    """CORRECTION-2 (row 11, the assigned dependent workflow): inspect the week, book into the
    FIRST free slot, link a note, set an alert 15 minutes before — every part reported; the
    booking is an approval-gated durable action at the chosen slot; the note is linked to the
    proposal; the alert is scheduled from the PROPOSAL's staged start; after approval the event
    exists at the chosen slot, the note carries the receipt, and the reminder is pending."""
    from datetime import timedelta

    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        state.seed_event(CAL, "busy@f", summary="Standing sync", start=T0 + timedelta(days=4, hours=3), minutes=60)  # Friday 13:00-14:00 local
        session = "dependent"
        result = _run(
            "Check Friday for a free 30-minute slot, propose \"Retro\" in the first free slot, "
            "save a note with agenda: what to improve, and remind me 15 minutes before it",
            session_id=session,
        )
        parts = {part["kind"]: part for part in result.details["parts"]}
        assert set(parts) == {"check_availability", "propose_calendar_event", "save_note", "schedule_reminder"}, parts.keys()
        assert parts["check_availability"]["ok"]
        options = parts["check_availability"]["details"]["options"]
        assert options, result.response_text

        booking = parts["propose_calendar_event"]
        assert booking["status"] == "approval_required", result.response_text
        assert booking["details"]["start_utc"] == options[0]["start_utc"], (booking["details"], options[0])
        assert parts["save_note"]["ok"], result.response_text
        note_path = parts["save_note"]["details"]["note_path"]
        assert booking["details"].get("note_path") == note_path, "the proposal links the note part's file"

        alert = parts["schedule_reminder"]
        assert alert["ok"], result.response_text
        from datetime import datetime

        expected_due = datetime.fromisoformat(options[0]["start_utc"]) - timedelta(minutes=15)
        assert alert["details"]["due_at_utc"].startswith(expected_due.replace(tzinfo=None).isoformat(timespec="minutes")), alert["details"]

        approved = _run(f"approve calendar {booking['details']['action_id']}", session_id=session)
        assert approved.ok, approved.response_text
        uid = approved.details["uid"]
        stored = state.snapshot()[CAL.rstrip("/") + "/"][uid]
        assert stored["summary"] == "Retro" and stored["start_utc"].startswith(options[0]["start_utc"][:16]), stored

        from core.operator.reminders import list_reminders
        from storage.db import get_connection

        [reminder] = list_reminders(session_id=session, get_connection_fn=get_connection)
        assert "proposed event" in reminder["note"] or "Retro" in reminder["note"], reminder
        note_text = Path(note_path).read_text()
        assert "what to improve" in note_text and uid[:8] in note_text, note_text  # receipt linked into the note
        assert True
    finally:
        server.shutdown(); server.server_close()


def test_dependent_parts_do_not_guess_when_prerequisite_fails(home, monkeypatch):
    """CONTROL: when the week is completely busy, the booking and the alert parts are BLOCKED
    (no slot guessed, no reminder at a made-up time); the note part, which depends on nothing,
    still completes; the reply is partial, never success and never silent."""
    from datetime import timedelta

    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        # Occupy every working hour of the week so no free slot exists for 30 minutes.
        day = (T0 + timedelta(days=1)).date()
        for offset in range(7):
            for hour in range(24):
                state.seed_event(CAL, f"wall-{offset}-{hour}@f", summary="Wall",
                                 start=T0 + timedelta(days=offset, hours=hour - T0.hour), minutes=60)
        session = "dependent-blocked"
        result = _run(
            "Check Friday for a free 30-minute slot, propose \"Retro\" in the first free slot, "
            "save a note with agenda: fallback, and remind me 15 minutes before it",
            session_id=session,
        )
        parts = {part["kind"]: part for part in result.details["parts"]}
        assert result.status == "partial", result.status
        assert parts["check_availability"]["ok"] and parts["check_availability"]["details"]["options"] == []
        assert parts["propose_calendar_event"]["status"] == "blocked", parts["propose_calendar_event"]
        assert parts["schedule_reminder"]["status"] == "blocked", parts["schedule_reminder"]
        assert parts["save_note"]["ok"], result.response_text
        assert state.snapshot()[CAL.rstrip("/") + "/"] == {} or all(
            row["summary"] == "Wall" for row in state.snapshot()[CAL.rstrip("/") + "/"].values())
        from core.operator.reminders import list_reminders
        from storage.db import get_connection

        assert list_reminders(session_id=session, get_connection_fn=get_connection) == [], "no reminder was guessed"
    finally:
        server.shutdown(); server.server_close()
