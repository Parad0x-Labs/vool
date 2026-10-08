"""A forgotten source, its derived receipts and its index entries never reach retrieval or bind a claim.

`forget_session_memory` retires the semantic nodes, the source body, its FTS row and its embeddings, but the memory
kernel's receipts (typed facts with the source sentence) were a fourth store it never touched: the forgotten value
came back in the receipt packet. And a packet compiled before the forget still carried the value into the binders.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

CHAT = "chat-forget"
SECRET = "4417"


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
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=CHAT)
    for text, stated in (
        (f"My gym locker code is {SECRET}, I keep forgetting it.", _epoch(2025, 3, 2)),
        ("I swim on Tuesdays and Thursdays at the gym.", _epoch(2025, 3, 3)),
        ("The gym shuts at 10pm on weekdays.", _epoch(2025, 3, 4)),
    ):
        cr.store_turn(CHAT, text, "Noted.", access_policy=policy,
                      source_context={"chat_id": CHAT, "runtime_home": str(profile), "statement_at": stated})
    yield str(profile)
    configure_runtime_home(None)


def _ask(home, question):
    policy = resolve_memory_access_policy(chat_id=CHAT)
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(CHAT, question, [{"role": "user", "content": question}], access_policy=policy,
                              source_context={"chat_id": CHAT, "runtime_home": home})
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    return block, list(cr.get_last_retrieval_telemetry().get("evidence_packet_facts") or [])


def _forget(home):
    return cr.forget_session_memory(CHAT, SECRET, source_context={"chat_id": CHAT, "runtime_home": home})


QUESTION = "What is my gym locker code?"


def test_before_the_forget_the_value_is_in_the_packet(home):
    block, facts = _ask(home, QUESTION)
    assert SECRET in block


def test_after_the_forget_no_retrieval_surface_carries_the_value(home):
    _forget(home)
    block, facts = _ask(home, QUESTION)
    assert SECRET not in block, block
    assert not any(SECRET in str(fact) for fact in facts), facts
    # No derived store keeps the forgotten bytes at rest either.
    import sqlite3
    from pathlib import Path

    db = Path(home) / "data" / "memory" / "vool_memory.db"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        leaks = {}
        for table in tables:
            if table == "memory_revocations":
                continue  # the forget law's own ledger: it holds the revoked token by design
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            for col in cols:
                try:
                    n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE CAST({col} AS TEXT) LIKE ?", (f"%{SECRET}%",)).fetchone()[0]
                except sqlite3.Error:
                    continue
                if n:
                    leaks[f"{table}.{col}"] = n
    finally:
        con.close()
    assert not leaks, leaks
    # Sibling facts of the chat still serve.
    other, _ = _ask(home, "Which days do I swim at the gym?")
    assert "Tuesdays" in other, other


def test_a_packet_compiled_before_the_forget_cannot_bind_the_value(home, monkeypatch):
    import core.bootstrap_context as bootstrap_context
    from core.unsourced_current_claim import inspect_unsourced_current_claim

    block, facts = _ask(home, QUESTION)
    assert SECRET in block
    answer = f"Your locker code is {SECRET}."

    def verdict():
        monkeypatch.setattr(bootstrap_context, "admitted_capsule_evidence_text", lambda *_a, **_k: block)
        cr.update_retrieval_telemetry(evidence_packet_facts=facts)
        return inspect_unsourced_current_claim(answer=answer, requires_current=True, user_turn_text=QUESTION,
                                               source_context={"chat_id": CHAT}, session_id=CHAT)

    before = verdict()
    assert before.claim_binding["all_supported"] is True, before.as_dict()  # the compiled packet binds the value
    _forget(home)
    after = verdict()  # the same packet, bound after the forget
    assert after.claim_binding["all_supported"] is False and after.has_evidence is False, after.as_dict()
