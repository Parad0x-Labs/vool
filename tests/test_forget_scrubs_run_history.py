"""A chat "forget" erases the forgotten words from the runtime's run history, not only from memory.

Measured 2026-10-14 on a served daemon: after "Forget Flourstone Bakehouse." answered "Forget applied", the
words were still readable in vool_web0_v2.db -- runtime_session_events, runtime_checkpoints, runtime_attempts,
obligation_sets, a7_finalizations and runtime_sessions -- because the forget never touched those tables. And
"forget the code 12" also erased "the meeting is in room 312": forget matching was a plain substring test.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.test_forget_context_pages_r6 import admit
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_memory_forget_served_p0 import _reply_text, served  # served: the daemon fixture
from tests.test_question_date_time_leg_20261002 import _hash_backend, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")

CANARY = "Flourstone Bakehouse"
BYSTANDER = "signal green"


@pytest.fixture
def isolated_pages(tmp_path, runtime_storage_reset):
    """One runtime home whose database every writer and the forget share."""
    import core.runtime_continuity as continuity
    import core.runtime_paths as paths
    import storage.db as database
    from storage.migrations import run_migrations

    previous = paths._VOOL_HOME_OVERRIDE
    previous_db = database.active_default_db_path()
    previous_continuity_db = continuity._DB_PATH_OVERRIDE
    profile = _profile(tmp_path)
    paths.configure_runtime_home(profile)
    try:
        db_path = profile / "data" / "vool_web0_v2.db"
        database.configure_default_db_path(db_path)
        continuity.configure_runtime_continuity_db_path(db_path)
        run_migrations()
        yield profile
    finally:
        continuity.configure_runtime_continuity_db_path(previous_continuity_db)
        database.configure_default_db_path(previous_db)
        paths.configure_runtime_home(previous)


def _db_carriers(db_path: Path, needle: str) -> list[str]:
    """Every table.column of a database file whose value holds ``needle`` (case-insensitive)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        found = []
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            columns = [str(column[1]) for column in conn.execute(f'PRAGMA table_info("{table}")')]
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                for column, value in zip(columns, row, strict=True):
                    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
                    if needle.casefold() in text.casefold():
                        found.append(f"{table}.{column}")
        return sorted(set(found))
    finally:
        conn.close()


def _file_carriers(db_path: Path, needle: str) -> list[str]:
    """The database file and its -wal/-shm sidecars, read as raw bytes."""
    hits = []
    for path in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
        if path.exists() and needle.casefold().encode() in path.read_bytes().lower():
            hits.append(path.name)
    return hits


def _runtime_db() -> Path:
    from storage.db import active_default_db_path

    return Path(active_default_db_path())


def _remember(profile: Path, chat: str, text: str) -> None:
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(chat, grant_current_receipts=False)
    handled, reply = command(profile, chat, "Remember that " + text)
    assert handled and "Locked in" in reply, reply


def _turn_history(chat: str, request: str, answer: str) -> dict[str, Any]:
    """One turn's run history, written by the runtime's own writers."""
    from core.runtime_continuity import (
        append_runtime_event,
        create_runtime_attempt,
        create_runtime_checkpoint,
        finalize_runtime_checkpoint,
    )

    checkpoint = create_runtime_checkpoint(session_id=chat, request_text=request, task_class="chat_conversation")
    attempt = create_runtime_attempt(session_id=chat, original_request=request,
                                     checkpoint_id=checkpoint["checkpoint_id"])
    append_runtime_event(session_id=chat, event_type="task_received", message="Received request: " + request,
                         details={"request": request})
    finalize_runtime_checkpoint(checkpoint["checkpoint_id"], status="completed", final_response=answer)
    return {"checkpoint": checkpoint["checkpoint_id"], "attempt": attempt["attempt_id"]}


def _session_words(chat: str) -> str:
    conn = sqlite3.connect(_runtime_db())
    try:
        parts = [json.dumps(conn.execute(f"SELECT * FROM {table} WHERE session_id = ?", (chat,)).fetchall())
                 for table in ("runtime_session_events", "runtime_checkpoints", "runtime_attempts", "runtime_sessions")]
    finally:
        conn.close()
    return "\n".join(parts)


