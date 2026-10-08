"""FRESH ACCEPTANCE SET — mb-native-memrepair2-20260927 (candidate 247c6bea lineage).

Frozen BEFORE first acceptance execution. New wording, new domains, new
entities, new event order (telescope gear, tabla drum, plant shelves, violin
lessons, e-bike, hostel keys) — not renamings of the benchmark failures.

Covers the three repaired contracts:

Contract A (speaker-role provenance): inside a stored pasted transcript,
generated ASSISTANT advice never outranks the user's own assertion; advice
still fills the capsule when nothing user-authored qualifies (fallback
preserved); an assistant-suggested item the user never adopted is not
delivered as the user's own choice.

Contract T (stated-time supersession): when two records assert different
values for the same subject, the value with the LATEST stated conversation
date wins even when it was ingested FIRST (import-order independence).

Contract M (utterance admission): a conversational first-person fact with a
concrete value is stored at utterance granularity and recallable; filler
acknowledgements and bare questions stay below the floor; secret redaction
still applies on the newly admitted classes.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.runtime_paths import configure_runtime_home

# Contract A — pasted transcript, user assertion vs dense assistant advice.
A_SCOPE_RECORD = (
    "Session date: 2026/03/14 (Sat) 10:02\n"
    "USER: I'm saving up for a bigger telescope. By the way, I already bought "
    "a used 8-inch Dobsonian from the astronomy club for 320.\n"
    "\n"
    "ASSISTANT: Great choice! Here are accessories worth considering:\n"
    "\n"
    "1. **Eyepiece Kit**: A multi-focal-length eyepiece set improves versatility.\n"
    "2. **Barlow Lens**: A 2x Barlow lens doubles your effective focal length.\n"
    "3. **Phone Adapter**: A smartphone mount lets you photograph the moon.\n"
)
A_QUESTION = "Which telescope did I buy from the astronomy club?"
A_USER_LINE = "I already bought a used 8-inch Dobsonian from the astronomy club for 320."

# Contract A fallback — a record whose only qualifying content is advice.
A_FALLBACK_QUESTION = "What magnification does a 2x Barlow lens give?"
A_ADVICE_LINE = "A 2x Barlow lens doubles your effective focal length."

# Contract A refusal — assistant suggested a mount; the user never adopted it.
A_NEVER_RECORD = (
    "Session date: 2026/03/20 (Fri) 21:40\n"
    "USER: Any tips on storing my guitar over the winter?\n"
    "\n"
    "ASSISTANT: Buy a humidity-controlled cabinet; the Casa Tone CabX keeps "
    "a steady 45% humidity and fits two guitars.\n"
)
A_NEVER_QUESTION = "Which guitar cabinet did I buy?"
A_NEVER_BRAND = "CabX"

# Contract T — same subject, two values, stated dates OPPOSE ingest order.
T_EARLIER_INGESTED_LAST = (
    "Session date: 2026/04/02 (Thu) 08:15\n"
    "USER: Quick update on my plant corner: the orchids moved to the balcony "
    "shelf, I've counted 11 pots there now.\n"
)
T_LATER_INGESTED_FIRST = (
    "Session date: 2026/04/19 (Sun) 19:03\n"
    "USER: The balcony shelf is too sunny, so the orchids are on the study "
    "windowsill now, all 14 pots of them.\n"
)
T_QUESTION = "How many orchid pots do I have right now?"
T_LATEST_VALUE = "14 pots"
T_STALE_VALUE = "11 pots"

# Contract T event pair — earlier/current role wording.
T2_FIRST = (
    "Session date: 2026/01/09 (Fri) 12:00\n"
    "USER: I started violin lessons with teacher Mara, the fee is 40 per hour.\n"
)
T2_UPDATED = (
    "Session date: 2026/05/30 (Sat) 15:45\n"
    "USER: My new violin teacher is Dorian and I pay 55 per hour now.\n"
)
T2_QUESTION_LATEST = "What hourly fee do I pay my violin teacher these days?"
T2_LATEST = "55 per hour"
T2_STALE = "40 per hour"

# Contract M — utterance-level admission of conversational facts.
M_RECORD = (
    "Session date: 2026/06/06 (Sat) 09:20\n"
    "USER: By the way, I paid 180 for the used tabla drum at the winter fair, "
    "and I've been practicing it every evening since.\n"
)
M_QUESTION = "How much did my tabla drum cost?"
M_FILLER = "USER: Haha yeah exactly, thanks a lot!"
M_QUESTION_ONLY = "USER: So what do you think about second-hand instruments?"
M_SECRET = (
    "Session date: 2026/06/07 (Sun) 10:00\n"
    "USER: I paid 12 for the locker key deposit and my API key is "
    "sk-TEST-MEMREPAIR2-4hZ9pQw7Xk2Lm.\n"
)


@pytest.fixture()
def fresh_profile(tmp_path, monkeypatch):
    import core.embedding_service as embedding_service

    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_CONTEXT_CAPSULE_V2", "1")
    configure_runtime_home(home)
    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: None)
    from storage.migrations import run_migrations

    run_migrations()
    yield str(home)
    configure_runtime_home(None)


def _store(home: str, chat: str, text: str):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(
        chat, text, "Noted.", access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _inject(home: str, chat: str, question: str):
    policy = resolve_memory_access_policy(chat_id=chat)
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy, source_context={"chat_id": chat, "runtime_home": home},
    )
    return "\n".join(
        str(m.get("content") or "") for m in out if "retrieved_context" in str(m.get("content"))
    )


def test_a1_user_assertion_outranks_dense_assistant_advice(fresh_profile):
    _store(fresh_profile, "chat-a1", A_SCOPE_RECORD)
    block = _inject(fresh_profile, "chat-a1", A_QUESTION)
    assert A_USER_LINE.split(" for 320")[0] in block


def test_a2_assistant_advice_not_presented_as_user_choice(fresh_profile):
    _store(fresh_profile, "chat-a1", A_SCOPE_RECORD)
    block = _inject(fresh_profile, "chat-a1", A_QUESTION)
    assert "you should have bought" not in block
    # the advice list may appear, but never ahead of the user's own line
    if "Barlow" in block:
        assert block.index("Dobsonian") < block.index("Barlow")


def test_a3_advice_still_fills_when_nothing_user_authored_qualifies(fresh_profile):
    # first-run harness defect, retained: this stored A_NEVER_RECORD (guitar
    # transcript) while asking a Barlow question about A_SCOPE_RECORD; the
    # fixture now stores the record that owns the advice line. Product
    # behavior under test is unchanged by the correction.
    _store(fresh_profile, "chat-a3", A_SCOPE_RECORD)
    block = _inject(fresh_profile, "chat-a3", A_FALLBACK_QUESTION)
    assert A_ADVICE_LINE in block


def test_a4_assistant_suggestion_never_adopted_is_not_a_user_fact(fresh_profile):
    _store(fresh_profile, "chat-a4", A_NEVER_RECORD)
    block = _inject(fresh_profile, "chat-a4", A_NEVER_QUESTION)
    assert A_NEVER_BRAND not in block


def test_t1_latest_stated_value_wins_regardless_of_ingest_order(fresh_profile):
    # ingest NEWEST statement FIRST: import order must not decide currency
    _store(fresh_profile, "chat-t1", T_LATER_INGESTED_FIRST)
    _store(fresh_profile, "chat-t1", T_EARLIER_INGESTED_LAST)
    block = _inject(fresh_profile, "chat-t1", T_QUESTION)
    assert T_LATEST_VALUE in block
    if T_STALE_VALUE in block:
        # both may appear; the latest must be presented first
        assert block.index(T_LATEST_VALUE) < block.index(T_STALE_VALUE)


def test_t2_current_fee_supersedes_initial_fee(fresh_profile):
    _store(fresh_profile, "chat-t2", T2_UPDATED)
    _store(fresh_profile, "chat-t2", T2_FIRST)
    block = _inject(fresh_profile, "chat-t2", T2_QUESTION_LATEST)
    assert T2_LATEST in block
    if T2_STALE in block:
        assert block.index(T2_LATEST) < block.index(T2_STALE)


def test_m1_conversational_first_person_fact_stored_and_recalled(fresh_profile):
    status = _store(fresh_profile, "chat-m1", M_RECORD)
    assert status.get("status") == "stored"
    block = _inject(fresh_profile, "chat-m1", M_QUESTION)
    assert "180" in block and "tabla" in block


def test_m2_filler_and_question_only_utterances_stay_unstored(fresh_profile):
    # Since "retain both roles as source evidence" (2026-09-29), every turn is kept verbatim as layer-1
    # source evidence and reports "retained"; the admission gates decide only semantic INDEXING. Filler
    # and a bare question must still never be indexed (stored_count 0).
    for text in (M_FILLER, M_QUESTION_ONLY):
        result = _store(fresh_profile, "chat-m2", text)
        assert result.get("status") == "retained"
        assert result.get("reason") == "source_evidence_retained"
        assert result.get("stored_count") == 0
        assert result.get("occurrence_ids")


def test_m3_secret_redaction_applies_to_newly_admitted_utters(fresh_profile):
    status = _store(fresh_profile, "chat-m3", M_SECRET)
    assert status.get("secret_redacted") is True
    block = _inject(fresh_profile, "chat-m3", "What did I pay as locker deposit?")
    assert "sk-TEST-MEMREPAIR2" not in block
    assert "12" in block
