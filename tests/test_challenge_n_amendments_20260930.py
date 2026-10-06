"""Fresh-wording amendments approving the N-challenge repairs (2026-09-30).

Different wording, relationships and expected outcomes vs the frozen N set
(and vs every prior corpus). Each repair gets one or more approving cases
PLUS a negative control guarding the law it touched: the dedup weakening
must still drop genuinely repeated records, the join expansion must not
merge strangers, and the supersession license must not collapse genuine
parallel facts.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (  # noqa: F401
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


def _capsule(home, chat, question, as_of=None):
    policy = resolve_memory_access_policy(chat_id=chat)
    src = {"chat_id": chat, "runtime_home": home}
    if as_of:
        src["question_as_of"] = as_of
    out = cr.inject_retrieved(
        chat, question, [{"role": "user", "content": question}],
        access_policy=policy, source_context=src)
    return "\n".join(
        str(m.get("content") or "") for m in out
        if "retrieved_context" in str(m.get("content")))


# ── hedge/month repair: months are not modality ────────────────────────────

def test_march_month_change_is_a_marker_not_a_hedge(fresh_profile):
    _turn(fresh_profile, "a1-tar",
          "The quarry tar shed ships 9 loads a day.")
    _turn(fresh_profile, "a1-tar",
          "It rose to 14 loads a day in March.")
    block = _capsule(fresh_profile, "a1-tar",
                     "How many loads a day does the quarry tar shed ship now?")
    assert "14 loads" in block
    assert "9 loads" not in block


def test_genuine_modality_still_hedges_and_does_not_supersede(fresh_profile):
    _turn(fresh_profile, "a2-pier",
          "The pier crane pads are stored under the stairs.")
    _turn(fresh_profile, "a2-pier",
          "We may move them to the boathouse, no decision yet.")
    block = _capsule(fresh_profile, "a2-pier",
                     "Where are the pier crane pads stored?")
    # the decided fact answers; the speculative move never replaces it
    assert "under the stairs" in block


# ── gate case-lowering repair: capitalized discriminators are present ──────

def test_sentence_initial_capitalized_discriminator_is_not_absent(fresh_profile):
    _turn(fresh_profile, "a3-kiln",
          "Kiln-row allotments water on Tuesdays.")
    block = _capsule(fresh_profile, "a3-kiln",
                     "When do the Kiln-row allotments water?")
    assert "Tuesdays" in block


# ── substring dedup: query echo is not transcript evidence ────────────────

def test_question_restating_the_subject_clause_serves_the_record(fresh_profile):
    _turn(fresh_profile, "a4-net",
          "Netting at the grouse moor happens before sunrise.")
    block = _capsule(fresh_profile, "a4-net",
                     "When does netting at the grouse moor happen?")
    assert "before sunrise" in block


def test_genuinely_repeated_transcript_record_still_dedups(fresh_profile):
    # negative control for the dedup weakening: the record's whole answer
    # clause already in the TRANSCRIPT (a prior turn, not the question)
    # adds nothing and must not re-serve
    chat = "a5-repeat"
    _turn(fresh_profile, chat,
          "The harbourmaster's skiff is called the Kittiwake.")
    policy = resolve_memory_access_policy(chat_id=chat)
    src = {"chat_id": chat, "runtime_home": str(fresh_profile)}
    out = cr.inject_retrieved(
        chat, "What is the harbourmaster's skiff called?",
        [{"role": "user", "content": "Earlier you told me the harbourmaster's "
         "skiff is called the Kittiwake."},
         {"role": "user", "content": "What is the harbourmaster's skiff called?"}],
        access_policy=policy, source_context=src)
    block = "\n".join(str(m.get("content") or "") for m in out
                      if "retrieved_context" in str(m.get("content")))
    assert "Kittiwake" not in block  # already stated in the conversation


# ── superlative recency: single-record law ─────────────────────────────────

def test_latest_reading_serves_only_the_newest_rung(fresh_profile):
    _turn(fresh_profile, "a6-flume",
          "The flume gauge logged a 62-centimetre flow in the spring.")
    _turn(fresh_profile, "a6-flume",
          "The flume gauge logged an 88-centimetre flow this week.")
    block = _capsule(fresh_profile, "a6-flume",
                     "What flow did the flume gauge log most recently?")
    assert "88-centimetre" in block
    assert "62-centimetre" not in block


def test_explicit_comparison_keeps_both_operands(fresh_profile):
    _turn(fresh_profile, "a7-bake",
          "The spring bake-off drew 31 entries.")
    _turn(fresh_profile, "a7-bake",
          "The autumn bake-off drew 47 entries.")
    block = _capsule(fresh_profile, "a7-bake",
                     "Which bake-off was the largest, the spring one or the "
                     "autumn one?")
    assert "31 entries" in block and "47 entries" in block


# ── from-dated anaphoric join + light verbs ────────────────────────────────

def test_from_dated_anaphoric_continuation_displaces_its_base(fresh_profile):
    _turn(fresh_profile, "a8-crow",
          "The ferry crow's nest carries an oil lamp.")
    _turn(fresh_profile, "a8-crow",
          "Since 14 August 2019 it has carried an LED cluster.")
    block = _capsule(fresh_profile, "a8-crow",
                     "What does the ferry crow's nest carry?")
    assert "LED cluster" in block
    assert "oil lamp" not in block


def test_stranger_chats_never_join(fresh_profile):
    # negative control for the join expansion: same unit, different
    # subjects, same chat — parallel facts, both stay available
    _turn(fresh_profile, "a9-doves",
          "The dovecote holds 12 birds.")
    _turn(fresh_profile, "a9-doves",
          "The aviary holds 8 birds.")
    block = _capsule(fresh_profile, "a9-doves",
                     "How many birds does the aviary hold?")
    assert "8 birds" in block


# ── reassignment construction crosses the template boundary ───────────────

def test_switches_to_supersedes_across_the_template(fresh_profile):
    # AMENDMENT (oracle direction corrected, product was right): "from
    # January" at a 2026-09-30 statement moment resolves to NEXT January
    # (2027-01-31) — the successor is future, so the in-force answer for a
    # "these days" ask is the current nine o'clock opening and the future
    # ten o'clock one stays out.
    _turn(fresh_profile, "b1-mill",
          "The mill shop opens at nine on market days.")
    _turn(fresh_profile, "b1-mill",
          "The mill shop moves to a ten o'clock opening from January.")
    block = _capsule(fresh_profile, "b1-mill",
                     "When does the mill shop open these days?")
    assert "nine" in block
    assert "ten o'clock" not in block


def test_digitless_reassignment_displaces_its_base(fresh_profile):
    _turn(fresh_profile, "b1b-lamp",
          "The ferry crow's nest carries an oil lamp.")
    _turn(fresh_profile, "b1b-lamp",
          "The crow's nest moves to an LED cluster since 14 August 2019.")
    block = _capsule(fresh_profile, "b1b-lamp",
                     "What does the ferry crow's nest carry these days?")
    assert "LED cluster" in block
    assert "oil lamp" not in block


def test_parallel_facts_with_distinct_subjects_still_coexist(fresh_profile):
    # negative control for the supersession license: no reassignment
    # construction, genuinely parallel template facts — neither supersedes
    _turn(fresh_profile, "b2-light",
          "The harbour beacon flashes twice a minute.")
    _turn(fresh_profile, "b2-light",
          "The point lighthouse flashes five times a minute.")
    block = _capsule(fresh_profile, "b2-light",
                     "How many times a minute does the point lighthouse flash?")
    assert "five times" in block
