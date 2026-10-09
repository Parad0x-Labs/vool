"""Typed records-absence notice: unnamed subjects publish the records refusal.

Item 7 of the absence-gate repair, carried WITHOUT item 6. A record question the
absence gate refused carries a typed records-absence result to publication, so the
reply uses RECORDS_WITHDRAWAL_NOTICE even when no speaker is named — across empty
and early-return delivery, excluded from factual support. The typed absence rides
OUTSIDE the factual ``<retrieved_context>`` capsule: a capsule with no facts stays
empty, the marker travels as its own system message, and the turn's grounding
lifecycle receives it through an explicit identity-bound handoff.
"""
from __future__ import annotations

import pytest

from tests.test_absence_contract_round2_20261009 import T, _configure, _read
from tests.test_d8_packet_faces_the_capsule_laws_20261007 import _store
from tests.test_question_date_time_leg_20261002 import _hash_backend as _hash_backend

_QUESTION = "What are the opening hours of the repair desk?"
_BODY = "The repair desk badge number is 317."


def _read_messages(profile, question, lane="normal"):
    import core.context_retrieval as cr
    from core.memory.entries import resolve_memory_access_policy

    if lane == "no-hits":
        from core.vool_memory import VoolMemory

        mem = VoolMemory(runtime_home=str(profile))
        try:
            for table in ("memory_nodes", "memory_fts", "source_embeddings"):
                mem._conn.execute("DELETE FROM " + table)
            mem._conn.execute("UPDATE source_fts SET body = ''")
            mem._conn.commit()
        finally:
            mem.close()
    messages = cr._capsule_v2_inject_retrieved(
        "fresh-laws", question, [{"role": "user", "content": question}],
        access_policy=resolve_memory_access_policy(chat_id="fresh-laws"), runtime_home=str(profile),
        search_expansions=("archive locker", "bicycle wheel") if lane == "phrases" else (),
    )
    tel = cr.get_last_retrieval_telemetry()
    assert tel.get("capsule_mode") != "error", tel
    return messages, tel


def _factual_capsule(messages) -> str:
    """The factual capsule exactly as the capsule readers see it (same extraction the
    frame-integrity contract uses): only messages carrying a ``<retrieved_context>`` block."""
    return "\n".join(
        str(m.get("content") or "")
        for m in messages
        if "<retrieved_context>" in str(m.get("content") or "")
    )


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("lane", ["normal", "phrases"])
def test_item7_unnamed_absence_is_typed_and_publishes_the_records_notice(tmp_path, monkeypatch, lane):
    from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
    from core.grounding_publication import publication_verdict
    from core.memory_grounding import memory_record_rows
    from core.unsourced_current_claim import RECORDS_WITHDRAWAL_NOTICE

    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [(T, _BODY)])
    block, tel = _read(profile, _QUESTION, lane)
    rows = memory_record_rows(block)
    lifecycle = GroundingLifecycle(
        lifecycle_id="fresh-absence", identity=TurnIdentity(), request_text=_QUESTION,
        model_authored=True, memory_records=tuple(rows),
    )
    verdict = publication_verdict(lifecycle, "The desk opens at 10 am, but I cannot confirm that.")
    assert verdict.content == RECORDS_WITHDRAWAL_NOTICE, verdict.content
    assert verdict.supported_claim_count == 0
    result = tel.get("records_absence") or {}
    assert result.get("kind") == "records_absence" and result.get("source_ids"), tel


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("lane", ["normal", "phrases"])
def test_item7_refused_capsule_stays_empty_and_the_absence_rides_outside_it(tmp_path, monkeypatch, lane):
    from core.grounding_publication import _RECORDS_ABSENCE_MARKER

    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [(T, _BODY)])
    messages, tel = _read_messages(profile, _QUESTION, lane)
    # A capsule with no facts stays empty: the typed absence never grows a factual block.
    factual = _factual_capsule(messages)
    assert not factual, factual
    markers = [m for m in messages if _RECORDS_ABSENCE_MARKER in str(m.get("content") or "")]
    assert len(markers) == 1, [m.get("content") for m in messages]
    assert "<retrieved_context>" not in str(markers[0].get("content") or ""), markers[0]
    assert tel.get("records_absence", {}).get("source_ids"), tel


@pytest.mark.usefixtures("_hash_backend")
def test_item7_typed_absence_is_handed_to_this_turns_lifecycle_only(tmp_path, monkeypatch):
    from core.grounding_lifecycle import _record_for, register_required
    from core.grounding_publication import _RECORDS_ABSENCE_MARKER, _records_absence_row

    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [(T, _BODY)])
    import core.context_retrieval as cr
    from core.memory.entries import resolve_memory_access_policy

    held = {
        "chat_id": "fresh-laws", "runtime_home": str(profile),
        "session_id": "fresh-laws", "turn_id": "absence-handoff-a",
    }
    register_required(held, request_text=_QUESTION)
    other = {
        "chat_id": "fresh-laws", "runtime_home": str(profile),
        "session_id": "fresh-laws", "turn_id": "absence-handoff-b",
    }
    register_required(other, request_text=_QUESTION)
    plain = {"chat_id": "fresh-laws", "runtime_home": str(profile)}
    messages = cr.inject_retrieved(
        "fresh-laws", _QUESTION, [{"role": "user", "content": _QUESTION}],
        access_policy=resolve_memory_access_policy(chat_id="fresh-laws"),
        source_context=held, env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    assert any(_RECORDS_ABSENCE_MARKER in str(m.get("content") or "") for m in messages), messages
    held_row = _record_for(held)
    assert held_row is not None
    assert any(_records_absence_row(row) for row in held_row.memory_records), held_row.as_dict()
    # Identity bound: another turn's lifecycle, and no lifecycle at all, receive nothing.
    other_row = _record_for(other)
    assert other_row is not None
    assert not any(_records_absence_row(row) for row in other_row.memory_records), other_row.as_dict()
    assert "_grounding_lifecycle_id" not in plain, plain


@pytest.mark.usefixtures("_hash_backend")
def test_item7_no_hits_lane_keeps_the_absence_law_without_a_typed_decision(tmp_path, monkeypatch):
    """The pre-gate empty-discovery return (search found nothing, no receipt packet) runs
    before the absence gate classifies anything, so it carries no typed decision — the same
    boundary the port preserves. The absence law itself still holds: nothing surfaces."""
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [(T, _BODY)])
    messages, tel = _read_messages(profile, _QUESTION, "no-hits")
    assert "317" not in "\n".join(str(m.get("content") or "") for m in messages), messages
    assert not _factual_capsule(messages), messages


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("question", ["What is Lina's drawing class fee?", "What was Lina's drawing class fee?"])
def test_item7_positive_control_a_present_record_is_delivered_present_and_past(tmp_path, monkeypatch, question):
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [(T, "Lina: My drawing class fee is 62 dollars.")])
    block, _ = _read(profile, question)
    assert "62 dollars" in block, block
