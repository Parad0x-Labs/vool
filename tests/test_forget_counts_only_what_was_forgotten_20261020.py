"""A forget reports only memory it removed, and forgets every target a message names (2026-10-20).

Measured on a served daemon at 153148eb (the 0.7.2 train head):

- "Forget storage label MINT-84." in a chat that never stored it answered "Forget applied. Removed 1 memory entry."
  The one row was the previous turn's finalized answer quoting the words (run history). The answer quality run
  saw "Removed 2 memory entries" for "Forget my name. Forget storage label MINT-84." the same way. The memory
  law says a forget that found nothing says nothing was forgotten.
- "Forget MINT-84. Forget 5906." with both stored answered 'Nothing was removed ... matched "MINT-84. Forget
  5906"' and both stayed in memory: the two targets were searched as one string.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_forget_scrubs_run_history import (
    _db_carriers,
    _file_carriers,
    _remember,
    _runtime_db,
    _session_words,
    _turn_history,
    command,
    isolated_pages,  # the runtime home fixture
)
from tests.test_memory_forget_served_p0 import _reply_text, served  # served: the daemon fixture
from tests.test_question_date_time_leg_20261002 import _hash_backend

pytestmark = pytest.mark.usefixtures("_hash_backend")

LABEL = "MINT-84"
CODE = "5906"
BYSTANDER = "signal green"


def _live_entries() -> list[str]:
    from core.memory.files import memory_entries_path

    path = memory_entries_path()
    if not path.exists():
        return []
    return [str(row.get("text") or "") for row in map(json.loads, path.read_text(encoding="utf-8").splitlines())
            if row.get("status") != "erased"]


def test_a_forget_that_only_scrubs_chat_history_says_nothing_was_removed(isolated_pages):
    """Nothing stored; the chat's own earlier turn quoted the words. The run history loses the words, and the
    reply still says nothing was removed: those rows are the chat's history, not memory."""
    chat = "forget-count-history-only"
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(chat, grant_current_receipts=False)
    _turn_history(chat, "Put storage label MINT-84 on the blue box.", "Storage label MINT-84 it is.")
    _turn_history(chat, "Remember that my harbor lantern is painted signal green.", "Noted.")

    handled, reply = command(isolated_pages, chat, f"Forget storage label {LABEL}.")

    assert handled and reply.startswith("Nothing was removed"), reply
    assert "Removed" not in reply and "Forget applied" not in reply, reply
    words = _session_words(chat)
    assert LABEL.casefold() not in words.casefold(), words
    assert "signal green" in words


def test_two_named_targets_are_both_forgotten_and_counted_once_each(isolated_pages):
    chat = "forget-count-two-targets"
    _remember(isolated_pages, chat, f"my storage label is {LABEL}.")
    _remember(isolated_pages, chat, f"my locker code is {CODE}.")
    _remember(isolated_pages, chat, "my harbor lantern is painted signal green.")

    handled, reply = command(isolated_pages, chat, f"Forget {LABEL} and forget {CODE}.")

    assert handled and reply == "Forget applied. Removed 2 memory entries.", reply
    assert _live_entries() == ["my harbor lantern is painted signal green."]
    # Both stay forgotten in this chat: saying either again is refused calmly, not stored.
    for fact in (f"my storage label is {LABEL}.", f"my locker code is {CODE}."):
        handled, refused = command(isolated_pages, chat, "Remember that " + fact)
        assert handled and "You asked me to forget that in this chat" in refused, (fact, refused)
    assert _live_entries() == ["my harbor lantern is painted signal green."]


def test_one_target_stored_and_one_not_reports_each(isolated_pages):
    chat = "forget-count-partial"
    _remember(isolated_pages, chat, f"storage label {LABEL} goes on the blue box.")

    handled, reply = command(isolated_pages, chat, f"Forget my locker code {CODE}. Forget storage label {LABEL}.")

    assert handled, reply
    assert reply.splitlines() == [
        f'Nothing was removed. Forget found no memory in this chat that matched "my locker code {CODE}".',
        "Forget applied. Removed 1 memory entry.",
    ], reply
    assert _live_entries() == []


def test_two_targets_with_nothing_stored_say_nothing_was_removed_for_each(isolated_pages):
    chat = "forget-count-two-empty"
    from core.context_namespace import ensure_chat_namespace

    ensure_chat_namespace(chat, grant_current_receipts=False)
    _turn_history(chat, f"Is {LABEL} a good label for locker {CODE}?", "Either works.")

    handled, reply = command(isolated_pages, chat, f"Forget {LABEL}. Forget {CODE}.")

    assert handled and "Removed" not in reply and "Forget applied" not in reply, reply
    assert reply.splitlines() == [
        f'Nothing was removed. Forget found no memory in this chat that matched "{LABEL}".',
        f'Nothing was removed. Forget found no memory in this chat that matched "{CODE}".',
    ], reply
    words = _session_words(chat)
    assert LABEL.casefold() not in words.casefold() and CODE not in words, words


