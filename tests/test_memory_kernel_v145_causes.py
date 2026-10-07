"""Six memory-kernel causes, one test group each, on the real store_turn and retrieval seams (hash embeddings).

Packets are built from authored histories; no held-out wording is used. Each group pairs the answer the change lets
through with one it must still hold back.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.evidence_kernel.temporal_binder import bind_temporal_claims
from core.memory.entries import resolve_memory_access_policy
from core.memory_receipts import question_terms
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d):
    return float(calendar.timegm(datetime(y, m, d, 9, 0, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_HOP", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def _store(home, chat, text, stated):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    cr.store_turn(chat, text, "Noted.", access_policy=policy,
                  source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated})


def _packet(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat)
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}], access_policy=policy,
                              source_context={"chat_id": chat, "runtime_home": home})
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    return block, list(cr.get_last_retrieval_telemetry().get("evidence_packet_facts") or [])


# ─── 1 + 4: a margin against a goal; the finish time reaches the packet ──────────────────────────

MARGIN_Q = "By how many minutes did I miss my goal time in the 10K?"


def _race_history(home):
    _store(home, "chat-race", "My goal for the harbour 10K was to finish in 60 minutes.", _epoch(2025, 3, 1))
    _store(home, "chat-race", "I ran the harbour 10K yesterday and finished in 67 minutes.", _epoch(2025, 3, 10))
    _store(home, "chat-race", "I prefer running in the morning before work.", _epoch(2025, 3, 12))


def test_the_asked_unit_is_noise_and_a_digit_led_token_is_a_content_term():
    terms = set(question_terms(MARGIN_Q))
    assert "10k" in terms and "minutes" not in terms, terms
    assert "401k" in set(question_terms("How much did I put into my 401k?"))


def test_the_finish_time_reaches_the_packet(home):
    _race_history(home)
    block, facts = _packet(home, "chat-race", MARGIN_Q)
    assert "67 minutes" in block and "60 minutes" in block, block


def test_a_margin_the_two_durations_derive_ships_and_a_wrong_one_does_not(home):
    _race_history(home)
    _block, facts = _packet(home, "chat-race", MARGIN_Q)
    right = bind_temporal_claims(question=MARGIN_Q, reply="7 minutes.", packet_facts=facts, reference_day=date(2025, 4, 1))
    assert right.attempted and right.restored, right.as_dict()
    wrong = bind_temporal_claims(question=MARGIN_Q, reply="9 minutes.", packet_facts=facts, reference_day=date(2025, 4, 1))
    assert not wrong.restored, wrong.as_dict()


# ─── 2 + 3: a rate's denominator is not a duration; "stated <date>" frames a statement day ────────

RATE_Q = "How long do I practise the cello?"


@pytest.fixture()
def cello(home):
    _store(home, "chat-rate", "I practise the cello for about an hour every day after dinner.", _epoch(2025, 2, 5))
    _store(home, "chat-rate", "My cello teacher lives across town.", _epoch(2025, 2, 6))
    _block, facts = _packet(home, "chat-rate", RATE_Q)
    return facts


@pytest.mark.parametrize("reply,ships", [
    ("About an hour a day.", True),
    ("About an hour a day (stated 2025-02-05).", True),
    ("About three hours a day.", False),
    ("About an hour a day, since 5 February 2025.", False),
], ids=["rate", "statement-frame", "wrong-duration", "event-claim-without-event-day"])
def test_a_rate_and_a_statement_frame_ship_and_unsupported_values_do_not(cello, reply, ships):
    binding = bind_temporal_claims(question=RATE_Q, reply=reply, packet_facts=cello, reference_day=date(2025, 4, 1))
    assert binding.attempted and binding.restored is ships, binding.as_dict()


# ─── 5 + 6: routine preferences reach a routine ask, tagged as binding ────────────────────────────

def test_a_routine_ask_carries_the_stated_preference_tagged_as_binding(home):
    _store(home, "chat-pref", "I like to swim before 7am, the pool is empty then.", _epoch(2025, 3, 2))
    _store(home, "chat-pref", "My knee has been fine since the physio sessions.", _epoch(2025, 3, 9))
    block, _facts = _packet(home, "chat-pref", "Can you put together a weekly workout routine for me?")
    assert "swim before 7am" in block, block
    assert "<stated preference: respect it in the answer>" in block, block


def test_a_fact_ask_does_not_get_the_preference_operands(home):
    _store(home, "chat-pref", "I like to swim before 7am, the pool is empty then.", _epoch(2025, 3, 2))
    _store(home, "chat-pref", "My knee has been fine since the physio sessions.", _epoch(2025, 3, 9))
    block, _facts = _packet(home, "chat-pref", "How long have my knee sessions been going on?")
    assert "<stated preference: respect it in the answer>" not in block, block
