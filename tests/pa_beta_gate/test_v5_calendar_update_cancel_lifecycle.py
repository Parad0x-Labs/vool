"""pa_beta_gate -- revision-5 NOVEL cases: updates and cancellations use the same operation lifecycle.

Before revision 5 an approved update or cancellation that stopped after its claim (stale version,
taken destination, refusal, lost reply) left the approval stuck ``executing`` forever and nothing
could ever resume it. These cases drive the real SQLite approval store with a LABELLED synthetic
calendar double, plus served dispatch against the disposable CalDAV service (a local protocol
fixture, not a live account).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from core.kas.contract import CalendarRefusedError, CalEvent, TransportUnknownError
from core.operator import approvals
from core.operator import calendar_provider as cp
from core.operator.calendar_provider import CalendarProviderConfig
from core.operator.models import OperatorActionIntent
from tests.pa_beta_gate.test_calendar_provider_vertical import (
    VILNIUS_CAL,
    _provider_events,
    _run,
    vilnius_env,
)

_SESSION = "update-cancel-session"
_TABLE = ("CREATE TABLE IF NOT EXISTS operator_action_requests (action_id TEXT PRIMARY KEY, session_id TEXT, task_id TEXT, "
          "action_kind TEXT, scope_json TEXT, result_json TEXT, status TEXT, created_at TEXT, updated_at TEXT, executed_at TEXT)")
_CONFIG = CalendarProviderConfig("caldav", "https://calendar.example.test/api", "account-A", "work", "10")


def _connection(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return "2026-09-14T16:00:00+00:00"


class Calendar:
    """A LABELLED calendar double with real version semantics: writes need the current etag."""

    def __init__(self, *events: CalEvent):
        self.events = {event.uid: event for event in events}
        self.version = 1
        self.calls: list[tuple[str, str]] = []
        self.fail_mode = ""  # "lose_before" | "lose_after"

    def get_event(self, cal, uid):
        if uid not in self.events:
            raise CalendarRefusedError(404, reason="not_found")
        return self.events[uid]

    def events_in_range(self, cal, *, start_utc, end_utc):
        return list(self.events.values())

    def _maybe_fail(self, moment):
        if self.fail_mode == moment:
            self.fail_mode = ""
            raise TransportUnknownError(f"reply_lost_{moment}")

    def update_event(self, cal, event):
        self.calls.append(("update", event.etag))
        self._maybe_fail("lose_before")
        if event.etag != self.events[event.uid].etag:
            raise CalendarRefusedError(412, reason="precondition_failed")
        self.version += 1
        self.events[event.uid] = replace(event, etag=f'"v{self.version}"')
        self._maybe_fail("lose_after")
        return self.events[event.uid]

    def cancel_event(self, cal, uid, etag):
        self.calls.append(("cancel", etag))
        self._maybe_fail("lose_before")
        if uid not in self.events:
            raise CalendarRefusedError(404, reason="not_found")
        if etag != self.events[uid].etag:
            raise CalendarRefusedError(412, reason="precondition_failed")
        del self.events[uid]
        self._maybe_fail("lose_after")
        return True


def _event(uid="evt-1", title="Novel greenhouse vent check", start="2026-09-15T12:00:00+00:00", end="2026-09-15T12:30:00+00:00", etag='"v1"'):
    return CalEvent(provider_id="caldav", uid=uid, calendar_id="work", summary=title, start_utc=start, end_utc=end, tz_name="UTC", etag=etag)


def _stage(tmp_path, kind, scope):
    path = str(tmp_path / "operator.sqlite")
    with _connection(path) as conn:
        conn.execute(_TABLE)
    aid = approvals.create_pending_action(session_id=_SESSION, task_id="uc-task", action_kind=kind, scope=scope, now_fn=_now,
                                          get_connection_fn=lambda: _connection(path))
    return path, aid


def _row(path, kind, aid):
    return cp.load_action_any_state(session_id=_SESSION, action_kind=kind, action_id=aid, get_connection_fn=lambda: _connection(path))


def _approve(path, kind, aid, calendar):
    kwargs = dict(now_fn=_now, get_connection_fn=lambda: _connection(path))
    executor = cp.execute_provider_update_approval if kind == "provider_calendar_update" else cp.execute_provider_cancel_approval
    row = _row(path, kind, aid)
    return executor(
        OperatorActionIntent(kind="approve_calendar_event", action_id=aid), task_id="uc-task", session_id=_SESSION, adapter=calendar,
        config=_CONFIG, pending_row=row if row["status"] != "pending_approval" else None,
        load_pending_action_fn=lambda **k: approvals.load_pending_action(**k, get_connection_fn=lambda: _connection(path)),
        claim_action_fn=lambda key: approvals.claim_pending_action(key, **kwargs),
        mark_action_executed_fn=lambda key, **k: approvals.mark_action_executed(key, **kwargs, **k),
        mark_outcome_unproven_fn=lambda key, **k: approvals.mark_action_outcome_unproven(key, **kwargs, **k),
        mark_effect_unrecorded_fn=lambda key, **k: approvals.mark_action_effect_unrecorded(key, **kwargs, **k),
        requeue_interrupted_fn=lambda key, **k: approvals.requeue_interrupted_action(key, **kwargs, **k),
        audit_log_fn=lambda *a, **k: None,
    )


def _update_scope(event, **overrides):
    scope = {"uid": event.uid, "calendar_id": "work", "etag_seen": event.etag, "title_seen": event.summary,
             "new_title": "Novel greenhouse vent check (moved)", "new_start_utc": "2026-09-16T09:00:00+00:00", "action_ref": "create-1"}
    scope.update(overrides)
    return scope


# ---------------------------------------------------------------------------------------------
# Pre-write exits return to pending instead of sticking in executing
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("change", ["stale_version", "destination_taken"])
def test_update_that_stops_before_writing_returns_to_pending_and_can_be_approved_again(tmp_path, change):
    event = _event()
    calendar = Calendar(event)
    path, aid = _stage(tmp_path, "provider_calendar_update", _update_scope(event))
    if change == "stale_version":
        calendar.events[event.uid] = replace(event, etag='"v-edited-elsewhere"')
    else:
        calendar.events["blocker"] = _event(uid="blocker", title="Novel boiler service", start="2026-09-16T09:10:00+00:00", end="2026-09-16T09:40:00+00:00")
    refused = _approve(path, "provider_calendar_update", aid, calendar)
    assert refused.status == {"stale_version": "stale_etag", "destination_taken": "conflict"}[change], refused.response_text
    assert _row(path, "provider_calendar_update", aid)["status"] == "pending_approval" and calendar.calls == []
    again = _approve(path, "provider_calendar_update", aid, calendar)
    assert again.status == refused.status and calendar.calls == [], "a second approval re-checks and still sends nothing"


def test_cancellation_of_an_event_already_gone_returns_to_pending_without_claiming_a_cancellation(tmp_path):
    event = _event(title="Novel lab waste pickup")
    calendar = Calendar()
    path, aid = _stage(tmp_path, "provider_calendar_cancel", {"uid": event.uid, "calendar_id": "work", "etag_seen": event.etag, "title": event.summary})
    result = _approve(path, "provider_calendar_cancel", aid, calendar)
    assert result.status == "not_found" and "not reporting a cancellation" in result.response_text
    assert _row(path, "provider_calendar_cancel", aid)["status"] == "pending_approval" and calendar.calls == []


# ---------------------------------------------------------------------------------------------
# Lost replies: verified by the recorded target / absence, re-sent at most once
# ---------------------------------------------------------------------------------------------


def test_update_whose_reply_was_lost_after_applying_is_recovered_by_its_recorded_target(tmp_path):
    event = _event()
    calendar = Calendar(event)
    calendar.fail_mode = "lose_after"
    path, aid = _stage(tmp_path, "provider_calendar_update", _update_scope(event))
    first = _approve(path, "provider_calendar_update", aid, calendar)
    row = _row(path, "provider_calendar_update", aid)
    assert first.status == "outcome_unproven" and row["status"] == "outcome_unproven", first.response_text
    target = json.loads(row["result_json"])["operation"]["evidence"]["update_target"]
    assert target["title"] == "Novel greenhouse vent check (moved)" and target["end_utc"] == "2026-09-16T09:30:00+00:00"
    recovered = _approve(path, "provider_calendar_update", aid, calendar)
    assert recovered.ok and recovered.details["recovered"] is True, recovered.response_text
    assert [call[0] for call in calendar.calls] == ["update"] and _row(path, "provider_calendar_update", aid)["status"] == "executed"


def test_update_lost_before_applying_is_proven_unapplied_and_sent_once_more(tmp_path):
    event = _event(title="Novel forklift inspection")
    calendar = Calendar(event)
    calendar.fail_mode = "lose_before"
    path, aid = _stage(tmp_path, "provider_calendar_update", _update_scope(event, new_title="Novel forklift inspection (moved)"))
    assert _approve(path, "provider_calendar_update", aid, calendar).status == "outcome_unproven"
    resent = _approve(path, "provider_calendar_update", aid, calendar)
    assert resent.ok and resent.details["recovered"] is False, resent.response_text
    assert calendar.calls == [("update", '"v1"'), ("update", '"v1"')], "the re-send carried the version the user saw"
    assert calendar.events[event.uid].summary == "Novel forklift inspection (moved)"


def test_update_recovery_never_overwrites_an_event_changed_elsewhere(tmp_path):
    event = _event(title="Novel archive reshelving")
    calendar = Calendar(event)
    calendar.fail_mode = "lose_before"
    path, aid = _stage(tmp_path, "provider_calendar_update", _update_scope(event, new_title="Novel archive reshelving (moved)"))
    assert _approve(path, "provider_calendar_update", aid, calendar).status == "outcome_unproven"
    calendar.events[event.uid] = replace(event, summary="Archive reshelving (edited by a colleague)", etag='"v-colleague"')
    result = _approve(path, "provider_calendar_update", aid, calendar)
    assert result.status == "stale_etag" and len(calendar.calls) == 1, result.response_text
    assert calendar.events[event.uid].summary == "Archive reshelving (edited by a colleague)"
    row = _row(path, "provider_calendar_update", aid)
    record = json.loads(row["result_json"])["operation"]
    assert row["status"] == "outcome_unproven" and record["phase"] == "dispatching", record
    assert not any("proven_not_applied" in entry["event"] for entry in record["history"]), "recovery recorded false non-application evidence"


def test_update_accepted_earlier_is_never_resent_even_if_the_version_token_did_not_move(tmp_path):
    """Coarse version tokens (EventKit's modification date) need not move on every write: an update the
    provider accepted is never re-sent on recovery just because the version still matches."""
    event = _event(title="Novel boiler flue check")
    calendar = Calendar(event)
    path, aid = _stage(tmp_path, "provider_calendar_update", _update_scope(event, new_title="Novel boiler flue check (moved)"))
    approvals.mark_action_outcome_unproven(aid, result={"uid": event.uid, "provider_uid": event.uid, "accepted": True, "reason": "read_back_http_503"},
                                           now_fn=_now, get_connection_fn=lambda: _connection(path))
    result = _approve(path, "provider_calendar_update", aid, calendar)
    assert result.status == "identity_mismatch" and calendar.calls == [], result.response_text
    assert calendar.events[event.uid].summary == "Novel boiler flue check"
    assert _row(path, "provider_calendar_update", aid)["status"] == "outcome_unproven"


def test_cancellation_whose_reply_was_lost_after_deleting_is_recorded_with_unproven_attribution(tmp_path):
    event = _event(title="Novel coolant top-up")
    calendar = Calendar(event)
    calendar.fail_mode = "lose_after"
    path, aid = _stage(tmp_path, "provider_calendar_cancel", {"uid": event.uid, "calendar_id": "work", "etag_seen": event.etag, "title": event.summary})
    assert _approve(path, "provider_calendar_cancel", aid, calendar).status == "outcome_unproven"
    recorded = _approve(path, "provider_calendar_cancel", aid, calendar)
    assert recorded.ok and recorded.details["attribution"] == "unproven", recorded.response_text
    assert "cannot prove it was this cancellation" in recorded.response_text and len(calendar.calls) == 1
    assert json.loads(_row(path, "provider_calendar_cancel", aid)["result_json"])["gone_attribution"] == "unproven"


def test_cancellation_lost_before_deleting_is_sent_once_more_and_verified(tmp_path):
    event = _event(title="Novel ladder safety check")
    calendar = Calendar(event)
    calendar.fail_mode = "lose_before"
    path, aid = _stage(tmp_path, "provider_calendar_cancel", {"uid": event.uid, "calendar_id": "work", "etag_seen": event.etag, "title": event.summary})
    assert _approve(path, "provider_calendar_cancel", aid, calendar).status == "outcome_unproven"
    resent = _approve(path, "provider_calendar_cancel", aid, calendar)
    assert resent.ok and resent.details["attribution"] == "verified" and calendar.events == {}, resent.response_text
    assert calendar.calls == [("cancel", '"v1"'), ("cancel", '"v1"')]


def test_update_worker_that_stopped_after_recording_its_target_is_recovered_by_evidence(tmp_path):
    event = _event(title="Novel pump rotation")
    calendar = Calendar(event)
    path, aid = _stage(tmp_path, "provider_calendar_update", _update_scope(event, new_title="Novel pump rotation (moved)"))
    kwargs = dict(now_fn=_now, get_connection_fn=lambda: _connection(path))
    assert approvals.claim_pending_action(aid, **kwargs)
    target = {"title": "Novel pump rotation (moved)", "start_utc": "2026-09-16T09:00:00+00:00", "end_utc": "2026-09-16T09:30:00+00:00"}
    assert approvals.held_claim(aid).record(phase="dispatching", evidence={"uid": event.uid, "update_target": target})
    calendar.events[event.uid] = replace(event, summary=target["title"], start_utc=target["start_utc"], end_utc=target["end_utc"], etag='"v2"')
    approvals.relinquish_claim(aid)  # the worker is gone: its owner lock is released, the row says executing
    assert _row(path, "provider_calendar_update", aid)["status"] == "executing"
    recovered = _approve(path, "provider_calendar_update", aid, calendar)
    assert recovered.ok and recovered.details["recovered"] is True and calendar.calls == [], recovered.response_text


# ---------------------------------------------------------------------------------------------
# Served dispatch against the disposable CalDAV service
# ---------------------------------------------------------------------------------------------


def _served_row(session, kind, action_id):
    return cp.load_action_any_state(session_id=session, action_kind=kind, action_id=action_id)


def test_served_update_whose_reply_is_withheld_is_recovered_on_reapproval(request, monkeypatch):
    env = request.getfixturevalue("vilnius_env")
    state = env["state"]
    session = "served-update-recovery"
    _intent, proposal = _run('propose "Novel dock survey" on 2026-09-16 10:00 Europe/Athens for 30m', session_id=session)
    _intent, created = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert created.ok, created.response_text
    _intent, rename = _run('rename the "Novel dock survey" event to "Novel dock survey (tide adjusted)"', session_id=session)
    assert rename.status == "approval_required", rename.response_text
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "1")
    state.hang_seconds = 3.0
    try:
        _intent, unknown = _run(f"approve calendar {rename.details['action_id']}", session_id=session)
    finally:
        state.hang_seconds = 0.0
    assert unknown.status == "outcome_unproven", unknown.response_text
    assert _served_row(session, "provider_calendar_update", rename.details["action_id"])["status"] == "outcome_unproven"
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    _intent, recovered = _run(f"approve calendar {rename.details['action_id']}", session_id=session)
    assert recovered.ok and recovered.details["recovered"] is True, recovered.response_text
    titles = [row["summary"] for row in _provider_events(state).values()]
    assert titles.count("Novel dock survey (tide adjusted)") == 1 and "Novel dock survey" not in titles


def test_served_cancellation_whose_reply_is_withheld_is_recorded_and_the_event_stays_released(request, monkeypatch):
    env = request.getfixturevalue("vilnius_env")
    state = env["state"]
    session = "served-cancel-recovery"
    _intent, proposal = _run('propose "Novel fire door audit" on 2026-09-16 15:00 Europe/Athens for 30m', session_id=session)
    _intent, created = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert created.ok, created.response_text
    _intent, cancel = _run('cancel the "Novel fire door audit" event', session_id=session)
    assert cancel.status == "approval_required", cancel.response_text
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "1")
    state.hang_seconds = 3.0
    try:
        _intent, unknown = _run(f"approve calendar {cancel.details['action_id']}", session_id=session)
    finally:
        state.hang_seconds = 0.0
    assert unknown.status == "outcome_unproven", unknown.response_text
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    _intent, recorded = _run(f"approve calendar {cancel.details['action_id']}", session_id=session)
    assert recorded.ok and recorded.details["attribution"] == "unproven", recorded.response_text
    assert not any(row["summary"] == "Novel fire door audit" for row in _provider_events(state).values())
    _intent, replay = _run(f"approve calendar {cancel.details['action_id']}", session_id=session)
    assert replay.status == "already_executed" and "Nothing was cancelled a second time" in replay.response_text
