"""Paraphrase recall through the native retrieval path (scoped BM25 stemming fix).

Reproduced product failure (2026-09-27, 5b6fb6df): "Where do guests park?" could
not surface a stored "Parking: the north lot." — the session-scoped BM25 leg
matched exact tokens only ("park" != "parking", "guests" != "guest"), the FTS5
index it mirrors stems with porter, and the semantic leg was under the floor, so
hybrid search returned zero hits and the final provider-boundary context was
empty. The owning fix stems both sides of the scoped leg with the same porter
authority (core/porter_stem.py, equivalence pinned by
tests/test_porter_stem_equivalence.py).

Controls: a no-answer question about nothing stored must stay empty, and a
stem-disjoint distractor record must not be injected for the parking question.
"""
from __future__ import annotations

import pytest

from core.context_namespace import ensure_chat_namespace
from core.context_retrieval import inject_retrieved, store_turn
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home
from core.vool_memory import VoolMemory

MULTI_FACT = (
    "Please remember the observatory open-day notes:\n"
    "- Telescope filter: Hydrogen-alpha.\n"
    "- Guest speaker: Dr. Ines Okafor.\n"
    "- Parking: the north lot."
)
PARKING_QUESTION = "Where do guests park?"
DISTRACTOR = "Please remember: the greenhouse misting schedule is 06:15 daily."


@pytest.fixture()
def scoped_store(tmp_path, monkeypatch):
    """A real store→close→reopen flow in an isolated profile, hash embeddings."""
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    chat = "paraphrase-recall-chat"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    store_turn(
        chat, MULTI_FACT, "Noted.", access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(home)},
    )
    store_turn(
        chat, DISTRACTOR, "Noted.", access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(home)},
    )
    yield chat, home, policy
    configure_runtime_home(None)


def _injected_block(transcript):
    return next(
        (str(m.get("content") or "")
         for m in transcript
         if m.get("role") == "system" and "<retrieved_context>" in str(m.get("content") or "")),
        "",
    )


def _inject(chat, home, policy, query):
    out = inject_retrieved(
        chat, query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(home)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return _injected_block(out)


def test_scoped_bm25_stems_match_morphological_variants(scoped_store):
    chat, home, _policy = scoped_store
    mem = VoolMemory(runtime_home=str(home))
    scores = mem._bm25_scores(PARKING_QUESTION, session_id=chat)
    parking_rows = [
        r["node_id"] for r in mem._conn.execute(
            "SELECT node_id, content FROM memory_nodes WHERE valid_to IS NULL"
        ).fetchall()
        if "Parking: the north lot." in str(r["content"])
    ]
    mem.close()
    assert parking_rows, "multi-fact record must be stored"
    assert all(
        (scores.get(node_id) or 0.0) > 0.0 for node_id in parking_rows
    ), f"parking record must earn a lexical-leg score; got {scores}"


def test_paraphrased_parking_question_delivers_the_fact(scoped_store):
    chat, home, policy = scoped_store
    block = _inject(chat, home, policy, PARKING_QUESTION)
    assert "Parking: the north lot." in block, block
    # The delivered line must keep the proposition intact, not just the substring.
    assert "north lot" in block.lower()


def test_no_answer_question_stays_empty(scoped_store):
    chat, home, policy = scoped_store
    block = _inject(chat, home, policy, "What does the crew eat for lunch?")
    assert block == ""


def test_stem_disjoint_distractor_not_injected_for_parking(scoped_store):
    chat, home, policy = scoped_store
    block = _inject(chat, home, policy, PARKING_QUESTION)
    assert "misting" not in block.lower()
    assert "06:15" not in block
