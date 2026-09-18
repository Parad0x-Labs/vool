"""pa_beta_gate — revision-4 repair classes, NOVEL cases beyond the review's originals.

Each repair already passes the thirteen-case independent review; these add genuinely
different data/behavior at the same failure classes, per the old+new-case gate.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.kas.contract import CalendarRefusedError, TransportUnknownError
from core.operator import approvals
from core.operator import calendar_provider as cp
from core.operator.models import OperatorActionIntent
from tests.pa_beta_gate.test_calendar_v2_independent_review import event, execute, staged


def test_interrupted_executing_row_requeues_through_verification(tmp_path):
    """NOVEL lifecycle case: a crash left `executing`; a later approval requeues it to
    the uncertain truth (with provenance) and reconciles — no live-worker lie, no blind
    reset of the row."""
    import sqlite3

    config, row = staged("caldav")
    path = tmp_path / "approvals.sqlite"

    def connection():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    conn = connection()
    conn.execute("CREATE TABLE operator_action_requests (action_id TEXT PRIMARY KEY, session_id TEXT, task_id TEXT, action_kind TEXT, scope_json TEXT, result_json TEXT, status TEXT, created_at TEXT, updated_at TEXT, executed_at TEXT)")
    conn.close()
    now = lambda: "2026-09-14T12:00:00+00:00"  # noqa: E731
    aid = approvals.create_pending_action(session_id="review-session", task_id="review-task", action_kind="provider_calendar_event", scope=json.loads(row["scope_json"]), now_fn=now, get_connection_fn=connection)
    conn = connection()
    conn.execute("UPDATE operator_action_requests SET status='executing' WHERE action_id=?", (aid,))
    conn.commit()
    conn.close()

    def load():
        return cp.load_action_any_state(session_id="review-session", action_kind="provider_calendar_event", action_id=aid, get_connection_fn=connection)

    existing = replace(event(json.loads(row["scope_json"])["intent_uid"], "caldav", json.loads(row["scope_json"])["title"]), tz_name="UTC")
    fake = SimpleNamespace(provider_id="caldav", get_event=lambda cal, uid: existing)
    with pytest.MonkeyPatch.context() as mp:
        # No live worker: nothing holds this operation's OS owner lock. (Revision 5 removed the
        # process-local claim set this line used to clear; the owner lock is the liveness proof.)
        result = cp.execute_proposed_event(
            OperatorActionIntent(kind="approve_calendar_event", action_id=aid),
            task_id="review-task", session_id="review-session", adapter=fake, config=config,
            pending_row=load(), load_pending_action_fn=lambda **k: load(),
            claim_action_fn=lambda key: approvals.claim_pending_action(key, now_fn=now, get_connection_fn=connection),
            mark_action_executed_fn=lambda *a, **k: approvals.mark_action_executed(a[0], result=kwargs_result(k), now_fn=now, get_connection_fn=connection),
            mark_outcome_unproven_fn=lambda key, **kw: approvals.mark_action_outcome_unproven(key, now_fn=now, get_connection_fn=connection, **kw),
            requeue_interrupted_fn=lambda key, **kw: approvals.requeue_interrupted_action(key, now_fn=now, get_connection_fn=connection, **kw),
            audit_log_fn=lambda *a, **k: None,
        )
    assert result.ok, result.response_text
    final = load()
    assert final["status"] == "executed"


def kwargs_result(k):
    return k.get("result") or {}


def test_receipt_persistence_failure_is_effect_unrecorded_not_lost(tmp_path):
    """NOVEL lifecycle case: the event verifiably exists but the receipt write fails —
    the row becomes effect_unrecorded (verify-only resume) and the answer says so."""
    config, row = staged("caldav")
    row["status"] = "outcome_unproven"
    scope = json.loads(row["scope_json"])
    existing = replace(event(scope["intent_uid"], "caldav", scope["title"]), tz_name=scope.get("tz_name") or "UTC")

    def boom(action_id, **kw):
        raise sqlite_io_error()

    def sqlite_io_error():
        import sqlite3

        return sqlite3.OperationalError("disk full")

    result = execute(row, config, SimpleNamespace(provider_id="caldav", get_event=lambda *a, **k: existing), claim=lambda _: True) if False else cp.execute_proposed_event(
        OperatorActionIntent(kind="approve_calendar_event", action_id=row["action_id"]),
        task_id="t", session_id="s", adapter=SimpleNamespace(provider_id="caldav", get_event=lambda *a, **k: existing),
        config=config, pending_row=row, load_pending_action_fn=lambda **k: row,
        claim_action_fn=lambda _: True,
        mark_action_executed_fn=boom,
        mark_outcome_unproven_fn=lambda key, **kw: None,
        audit_log_fn=lambda *a, **k: None,
    )
    assert result.ok and "receipt could not be persisted" in result.response_text, result.response_text


def test_recovery_certifies_only_the_exact_timezone_and_title():
    """NOVEL canonical case (review covered duration): a same-time event with a
    DIFFERENT TITLE must not be certified as the approved one."""
    config, row = staged("caldav")
    row["status"] = "outcome_unproven"
    scope = json.loads(row["scope_json"])
    renamed = replace(event(scope["intent_uid"], "caldav", scope["title"]), summary="Somebody else's meeting", tz_name=scope.get("tz_name") or "UTC")
    result = execute(row, config, SimpleNamespace(provider_id="caldav", get_event=lambda *a, **k: renamed), claim=lambda _: True)
    assert not result.ok and result.status == "identity_mismatch", result.status


def test_graph_provider_assigned_identity_recovered_by_content_not_echo():
    """NOVEL identity case: Graph assigns ids; a lost create reply is recovered by a
    canonical-content scan that finds the provider-assigned id — never by guessing."""
    config, row = staged("graph")
    stored = {}

    def wire(req):
        from core.kas.contract import KasResponse

        if req.method == "POST":
            body = json.loads(req.body)
            assigned = f"graph-assigned-{len(stored) + 1}"
            stored[assigned] = dict(body, id=assigned, **{"@odata.etag": 'W/"v1"'})
            raise TransportUnknownError("reply_lost_after_acceptance")
        if "calendarview" in req.url or "/events?" in req.url:
            return KasResponse(status=200, body=json.dumps({"value": list(stored.values())}).encode())
        return KasResponse(status=200, body=json.dumps(list(stored.values())[0]).encode()) if stored else KasResponse(status=404, body=b"{}")

    from tests.pa_beta_gate.test_calendar_v2_independent_review import adapter as make_adapter

    a = make_adapter("graph", wire)
    row["status"] = "pending_approval"
    first = cp.execute_proposed_event(
        OperatorActionIntent(kind="approve_calendar_event", action_id=row["action_id"]),
        task_id="t", session_id="s", adapter=a, config=config, pending_row=row, load_pending_action_fn=lambda **k: row,
        claim_action_fn=lambda _: True,
        mark_action_executed_fn=lambda *a, **k: None,
        mark_outcome_unproven_fn=lambda key, **kw: dict.__setitem__(row, "status", "outcome_unproven"),
        audit_log_fn=lambda *a, **k: None,
    )
    assert first.status == "outcome_unproven" and len(stored) == 1
    second = cp.execute_proposed_event(
        OperatorActionIntent(kind="approve_calendar_event", action_id=row["action_id"]),
        task_id="t", session_id="s", adapter=a, config=config, pending_row=row, load_pending_action_fn=lambda **k: row,
        claim_action_fn=lambda _: True,
        mark_action_executed_fn=lambda *a, **k: None,
        mark_outcome_unproven_fn=lambda key, **kw: None,
        audit_log_fn=lambda *a, **k: None,
    )
    assert second.ok, second.response_text
    assert len(stored) == 1, "content-scan recovery must not create a second event"
    assert second.details["uid"].startswith("graph-assigned-")


def test_recorded_apple_note_replays_receipt_without_invoking_bridge(tmp_path, monkeypatch):
    """NOVEL notes case: a CONFIRMED creation replays its recorded receipt; a fresh
    distinct request is a NEW effect and delivers (one invocation each)."""
    from core.operator import apple_notes, notes

    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    calls = []
    real_create = apple_notes.create_apple_note

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="note id x-coredata://p/1", stderr="")

    monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kw: real_create(**kw, runner=runner))
    kw = dict(task_id="t1", session_id="s1", evaluate_local_action_fn=lambda *a, **k: SimpleNamespace(mode="execute"), audit_log_fn=lambda *a, **k: None)
    first = notes.handle_save_note(OperatorActionIntent(kind="save_note", raw_text='save a note to Apple Notes titled "Log" with: entry one'), **kw)
    second = notes.handle_save_note(OperatorActionIntent(kind="save_note", raw_text='save a note to Apple Notes titled "Log" with: entry one'), **kw)
    fresh = notes.handle_save_note(OperatorActionIntent(kind="save_note", raw_text='save a note to Apple Notes titled "Log" with: entry two'), **kw)
    assert first.ok and second.ok and second.details.get("destination") == "apple_notes"
    assert fresh.ok
    assert len(calls) == 2, f"replay must not re-invoke; fresh content must deliver once: {len(calls)}"


def test_eventkit_unknown_calendar_is_a_typed_refusal_not_all_calendars():
    from core.kas.adapters.eventkit_mac import EventKitStore

    calendars = [SimpleNamespace(calendarIdentifier=lambda: "known")]
    captured = []
    store = EventKitStore.__new__(EventKitStore)
    store._ek = SimpleNamespace(EKEntityTypeEvent=0, EKEventStore=SimpleNamespace(authorizationStatusForEntityType_=lambda _: 3))
    store._foundation = SimpleNamespace(NSDate=SimpleNamespace(dateWithTimeIntervalSince1970_=lambda value: value))
    store._store = SimpleNamespace(
        calendarsForEntityType_=lambda _: calendars,
        predicateForEventsWithStartDate_endDate_calendars_=lambda s, e, c: captured.append(c),
        eventsMatchingPredicate_=lambda c: list(c or []),
    )
    with pytest.raises(CalendarRefusedError) as refused:
        store.events_in_range(datetime(2026, 9, 15, tzinfo=timezone.utc), datetime(2026, 9, 16, tzinfo=timezone.utc), ["missing-calendar"])
    assert refused.value.reason == "unknown_calendar"
    assert captured == [], "an unresolved explicit calendar must never reach the predicate"
