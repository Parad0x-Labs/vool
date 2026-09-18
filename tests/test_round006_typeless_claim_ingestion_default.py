"""ROUND-006 pins — a synthesized claim that carries text but omits/garbles its `type` must be
RESCUED at ingestion (type defaulted, claim re-validated), not destroyed.

Frozen root (council/round-006/MISALIGNMENT_MATRIX.md, R1 unanimous on the root, R2 unanimous on
the site): turn 009's synthesis produced {"obligation_id":"ob1","text":"Warm air rises cooler."}
with NO type field. The gate at repl.py:2791 hard-rejected it (`ctype not in CLAIM_TYPES`), render
saw zero claims, and liveness shipped a dead-turn banner reading "no claim produced" — a lie, since
a claim WAS produced. Turn 010 (same question, type='timeless') shipped fine: the missing tag, not
the question, killed turn 009.

THE R2 DECISION THIS PINS. Two repairs were on the table:
  * INGESTION-DEFAULT (chosen, unanimous): default the missing type so the claim passes THROUGH the
    normal gates — including the Law-2 numeric re-gate that refuses an ungrounded number.
  * KEEP-ANY-REJECTED (refuted): let the rejected claim bypass validation as best-effort. Refuted
    because a type-less claim carrying an ungrounded number would then ship AS FACT.
`test_typeless_number_is_still_refused_by_law2` is that decisive probe, encoded: the rescue must NOT
become a validation bypass. If it ever ships the number as an observed fact, ingestion-default has
silently degraded into keep-any and this pin goes red.

Every test drives repl.run_turn. Nothing here reimplements the ingestion logic (the round-002
vacuous-pin trap). Sabotage seams are documented at the bottom.
"""
from __future__ import annotations

import json

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner

_FIVE_WORD = {repl._LEDGER_KEY: json.dumps([{"kind": "exact_words", "arg": "5", "ttl": 2}])}


def _judge(synth_claims, extract_desc="explain why hot air rises", lane="knowledge"):
    """Drive one knowledge obligation and return exactly the claim rows given — verbatim, so a row
    with no `type` key reaches the ingester exactly as turn 009's did."""
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": [{"description": extract_desc, "lane": lane, "query": "",
                                     "format": "", "source_offset": 0, "resolves_carryover": ""}]}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims}
        return {"verdicts": [], "all_parts_answered": True, "missing": ""}
    return fake


def _drive(monkeypatch, synth_claims, *, facts=None, question="Why does hot air rise?",
           lane="knowledge"):
    monkeypatch.setattr(repl, "_model_json", _judge(synth_claims, lane=lane))
    transcript, _j, _l, _f = repl.run_turn(
        question, EffectRunner(mode="record"), None, None,
        session_facts=dict(facts) if facts else None)
    return transcript, repl._extract_answer(transcript)


# ---------------------------------------------------------------- the turn must not die (turn 009)
def test_turn009_typeless_claim_ships_instead_of_dead_turn(monkeypatch):
    """The exact turn-009 shape: text present, NO type field, under a 5-word scope.

    ROUND-008 REPIN (council/round-008/FIX_PLAN.md, ROOT FROZEN 2026-08-23): this pin
    previously asserted the 4-word typeless claim SHIPS — round-006's resolution under the
    then-standing accept-and-degrade policy. Round-008 froze fail-closed exact_words: a
    proven non-conforming underrun no longer commits. The round-006 ROOT — the typeless
    claim is RESCUED at ingestion, never destroyed, and the banner must not lie — is still
    fully asserted: the transcript shows the loud type default, and the terminal names the
    TRUE cause ("format unsatisfied: 4 of 5 words"), never "no claim produced". The
    conforming sibling below proves the rescued claim still ships when the count is met.
    """
    transcript, answer = _drive(
        monkeypatch, [{"obligation_id": "ob1", "text": "Warm air rises cooler."}], facts=_FIVE_WORD)
    assert answer is not None and answer.strip(), "a produced answer must not end in a dead turn"
    assert "claim type defaulted" in transcript, "the typeless rescue must still happen"
    assert "couldn\u2019t meet the exact 5-word requirement" in answer, (
        f"the conversational fallback must ship (ROUND-011 UX repin): {answer!r}")
    assert "format unsatisfied: best model effort was 4 of 5 words" in transcript, (
        "internal truth must remain in the transcript/settle record")
    assert "no claim produced" not in transcript.split("\x01")[-1], (
        "the transcript must not declare 'no claim produced' — one WAS produced")