def test_a_quoted_phrase_holding_forget_is_one_target(isolated_pages):
    chat = "forget-count-quoted"
    _remember(isolated_pages, chat, "my favourite song line is 'stop and forget me'.")
    _remember(isolated_pages, chat, "my harbor lantern is painted signal green.")

    handled, reply = command(isolated_pages, chat, "Forget the phrase 'stop and forget me'.")

    assert handled and reply == "Forget applied. Removed 1 memory entry.", reply
    assert _live_entries() == ["my harbor lantern is painted signal green."]


def test_an_apostrophe_is_no_quote_and_keeps_two_targets_apart(isolated_pages):
    chat = "forget-count-possessive"
    _remember(isolated_pages, chat, "the kids' toys live in the attic.")
    _remember(isolated_pages, chat, f"my storage label is {LABEL}.")
    _remember(isolated_pages, chat, "my harbor lantern is painted signal green.")

    handled, reply = command(isolated_pages, chat, f"Forget the kids' toys. Forget {LABEL}.")

    assert handled and reply == "Forget applied. Removed 2 memory entries.", reply
    assert _live_entries() == ["my harbor lantern is painted signal green."]


def test_one_target_failing_while_another_is_forgotten_is_never_reported_as_applied(isolated_pages):
    from storage.db import get_connection

    chat = "forget-count-one-fails"
    _remember(isolated_pages, chat, f"my storage label is {LABEL}.")
    _remember(isolated_pages, chat, f"my locker code is {CODE}.")
    _turn_history(chat, f"Which locker has code {CODE}?", "The blue one.")
    conn = get_connection()
    try:
        conn.execute("CREATE TRIGGER refuse_event_erasure BEFORE DELETE ON runtime_session_events "
                     "BEGIN SELECT RAISE(ABORT, 'event store unavailable'); END")
        conn.commit()
        handled, reply = command(isolated_pages, chat, f"Forget {LABEL}. Forget {CODE}.")
    finally:
        conn.execute("DROP TRIGGER IF EXISTS refuse_event_erasure")
        conn.commit()
        conn.close()

    lines = reply.splitlines()
    assert handled and len(lines) == 2, reply
    assert lines[0] == "Forget applied. Removed 1 memory entry.", reply
    assert lines[1].startswith("Forget could not be verified as fully applied") and "Please retry the forget" in lines[1], reply

    # The retry finishes the run history the first attempt could not erase, and says it removed rows.
    handled, retry = command(isolated_pages, chat, f"Forget {CODE}.")
    assert handled and retry.startswith("Forget applied. Removed "), retry
    assert CODE not in _session_words(chat)
    assert _live_entries() == []


def _carriers_outside_the_revocation_ledger(home: Path, needle: str) -> dict[str, list[str]]:
    """Every database (and its -wal/-shm) and text store under a home that still holds ``needle``. The chat's
    revocation ledger keeps the canonical token by design: it is the authority that refuses the words later."""
    found: dict[str, list[str]] = {}
    for database in sorted(home.rglob("*.db")):
        rows = _db_carriers(database, needle)
        # Where the ledger holds the very bytes, its file cannot speak for the other tables; their rows still do.
        file_bytes = [] if "memory_revocations.token" in rows else _file_carriers(database, needle)
        hits = [hit for hit in rows if hit != "memory_revocations.token"] + file_bytes
        if hits:
            found[str(database.relative_to(home))] = hits
    for text_file in sorted([*home.rglob("*.jsonl"), *home.rglob("*.md")]):
        if needle.casefold() in text_file.read_text(encoding="utf-8", errors="replace").casefold():
            found[str(text_file.relative_to(home))] = ["text"]
    return found


def test_a_multi_target_forget_leaves_no_bytes_in_the_database_or_its_write_ahead_log(isolated_pages):
    chat = "forget-count-erasure-state"
    _remember(isolated_pages, chat, f"my storage label is {LABEL}.")
    _remember(isolated_pages, chat, f"my locker code is {CODE}.")
    _remember(isolated_pages, chat, "my harbor lantern is painted signal green.")
    _turn_history(chat, f"Which box has label {LABEL}?", f"The blue one, locker {CODE}.")
    home = Path(isolated_pages)
    assert _carriers_outside_the_revocation_ledger(home, LABEL), "probe is blind: the label was never written"
    assert _file_carriers(_runtime_db(), LABEL), "probe is blind: the label never reached the database file"

    handled, reply = command(isolated_pages, chat, f"Forget {LABEL}. Forget {CODE}.")

    assert handled and reply == "Forget applied. Removed 2 memory entries.", reply
    for needle in (LABEL, CODE):
        assert _carriers_outside_the_revocation_ledger(home, needle) == {}, needle
        assert _file_carriers(_runtime_db(), needle) == [], needle
    assert BYSTANDER in "\n".join(_live_entries())
    assert _carriers_outside_the_revocation_ledger(home, BYSTANDER)


