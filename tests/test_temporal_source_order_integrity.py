"""Persisted source order, not retrieval rank, resolves temporal ties."""

from __future__ import annotations

import hashlib
import itertools
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.temporal_selection import AsOfIntent, TemporalCandidate, apply_temporal_selection


@pytest.mark.parametrize("as_of", [None, datetime(2025, 1, 1, tzinfo=timezone.utc)])
def test_mapping_source_sequence_is_preserved_under_retrieval_permutation(as_of):
    records = [
        dict(key="old", body="The lantern cabinet code is 3154.", statement_at=100.0,
             recorded_at=200.0, seq=0),
        dict(key="new", body="The lantern cabinet code is 8826.", statement_at=100.0,
             recorded_at=200.0, seq=7),
    ]
    for order in itertools.permutations(records):
        verdicts = apply_temporal_selection(order, intent=AsOfIntent(as_of_end=as_of))
        assert verdicts["new"].eligible
        assert not verdicts["old"].eligible
        assert verdicts["old"].superseded_by == "new"


@pytest.mark.parametrize("mapping", [False, True])
def test_zero_sequence_chain_origin_does_not_turn_into_query_evidence(mapping):
    record = dict(key="chain", body="The unrelated cider crate contains 18 bottles.",
                  statement_at=100.0, recorded_at=100.0, seq=0, origin="chain")
    candidate = record if mapping else TemporalCandidate(**record)
    verdict = apply_temporal_selection([candidate], intent=AsOfIntent())["chain"]
    assert not verdict.eligible
    assert verdict.reason == "chain-unlinked"


@pytest.mark.parametrize("mapping", [False, True])
def test_linked_chain_correction_stays_eligible(mapping):
    records = [
        dict(key="query", body="The lantern cabinet code is 3154.", statement_at=100.0,
             recorded_at=100.0, seq=0, origin="query-leg"),
        dict(key="chain", body="Correction: the lantern cabinet code is 8826.",
             statement_at=101.0, recorded_at=101.0, seq=1, origin="chain"),
    ]
    candidates = records if mapping else [TemporalCandidate(**record) for record in records]
    verdicts = apply_temporal_selection(candidates, intent=AsOfIntent())
    assert verdicts["chain"].eligible
    assert not verdicts["query"].eligible


@pytest.mark.parametrize("as_of", [None, datetime(2025, 1, 1, tzinfo=timezone.utc)])
def test_missing_source_order_keeps_tied_conflict_honestly(as_of):
    records = [
        TemporalCandidate(key="a", body="The lantern cabinet code is 3154.",
                          statement_at=100.0, recorded_at=200.0),
        TemporalCandidate(key="b", body="The lantern cabinet code is 8826.",
                          statement_at=100.0, recorded_at=200.0),
    ]
    for order in itertools.permutations(records):
        verdicts = apply_temporal_selection(order, intent=AsOfIntent(as_of_end=as_of))
        assert all(verdict.eligible for verdict in verdicts.values())
        assert all(verdict.reason == "chronology-undetermined" for verdict in verdicts.values())


@pytest.mark.parametrize("question", ["What was the first lantern cabinet code?", "What was the latest lantern cabinet code?"])
def test_ordinal_cannot_invent_order_for_tied_sources(question):
    records = [
        TemporalCandidate(key="a", body="The lantern cabinet code is 3154.",
                          statement_at=100.0, recorded_at=200.0),
        TemporalCandidate(key="b", body="The lantern cabinet code is 8826.",
                          statement_at=100.0, recorded_at=200.0),
    ]
    for order in itertools.permutations(records):
        verdicts = apply_temporal_selection(order, intent=AsOfIntent(), question=question)
        assert all(verdict.eligible for verdict in verdicts.values())
        assert all(verdict.reason == "chronology-undetermined" for verdict in verdicts.values())


def test_event_dates_outrank_capture_sequence_for_as_of():
    records = [
        TemporalCandidate(key="future", body="The cabinet code is 8826.",
                          event_at=1_735_689_600.0, recorded_at=200.0, seq=1),
        TemporalCandidate(key="past", body="The cabinet code is 3154.",
                          event_at=1_704_067_200.0, recorded_at=200.0, seq=2),
    ]
    verdicts = apply_temporal_selection(
        records, intent=AsOfIntent(as_of_end=datetime(2024, 6, 1, tzinfo=timezone.utc)),
    )
    assert verdicts["past"].eligible
    assert not verdicts["future"].eligible
    assert verdicts["future"].reason == "future-relative-to-as-of"


