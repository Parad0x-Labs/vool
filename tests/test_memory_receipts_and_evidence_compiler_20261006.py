"""Memory receipts + evidence compiler on the served store path (LongMemEval diagnosis, 2026-10-06).

Every case stores turns through the real ``store_turn`` seam with the receipt writer on, then asks through the real
``inject_retrieved`` seam with the compiler on, and reads the packet the reader would see. Hash embeddings (no
Ollama) keep the tests offline; the receipt and compiler paths do not depend on the embedding backend.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d, hh=9, mm=0):
    return float(calendar.timegm(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.setenv("VOOL_MEMORY_RECEIPTS", "1")
    monkeypatch.setenv("VOOL_EVIDENCE_COMPILER", "1")
    configure_runtime_home(profile)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def _store(home, chat, user_text, assistant_text, stated_at):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(chat, user_text, assistant_text, access_policy=policy,
                         source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated_at})


def _ask(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat)
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}],
                              access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    block = "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))
    return block, dict(cr.get_last_retrieval_telemetry())


def _receipts(home, chat):
    from core.memory_receipts import receipts_for_scope

    mem = cr._open_memory_for_runtime(home)
    try:
        return receipts_for_scope(mem, chat)
    finally:
        mem.close()


def test_knowledge_update_receipt_marks_the_old_value_replaced_and_the_packet_says_so(home):
    chat = "chat-ku"
    _store(home, chat, "Session date: 2023/05/20 (Sat) 09:04\nI've been dedicating about an hour each day to coding exercises, which has been helpful in making progress.",
           "Good habit.", _epoch(2023, 5, 20))
    _store(home, chat, "Session date: 2023/05/29 (Mon) 01:53\nI've been dedicating about two hours each day to coding exercises, and I'm excited to see progress in my skills.",
           "Great.", _epoch(2023, 5, 29))
    receipts = _receipts(home, chat)
    user = [r for r in receipts if r["role"] == "user"]
    assert len(user) == 2
    newer = max(user, key=lambda r: r["statement_at"])
    older = min(user, key=lambda r: r["statement_at"])
    assert newer["changes"] and newer["changes"][0]["old_value"] == "an hour" and newer["changes"][0]["new_value"] == "two hours"
    assert older["replaced_by"] and older["replaced_by"][0]["receipt_id"] == newer["receipt_id"]
    assert any(f.get("replaced_by") == newer["receipt_id"] for f in older["facts"])
    block, telemetry = _ask(home, chat, "How much time do I dedicate to coding exercises each day?")
    assert "Evidence receipts" in block
    assert 'two hours' in block and '[replaces "an hour" stated 2023-05-20]' in block
    assert "REPLACED later by \"two hours\"" in block
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["kind"] == "current_value" and ec["complete"] is True


def test_interval_between_two_dated_events_is_complete_and_verifies_the_computed_days(home):
    chat = "chat-between"
    _store(home, chat, "Session date: 2023/04/15 (Sat) 17:41\nI'm trying to figure out what fertilizer to use for my herbs. By the way, I attended a gardening workshop today and learned a lot about composting.",
           "Compost works.", _epoch(2023, 4, 15, 17, 41))
    _store(home, chat, "Session date: 2023/04/21 (Fri) 11:01\nI'm looking for advice on keeping my tomato plants healthy. By the way, I just planted 12 new tomato saplings today and I'm excited to see them grow.",
           "Water them well.", _epoch(2023, 4, 21, 11, 1))
    question = "How many days passed between the day I attended the gardening workshop and the day I planted the tomato saplings?"
    block, telemetry = _ask(home, chat, question)
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["kind"] == "interval_between"
    assert ec["complete"] is True, ec
    assert "(event day 2023-04-15" in block and "(event day 2023-04-21" in block
    assert "- derived from the receipts: 6 days between" in block and "(2023-04-15)" in block and "(2023-04-21)" in block
    from core.evidence_compiler import verify_answer

    facts = telemetry["evidence_packet_facts"]
    good = verify_answer(question, "6 days (Apr 15 to Apr 21).", facts)
    assert good["verified"] and good["rule"] == "interval_from_two_event_days", good
    bad = verify_answer(question, "16 days (Apr 15 to Apr 21, inferred).", facts)
    assert not bad["verified"]


def test_interval_ago_verifies_against_the_reference_day(home):
    chat = "chat-ago"
    _store(home, chat, "Session date: 2023/07/15 (Sat) 08:13\nI just ran the 5K charity run today and finished in 27 minutes, which was a great motivator.",
           "Congratulations.", _epoch(2023, 7, 15, 8, 13))
    question = "How many weeks ago did I participate in the 5K charity run?"
    block, telemetry = _ask(home, chat, question)
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["kind"] == "interval_ago" and ec["complete"] is True, ec
    from core.evidence_compiler import verify_answer

    facts = telemetry["evidence_packet_facts"]
    assert verify_answer(question, "3 weeks ago.", facts, reference_day=date(2023, 8, 5))["verified"]
    assert not verify_answer(question, "6 weeks ago.", facts, reference_day=date(2023, 8, 5))["verified"]


def test_assistant_list_is_a_receipt_fact_and_the_packet_delivers_the_numbered_items(home):
    chat = "chat-list"
    jobs = ["Virtual customer service representative", "Telehealth professional", "Remote bookkeeper", "Virtual tutor",
            "Freelance writer or editor", "Online survey taker", "Transcriptionist", "Social media manager"]
    reply = "Here are some work from home jobs for seniors:\n" + "\n".join(f"{i + 1}. {j}" for i, j in enumerate(jobs))
    _store(home, chat, "Session date: 2023/05/26 (Fri) 12:40\nBrainstorm ideas for work from home jobs for seniors", reply, _epoch(2023, 5, 26, 12, 40))
    block, telemetry = _ask(home, chat, "I think we discussed work from home jobs for seniors earlier. Can you remind me what was the 7th job in the list you provided?")
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["kind"] == "assistant_output" and ec["complete"] is True, ec
    assert "assistant listed:" in block and "7. Transcriptionist" in block


def test_hop_fires_for_a_missing_operand_and_finds_the_dated_record(home):
    chat = "chat-hop"
    _store(home, chat, "Session date: 2023/04/10 (Mon) 10:00\nI went to a lecture on sustainable development at the public library yesterday and it was inspiring.",
           "Nice.", _epoch(2023, 4, 10, 10, 0))
    # the dated sentence of this turn shares no content word with the question; only ITS NEIGHBOUR sentence names
    # the workshop, so the fact is reachable by a search over the whole turn (the hop), not by slot overlap
    _store(home, chat, "Session date: 2023/04/19 (Wed) 20:24\nI'm going over the normalization workshop materials again. The course itself ran on the 17th and 18th of April, and I'm still unclear on a few things.",
           "Standardize for regression.", _epoch(2023, 4, 19, 20, 24))
    for i in range(6):
        _store(home, chat, f"Session date: 2023/04/2{i} (Thu) 09:00\nCan you suggest a podcast about cooking number {i}?", "Sure, try one.", _epoch(2023, 4, 20 + i, 9, 0))
    question = "Which did I attend first, the lecture at the library or the workshop on normalization?"
    block, telemetry = _ask(home, chat, question)
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["kind"] == "order"
    assert ec["hop_fired"] is True and ec["hop_new_facts"] >= 1, ec
    assert ec["complete"] is True, ec
    assert "(event day 2023-04-09" in block and "(event day 2023-04-18" in block
    assert "- derived order from the receipts:" in block and "(2023-04-09) came before" in block


def test_compiler_off_leaves_the_block_without_a_packet(home, monkeypatch):
    chat = "chat-off"
    _store(home, chat, "Session date: 2023/05/20 (Sat) 09:04\nI've been dedicating about an hour each day to coding exercises.", "Good.", _epoch(2023, 5, 20))
    monkeypatch.setenv("VOOL_EVIDENCE_COMPILER", "0")
    block, telemetry = _ask(home, chat, "How much time do I dedicate to coding exercises each day?")
    assert "Evidence receipts" not in block
    assert "evidence_compiler" not in telemetry


def test_receipts_off_writes_no_table(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    profile = tmp_path / "p2"
    profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    monkeypatch.delenv("VOOL_MEMORY_RECEIPTS", raising=False)
    configure_runtime_home(profile)
    embedding_service._best_embed_model = lambda: None
    from storage.migrations import run_migrations

    run_migrations()
    try:
        _store(str(profile), "chat-none", "Session date: 2023/05/20 (Sat) 09:04\nI've been dedicating about an hour each day to coding exercises.", "Good.", _epoch(2023, 5, 20))
        mem = cr._open_memory_for_runtime(str(profile))
        try:
            rows = mem._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_receipts'").fetchall()
        finally:
            mem.close()
        assert rows == []
    finally:
        configure_runtime_home(None)