def test_a_forget_leaves_no_terms_in_the_full_text_index_pages(isolated_pages):
    """Mac smoke on 4ed0e1f9 (also v0.7.1): after "Forget silver birch" the words were still in vool_memory.db.
    FTS5 keeps a deleted row's terms in its index pages until the segments merge."""
    from tests.test_question_date_time_leg_20261002 import _ingest

    chat = "forget-count-index-terms"
    _ingest(isolated_pages, chat, [(1792400000.0, "My favourite tree is the silver birch."),
                                   (1792400100.0, "My harbor lantern is painted signal green.")])
    memory_db = Path(isolated_pages) / "data" / "memory" / "vool_memory.db"

    def index_terms(term: bytes) -> list[str]:
        conn = sqlite3.connect(f"file:{memory_db}?mode=ro", uri=True)
        try:
            return [table for table in ("memory_fts_data", "source_fts_data")
                    if any(term in bytes(row[0] or b"") for row in conn.execute(f'SELECT block FROM "{table}"'))]
        finally:
            conn.close()

    assert index_terms(b"birch"), "probe is blind: the index never held the words"

    handled, reply = command(isolated_pages, chat, "Forget silver birch.")

    assert handled and reply.startswith("Forget applied"), reply
    for term in (b"silver", b"birch"):
        assert index_terms(term) == [], term
    assert index_terms(b"lantern"), "the words nobody forgot must stay searchable"
    conn = sqlite3.connect(f"file:{memory_db}?mode=ro", uri=True)
    try:
        # The revocation ledger keeps the token by design (it refuses the words later); nothing else does.
        assert [row[0] for row in conn.execute("SELECT token FROM memory_revocations")] == ["silver birch"]
    finally:
        conn.close()
    assert _carriers_outside_the_revocation_ledger(Path(isolated_pages), "birch") == {}


def test_served_forget_counts_only_memory_and_forgets_the_named_label(served):
    """The real daemon and /api/chat door with the certified scripted model: the reported message in a chat that
    stored the label, then the same words again."""
    daemon, home = served["daemon"], served["home"]
    stored = "forget-count-served"
    for message in (f"Remember that storage label {LABEL} goes on the blue box.",
                    "Remember that my harbor lantern is painted signal green.",
                    f"Which box did I put storage label {LABEL} on?"):
        assert _reply_text(daemon.chat(message, session_id=stored, mode="auto")).strip()
    assert _carriers_outside_the_revocation_ledger(home, LABEL), "probe is blind: the label was never written"

    reply = _reply_text(daemon.chat(f"Forget my name. Forget storage label {LABEL}.", session_id=stored, mode="auto"))

    # The entry, and the chat's semantic records of the turns that quoted it.
    assert re.fullmatch(r"Forget applied\. Removed [1-9]\d* memory entr(?:y|ies)\.", reply), reply
    # The daemon still runs: the database files and their write-ahead logs as they are now.
    assert _carriers_outside_the_revocation_ledger(home, LABEL) == {}
    entries = (home / "data" / "memory_entries.jsonl").read_text(encoding="utf-8")
    assert BYSTANDER in entries and LABEL.casefold() not in entries.casefold()

    again = _reply_text(daemon.chat(f"Forget storage label {LABEL}.", session_id=stored, mode="auto"))
    assert again.startswith("Nothing was removed") and "Removed" not in again, again
    refused = _reply_text(daemon.chat(f"Remember that storage label {LABEL} goes on the blue box.", session_id=stored,
                                      mode="auto"))
    assert "You asked me to forget that in this chat" in refused, refused


def test_served_forgets_in_a_chat_that_stored_nothing_say_nothing_was_removed_each_time(served):
    """Each forget's own turn quotes the words (its run history, its finalized answer); the next forget erases
    those rows and answered "Forget applied. Removed 1 memory entry." over them."""
    daemon, empty = served["daemon"], "forget-count-served-empty"
    for message in (f"Forget my name. Forget storage label {LABEL}.", f"Forget storage label {LABEL}.",
                    f"Forget {LABEL}."):
        reply = _reply_text(daemon.chat(message, session_id=empty, mode="auto"))
        assert reply.startswith("Nothing was removed") and "Removed" not in reply, (message, reply)
