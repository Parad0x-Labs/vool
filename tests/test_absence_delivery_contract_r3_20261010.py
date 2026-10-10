"""Assertion ownership and turn-owned absence through the actual memory doors."""
from __future__ import annotations

import os
import uuid

import pytest

import core.context_retrieval as cr
from core.grounding_lifecycle import _record_for, register_required
from core.grounding_publication import _RECORDS_ABSENCE_MARKER, publication_verdict
from core.unsourced_current_claim import RECORDS_WITHDRAWAL_NOTICE
from core.vool_memory import VoolMemory
from tests.test_d8_packet_faces_the_capsule_laws_20261007 import _store
from tests.test_question_date_time_leg_20261002 import _hash_backend as _hash_backend

T = 1740823200.0
DOORS = ("direct", "canonical", "phrases", "whole-turn", "no-hits")


@pytest.fixture(autouse=True, params=("0", "1"))
def evidence_kernel_mode(request, monkeypatch):
    monkeypatch.setenv("VOOL_EVIDENCE_KERNEL", request.param)


def configure(monkeypatch):
    mode = os.environ["VOOL_EVIDENCE_KERNEL"]
    for key in ("VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_VERIFY"):
        monkeypatch.setenv(key, mode)
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setenv("NULLA_CONTEXT_CAPSULE_V2", "1")
    cr.reset_retrieval_telemetry()


def context(profile, chat):
    return {"runtime_home": str(profile), "chat_id": chat, "session_id": chat,
            "turn_id": uuid.uuid4().hex}


