"""v14.6 hardening item 1 (ASTRA Pro review, 2026-10-07): typed completeness in the evidence compiler. An obligation
carries a class; ABSENCE, EXTREMUM, LIST_ALL, CURRENT_STATE and AGGREGATE are complete only when the scoped candidate set
(every user fact sharing a subject term) is shown in full, and the packet says so. A clean refusal (no record at all)
stays clean. Served-store seams, no model call. Contributor: sls_0x."""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.evidence_compiler import obligation_class, obligations, scoped_coverage
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home


def _epoch(y, m, d, hh=9):
    return float(calendar.timegm(datetime(y, m, d, hh, tzinfo=timezone.utc).timetuple()))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    import core.embedding_service as es

    profile = tmp_path / "profile"; profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("NULLA_CONTEXT_CAPSULE_V2", "VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_KERNEL"):
        monkeypatch.setenv(name, "1")
    configure_runtime_home(profile); es._best_embed_model = lambda: None
    from storage.migrations import run_migrations
    run_migrations()
    yield str(profile)
    configure_runtime_home(None)


def _store(home, chat, user_text, stated_at):
    ensure_chat_namespace(chat, grant_current_receipts=False); policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(chat, user_text, "Noted.", access_policy=policy, source_context={"chat_id": chat, "runtime_home": home, "statement_at": stated_at})


def _packet(block: str) -> str:
    # the compiler's own lines (the receipts packet), not the capsule's retrieval block above it
    return "\n".join(l for l in block.split("\n") if l.startswith("- [") or l.startswith("- This question needs"))


def _ask(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat); cr.reset_retrieval_telemetry()
    out = cr.inject_retrieved(chat, question, [{"role": "user", "content": question}], access_policy=policy, source_context={"chat_id": chat, "runtime_home": home})
    return "\n".join(str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content"))), dict(cr.get_last_retrieval_telemetry())


# ─── classes ───────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question, cls", [
    ("Did I ever say I hated jazz?", "EXISTS"),
    ("have i ever mentioned a cousin in Lyon", "EXISTS"),
    ("Have I never mentioned Berlin?", "ABSENCE"),
    ("Did I not tell you about the lease at any point?", "ABSENCE"),
    ("Is there any record of me buying a tent?", "ABSENCE"),
    ("What was the first laptop I bought?", "EXTREMUM"),
    ("whats the latest camera i picked up", "EXTREMUM"),
    ("What did I buy most recently for the kitchen?", "EXTREMUM"),
    ("Which did I buy first, the lamp or the desk?", "ORDER"),
    ("Did the dentist visit come before the pottery class?", "ORDER"),
    ("How long between the two trips?", "PAIR"),
    ("What do I currently prefer for breakfast?", "CURRENT_STATE"),
    ("What car do I drive now?", "CURRENT_STATE"),
    ("List all the cities I visited.", "LIST_ALL"),
    ("which of them did i actually finish", "LIST_ALL"),
    ("How many books did I read?", "AGGREGATE"),
    ("What did you recommend for the trip?", "QUOTE_SPEAKER"),
    ("When did I say I moved to Berlin?", "SINGLE"),
    ("What was the name of the hotel in Lyon?", "SINGLE"),
])
def test_obligation_classes(question, cls):
    ob = obligations(question)
    assert ob.cls == cls, (question, ob.kind, ob.cls)
    assert ob.needs_coverage == (cls in ("ABSENCE", "EXTREMUM", "LIST_ALL", "CURRENT_STATE", "AGGREGATE"))


def test_a_class_is_stamped_from_the_kind_and_the_wording():
    assert obligation_class("existence", "Have I ever been to Oslo?") == "EXISTS"
    assert obligation_class("existence", "Have I never been to Oslo?") == "ABSENCE"
    assert obligation_class("single_fact", "What was my earliest job?") == "EXTREMUM"


# ─── coverage on the served store ──────────────────────────────────────────────────────────────

def _laptops(home, chat, n):
    # n dated laptop records. The earliest and the latest share ONE term with "What was the first/latest laptop I bought?"
    # (laptop), the others share two (laptop, bought), so lexical rank alone pushes the edges out of a 14-line view and
    # only time-ordered rendering keeps them in
    _store(home, chat, "My laptop, a Zenbook 1, came home on 1 March.", _epoch(2025, 3, 1))
    for i in range(1, n - 1):
        _store(home, chat, f"I bought a laptop, a Zenbook {i + 1}, on {2 + i} March for work.", _epoch(2025, 3, 2 + i))
    _store(home, chat, f"My laptop, a Zenbook {n}, came home on {1 + n} March.", _epoch(2025, 3, 1 + n))


def test_an_extremum_over_a_small_scope_is_complete_and_exhaustive(home):
    chat = "chat-first-laptop-small"
    _laptops(home, chat, 4)
    block, telemetry = _ask(home, chat, "What was the first laptop I bought?")
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["class"] == "EXTREMUM" and ec["complete"] is True, ec
    assert ec["coverage"]["exhaustive"] and ec["coverage"]["candidates"] == ec["coverage"]["shown"] >= 4
    assert "Coverage: exhaustive" in block and "Zenbook 1" in _packet(block)


