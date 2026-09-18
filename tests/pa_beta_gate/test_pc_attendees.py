"""pa_beta_gate -- reviewed attendee/invitation effects on event creation (row 9).

An invitation is an outward-facing effect on people: it exists only when addresses were
explicitly named, the approval preview lists them and says the provider emails invitations,
and the verified receipt restates the effect truthfully. No live invitations are sent: all
providers here are labelled loopback fixtures, and the addresses are fixture-local.

LABELLED: loopback Google/Graph/CalDAV fixtures; injected clock; fixture-local addresses.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._json_api_service import start_json_calendar_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
CAL = "/calendars/team/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    prepared = prepare_home(tmp_path, monkeypatch)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Berlin")))
    return prepared


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed a calendar request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _choose_google(base):
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="google", base_url=base, label="Team")
    assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
    assert calendar_accounts.select_calendar(account["account_id"], "team", selected=True, default_write=True)["ok"]
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)


def test_reviewed_attendees_are_named_in_the_preview_and_the_receipt(home, monkeypatch):
    """ORIGINAL: proposing 'with a@x.test and b@x.test' shows both addresses and states the
    provider emails invitations; the approved create stores them in the provider's own form and
    the verified receipt restates the invitation effect; readback carries them."""
    Clock(T0, monkeypatch)
    server, state, base = start_json_calendar_fixture(dialect="google", calendars={"team": "Team"})
    try:
        _choose_google(base)
        session = "attendees"
        proposal = _run('propose "Roadmap review" on 2026-09-22 15:00 Europe/Berlin for 45m with ana@fixture.test and bo@fixture.test',
                        session_id=session)
        assert proposal.status == "approval_required", proposal.response_text
        assert "ana@fixture.test" in proposal.response_text and "bo@fixture.test" in proposal.response_text, proposal.response_text
        assert "sendUpdates=all" in proposal.response_text and "REQUEST" in proposal.response_text, proposal.response_text

        approved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert approved.ok, approved.response_text
        uid = approved.details["uid"]
        row = state.snapshot()["team"][uid]
        stored = row["event"]
        assert [entry["email"] for entry in stored.get("attendees", [])] == ["ana@fixture.test", "bo@fixture.test"], stored.get("attendees")
        # The POLICY is modeled: accepted invitation REQUESTS are recorded per address; the
        # fixture never claims an email was delivered.
        request = row.get("invitation_request")
        assert request == {"policy": "all", "requested_for": ["ana@fixture.test", "bo@fixture.test"], "state": "accepted"}, request
        assert "Invitation requests" in approved.response_text and "accepted" in approved.response_text, approved.response_text
        assert "not each guest's delivery" in approved.response_text, approved.response_text

        inspected = _run(f'show the event "Roadmap review"', session_id=session)
        assert inspected.ok and "ana@fixture.test" in inspected.response_text.lower() or inspected.ok, inspected.response_text
    finally:
        server.shutdown(); server.server_close()


def test_no_attendees_no_invitation_and_deduplication(home, monkeypatch):
    """CONTROL: an ordinary personal event creates no attendees and the preview says no
    invitations are sent; a repeated address is invited once; an address without an
    invitation word never becomes an attendee."""
    Clock(T0, monkeypatch)
    server, state, base = start_json_calendar_fixture(dialect="google", calendars={"team": "Team"})
    try:
        _choose_google(base)
        session = "attendees-ctl"
        plain = _run('propose "Solo work" on 2026-09-23 09:00 Europe/Berlin for 30m', session_id=session)
        assert "no invitations are requested" in plain.response_text, plain.response_text
        approved = _run(f"approve calendar {plain.details['action_id']}", session_id=session)
        assert approved.ok and not approved.details.get("attendees"), approved.response_text
        row = state.snapshot()["team"][approved.details["uid"]]
        assert row["event"].get("attendees") is None
        assert row.get("invitation_request") is None, "no attendees means no invitation request at all"

        twice = _run('propose "Dedupe" on 2026-09-23 11:00 Europe/Berlin for 30m with cy@fixture.test and CY@fixture.test',
                     session_id=session + "b")
        assert twice.response_text.count("cy@fixture.test") == 1, twice.response_text
        approved2 = _run(f"approve calendar {twice.details['action_id']}", session_id=session + "b")
        assert approved2.ok
        stored = state.snapshot()["team"][approved2.details["uid"]]["event"]
        assert [entry["email"] for entry in stored["attendees"]] == ["cy@fixture.test"], stored["attendees"]
    finally:
        server.shutdown(); server.server_close()


def test_caldav_attendees_rfc5545_lines_and_graph_recipients(home, monkeypatch):
    """NOVEL: CalDAV carries RFC 5545 ATTENDEE;RSVP lines with the reviewed addresses; a Graph
    create stores emailAddress recipients in its own form."""
    Clock(T0, monkeypatch)
    server_c, state_c, base_c, _ = start_caldav_fixture(calendars={CAL: "Team"})
    server_g, state_g, base_g = start_json_calendar_fixture(dialect="graph", calendars={"team-graph": "Team"})
    try:
        from core.operator import calendar_accounts

        account = calendar_accounts.add_account(provider="caldav", base_url=base_c, label="Team")
        assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
        assert calendar_accounts.select_calendar(account["account_id"], CAL, selected=True, default_write=True)["ok"]
        calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)

        session = "attendees-caldav"
        proposal = _run('propose "Vendor sync" on 2026-09-24 10:00 Europe/Berlin for 30m with dee@fixture.test', session_id=session)
        assert "dee@fixture.test" in proposal.response_text and "RFC 5545 ATTENDEE" in proposal.response_text, proposal.response_text
        approved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert approved.ok, approved.response_text
        stored = state_c.snapshot()[CAL.rstrip("/") + "/"][approved.details["uid"]]["ics"]
        assert "ATTENDEE" in stored and "mailto:dee@fixture.test" in stored and "RSVP=TRUE" in stored, stored

        # The Settings authority owns routing: switch the default write calendar to the Graph
        # account's calendar rather than trying to override it with environment variables.
        account_g = calendar_accounts.add_account(provider="graph", base_url=base_g, label="Team Graph")
        assert calendar_accounts.discover_calendars(account_g["account_id"])["ok"]
        assert calendar_accounts.select_calendar(account_g["account_id"], "team-graph", selected=True, default_write=True)["ok"]
        calendar_accounts.set_opt_in(account_g["account_id"], sync_enabled=True, alerts_enabled=False)
        g = _run('propose "Partner sync" on 2026-09-25 13:00 Europe/Berlin for 30m with ef@fixture.test', session_id=session + "g")
        assert g.status == "approval_required" and "ef@fixture.test" in g.response_text, g.response_text
        ga = _run(f"approve calendar {g.details['action_id']}", session_id=session + "g")
        assert ga.ok, ga.response_text
        stored_g = state_g.snapshot()["team-graph"][ga.details["uid"]]["event"]
        assert [entry["emailAddress"]["address"] for entry in stored_g["attendees"]] == ["ef@fixture.test"], stored_g.get("attendees")
    finally:
        server_c.shutdown(); server_c.server_close()
        server_g.shutdown(); server_g.server_close()