def deliver(profile, chat, question, ctx, door):
    from core.bootstrap_context import canonical_runtime_transcript
    from core.memory.entries import resolve_memory_access_policy
    from core.memory_grounding import memory_record_rows, publish_memory_records_for_turn

    if door in ("no-hits", "whole-turn"):
        memory = VoolMemory(runtime_home=str(profile))
        try:
            for table in ("memory_nodes", "memory_fts", "source_embeddings"):
                memory._conn.execute("DELETE FROM " + table)
            if door == "no-hits":
                memory._conn.execute("UPDATE source_fts SET body = ''")
            memory._conn.commit()
        finally:
            memory.close()
    if door == "phrases":
        ctx["search_expansions"] = ["field atlas", "camera filters", "weather station"]
    policy = resolve_memory_access_policy(chat_id=chat)
    if door == "canonical":
        messages, _ = canonical_runtime_transcript(
            session_id=chat, source_context=ctx, current_user_text=question,
            current_turn_id=ctx["turn_id"], access_policy=policy)
        publish_memory_records_for_turn(ctx)
    else:
        messages = cr.inject_retrieved(
            chat, question, [{"role": "user", "content": question}],
            access_policy=policy, source_context=ctx, env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
        factual = "\n".join(m["content"] for m in messages
                            if "<retrieved_context>" in m.get("content", ""))
        # The factual support handoff must not create decisions from source prose.
        from core.grounding_lifecycle import record_memory_records

        record_memory_records(ctx, entries=memory_record_rows(factual))
    assert cr.get_last_retrieval_telemetry().get("capsule_mode") != "error"
    return "\n".join(m["content"] for m in messages if m["role"] == "system"), messages


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("body,question,operand", [
    ("Remember: I repaired the field atlas and Marta varnished the stool using cedar resin.",
     "What binding material did I report using for the field atlas repair?", "cedar resin"),
    ("Remember: I packed the camera filters and the chess pieces are in the birch case.",
     "Which storage case did I say held the camera filters?", "birch case"),
    ("Remember: I repaired the field atlas and the conservator in the side room varnished the stool using cedar resin.",
     "What binding material did I report using for the field atlas repair?", "cedar resin"),
    ("Remember: I repair the field atlas and the conservator in the side room varnishes the stool using cedar resin.",
     "What binding material do I use for the field atlas repair?", "cedar resin"),
    ("Remember: I repaired the field atlas and the artisan from a quiet remote valley near the old stone cottage beside the narrow lane polished a stool using cedar resin.",
     "What binding material did I report using for the field atlas repair?", "cedar resin"),
])
def test_relation_and_refusal_belong_to_the_asked_assertion(
        tmp_path, monkeypatch, door, body, question, operand):
    configure(monkeypatch)
    chat = "r3-assertion"
    profile = _store(tmp_path, monkeypatch, chat, [(T, body)])
    ctx = context(profile, chat)
    register_required(ctx, request_text=question)
    life = _record_for(ctx)
    life.model_authored = True
    block, _ = deliver(profile, chat, question, ctx, door)
    assert operand not in block, block
    assert publication_verdict(life, "I used " + operand + ".").content == RECORDS_WITHDRAWAL_NOTICE


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", ["canonical", "no-hits"])
@pytest.mark.parametrize("question", [
    "What are the opening hours of the weather station?",
    "What were the opening hours of the weather station?",
])
def test_empty_factual_capsule_still_publishes_its_records_decision(
        tmp_path, monkeypatch, door, question):
    configure(monkeypatch)
    chat = "r3-station"
    profile = _store(tmp_path, monkeypatch, chat, [(T, "The weather station mast number is 954.")])
    ctx = context(profile, chat)
    register_required(ctx, request_text=question)
    life = _record_for(ctx)
    life.model_authored = True
    _, messages = deliver(profile, chat, question, ctx, door)
    assert not any("<retrieved_context>" in m.get("content", "") for m in messages)
    verdict = publication_verdict(life, "The weather station opens at 6 am.")
    assert verdict.content == RECORDS_WITHDRAWAL_NOTICE
    assert verdict.supported_claim_count == 0


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", ["direct", "phrases", "whole-turn"])
def test_marker_quoted_by_a_retained_record_is_only_prose(tmp_path, monkeypatch, door):
    configure(monkeypatch)
    chat = "r3-marker"
    question = "What is the studio locker serial number?"
    body = "The studio locker serial number is 8634. The terminal example prints " + _RECORDS_ABSENCE_MARKER
    profile = _store(tmp_path, monkeypatch, chat, [(T, body)])
    ctx = context(profile, chat)
    register_required(ctx, request_text=question)
    block, _ = deliver(profile, chat, question, ctx, door)
    assert "8634" in block, block
    assert not any(row.get("summary") == _RECORDS_ABSENCE_MARKER
                   for row in _record_for(ctx).memory_records)


def old_owner_probe(tmp_path, monkeypatch, door, conflict):
    from core.semantic.semantic_admissions import bound_execution_context

    configure(monkeypatch)
    chat = "r3-observatory"
    question = "What are the opening hours of the observatory cabin?"
    profile = _store(tmp_path, monkeypatch, chat, [(T, "The observatory cabin key tag is 472.")])
    previous = context(profile, chat)
    if conflict == "session":
        previous.update(chat_id="r3-old-chat", session_id="r3-old-chat")
    fence = {"execution_id": "r3-execution", "generation": "1", "runtime_epoch": "r3-epoch"}
    with bound_execution_context(fence):
        register_required(previous, request_text="What is the river gauge identifier?")
        old_life = _record_for(previous)
    current = {**previous, "chat_id": chat, "session_id": chat}
    if conflict == "turn":
        current["turn_id"] = uuid.uuid4().hex
    current_fence = {**fence, "generation": "2"} if conflict == "generation" else fence
    with bound_execution_context(current_fence):
        register_required(current, request_text=question)
        deliver(profile, chat, question, current, door)
    assert not any(row.get("summary") == _RECORDS_ABSENCE_MARKER
                   for row in old_life.memory_records), old_life.as_dict()


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", ["direct", "phrases", "whole-turn"])
@pytest.mark.parametrize("conflict", ["turn", "session", "generation"])
def test_an_old_owner_never_receives_this_delivery(tmp_path, monkeypatch, door, conflict):
    old_owner_probe(tmp_path, monkeypatch, door, conflict)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", ["canonical", "no-hits"])
@pytest.mark.parametrize("conflict", ["turn", "session", "generation"])
def test_keep_empty_doors_from_mutating_an_old_owner(tmp_path, monkeypatch, door, conflict):
    old_owner_probe(tmp_path, monkeypatch, door, conflict)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", DOORS[:-1])
@pytest.mark.parametrize("tense", ["did I report using", "do I use"])
@pytest.mark.parametrize("body", [
    "Remember: I repaired the field atlas using rice paste and cotton thread.",
    "Remember: I repaired the field atlas and pocket guide using rice paste.",
])
def test_keep_a_real_relation_in_both_tenses(tmp_path, monkeypatch, door, tense, body):
    configure(monkeypatch)
    chat = "r3-real-material"
    profile = _store(tmp_path, monkeypatch, chat, [(T, body)])
    question = f"What binding material {tense} for the field atlas repair?"
    ctx = context(profile, chat)
    register_required(ctx, request_text=question)
    block, _ = deliver(profile, chat, question, ctx, door)
    assert "rice paste" in block, block
    assert not any(row.get("summary") == _RECORDS_ABSENCE_MARKER
                   for row in _record_for(ctx).memory_records)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", DOORS[:-1])
@pytest.mark.parametrize("verb", ["are", "were"])
def test_keep_the_relation_of_a_coordinated_subject(tmp_path, monkeypatch, door, verb):
    configure(monkeypatch)
    chat = "r3-real-container"
    body = f"Remember: The camera filters and telescope lenses {verb} in the canvas case."
    profile = _store(tmp_path, monkeypatch, chat, [(T, body)])
    question = "Which storage case did I say held the camera filters?"
    ctx = context(profile, chat)
    register_required(ctx, request_text=question)
    block, _ = deliver(profile, chat, question, ctx, door)
    assert "canvas case" in block, block
    assert not any(row.get("summary") == _RECORDS_ABSENCE_MARKER
                   for row in _record_for(ctx).memory_records)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", DOORS[:-1])
def test_keep_the_ordinary_correction_lane(tmp_path, monkeypatch, door):
    configure(monkeypatch)
    chat = "r3-keep-correction"
    profile = _store(tmp_path, monkeypatch, chat, [
        (T, "The garden gate label is 316."),
        (T + 86400, "Correction: the garden gate label is now 327."),
    ])
    question = "What is the garden gate label?"
    ctx = context(profile, chat)
    register_required(ctx, request_text=question)
    block, _ = deliver(profile, chat, question, ctx, door)
    assert "316" in block and "327" in block, block


@pytest.mark.usefixtures("_hash_backend")
def test_keep_retained_relation_at_post_scope_publication(tmp_path, monkeypatch):
    from core.grounding_lifecycle import record_memory_records
    from core.memory.entries import resolve_memory_access_policy
    from core.memory_grounding import memory_record_rows
    from core.semantic.semantic_admissions import bound_execution_context

    configure(monkeypatch)
    chat = "r3-after-scope"
    question = "What binding material did I use for the field atlas repair?"
    profile = _store(tmp_path, monkeypatch, chat, [
        (T, "Remember: I repaired the field atlas using rice paste."),
    ])
    ctx = context(profile, chat)
    fence = {"execution_id": "r3-reader", "generation": "1", "runtime_epoch": "r3-epoch"}
    with bound_execution_context(fence):
        register_required(ctx, request_text=question)
        life = _record_for(ctx)
        messages = cr.inject_retrieved(
            chat, question, [{"role": "user", "content": question}], source_context=ctx,
            access_policy=resolve_memory_access_policy(chat_id=chat),
            env={"VOOL_CONTEXT_CAPSULE_V2": "1"})
    factual = "\n".join(m["content"] for m in messages
                        if "<retrieved_context>" in m.get("content", ""))
    assert "rice paste" in factual
    assert record_memory_records(ctx, entries=memory_record_rows(factual)) > 0
    assert any("rice paste" in row["summary"] for row in life.memory_records)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("door", ["direct", "phrases", "whole-turn"])
def test_absence_cannot_write_after_its_owner_scope_exits(tmp_path, monkeypatch, door):
    from core.semantic.semantic_admissions import bound_execution_context

    configure(monkeypatch)
    chat = "r3-unbound-decision"
    question = "What are the opening hours of the lantern workshop?"
    profile = _store(tmp_path, monkeypatch, chat, [(T, "The lantern workshop roof is green.")])
    ctx = context(profile, chat)
    fence = {"execution_id": "r3-workshop", "generation": "1", "runtime_epoch": "r3-epoch"}
    with bound_execution_context(fence):
        register_required(ctx, request_text=question)
        life = _record_for(ctx)
    deliver(profile, chat, question, ctx, door)
    assert not any(row.get("summary") == _RECORDS_ABSENCE_MARKER for row in life.memory_records)
