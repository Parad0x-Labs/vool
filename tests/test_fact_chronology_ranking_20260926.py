"""Fact chronology versus access popularity (2026-09-26).

A controlled rank probe showed that 12 targeted recalls of an older Monday
statement made it outrank the newer Friday statement for the same subject:
effective importance computed its recency term from last_access, so merely
READING a record laundered it into looking freshly stated, and the bounded
frequency term compounded the flip. Recency is now knowledge time (the
record's own timestamp); access frequency remains a bounded usefulness
signal; explicit valid-time invalidation and restatement collapse are
unchanged, and superseded-but-not-invalidated statements stay retrievable
for historical questions.
"""
from __future__ import annotations

import time

from core.vool_memory import VoolMemory

DAY = 86400.0


def _store_pair(memory: VoolMemory, scope: str, *, older_ts: float, newer_ts: float):
    def put(text, ts):
        return memory.node_store(
            content=text,
            keywords=[],
            tags=["user", f"session:{scope}"],
            context_description=f"session={scope} role=user",
            embedding=[0.0, 1.0, 0.0],
            embedding_backend="diagnostic",
            timestamp=ts,
        )

    older = put("Orchid delivery window is Monday.", older_ts)
    newer = put("Orchid delivery window is Friday.", newer_ts)
    return older, newer


def _search(memory: VoolMemory, scope: str, text: str, k: int = 2):
    hits = memory.node_search_hybrid(
        text,
        [1.0, 0.0, 0.0],
        top_k=k,
        min_score=0.0,
        session_id="chronology-chat",
        query_embedding_backend="diagnostic",
    )
    return [node.content for node, _score in hits]


def test_repeated_recalls_do_not_flip_fact_chronology(tmp_path) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.memory import entries as memory_entries

    ensure_chat_namespace("chronology-chat", grant_current_receipts=False)
    scope = memory_entries  # namespace ensured above; scope key via retrieval helper
    from core.context_retrieval import _session_scope_key

    session_scope = _session_scope_key("chronology-chat")
    memory = VoolMemory(db_path=tmp_path / "rank.db")
    try:
        now = time.time()
        older, newer = _store_pair(
            memory, session_scope, older_ts=now - 30 * DAY, newer_ts=now - DAY
        )
        assert older.node_id and newer.node_id

        before = _search(memory, session_scope, "Orchid delivery window")
        assert before[0] == "Orchid delivery window is Friday."  # newer value first

        for _ in range(12):
            _search(memory, session_scope, "Monday", k=1)

        after = _search(memory, session_scope, "Orchid delivery window")
        assert after[0] == "Orchid delivery window is Friday.", (
            "access popularity must not override fact chronology"
        )
        # the older statement remains visible for provenance/historical recall
        assert "Orchid delivery window is Monday." in after
    finally:
        memory.close()


def test_historical_question_still_finds_the_older_statement(tmp_path) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import _session_scope_key

    ensure_chat_namespace("chronology-chat", grant_current_receipts=False)
    session_scope = _session_scope_key("chronology-chat")
    memory = VoolMemory(db_path=tmp_path / "rank.db")
    try:
        now = time.time()
        _store_pair(memory, session_scope, older_ts=now - 30 * DAY, newer_ts=now - DAY)
        hits = _search(memory, session_scope, "Orchid delivery window Monday", k=5)
        assert "Orchid delivery window is Monday." in hits
    finally:
        memory.close()


def test_popular_record_still_ranks_for_its_own_subject(tmp_path) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import _session_scope_key

    ensure_chat_namespace("chronology-chat", grant_current_receipts=False)
    session_scope = _session_scope_key("chronology-chat")
    memory = VoolMemory(db_path=tmp_path / "rank.db")
    try:
        now = time.time()
        popular = memory.node_store(
            content="Fern care guide: keep the soil moist.",
            keywords=[],
            tags=["user", f"session:{session_scope}"],
            context_description=f"session={session_scope} role=user",
            embedding=[0.0, 1.0, 0.0],
            embedding_backend="diagnostic",
            timestamp=now - 60 * DAY,
        )
        memory.node_store(
            content="Orchid delivery window is Friday.",
            keywords=[],
            tags=["user", f"session:{session_scope}"],
            context_description=f"session={session_scope} role=user",
            embedding=[0.0, 1.0, 0.0],
            embedding_backend="diagnostic",
            timestamp=now - DAY,
        )
        for _ in range(15):
            hits = memory.node_search_hybrid(
                "fern care soil",
                [1.0, 0.0, 0.0],
                top_k=1,
                min_score=0.0,
                session_id="chronology-chat",
                query_embedding_backend="diagnostic",
            )
            assert hits
        assert popular.node_id
        ranked = _search(memory, session_scope, "fern care soil", k=5)
        assert ranked and ranked[0] == "Fern care guide: keep the soil moist."
    finally:
        memory.close()


def test_ordering_survives_restart(tmp_path) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import _session_scope_key

    ensure_chat_namespace("chronology-chat", grant_current_receipts=False)
    session_scope = _session_scope_key("chronology-chat")
    memory = VoolMemory(db_path=tmp_path / "rank.db")
    now = time.time()
    _store_pair(memory, session_scope, older_ts=now - 30 * DAY, newer_ts=now - DAY)
    for _ in range(12):
        _search(memory, session_scope, "Monday", k=1)
    memory.close()

    reopened = VoolMemory(db_path=tmp_path / "rank.db")
    try:
        after = _search(reopened, session_scope, "Orchid delivery window")
        assert after[0] == "Orchid delivery window is Friday."
    finally:
        reopened.close()


def test_explicit_invalidation_still_overrides_everything(tmp_path) -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.context_retrieval import _session_scope_key

    ensure_chat_namespace("chronology-chat", grant_current_receipts=False)
    session_scope = _session_scope_key("chronology-chat")
    memory = VoolMemory(db_path=tmp_path / "rank.db")
    try:
        now = time.time()
        older, newer = _store_pair(
            memory, session_scope, older_ts=now - 30 * DAY, newer_ts=now - DAY
        )
        memory.node_invalidate(newer.node_id)
        hits = _search(memory, session_scope, "Orchid delivery window", k=5)
        assert "Orchid delivery window is Friday." not in hits
        assert "Orchid delivery window is Monday." in hits  # historical row retained
    finally:
        memory.close()
