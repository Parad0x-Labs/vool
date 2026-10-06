"""SECOND FRESH ACCEPTANCE SET — mb-native-memrepair2-20260927, head 125d5f27.

Genuinely new wording/domains/entities versus the first set (which became
regression evidence after its first 6/9 run was inspected). Frozen before
first execution. Kayak rentals, chess club, fermentation, printer paper.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

B1_RECORD = (
    "Session date: 2026/07/11 (Sat) 07:55\n"
    "USER: The kayak group voted and I booked the Sundfjord paddle tour for 95.\n"
    "\n"
    "ASSISTANT: Smart! Popular alternatives include the Nordvik Fjord Cruise "
    "(240) and the Bjornhavn Kayak Safari (120).\n"
)
B1_QUESTION = "Which paddle tour did I book, and what did it cost?"
B1_EXPECT = "Sundfjord paddle tour for 95"
B1_FORBIDDEN = "Nordvik"

B2_RECORDS = (
    "Session date: 2026/02/03 (Mon) 18:30\n"
    "USER: Heads up, my fermentation bench holds 6 jars right now.\n",
    "Session date: 2026/03/28 (Fri) 22:10\n"
    "USER: The kimchi batch expanded, so the fermentation bench is down to "
    "4 jars after moving two to the cellar.\n",
)
B2_QUESTION = "How many jars are on my fermentation bench at the moment?"
B2_LATEST = "4 jars"
B2_STALE = "6 jars"

B3_RECORD = (
    "Session date: 2026/08/02 (Sun) 11:15\n"
    "USER: Quick note, the chess club meets in the annex on Thursdays and I "
    "paid the 30 euro semester fee at the front desk.\n"
)
B3_QUESTION = "Where does my chess club meet?"
B3_EXPECT = "annex"

B4_FILLER = "Session date: 2026/08/09 (Sun) 09:00\nUSER: Sounds perfect, cheers!"
B4_QUESTION_ONLY = (
    "Session date: 2026/08/09 (Sun) 09:05\nUSER: Should I bring printer paper?"
)


@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def _store(home, chat, text):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(
        chat, text, "Noted.", access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _inject(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat)
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy, source_context={"chat_id": chat, "runtime_home": home},
    )
    return "\n".join(
        str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content"))
    )


def test_b1_user_booking_survives_richer_assistant_alternatives(fresh_profile):
    _store(fresh_profile, "chat-b1", B1_RECORD)
    block = _inject(fresh_profile, "chat-b1", B1_QUESTION)
    assert B1_EXPECT in block
    assert B1_FORBIDDEN not in block


def test_b2_current_jar_count_supersedes_february_value(fresh_profile):
    # ingest the MARCH statement first: currency must follow stated time
    _store(fresh_profile, "chat-b2", B2_RECORDS[1])
    _store(fresh_profile, "chat-b2", B2_RECORDS[0])
    block = _inject(fresh_profile, "chat-b2", B2_QUESTION)
    assert B2_LATEST in block
    if B2_STALE in block:
        assert block.index(B2_LATEST) < block.index(B2_STALE)


def test_b3_pronounless_user_note_stored_and_recalled(fresh_profile):
    status = _store(fresh_profile, "chat-b3", B3_RECORD)
    assert status.get("status") == "stored"
    block = _inject(fresh_profile, "chat-b3", B3_QUESTION)
    assert B3_EXPECT in block


def test_b4_filler_and_question_marks_stay_unstored(fresh_profile):
    # mr29 evidence contract: filler/questions stay UNINDEXED (stored_count 0)
    # but are retained as source evidence ("retained") rather than dropped.
    for text in (B4_FILLER, B4_QUESTION_ONLY):
        result = _store(fresh_profile, "chat-b4", text)
        assert result.get("status") in ("skipped", "retained")
        assert result.get("stored_count") == 0
