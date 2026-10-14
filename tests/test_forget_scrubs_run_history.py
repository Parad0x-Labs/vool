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
    from core.agent_runtime.request_authority import request_provenance_for_visible_user_text
    from core.runtime_continuity import (
        append_runtime_event,
        create_runtime_attempt,
        create_runtime_checkpoint,
        finalize_runtime_checkpoint,
    )

    # The served chat lane stamps the request's provenance (with its sha256) into the source context.
    provenance = request_provenance_for_visible_user_text(request, session_id=chat)
    checkpoint = create_runtime_checkpoint(session_id=chat, request_text=request, task_class="chat_conversation",
                                           source_context={"_request_provenance": provenance})
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
    # No retry can clear a table no erasure step knows: the reply does not ask for one.
    assert "retry the forget" not in reply and "A retry will not clear turn scratch notes" in reply, reply


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


def _database_text(db_path: Path) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return "\n".join(json.dumps(conn.execute(f'SELECT * FROM "{table}"').fetchall(), default=str)
                         for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))
    finally:
        conn.close()


def test_a_forgotten_requests_digests_go_with_its_words(isolated_pages):
    """An unsalted sha256 of a short request gives the request back by trial ("my locker code is 4417"). The
    checkpoint kept ``_request_provenance.request_sha256`` and ``outcome.original_request_hash`` (review of
    9b78dac), nested in rows whose words the forget blanked."""
    import hashlib

    from core.runtime_task_outcome import request_identity_hash

    chat = "run-history-digests"
    request = "Tell me a rhyme about the Flourstone Bakehouse."
    kept_request = "Tell me a rhyme about harbor lanterns."
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    _turn_history(chat, request, "It bakes at dawn.")
    _turn_history(chat, kept_request, "They glow at dusk.")
    digests = {hashlib.sha256(request.encode()).hexdigest(), request_identity_hash(request)}
    before = _database_text(_runtime_db())
    present = sorted(digest for digest in digests if digest in before)
    assert hashlib.sha256(request.encode()).hexdigest() in present, "no request digest was written; check is vacuous"

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    after = _database_text(_runtime_db())
    assert [digest for digest in present if digest in after] == []
    # A row that did not carry the words keeps its digests: the other turn can still be resumed and checked.
    assert hashlib.sha256(kept_request.encode()).hexdigest() in after


def test_forget_removes_pinned_messages_that_carry_the_words(isolated_pages):
    """A pinned user message carrying the forgotten words was still served after the forget, and the pin file
    and its backup kept the pinned answer's bytes (review of 9b78dac)."""
    from core import message_pins

    chat, other = "run-history-pins", "run-history-pins-other"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    message_pins.pin_message(chat, "user", "When does the Flourstone Bakehouse open?")
    message_pins.pin_message(chat, "assistant", "The Flourstone Bakehouse opens at six.")
    message_pins.pin_message(chat, "user", "My harbor lantern is painted signal green.")
    message_pins.pin_message(other, "user", "When does the Flourstone Bakehouse open?")
    path = message_pins.pins_path()
    backup = path.with_name(path.name + ".bak")
    assert CANARY in path.read_text(encoding="utf-8") and CANARY in backup.read_text(encoding="utf-8")

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    assert [pin["text"] for pin in message_pins.list_servable_pins(chat)] == [
        "My harbor lantern is painted signal green."]
    for stored in (path, backup):
        store = json.loads(stored.read_text(encoding="utf-8"))
        assert CANARY not in json.dumps(store[chat]), stored.name
        # Another chat's pin is that chat's.
        assert [pin["text"] for pin in store[other]] == ["When does the Flourstone Bakehouse open?"], stored.name


def test_a_turn_that_finalizes_after_the_forget_does_not_write_the_words_back(isolated_pages):
    """A turn still running when the forget came finalized later and put the word back into
    ``final_response``, ``runtime_sessions.last_message`` and the event log; the next forget then answered
    "Nothing was removed" while it erased those rows (review of 9b78dac)."""
    from core.runtime_continuity import (
        append_runtime_event,
        create_runtime_checkpoint,
        finalize_runtime_checkpoint,
    )

    chat = "run-history-late-writer"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    running = create_runtime_checkpoint(session_id=chat, request_text="Plan a visit to the Flourstone Bakehouse.",
                                        task_class="chat_conversation")

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
    assert handled and reply.startswith("Forget applied"), reply

    finalize_runtime_checkpoint(running["checkpoint_id"], status="completed",
                                final_response="Visit the Flourstone Bakehouse at six.")
    append_runtime_event(session_id=chat, event_type="task_completed",
                         message="Answered: Visit the Flourstone Bakehouse at six.",
                         details={"answer": "Visit the Flourstone Bakehouse at six.", "elapsed_ms": 12})
    assert "flourstone" not in _session_words(chat).casefold()
    # Words the chat did not forget are still written.
    append_runtime_event(session_id=chat, event_type="status", message="The harbor lantern is signal green.")
    assert "The harbor lantern is signal green." in _session_words(chat)


