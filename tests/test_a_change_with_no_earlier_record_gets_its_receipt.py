"""A turn that states a change ("I switched from X to Y") with no earlier record of X still gets its memory receipt.

Such a turn yields a CHANGE row whose old occurrence is unknown (rule transition-stated-in-turn). head_text sliced
that None and raised TypeError, so the receipt write failed; the "rebuilt on the next read" path called the same
write and failed the same way every time. Exactly the turns where the user states a change were missing from the
evidence packet. Every name and value below is written for this file.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from core import memory_receipts
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

CHAT = "chat-switch"
CHANGE_TURN = "I switched from beekeeping to pottery last month, the studio is near the river."


def _epoch(y, m, d):
    return float(calendar.timegm(datetime(y, m, d, 9, 0, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    yield str(profile)
    configure_runtime_home(None)


def _store(home, text, stated):
    policy = resolve_memory_access_policy(chat_id=CHAT)
    cr.store_turn(CHAT, text, "Noted.", access_policy=policy,
                  source_context={"chat_id": CHAT, "runtime_home": home, "statement_at": stated})


def _receipts(home):
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=home)
    try:
        return memory_receipts.receipts_for_scope(mem, CHAT)
    finally:
        close = getattr(mem, "close", None)
        if callable(close):
            close()


def test_the_change_turn_gets_its_receipt_with_the_new_value(home):
    _store(home, "The allotment gate code is 4471.", _epoch(2025, 4, 1))
    _store(home, CHANGE_TURN, _epoch(2025, 6, 3))
    receipts = _receipts(home)
    change = [r for r in receipts if "pottery" in str(r.get("said") or "") or "pottery" in str(r.get("head_text") or "")]
    assert change, [r.get("head_text") for r in receipts]
    rows = [c for r in change for c in r.get("changes") or []]
    assert any(c.get("new_value", "").lower() == "pottery" and c.get("old_value", "").lower() == "beekeeping"
               for c in rows), rows
    assert "pottery" in change[0]["head_text"] and "beekeeping" in change[0]["head_text"], change[0]["head_text"]


def test_the_change_reaches_the_evidence_packet(home):
    _store(home, CHANGE_TURN, _epoch(2025, 6, 3))
    policy = resolve_memory_access_policy(chat_id=CHAT)
    cr.reset_retrieval_telemetry()
    question = "What hobby did I switch to?"
    cr.inject_retrieved(CHAT, question, [{"role": "user", "content": question}], access_policy=policy,
                        source_context={"chat_id": CHAT, "runtime_home": home})
    facts = list(cr.get_last_retrieval_telemetry().get("evidence_packet_facts") or [])
    assert any("pottery" in str(f).lower() for f in facts), facts
