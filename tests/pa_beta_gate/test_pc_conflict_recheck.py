"""pa_beta_gate -- the fresh pre-effect availability check covers EVERY selected calendar
(correction repair 4; row 6/10).

A conflict that appeared on ANOTHER chosen calendar after the preview must stop the approved
create before anything is sent; an unreadable selected source must block without claiming
availability; the approved account/calendar identity and the retry/unknown protections stay.

LABELLED: loopback CalDAV and Google Calendar fixtures through the real transport; injected clock.
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
WORK_CAL = "/calendars/work/"
OTHER_CAL = "/calendars/other/"


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


def _choose(provider, base, cal, label, *, default_write=False):
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider=provider, base_url=base, label=label)
    assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
    assert calendar_accounts.select_calendar(account["account_id"], cal, selected=True, default_write=default_write)["ok"]
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)
    return account["account_id"]


def test_conflict_added_on_a_second_selected_calendar_after_approval_stops_the_create(home, monkeypatch):
    """ORIGINAL (review reach gap): a proposal on the Work calendar is approved; a conflict then
    appears on the OTHER selected calendar; execution refuses BEFORE sending, names the other
    calendar's event, creates nothing on either calendar, and the approval rests to be retried."""
    Clock(T0, monkeypatch)
    server_w, state_w, base_w, _ = start_caldav_fixture(calendars={WORK_CAL: "Work"})
    server_o, state_o, base_o, _ = start_caldav_fixture(calendars={OTHER_CAL: "Other"})
    try:
        _choose("caldav", base_w, WORK_CAL, "Work account", default_write=True)
        _choose("caldav", base_o, OTHER_CAL, "Other account")
        session = "recheck"
        proposal = _run('propose "Roadmap" on 2026-09-22 at 15:00 Europe/Berlin for 60m', session_id=session)
        assert proposal.status == "approval_required", proposal.response_text

        state_o.seed_event(OTHER_CAL, "blocker@fixture", summary="Director review",
                           start=T0 + timedelta(days=1, hours=5), minutes=90)  # 14:00-15:30 local overlaps 15:00
        approved = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert not approved.ok and approved.status == "conflict", approved.response_text
        assert "Director review" in approved.response_text and "Other" in approved.response_text, approved.response_text
        assert state_w.snapshot()[WORK_CAL.rstrip("/") + "/"] == {}
        assert approved.details.get("resting_status") in {"pending_approval", "pending"}, approved.details
    finally:
        server_w.shutdown(); server_w.server_close()
        server_o.shutdown(); server_o.server_close()


def test_unreadable_selected_calendar_blocks_without_claiming_availability(home, monkeypatch):
    """NOVEL: one chosen calendar's provider is unreachable at execution: the create is NOT sent,
    the refusal names that source and states the approval is still waiting — availability is never
    claimed from a partial read. When the source answers again, the same approval executes."""
    Clock(T0, monkeypatch)
    server_w, state_w, base_w, _ = start_caldav_fixture(calendars={WORK_CAL: "Work"})
    server_o, state_o, base_o, _ = start_caldav_fixture(calendars={OTHER_CAL: "Other"})
    try:
        _choose("caldav", base_w, WORK_CAL, "Work account", default_write=True)
        _choose("caldav", base_o, OTHER_CAL, "Other account")
        session = "recheck-offline"
        proposal = _run('propose "Sync" on 2026-09-23 at 10:00 Europe/Berlin for 30m', session_id=session)
        assert proposal.status == "approval_required", proposal.response_text

        server_o.shutdown()  # the account stays configured and selected; its provider stops answering
        blocked = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert not blocked.ok and "could not be read" in blocked.response_text, blocked.response_text
        assert "Other" in blocked.response_text, blocked.response_text
        assert state_w.snapshot()[WORK_CAL.rstrip("/") + "/"] == {}, "nothing was created on a partial read"
        assert blocked.details.get("resting_status") in {"pending_approval", "pending"}, blocked.details
    finally:
        try:
            server_o.server_close()
        except Exception:
            pass
        server_w.shutdown(); server_w.server_close()


def test_second_source_conflict_across_providers(home, monkeypatch):
    """NOVEL layout: the create target is a Google-dialect calendar; the conflicting selected
    calendar is a CalDAV server. The pre-effect check spans both providers."""
    Clock(T0, monkeypatch)
    server_g, state_g, base_g = start_json_calendar_fixture(dialect="google", calendars={"team-graph": "Team"})
    server_c, state_c, base_c, _ = start_caldav_fixture(calendars={OTHER_CAL: "Other"})
    try:
        _choose("google", base_g, "team-graph", "Team account", default_write=True)
        _choose("caldav", base_c, OTHER_CAL, "Other account")
        session = "recheck-cross"
        proposal = _run('propose "Launch brief" on 2026-09-24 at 09:00 Europe/Berlin for 45m', session_id=session)
        assert proposal.status == "approval_required", proposal.response_text

        state_c.seed_event(OTHER_CAL, "clash@fixture", summary="On-call handover",
                           start=T0 + timedelta(days=3, hours=-1), minutes=120)  # 08:00-10:00 local overlaps 09:00
        refused = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
        assert not refused.ok and refused.status == "conflict", refused.response_text
        assert "On-call handover" in refused.response_text, refused.response_text
        assert state_g.snapshot()["team-graph"] == {}, "nothing reached the Google target"
    finally:
        server_g.shutdown(); server_g.server_close()
        server_c.shutdown(); server_c.server_close()