def test_tied_capture_permutations_preserve_a_restored_value():
    records = [
        TemporalCandidate(key="old", body="The pottery shop opens at 07:15.",
                          statement_at=100.0, recorded_at=100.0, seq=1),
        TemporalCandidate(key="correction", body="Correction: the pottery shop opens at 09:30.",
                          statement_at=100.0, recorded_at=100.0, seq=2),
        TemporalCandidate(key="restore", body="Ignore that correction, 07:15 was right all along.",
                          statement_at=100.0, recorded_at=100.0, seq=3),
    ]
    for order in itertools.permutations(records):
        verdicts = apply_temporal_selection(order, intent=AsOfIntent())
        assert verdicts["restore"].eligible
        assert verdicts["old"].eligible
        assert not verdicts["correction"].eligible


@pytest.fixture
def source_store(tmp_path, monkeypatch):
    from core.vool_memory import VoolMemory

    home = Path(os.environ["VOOL_HOME"]) / tmp_path.name
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(home / "workspace"))
    memory = VoolMemory(runtime_home=str(home))
    yield memory, home
    memory.close()


def _store(memory, body, *, role="user", recorded_at=100.0):
    return memory.occurrence_store(
        chat_scope="source-order", role=role, body=body,
        authority="assistant-output" if role == "assistant" else "observed-user-statement",
        recorded_at=recorded_at, statement_at=100.0,
    )


def test_source_sequence_survives_all_serving_read_paths_and_reopen(source_store):
    from core.vool_memory import VoolMemory

    memory, home = source_store
    old = _store(memory, "The lantern cabinet code is 3154.")
    new = _store(memory, "The lantern cabinet code is 8826.")
    first_seq = getattr(old, "source_sequence", None)
    second_seq = getattr(new, "source_sequence", None)
    assert first_seq is not None and second_seq > first_seq
    finalized = dict(chat_scope="source-order", role="user", body="The compass color is amber.",
                     authority="observed-user-statement", recorded_at=100.0, request_id="finalized-turn")
    stored = memory.occurrence_store(**finalized)
    retry = memory.occurrence_store(**finalized)
    assert retry.occurrence_id == stored.occurrence_id
    assert retry.source_sequence == stored.source_sequence is not None
    for row in (old, new):
        memory.occurrence_embedding_upsert(
            row.occurrence_id, vector=[1.0, 0.0], backend="fixture",
            body_sha256=hashlib.sha256(row.body.encode()).hexdigest(),
        )
    lexical = dict((row.occurrence_id, row.source_sequence) for row, _ in memory.occurrence_search(
        "lantern cabinet", chat_scope="source-order", limit=8,
    ))
    semantic = dict((row.occurrence_id, row.source_sequence) for row, _ in memory.occurrence_search_semantic(
        [1.0, 0.0], chat_scope="source-order", backend="fixture", limit=8,
    ))
    expected = {old.occurrence_id: first_seq, new.occurrence_id: second_seq}
    assert lexical == semantic == expected
    assert memory.occurrence_neighbors(old, before=0, after=1)[0].source_sequence == second_seq
    with VoolMemory(runtime_home=str(home)) as reopened:
        assert reopened.occurrence_get(old.occurrence_id).source_sequence == first_seq
        assert reopened.occurrence_get(new.occurrence_id).source_sequence == second_seq


