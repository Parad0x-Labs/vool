"""The semantic-memory capsule reaches ordinary chat requests through the real
transcript authority (review round 2, work item 2).

Integration shape: ``canonical_runtime_transcript`` (the transcript authority
every chat request assembles from) inserts the session-scoped capsule block
BEHIND ``VOOL_CONTEXT_CAPSULE_V2`` — the same default-off flag as the capsule
path itself. The request assembler's consumer side already existed
(prompt_normalizer separates ``<retrieved_context>`` messages into the
``retrieved_capsule`` payload category with their own budget slot and switches
the profile to ``chat_capsule``); this is the producer side of that contract.

Controls: flag-off transcript unchanged; foreign-session facts never cross;
labelled secrets stay masked in the delivered block; the turn-level retrieval
telemetry is preserved (transcript assembly runs several times per turn and must
not clobber the state K-08 post-seal verification reads); denial fails closed.
"""
from __future__ import annotations

import pytest

from core.context_namespace import ensure_chat_namespace
from core.context_retrieval import (
    get_last_retrieval_telemetry,
    reset_retrieval_telemetry,
    store_turn,
    update_retrieval_telemetry,
)
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

FACT = "Please remember: the pilot boat moors at the southern pontoon."
FOREIGN = "Please remember: the crane pad lights run amber after dusk."
SECRET_WORDS = ["harbor", "lantern", "meadow", "cinder", "tundra", "willow", "ember", "fjord"]
SECRET_RECORD = "Please remember: my seed phrase is " + ", ".join(SECRET_WORDS) + "."


@pytest.fixture()
def profile(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    ensure_chat_namespace("own-chat", grant_current_receipts=False)
    ensure_chat_namespace("foreign-chat", grant_current_receipts=False)
    own = resolve_memory_access_policy(chat_id="own-chat")
    foreign = resolve_memory_access_policy(chat_id="foreign-chat")
    ctx = {"chat_id": "own-chat", "runtime_home": str(home)}
    assert store_turn("own-chat", FACT, "Noted.", access_policy=own,
                      source_context=ctx)["status"] == "stored"
    assert store_turn("own-chat", SECRET_RECORD, "Noted.", access_policy=own,
                      source_context=ctx)["status"] == "stored"
    assert store_turn("foreign-chat", FOREIGN, "Noted.", access_policy=foreign,
                      source_context={"chat_id": "foreign-chat",
                                      "runtime_home": str(home)})["status"] == "stored"
    yield str(home)
    configure_runtime_home(None)


def _transcript(home, chat, query, *, env_flag="1"):
    import os

    import core.bootstrap_context as bc

    old = os.environ.get("VOOL_CONTEXT_CAPSULE_V2")
    if env_flag is None:
        os.environ.pop("VOOL_CONTEXT_CAPSULE_V2", None)
    else:
        os.environ["VOOL_CONTEXT_CAPSULE_V2"] = env_flag
    try:
        return bc.canonical_runtime_transcript(
            session_id=chat,
            source_context={"chat_id": chat, "runtime_home": home},
            current_user_text=query,
        )
    finally:
        if old is None:
            os.environ.pop("VOOL_CONTEXT_CAPSULE_V2", None)
        else:
            os.environ["VOOL_CONTEXT_CAPSULE_V2"] = old


def _blob(transcript):
    return " ".join(str(m.get("content") or "") for m in transcript)


def test_flag_on_transcript_carries_scoped_fact(profile):
    reset_retrieval_telemetry()
    transcript, source = _transcript(profile, "own-chat", "Where does the pilot boat moor?")
    blob = _blob(transcript)
    assert "<retrieved_context>" in blob, blob
    assert "southern pontoon" in blob, blob


def test_flag_off_transcript_has_no_capsule(profile):
    reset_retrieval_telemetry()
    transcript, source = _transcript(profile, "own-chat", "Where does the pilot boat moor?",
                                     env_flag=None)
    blob = _blob(transcript)
    assert "<retrieved_context>" not in blob, blob
    assert "southern pontoon" not in blob, blob


def test_foreign_scope_fact_never_crosses(profile):
    reset_retrieval_telemetry()
    transcript, _ = _transcript(profile, "own-chat", "What color do the crane pad lights run?")
    blob = _blob(transcript)
    assert "crane pad" not in blob, blob  # foreign-chat record stays out


def test_labelled_secret_masked_in_transcript_block(profile):
    import re

    reset_retrieval_telemetry()
    transcript, _ = _transcript(profile, "own-chat", "What is my seed phrase?")
    blob = _blob(transcript)
    assert "[redacted]" in blob, blob
    for word in SECRET_WORDS:
        assert not re.search(rf"\b{word}\b", blob.lower()), (word, blob)


def test_transcript_assembly_preserves_turn_level_telemetry(profile):
    reset_retrieval_telemetry()
    # The turn's own state (e.g. the exact-recall fast path) must survive any
    # number of transcript assemblies — K-08 post-seal verification reads it.
    update_retrieval_telemetry(capsule_mode="distilled",
                               selected_facts=["- exact code: RCPT-9182"])
    _transcript(profile, "own-chat", "Where does the pilot boat moor?")
    telemetry = get_last_retrieval_telemetry()
    assert telemetry["selected_facts"] == ["- exact code: RCPT-9182"], telemetry


def test_unregistered_session_fails_closed_without_capsule(profile):
    transcript, source = _transcript(profile, "never-registered-chat",
                                     "Where does the pilot boat moor?")
    assert "<retrieved_context>" not in _blob(transcript)


def test_capsule_message_shape_matches_assembler_contract(profile):
    reset_retrieval_telemetry()
    transcript, _ = _transcript(profile, "own-chat", "Where does the pilot boat moor?")
    capsule = [m for m in transcript if m.get("role") == "system"
               and "<retrieved_context>" in str(m.get("content") or "")]
    assert capsule, transcript
    # the assembler separates exactly these; no current-user turn may ride along (asserted below)
    user_turns = [m for m in transcript if m.get("role") == "user"]
    assert user_turns == [], user_turns  # carrier turn removed; assembler adds the real one
