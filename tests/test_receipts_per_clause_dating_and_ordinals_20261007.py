"""v14.3: per-clause event dating in the receipts and ordinal operand selection in the compiler, the two set-two
misses that were not guard withdrawals (q33b4b174b7d42a1c / q4fef5a616f2e3be7: one sentence narrating two events;
q4bac36e6fd9eebe4: "my first dentist appointment" among three dentist records). Stores go through the real store_turn
seam and asks through inject_retrieved with hash embeddings. Contributor: sls_0x."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.evidence_compiler import Obligation, _operand_sides, derived_lines
from core.memory.entries import resolve_memory_access_policy
from core.memory_receipts import extract_facts
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d):
    return float(calendar.timegm(datetime(y, m, d, 9, 0, tzinfo=timezone.utc).timetuple()))


def _day(f):
    return datetime.fromtimestamp(f.event_at, tz=timezone.utc).date() if f.event_at else None


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


def _store(home, chat, user_text, stated_at):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(chat, user_text, "Noted.", access_policy=policy, source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated_at})


def _ask(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat)
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}], access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    return "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))


# ─── per-clause dating ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence,first,second", [
    ("I went to the dentist on 25 April and just got back two weeks ago from a trip to Groningen.", date(2025, 4, 25), date(2025, 4, 16)),
    ("I saw the physio yesterday, and I ran the river 10k on 12 April.", date(2025, 4, 29), date(2025, 4, 12)),
    ("Last week I finished the pottery course; I also started the welding class three days ago.", date(2025, 4, 23), date(2025, 4, 27)),
], ids=["explicit-then-relative", "deictic-then-explicit", "semicolon"])
def test_two_events_in_one_sentence_each_keep_their_own_day(sentence, first, second):
    facts = extract_facts(sentence, _epoch(2025, 4, 30), "user")
    events = [f for f in facts if f.value_type == "event"]
    assert {_day(f) for f in events} == {first, second}, [(f.value, _day(f)) for f in facts]
    assert all(f.event_grain != "ambiguous" for f in facts)


def test_a_single_event_sentence_and_an_ambiguous_one_are_unchanged():
    one = extract_facts("I just got back ten days ago from a trip to Malaga.", _epoch(2025, 2, 28), "user")
    assert [_day(f) for f in one if f.value_type == "event"] == [date(2025, 2, 18)]
    amb = extract_facts("I went there two days ago, or maybe three days ago, I forget.", _epoch(2025, 2, 28), "user")
    assert all(f.event_at is None for f in amb)


def test_the_interval_between_two_returns_derives_from_the_right_days(home):
    _store(home, "chat-pc", "I just got back ten days ago from a trip to Malaga.", _epoch(2025, 2, 28))
    _store(home, "chat-pc", "I went to the dentist on 25 April and just got back two weeks ago from a 12-day trip to Groningen.", _epoch(2025, 4, 30))
    _store(home, "chat-pc", "I prefer the train over flying when a trip is under eight hours.", _epoch(2025, 3, 19))
    block = _ask(home, "chat-pc", "How many days after I got back from Malaga did I get back from Groningen?")
    assert "57 days" in block and "66 days" not in block, block


# ─── ordinals ────────────────────────────────────────────────────────────────────────────────────

ORDER = Obligation("order", ["event_date_a", "event_date_b"], "two dated events, ordered")


def _ordinal_history(home):
    _store(home, "chat-ord", "I had a dentist appointment on 31 January for a cleaning.", _epoch(2025, 2, 2))
    _store(home, "chat-ord", "I went to the intro to pottery workshop on 13 March.", _epoch(2025, 3, 15))
    _store(home, "chat-ord", "I had another dentist appointment on 1 April to check a filling.", _epoch(2025, 4, 3))
    _store(home, "chat-ord", "I had my dentist appointment on 20 April for the crown.", _epoch(2025, 4, 22))


@pytest.mark.parametrize("q,before", [
    ("Which came first, the intro to pottery workshop or my first dentist appointment?", "2025-01-31"),
    ("Which came first, the intro to pottery workshop or my last dentist appointment?", "2025-03-13"),
    ("which came first the pottery workshop or my second dentist appointment", "2025-03-13"),
], ids=["first", "last", "second-sloppy"])
def test_ordinal_variants_pick_their_record(home, q, before):
    _ordinal_history(home)
    block = _ask(home, "chat-ord", q)
    assert f"({before}) came before" in block, block


def test_side_parsing_needs_two_named_events():
    assert _operand_sides("Which came first, my last optometrist visit or the welding workshop?") == ["my last optometrist visit", "the welding workshop"]
    assert _operand_sides("How many days were there between my first two dermatologist appointments?") == []
    assert _operand_sides("How many days ago did I get back from Salzburg?") == []


# ─── set three: two purchases are not one changed value ───────────────────────────────────────────

def test_a_second_purchase_does_not_replace_the_first_but_a_corrected_price_does(home):
    from core.memory_receipts import receipts_for_scope

    _store(home, "chat-ch", "Just last weekend, I bought a bike helmet for $95 and I need to budget better.", _epoch(2025, 3, 25))
    _store(home, "chat-ch", "I recently bought a bike computer for $165 to help track my rides.", _epoch(2025, 4, 24))
    _store(home, "chat-ch", "Correction: the bike helmet I bought last weekend was $99, not $95.", _epoch(2025, 4, 26))
    mem = cr._open_memory_for_runtime(home)
    try:
        receipts = receipts_for_scope(mem, "chat-ch")
    finally:
        mem.close()
    def first_sentence(r):
        return str((r.get("facts") or [{}])[0].get("sentence") or "")

    user = [r for r in receipts if r["role"] == "user"]
    computer = next(r for r in user if first_sentence(r).startswith("I recently bought"))
    correction = next(r for r in user if first_sentence(r).startswith("Correction:"))
    assert computer["changes"] == [], computer["changes"]
    assert any(c["old_value"].strip("*") == "$95" for c in correction["changes"]), correction["changes"]
