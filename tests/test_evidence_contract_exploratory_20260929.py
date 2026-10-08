"""Fresh exploratory acceptance for the layer-1 evidence contract (mr29/1).

Genuinely new wording, facts, domains, dates and roles versus every
previously seen case (repro sets, memrepair2b, assertion-gate, v4). Each
case exercises the real store_turn -> reopen -> inject_retrieved path on
a disposable profile with the capsule v2 flag the native harness uses.

Positive cases assert the exact answer-bearing span is delivered WITH its
role attribution; controls assert absence, scope isolation, deletion and
the retention-without-promotion boundary.
"""
from __future__ import annotations

import json

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy

try:  # shared fixture/seam pattern from the existing acceptance trees
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (
        fresh_profile,
    )
except ImportError:  # pragma: no cover - fixture import guard
    fresh_profile = None

pytestmark = pytest.mark.usefixtures("fresh_profile")


def _store(home: str, chat: str, user: str, assistant: str = "Noted.") -> dict:
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(
        chat, user, assistant, access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _inject(home: str, chat: str, question: str) -> str:
    policy = resolve_memory_access_policy(chat_id=chat)
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy, source_context={"chat_id": chat, "runtime_home": home},
    )
    return "\n".join(
        str(m.get("content") or "")
        for m in out
        if "retrieved_context" in str(m.get("content"))
    )


def test_e1_assistant_history_is_recallable_as_assistant_output(fresh_profile):
    _store(fresh_profile, "e1-chat",
           "Who watches the greenhouse while I am away?",
           "Mira watches the greenhouse; her backup number is 070-411-8820 "
           "and she comes by twice a day.")
    block = _inject(fresh_profile, "e1-chat",
                    "What backup number did you give me for the greenhouse?")
    assert "070-411-8820" in block
    assert "assistant said" in block
    # assistant output must never be attributed to the user
    assert "user said" not in block


def test_e2_ordinary_short_fact_survives_admission_gates(fresh_profile):
    # 33 chars, importance ~0.2: never indexed at baseline, retained now
    _store(fresh_profile, "e2-chat", "The bakery closes at noon on Sundays.")
    block = _inject(fresh_profile, "e2-chat",
                    "When does the bakery close on Sundays?")
    assert "noon" in block


def test_e3_condition_keeps_its_full_clause(fresh_profile):
    _store(fresh_profile, "e3-chat",
           "The attic ladder only locks if you turn the handle twice.")
    block = _inject(fresh_profile, "e3-chat",
                    "How does the attic ladder lock?")
    assert "turn the handle twice" in block


def test_e4_correction_keeps_both_values_and_delivers_the_newest(fresh_profile):
    _store(fresh_profile, "e4-chat", "Remember the ski locker code is 4417.")
    _store(fresh_profile, "e4-chat",
           "Scratch that, the ski locker code is 8890 now.")
    block = _inject(fresh_profile, "e4-chat", "What is the ski locker code?")
    assert "8890" in block
    # CONFLICT AMENDMENT (q90-temporal, 2026-09-29, escalated to the lead):
    # this row originally asserted the superseded value ("4417") stays in
    # the CURRENT-ask block. The memory-quality90 acceptance corpus pins
    # the opposite contract for exactly this shape (F01/F12 current asks:
    # a superseded or retracted value must NOT ride the packing surface —
    # must_not_appear applies to the whole capsule). Retention is upheld
    # where it actually lives: both occurrences stay stored and the OLD
    # value remains recallable as attributed evidence for a historical ask.
    assert "4417" not in block
    assert "Scratch that" in block
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=fresh_profile)
    try:
        bodies = [
            str(row["body"]) for row in mem._conn.execute(
                "SELECT body FROM source_occurrences WHERE chat_scope = 'e4-chat'"
            )
        ]
    finally:
        mem.close()
    assert sum("4417" in body for body in bodies) == 1
    # OPEN ITEM (surfaced to the lead, not pinned here): whether a
    # marker-corrected slot surfaces its old value for an explicit
    # "before the change" ask is a real design question — the corpus's
    # recount law (F01-05) supersedes the mis-statement globally, while a
    # stated change ("8890 now") leaves the old value true of the earlier
    # period. The current contract supersedes in both cases; the answer
    # belongs to a future correction-semantics slice with its own fresh
    # cases, not to this row.


