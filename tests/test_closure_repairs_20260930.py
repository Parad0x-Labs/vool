"""Fresh-wording regression for the closure repairs, 2026-09-30.

New domains/wordings vs the frozen challenge AND every prior corpus. Each
case reproduces a challenge/owner-probe failure family through the real
store -> inject path and pins the repaired contract:

1. A forward-declared successor ("…goes to N in <future month>") must not
   win a TODAY ask while an in-force rung exists (challenge C01).
2. A slot whose ONLY record is future-declared (a scheduled fact) still
   answers a present ask — displacement never starves it (the guard's
   negative case).
3. An anchor ride from a contract-excluded carrier binds the subject and
   never re-delivers the excluded value (owner probe M3 / C02-C07/C24/C35).
4. A paraphrased price ask over a stored priced amount serves — price
   attribute words answer by value shape (C12).
5. "starts on <date>" declares an effective date: future to an as-of ask,
   it abstains (C31).
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy

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


# ── 1. forward-declared successor vs a TODAY ask ─────────────────────────

def test_future_declared_successor_does_not_win_a_current_ask(fresh_profile):
    _turn(fresh_profile, "x1-kiln",
          "The kiln inspection sticker costs 12 dram.")
    _turn(fresh_profile, "x1-kiln",
          "The kiln inspection sticker goes to 17 dram in December.")
    block = _capsule(fresh_profile, "x1-kiln",
                     "What does the kiln inspection sticker cost at the moment?")
    assert "12 dram" in block
    assert "17 dram" not in block


def test_future_declared_successor_does_not_win_a_current_ask_number_two(
        fresh_profile):
    _turn(fresh_profile, "x1-tow",
          "The tow-hamper retrieval deposit is 4 pa'anga.")
    _turn(fresh_profile, "x1-tow",
          "The tow-hamper retrieval deposit climbs to 6 pa'anga from March.")
    block = _capsule(fresh_profile, "x1-tow",
                     "These days, what is the tow-hamper retrieval deposit?")
    assert "4 pa'anga" in block
    assert "6 pa'anga" not in block


# ── 2. the displacement guard's negative case ────────────────────────────

def test_a_lone_scheduled_future_fact_still_answers_a_present_ask(
        fresh_profile):
    _turn(fresh_profile, "x2-lantern",
          "The harbour lantern relight ceremony begins on 9 December.")
    block = _capsule(fresh_profile, "x2-lantern",
                     "When is the harbour lantern relight ceremony?")
    assert "9 December" in block


# ── 3. anchor rides stay value-free for excluded carriers ────────────────

def test_asof_anchor_binds_subject_without_the_future_value(fresh_profile):
    _turn(fresh_profile, "x3-mint",
          "The mint rooftop tour tariff was 8 dirham.")
    _turn(fresh_profile, "x3-mint",
          "It climbed to 12 dirham in February.")
    _turn(fresh_profile, "x3-mint",
          "The mint rooftop tour tariff leaps to 15 dirham from January.")
    block = _capsule(fresh_profile, "x3-mint",
                     "As of late May, what did the mint rooftop tour tariff run at?",
                     as_of="2026-05-28T00:00:00")
    assert "12 dirham" in block
    assert "15 dirham" not in block
    assert "8 dirham" not in block


@pytest.mark.xfail(
    strict=True,
    reason="offline-lane recall limitation (documented, not repaired here): "
    "a markerless anaphoric successor ('It only needs 4 minutes since the "
    "new bracket') has no lexical or temporal hook for the offline "
    "hash-bow lane to join or recall — the frozen challenge's anaphora "
    "cases all carried date/markers and are repaired; this shape needs "
    "the semantic channel",
)
def test_current_anchor_binds_subject_without_the_displaced_value(
        fresh_profile):
    _turn(fresh_profile, "x4-crank",
          "The dockside crank handle takes 9 minutes to stow.")
    _turn(fresh_profile, "x4-crank",
          "It only needs 4 minutes since the new bracket.")
    block = _capsule(fresh_profile, "x4-crank",
                     "How long does the dockside crank handle take to stow now?")
    assert "4 minutes" in block
    assert "9 minutes" not in block


def test_reading_series_displaced_value_does_not_ride_the_anchor(
        fresh_profile):
    _turn(fresh_profile, "x5-well",
          "The dawn well gauge says the cistern depth was 2.9 metres.")
    _turn(fresh_profile, "x5-well",
          "The dusk well gauge says 3.1 metres.")
    block = _capsule(fresh_profile, "x5-well",
                     "What is the cistern depth at present?")
    assert "3.1 metres" in block
    assert "2.9 metres" not in block


# ── 4. paraphrased price asks serve on value shape ────────────────────────

def test_reworded_price_ask_over_a_priced_amount_serves(fresh_profile):
    _turn(fresh_profile, "x6-ferry",
          "The night ferry crossing surcharge is 25 baht.")
    block = _capsule(fresh_profile, "x6-ferry",
                     "How much does the night ferry crossing set you back?")
    assert "25 baht" in block


def test_reworded_fee_ask_over_a_priced_amount_serves(fresh_profile):
    _turn(fresh_profile, "x6-toll",
          "The orchard gate toll runs 7 quetzal a basket.")
    block = _capsule(fresh_profile, "x6-toll",
                     "What's the damage per basket at the orchard gate?")
    assert "7 quetzal" in block


# ── 5. "starts on" declares an effective date ─────────────────────────────

def test_starts_on_is_future_to_an_earlier_as_of_ask(fresh_profile):
    _turn(fresh_profile, "x7-choir",
          "The chapel choir festival kicks off on 3 December.")
    block = _capsule(fresh_profile, "x7-choir",
                     "As of mid November, when did the chapel choir festival kick off?",
                     as_of="2026-11-15T00:00:00")
    assert "3 December" not in block
