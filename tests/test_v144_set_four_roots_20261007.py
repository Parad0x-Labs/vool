"""v14.4: the four code-caused misses of sealed set four, fixed at the root, with controls (CLAUDE.md 6b family):
compound number words ("forty-five"), a traded-in car named with an article, a former state ("I used to live in"),
and apostrophes inside contractions read as quote marks. Stores go through the real store_turn seam and asks through
inject_retrieved with hash embeddings. Contributor: sls_0x."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.evidence_compiler import question_state_key
from core.evidence_kernel.claim_binder import bind_claims, evidence_records
from core.evidence_kernel.temporal_binder import bind_temporal_claims
from core.memory.entries import resolve_memory_access_policy
from core.memory_receipts import _number, extract_facts
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


def _store(home, chat, text, stated_at):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    return cr.store_turn(chat, text, "Noted.", access_policy=resolve_memory_access_policy(chat_id=chat), source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated_at})


def _ask(home, chat, q):
    cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, q, [{"role": "user", "content": q}], access_policy=resolve_memory_access_policy(chat_id=chat), source_context={"chat_id": chat, "runtime_home": home})
    return "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content")))


# ─── compound number words ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,value", [("forty-five", 45), ("forty five", 45), ("twenty-two", 22), ("ninety nine", 99), ("thirty", 30), ("fifteen", 15), ("five", 5)])
def test_compound_number_words_read_as_one_number(text, value):
    assert _number(text) == value


@pytest.mark.parametrize("sentence,minutes", [
    ("I already spend about forty-five minutes meditating daily.", 45), ("i meditate twenty five minutes most mornings", 25),
    ("My commute is thirty-two minutes each way.", 32), ("It took one hour and five minutes.", 65),
], ids=["forty-five", "twenty-five-sloppy", "thirty-two", "hour-and-five"])
def test_a_compound_duration_is_the_whole_number_not_its_last_word(sentence, minutes):
    durs = [f for f in extract_facts(sentence, _epoch(2025, 2, 9), "user") if f.value_type == "duration"]
    assert durs and durs[0].norm == f"{minutes:g}min", [(f.value, f.norm) for f in durs]


def test_forty_five_minutes_binds_and_thirty_does_not():
    f = next(iter(extract_facts("I already spend **about forty-five minutes** meditating daily.", _epoch(2025, 2, 9), "user")))
    facts = [{"sentence": f.sentence, "value_type": f.value_type, "value": f.value, "norm": f.norm, "event_at": None, "statement_at": _epoch(2025, 2, 9), "receipt_id": "r:x", "role": "user"}]
    q = "How long did I spend meditating each day when I first mentioned it?"
    assert bind_temporal_claims(question=q, reply="About forty-five minutes daily (first mentioned 2025-02-09).", packet_facts=facts, reference_day=date(2025, 6, 25)).restored
    assert not bind_temporal_claims(question=q, reply="About thirty minutes daily.", packet_facts=facts, reference_day=date(2025, 6, 25)).restored


# ─── a traded-in car named with an article; a former home ────────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "I remember when I traded in the Renault Clio for a Kia Niro and thought I'd be organized.",
    "traded in my renault clio for a kia niro last month",
    "I swapped the Renault Clio for a Kia Niro.",
], ids=["the", "my-sloppy", "swapped"])
def test_the_traded_in_car_is_named_without_its_article(sentence):
    tr = [f for f in extract_facts(sentence, _epoch(2025, 6, 20), "user") if f.value_type == "state_transition"]
    assert tr and tr[0].norm == "?=kia niro|old=renault clio", [(f.value, f.norm) for f in tr]


def test_the_newer_car_is_the_current_vehicle_in_the_packet(home):
    _store(home, "chat-car", "I drive a Renault Clio and sometimes have time during my drives.", _epoch(2025, 1, 19))
    _store(home, "chat-car", "I remember when I traded in the Renault Clio for a Kia Niro and thought I'd be organized, but this is chaos!", _epoch(2025, 6, 20))
    block = _ask(home, "chat-car", "What car do I drive now?")
    assert "<vehicle = Kia Niro>" in block and "Renault Clio" in block and block.index("Kia Niro") < block.index("<vehicle = Renault Clio>") if "<vehicle = Renault Clio>" in block else "<vehicle = Kia Niro>" in block, block


@pytest.mark.parametrize("sentence", ["I remember when I used to live in Rijeka, I had a simpler inbox.", "i used to live in rijeka", "We used to live in Rijeka before the move."], ids=["clause", "sloppy", "we"])
def test_a_former_home_is_a_former_state_not_a_current_one(sentence):
    st = [f for f in extract_facts(sentence, _epoch(2025, 1, 9), "user") if f.value_type == "state"]
    assert st and st[0].norm == "home_city=rijeka|former"


def test_where_did_i_grow_up_reaches_the_former_home_without_making_it_current(home):
    _store(home, "chat-home", "I remember when I used to live in Rijeka, I had a much simpler inbox.", _epoch(2025, 1, 9))
    _store(home, "chat-home", "I live in Tampere these days and the winters are long.", _epoch(2025, 3, 2))
    _store(home, "chat-home", "I recently moved to Antwerp for work.", _epoch(2025, 6, 1))
    assert question_state_key("Where did I grow up?") == "home_city" and question_state_key("What is my hometown?") == "home_city"
    block = _ask(home, "chat-home", "Where did I grow up?")
    assert "<former home_city = Rijeka>" in block, block
    now = _ask(home, "chat-home", "Which city do I live in now?")
    assert "<home_city = Antwerp>" in now and now.index("<home_city = Antwerp>") < now.index("<former home_city = Rijeka>"), now


# ─── apostrophes in contractions are not quote marks ─────────────────────────────────────────────

@pytest.mark.parametrize("line", [
    "I've been thinking about reading more, but I just bought a rear rack for $79 this week and I'm not sure how to fit it in. (stated: 2026-02-08)",
    "I'm so glad I bought the rear rack for $79, it's great and I'd do it again. (stated: 2026-02-08)",
    "Don't laugh, but I paid $79 for a rear rack and I can't return it. (stated: 2026-02-08)",
], ids=["ive-im", "im-its-id", "dont-cant"])
def test_contraction_apostrophes_do_not_make_a_quotation(line):
    recs = evidence_records([line])
    assert recs and not recs[0].quoted and recs[0].usable


def test_real_single_quotes_still_mark_a_quotation():
    recs = evidence_records(["A forum post reads 'my rear rack was $79' and I'm not sure I believe it. (stated: 2026-02-08)"])
    assert recs and recs[0].quoted


def test_the_cycling_average_binds_with_a_contraction_in_the_rack_record():
    ev = "<retrieved_context>\n- user said: I've been trying to get my budget in order lately because I recently bought a bike computer for $165. (stated: 2025-04-06)\n- user said: I've been thinking about getting back into reading more, but I just bought a rear rack for $79 this week and I'm not sure how to fit reading in. (stated: 2025-02-08)\n- user said: I bought a saddle bag for $36 last weekend, and I'd like to make cleaning easier. (stated: 2025-03-17)\n</retrieved_context>"
    r = bind_claims(question="What is the average price of the cycling items I bought this year?", reply="$93.33 — bike computer $165, rear rack $79, saddle bag $36 (total $280 ÷ 3).", evidence_text=ev)
    assert r.all_supported, r.as_dict()


def test_a_former_home_stated_later_does_not_replace_the_current_one(home):
    _store(home, "chat-home2", "I live in Antwerp now and like the harbour.", _epoch(2025, 1, 9))
    _store(home, "chat-home2", "Years ago I used to live in Rijeka, by the way.", _epoch(2025, 6, 1))
    now = _ask(home, "chat-home2", "Which city do I live in now?")
    assert "<home_city = Antwerp>" in now and "REPLACED later" not in now.split("<home_city = Antwerp>")[1].split("\n")[0], now
    assert "<former home_city = Rijeka>" in now
