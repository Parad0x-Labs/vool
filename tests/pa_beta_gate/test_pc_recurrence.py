"""pa_beta_gate -- basic recurring-event creation and single-vs-series protection (row 8).

Creation of the supported shapes (daily, weekly on named weekdays, monthly on a day) through the
chat proposal path onto real loopback providers; a CalDAV series master is never silently edited
as if it were one occurrence; unsupported richer shapes are refused precisely.

LABELLED: loopback CalDAV and Google Calendar fixtures through the real VOOL transport.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._json_api_service import start_json_calendar_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)  # Monday 10:00 Vilnius
CAL = "/calendars/team/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture
def caldav(home, monkeypatch):
    server, state, base, _ = start_caldav_fixture(calendars={CAL: "Team"})
    Clock(T0, monkeypatch)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Berlin")))
    yield state, base
    server.shutdown(); server.server_close()


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed a calendar request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def test_weekly_series_created_on_caldav_and_scope_is_asked_before_edits(caldav):
    """ORIGINAL: 'propose "Standup" every Monday at 09:00' stages a series the preview names; the
    approved event carries the RRULE; an unnamed move of "Standup" asks which scope instead of
    rewriting the series; naming the whole series proceeds to the normal approval."""
    state, base = caldav
    session = "rec-caldav"
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="caldav", base_url=base, label="Team")
    assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
    assert calendar_accounts.select_calendar(account["account_id"], CAL, selected=True, default_write=True)["ok"]
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)

    proposal = _run('propose "Standup" on 2026-09-21 at 09:00 Europe/Berlin for 30m every Monday', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    assert "repeats weekly on Monday" in proposal.response_text, proposal.response_text
    approved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert approved.ok, approved.response_text
    uid = approved.details["uid"]
    stored = state.snapshot()[CAL.rstrip("/") + "/"][uid]["ics"]
    assert "RRULE:FREQ=WEEKLY" in stored and "BYDAY=MO" in stored, stored

    unnamed = _run('move the "Standup" event to Friday at 10:00 Europe/Berlin', session_id=session)
    assert unnamed.status == "approval_required", unnamed.response_text
    # The scope question lands BEFORE anything is sent: an approval that never named the
    # series is refused at execution and the series is untouched.
    refused = _run(f"approve calendar {unnamed.details['action_id']}", session_id=session)
    assert not refused.ok and "repeating series" in refused.response_text, refused.response_text
    assert "RRULE:FREQ=WEEKLY" in state.snapshot()[CAL.rstrip("/") + "/"][uid]["ics"], "the series was not touched"

    scoped = _run('move the whole "Standup" series to 2026-09-21 09:30 Europe/Berlin', session_id=session + "b")
    assert scoped.status == "approval_required", scoped.response_text
    scoped_ok = _run(f"approve calendar {scoped.details['action_id']}", session_id=session + "b")
    assert scoped_ok.ok, scoped_ok.response_text


def test_daily_series_created_on_google_with_provider_recurrence(caldav, monkeypatch):
    """NOVEL: a Google Calendar account creates a daily series with the provider's own
    recurrence array, and the approved read-back is the series master."""
    import os

    state_caldav, _base = caldav
    server, state, base = start_json_calendar_fixture(dialect="google", calendars={"work-graph": "Work"})
    try:
        os.environ["VOOL_CALENDAR_PROVIDER"] = "google"
        os.environ["VOOL_CALENDAR_URL"] = base
        os.environ["VOOL_CALENDAR_ID"] = "work-graph"
        session = "rec-google"
        proposal = _run('propose "Focus block" on 2026-09-22 at 08:00 Europe/Berlin for 60m every day', session_id=session)
        assert proposal.status == "approval_required", proposal.response_text
        assert "repeats every day" in proposal.response_text, proposal.response_text
        approved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert approved.ok, approved.response_text
        uid = approved.details["uid"]
        stored = state.snapshot()["work-graph"][uid]["event"]
        assert stored.get("recurrence") == ["RRULE:FREQ=DAILY"], stored.get("recurrence")
    finally:
        del os.environ["VOOL_CALENDAR_PROVIDER"]
        del os.environ["VOOL_CALENDAR_URL"]
        del os.environ["VOOL_CALENDAR_ID"]
        server.shutdown(); server.server_close()


def test_unsupported_shapes_are_refused_precisely(caldav):
    """CONTROL: a shape the vocabulary does not carry is not approximated onto the provider."""
    state, base = caldav
    session = "rec-refuse"
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="caldav", base_url=base, label="Team")
    assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
    assert calendar_accounts.select_calendar(account["account_id"], CAL, selected=True, default_write=True)["ok"]
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)
    refused = _run('propose "Review" on 2026-09-22 at 15:00 Europe/Berlin for 30m every second Tuesday', session_id=session)
    assert refused.status == "approval_required", refused.response_text  # no repeat parsed: a plain proposal
    assert "repeats" not in refused.response_text, refused.response_text
    assert not any("RRULE" in row["ics"] for row in state.snapshot()[CAL.rstrip("/") + "/"].values())


def test_graph_series_master_cannot_be_changed_by_an_unnamed_single_event_action(caldav, monkeypatch):
    """CORRECTION (review probe 3): a Graph seriesMaster carries its recurrence as JSON, not an
    RRULE string. An unnamed move/cancel of it must ask the scope question at execution, refuse
    to send, and leave the master untouched; naming the whole series proceeds."""
    import os

    state_caldav, _base = caldav
    server, state, base = start_json_calendar_fixture(dialect="graph", calendars={"work-graph": "Work"})
    try:
        os.environ["VOOL_CALENDAR_PROVIDER"] = "graph"
        os.environ["VOOL_CALENDAR_URL"] = base
        os.environ["VOOL_CALENDAR_ID"] = "work-graph"
        session = "rec-graph"
        proposal = _run('propose "Planning" on 2026-09-22 at 10:00 Europe/Berlin for 45m every Tuesday', session_id=session)
        assert proposal.status == "approval_required", proposal.response_text
        approved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert approved.ok, approved.response_text
        uid = approved.details["uid"]
        stored = state.snapshot()["work-graph"][uid]["event"]
        assert stored.get("recurrence", {}).get("pattern", {}).get("type") == "weekly", stored.get("recurrence")
        # Serve the master exactly as Graph would: a seriesMaster carrying the recurrence object.
        state.calendars["work-graph"]["events"][uid]["event"]["type"] = "seriesMaster"

        unnamed = _run('move the "Planning" event to 2026-09-23 at 10:00 Europe/Berlin', session_id=session + "b")
        assert unnamed.status == "approval_required", unnamed.response_text
        refused = _run(f"approve calendar {unnamed.details['action_id']}", session_id=session + "b")
        assert not refused.ok and "repeating series" in refused.response_text, refused.response_text
        assert state.snapshot()["work-graph"][uid]["event"].get("recurrence") is not None, "the master was not touched"

        cancel = _run('cancel the "Planning" event', session_id=session + "c")
        assert cancel.status == "approval_required", cancel.response_text
        refused_cancel = _run(f"approve calendar {cancel.details['action_id']}", session_id=session + "c")
        assert not refused_cancel.ok and "repeating series" in refused_cancel.response_text, refused_cancel.response_text
        assert uid in state.snapshot()["work-graph"], "the master still exists"

        scoped = _run('move the whole "Planning" series to 2026-09-22 11:00 Europe/Berlin', session_id=session + "d")
        assert scoped.status == "approval_required", scoped.response_text
        scoped_ok = _run(f"approve calendar {scoped.details['action_id']}", session_id=session + "d")
        assert scoped_ok.ok, scoped_ok.response_text
    finally:
        del os.environ["VOOL_CALENDAR_PROVIDER"]
        del os.environ["VOOL_CALENDAR_URL"]
        del os.environ["VOOL_CALENDAR_ID"]
        server.shutdown(); server.server_close()
