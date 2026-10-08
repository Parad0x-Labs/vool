"""The canonical occurrence write comes before every index over it; an index failure costs a rebuild, not a memory.

With the memory kernel on, a failed receipt write raised out of the turn write: the turn's remaining occurrences
(the assistant's reply, or the user's when the first receipt failed) and its semantic index admission never ran.
And the history import referenced names it never defined once receipts were on, so it stopped at its first record.
Now every canonical write of the turn lands, the failure is reported (status failed, reason named), and the next
read of the chat rebuilds the missing receipts.
"""
from __future__ import annotations

import calendar
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d):
    return float(calendar.timegm(datetime(y, m, d, 9, 0, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_HOP",
                 "VOOL_EVIDENCE_VERIFY", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def _db(home):
    return sqlite3.connect(f"file:{Path(home) / 'data' / 'memory' / 'vool_memory.db'}?mode=ro", uri=True)


def _store(home, chat, user, assistant, stated):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    return cr.store_turn(chat, user, assistant, access_policy=resolve_memory_access_policy(chat_id=chat),
                         source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated})


def _ask(home, chat, question):
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}],
                              access_policy=resolve_memory_access_policy(chat_id=chat),
                              source_context={"chat_id": chat, "runtime_home": home})
    return "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))


def test_a_failed_receipt_keeps_every_canonical_write_and_is_rebuilt_on_the_next_read(home, monkeypatch):
    import core.memory_receipts as mr

    real = mr.write_receipt
    monkeypatch.setattr(mr, "write_receipt", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    out = _store(home, "chat-crash", "My bike helmet cost $95 last weekend.", "Noted, $95 for the helmet.", _epoch(2025, 3, 2))
    assert out["status"] == "failed" and out["reason"] == "receipt_write_error", out
    assert len(out["occurrence_ids"]) == 2, out  # the user's AND the assistant's turn both landed
    con = _db(home)
    try:
        assert con.execute("SELECT COUNT(*) FROM source_occurrences WHERE chat_scope = 'chat-crash' AND status = 'active'").fetchone()[0] == 2
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "memory_receipts" in tables:
            assert con.execute("SELECT COUNT(*) FROM memory_receipts WHERE chat_scope = 'chat-crash'").fetchone()[0] == 0
    finally:
        con.close()
    monkeypatch.setattr(mr, "write_receipt", real)  # the disk is back
    block = _ask(home, "chat-crash", "How much did my bike helmet cost?")
    assert "$95" in block and "Evidence receipts" in block, block
    con = _db(home)
    try:
        assert con.execute("SELECT COUNT(*) FROM memory_receipts WHERE chat_scope = 'chat-crash'").fetchone()[0] == 2
    finally:
        con.close()


def test_a_stop_between_the_occurrence_and_its_receipt_is_rebuilt_on_restart(home):
    _store(home, "chat-stop", "I renewed my passport in March for $130.", "Ok.", _epoch(2025, 3, 20))
    con = sqlite3.connect(str(Path(home) / "data" / "memory" / "vool_memory.db"))
    try:
        con.execute("DELETE FROM memory_receipts WHERE chat_scope = 'chat-stop'")  # the receipt write never happened
        con.commit()
    finally:
        con.close()
    block = _ask(home, "chat-stop", "How much did my passport renewal cost?")
    assert "$130" in block and "Evidence receipts" in block, block


def test_the_history_import_retains_every_record_with_the_kernel_on(home):
    ensure_chat_namespace("chat-import", grant_current_receipts=False)
    records = [
        {"role": "user", "text": "I adopted a cat named Miso in 2021.", "statement_at": "2021-05-01T09:00:00Z"},
        {"role": "assistant", "text": "Miso is a lovely name.", "statement_at": "2021-05-01T09:01:00Z"},
        {"role": "user", "text": "Miso turned three last week.", "statement_at": "2024-05-08T09:00:00Z"},
    ]
    out = cr.import_conversation_history("chat-import", records, import_batch="b1",
                                         source_context={"chat_id": "chat-import", "runtime_home": home})
    assert out["status"] == "imported", out
    assert len(out["per_record"]) == 3, out