def test_forget_erases_this_chats_run_history_and_keeps_other_words_and_chats(isolated_pages):
    chat, other = "run-history-own", "run-history-other"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    _turn_history(chat, "Remember that my sister's bakery is called Flourstone Bakehouse.", "Locked in.")
    rhyme = _turn_history(chat, "Tell me a rhyme about the Flourstone Bakehouse.", "Flourstone Bakehouse bakes at dawn.")
    _turn_history(chat, "Remember that my harbor lantern is painted signal green.", "Locked in.")
    _turn_history(other, "My cousin also likes the Flourstone Bakehouse.", "Noted.")

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    conn = sqlite3.connect(_runtime_db())
    try:
        digests = conn.execute(
            "SELECT c.final_response_hash, a.original_request_hash FROM runtime_checkpoints c "
            "JOIN runtime_attempts a ON a.checkpoint_id = c.checkpoint_id WHERE c.checkpoint_id = ?",
            (rhyme["checkpoint"],)).fetchone()
    finally:
        conn.close()
    # A digest of the erased words goes with them: it would confirm a guess of what was forgotten.
    assert digests == ("", ""), digests
    own = _session_words(chat)
    assert "flourstone" not in own.casefold(), own
    # Rows about something else stay, word for word.
    assert "Remember that my harbor lantern is painted signal green." in own
    # Another chat's run history is not this chat's forget.
    assert CANARY in _session_words(other)


def test_forget_of_a_number_erases_that_number_and_not_a_longer_one(isolated_pages):
    """"forget the code 12" erased "the meeting is in room 312" too (measured 2026-10-14)."""
    from core.memory.files import memory_entries_path

    chat = "run-history-numbers"
    _remember(isolated_pages, chat, "my gate code is 12")
    _remember(isolated_pages, chat, "the meeting is in room 312")
    _turn_history(chat, "my gate code is 12", "Noted.")
    _turn_history(chat, "the meeting is in room 312", "Noted.")

    handled, reply = command(isolated_pages, chat, "forget the code 12")

    assert handled and reply == "Forget applied. Removed 1 memory entry.", reply
    live = [row.get("text") for row in map(json.loads, memory_entries_path().read_text().splitlines())
            if row.get("status") != "erased"]
    assert live == ["the meeting is in room 312"], live
    history = _session_words(chat)
    assert "room 312" in history
    assert "gate code is 12" not in history, history


def test_a_number_glued_to_its_unit_is_still_that_number(isolated_pages):
    """The boundary keeps "312" apart from "12"; it must not let "12kg" escape a "forget 12"."""
    from core.memory.files import memory_entries_path

    chat = "run-history-units"
    _remember(isolated_pages, chat, "my bag weighs 12kg")

    handled, reply = command(isolated_pages, chat, "forget 12")

    assert handled and reply.startswith("Forget applied"), reply
    assert "12kg" not in memory_entries_path().read_text()


def test_run_history_that_cannot_be_erased_is_never_reported_as_forgotten(isolated_pages):
    from storage.db import get_connection

    chat = "run-history-refused"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    _turn_history(chat, "Tell me about the Flourstone Bakehouse.", "It bakes at dawn.")
    conn = get_connection()
    try:
        conn.execute("CREATE TRIGGER refuse_event_erasure BEFORE DELETE ON runtime_session_events "
                     "BEGIN SELECT RAISE(ABORT, 'event store unavailable'); END")
        conn.commit()
        handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
        assert handled and reply.startswith("Forget could not be verified as fully applied"), reply
        assert "run history runtime session events" in reply, reply
        assert CANARY in _session_words(chat)
    finally:
        conn.execute("DROP TRIGGER IF EXISTS refuse_event_erasure")
        conn.commit()
        conn.close()

    # The retry the reply asks for finishes the job.
    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
    assert handled and "could not be verified" not in reply, reply
    assert "flourstone" not in _session_words(chat).casefold()