def test_a_retry_that_only_finishes_the_run_history_says_what_it_removed(isolated_pages):
    """The retry after a refused run-history erasure removed rows and answered "Nothing was removed"
    (review of 9b78dac): run-history rows were not counted."""
    from storage.db import get_connection

    chat = "run-history-retry-count"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    _turn_history(chat, "Tell me about the Flourstone Bakehouse.", "It bakes at dawn.")
    conn = get_connection()
    try:
        conn.execute("CREATE TRIGGER refuse_event_erasure BEFORE DELETE ON runtime_session_events "
                     "BEGIN SELECT RAISE(ABORT, 'event store unavailable'); END")
        conn.commit()
        handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
        assert handled and reply.startswith("Forget could not be verified"), reply
    finally:
        conn.execute("DROP TRIGGER IF EXISTS refuse_event_erasure")
        conn.commit()
        conn.close()

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied. Removed "), reply
    assert "flourstone" not in _session_words(chat).casefold()


def test_research_artifacts_and_hive_state_of_the_chat_are_erased(isolated_pages):
    """``artifact_manifests`` rows with a session id are written by autonomous research
    (core/autonomous_topic_research.py); before, every forget over one ended "unclassified ... Please retry"
    and no retry could clear it (review of 9b78dac)."""
    from core.brain_hive_artifacts import store_artifact_manifest
    from core.hive_activity_tracker import _ensure_session_hive_state_table
    from storage.db import get_connection

    chat = "run-history-artifacts"
    _remember(isolated_pages, chat, "my sister's bakery is called Flourstone Bakehouse.")
    own = store_artifact_manifest(source_kind="research_bundle", title="Research packet: bakeries",
                                  summary="Opening hours research.", payload={"query": "Flourstone Bakehouse hours"},
                                  session_id=chat)
    foreign = store_artifact_manifest(source_kind="research_bundle", title="Research packet: Flourstone Bakehouse",
                                      summary="Opening hours research.", payload={"query": "Flourstone Bakehouse"},
                                      session_id="another-chat")
    kept = store_artifact_manifest(source_kind="research_bundle", title="Research packet: lanterns",
                                   summary="Harbor lantern paint.", payload={"query": "signal green paint"},
                                   session_id=chat)
    _ensure_session_hive_state_table()  # the tracker's own schema, with the interaction payload column
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO session_hive_watch_state (session_id, interaction_payload_json, updated_at) VALUES (?, ?, ?)",
            (chat, json.dumps({"last_prompt": "news about the Flourstone Bakehouse", "seen": 12}), "2026-10-14"))
        conn.commit()
    finally:
        conn.close()

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    conn = get_connection()
    try:
        artifacts = {row[0] for row in conn.execute("SELECT artifact_id FROM artifact_manifests")}
        watch = conn.execute("SELECT interaction_payload_json FROM session_hive_watch_state WHERE session_id = ?",
                             (chat,)).fetchone()
    finally:
        conn.close()
    assert artifacts == {foreign["artifact_id"], kept["artifact_id"]}
    assert not Path(own["file_path"]).exists()
    assert Path(foreign["file_path"]).exists() and Path(kept["file_path"]).exists()
    assert watch is not None and "flourstone" not in watch[0].casefold() and '"seen": 12' in watch[0], watch


def test_numbers_inside_a_longer_number_date_or_time_are_not_the_forgotten_number(isolated_pages):
    """"12" matched "3.12", "12.5", "2026-10-12" and "12:30", "200" matched "1,200", and a bookkeeping
    ``{"elapsed_ms": 12}`` event was deleted (review of 9b78dac)."""
    from core.memory.files import memory_entries_path
    from core.runtime_continuity import append_runtime_event

    chat = "run-history-number-shapes"
    kept = ["my python is 3.12", "the tank holds 12.5 liters", "the trip starts 2026-10-12",
            "the call is at 12:30", "my step goal is 1,200"]
    for text in [*kept, "my gate code is 12", "my locker is \uff11\uff12"]:
        _remember(isolated_pages, chat, text)
    append_runtime_event(session_id=chat, event_type="status", message="Turn finished.", details={"elapsed_ms": 12})

    handled, reply = command(isolated_pages, chat, "forget 12")

    assert handled and reply == "Forget applied. Removed 2 memory entries.", reply
    live = sorted(row.get("text") for row in map(json.loads, memory_entries_path().read_text().splitlines())
                  if row.get("status") != "erased")
    assert live == sorted(kept), live
    assert "Turn finished." in _session_words(chat)

    handled, reply = command(isolated_pages, chat, "forget 200")
    assert handled and reply.startswith("Nothing was removed"), reply