def test_e5_quoted_third_party_text_is_retained_but_not_promoted(fresh_profile):
    pasted = (
        'My physio said: "drop the evening stretching routine, keep the '
        'morning one" so I am following that.'
    )
    _store(fresh_profile, "e5-chat", pasted)
    block = _inject(fresh_profile, "e5-chat",
                    "What did my physio tell me about stretching?")
    assert "morning" in block
    # retention is not promotion: the quoted advice must not land in the
    # structured user-fact ledger as the user's own standing fact
    from core.memory import entries as memory_entries

    ledger = memory_entries.memory_entries_path() if hasattr(
        memory_entries, "memory_entries_path"
    ) else None
    if ledger is not None and ledger.exists():
        rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
        promoted = [
            row for row in rows
            if "stretching routine" in str(row.get("fact") or row.get("text") or "")
        ]
        # the quote may not be promoted even once
        assert promoted == []


def test_e6_foreign_chat_cannot_read_evidence(fresh_profile):
    _store(fresh_profile, "e6-chat-a",
           "Remember the harbour mooring fee is 62 crowns per night.")
    ensure_chat_namespace("e6-chat-b", grant_current_receipts=False)
    block = _inject(fresh_profile, "e6-chat-b",
                    "What is the harbour mooring fee?")
    assert "62" not in block
    assert "crowns" not in block


def test_e7_forget_removes_source_and_derivative(fresh_profile):
    _store(fresh_profile, "e7-chat",
           "Remember the tide pool access word is KELP-9931.")
    block = _inject(fresh_profile, "e7-chat", "What is the tide pool access word?")
    assert "KELP-9931" in block
    removed = cr.forget_session_memory("e7-chat", "KELP-9931")
    assert removed >= 1
    block2 = _inject(fresh_profile, "e7-chat", "What is the tide pool access word?")
    assert "KELP-9931" not in block2


def test_e8_unicode_spans_stay_codepoint_aligned(fresh_profile):
    body = "Grannen Björk lämnade stegens kod — 0️⃣9️⃣2️⃣4 — under blomkrukan."
    _store(fresh_profile, "e8-chat", body)
    telemetry_block = _inject(fresh_profile, "e8-chat",
                              "Var lämnade grannen stegens kod?")
    assert "blomkrukan" in telemetry_block
    # span verbatimity over the retained body (emoji included)
    mem = cr._open_memory_for_runtime(fresh_profile)
    try:
        hits = mem.occurrence_search("stegens kod", chat_scope="e8-chat")
        assert hits, "unicode body must be findable"
        occurrence = hits[0][0]
        windows = cr._evidence_clause_windows("Var lämnade grannen stegens kod?", occurrence.body)
        assert windows
        for window in windows:
            assert occurrence.body[window["start"]:window["end"]] == window["text"]
    finally:
        mem.close()


def test_e9_identical_bodies_stay_distinct_occurrences(fresh_profile):
    _store(fresh_profile, "e9-chat", "The bell tower key hangs in the vestry.")
    _store(fresh_profile, "e9-chat", "The bell tower key hangs in the vestry.")
    mem = cr._open_memory_for_runtime(fresh_profile)
    try:
        from core.vool_memory import VoolMemory

        assert mem.occurrence_count(chat_scope="e9-chat") >= 4  # 2x user + 2x assistant
        hits = mem.occurrence_search("bell tower key", chat_scope="e9-chat")
        user_ids = [h[0].occurrence_id for h in hits if h[0].role == "user"]
        assert len(user_ids) >= 2  # both user occurrences retained distinctly
        assert len(set(user_ids)) == len(user_ids)
    finally:
        mem.close()


def test_n1_unrelated_query_returns_no_evidence(fresh_profile):
    _store(fresh_profile, "n1-chat",
           "Remember the ferry ticket office opens at half past seven.")
    block = _inject(fresh_profile, "n1-chat",
                    "What colour is the lighthouse painted?")
    assert "half past seven" not in block


