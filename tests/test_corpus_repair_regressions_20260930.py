"""Exposed-corpus regression repairs (2026-09-30 handoff continuation).

The 208 exposed-corpus run on ef8fd744 surfaced five pass→fail vs the
earlier baseline; diagnosis attributed them to owners repaired here:

1. Parallel weekday-named subjects ("the Monday ferry" vs "the Thursday
   ferry", "weekday/weekend inspection slot") collapsed under recency
   supersession because slot signatures stripped ALL temporal vocabulary —
   an ATTRIBUTIVE day/season word names WHICH subject. Time-of-day words
   stay temporal (reading-series law owns them).
2. The value-free anchor prefix refused terse subjects ("Binding was 6
   credits…" -> "Binding" < 12 chars) and its verb-cut truncated
   constructions where the numeral cut was the right boundary; a
   sentence-final period defeated the numeral match. The prefix now cuts
   at the numeral when one exists, falls back to the verb only for
   digit-less values, and never strips below the anchor-bearing subject.
3. The facet gate counted the ask's mention-register idiom ("as I
   mentioned in passing") and action verb ("When do I start…") as absent
   discriminators and blanked the answer record.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.temporal_selection import _parallel_subject_facts, slot_signature

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

pytestmark = pytest.mark.usefixtures("fresh_profile")


def _turn(home, chat, user, assistant="Noted."):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(
        chat, user, assistant, access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home})


def _capsule(home, chat, question):
    policy = resolve_memory_access_policy(chat_id=chat)
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home})
    return "\n".join(
        str(m.get("content") or "") for m in out
        if "retrieved_context" in str(m.get("content")))


# ── 1. attributive day-words are subject identity ─────────────────────────

def test_weekday_named_subjects_are_parallel_not_superseded():
    a = "The Monday ferry leaves at 09:15."
    b = "The Thursday ferry leaves at 10:40."
    assert "monday" in slot_signature(a)
    assert "thursday" in slot_signature(b)
    assert _parallel_subject_facts(a, b) is True


def test_adverbial_day_words_stay_temporal():
    a = "The ferry leaves at 09:15 on Monday."
    b = "The ferry leaves at 10:40 on Thursday."
    assert "monday" not in slot_signature(a)
    # one ferry, two readings: NOT parallel facts, recency law applies
    assert _parallel_subject_facts(a, b) is False


def test_time_of_day_words_stay_temporal_even_attributive():
    # the reading-series law owns them: morning/afternoon logs are ONE
    # series observed twice
    assert "morning" not in slot_signature("The morning log says 27.")
    assert "afternoon" not in slot_signature("The afternoon log says 28.5.")


def test_weekday_and_weekend_slots_both_serve(fresh_profile):
    _turn(fresh_profile, "x-wd", "The weekday inspection slot is 06:30.")
    _turn(fresh_profile, "x-wd", "The weekend inspection slot is 07:45.")
    block = _capsule(fresh_profile, "x-wd",
                     "What are the two inspection slots?")
    assert "06:30" in block and "07:45" in block


def test_two_ferries_both_serve_their_times(fresh_profile):
    _turn(fresh_profile, "x-ferry", "The Monday ferry leaves at 09:15.")
    _turn(fresh_profile, "x-ferry", "The Thursday ferry leaves at 10:40.")
    block = _capsule(fresh_profile, "x-ferry",
                     "What times do the Monday and Thursday ferries leave?")
    assert "09:15" in block and "10:40" in block


# ── 2. the value-free anchor prefix ───────────────────────────────────────

def test_terse_subject_anchor_is_delivered_value_free():
    prefix = cr._value_free_anchor_prefix(
        "Binding was 6 credits for years.", {"binding"}, {"6"})
    assert prefix == "Binding"


def test_numeral_cut_wins_over_verb_cut_when_both_exist():
    prefix = cr._value_free_anchor_prefix(
        "The morning log says the water temperature was 27.",
        {"water", "temperature"}, {"27"})
    assert prefix is not None
    assert "temperature" in prefix and "27" not in prefix


def test_sentence_final_period_does_not_defeat_the_numeral_cut():
    prefix = cr._value_free_anchor_prefix(
        "The gauge reads 41.", {"gauge"}, {"41"})
    assert prefix == "The gauge"


# ── 3. ask-frame words are not absent discriminators ──────────────────────

def test_mention_in_passing_ask_serves_the_passing_mention(fresh_profile):
    _turn(fresh_profile, "x-pass",
          "While we wait — I told the guard I take over the parcel van "
          "duties in February, right? Anyway, the trolley needs a wash.")
    _turn(fresh_profile, "x-pass", "The platform kettle is broken too.")
    block = _capsule(fresh_profile, "x-pass",
                     "When do I start on the parcel van duties, as I "
                     "mentioned in passing?")
    assert "February" in block
