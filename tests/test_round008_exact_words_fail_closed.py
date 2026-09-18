"""ROUND-008 pins — fail-closed exact_words (ROOT FROZEN, council/round-008/FIX_PLAN.md).

Frozen invariant: a HARD exact_words contract must never commit substantive bytes the kernel
has already deterministically proven violate that contract. After the informed retries are
exhausted, an underrun has no repair (nothing to truncate to; padding is fabrication), so the
claim is dropped and the existing BLOCK-C seam-10 liveness terminal ships a truthfully named
FORMAT_UNSATISFIED banner — non-empty, never truncated by the ledger transforms (PROBE2), with
the TTL decrementing exactly once (D1: the reply position is consumed, succeeded or not).

Two load-bearing guards, each with its own sabotage seam:
  * GUARD 1 (gate):        the synthesis word gate's final-round keep is now a reject
  * GUARD 2 (renderer):    the terminal render's best-underrun fallback is closed, so a
                           multi-row turn (the gate checks len(rows) == 1) cannot resurrect
                           a short claim the gate never vetted — found independently by the
                           DeepSeek and Gemini seats.

Anti-overfit law: nothing here keys on a specific count, prompt, or round-008 string. The
family spans semantic shapes, sloppy human phrasings, negative controls, and an adversarial
near-miss (a word CEILING is not an exact count and must not arm the fail-closed path).
"""
from __future__ import annotations

import json

import pytest

import core.kernel.repl as repl
from core.kernel.effects import EffectRunner


def _judge(extract_rows, synth_claims):
    def fake(runner, effect_id, system, user):
        if effect_id == "model.extract":
            return {"obligations": extract_rows}
        if effect_id == "model.synthesize":
            return {"claims": synth_claims}
        if effect_id == "model.derive":
            return {"computations": [], "missing": []}
        if effect_id == "model.verify":
            return {"claims": [], "all_parts_answered": "yes", "missing": []}
        raise AssertionError(effect_id)
    return fake


def _row(desc, lane="knowledge", fmt=""):
    return {"description": desc, "lane": lane, "query": "", "format": fmt,
            "source_offset": 0, "resolves_carryover": ""}


def _ledger(n, ttl=2):
    return {"_ledger": json.dumps([{"kind": "exact_words", "arg": str(n), "ttl": ttl}])}


def _turn(monkeypatch, question, rows, claims, facts=None):
    monkeypatch.setattr(repl, "_model_json", _judge(rows, claims))
    transcript, _j, _l, facts_out = repl.run_turn(
        question, EffectRunner(mode="record"), None, None, session_facts=facts or {})
    return transcript, repl._extract_answer(transcript) or "", facts_out


def _bare(ans: str) -> str:
    """Strip the provenance tag an unconstrained unverified claim legitimately carries
    (R0 turn 010 shipped it too) so shape assertions see the payload only."""
    import re as _re
    return _re.sub(r" \[(?:unverified - model memory|stipulated|receipt:[^\]]+)\]\s*$", "", ans).strip()


def _short(n_words, seed="alpha"):
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
             "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi"]
    return " ".join(words[:n_words])


def _exact(n_words, seed="sigma"):
    words = ["sigma", "tau", "upsilon", "phi", "chi", "psi", "omega", "aleph", "bet",
             "gimel", "dalet", "hei", "vav", "zayin", "het", "tet"]
    return " ".join(words[:n_words])


# ================================================================== A. TARGETED ROOT PROOF
def test_A1_underrun_after_exhaustion_does_not_commit(monkeypatch):
    """The frozen invariant, directly: a proven non-conforming candidate never becomes the
    committed substantive answer."""
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process", fmt="use exactly 7 words")],
                       [{"obligation_id": "ob1", "text": _short(5), "type": "unverified"}],
                       _ledger(7))
    assert "COMMIT: committed" not in tr, f"malformed bytes committed: {ans!r}"
    assert "couldn\u2019t meet the exact 7-word requirement" in ans
    assert "FORMAT_UNSATISFIED" in tr and "best model effort 5 of 7 words" in tr


def test_A2_named_format_unsatisfied_terminal_appears(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process", fmt="use exactly 7 words")],
                       [{"obligation_id": "ob1", "text": _short(5), "type": "unverified"}],
                       _ledger(7))
    # ROUND-011 UX repin: internal truth (FORMAT_UNSATISFIED + true cause) rides the
    # transcript/settle record and receipts; the user sees the conversational fallback.
    assert "format unsatisfied: best model effort was 5 of 7 words" in tr
    assert "FORMAT_UNSATISFIED" in tr
    assert "couldn\u2019t meet the exact 7-word requirement" in ans
    assert "no claim produced" not in ans and "no claim survived" not in ans


def test_A3_never_zero_bytes(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process", fmt="use exactly 7 words")],
                       [{"obligation_id": "ob1", "text": _short(1), "type": "unverified"}],
                       _ledger(7))
    assert ans.strip(), "the fail-closed terminal must ship visible bytes"