def test_turn009_typeless_claim_conforming_to_the_count_still_ships(monkeypatch):
    """The rescue ships end-to-end when the count is met: same typeless shape, 5 words."""
    transcript, answer = _drive(
        monkeypatch, [{"obligation_id": "ob1", "text": "Warm air rises and cools."}],
        facts=_FIVE_WORD)
    assert answer is not None and answer.strip()
    assert "Warm air rises and cools" in (answer or ""), (
        f"a rescued conforming claim must ship: {answer!r}")
    assert "claim type defaulted" in transcript
    assert "Cannot answer this turn" not in (answer or "")


def test_typeless_claim_type_is_defaulted_loudly(monkeypatch):
    """The rescue is a visible decision, never silent (the transcript names the default)."""
    transcript, _ = _drive(
        monkeypatch, [{"obligation_id": "ob1", "text": "Warm air rises cooler."}], facts=_FIVE_WORD)
    assert "claim type defaulted" in transcript, "the default must be recorded in the transcript"
    assert "claim row rejected (no text" not in transcript, "a text-present claim is not a no-text reject"


# ----------------------------------------------------- THE DECISIVE R2 PROBE: no validation bypass
def test_typeless_number_is_still_refused_by_law2(monkeypatch):
    """The vote-deciding control. A type-less claim carrying an UNGROUNDED number must NOT ship the
    number as an observed fact. Ingestion-default routes it through Law 2 (which refuses it);
    keep-any-rejected would have shipped it. If this fails, the rescue has become a bypass."""
    transcript, answer = _drive(
        monkeypatch, [{"obligation_id": "ob1", "text": "Hot air is 450 kelvin hot."}])
    ans = answer or ""
    # The ungrounded number must never ship dressed as a grounded/observed fact. Either the claim
    # is refused (fabricated-number class) or it ships wearing the unverified/model-memory mark —
    # never as bare observed fact with a receipt it does not have.
    if "450" in ans:
        assert ("unverified" in ans.lower() or "model memory" in ans.lower()
                or "[unverified" in ans.lower()), (
            f"an ungrounded number shipped as unmarked fact — ingestion-default degraded into "
            f"keep-any-rejected: {ans!r}")
    assert "observed" not in ans.lower(), "an ungrounded number must never wear an observed/receipt mark"


# ---------------------------------------------------------------------- nothing else may change
def test_no_text_claim_still_rejects_honestly(monkeypatch):
    """The split must keep the ONE honest rejection: a claim with no text is still declined."""
    transcript, _ = _drive(monkeypatch, [{"obligation_id": "ob1", "type": "unverified", "text": ""}])
    assert "claim row rejected (no text" in transcript, (
        "a genuinely empty claim must still be rejected — the default rescues text, not silence")


def test_valid_typed_claim_is_untouched(monkeypatch):
    """Negative control: a well-typed claim (turn 010's shape) ships exactly as before."""
    transcript, answer = _drive(
        monkeypatch, [{"obligation_id": "ob1", "type": "timeless", "text": "Warmer air is less dense."}],
        facts=_FIVE_WORD)
    assert "Warmer air is less dense" in (answer or ""), f"a valid claim must ship clean: {answer!r}"
    assert "claim type defaulted" not in transcript, "a valid type is never defaulted"


def test_authoring_lane_typeless_defaults_conversational(monkeypatch):
    """A type-less claim on an authoring (chat) obligation defaults to conversational, not
    unverified — an authored deliverable is not a knowledge assertion."""
    transcript, answer = _drive(
        monkeypatch, [{"obligation_id": "ob1", "text": "Hello there friend."}],
        question="say hello", lane="chat")
    assert answer and "Hello there friend" in answer, f"authored text must ship: {answer!r}"
    assert "-> 'conversational'" in transcript, (
        "an authoring-lane type-less claim defaults to conversational")


# ------------------------------------------------------------------------------- SABOTAGE SEAMS
# revert the ingestion default (restore `if ctype not in CLAIM_TYPES or not text: continue`):
#   -> test_turn009_typeless_claim_ships_instead_of_dead_turn RED (dead-turn banner returns)
#   -> test_typeless_claim_type_is_defaulted_loudly           RED (no default line)
# widen the default to also rescue no-text claims:
#   -> test_no_text_claim_still_rejects_honestly              RED (empty answer dressed as one)
# let the rescued claim BYPASS Law 2 (the keep-any variant the council refuted):
#   -> test_typeless_number_is_still_refused_by_law2          RED (ungrounded 450 ships as fact)