def test_a_decomposed_spelling_of_the_word_is_the_same_word(isolated_pages):
    """An NFD copy of "Brötchenhaus" survived a forget that reported "applied" (review of 9b78dac)."""
    import unicodedata

    from core.memory.files import memory_entries_path

    chat = "run-history-unicode"
    decomposed = unicodedata.normalize("NFD", "Brötchenhaus")
    _remember(isolated_pages, chat, f"my favourite bakery is {decomposed}")
    _turn_history(chat, f"Tell me about {decomposed}.", "It bakes at dawn.")

    handled, reply = command(isolated_pages, chat, "Forget Brötchenhaus.")

    assert handled and reply.startswith("Forget applied"), reply
    for text in (memory_entries_path().read_text(encoding="utf-8"), _session_words(chat)):
        normalized = unicodedata.normalize("NFC", json.loads(json.dumps(text)))
        assert "brötchenhaus" not in normalized.casefold()


# ── the served workflow ──────────────────────────────────────────────────────


def _pin(daemon: Any, session: str, role: str, text: str) -> None:
    from urllib.request import Request, urlopen

    body = json.dumps({"session_id": session, "role": role, "text": text}).encode("utf-8")
    request = Request(f"{daemon.base_url}/api/chat/pin", data=body, headers={"Content-Type": "application/json"},
                      method="POST")
    with urlopen(request, timeout=60) as response:
        assert json.loads(response.read().decode("utf-8"))["ok"]


def _served_pins(daemon: Any, session: str) -> list[str]:
    from urllib.parse import quote
    from urllib.request import urlopen

    with urlopen(f"{daemon.base_url}/api/chat/pins?session={quote(session)}", timeout=60) as response:
        return [pin["text"] for pin in json.loads(response.read().decode("utf-8"))["pins"]]


def test_served_forget_leaves_no_forgotten_words_in_the_runtime_database(served):
    """The real daemon, the real /api/chat door, the real model lane (scripted provider): the database file
    and its write-ahead log hold no byte of the forgotten words after "Forget applied", while the daemon
    still runs, and the words the person did not forget are still there."""
    daemon, home = served["daemon"], served["home"]
    session = "forget-run-history"
    answers = {}
    for message in (
        "Remember that my sister's bakery is called Flourstone Bakehouse.",
        "Remember that my harbor lantern is painted signal green.",
        "Tell me a short rhyme about the Flourstone Bakehouse.",
    ):
        answers[message] = _reply_text(daemon.chat(message, session_id=session, mode="auto")).strip()
        assert answers[message]
    # The person pins their question, the answer, and something else (the pin panel's own door).
    rhyme_request = "Tell me a short rhyme about the Flourstone Bakehouse."
    _pin(daemon, session, "user", rhyme_request)
    _pin(daemon, session, "assistant", answers[rhyme_request])
    _pin(daemon, session, "user", "Remember that my harbor lantern is painted signal green.")
    db_path = home / "data" / "vool_web0_v2.db"
    before = _db_carriers(db_path, "flourstone")
    assert {"runtime_session_events.message", "runtime_checkpoints.request_text",
            "runtime_attempts.original_request_snapshot", "obligation_sets.snapshot_json",
            "a7_finalizations.canonical_content"} <= set(before), before

    reply = _reply_text(daemon.chat(f"Forget {CANARY}.", session_id=session, mode="auto"))

    assert reply.startswith("Forget applied"), reply
    assert _db_carriers(db_path, "flourstone") == []
    assert _file_carriers(db_path, "flourstone") == []
    # Each answer the forget erased went through the A8 erasure with its derivative sweep completed.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        erased = {row[0] for row in conn.execute(
            "SELECT finalization_id FROM a7_governance_events WHERE actor = 'memory_forget'")}
        swept = {row[0] for row in conn.execute(
            "SELECT finalization_id FROM a7_governance_events WHERE event_kind = 'erasure_sweep_complete'")}
    finally:
        conn.close()
    assert erased and erased <= swept, (erased, swept)
    # Pinned messages carrying the words are gone from the store and its backup; the other pin stays.
    assert _served_pins(daemon, session) == ["Remember that my harbor lantern is painted signal green."]
    for name in ("message_pins.json", "message_pins.json.bak"):
        assert "flourstone" not in (home / "data" / name).read_text(encoding="utf-8").casefold(), name
    kept = _db_carriers(db_path, BYSTANDER)
    assert "runtime_checkpoints.request_text" in kept, kept
    entries = (home / "data" / "memory_entries.jsonl").read_text(encoding="utf-8")
    assert BYSTANDER in entries and "flourstone" not in entries.casefold()
    # The next turn in the same chat still works, and writes nothing of the forgotten words back.
    assert _reply_text(daemon.chat("Tell me a short rhyme about harbor lanterns.", session_id=session,
                                   mode="auto")).strip()
    assert _db_carriers(db_path, "flourstone") == []
