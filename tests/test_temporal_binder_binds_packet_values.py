"""The temporal claim binder ships a withdrawn interval only when the turn's receipt packet derives it.

The past-time guard withdraws a specific day count or date the request material does not literally carry. With the
memory kernel on (VOOL_EVIDENCE_KERNEL=1), `core.evidence_kernel.temporal_binder.bind_temporal_claims` reads the
packet's typed event days: a value the packet derives ships, a value it does not stays withdrawn. Packets here are
built by the real store_turn and retrieval seams (hash embeddings); no held-out wording is used.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.evidence_kernel.temporal_binder import bind_temporal_claims
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

QUESTION = "How many days passed between the pottery workshop and the welding class?"


def _epoch(y, m, d):
    return float(calendar.timegm(datetime(y, m, d, 9, 0, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def packet(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("VOOL_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_HOP", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    chat = "chat-binder"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    for text, stated in (
        ("I went to the pottery workshop on 3 March, it was messy but fun.", _epoch(2025, 3, 5)),
        ("I started the welding class on 17 March at the community college.", _epoch(2025, 3, 18)),
        ("I prefer evening classes because of work.", _epoch(2025, 3, 20)),
    ):
        cr.store_turn(chat, text, "Noted.", access_policy=policy,
                      source_context={"chat_id": chat, "runtime_home": str(profile), "statement_at": stated})
    cr.reset_retrieval_telemetry()
    cr.inject_retrieved(chat, QUESTION, [{"role": "user", "content": QUESTION}], access_policy=policy,
                        source_context={"chat_id": chat, "runtime_home": str(profile)})
    facts = list(cr.get_last_retrieval_telemetry().get("evidence_packet_facts") or [])
    yield facts
    configure_runtime_home(None)


def test_a_day_count_the_packet_derives_ships(packet):
    assert packet, "the compiler delivered no packet facts"
    binding = bind_temporal_claims(question=QUESTION, reply="14 days (3 March to 17 March).", packet_facts=packet,
                                   reference_day=date(2025, 4, 1))
    assert binding.attempted and binding.restored, binding.as_dict()
    assert "14 days" in binding.text


@pytest.mark.parametrize("reply", [
    "21 days (3 March to 24 March).",
    "12 days.",
], ids=["fabricated-date", "wrong-count"])
def test_a_value_the_packet_does_not_derive_stays_withdrawn(packet, reply):
    binding = bind_temporal_claims(question=QUESTION, reply=reply, packet_facts=packet, reference_day=date(2025, 4, 1))
    assert not binding.restored, binding.as_dict()


def test_no_packet_binds_nothing():
    binding = bind_temporal_claims(question=QUESTION, reply="14 days (3 March to 17 March).", packet_facts=[],
                                   reference_day=date(2025, 4, 1))
    assert not binding.restored, binding.as_dict()
