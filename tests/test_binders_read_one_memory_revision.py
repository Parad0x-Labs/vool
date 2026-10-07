"""The binders read packet facts only from the retrieval whose evidence the turn admitted.

Packet facts reach the claim binder and the withdrawn-answer verifier through the process's last-retrieval
telemetry, which any later retrieval overwrites: another question's recall, a second assembly pass, another chat's
turn. Before this, a value one chat's packet stated could bind a claim in a different chat's turn. Each binder now
takes the packet's facts only when that packet was compiled for this chat and is the packet inside the evidence the
turn admitted.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import pytest

import core.bootstrap_context as bootstrap_context
import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home
from core.unsourced_current_claim import inspect_unsourced_current_claim


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
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def _store(home, chat, text, stated):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    cr.store_turn(chat, text, "Noted.", access_policy=resolve_memory_access_policy(chat_id=chat),
                  source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated})


def _ask(home, chat, question):
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}],
                              access_policy=resolve_memory_access_policy(chat_id=chat),
                              source_context={"chat_id": chat, "runtime_home": home})
    return "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))


QUESTION = "What is the total I spent on my bike gear?"
ANSWER = "$188 total: helmet $95 and lights $93."


def _bike_history(home, chat):
    _store(home, chat, "I bought a new bike helmet for $95 last weekend.", _epoch(2025, 3, 2))
    _store(home, chat, "I got front and rear bike lights for $93 too.", _epoch(2025, 3, 9))


def _verdict(monkeypatch, chat, admitted):
    monkeypatch.setattr(bootstrap_context, "admitted_capsule_evidence_text", lambda *_a, **_k: admitted)
    return inspect_unsourced_current_claim(answer=ANSWER, requires_current=True, user_turn_text=QUESTION,
                                           source_context={"chat_id": chat}, session_id=chat)


def test_the_turns_own_packet_binds_its_derived_total(home, monkeypatch):
    _bike_history(home, "chat-a")
    block = _ask(home, "chat-a", QUESTION)
    verdict = _verdict(monkeypatch, "chat-a", block)
    assert verdict.claim_binding["all_supported"] is True, verdict.as_dict()


def test_another_chats_later_packet_never_binds_this_turns_claim(home, monkeypatch):
    _store(home, "chat-a", "I like riding along the river on Sundays.", _epoch(2025, 3, 1))
    block_a = _ask(home, "chat-a", QUESTION)          # chat A has no bike purchases
    _bike_history(home, "chat-b")
    _ask(home, "chat-b", QUESTION)                     # a later retrieval for chat B overwrites the telemetry
    verdict = _verdict(monkeypatch, "chat-a", block_a)
    assert not verdict.claim_binding.get("all_supported"), verdict.as_dict()


def test_a_later_packet_of_the_same_chat_for_another_question_is_not_this_turns(home, monkeypatch):
    _store(home, "chat-a", "I like riding along the river on Sundays.", _epoch(2025, 3, 1))
    block_a = _ask(home, "chat-a", QUESTION)          # compiled before the purchases were stored
    _bike_history(home, "chat-a")
    _ask(home, "chat-a", "How much were my bike lights?")  # a newer revision, another question
    verdict = _verdict(monkeypatch, "chat-a", block_a)
    assert not verdict.claim_binding.get("all_supported"), verdict.as_dict()