def test_A4_ttl_decrements_exactly_once(monkeypatch):
    tr, ans, facts = _turn(monkeypatch, "Explain the process.",
                           [_row("explain the process", fmt="use exactly 7 words")],
                           [{"obligation_id": "ob1", "text": _short(5), "type": "unverified"}],
                           _ledger(7, ttl=3))
    assert json.loads(facts[repl._LEDGER_KEY]) == [
        {"kind": "exact_words", "arg": "7", "ttl": 2}], (
        f"D1: the failed reply position is consumed, ttl 3 -> 2: {facts.get(repl._LEDGER_KEY)!r}")


def test_A5_compliant_candidate_commits_normally(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process", fmt="use exactly 7 words")],
                       [{"obligation_id": "ob1", "text": _exact(7), "type": "unverified"}],
                       _ledger(7))
    assert ans.strip() == _exact(7)
    assert "COMMIT: committed" in tr
    assert "Cannot answer this turn" not in ans


# ================================================================== B. SECOND-PATH PROOF
def test_B1_multi_row_turn_cannot_resurrect_the_underrun(monkeypatch):
    """GUARD 2: the gate word-check is conditioned on len(rows) == 1, so a multi-row turn's
    short claim reaches `valid` un-gated. The renderer's best-underrun fallback must not
    recover it (DeepSeek/Gemini second commit path)."""
    tr, ans, _ = _turn(monkeypatch, "Explain both processes.",
                       [_row("explain evaporation", fmt="use exactly 6 words"),
                        _row("explain condensation", fmt="use exactly 6 words")],
                       [{"obligation_id": "ob1", "text": _short(4), "type": "unverified"},
                        {"obligation_id": "ob2", "text": _short(3), "type": "unverified"}],
                       _ledger(6))
    # ROUND-011 UX repin: the malformed claims do not COMMIT; the closest model-authored
    # attempt may be DISPLAYED inside the fallback (mechanical selection), never as the
    # substantive answer — the span must lead with the fallback message.
    assert "couldn\u2019t meet the exact 6-word requirement" in ans
    assert ans.split("\n")[0].startswith("I couldn"), (
        f"the answer span must be the fallback message, not a bare candidate: {ans!r}")
    assert "FORMAT_UNSATISFIED" in tr and "exact-words render: no claim satisfies the count" in tr


def test_B2_multi_row_mixed_conforming_and_malformed_ships_only_the_conforming(monkeypatch):
    """A compliant sibling claim must not be collateral damage: it ships, the malformed
    one does not."""
    tr, ans, _ = _turn(monkeypatch, "Explain both processes.",
                       [_row("explain evaporation", fmt="use exactly 6 words"),
                        _row("explain condensation", fmt="use exactly 6 words")],
                       [{"obligation_id": "ob1", "text": _exact(6), "type": "unverified"},
                        {"obligation_id": "ob2", "text": _short(4), "type": "unverified"}],
                       _ledger(6))
    assert _exact(6) in ans
    assert _short(4) not in ans, f"malformed sibling shipped: {ans!r}"
    assert "Cannot answer this turn" not in ans


# ================================================================== C. PRESERVED BEHAVIOR
def test_C1_overrun_truncation_unchanged(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process", fmt="use exactly 6 words")],
                       [{"obligation_id": "ob1", "text": _exact(9), "type": "unverified"}],
                       _ledger(6))
    assert len(ans.split()) == 6 and ans.split() == _exact(9).split()[:6]
    assert "COMMIT: committed" in tr


def test_C2_exact_chars_gate_unchanged(monkeypatch):
    """The character-count sibling keeps its own always-reject law — the repair made the
    word gate CONSISTENT with it, and must not touch it."""
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process", fmt="use exactly 40 characters")],
                       [{"obligation_id": "ob1", "text": _short(5), "type": "unverified"}])
    assert "Cannot answer this turn" in ans
    assert "format unsatisfied" not in ans, "the char gate must not gain word wording"


def test_C3_lowercase_only_contract_still_ships_short_answers(monkeypatch):
    """Fail-closed applies to exact_words ONLY: a satisfiable transform contract (lowercase)
    has no count to violate, so a short answer still ships."""
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process")],
                       [{"obligation_id": "ob1", "text": "Warm Sun Drives It", "type": "unverified"}],
                       {"_ledger": json.dumps([{"kind": "lowercase", "arg": "", "ttl": 2}])})
    assert ans.strip() == "warm sun drives it"


def test_C4_setter_ack_unchanged(monkeypatch):
    """Round-007 pins: the mint turn ships the deterministic value-bearing ack and consumes
    no permit."""
    tr, ans, facts = _turn(monkeypatch,
                           "For your next TWO answers only, use exactly five words each.",
                           [_row("ack")],
                           [{"obligation_id": "ob1", "text": "OK noted for next time.", "type": "conversational"}])
    assert "applies to your next" in ans
    ledger = json.loads(facts.get(repl._LEDGER_KEY, "[]"))
    assert ledger == [{"kind": "exact_words", "arg": "5", "ttl": 2}], (
        f"the setter turn must arm without consuming: {ledger!r}")


