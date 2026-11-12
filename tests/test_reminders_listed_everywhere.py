"""Reminders set in one chat are listed from every chat; private chats keep their own rule.

The listing law (owner-wide): "what reminders do I have?" asked in ANY chat answers from
all of the owner's pending reminders, soonest due first, naming the chat each foreign row
came from in plain words. Rows born in a private chat never leave that chat, and the asking
chat's own rows always show without an origin suffix. Cancelling stays scoped to the asking
chat, exactly as before. Store-level truth: the owner listing spans sessions, keeps the
pending status filter, skips calendar_alert rows, and leaves the per-session listing alone.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from core.operator.parser import parse_operator_action_intent

# ------------------------------------------------------------------ fabricated store rows

def _row(reminder_id: str, *, session_id: str, note: str, due_at_utc: str, due_wall: str,
         status: str = "scheduled") -> dict:
    return {
        "reminder_id": reminder_id,
        "session_id": session_id,
        "task_id": "task-of-" + session_id,
        "note": note,
        "due_at_utc": due_at_utc,
        "tz_name": "UTC",
        "due_wall": due_wall,
        "status": status,
        "source_kind": "reminder",
    }


ROW_ALPHA_EARLIEST = _row(
    "0a1b2c3d-1111-4111-8111-0a1b2c3d4e5f", session_id="chat-alpha",
    note="renew the domain", due_at_utc="2026-11-12T09:30:00+00:00", due_wall="2026-11-12 09:30",
)
ROW_BETA_OWN = _row(
    "5a5a5a5a-2222-4222-8222-5a5a5a5a6b6b", session_id="chat-beta",
    note="water the ferns", due_at_utc="2026-11-12T15:00:00+00:00", due_wall="2026-11-12 15:00",
)
ROW_ALPHA_LATEST = _row(
    "9f8e7d6c-3333-4333-8333-9f8e7d6c0d0d", session_id="chat-alpha",
    note="call the chimney sweep", due_at_utc="2026-11-13T08:00:00+00:00", due_wall="2026-11-13 08:00",
)
ROW_SEALED = _row(
    "f1e2d3c4-4444-4444-8444-f1e2d3c4e5e5", session_id="chat-sealed",
    note="book the dentist", due_at_utc="2026-11-12T09:30:00+00:00", due_wall="2026-11-12 09:30",
)
ROW_UNNAMED = _row(
    "7c7c7c7c-5555-4555-8555-7c7c7c7c9d9d", session_id="chat-unnamed",
    note="pick up the parcel", due_at_utc="2026-11-12T18:00:00+00:00", due_wall="2026-11-12 18:00",
)

CHAT_LABELS = {
    "chat-alpha": "Kitchen chat",
    "chat-beta": "Studio chat",
    "chat-sealed": "Locked chat",
}


def _chat_label_fn(session_id: str) -> str:
    return CHAT_LABELS.get(session_id, "")


def _never_private(session_id: str) -> bool:
    del session_id
    return False


class _OwnerListStore:
    """Stands in for the store's owner-wide listing: pending rows only, soonest due first
    (the caller hands the rows in that order), and NO session argument is accepted."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> list[dict]:
        self.calls.append(dict(kwargs))
        return list(self._rows)


def _listed_lines(response_text: str) -> list[str]:
    return [line for line in str(response_text).splitlines() if line.startswith("- ")]


# ------------------------------------------------------------------ handler: owner-wide listing