def test_an_extremum_below_top_k_is_partial_and_the_earliest_is_still_shown(home):
    chat = "chat-first-laptop-many"
    _laptops(home, chat, 20)
    block, telemetry = _ask(home, chat, "What was the first laptop I bought?")
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["class"] == "EXTREMUM"
    assert ec["complete"] is False, ec
    assert not ec["coverage"]["exhaustive"] and ec["coverage"]["candidates"] > ec["coverage"]["shown"]
    assert "Coverage: partial" in block and "do not conclude which was first or latest" in block
    assert "Zenbook 1" in _packet(block)   # time-ordered rendering keeps the earliest candidate inside the partial view


def test_a_latest_ask_renders_the_newest_candidate_first(home):
    chat = "chat-latest-laptop"
    _laptops(home, chat, 20)
    block, telemetry = _ask(home, chat, "What is the latest laptop I bought?")
    assert "Zenbook 20" in _packet(block) and telemetry["evidence_compiler"]["complete"] is False


def test_an_absence_ask_over_an_exhausted_scope_is_complete(home):
    chat = "chat-absence-small"
    _store(home, chat, "I bought a tent on 2 May for the camping trip.", _epoch(2025, 5, 2))
    _store(home, chat, "The tent leaked on 9 May, so I returned it.", _epoch(2025, 5, 9))
    block, telemetry = _ask(home, chat, "Have I never mentioned a tent?")
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["class"] == "ABSENCE" and ec["complete"] is True and ec["coverage"]["exhaustive"]


def test_an_absence_ask_over_a_truncated_scope_is_partial(home):
    chat = "chat-absence-many"
    for i in range(20):
        _store(home, chat, f"Tent note {i + 1}: I checked the tent poles on {1 + i} June.", _epoch(2025, 6, 1 + i))
    block, telemetry = _ask(home, chat, "Have I never mentioned a tent?")
    ec = telemetry["evidence_compiler"]
    assert ec["complete"] is False and "do not conclude that something was never said" in block


def test_a_current_state_ask_is_complete_only_when_the_chain_is_shown(home):
    chat = "chat-current-car"
    _store(home, chat, "I drive a Fiat Panda these days.", _epoch(2025, 1, 10))
    _store(home, chat, "I traded the Panda in; I drive a Kia Niro now.", _epoch(2025, 4, 2))
    block, telemetry = _ask(home, chat, "What car do I drive now?")
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["class"] == "CURRENT_STATE" and ec["complete"] is True and ec["coverage"]["exhaustive"], ec
    assert "Kia Niro" in block


def test_a_trap_with_no_record_still_refuses_cleanly(home):
    # the coordinator's case: nothing about Berlin was ever stored; no packet line may hedge toward an answer
    chat = "chat-berlin-trap"
    _store(home, chat, "I bought a laptop on 3 March.", _epoch(2025, 3, 3))
    _store(home, chat, "The dentist visit is on 12 April.", _epoch(2025, 4, 1))
    block, telemetry = _ask(home, chat, "When did I say I moved to Berlin?")
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["class"] == "SINGLE"
    assert "Berlin" not in block.replace("When did I say I moved to Berlin?", "")
    assert ec["coverage"]["candidates"] == ec["coverage"]["shown"]
    assert "Coverage: partial" not in block and "do not conclude" not in block


def test_an_absence_trap_with_no_candidates_is_exhaustive_not_a_hedge(home):
    chat = "chat-berlin-absence"
    _store(home, chat, "I bought a laptop on 3 March.", _epoch(2025, 3, 3))
    block, telemetry = _ask(home, chat, "Have I never said anything about Berlin?")
    ec = telemetry["evidence_compiler"]
    assert ec["obligation"]["class"] == "ABSENCE"
    assert ec["coverage"]["candidates"] == 0 and ec["coverage"]["exhaustive"]
    assert "do not conclude" not in block


def test_scoped_coverage_counts_only_user_facts_that_share_a_term():
    receipts = [{"receipt_id": "r:1", "role": "user", "facts": [{"slot": ["laptop", "bought"], "sentence": "I bought a laptop.", "value_type": "event"}]},
                {"receipt_id": "r:2", "role": "user", "facts": [{"slot": ["dentist"], "sentence": "Dentist on Monday.", "value_type": "event"}]},
                {"receipt_id": "r:3", "role": "assistant", "facts": [{"slot": ["laptop", "tip"], "sentence": "A laptop tip.", "value_type": "list"}]}]
    cov = scoped_coverage(receipts, "What was the first laptop I bought?", [{"receipt_id": "r:1", "sentence": "I bought a laptop."}])
    assert cov == {"candidates": 1, "shown": 1, "exhaustive": True, "unseen_receipts": []}
    cov = scoped_coverage(receipts, "What was the first laptop I bought?", [])
    assert cov["candidates"] == 1 and cov["shown"] == 0 and not cov["exhaustive"] and cov["unseen_receipts"] == ["r:1"]
