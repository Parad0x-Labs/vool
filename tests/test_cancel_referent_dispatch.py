"""Edge laws for the pointer-only cancel lane ("cancel that one") beyond the TASK's three named
scenarios (those live in tests/test_cancel_that_one_is_real.py).

What this file proves: the pointer resolves ONLY against this chat's own rows — a reminder mid-
delivery is refused with its real reason instead of a claim, a message already on its way out is
not promised away, another chat's rows are never touched, the local-action policy door still
gates the pointer the same way it gates an explicit cancel, and the cancel is audited.
"""
from __future__ import annotations

import uuid

import pytest

from core.local_operator_actions import (
    dispatch_operator_action,
    parse_operator_action_intent,
)
from core.operator import reminders as rem
from storage.db import get_connection
from storage.migrations import run_migrations

pytestmark = [pytest.mark.pa_beta]


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    run_migrations()
    from core.user_preferences import save_user_timezone

    assert save_user_timezone("Europe/Athens")
    return tmp_path


def _sid(label: str) -> str:
    return f"cancel-edge-{label}-{uuid.uuid4().hex[:8]}"


def _turn(text: str, *, session_id: str):
    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _pending(session_id: str):
    return rem.list_reminders(session_id=session_id, include_delivered=False, get_connection_fn=get_connection)


_FUTURE_DAY = "2027-03-"


def test_pointer_refuses_a_reminder_whose_delivery_already_started(isolated_home):
    """The most recent row is mid-delivery: the REAL cancel refuses it and the reply carries that
    refusal, so the pointer never manufactures a cancellation that did not happen."""
    sid = _sid("uncertain")
    _turn(f"remind me to sign the lease on {_FUTURE_DAY}01 at 09:00", session_id=sid)
    from storage.db import get_connection as gc

    row = _pending(sid)[0]
    conn = gc()
    try:
        conn.execute(
            "UPDATE reminder_requests SET status = 'delivery_uncertain' WHERE reminder_id = ?",
            (row["reminder_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    result = _turn("cancel that one", session_id=sid)
    assert not result.ok
    assert "could not cancel" in result.response_text
    assert _pending(sid), "the uncertain row must still be pending"


def test_pointer_does_not_touch_another_chats_reminder(isolated_home):
    """The pointer resolves inside this chat only: another chat's pending reminder is invisible,
    so the cancel here reports nothing found and that other row is untouched."""
    mine, other = _sid("mine"), _sid("other")
    _turn(f"remind me to vet the cat on {_FUTURE_DAY}02 at 10:00", session_id=other)

    result = _turn("cancel that one", session_id=mine)
    assert not result.ok
    assert "couldn't find a reminder to cancel" in result.response_text
    assert len(_pending(other)) == 1 and _pending(other)[0]["status"] == "scheduled"


def test_pointer_falls_through_to_a_waiting_queued_message(isolated_home):
    """No reminders in the chat: the pointer means the newest message still waiting to be sent,
    and the REAL queue cancel runs. Only a still-waiting message is a candidate: once both are
    cancelled, a third pointer has nothing to point at and says so."""
    from core import runtime_continuity as rc

    sid = _sid("queue")
    first = rc.enqueue_message(session_id=sid, payload={"text": "draft about the fence"})
    second = rc.enqueue_message(session_id=sid, payload={"text": "follow-up about the invoice"})

    result = _turn("cancel that", session_id=sid)
    assert result.ok and "queued message" in result.response_text
    assert "invoice" in result.response_text, "the pointer means the newest waiting message"
    assert str(rc.get_queue_item(second["queue_item_id"])["status"]) == "cancelled"
    assert str(rc.get_queue_item(first["queue_item_id"])["status"]) == "pending"

    result2 = _turn("cancel that", session_id=sid)
    assert result2.ok and "fence" in result2.response_text
    assert str(rc.get_queue_item(first["queue_item_id"])["status"]) == "cancelled"

    result3 = _turn("cancel that", session_id=sid)
    assert not result3.ok and "couldn't find a reminder to cancel" in result3.response_text


def test_pointer_cancel_is_audited(isolated_home):
    """The pointer cancel leaves the same audit trail an explicit cancel leaves."""
    sid = _sid("audit")
    _turn(f"remind me to call the bank on {_FUTURE_DAY}03 at 11:00", session_id=sid)
    _turn("cancel that one", session_id=sid)

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT event_type, details_json FROM audit_log WHERE event_type = 'operator_action_cancel_reminder'"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "the pointer cancel must be audited like an explicit cancel"
    assert "cancelled" in rows[-1]["details_json"]


def test_pointer_cancel_crosses_the_local_action_policy_door(isolated_home, monkeypatch):
    """With safe local actions disabled, the pointer cancel is blocked exactly like an explicit
    one; nothing runs and the store keeps its row."""
    from core import policy_engine

    sid = _sid("policy")
    _turn(f"remind me to renew the badge on {_FUTURE_DAY}04 at 12:00", session_id=sid)
    previous = policy_engine.get
    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda key, default=None: False if key == "execution.allow_safe_local_actions" else previous(key, default),
    )
    result = _turn("cancel that one", session_id=sid)
    assert not result.ok and result.status == "blocked"
    assert len(_pending(sid)) == 1 and _pending(sid)[0]["status"] == "scheduled"


def test_parser_pointer_shapes_and_non_shapes():
    """The pointer is a whole clause: noun-bearing cancels keep their lanes, and a cancel whose
    object is a real thing ("cancel that flight") is left to other lanes."""
    for text in ("cancel that one", "actually cancel that", "cancel the last one", "call it off"):
        intent = parse_operator_action_intent(text)
        assert intent is not None and intent.kind == "cancel_reminder" and intent.referent == "most_recent", text
    for text in ("cancel the reminder", "cancel the meeting draft"):
        intent = parse_operator_action_intent(text)
        assert intent is not None and not intent.referent, text
    for text in ("cancel that flight", "cancel my dentist appointment"):
        intent = parse_operator_action_intent(text)
        assert intent is None or intent.referent is None, text


def test_pointer_does_not_eat_a_request_with_words_of_its_own():
    """A trailing "cancel that" on a request that names its own action WITHHOLDS that action's
    approval; the pointer lane must not claim the turn (measured regression: the note delete
    'yes, delete my apple note "Tax receipts 2025", cancel that' stopped withholding)."""
    intent = parse_operator_action_intent('yes, delete my apple note "Tax receipts 2025", cancel that')
    assert intent is not None and intent.kind == "apple_note_delete" and not intent.referent
    assert intent.approval_requested is False, "the trailing pointer withholds the approval"
