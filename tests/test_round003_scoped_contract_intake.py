"""ROUND-003 pins — a scoped output contract must mint from ordinary human wording.

Frozen root (council/round-003/FIX_PLAN.md): `_mint_ledger`'s frame regex required the count to
sit IMMEDIATELY before the answer-noun, so one adjective silently killed the contract. Both
contract turns of the live session minted nothing:

    'my next TWO actual answers'          NO MATCH
    'the next THREE substantive replies'  NO MATCH

Everything downstream is correct and was never armed. Probe 1 (council/round-003/R2/PROBE1_RAW.md),
intake widened and nothing else changed:

    006 -> 'noted — exact words applies to your next 2 answer(s).'   (six garbage lines gone)
    007 -> 'Atoms vibrate faster.'                        [words = 3]

SABOTAGE SEAM: revert the bridge in the frame regex and `test_contract_mints_*` goes red naming the
missing mint. These tests drive `repl._mint_ledger` DIRECTLY — never a reimplementation. Round-002's
first dominance pin reimplemented its logic and stayed green when the real code was reverted; a pin
that does not drive the real function proves nothing.
"""
from __future__ import annotations

import pytest

import core.kernel.repl as repl

# Verbatim from the frozen round-003 tape.
TURN_006 = ("Starting with my next TWO actual answers, reply using exactly three words each. "
            "This instruction-setting reply itself is not one of those two answers.")
TURN_010 = ("For the next THREE substantive replies after this message, use exactly six lowercase "
            "words per reply. This acknowledgement is outside that three-answer count.")


def _kinds(question: str) -> dict[str, str]:
    return {e["kind"]: e["arg"] for e in repl._mint_ledger(question)}


# ------------------------------------------------------------------ the contract must mint
@pytest.mark.parametrize("question,kind,arg,ttl", [
    (TURN_006, "exact_words", "3", 2),
    ("For your next two real replies, use exactly four words each.", "exact_words", "4", 2),
    ("for my next 2 actual answers pls use exactly 3 words each thx", "exact_words", "3", 2),
    ("The next 3 proper answers should be exactly five words.", "exact_words", "5", 3),
])
def test_contract_mints_from_ordinary_wording(question, kind, arg, ttl):
    """The count and the answer-noun need not be adjacent. Reverting the bridge turns this red."""
    entries = repl._mint_ledger(question)
    assert entries, f"the scoped contract did not mint at all: {question!r}"
    got = {e["kind"]: e for e in entries}
    assert kind in got, f"expected a {kind} entry, got {entries!r}"
    assert got[kind]["arg"] == arg
    assert got[kind]["ttl"] == ttl, "the TTL must come from the answer count, not the word count"


def test_turn_010_frame_mints_both_constraints_gap_closed_round007():
    """Turn 010 ("the next THREE substantive replies … exactly SIX lowercase words") now arms in
    FULL. Round-003 closed the token-adjacency root (the frame matches despite "substantive") but
    deliberately left a PARKED partial: `six` was outside `_TTL_WORDS` and the count alternation,
    so the word count silently dropped and only `lowercase` armed.

    ROUND-007 CLOSED THAT GAP (council/round-007/MISALIGNMENT_MATRIX.md §1, IDEA-005). The number
    vocabulary is one shared table reaching twenty, so a spelled count above five arms exact_words.
    This test — which round-003 wrote anticipating exactly this ("if this now mints, the parked gap
    was closed elsewhere") — now pins the CLOSED state: both constraints arm.
    """
    kinds = _kinds(TURN_010)
    assert "lowercase" in kinds, "the frame must arm the contract"
    assert kinds.get("exact_words") == "6", (
        "round-007 closed the number-vocabulary gap: 'exactly six lowercase words' must now arm "
        "exact_words(6), not silently drop it")


def test_the_adjacent_form_still_mints():
    """Negative control on the repair itself: the wording that always worked must keep working."""
    assert _kinds("For your next TWO answers only, use exactly three words each.") \
        .get("exact_words") == "3"


def test_lowercase_contract_still_mints():
    """The constraint-clause detection is untouched by this repair."""
    assert "lowercase" in _kinds(
        "For the next two actual replies, use lowercase and exactly four words.")


# ------------------------------------------------------------------ it must NOT over-mint
@pytest.mark.parametrize("question", [
    "What are the next TWO actual prime numbers?",           # not an answer-noun
    "Tell me about the next THREE major wars in history",
    "List the next FIVE important events",
    "Answer the next two questions for me",                  # frame, but NO constraint clause
    "In the next two answers you gave earlier, you mentioned X.",   # no constraint clause
    "Why does metal expand when heated?",
    "Return ONLY the final numeric total. No label, receipt, or extra words.",
    "hi",
])
def test_intake_does_not_over_mint(question):
    """A frame without a constraint clause mints nothing, and a non-answer noun never frames.
    These are what bound the false-positive risk three seats raised in R2."""
    assert repl._mint_ledger(question) == [], f"over-minted: {repl._mint_ledger(question)!r}"


def test_known_pre_existing_false_positive_is_unchanged_by_this_repair():
    """HONEST BOUNDARY, not an endorsement.

    A DESCRIPTIVE sentence that both names a next-N answer frame and quotes a word count already
    minted on the pre-repair regex — it is a tense/imperative discrimination gap, a separate root,
    and fixing it here would break the one-repair rule. This test records the behaviour so the
    boundary is visible and a future round can close it deliberately rather than by accident.
    Parked in council/IDEA_BACKLOG.md.
    """
    descriptive = "In the next two answers you gave, you used exactly three words."
    assert repl._mint_ledger(descriptive), (
        "this pre-existing false positive is expected to still mint; if it stopped minting, "
        "something outside this repair's scope changed and the backlog item needs revisiting")
