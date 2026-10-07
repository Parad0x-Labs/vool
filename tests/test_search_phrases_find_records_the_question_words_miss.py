"""Records found only by the turn's search phrases reach the reader.

A question whose own words match nothing in the chat ("Summarize my project" over "I'm building a recipe-sharing app
called PantryPal") is what the search-expansion call exists for: the model writes phrases ("building an app") and the
whole-turn lane searches them. Retrieval returned no_hits before that lane could deliver: two early returns counted
only the question-word lanes, and the pack step dropped the turn when no capsule line was distilled. Authored records
without speaker labels; hash-embedding fallback, so only the lexical legs run.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

CHAT = "chat-phrases"


def _epoch(y, m, d):
    return float(calendar.timegm(datetime(y, m, d, 9, 0, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(profile)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    ensure_chat_namespace(CHAT, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=CHAT)
    for text, stated in (
        ("I'm building a recipe-sharing app called PantryPal. It has a Flask backend and a React frontend.", _epoch(2025, 2, 1)),
        ("For PantryPal I added a shopping-list feature that groups ingredients by aisle.", _epoch(2025, 2, 10)),
    ):
        cr.store_turn(CHAT, text, "Nice.", access_policy=policy,
                      source_context={"chat_id": CHAT, "runtime_home": str(profile), "statement_at": stated})
    yield str(profile)
    configure_runtime_home(None)


def _ask(home, question, phrases=()):
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(CHAT, question, [{"role": "user", "content": question}],
                              access_policy=resolve_memory_access_policy(chat_id=CHAT),
                              source_context={"chat_id": CHAT, "runtime_home": home, "search_expansions": list(phrases)})
    return "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))


def test_the_search_phrases_deliver_the_records_the_question_words_miss(home):
    assert "PantryPal" not in _ask(home, "Summarize my project")  # the question's own words match nothing
    assert "PantryPal" in _ask(home, "Summarize my project", ("building an app", "app I am making"))


def test_phrases_that_match_nothing_still_deliver_nothing(home):
    assert _ask(home, "What is the capital of Peru?", ("capital of Peru", "Peru capital city")) == ""
