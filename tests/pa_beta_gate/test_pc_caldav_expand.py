"""pa_beta_gate -- bounded CalDAV recurrence expansion with exceptions (row 8).

A repeating CalDAV series reads as its dated occurrences (RECURRENCE-ID identity), EXDATE and
cancelled/overridden occurrences are honoured, timezone-stable, and a titled move of one
occurrence asks which one instead of touching the series master.

LABELLED: loopback CalDAV fixture (server-side expansion implemented per RFC 4791); injected clock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)  # Monday 10:00 Vilnius
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


def _master_ics(uid, summary, first_start_utc, minutes, rrule, extra=""):
    start = first_start_utc.strftime("%Y%m%dT%H%M%SZ")
    end = (first_start_utc + timedelta(minutes=minutes)).strftime("%Y%m%dT%H%M%SZ")
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Fixture//CalDAV//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:{start}\r\nDTEND:{end}\r\n"
        f"SUMMARY:{summary}\r\nRRULE:{rrule}\r\n{extra}END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def _put(state, uid, ics):
    state.put_event(CAL, uid, ics)


def _exception_ics(uid, summary, start_utc, minutes, recurrence_id, extra=""):
    start = start_utc.strftime("%Y%m%dT%H%M%SZ")
    end = (start_utc + timedelta(minutes=minutes)).strftime("%Y%m%dT%H%M%SZ")
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Fixture//CalDAV//EN\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:20260901T000000Z\r\nRECURRENCE-ID:{recurrence_id}\r\nDTSTART:{start}\r\nDTEND:{end}\r\n"
        f"SUMMARY:{summary}\r\n{extra}END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def _series_resource(master_ics, *exception_ics):
    """ONE calendar object resource holding a series master and its RECURRENCE-ID exceptions. RFC 4791 section 4.1 keeps
    every component sharing a UID in the same resource, and the fixture refuses a second resource carrying that UID."""
    blocks = [ics[ics.index("BEGIN:VEVENT"):ics.index("END:VEVENT") + len("END:VEVENT")] for ics in (master_ics, *exception_ics)]
    return master_ics[:master_ics.index("BEGIN:VEVENT")] + "\r\n".join(blocks) + "\r\nEND:VCALENDAR\r\n"


def _components(ics):
    """(the master VEVENT, [each RECURRENCE-ID exception VEVENT]) of one stored resource, as text."""
    blocks = []
    rest = ics
    while "BEGIN:VEVENT" in rest:
        start = rest.index("BEGIN:VEVENT")
        end = rest.index("END:VEVENT", start) + len("END:VEVENT")
        blocks.append(rest[start:end])
        rest = rest[end:]
    masters = [block for block in blocks if "RECURRENCE-ID" not in block]
    assert len(masters) == 1, blocks
    return masters[0], [block for block in blocks if "RECURRENCE-ID" in block]


def test_weekly_series_reads_as_dated_occurrences_with_exdate_and_override(home, monkeypatch):
    """ORIGINAL: a weekly Monday series expands to one row per occurrence in the week's agenda;
    an EXDATE'd Monday is a hole; an overridden occurrence carries its own content; the master's
    own DTSTART alone never bounds the read."""
    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        first = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)  # Mondays 14:00 UTC
        # One overridden occurrence -- moved later that Monday, same UID + RECURRENCE-ID -- kept in the series' own
        # resource as RFC 4791 section 4.1 requires (correction 3: it was a second resource sharing the UID).
        _put(state, "series@f", _series_resource(
            _master_ics("series@f", "Standup", first, 30, "FREQ=WEEKLY;BYDAY=MO", extra="EXDATE:20261005T140000Z\r\n"),
            _exception_ics("series@f", "Standup", datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc), 30, "20260928T140000Z")))

        week = _run("what's on my calendar this week", session_id="expand")
        assert week.ok, week.response_text
        standups = [row for row in week.details["events"] if row["summary"] == "Standup"]
        assert [row["start_utc"][:10] for row in standups] == ["2026-09-21"], (standups, week.response_text)

        following = _run("agenda from 2026-09-28 to 2026-10-04", session_id="expand")
        assert following.ok, following.response_text
        overridden = [row for row in following.details["events"] if row["summary"] == "Standup"]
        assert [row["start_utc"][:10] for row in overridden] == ["2026-09-28"], following.response_text
        assert "T16" in overridden[0]["start_utc"], overridden  # the override's own later time that Monday

        next_week = _run("agenda from 2026-10-05 to 2026-10-11", session_id="expand")
        holes = [row for row in next_week.details["events"] if row["summary"] == "Standup"]
        assert holes == [], next_week.response_text  # the EXDATE'd Monday is a hole, not an event

        later = _run("agenda from 2026-10-12 to 2026-10-18", session_id="expand")
        resumed = [row for row in later.details["events"] if row["summary"] == "Standup"]
        assert [row["start_utc"][:10] for row in resumed] == ["2026-10-12"], later.response_text
    finally:
        server.shutdown(); server.server_close()


def test_cancelled_override_is_a_hole_and_single_occurrence_move_asks(home, monkeypatch):
    """NOVEL: a cancelled occurrence (same UID, RECURRENCE-ID, STATUS:CANCELLED) disappears from
    the agenda; a titled move of the series asks WHICH occurrence, naming dates, and mutates
    nothing until one is named; moving the whole series still names its scope."""
    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        first = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)  # Tuesdays
        # The cancelled occurrence lives in the series' own resource (RFC 4791 section 4.1; correction 3 translation).
        cancelled = _exception_ics("series@f", "Retro", first + timedelta(weeks=1), 60, "20260929T090000Z", extra="STATUS:CANCELLED\r\n")
        _put(state, "series@f", _series_resource(_master_ics("series@f", "Retro", first, 60, "FREQ=WEEKLY;BYDAY=TU"), cancelled))

        week = _run("what's on my calendar this week", session_id="expand2")
        retros = [row for row in week.details["events"] if row["summary"] == "Retro"]
        assert [row["start_utc"][:10] for row in retros] == ["2026-09-22"], week.response_text

        following = _run("agenda from 2026-09-28 to 2026-10-04", session_id="expand2")
        assert [row for row in following.details["events"] if row["summary"] == "Retro"] == [], following.response_text

        # A titled move across the expanded series resolves occurrences, not the master.
        move = _run('move the "Retro" event to 2026-09-22 11:00 Europe/Athens', session_id="expand2b")
        assert move.status in {"invalid_request", "ambiguous", "approval_required"}, move.response_text
        if move.status == "approval_required":
            refused = _run(f"approve calendar {move.details['action_id']}", session_id="expand2b")
            assert not refused.ok and ("repeating series" in refused.response_text or "Several events" in refused.response_text), refused.response_text
        master_ics = state.snapshot()[CAL.rstrip("/") + "/"]["series@f"]["ics"]
        assert "RRULE:FREQ=WEEKLY" in master_ics and "DTSTART:20260922T090000Z" in master_ics, master_ics
    finally:
        server.shutdown(); server.server_close()


def _master(uid, summary, first, minutes, rrule):
    start = first.strftime("%Y%m%dT%H%M%SZ")
    end = (first + timedelta(minutes=minutes)).strftime("%Y%m%dT%H%M%SZ")
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Fixture//CalDAV//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20260901T000000Z\r\nDTSTART:{start}\r\nDTEND:{end}\r\n"
            f"SUMMARY:{summary}\r\nRRULE:{rrule}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def test_move_one_named_occurrence_preserves_series(home, monkeypatch):
    """CORRECTION-2 (row 8): 'move the Retro on <date>' changes exactly that occurrence via a
    RECURRENCE-ID override: the master keeps its RRULE and DTSTART, the other occurrences keep
    their times, and the moved occurrence reads at its new time; a series-wide move afterwards
    still requires its own named scope."""
    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        first = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)  # Tuesdays 09:00Z
        state.put_event(CAL, "retro@f", _master("retro@f", "Retro", first, 60, "FREQ=WEEKLY;BYDAY=TU"))

        session = "occ-move"
        proposal = _run('move the "Retro" event to 2026-09-29 13:00 Europe/Athens (the 2026-09-29 occurrence)',
                        session_id=session)
        assert proposal.status == "approval_required", proposal.response_text
        assert "2026-09-29" in proposal.response_text, proposal.response_text  # names the dated occurrence
        moved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert moved.ok, moved.response_text

        # Correction 3 translation: the exception is a component INSIDE the series resource (RFC 4791 section 4.1), not a
        # second resource keyed 'uid#recurrence-id'. Same demands, stronger: the master is byte-for-byte as seeded.
        rows = state.snapshot()[CAL.rstrip("/") + "/"]
        assert list(rows) == ["retro@f"], rows.keys()
        master, overrides = _components(rows["retro@f"]["ics"])
        assert "RRULE:FREQ=WEEKLY" in master and "DTSTART:20260922T090000Z" in master, master  # master untouched
        assert master == _components(_master("retro@f", "Retro", first, 60, "FREQ=WEEKLY;BYDAY=TU"))[0], master
        assert len(overrides) == 1, overrides
        override_ics = overrides[0]
        assert "RECURRENCE-ID:20260929T090000Z" in override_ics, override_ics
        assert "DTSTART:20260929T100000Z" in override_ics, override_ics  # 13:00 Europe/Athens

        following = _run("agenda from 2026-09-28 to 2026-10-04", session_id=session + "b")
        retros = [row for row in following.details["events"] if row["summary"] == "Retro"]
        assert [row["start_utc"] for row in retros] == ["2026-09-29T10:00:00+00:00"], retros  # moved; the week's other
        later = _run("agenda from 2026-10-05 to 2026-10-11", session_id=session + "c")         # occurrence is Oct 6
        assert [row["start_utc"] for row in later.details["events"] if row["summary"] == "Retro"] == ["2026-10-06T09:00:00+00:00"], later.response_text
    finally:
        server.shutdown(); server.server_close()


def test_cancel_one_named_occurrence_is_a_hole_series_intact(home, monkeypatch):
    """NOVEL: cancelling the dated occurrence leaves a STATUS:CANCELLED override; the agenda
    shows a hole for that date while neighbouring occurrences and the master survive; an
    unnamed whole-series cancel still asks its scope question."""
    Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        _setup(base)
        first = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
        state.put_event(CAL, "retro@f", _master("retro@f", "Retro", first, 60, "FREQ=WEEKLY;BYDAY=TU"))

        session = "occ-cancel"
        cancel = _run('cancel the "Retro" event on 2026-10-06', session_id=session)
        assert cancel.status == "approval_required", cancel.response_text
        cancelled = _run(f"approve calendar {cancel.details['action_id']}", session_id=session)
        assert cancelled.ok, cancelled.response_text

        # Correction 3 translation: the cancelled exception is a component INSIDE the series resource; same demands.
        rows = state.snapshot()[CAL.rstrip("/") + "/"]
        assert list(rows) == ["retro@f"], rows.keys()
        master, overrides = _components(rows["retro@f"]["ics"])
        assert len(overrides) == 1
        assert "STATUS:CANCELLED" in overrides[0] and "RECURRENCE-ID:20261006T090000Z" in overrides[0], overrides
        assert "RRULE:FREQ=WEEKLY" in master and "STATUS:CANCELLED" not in master, "the master survives"

        hole = _run("agenda from 2026-10-05 to 2026-10-11", session_id=session + "b")
        assert [row for row in hole.details["events"] if row["summary"] == "Retro"] == [], hole.response_text
        before = _run("agenda from 2026-09-28 to 2026-10-04", session_id=session + "c")
        assert [row["start_utc"][:10] for row in before.details["events"] if row["summary"] == "Retro"] == ["2026-09-29"], before.response_text

        unnamed = _run('cancel the "Retro" event', session_id=session + "d")
        assert not unnamed.ok, unnamed.response_text  # an unnamed multi-occurrence target asks, never guesses
        text = unnamed.response_text
        assert "Tell me which one" in text or "repeating series" in text, text
        assert "2026-09-29" in text or "uid retro@f" in text, text  # the choices name dated occurrences
    finally:
        server.shutdown(); server.server_close()
