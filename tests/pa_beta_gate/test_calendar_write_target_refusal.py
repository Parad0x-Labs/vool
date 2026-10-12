"""pa_beta_gate -- a proposal with calendars connected but no "new events" calendar says so.

With two Settings CalDAV accounts read and selected (test_pc_agenda's fixtures) and no calendar
chosen for new events, a proposal used to answer "no calendar provider is configured" and point
at environment variables. It must name the real missing choice and where to make it; once the
choice is made the same words stage the event for approval.

LABELLED: disposable loopback CalDAV fixtures; injected clock; no live provider.
"""
from __future__ import annotations

import pytest

from ._pc_calendar_rig import Clock
from .test_pc_agenda import T0, _run, home, servers

pytestmark = [pytest.mark.pa_beta]

WORDS = "propose a 30 minute meeting called Budget check today at 15:00"


def test_no_new_events_calendar_is_named_as_the_missing_choice(servers, monkeypatch):
    Clock(T0, monkeypatch)
    state_a, _ = servers
    before = sorted(state_a.calendars["/calendars/work/"]["events"])
    refused = _run(WORDS, session_id="no-write-target")
    assert not refused.ok and refused.details.get("reason") == "missing_default_write_calendar", refused.response_text
    assert "Use for new events" in refused.response_text and "no calendar provider" not in refused.response_text
    assert sorted(state_a.calendars["/calendars/work/"]["events"]) == before

    from core.operator import calendar_accounts

    [work] = [a for a in calendar_accounts.list_accounts() if a["label"] == "Work account"]
    assert calendar_accounts.select_calendar(work["account_id"], "/calendars/work/", selected=True, default_write=True)["ok"]
    staged = _run(WORDS, session_id="no-write-target")
    assert "approve calendar" in staged.response_text.lower(), staged.response_text
    assert sorted(state_a.calendars["/calendars/work/"]["events"]) == before  # staged, not written


def test_reads_still_work_without_a_new_events_calendar(servers, monkeypatch):
    Clock(T0, monkeypatch)
    result = _run("Check today for a free 30-minute slot", session_id="reads-ok")
    assert result.ok and result.details.get("options"), result.response_text


def test_nothing_connected_points_at_settings(home, monkeypatch):
    monkeypatch.delenv("VOOL_CALENDAR_PROVIDER", raising=False)
    monkeypatch.delenv("VOOL_CALENDAR_URL", raising=False)
    Clock(T0, monkeypatch)
    refused = _run(WORDS, session_id="nothing")
    assert not refused.ok and refused.details.get("reason") == "missing_calendar_configuration", refused.response_text
    assert "Settings" in refused.response_text
