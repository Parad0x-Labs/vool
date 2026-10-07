"""A memory store written before the v14 memory layers opens, keeps every memory and upgrades without a reset.

The fixture is a synthetic store written by the pre-v14 product (main at 9adff83, authored statements only): semantic
nodes and their search index, no source-occurrence layer, no memory receipts. The current product, kernel on, must
serve those memories, write new turns beside them (creating the new tables in place), and never drop a row.
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

FIXTURE = Path(__file__).parent / "fixtures" / "pre_v14_memory_store.sql"
CHAT = "chat-old"


@pytest.fixture()
def old_store(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_HOP",
                 "VOOL_EVIDENCE_VERIFY", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    db = profile / "data" / "memory" / "vool_memory.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db))
    try:
        con.executescript(FIXTURE.read_text())
    finally:
        con.close()
    configure_runtime_home(profile)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    yield profile, db
    configure_runtime_home(None)


def _tables(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return names, {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("memory_nodes",) if t in names}
    finally:
        con.close()


def test_the_old_store_serves_its_memories_and_upgrades_in_place(old_store):
    profile, db = old_store
    names, counts = _tables(db)
    assert "source_occurrences" not in names and counts["memory_nodes"] == 3
    policy = resolve_memory_access_policy(chat_id=CHAT)
    question = "How much did my bike helmet cost?"
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(CHAT, question, [{"role": "user", "content": question}], access_policy=policy,
                              source_context={"chat_id": CHAT, "runtime_home": str(profile)})
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    assert "$95" in block, block
    stated = float(calendar.timegm(datetime(2025, 1, 2, 9, tzinfo=timezone.utc).timetuple()))
    written = cr.store_turn(CHAT, "I added bike lights for $40.", "Noted.", access_policy=policy,
                            source_context={"chat_id": CHAT, "runtime_home": str(profile), "statement_at": stated})
    assert written["status"] in {"stored", "retained"} and len(written["occurrence_ids"]) == 2, written
    names, counts = _tables(db)
    assert {"source_occurrences", "memory_receipts"} <= names
    assert counts["memory_nodes"] >= 3  # no pre-v14 memory was dropped
    again = cr.inject_retrieved(CHAT, question, [{"role": "user", "content": question}], access_policy=policy,
                                source_context={"chat_id": CHAT, "runtime_home": str(profile)})
    assert "$95" in "\n".join(str(m.get("content") or "") for m in again), again
