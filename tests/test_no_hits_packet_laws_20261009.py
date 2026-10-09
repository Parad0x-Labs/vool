"""Receipt-only recall obeys the same refusals when search derivatives miss."""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.evidence_compiler import compile_packet
from core.vool_memory import VoolMemory
from tests.test_d8_packet_faces_the_capsule_laws_20261007 import ON, _ask, _store
from tests.test_question_date_time_leg_20261002 import _hash_backend as _hash_backend


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("law,turns,question,forbidden", [
    ("as-of", [(1747040400.0, "The glaze workshop is on 12 May 2025 at 11 am.")],
     "When was the glaze workshop as of 2025-03-01?", "11 am"),
    ("withdrawn", [(1740823200.0, "The glaze workshop is on 12 May 2025 at 11 am."),
                   (1740909600.0, "Actually, scratch that — the glaze workshop is off.")],
     "When is the glaze workshop?", "11 am"),
    ("live-retraction", [(1740823200.0, "The orchard gatehouse entry fee is 59 dollars."),
                         (1740909600.0, "Disregard that entry fee for the orchard gatehouse."),
                         (1740996000.0, "The orchard gatehouse entry fee is now 80 dollars.")],
     "What is the orchard gatehouse entry fee?", "59"),
    ("absence", [(1740823200.0, "Mira: I signed up for a cooking class. At only $17 per class, it's very cheap!")],
     "How much did Mira pay per dance class?", "$17"),
])
def test_a_refused_receipt_row_never_reaches_the_reader_without_search_hits(
        tmp_path, monkeypatch, law, turns, question, forbidden):
    if law == "live-retraction":
        from core.temporal_selection import retraction_marker

        assert retraction_marker(turns[1][1]) is not None
    for key, value in ON.items():
        monkeypatch.setenv(key, value)
    chat = "packet-only-" + law
    profile = _store(tmp_path, monkeypatch, chat, turns)
    memory = VoolMemory(runtime_home=str(profile))
    try:
        # Real retained sources and real receipts, with unavailable search
        # derivatives. Keep FTS row identities so opening the store does not
        # backfill the deliberately empty index from the retained sources.
        memory._conn.execute("DELETE FROM memory_nodes")
        memory._conn.execute("DELETE FROM memory_fts")
        memory._conn.execute("DELETE FROM source_embeddings")
        memory._conn.execute("UPDATE source_fts SET body = ''")
        memory._conn.commit()
        assert memory.occurrence_search(question, chat_scope=chat) == []
        packet = compile_packet(memory, chat, question)
        assert any(forbidden in str(fact.get("sentence")) for fact in packet.facts), packet
        assert packet.telemetry.get("complete"), packet.telemetry
    finally:
        memory.close()

    block, telemetry = _ask(profile, chat, question)
    assert forbidden not in block, (law, telemetry, block)
    if law == "live-retraction":
        assert "80" in block, block
    refused = (telemetry.get("evidence_compiler") or {}).get("refused_rows") or []
    assert refused, telemetry


@pytest.mark.usefixtures("_hash_backend")
def test_a_corrected_receipt_keeps_its_predecessor_without_search_hits(tmp_path, monkeypatch):
    for key, value in ON.items():
        monkeypatch.setenv(key, value)
    chat = "packet-only-correction"
    profile = _store(tmp_path, monkeypatch, chat, [
        (1740823200.0, "The orchard gatehouse entry fee is 59 dollars."),
        (1740909600.0, "Correction: the orchard gatehouse entry fee is 80 dollars."),
    ])
    memory = VoolMemory(runtime_home=str(profile))
    try:
        memory._conn.execute("DELETE FROM memory_nodes")
        memory._conn.execute("DELETE FROM memory_fts")
        memory._conn.execute("DELETE FROM source_embeddings")
        memory._conn.execute("UPDATE source_fts SET body = ''")
        memory._conn.commit()
    finally:
        memory.close()
    block, _telemetry = _ask(profile, chat, "What is the orchard gatehouse entry fee?")
    assert "59" in block and "80" in block, block


@pytest.mark.usefixtures("_hash_backend")
def test_a_forget_blocks_a_previously_compiled_packet_without_search_hits(tmp_path, monkeypatch):
    from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command

    for key, value in ON.items():
        monkeypatch.setenv(key, value)
    chat = "packet-only-forget"
    profile = _store(tmp_path, monkeypatch, chat, [
        (1740823200.0, "The orchard gatehouse entry fee is 59 dollars."),
    ])
    question = "What is the orchard gatehouse entry fee?"
    memory = VoolMemory(runtime_home=str(profile))
    try:
        memory._conn.execute("DELETE FROM memory_nodes")
        memory._conn.execute("DELETE FROM memory_fts")
        memory._conn.execute("DELETE FROM source_embeddings")
        memory._conn.execute("UPDATE source_fts SET body = ''")
        memory._conn.commit()
        assert memory.occurrence_search(question, chat_scope=chat) == []
        packet = compile_packet(memory, chat, question)
        assert any("59" in str(fact.get("sentence")) for fact in packet.facts), packet
    finally:
        memory.close()
    handled, reply = _forget_command(profile, chat, "forget 59")
    assert handled and "Forget applied" in reply, (handled, reply)
    messages = cr._packet_only_injection(
        [{"role": "user", "content": question}], packet, {},
        chat_id=chat, question=question, check_revocations=True, runtime_home=str(profile))
    assert all("59" not in message["content"] for message in messages), messages


@pytest.mark.usefixtures("_hash_backend")
def test_a_refused_predecessor_cannot_ride_only_as_a_packet_annotation(tmp_path, monkeypatch):
    import core.evidence_compiler as compiler
    from core.temporal_selection import retraction_marker

    for key, value in ON.items():
        monkeypatch.setenv(key, value)
    chat = "packet-only-predecessor-note"
    withdrawal = "Disregard that entry fee for the orchard gatehouse."
    assert retraction_marker(withdrawal) is not None
    profile = _store(tmp_path, monkeypatch, chat, [
        (1740823200.0, "The orchard gatehouse entry fee is 59 dollars."),
        (1740909600.0, withdrawal),
        (1740996000.0, "The orchard gatehouse entry fee is now 80 dollars."),
    ])
    question = "What is the orchard gatehouse entry fee?"
    memory = VoolMemory(runtime_home=str(profile))
    try:
        memory._conn.execute("DELETE FROM memory_nodes")
        memory._conn.execute("DELETE FROM memory_fts")
        memory._conn.execute("DELETE FROM source_embeddings")
        memory._conn.execute("UPDATE source_fts SET body = ''")
        memory._conn.commit()
        assert memory.occurrence_search(question, chat_scope=chat) == []
    finally:
        memory.close()

    compiled = []

    def one_row_packet(*args, **kwargs):
        # Exercise the real compiler's public limit: the predecessor is a
        # dependency of the generated note, without a fact row of its own.
        kwargs["max_lines"] = 1
        packet = compile_packet(*args, **kwargs)
        compiled.append(packet)
        return packet

    monkeypatch.setattr(compiler, "compile_packet", one_row_packet)
    block, telemetry = _ask(profile, chat, question)
    assert len(compiled) == 1
    assert len(compiled[0].facts) == 1
    assert "80" in compiled[0].facts[0]["sentence"]
    assert "59" not in compiled[0].facts[0]["sentence"]
    assert 'replaces "59 dollars"' in compiler.render(compiled[0])
    assert "59" not in block, (telemetry, block)
    assert "80" in block, block
