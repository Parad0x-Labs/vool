"""'cancel that one' must cancel the real thing it points at, never just say it did.

This file drives the follow-up cancel contract through the same seam the served turn uses
(core.local_operator_actions parse + dispatch) against the real sqlite store under a
temporary home: 'cancel that one' / 'actually cancel that' must resolve to the reminder
created last in that chat and flip its durable row to 'cancelled' while naming it in the
reply; with nothing to cancel, the reply must say plainly that nothing was found and the
store must stay untouched; with two reminders pending, the latest-created one is the one
cancelled; and when the chat has no reminders but a queued message, 'cancel that' cancels
the queue item itself. A reply that confirms a cancel the store never recorded is exactly
the failure this file exists to catch.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent
from core.operator import reminders as rem
from storage.db import get_connection
from storage.migrations import run_migrations

TIMEZONE = "Europe/Athens"

pytestmark = [pytest.mark.pa_beta]


# ---------------------------------------------------------------------------
# Fixtures and helpers (the rig of tests/pa_beta_gate/test_reminder_vertical.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    run_migrations()
    from core.user_preferences import save_user_timezone

    assert save_user_timezone(TIMEZONE)
    return tmp_path


def _days_ahead(days: int) -> str:
    """The calendar day `days` after today in the chat's timezone, as YYYY-MM-DD.

    A reminder here must still be ahead whenever the suite runs: the fixed 3, 4 and 6 November
    2026 dates passed, and VOOL rightly refused them as already passed (CI #682)."""
    return (datetime.now(ZoneInfo(TIMEZONE)).date() + timedelta(days=days)).isoformat()


def _sid(label: str) -> str:
    return f"cancel-that-{label}-{uuid.uuid4().hex[:8]}"


def _run_turn(text: str, *, session_id: str):
    """The exact seam the served turn fast path uses: parse + wired dispatch."""
    intent = parse_operator_action_intent(text)
    assert intent is not None, f"operator parser missed the request: {text!r}"
    return intent, dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _dispatch(text: str, *, session_id: str):
    """Parse and dispatch without the not-None guard, for the lanes that may refuse."""
    intent = parse_operator_action_intent(text)
    assert intent is not None, f"operator parser missed the request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _pending_rows(session_id: str) -> list[dict]:
    return rem.list_reminders(
        session_id=session_id, include_delivered=False, get_connection_fn=get_connection
    )


def _all_reminder_rows(session_id: str) -> list[dict]:
    """Every reminder row for the session, whatever its status: a cancel that must not
    happen may not leave a row in ANY status."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM reminder_requests WHERE session_id = ?", (session_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _all_queue_rows(session_id: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM message_queue WHERE session_id = ?", (session_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _mentions_nothing_found(reply_lower: str) -> bool:
    return (
        "could not find" in reply_lower
        or "couldn't find" in reply_lower
        or "nothing to cancel" in reply_lower
    )


# ---------------------------------------------------------------------------
# The effect itself: the store row must move, and the reply must match it
# ---------------------------------------------------------------------------


def test_cancel_that_one_cancels_the_real_reminder_and_empties_the_store(isolated_home):
    sid = _sid("one")
    _, created = _run_turn(
        f"remind me to renew my passport on {_days_ahead(7)} at 10:00", session_id=sid
    )
    assert created.ok and created.status == "executed", created.response_text
    reminder_id = created.details["reminder_id"]
    assert len(_pending_rows(sid)) == 1  # the row is really there before the cancel

    _, cancelled = _run_turn("cancel that one", session_id=sid)
    assert cancelled.ok and cancelled.status == "executed", cancelled.response_text

    reply = cancelled.response_text
    lowered = reply.lower()
    assert "cancel" in lowered
    assert "renew my passport" in reply, "the reply must name the thing it cancelled"
    # The reply reports a cancellation, so it must not also claim a delivery or a scheduling.
    assert "schedul" not in lowered, "a cancel reply must not claim anything was scheduled"
    assert re.search(r"(?:has been|was|is|been) deliv", lowered) is None, (
        "a cancel reply must not claim anything was delivered"
    )

    assert _pending_rows(sid) == []  # nothing pending left for this chat
    row = rem.load_reminder(reminder_id, get_connection_fn=get_connection)
    assert row is not None and row["status"] == "cancelled"


@pytest.mark.parametrize(
    "phrase", ["cancel that one", "actually cancel that"], ids=["that-one", "actually-that"]
)
def test_cancel_referent_with_nothing_to_cancel_says_so_and_touches_nothing(isolated_home, phrase):
    sid = _sid("empty")

    result = _dispatch(phrase, session_id=sid)

    reply = str(result.response_text or "")
    lowered = reply.lower()
    assert (not result.ok) or _mentions_nothing_found(lowered), reply
    assert _mentions_nothing_found(lowered), (
        f"{phrase!r} with nothing to cancel must say plainly that nothing was found: {reply!r}"
    )
    assert "cancelled" not in lowered and "canceled" not in lowered, (
        f"{phrase!r} claimed a cancel happened with nothing in the store: {reply!r}"
    )
    # No row may appear and no status may change: the session stays exactly empty.
    assert _pending_rows(sid) == []
    assert _all_reminder_rows(sid) == []
    assert _all_queue_rows(sid) == []


def test_cancel_that_one_picks_the_latest_created_of_two_reminders(isolated_home):
    sid = _sid("two")
    _, early = _run_turn(
        f"remind me to water the herbs on {_days_ahead(8)} at 09:00", session_id=sid
    )
    assert early.ok and early.status == "executed", early.response_text
    _, late = _run_turn(
        f"remind me to return the library books on {_days_ahead(10)} at 17:30", session_id=sid
    )
    assert late.ok and late.status == "executed", late.response_text
    assert len(_pending_rows(sid)) == 2

    _, cancelled = _run_turn("cancel that one", session_id=sid)
    assert cancelled.ok and cancelled.status == "executed", cancelled.response_text
    assert "return the library books" in cancelled.response_text, (
        "the reply must name the reminder it cancelled"
    )
    assert "water the herbs" not in cancelled.response_text

    remaining = _pending_rows(sid)
    assert len(remaining) == 1 and "water the herbs" in str(remaining[0]["note"])
    early_row = rem.load_reminder(early.details["reminder_id"], get_connection_fn=get_connection)
    late_row = rem.load_reminder(late.details["reminder_id"], get_connection_fn=get_connection)
    assert early_row is not None and early_row["status"] == "scheduled"
    assert late_row is not None and late_row["status"] == "cancelled"


def test_cancel_that_cancels_the_queued_message(isolated_home):
    from core.runtime_continuity import (
        enqueue_message,
        get_queue_item,
        list_queued_messages,
    )

    sid = _sid("queue")
    assert _pending_rows(sid) == []  # no reminders: the referent must fall to the queue
    item = enqueue_message(
        session_id=sid, payload={"text": "draft to the council about the fence"}
    )
    queue_item_id = item["queue_item_id"]
    assert item["status"] == "pending"
    assert len(list_queued_messages(sid)) == 1

    result = _dispatch("cancel that", session_id=sid)

    assert result.ok and result.status == "executed", result.response_text
    stored = get_queue_item(queue_item_id)
    assert stored is not None and stored["status"] == "cancelled", (
        "the queued message row must itself move to cancelled"
    )
    reply = str(result.response_text or "")
    lowered = reply.lower()
    assert "cancel" in lowered
    assert any(word in lowered for word in ("queue", "message", "send", "sent")), (
        f"the reply must say the queued message was cancelled: {reply!r}"
    )
    # The fallback may not invent a reminder while cancelling the queued message.
    assert _pending_rows(sid) == [] and _all_reminder_rows(sid) == []


# ---------------------------------------------------------------------------
# Parser boundaries: the referent phrases are cancels, and only those change
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    ["cancel that one", "actually cancel that", "cancel it", "cancel the last one"],
    ids=["that-one", "actually-that", "it", "the-last-one"],
)
def test_parser_reads_referent_cancel_phrases(phrase):
    intent = parse_operator_action_intent(phrase)
    assert intent is not None, f"a follow-up cancel must parse: {phrase!r}"


def test_parser_keeps_named_thing_cancels_parseable():
    intent = parse_operator_action_intent("cancel the meeting")
    assert intent is not None, "a cancel naming a calendar thing must still parse"
    assert not intent.referent, "a named-thing cancel is never the pointer lane"


def test_parser_does_not_claim_an_unknown_thing_cancel():
    """A cancel naming a thing no lane knows ("cancel my dentist thing") parsed to None before
    the pointer lane existed and must stay unclaimed by it: the pointer is only for a cancel
    whose words name no thing at all."""
    intent = parse_operator_action_intent("cancel my dentist thing")
    assert intent is None or intent.referent is None


def test_parser_leaves_a_calendar_question_off_the_cancel_lane():
    intent = parse_operator_action_intent("what is on my calendar tomorrow")
    assert intent is None or intent.kind != "cancel_reminder"