def test_tied_neighbor_after_answer_reaches_capsule(source_store, monkeypatch):
    from core import context_retrieval, embedding_service
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    memory, home = source_store
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    ensure_chat_namespace("source-order", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="source-order")
    _store(memory, "What is the lantern cabinet access code?")
    answer = _store(memory, "It is 8826.", role="assistant")
    query = "What access code did you give me for the lantern cabinet?"
    messages = context_retrieval.inject_retrieved(
        "source-order", query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": "source-order", "runtime_home": str(home)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    capsule = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "8826" in capsule, capsule
    refs = context_retrieval.get_last_retrieval_telemetry().get("evidence_refs", [])
    assert any(ref.get("occurrence_id") == answer.occurrence_id and ref.get("delivered") for ref in refs)
    assert "What is the lantern cabinet access code?" in capsule
    assert any(ref.get("context_only") and ref.get("delivered") for ref in refs)


def test_source_order_reaches_temporal_caller_for_lexical_records(source_store, monkeypatch):
    from core import context_retrieval, embedding_service, temporal_selection
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    memory, home = source_store
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    ensure_chat_namespace("source-order", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="source-order")
    old = _store(memory, "The lantern cabinet code is 3154.")
    new = _store(memory, "The lantern cabinet code is 8826.")
    observed = []
    owner = temporal_selection.apply_temporal_selection

    def capture(candidates, **kwargs):
        observed.extend(candidates)
        return owner(candidates, **kwargs)

    monkeypatch.setattr(temporal_selection, "apply_temporal_selection", capture)
    query = "What is the lantern cabinet code?"
    context_retrieval.inject_retrieved(
        "source-order", query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": "source-order", "runtime_home": str(home)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    by_key = {candidate.key: candidate.seq for candidate in observed}
    assert by_key[old.occurrence_id] == getattr(old, "source_sequence", None) is not None
    assert by_key[new.occurrence_id] == getattr(new, "source_sequence", None) is not None


def test_past_user_value_does_not_take_an_adjacent_current_value(source_store, monkeypatch):
    from core import context_retrieval, embedding_service
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    memory, home = source_store
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    ensure_chat_namespace("source-order", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="source-order")
    _store(memory, "The lantern cabinet code was 3154 before the revision.")
    _store(memory, "It is now 8826.", role="assistant")
    query = "What was the lantern cabinet code before the revision?"
    messages = context_retrieval.inject_retrieved(
        "source-order", query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": "source-order", "runtime_home": str(home)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    capsule = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "3154" in capsule, capsule
    assert "8826" not in capsule, capsule


@pytest.mark.parametrize("answer", ["Warehouse 8826.", "Noted, 8826."])
def test_short_neighbor_fallback_does_not_promote_wrong_subject_or_ack(source_store, monkeypatch, answer):
    from core import context_retrieval, embedding_service
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    memory, home = source_store
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    ensure_chat_namespace("source-order", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="source-order")
    _store(memory, "What is the lantern cabinet access code?")
    _store(memory, answer, role="assistant")
    query = "What access code did you give me for the lantern cabinet?"
    messages = context_retrieval.inject_retrieved(
        "source-order", query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": "source-order", "runtime_home": str(home)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    capsule = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "8826" not in capsule, capsule


def test_short_answer_does_not_bind_another_questions_subject(source_store, monkeypatch):
    from core import context_retrieval, embedding_service
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    memory, home = source_store
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    ensure_chat_namespace("source-order", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="source-order")
    _store(memory, "What is the warehouse access code?")
    _store(memory, "It is 8826.", role="assistant")
    query = "What access code did you give me for the lantern cabinet?"
    messages = context_retrieval.inject_retrieved(
        "source-order", query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": "source-order", "runtime_home": str(home)},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    capsule = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "8826" not in capsule, capsule


@pytest.mark.parametrize("free_tokens", [40, 80])
def test_short_question_answer_pair_stays_atomic_under_budget(source_store, monkeypatch, free_tokens):
    from dataclasses import replace

    from core import context_retrieval, embedding_service
    from core.context_capsule_v2 import resolve_budget
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    memory, home = source_store
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    ensure_chat_namespace("source-order", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="source-order")
    source_question = "What is the lantern cabinet access code?"
    _store(memory, source_question)
    _store(memory, "It is 8826.", role="assistant")
    query = "What access code did you give me for the lantern cabinet?"
    messages = context_retrieval.inject_retrieved(
        "source-order", query, [{"role": "user", "content": query}],
        access_policy=policy,
        source_context={"chat_id": "source-order", "runtime_home": str(home)},
        budget=replace(resolve_budget(bucket="B", role="general"), free_tokens=free_tokens),
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    capsule = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert (source_question in capsule) == ("8826" in capsule), capsule
    refs = context_retrieval.get_last_retrieval_telemetry().get("evidence_refs", [])
    bundled = [ref for ref in refs if ref.get("evidence_bundle_id")]
    assert len(bundled) == 2
    assert {ref["packing_unit"] for ref in bundled} == {"\n".join(ref["line"] for ref in bundled)}
    assert all(ref["line"] != ref["packing_unit"] for ref in bundled)
    assert all(bool(ref.get("delivered")) == ("8826" in capsule) for ref in bundled)
    if free_tokens == 80:
        assert "8826" in capsule