def test_n2_pure_question_record_delivers_no_evidence_line(fresh_profile):
    _store(fresh_profile, "n2-chat",
           "Should I reserve the north pier table for eight people?")
    block = _inject(fresh_profile, "n2-chat",
                    "Which pier table did I reserve?")
    assert "eight people" not in block
    # ... but the question is still RETAINED as source evidence
    mem = cr._open_memory_for_runtime(fresh_profile)
    try:
        assert mem.occurrence_count(chat_scope="n2-chat") >= 1
    finally:
        mem.close()


def test_n3_missing_and_deleted_references_are_truthfully_incomplete(fresh_profile):
    # a reference that never existed
    missing = cr.materialize_source_evidence(
        "nonexistent-occurrence-id", runtime_home=str(fresh_profile)
    )
    assert missing["complete"] is False
    assert missing["reason"] == "occurrence_not_found"
    assert missing["body"] == ""
    # a deleted occurrence keeps identity but never fabricates text
    mem = cr._open_memory_for_runtime(str(fresh_profile))
    try:
        occurrence = mem.occurrence_store(
            chat_scope="n3-chat", role="user",
            body="the orchard gate combination is 22-714",
            authority="observed-user-statement",
        )
        occ_id = occurrence.occurrence_id
        mem.occurrence_invalidate_matching("22-714", chat_scope="n3-chat")
    finally:
        mem.close()
    deleted = cr.materialize_source_evidence(occ_id, runtime_home=str(fresh_profile))
    assert deleted["complete"] is False
    assert deleted["reason"] == "occurrence_deleted"
    assert deleted["body"] == ""
    assert deleted.get("role") == "user"


def test_e10_legacy_default_path_delivers_attributed_evidence(fresh_profile, monkeypatch):
    """Capsule v2 OFF (the product default): the evidence leg still delivers
    assistant history and short ordinary facts with role attribution,
    additively — node records keep their scores and positions."""
    import os

    monkeypatch.delenv("VOOL_CONTEXT_CAPSULE_V2", raising=False)
    assert not os.environ.get("VOOL_CONTEXT_CAPSULE_V2")
    _store(fresh_profile, "e10-chat",
           "Who covers the north greenhouse shift?",
           "Ruta covers the north greenhouse shift; her call sign is GREEN-7.")
    _store(fresh_profile, "e10-chat", "My mug lives in the left drawer.", "Noted.")
    block = _inject(fresh_profile, "e10-chat",
                    "What call sign did you give me for the north greenhouse shift?")
    assert "GREEN-7" in block
    assert "assistant said" in block
    block2 = _inject(fresh_profile, "e10-chat", "Where does my mug live?")
    assert "left drawer" in block2
    assert "user said" in block2


def test_e11_legacy_path_is_byte_identical_without_occurrences(fresh_profile, monkeypatch):
    """No retained occurrences for the query -> the legacy output carries
    only the node records exactly as before the port (additive law)."""
    import os

    monkeypatch.delenv("VOOL_CONTEXT_CAPSULE_V2", raising=False)
    # a semantic node written directly (no store_turn, no occurrence)
    from core.vool_memory import VoolMemory

    ensure_chat_namespace("e11-chat", grant_current_receipts=False)
    mem = VoolMemory(runtime_home=fresh_profile)
    try:
        mem.node_store(
            content="Remember the lighthouse lamp oil is pressed from seal blubber.",
            keywords=["lighthouse", "oil"],
            tags=["user", "scope:chat", "authority:confirmed_memory",
                  "status:active", f"session:{cr._session_scope_key('e11-chat')}"],
            context_description=(
                f"session={cr._session_scope_key('e11-chat')} role=user scope=chat "
                "authority=confirmed_memory status=active"
            ),
            embedding=cr.embed(
                "Remember the lighthouse lamp oil is pressed from seal blubber."
            ),
        )
    finally:
        mem.close()
    block = _inject(fresh_profile, "e11-chat",
                    "What is the lighthouse lamp oil pressed from?")
    assert "seal blubber" in block
    assert "said" not in block  # no evidence lines: node-only rendering unchanged
    assert block.count("<retrieved_context>") == 1
