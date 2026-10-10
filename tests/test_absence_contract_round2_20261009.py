"""Fresh retained-store stories for the shared absence delivery contract."""
from __future__ import annotations

import json
import os

import pytest

import core.context_retrieval as cr
from core.vool_memory import VoolMemory
from tests.test_d8_packet_faces_the_capsule_laws_20261007 import _store
from tests.test_question_date_time_leg_20261002 import _hash_backend as _hash_backend

T = 1740823200.0


@pytest.fixture(autouse=True, params=("0", "1"))
def evidence_kernel_mode(request, monkeypatch):
    monkeypatch.setenv("VOOL_EVIDENCE_KERNEL", request.param)


def _configure(monkeypatch):
    mode = os.environ.get("VOOL_EVIDENCE_KERNEL", "0")
    for key in ("VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_VERIFY"):
        monkeypatch.setenv(key, mode)
    monkeypatch.setenv("VOOL_CAPSULE_DEBUG_DROPS", "1")
    cr._CAPSULE_DEBUG_DROPS.clear()
    cr.reset_retrieval_telemetry()


def _read(profile, question, lane="normal"):
    from core.memory.entries import resolve_memory_access_policy

    if lane == "no-hits":
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
    return "\n".join(m["content"] for m in messages if m["role"] == "system"), tel


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("lane", ["normal", "phrases", "no-hits"])
@pytest.mark.parametrize("body,question,forbidden", [
    ("Remember: I repaired the library ledger. I polished the desk using lemon oil.",
     "What binding material did I report using for the library ledger repair?", "lemon oil"),
    ("Remember: I packed the telescope lenses. The dice are in the walnut case.",
     "Which storage case did I say held the telescope lenses?", "walnut case"),
])
def test_item1_relation_does_not_borrow_another_assertion(tmp_path, monkeypatch, lane, body, question, forbidden):
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [(T, body)])
    block, _ = _read(profile, question, lane)
    assert forbidden not in block, block


@pytest.mark.usefixtures("_hash_backend")
def test_item1_joined_clauses_are_distinct_assertions(tmp_path, monkeypatch):
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [
        (T, "Remember: I restored the museum journal, and I cleaned a shelf using pine wax."),
    ])
    block, _ = _read(profile, "What binding material did I report using for the museum journal restoration?")
    assert "pine wax" not in block, block


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("lane", ["normal", "phrases", "no-hits"])
@pytest.mark.parametrize("body,question,forbidden", [
    ("Remember: I prefer a bicycle with a basket.",
     "What advice did I give about truing my bicycle wheel?", "basket"),
    ("Remember: I replaced the archive locker hinge on 14 June 2025.",
     "When were the opening hours of the archive locker announced?", "hinge"),
])
def test_item2_packet_kinds_cannot_override_absence(tmp_path, monkeypatch, lane, body, question, forbidden):
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [(T, body)])
    block, _ = _read(profile, question, lane)
    assert forbidden not in block, block


@pytest.mark.usefixtures("_hash_backend")
def test_item3_partial_hits_do_not_license_a_future_receipt(tmp_path, monkeypatch):
    _configure(monkeypatch)
    turns = [(T, "My mosaic tiles are beside the clinic door.")]
    turns += [(T + 3600 * (i + 1), f"The pantry jar labelled batch {i} is on the oak shelf.") for i in range(20)]
    turns += [(T + 6 * 86400, "The mosaic clinic is on 14 June 2025 at 2 pm.")]
    profile = _store(tmp_path, monkeypatch, "fresh-laws", turns)
    question = "When was the mosaic clinic as of 2025-03-01?"
    mem = VoolMemory(runtime_home=str(profile))
    try:
        for table in ("memory_nodes", "memory_fts", "source_embeddings"):
            mem._conn.execute("DELETE FROM " + table)
        mem._conn.execute("UPDATE source_fts SET body = '' WHERE body LIKE '%2 pm%'")
        mem._conn.commit()
        assert mem.occurrence_search(question, chat_scope="fresh-laws")
        if os.environ["VOOL_EVIDENCE_KERNEL"] == "1":
            from core.evidence_compiler import compile_packet

            assert any("2 pm" in f["sentence"] for f in compile_packet(mem, "fresh-laws", question).facts)
    finally:
        mem.close()
    block, tel = _read(profile, question)
    assert "2 pm" not in block, block
    assert "2 pm" not in json.dumps(tel), tel


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("lane", ["normal", "phrases", "no-hits"])
def test_item4_live_undo_refuses_the_old_hours(tmp_path, monkeypatch, lane):
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [
        (T, "The archive locker number is 831."),
        (T + 86400, "The archive locker opening hours are 3 pm to 5 pm."),
        (T + 2 * 86400, "Actually, scratch that — the archive locker opening hours are withdrawn."),
    ])
    block, _ = _read(profile, "What were the opening hours of the archive locker?", lane)
    assert "3 pm" not in block, block


@pytest.mark.usefixtures("_hash_backend")
def test_item5_diagnostics_do_not_carry_a_withdrawn_value(tmp_path, monkeypatch):
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [
        (T, "The pottery seminar is on Monday at 4 pm."),
        (T + 86400, "Actually, scratch that — the pottery seminar is off."),
    ])
    block, tel = _read(profile, "When is the pottery seminar?")
    assert "4 pm" not in block, block
    assert "4 pm" not in json.dumps([tel, cr._CAPSULE_DEBUG_DROPS]), (tel, cr._CAPSULE_DEBUG_DROPS)


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("tense", ["did I report using", "do I use"])
def test_present_and_past_positive_control(tmp_path, monkeypatch, tense):
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [
        (T, "Remember: I repaired the library ledger using wheat starch paste."),
    ])
    block, _ = _read(profile, f"What binding material {tense} for the library ledger repair?")
    assert "wheat starch paste" in block, block


@pytest.mark.usefixtures("_hash_backend")
def test_keep_control_an_ordinary_corrected_predecessor_rides(tmp_path, monkeypatch):
    _configure(monkeypatch)
    profile = _store(tmp_path, monkeypatch, "fresh-laws", [
        (T, "The tool chest label is 931."),
        (T + 86400, "Correction: the tool chest label is now 942."),
    ])
    block, _ = _read(profile, "What is the tool chest label?")
    assert "931" in block and "942" in block, block
