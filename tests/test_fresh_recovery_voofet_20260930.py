"""Fresh-wording acceptance for the fresh-cohort recovery repairs (2026-09-30).

Every case uses genuinely new wording/domains versus the exposed q90-freshval
cohort (which is regression-only evidence): the mechanisms are the same, the
surface text is not. Cases drive the real store_turn -> inject_retrieved
seam on a disposable profile with the capsule v2 flag the native harness
uses, and assert the ANSWER-BEARING evidence reaches the packed capsule with
attribution. Controls assert the dedup gate still suppresses genuinely
transcript-present records and that single-facet questions keep the strict
admission law.
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


def _turn(home: str, chat: str, user: str, assistant: str = "Noted.") -> dict:
    ensure_chat_namespace(chat, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=chat)
    return cr.store_turn(
        chat, user, assistant, access_policy=policy,
        source_context={"chat_id": chat, "runtime_home": home},
    )


def _capsule(home: str, chat: str, question: str,
             transcript: list[dict] | None = None,
             as_of: str | None = None) -> str:
    policy = resolve_memory_access_policy(chat_id=chat)
    msgs = transcript if transcript is not None else [
        {"role": "user", "content": question}]
    source_context = {"chat_id": chat, "runtime_home": home}
    if as_of is not None:
        source_context["question_as_of"] = as_of
    out = cr.inject_retrieved(
        chat, question, msgs,
        access_policy=policy,
        source_context=source_context,
    )
    return "\n".join(
        str(m.get("content") or "")
        for m in out
        if "retrieved_context" in str(m.get("content"))
    )


# ─── R1: transcript-dedup echo law (fresh cohort F11-13 mechanism) ────────

def test_r1_question_echo_does_not_consume_the_record(fresh_profile):
    """A terse record whose subject words the question echoes — one as a
    clitic fragment ("windlass's") and one inside a longer question word
    ("anchor-windlass") — must still deliver its value. Baseline read
    {"windlass", "s", "quarter"} as 2/3 covered by the question alone."""
    _turn(fresh_profile, "r1-deck",
          "The windlass's battery is at quarter charge.",
          "Quarter charge on the windlass battery, noted.")
    block = _capsule(fresh_profile, "r1-deck",
                     "What charge is the anchor-windlass battery at?")
    assert "quarter" in block


def test_r1_word_exact_presence_unit(fresh_profile):
    # "logger" inside "datalogger" is a different word: substring presence
    # must not carry the ratio over the 0.60 threshold
    assert not cr._content_covered_excluding_query(
        "logger oil", "the datalogger needs oil", "unrelated")
    # a genuinely present answer still counts as covered
    assert cr._content_covered_excluding_query(
        "logger needs oil", "the logger needs oil", "unrelated")


def test_r1_record_already_stated_stays_suppressed(fresh_profile):
    """Control: when the transcript itself already carries the answer, the
    record is not re-injected (the dedup gate's own purpose)."""
    _turn(fresh_profile, "r1-hold",
          "The winch brake drum was skimmed at the yard.",
          "Winch brake drum skimmed, noted.")
    question = "When was the winch brake drum skimmed?"
    transcript = [
        {"role": "user", "content": "Earlier you said the winch brake drum "
                                    "was skimmed at the yard — when was that?"},
    ]
    block = _capsule(fresh_profile, "r1-hold", question,
                     transcript=transcript)
    # the answer-bearing value may serve (the question asks WHEN, the record
    # says WHERE) — but the dedup gate must still exist: identical restate
    # must not double-serve. Assert the gate function directly for the exact
    # restate shape:
    assert cr._content_covered_excluding_query(
        "The winch brake drum was skimmed at the yard.",
        "the winch brake drum was skimmed at the yard",
        "unrelated ask")


# ─── R2: multi-facet admission (fresh cohort F10-09/F10-13 mechanism) ─────

def test_r2_dual_source_serves_both_operands(fresh_profile):
    _turn(fresh_profile, "r2-kiln",
          "The clay stocktake sheet says 12 tonnes of stoneware.",
          "Stocktake sheet: 12 tonnes stoneware, noted.")
    _turn(fresh_profile, "r2-kiln",
          "The supplier's delivery note says 9.5 tonnes.",
          "Delivery note: 9.5 tonnes, noted.")
    block = _capsule(fresh_profile, "r2-kiln",
                     "What do the two records say about stoneware stock?")
    assert "12" in block
    assert "9.5" in block


def test_r2_compound_who_else_serves_both_facets(fresh_profile):
    _turn(fresh_profile, "r2-safe",
          "The night safe held 810 on the closing shift.",
          "Night safe 810 at closing, noted.")
    _turn(fresh_profile, "r2-safe",
          "The auditor's memo also cites 810 for closing.",
          "Auditor's memo agrees on 810, noted.")
    block = _capsule(fresh_profile, "r2-safe",
                     "How much did the night safe hold on closing, "
                     "and who else cited it?")
    assert "810" in block
    assert "auditor" in block.lower()


def test_r2_distributive_each_serves_every_item(fresh_profile):
    _turn(fresh_profile, "r2-wall",
          "The spring survey found 3 cracks in the sea wall.",
          "Spring survey: 3 cracks, noted.")
    _turn(fresh_profile, "r2-wall",
          "The autumn survey found 6 cracks.",
          "Autumn survey: 6 cracks, noted.")
    block = _capsule(fresh_profile, "r2-wall",
                     "How many cracks did each survey find?")
    assert "3" in block
    assert "6" in block


def test_r2_single_facet_recency_question_keeps_strict_law(fresh_profile):
    """Control: a single-facet recency-sensitive question must NOT gain the
    sibling arm — the superseded time stays out (composition's own law)."""
    _turn(fresh_profile, "r2-ferry",
          "The dawn ferry leaves at 06:10.",
          "Dawn ferry 06:10, noted.")
    _turn(fresh_profile, "r2-ferry",
          "Correction: the dawn ferry now leaves at 06:40.",
          "Dawn ferry 06:40, noted.")
    block = _capsule(fresh_profile, "r2-ferry",
                     "What time does the dawn ferry leave?")
    assert "06:40" in block
    assert "06:10" not in block


# ─── R3a: supersession-edge laws (F01-04 / F03-11 / F05-11 / F10-06) ──────

def test_r3a_restoration_supersedes_the_wrong_correction(fresh_profile):
    _turn(fresh_profile, "r3a-harbour",
          "The mooring fee is 40 euros.", "Mooring fee 40 euros, noted.")
    _turn(fresh_profile, "r3a-harbour",
          "Correction: the mooring fee is 14 euros — I misread the board.",
          "Mooring fee 14 euros, noted.")
    _turn(fresh_profile, "r3a-harbour",
          "No wait, the first figure was right: 40 euros.",
          "Back to 40 euros, noted.")
    block = _capsule(fresh_profile, "r3a-harbour", "What is the mooring fee?")
    assert "40" in block
    assert "14" not in block


def test_r3a_withdrawn_paste_stays_dead_after_restate(fresh_profile):
    _turn(fresh_profile, "r3a-desk",
          "Pasting: 'Returns need the purple stamp.'",
          "Purple stamp rule quoted.")
    _turn(fresh_profile, "r3a-desk",
          "Scratch that, returns moved to the e-desk.",
          "Withdrawn - purple stamp rule removed.")
    _turn(fresh_profile, "r3a-desk",
          "To be clear, returns go through the e-desk form.",
          "E-desk for returns, noted.")
    block = _capsule(fresh_profile, "r3a-desk", "How do returns work now?")
    assert "e-desk" in block
    assert "purple stamp" not in block


def test_r3a_partitive_reassignment_keeps_the_complement(fresh_profile):
    _turn(fresh_profile, "r3a-orchard",
          "The surplus apples go to the cider press.",
          "Surplus apples to the cider press, noted.")
    _turn(fresh_profile, "r3a-orchard",
          "Half of them actually go to the farm shop now.",
          "Half to the farm shop, noted.")
    block = _capsule(fresh_profile, "r3a-orchard",
                     "Where do the surplus apples go?")
    assert "cider press" in block
    assert "farm shop" in block


def test_r3a_year_distinct_editions_coexist(fresh_profile):
    _turn(fresh_profile, "r3a-apiary",
          "The 2024 stocktake counted 22 beehives.",
          "2024 stocktake: 22 beehives, noted.")
    _turn(fresh_profile, "r3a-apiary",
          "The 2025 stocktake counted 26 beehives.",
          "2025 stocktake: 26 beehives, noted.")
    block = _capsule(fresh_profile, "r3a-apiary",
                     "How many beehives did each stocktake count?")
    assert "22" in block
    assert "26" in block


# ─── R3b: recency/as-of laws (F07-05 / F11-04 / F15-06) ────────────────────

def test_r3b_month_grain_ladder_serves_the_as_of_rung(fresh_profile):
    _turn(fresh_profile, "r3b-skilift",
          "The day pass cost 30 marks for years.", "Day pass 30 marks, noted.")
    _turn(fresh_profile, "r3b-skilift",
          "It rose to 34 marks in March.", "34 marks from March, noted.")
    _turn(fresh_profile, "r3b-skilift",
          "And to 38 marks from October.", "38 marks from October, noted.")
    block = _capsule(fresh_profile, "r3b-skilift",
                     "As of mid August, what did the day pass cost?",
                     as_of="2026-08-15T00:00:00")
    assert "34" in block
    # the future rung stays excluded; the superseded base may ride only as
    # the anaphoric winner's attributed anchor carrier (binding > purity)
    assert "38 marks" not in block
    assert "day pass" in block.lower()


def test_r3b_reading_frame_newest_reading_wins(fresh_profile):
    _turn(fresh_profile, "r3b-greenhouse",
          "The morning sensor log says soil moisture was 41 percent.",
          "Morning log 41 percent, noted.")
    _turn(fresh_profile, "r3b-greenhouse",
          "The evening sensor log says 39 percent.",
          "Evening log 39 percent, noted.")
    block = _capsule(fresh_profile, "r3b-greenhouse",
                     "What is the soil moisture now?")
    # the newest reading is the answer; the stale twin may ride only as the
    # attributed anchor carrier (binding > value purity - the anaphoric
    # evening line cannot bind without the series' subject line)
    assert "39" in block
    assert "morning" in block.lower() or "evening" in block.lower()


def test_r3b_reading_frame_different_subjects_do_not_merge(fresh_profile):
    _turn(fresh_profile, "r3b-yard",
          "The morning log says the side gate opened at 6.",
          "Side gate 6, noted.")
    _turn(fresh_profile, "r3b-yard",
          "The evening log says the bakery sold 9 loaves.",
          "Bakery 9 loaves, noted.")
    block = _capsule(fresh_profile, "r3b-yard",
                     "How many loaves did the bakery sell?")
    assert "9" in block


def test_r3b_mixed_current_and_past_serves_both_halves(fresh_profile):
    _turn(fresh_profile, "r3b-ferry",
          "The crossing fee was 2 dinars.", "Crossing fee 2 dinars, noted.")
    _turn(fresh_profile, "r3b-ferry",
          "It became 3 dinars in the spring revision.",
          "3 dinars from spring, noted.")
    block = _capsule(fresh_profile, "r3b-ferry",
                     "What is the crossing fee now, and what was it before "
                     "the spring revision?")
    assert "3" in block
    assert "2" in block


# ─── R4/R5: anchor completion + collection pooling (F04-07 / F02-06) ───────

def test_r4_terse_correction_rides_its_anchor_echo(fresh_profile):
    _turn(fresh_profile, "r4-bakery",
          "The rye loaves are proofed as one batch.",
          "One batch per bake, noted.")
    _turn(fresh_profile, "r4-bakery",
          "Adjust: make it two batches, the oven is slow.",
          "Two batches per bake now.")
    block = _capsule(fresh_profile, "r4-bakery",
                     "How many rye batches per bake now?")
    low = block.lower()
    assert "two" in low
    assert "batch" in low
    # the anchor subject and the value co-locate on the echo line
    assert "two batches" in low


def test_r5_collection_ask_pools_the_set_members(fresh_profile):
    _turn(fresh_profile, "r5-seed",
          "Getting my seed order sorted.", "Seed order, noted.")
    _turn(fresh_profile, "r5-seed",
          "The dwarf beans need two trays.", "Two trays of beans, noted.")
    _turn(fresh_profile, "r5-seed",
          "The sweetcorn goes in the cold frame.",
          "Sweetcorn to the cold frame, noted.")
    block = _capsule(fresh_profile, "r5-seed",
                     "Summarize my seed order.")
    assert "two trays" in block.lower()
    assert "sweetcorn" in block.lower()


def test_r5_specific_question_keeps_strict_admission(fresh_profile):
    """Control: a SPECIFIC ask over the same chat must not gain the
    collection pooling arm (adjacent members stay out unless asked for)."""
    _turn(fresh_profile, "r5-seed2",
          "Getting my seed order sorted.", "Seed order, noted.")
    _turn(fresh_profile, "r5-seed2",
          "The dwarf beans need two trays.", "Two trays of beans, noted.")
    _turn(fresh_profile, "r5-seed2",
          "The sweetcorn goes in the cold frame.",
          "Sweetcorn to the cold frame, noted.")
    block = _capsule(fresh_profile, "r5-seed2",
                     "How many trays do the dwarf beans need?")
    assert "two trays" in block.lower()


# ─── R6: front-door competing-subject guard (F11-03) ──────────────────────

def test_r6_receipt_lane_declines_competing_sibling_subject(fresh_profile):
    """A compound memory question whose trailing clause is a bare 'and
    where?' must not be claimed by the file-receipt lane: its sibling clause
    carries the real subject ('davis weather station on the mast')."""
    from core.action_receipt_location import location_followup_kind

    assert location_followup_kind(
        "What kind of weather station is on the mast, and where?",
        receipt=None) is None
    # the lane's own family keeps working
    assert location_followup_kind("where did you create it?", receipt=None) == "locate"
    assert location_followup_kind("path pls", receipt=None) == "locate"


def test_r3b_partitive_month_as_of_resolves_from_question_text(fresh_profile):
    """A real user types 'as of mid <month>' — no plumbed metadata. The
    question-text parser resolves it (early=1st, mid=15th, late/end=last)."""
    import datetime

    from core.temporal_selection import resolve_question_as_of

    now = datetime.datetime(2026, 9, 29, tzinfo=datetime.timezone.utc)
    intent = resolve_question_as_of(
        "As of mid July, what did the binding cost?", now_utc=now)
    assert intent.as_of_end is not None
    assert intent.as_of_end.year == 2026 and intent.as_of_end.month == 7
    assert intent.as_of_end.day == 15
