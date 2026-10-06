"""Long-record window selection must use the same lexical matching authority as
retrieval (review R6, 2026-09-27).

A >500-character sentence is admitted and retrieved by the scoped BM25 leg
after the stemming repair, but the window finder anchored ONLY on raw substring
occurrences: for the query "Where are trolleys?" against a stored clause using
"trolley", no anchor existed, the window was refused, and the generic fallback
clipped the first 360 characters — the record was retrieved and the answer then
discarded at selection. Anchors and window scoring now use raw-substring OR
Porter-stem matching (the one stemming authority), with windows taken from the
ORIGINAL span (word-boundary snapped, never mid-word).

Controls: the short form of the same fact, qualifier/negation clauses inside
the long sentence, provenance survival, and an unrelated no-answer question.
"""
from __future__ import annotations

import pytest

from core.context_namespace import ensure_chat_namespace
from core.context_retrieval import inject_retrieved, store_turn
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

FILLER = "silver canvas banners line the quiet hall and the decorative border repeats " * 9
TROLLEY_FACT = "the trolley is kept beside the eastern ramp."
LONG_WITH_NEGATION = (
    FILLER
    + "the service lift is not for passengers, and whenever the bay doors are open "
    + TROLLEY_FACT
)


@pytest.fixture()
def long_store(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    chat = "window-selection-chat"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for text in (
        "Please remember: " + TROLLEY_FACT,          # short form control
        "Please remember: " + FILLER + TROLLEY_FACT,  # R6 shape (review fixture class)
        "Please remember: " + LONG_WITH_NEGATION,     # qualifiers + negation + fact
    ):
        status = store_turn(
            chat, text, "Noted.", access_policy=policy,
            source_context={"chat_id": chat, "runtime_home": str(home)},
        )
        assert status["status"] == "stored", status
    yield chat, home, policy
    configure_runtime_home(None)


def _context(home, chat, policy, query):
    out = inject_retrieved(
        chat, query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": str(home)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    return next(
        (str(m.get("content") or "") for m in out
         if m.get("role") == "system" and "<retrieved_context>" in str(m.get("content") or "")),
        "",
    )


def test_short_form_fact_still_delivered(long_store):
    chat, home, policy = long_store
    ctx = _context(home, chat, policy, "Where is the trolley kept?")
    assert "eastern ramp" in ctx, ctx


def test_morphology_matched_clause_survives_long_sentence(long_store):
    """The R6 shape: plural query, singular stored word, fact at the very end."""
    chat, home, policy = long_store
    ctx = _context(home, chat, policy, "Where are trolleys?")
    assert "beside the eastern ramp" in ctx, ctx


def test_long_sentence_negation_and_condition_survive(long_store):
    chat, home, policy = long_store
    ctx = _context(home, chat, policy, "Where are trolleys?")
    assert "not for passengers" in ctx, ctx
    assert "whenever the bay doors are open" in ctx, ctx


def test_delivered_window_is_bounded_not_the_whole_sentence(long_store):
    chat, home, policy = long_store
    ctx = _context(home, chat, policy, "Where are trolleys?")
    # The first 500+ char preamble must not be delivered wholesale...
    assert "silver canvas banners line the quiet hall and the decorative border repeats silver" \
        not in ctx, ctx


def test_provenance_suffix_survives_long_window(long_store):
    chat, home, policy = long_store
    ctx = _context(home, chat, policy, "Where are trolleys?")
    assert "(recorded: 20" in ctx, ctx  # record-date provenance travels with the line


def test_unrelated_question_gets_no_context(long_store):
    chat, home, policy = long_store
    ctx = _context(home, chat, policy, "Who painted the sundial?")
    assert ctx == "", ctx


def test_window_snaps_to_word_boundaries():
    from core.context_retrieval import _answer_bearing_window, _query_overlap_terms

    filler = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu " * 8
    text = filler + "the forklifts park beside the northern ramp."
    window = _answer_bearing_window(text, _query_overlap_terms("Where do forklifts park?"))
    assert window is not None
    assert "northern ramp" in window
    assert window[0] not in (" ", "\t", "\n")  # original-span word snap
    # no anchor at all -> None (record then follows the generic fallback path)
    assert _answer_bearing_window(text, _query_overlap_terms("unrelated orchid question")) is None