def test_listing_from_another_chat_shows_every_pending_row_soonest_due_first():
    from core.operator.handlers import handle_list_reminders

    store = _OwnerListStore([ROW_ALPHA_EARLIEST, ROW_BETA_OWN, ROW_ALPHA_LATEST])
    result = handle_list_reminders(
        None,
        task_id="task-list", session_id="chat-beta",
        list_owner_reminders_fn=store,
        chat_label_fn=_chat_label_fn,
        is_private_chat_fn=_never_private,
        now_fn=lambda: "2026-11-12T12:00:00+00:00",
        audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok is True
    assert result.status == "reported"
    # The handler asked the store owner-wide: pending rows only, no session argument.
    assert store.calls == [{"include_delivered": False}]
    assert _listed_lines(result.response_text) == [
        "- renew the domain — due 12 Nov 2026 at 09:30:00 UTC (OVERDUE) — set in \"Kitchen chat\" (id 0a1b2c3d)",
        "- water the ferns — due 12 Nov 2026 at 15:00:00 UTC (id 5a5a5a5a)",
        "- call the chimney sweep — due 13 Nov 2026 at 08:00:00 UTC — set in \"Kitchen chat\" (id 9f8e7d6c)",
    ]
    assert result.details["scope"] == "owner"
    assert result.details["count"] == 3
    assert len(result.details["reminders"]) == 3


def test_listing_from_a_brand_new_chat_still_lists_every_row_as_foreign():
    from core.operator.handlers import handle_list_reminders

    store = _OwnerListStore([ROW_ALPHA_EARLIEST, ROW_BETA_OWN, ROW_ALPHA_LATEST])
    result = handle_list_reminders(
        None,
        task_id="task-list", session_id="chat-fresh-never-used",
        list_owner_reminders_fn=store,
        chat_label_fn=_chat_label_fn,
        is_private_chat_fn=_never_private,
        now_fn=lambda: "2026-11-10T00:00:00+00:00",
        audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok is True
    # A chat with no rows of its own still sees all three, each tagged with its origin.
    assert _listed_lines(result.response_text) == [
        "- renew the domain — due 12 Nov 2026 at 09:30:00 UTC — set in \"Kitchen chat\" (id 0a1b2c3d)",
        "- water the ferns — due 12 Nov 2026 at 15:00:00 UTC — set in \"Studio chat\" (id 5a5a5a5a)",
        "- call the chimney sweep — due 13 Nov 2026 at 08:00:00 UTC — set in \"Kitchen chat\" (id 9f8e7d6c)",
    ]
    assert result.details["scope"] == "owner"
    assert result.details["count"] == 3


def test_a_chat_with_no_title_is_named_in_plain_words_not_empty_quotes():
    from core.operator.handlers import handle_list_reminders

    store = _OwnerListStore([ROW_UNNAMED])
    result = handle_list_reminders(
        None,
        task_id="task-list", session_id="chat-beta",
        list_owner_reminders_fn=store,
        chat_label_fn=_chat_label_fn,
        is_private_chat_fn=_never_private,
        now_fn=lambda: "2026-11-10T00:00:00+00:00",
        audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok is True
    assert _listed_lines(result.response_text) == [
        "- pick up the parcel — due 12 Nov 2026 at 18:00:00 UTC — set in another chat (id 7c7c7c7c)",
    ]
    assert "set in \"\"" not in result.response_text


def test_an_owner_with_no_pending_reminders_anywhere_is_told_so_once():
    from core.operator.handlers import handle_list_reminders

    store = _OwnerListStore([])
    result = handle_list_reminders(
        None,
        task_id="task-list", session_id="chat-beta",
        list_owner_reminders_fn=store,
        chat_label_fn=_chat_label_fn,
        is_private_chat_fn=_never_private,
        now_fn=lambda: "2026-11-12T12:00:00+00:00",
        audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok is True
    assert result.response_text == "You have no pending reminders."
    assert result.details["count"] == 0


# ------------------------------------------------------------------ handler: private chats

def test_rows_born_in_a_private_chat_are_hidden_from_other_chats():
    from core.operator.handlers import handle_list_reminders

    def sealed_only(session_id: str) -> bool:
        return session_id == "chat-sealed"

    store = _OwnerListStore([ROW_SEALED, ROW_ALPHA_EARLIEST, ROW_BETA_OWN])
    result = handle_list_reminders(
        None,
        task_id="task-list", session_id="chat-beta",
        list_owner_reminders_fn=store,
        chat_label_fn=_chat_label_fn,
        is_private_chat_fn=sealed_only,
        now_fn=lambda: "2026-11-10T00:00:00+00:00",
        audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok is True
    assert "book the dentist" not in result.response_text
    # A non-private foreign row and the asking chat's own row still show.
    assert _listed_lines(result.response_text) == [
        "- renew the domain — due 12 Nov 2026 at 09:30:00 UTC — set in \"Kitchen chat\" (id 0a1b2c3d)",
        "- water the ferns — due 12 Nov 2026 at 15:00:00 UTC (id 5a5a5a5a)",
    ]
    assert result.details["count"] == 2


def test_a_private_chat_still_sees_its_own_rows_and_public_foreign_rows():
    from core.operator.handlers import handle_list_reminders

    def sealed_only(session_id: str) -> bool:
        return session_id == "chat-sealed"

    store = _OwnerListStore([ROW_SEALED, ROW_ALPHA_EARLIEST, ROW_BETA_OWN])
    result = handle_list_reminders(
        None,
        task_id="task-list", session_id="chat-sealed",
        list_owner_reminders_fn=store,
        chat_label_fn=_chat_label_fn,
        is_private_chat_fn=sealed_only,
        now_fn=lambda: "2026-11-10T00:00:00+00:00",
        audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok is True
    assert _listed_lines(result.response_text) == [
        "- book the dentist — due 12 Nov 2026 at 09:30:00 UTC (id f1e2d3c4)",
        "- renew the domain — due 12 Nov 2026 at 09:30:00 UTC — set in \"Kitchen chat\" (id 0a1b2c3d)",
        "- water the ferns — due 12 Nov 2026 at 15:00:00 UTC — set in \"Studio chat\" (id 5a5a5a5a)",
    ]


# ------------------------------------------------------------------ store: real sqlite

_REMINDER_REQUESTS_DDL = """
CREATE TABLE IF NOT EXISTS reminder_requests (
    reminder_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    note TEXT NOT NULL,
    due_at_utc TEXT NOT NULL,
    tz_name TEXT NOT NULL DEFAULT '',
    due_wall TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'scheduled',
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT,
    last_error TEXT,
    delivery_receipt_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'reminder',
    source_ref TEXT NOT NULL DEFAULT '',
    schedule_key TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    snooze_count INTEGER NOT NULL DEFAULT 0,
    fire_generation INTEGER NOT NULL DEFAULT 0
)
"""


def _memory_store():
    """One in-memory sqlite database shared by every connection the store opens.

    Each store function takes a connection and closes it, so a plain ':memory:' handle
    would vanish with the first close. A shared-cache URI plus one keeper connection
    keeps the same real sqlite database alive for the whole test.
    """
    uri = f"file:reminders_everywhere_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper = sqlite3.connect(uri, uri=True)
    keeper.row_factory = sqlite3.Row
    keeper.execute(_REMINDER_REQUESTS_DDL)
    keeper.commit()

    def get_connection():
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    return get_connection, keeper


def _schedule(store, *, note: str, session_id: str, due_at_utc: str, created_at: str,
              source_kind: str = "reminder", source_ref: str = "", schedule_key: str = "") -> str:
    from core.operator import reminders as reminder_store

    record = reminder_store.schedule_reminder(
        session_id=session_id,
        task_id="task-of-" + session_id if session_id else "",
        note=note,
        due_at_utc=due_at_utc,
        tz_name="UTC",
        due_wall=due_at_utc[:16].replace("T", " "),
        source_kind=source_kind,
        source_ref=source_ref,
        schedule_key=schedule_key,
        now_fn=lambda: created_at,
        get_connection_fn=store,
    )
    return str(record["reminder_id"])


def test_owner_listing_spans_sessions_soonest_due_first_with_created_at_ties_broken():
    from core.operator import reminders as reminder_store

    get_connection, keeper = _memory_store()
    try:
        _schedule(get_connection, note="renew the domain", session_id="chat-alpha",
                  due_at_utc="2026-11-13T08:00:00+00:00", created_at="2026-10-30T03:00:00+00:00")
        _schedule(get_connection, note="water the ferns", session_id="chat-beta",
                  due_at_utc="2026-11-12T09:30:00+00:00", created_at="2026-10-30T04:00:00+00:00")
        claimed = _schedule(get_connection, note="pick up the parcel", session_id="chat-gamma",
                            due_at_utc="2026-11-12T10:00:00+00:00", created_at="2026-10-30T05:00:00+00:00")
        # A row mid-dispatch is still pending and still listed.
        assert reminder_store.claim_for_dispatch(
            claimed, now_fn=lambda: "2026-11-12T10:00:01+00:00", get_connection_fn=get_connection)
        # Same due instant: the earlier created row leads.
        _schedule(get_connection, note="book the dentist", session_id="chat-gamma",
                  due_at_utc="2026-11-12T11:00:00+00:00", created_at="2026-10-30T01:00:00+00:00")
        _schedule(get_connection, note="call the chimney sweep", session_id="chat-alpha",
                  due_at_utc="2026-11-12T11:00:00+00:00", created_at="2026-10-30T02:00:00+00:00")

        rows = reminder_store.list_owner_reminders(get_connection_fn=get_connection)
    finally:
        keeper.close()
    assert [row["note"] for row in rows] == [
        "water the ferns",
        "pick up the parcel",
        "book the dentist",
        "call the chimney sweep",
        "renew the domain",
    ]
    assert {str(row["session_id"]) for row in rows} == {"chat-alpha", "chat-beta", "chat-gamma"}


def test_owner_listing_keeps_terminal_and_calendar_rows_out():
    from core.operator import reminders as reminder_store

    get_connection, keeper = _memory_store()
    try:
        delivered = _schedule(get_connection, note="water the ferns", session_id="chat-beta",
                              due_at_utc="2026-11-12T09:30:00+00:00", created_at="2026-10-30T01:00:00+00:00")
        assert reminder_store.claim_for_dispatch(
            delivered, now_fn=lambda: "2026-11-12T09:30:01+00:00", get_connection_fn=get_connection)
        assert reminder_store.mark_delivered(
            delivered, delivery_receipt={"effect": "shown in chat"},
            now_fn=lambda: "2026-11-12T09:30:02+00:00", get_connection_fn=get_connection)
        cancelled = _schedule(get_connection, note="renew the domain", session_id="chat-alpha",
                              due_at_utc="2026-11-12T10:00:00+00:00", created_at="2026-10-30T02:00:00+00:00")
        assert reminder_store.cancel_reminder(
            cancelled, now_fn=lambda: "2026-10-30T06:00:00+00:00", get_connection_fn=get_connection)["ok"]
        # A calendar alert row has no chat of its own (production inserts it with an empty
        # session); it is the calendar lane's surface and never a chat listing row.
        _schedule(get_connection, note="dentist at nine", session_id="",
                  due_at_utc="2026-11-12T13:00:00+00:00", created_at="2026-10-30T05:00:00+00:00",
                  source_kind="calendar_alert", source_ref="evt-dentist",
                  schedule_key="evt-dentist:lead30")
        _schedule(get_connection, note="call the chimney sweep", session_id="chat-beta",
                  due_at_utc="2026-11-12T12:00:00+00:00", created_at="2026-10-30T04:00:00+00:00")

        pending = reminder_store.list_owner_reminders(get_connection_fn=get_connection)
        with_history = reminder_store.list_owner_reminders(
            include_delivered=True, get_connection_fn=get_connection)
    finally:
        keeper.close()
    assert [row["note"] for row in pending] == ["call the chimney sweep"]
    # With history: the delivered row returns, the cancelled row and the calendar alert never do.
    assert [row["note"] for row in with_history] == ["water the ferns", "call the chimney sweep"]


def test_session_listing_still_answers_one_chat_only():
    from core.operator import reminders as reminder_store

    get_connection, keeper = _memory_store()
    try:
        _schedule(get_connection, note="renew the domain", session_id="chat-alpha",
                  due_at_utc="2026-11-12T09:30:00+00:00", created_at="2026-10-30T01:00:00+00:00")
        _schedule(get_connection, note="water the ferns", session_id="chat-beta",
                  due_at_utc="2026-11-12T10:00:00+00:00", created_at="2026-10-30T02:00:00+00:00")
        _schedule(get_connection, note="call the chimney sweep", session_id="chat-alpha",
                  due_at_utc="2026-11-12T11:00:00+00:00", created_at="2026-10-30T03:00:00+00:00")
        _schedule(get_connection, note="dentist at nine", session_id="",
                  due_at_utc="2026-11-12T12:00:00+00:00", created_at="2026-10-30T04:00:00+00:00",
                  source_kind="calendar_alert", source_ref="evt-dentist",
                  schedule_key="evt-dentist:lead30")

        alpha = reminder_store.list_reminders(session_id="chat-alpha", get_connection_fn=get_connection)
        beta = reminder_store.list_reminders(session_id="chat-beta", get_connection_fn=get_connection)
        empty = reminder_store.list_reminders(session_id="chat-fresh", get_connection_fn=get_connection)
    finally:
        keeper.close()
    assert [row["note"] for row in alpha] == ["renew the domain", "call the chimney sweep"]
    assert [row["note"] for row in beta] == ["water the ferns"]
    assert empty == []


# ------------------------------------------------------------------ cancel stays scoped

@pytest.mark.parametrize("action_id", [
    ROW_ALPHA_LATEST["reminder_id"],
    ROW_ALPHA_LATEST["reminder_id"][:8],
])
def test_cancelling_from_another_chat_refuses_the_foreign_reminder(action_id):
    from core.operator.handlers import handle_cancel_reminder
    from core.operator.models import OperatorActionIntent

    foreign_row = ROW_ALPHA_LATEST
    own_row = ROW_BETA_OWN

    def load_reminder(reminder_id):
        return foreign_row if reminder_id == foreign_row["reminder_id"] else None

    def list_rows(*, session_id, include_delivered=False):
        return [own_row] if session_id == "chat-beta" else []

    cancelled: list[str] = []

    def cancel_reminder(reminder_id):
        cancelled.append(reminder_id)
        return {"ok": True, "status": "cancelled"}

    result = handle_cancel_reminder(
        OperatorActionIntent(kind="cancel_reminder", action_id=action_id,
                             raw_text="cancel that reminder"),
        task_id="task-cancel", session_id="chat-beta",
        load_reminder_fn=load_reminder,
        list_reminders_fn=list_rows,
        cancel_reminder_fn=cancel_reminder,
        audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok is False
    assert "could not find a pending reminder" in result.response_text
    assert cancelled == []


def test_cancelling_ones_own_reminder_still_works():
    from core.operator.handlers import handle_cancel_reminder
    from core.operator.models import OperatorActionIntent

    own_row = ROW_BETA_OWN
    cancelled: list[str] = []

    result = handle_cancel_reminder(
        OperatorActionIntent(kind="cancel_reminder", action_id=own_row["reminder_id"],
                             raw_text="cancel that reminder"),
        task_id="task-cancel", session_id="chat-beta",
        load_reminder_fn=lambda reminder_id: own_row if reminder_id == own_row["reminder_id"] else None,
        list_reminders_fn=lambda **kwargs: [own_row],
        cancel_reminder_fn=lambda reminder_id: (cancelled.append(reminder_id), {"ok": True, "status": "cancelled"})[1],
        audit_log_fn=lambda *args, **kwargs: None,
    )
    assert result.ok is True
    assert cancelled == [own_row["reminder_id"]]


# ------------------------------------------------------------------ parser keeps the lane

@pytest.mark.parametrize("text", [
    "what reminders do I have?",
    "show my reminders",
    "list my reminders",
    "which reminders are set?",
])
def test_asking_for_the_reminder_list_still_routes_to_the_list_kind(text):
    intent = parse_operator_action_intent(text)
    assert intent is not None, text
    assert intent.kind == "list_reminders", text
