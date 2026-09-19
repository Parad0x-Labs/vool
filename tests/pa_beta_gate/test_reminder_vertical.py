"""pa_beta_gate — the scheduling vertical: durable reminders + calendar-draft lifecycle.

Covers the vertical end to end through the REAL production seams: the operator intent
parser, the wired dispatch (core.local_operator_actions, the same functions the turn
fast path calls), the durable store, and the dispatcher's state machine. Everything is
deterministic: fixed clocks, injected delivery.

The honest effect contract under test: a delivered reminder is a durable ``delivered``
row with a receipt, an audit entry, and an event in the owning session's conversation
log. It is NOT an OS push notification and NOT an external message. Crash-interrupted
dispatches become ``delivery_uncertain`` and are reported, never re-fired silently and
never claimed delivered — exactly-once delivery is never promised.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from core.local_operator_actions import (
    dispatch_operator_action,
    operator_capability_ledger,
    parse_operator_action_intent,
)
from core.operator import reminders as rem
from core.operator.reminder_dispatcher import ReminderDispatcher
from core.operator.when import parse_when_expression
from storage.db import get_connection
from storage.migrations import run_migrations

pytestmark = [pytest.mark.pa_beta]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    run_migrations()
    from core.user_preferences import save_user_timezone
    assert save_user_timezone('Europe/Athens')
    return tmp_path


def _sid(label: str) -> str:
    return f"pa-rem-{label}-{uuid.uuid4().hex[:8]}"


def _run_turn(text: str, *, session_id: str):
    """The exact seam the served turn fast path uses: parse + wired dispatch."""
    intent = parse_operator_action_intent(text)
    assert intent is not None, f"operator parser missed a scheduling request: {text!r}"
    return intent, dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _rows(session_id: str, *, include_delivered: bool = True) -> list[dict]:
    return rem.list_reminders(
        session_id=session_id, include_delivered=include_delivered, get_connection_fn=get_connection
    )


def _fixed_now(**kwargs) -> datetime:
    base = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(**kwargs)


def test_delivery_is_a_typed_transcript_artifact_not_a_fabricated_turn(isolated_home):
    import json

    from core.operator.reminder_dispatcher import _deliver_to_session_log
    from core.persistent_memory import recent_conversation_events
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    sid = _sid("visible-delivery")
    rid = str(uuid.uuid4())
    result = _deliver_to_session_log({"session_id": sid, "reminder_id": rid,
        "note": "check the saffron inventory", "due_wall": "2026-09-12T12:00:00+00:00"})
    assert result["ok"]
    response = dispatch_get(path="/api/chat/history", query={"session": [sid]},
        runtime=RuntimeServices(display_name="VOOL"), model_name="vool", client_host="127.0.0.1")
    rows = json.loads(response.body)["messages"]
    assert len(rows) == 1 and rows[0]["role"] == "assistant"
    assert rows[0].get("artifact") == {"kind": "reminder_delivery", "reminder_id": rid, "status": "delivered"}
    assert recent_conversation_events(sid) == [], "background delivery must not invent a dialogue turn"


class _Collector:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def __call__(self, row: dict) -> dict:
        self.events.append((str(row.get("note")), str(row.get("delivery_status") or "delivered")))
        return {"ok": True, "effect": "test_collector"}

    def notes(self) -> list[str]:
        return [note for note, _ in self.events]


# ---------------------------------------------------------------------------
# The when-parser: named timezone, absolute/relative, boundaries, DST honesty
# ---------------------------------------------------------------------------


def test_when_absolute_with_named_timezone():
    result = parse_when_expression(
        "remind me on 2026-09-14 09:30 Europe/Athens", now_fn=lambda: _fixed_now()
    )
    assert result.ok, result.problem
    assert result.tz_name == "Europe/Athens"
    assert result.due_at_utc.startswith("2026-09-14T06:30:00")  # 09:30+03:00
    assert result.due_wall.startswith("2026-09-14T09:30")


def test_when_explicit_zone_beats_host_zone():
    result = parse_when_expression(
        "2026-12-01 08:00 America/New_York", now_fn=lambda: _fixed_now()
    )
    assert result.ok and result.tz_name == "America/New_York"
    # 08:00 EST is 13:00 UTC -- not the host zone's offset.
    assert result.due_at_utc.startswith("2026-12-01T13:00:00")


def test_when_relative_and_day_words():
    now = _fixed_now()
    r1 = parse_when_expression("in 20 minutes", now_fn=lambda: now)
    assert r1.ok and r1.kind == "relative"
    assert datetime.fromisoformat(r1.due_at_utc) == now + timedelta(minutes=20)

    r2 = parse_when_expression("tomorrow at 9:00", now_fn=lambda: now)
    assert r2.ok
    assert datetime.fromisoformat(r2.due_at_utc) == (now + timedelta(days=1)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )

    r3 = parse_when_expression("next monday at 8:15 pm", now_fn=lambda: now)
    assert r3.ok
    due = datetime.fromisoformat(r3.due_at_utc)
    assert (due.weekday() == 0 and due > now) and due.hour == 20 and due.minute == 15


def test_when_month_and_year_boundary():
    result = parse_when_expression("2026-12-31 23:59", now_fn=lambda: _fixed_now())
    assert result.ok and result.due_at_utc.startswith("2026-12-31")
    result2 = parse_when_expression("2027-01-01 00:01", now_fn=lambda: _fixed_now())
    assert result2.ok and result2.due_at_utc.startswith("2027-01-01")


def test_when_dst_gap_is_refused_with_a_clarification_not_a_guess():
    # In Europe/Athens, 2026-03-29 03:30 does not exist (clocks jump 03:00 -> 04:00).
    result = parse_when_expression(
        "2026-03-29 03:30 Europe/Athens", now_fn=lambda: _fixed_now()
    )
    assert not result.ok
    assert "does not exist" in result.problem


def test_when_dst_fold_requires_an_unambiguous_choice():
    # In Europe/Athens, 2026-10-25 03:30 happens twice (04:00 CEST -> 03:00 EET).
    result = parse_when_expression(
        "2026-10-25 03:30 Europe/Athens", now_fn=lambda: _fixed_now()
    )
    assert not result.ok and "occurs twice" in result.problem
    assert result.details.get("dst") == "ambiguous"
    assert result.due_at_utc == "" and result.due_wall == ""


def test_when_missing_time_and_invalid_date_ask_for_clarification():
    r1 = parse_when_expression("remind me tomorrow", now_fn=lambda: _fixed_now())
    assert not r1.ok and "time" in r1.problem.lower()
    r2 = parse_when_expression("2026-02-30 10:00", now_fn=lambda: _fixed_now())
    assert not r2.ok
    r3 = parse_when_expression("hello there", now_fn=lambda: _fixed_now())
    assert not r3.ok and r3.problem


# ---------------------------------------------------------------------------
# Schedule / list / move / cancel through the real dispatch seam
# ---------------------------------------------------------------------------


def test_reminder_lifecycle_schedule_list_move_cancel(isolated_home):
    sid = _sid("lifecycle")

    _, created = _run_turn(
        "remind me to submit the visa application tomorrow at 9:30 Europe/Athens",
        session_id=sid,
    )
    assert created.ok and created.status == "executed"
    reminder_id = created.details["reminder_id"]
    assert created.details["effect"] == "durable_local_reminder_delivered_in_session"
    rows = _rows(sid)
    assert len(rows) == 1 and rows[0]["status"] == "scheduled"

    _, listed = _run_turn("what reminders do I have", session_id=sid)
    assert listed.ok and "visa application" in listed.response_text

    _, moved = _run_turn("move the reminder to 2026-09-20 14:00", session_id=sid)
    assert moved.ok and moved.status == "executed"
    assert _rows(sid)[0]["due_at_utc"].startswith("2026-09-20T11:00")  # 14:00 host(+03:00)

    _, cancelled = _run_turn("cancel the reminder", session_id=sid)
    assert cancelled.ok and cancelled.status == "executed"
    assert _rows(sid, include_delivered=True) == []  # cancelled rows are not listed
    assert rem.load_reminder(reminder_id, get_connection_fn=get_connection)["status"] == "cancelled"

    # A second cancel with nothing pending is an honest "nothing to cancel", not a new effect.
    _, again = _run_turn("cancel the reminder", session_id=sid)
    assert not again.ok
    assert "could not find a pending reminder" in again.response_text


def test_reminder_session_isolation(isolated_home):
    sid_a, sid_b = _sid("iso-a"), _sid("iso-b")
    _run_turn("remind me to water the plants in 30 minutes", session_id=sid_a)
    _run_turn("remind me to charge the drone in 45 minutes", session_id=sid_b)

    _, listed_b = _run_turn("list my reminders", session_id=sid_b)
    assert "charge the drone" in listed_b.response_text
    assert "water the plants" not in listed_b.response_text

    collector = _Collector()
    dispatcher = ReminderDispatcher(
        get_connection_fn=get_connection, deliver_fn=collector, sleep_fn=lambda s: None
    )
    outcome = dispatcher.sweep_once()
    assert outcome["delivered"] == 0  # neither is due yet


# ---------------------------------------------------------------------------
# Dispatcher: delivery, overdue-on-restart, duplicate suppression, races, crash honesty
# ---------------------------------------------------------------------------


def test_due_reminder_delivers_once_with_receipt_and_log_event(isolated_home):
    sid = _sid("deliver")
    _, created = _run_turn("remind me to stretch in 0 minutes", session_id=sid)
    assert created.ok
    reminder_id = created.details["reminder_id"]

    dispatcher = ReminderDispatcher(
        get_connection_fn=get_connection, sleep_fn=lambda s: None
    )  # real delivery fn: writes the session conversation log
    first = dispatcher.sweep_once()
    second = dispatcher.sweep_once()

    assert first["delivered"] == 1
    assert second["delivered"] == 0  # duplicate suppression: never delivered twice

    row = rem.load_reminder(reminder_id, get_connection_fn=get_connection)
    assert row["status"] == "delivered" and row["delivered_at"]
    assert row["delivery_receipt_json"] and "session_conversation_log" in row["delivery_receipt_json"]

    from core.persistent_memory import recent_conversation_events

    events = recent_conversation_events(sid, limit=5, include_artifacts=True)
    assert any("stretch" in str(e.get("assistant") or "") for e in events), (
        "delivered reminder did not reach the session conversation log"
    )


def test_overdue_reminder_delivers_after_restart_never_dropped(isolated_home):
    sid = _sid("overdue")
    past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    record = rem.schedule_reminder(
        session_id=sid, task_id="t", note="restart overdue task",
        due_at_utc=past, tz_name="", due_wall="", get_connection_fn=get_connection,
    )
    collector = _Collector()
    dispatcher = ReminderDispatcher(
        get_connection_fn=get_connection, deliver_fn=collector, sleep_fn=lambda s: None
    )
    outcome = dispatcher.sweep_once()
    assert outcome["delivered"] == 1
    assert collector.notes() == ["restart overdue task"]
    row = rem.load_reminder(record["reminder_id"], get_connection_fn=get_connection)
    assert row["status"] == "delivered"


def test_cancel_just_before_dispatch_wins_the_race(isolated_home):
    sid = _sid("race")
    _, created = _run_turn("remind me to mute the phone in 1 minute", session_id=sid)
    reminder_id = created.details["reminder_id"]
    # Simulate the cancel landing between the sweep's due-query and its claim:
    # cancel first, then sweep -- the claim CAS fails and nothing is delivered.
    outcome = rem.cancel_reminder(reminder_id, get_connection_fn=get_connection)
    assert outcome["ok"]
    collector = _Collector()
    dispatcher = ReminderDispatcher(
        get_connection_fn=get_connection, deliver_fn=collector, sleep_fn=lambda s: None
    )
    sweep = dispatcher.sweep_once()
    assert sweep["delivered"] == 0
    assert collector.notes() == []
    assert rem.load_reminder(reminder_id, get_connection_fn=get_connection)["status"] == "cancelled"


def test_crash_between_claim_and_effect_becomes_delivery_uncertain(isolated_home):
    sid = _sid("crash")
    record = rem.schedule_reminder(
        session_id=sid, task_id="t", note="interrupted task",
        due_at_utc=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        tz_name="", due_wall="", get_connection_fn=get_connection,
    )
    # The process claimed it, then died: status dispatching, timestamp aged past the window.
    assert rem.claim_for_dispatch(record["reminder_id"], get_connection_fn=get_connection)
    conn = get_connection()
    conn.execute(
        "UPDATE reminder_requests SET updated_at = ? WHERE reminder_id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), record["reminder_id"]),
    )
    conn.commit()
    conn.close()

    collector = _Collector()
    dispatcher = ReminderDispatcher(
        get_connection_fn=get_connection, deliver_fn=collector, sleep_fn=lambda s: None,
        stale_dispatch_seconds=60.0,
    )
    outcome = dispatcher.sweep_once()
    assert outcome["uncertain"] == 1
    row = rem.load_reminder(record["reminder_id"], get_connection_fn=get_connection)
    assert row["status"] == "delivery_uncertain"
    # The uncertain outcome is REPORTED to the session (a notice, distinct from a delivery).
    assert collector.notes() == ["interrupted task"]
    assert collector.events[0][1] == "delivery_uncertain"
    # And it is never re-fired as a normal delivery on later sweeps.
    again = dispatcher.sweep_once()
    assert again["delivered"] == 0 and again["uncertain"] == 0


def test_delivered_reminder_cannot_be_cancelled(isolated_home):
    sid = _sid("delivered")
    record = rem.schedule_reminder(
        session_id=sid, task_id="t", note="already gone out",
        due_at_utc=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        tz_name="", due_wall="", get_connection_fn=get_connection,
    )
    ReminderDispatcher(
        get_connection_fn=get_connection, deliver_fn=lambda row: {"ok": True}, sleep_fn=lambda s: None
    ).sweep_once()
    outcome = rem.cancel_reminder(record["reminder_id"], get_connection_fn=get_connection)
    assert not outcome["ok"] and outcome["status"] == "already_delivered"


def test_uncertain_and_failed_delivery_visibility_in_list(isolated_home):
    sid = _sid("visibility")
    record = rem.schedule_reminder(
        session_id=sid, task_id="t", note="interrupted task",
        due_at_utc=datetime.now(timezone.utc).isoformat(), tz_name="", due_wall="",
        get_connection_fn=get_connection,
    )
    assert rem.claim_for_dispatch(record["reminder_id"], get_connection_fn=get_connection)
    # Age the claim past the staleness window and reconcile: the row becomes uncertain
    # and the LIST surface shows that state to the operator.
    conn = get_connection()
    conn.execute(
        "UPDATE reminder_requests SET updated_at = ? WHERE reminder_id = ?",
        ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), record["reminder_id"]),
    )
    conn.commit()
    conn.close()
    ReminderDispatcher(
        get_connection_fn=get_connection, deliver_fn=_Collector(), sleep_fn=lambda s: None,
        stale_dispatch_seconds=60.0,
    ).sweep_once()
    _, listed = _run_turn("list my reminders", session_id=sid)
    assert "delivery uncertain" in listed.response_text


def test_registry_declares_the_honest_effect(isolated_home):
    ledger = {entry["capability_id"]: entry for entry in operator_capability_ledger()}
    reminder = ledger["operator.schedule_reminder"]
    assert reminder["supported"] and reminder["support_level"] == "full"
    assert "in-session" in reminder["partial_reason"]  # honest: not an OS push notification
    calendar_cancel = ledger["operator.cancel_calendar_event"]
    assert calendar_cancel["support_level"] == "partial"
    # Honest since the provider vertical: drafts stay local-only unless a provider is
    # configured — the reason names BOTH halves of that truth.
    assert "local .ics draft only" in calendar_cancel["partial_reason"]
    assert "a provider is configured" in calendar_cancel["partial_reason"]


# ---------------------------------------------------------------------------
# Calendar-draft lifecycle: move and cancel the LOCAL artifact
# ---------------------------------------------------------------------------


def _create_calendar_draft(session_id: str):
    """Drive the existing schedule_calendar_event lane to a real executed .ics draft."""
    _, created = _run_turn(
        'schedule a meeting "Ops Sync" on 2026-09-14 15:30 for 30m', session_id=session_id
    )
    # The existing lane is approval-gated; approve to execution the same turn when the
    # policy allows, otherwise approve by action id.
    if created.status == "approval_required":
        action_id = created.details["action_id"]
        intent = parse_operator_action_intent(f"approve calendar {action_id}")
        created = dispatch_operator_action(intent, task_id="t-approve", session_id=session_id)
    return created


def test_calendar_draft_move_and_cancel(isolated_home):
    sid = _sid("calendar")
    created = _create_calendar_draft(sid)
    assert created.ok and created.status == "executed", created.response_text
    ics_path = created.details["ics_path"]
    from pathlib import Path

    original = Path(ics_path).read_text(encoding="utf-8")
    assert "20260914T123000Z" in original  # 15:30 +03:00 host zone -> 12:30 UTC

    _, moved = _run_turn("move the meeting to 2026-09-15 10:00 Europe/Athens", session_id=sid)
    assert moved.ok and moved.status == "executed", moved.response_text
    rewritten = Path(ics_path).read_text(encoding="utf-8")
    assert "20260915T070000Z" in rewritten  # 10:00 +03:00 -> 07:00 UTC
    assert Path(ics_path).exists()

    _, cancelled = _run_turn("cancel the calendar draft", session_id=sid)
    assert cancelled.ok and cancelled.status == "executed"
    assert not Path(ics_path).exists()  # the local artifact is gone
    assert "nothing was ever sent to an external calendar service" in cancelled.response_text


def test_calendar_cancel_without_a_draft_is_honest(isolated_home):
    sid = _sid("no-draft")
    _, result = _run_turn("cancel the calendar draft", session_id=sid)
    assert not result.ok
    assert "don't have a calendar draft" in result.response_text


# ---------------------------------------------------------------------------
# Parser boundaries: the scheduling intents do not swallow other lanes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_kind",
    [
        ("remind me to file the report tomorrow at 9am", "schedule_reminder"),
        ("what reminders do I have", "list_reminders"),
        ("cancel the reminder", "cancel_reminder"),
        ("move the reminder to tomorrow 8pm", "move_reminder"),
        ("reschedule a meeting to 2026-09-20 10:00", "move_calendar_event"),
        ("cancel the calendar draft", "cancel_calendar_event"),
        # negatives: these stay on their own lanes
        ("schedule a meeting \"Sync\" on 2026-09-14 15:30", "schedule_calendar_event"),
        ("clean temp files in the cache", "cleanup_temp_files"),
        ("move '/tmp/a.txt' to '/tmp/b/'", "move_path"),
    ],
)
def test_parser_routes_scheduling_intents(text, expected_kind):
    intent = parse_operator_action_intent(text)
    assert intent is not None and intent.kind == expected_kind, f"{text!r} -> {intent}"