def test_an_unsent_queued_message_is_kept_and_the_forget_says_so(isolated_pages):
    from core.runtime_continuity import enqueue_message, get_queue_item

    chat = "run-history-queue"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    queued = enqueue_message(session_id=chat, payload={"text": "Order bread from the Flourstone Bakehouse."})

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and "could not be verified" in reply and "message queue" in reply, reply
    item = get_queue_item(queued["queue_item_id"])
    assert item["status"] == "pending" and CANARY in json.dumps(item), item


def test_a_chat_table_no_store_classifies_fails_closed(isolated_pages):
    """A future writer's chat-scoped table is read by the final verification: words left there are named."""
    from storage.db import get_connection

    chat = "run-history-unclassified"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE turn_scratch_notes (note_id TEXT PRIMARY KEY, session_id TEXT, note TEXT)")
        conn.executemany("INSERT INTO turn_scratch_notes VALUES (?, ?, ?)", [
            ("own", chat, "ask about the Flourstone Bakehouse"),
            ("foreign", "another-chat", "ask about the Flourstone Bakehouse"),
        ])
        conn.commit()
    finally:
        conn.close()

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and "could not be verified" in reply and "unclassified turn scratch notes" in reply, reply


def test_erasing_a_context_page_logs_no_digest_of_its_words(isolated_pages, caplog):
    chat = "run-history-log"
    page = admit(chat, "My locker is 14.")
    with caplog.at_level(logging.INFO):
        handled, reply = command(isolated_pages, chat, "forget 14")
    assert handled and reply.startswith("Forget applied"), reply
    conn = sqlite3.connect(_runtime_db())
    try:
        receipts = [str(row[0]) for row in conn.execute(
            "SELECT event_hash FROM event_hash_chain WHERE event_id LIKE ?", (f"ctx-page-erased:{page['page_hash']}:%",))]
    finally:
        conn.close()
    assert receipts, "the page erasure left no chain event; the log check would be vacuous"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "erased a context page via" in logged, logged
    digests = [page["page_hash"], *receipts]
    # A short sentence is recoverable from any prefix of its digest: no prefix of 8 hex characters may be logged.
    assert not [digest for digest in digests if digest[:8] in logged], logged


# ── the served workflow ──────────────────────────────────────────────────────


def test_served_forget_leaves_no_forgotten_words_in_the_runtime_database(served):
    """The real daemon, the real /api/chat door, the real model lane (scripted provider): the database file
    and its write-ahead log hold no byte of the forgotten words after "Forget applied", while the daemon
    still runs, and the words the person did not forget are still there."""
    daemon, home = served["daemon"], served["home"]
    session = "forget-run-history"
    for message in (
        "Remember that my sister's bakery is called Flourstone Bakehouse.",
        "Remember that my harbor lantern is painted signal green.",
        "Tell me a short rhyme about the Flourstone Bakehouse.",
    ):
        assert _reply_text(daemon.chat(message, session_id=session, mode="auto")).strip()
    db_path = home / "data" / "vool_web0_v2.db"
    before = _db_carriers(db_path, "flourstone")
    assert {"runtime_session_events.message", "runtime_checkpoints.request_text",
            "runtime_attempts.original_request_snapshot", "obligation_sets.snapshot_json",
            "a7_finalizations.canonical_content"} <= set(before), before

    reply = _reply_text(daemon.chat(f"Forget {CANARY}.", session_id=session, mode="auto"))

    assert reply.startswith("Forget applied"), reply
    assert _db_carriers(db_path, "flourstone") == []
    assert _file_carriers(db_path, "flourstone") == []
    kept = _db_carriers(db_path, BYSTANDER)
    assert "runtime_checkpoints.request_text" in kept, kept
    entries = (home / "data" / "memory_entries.jsonl").read_text(encoding="utf-8")
    assert BYSTANDER in entries and "flourstone" not in entries.casefold()
    # The next turn in the same chat still works, and writes nothing of the forgotten words back.
    assert _reply_text(daemon.chat("Tell me a short rhyme about harbor lanterns.", session_id=session,
                                   mode="auto")).strip()
    assert _db_carriers(db_path, "flourstone") == []