def test_C5_retry_feedback_unchanged(monkeypatch):
    """Rounds 1-2 still reject with named cumulative feedback before fail-closed."""
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process", fmt="use exactly 6 words")],
                       [{"obligation_id": "ob1", "text": _short(4), "type": "unverified"}],
                       _ledger(6))
    assert tr.count("claim rejected: 4 words against an exactly-6-words order") == 2


def test_C6_unconstrained_reply_unchanged(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process")],
                       [{"obligation_id": "ob1", "text": _short(5), "type": "unverified"}])
    assert _bare(ans) == _short(5)


# ================================================================== D. ANTI-OVERFIT FAMILY
# Original semantic shape + 5 clean variants: different N, different lanes, minted vs
# format-field arming, large N.
@pytest.mark.parametrize("n", [3, 4, 6, 8, 12])
def test_D1_clean_variants_fail_closed_for_any_count(monkeypatch, n):
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process", fmt=f"use exactly {n} words")],
                       [{"obligation_id": "ob1", "text": _short(max(1, n - 2)), "type": "unverified"}],
                       _ledger(n))
    assert "COMMIT: committed" not in tr
    assert f"couldn\u2019t meet the exact {n}-word requirement" in ans
    assert "format unsatisfied" in tr


# 5 sloppy/human variant setter phrasings: the mint must arm and the armed turns must fail
# closed — the repair cannot depend on canonical wording.
@pytest.mark.parametrize("question", [
    "ok starting after this ack my next three replies are exactly six words each ok?",
    "next 3 answers: exactly 6 words. don't use one on this acknowledgement.",
    "for your next three substantive answers use exactly six words apiece please",
    "your next THREE replies must each contain exactly six words starting now-ish",
    "after this, next three answers exactly six words each, all lowercase too",
])
def test_D2_sloppy_setter_variants_still_arm_and_fail_closed(monkeypatch, question):
    tr_ack, ans_ack, facts = _turn(monkeypatch, question,
                                    [_row("ack")],
                                    [{"obligation_id": "ob1", "text": "sure thing.",
                                      "type": "conversational"}])
    ledger = json.loads(facts.get(repl._LEDGER_KEY, "[]"))
    assert any(e["kind"] == "exact_words" and e["arg"] == "6" for e in ledger), (
        f"sloppy setter failed to arm: {ledger!r}")
    tr, ans, facts2 = _turn(monkeypatch, "Explain the process.",
                            [_row("explain the process")],
                            [{"obligation_id": "ob1", "text": _short(4), "type": "unverified"}],
                            facts)
    assert "COMMIT: committed" not in tr
    assert "couldn\u2019t meet the exact 6-word requirement" in ans
    assert "format unsatisfied" in tr


# 3 negative controls: nothing fail-closes when the contract is absent or of another kind.
def test_D3_negative_no_contract_from_any_source(monkeypatch):
    """Negative control: with neither an armed ledger nor an extraction format order, a
    short answer ships untouched. (The format field ALONE is a real user format order —
    PROBE1 case A armed the contract exactly that way — so it must fail-close; only the
    complete absence of the contract leaves the turn free.)"""
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process")],
                       [{"obligation_id": "ob1", "text": _short(4), "type": "unverified"}])
    assert _bare(ans) == _short(4), "no contract anywhere: the turn must ship normally"


def test_D4_negative_expired_ledger(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process")],
                       [{"obligation_id": "ob1", "text": _short(4), "type": "unverified"}],
                       {"_ledger": json.dumps([{"kind": "exact_words", "arg": "6", "ttl": 0}])})
    assert _bare(ans) == _short(4), "expired contract must not constrain"


def test_D5_negative_end_word_contract(monkeypatch):
    tr, ans, _ = _turn(monkeypatch, "Explain the process.",
                       [_row("explain the process")],
                       [{"obligation_id": "ob1", "text": "warm sun drives it", "type": "unverified"}],
                       {"_ledger": json.dumps([{"kind": "end_word", "arg": "done", "ttl": 2}])})
    assert ans.rstrip().endswith("done."), (
        f"end_word transform must keep working: {ans!r}")


# Adversarial near-miss: a word CEILING is not an exact count. "no more than N words"
# must not arm exact_words(N) — otherwise compliant short answers would start failing.
def test_D6_adversarial_ceiling_phrase_does_not_arm_exact_words(monkeypatch):
    armed = repl._mint_ledger("For your next two answers, use no more than five words each.")
    assert not any(e["kind"] == "exact_words" for e in armed), (
        f"a ceiling armed an exact count and would reject compliant short answers: {armed!r}")


def test_D7_adversarial_ceiling_turn_still_ships_a_short_answer(monkeypatch):
    """End-to-end: under a ceiling-shaped instruction the short answer ships untouched."""
    tr, ans, _ = _turn(monkeypatch, "Explain the process briefly, no more than five words.",
                       [_row("explain the process briefly")],
                       [{"obligation_id": "ob1", "text": _short(3), "type": "unverified"}])
    assert _bare(ans) == _short(3), (
        f"a ceiling must not trigger the exact_words fail-closed path: {ans!r}")
